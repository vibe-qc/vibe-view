"""First-run helpers for the standalone vibe-view command line.

The functions in this module deliberately avoid importing optional rendering
stacks.  ``vibe-view doctor`` must remain useful when one of those stacks is
exactly what is missing or broken.
"""

from __future__ import annotations

import importlib.util
import io
import os
import platform
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path
from typing import Any

from vibeview import __version__

_DEMO_XYZ = ("resources", "examples", "water.xyz")
_EXAMPLES_README = ("resources", "examples", "README.txt")


@dataclass(frozen=True)
class _ModuleRequirement:
    module: str
    distribution: str


_CORE_REQUIREMENTS = (
    _ModuleRequirement("click", "click"),
    _ModuleRequirement("jsonschema", "jsonschema"),
    _ModuleRequirement("matplotlib", "matplotlib"),
    _ModuleRequirement("numpy", "numpy"),
    _ModuleRequirement("plotly", "plotly"),
    _ModuleRequirement("pydantic", "pydantic"),
    _ModuleRequirement("pyvista", "pyvista"),
)

_OPTIONAL_REQUIREMENTS: dict[str, tuple[_ModuleRequirement, ...]] = {
    "browser": (
        _ModuleRequirement("trame", "trame"),
        _ModuleRequirement("trame_vtk", "trame-vtk"),
        _ModuleRequirement("trame_vuetify", "trame-vuetify"),
        _ModuleRequirement("uvicorn", "uvicorn"),
    ),
    "tui": (_ModuleRequirement("textual", "textual"),),
    "ase_import": (_ModuleRequirement("ase", "ase"),),
    "smiles": (_ModuleRequirement("rdkit", "rdkit"),),
    "jupyter": (
        _ModuleRequirement("IPython", "ipython"),
        _ModuleRequirement("ipywidgets", "ipywidgets"),
    ),
    # The import package is `vq`; the distribution is `vq` too (vibe-queue is
    # the repository name, not a package). Drives the queue panel in
    # `vibe-view open` and the `vibe-view from-vq` fetch path.
    "queue": (_ModuleRequirement("vq", "vq"),),
}

_CAPABILITY_EXTRAS = {
    "core": None,
    "browser": "viewer",
    "tui": "tui",
    "ase_import": "ase",
    "smiles": "smiles",
    "jupyter": "jupyter",
    "queue": "queue",
}

# Extras whose requirement cannot be resolved from a package index yet, so the
# install hint alone is not actionable. Kept next to the extras map so the two
# cannot drift.
_CAPABILITY_NOTES = {
    "queue": (
        "vq is not published on a package index; install it from a "
        "vibe-queue checkout first (pip install <path-to-vibe-queue>), "
        "after which this extra resolves against it. vq needs Python 3.12+"
    ),
}


def _module_available(module: str) -> bool:
    """Return whether *module* is discoverable without importing it."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _distribution_version(distribution: str) -> str | None:
    """Return installed version metadata, tolerating incomplete installs."""
    try:
        return metadata.version(distribution)
    except (metadata.PackageNotFoundError, ValueError, OSError):
        return None


def _requirement_report(requirements: Iterable[_ModuleRequirement]) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for requirement in requirements:
        available = _module_available(requirement.module)
        report.append(
            {
                "module": requirement.module,
                "distribution": requirement.distribution,
                "available": available,
                "version": (
                    _distribution_version(requirement.distribution) if available else None
                ),
            }
        )
    return report


def _resource_available(parts: tuple[str, ...]) -> bool:
    try:
        return resources.files("vibeview").joinpath(*parts).is_file()
    except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError):
        return False


def bundled_example_bytes(name: str) -> bytes:
    """Read a project-authored example from package resources."""
    paths = {
        "water.xyz": _DEMO_XYZ,
        "README.txt": _EXAMPLES_README,
    }
    try:
        parts = paths[name]
    except KeyError:
        raise ValueError(f"unknown bundled example: {name}") from None
    return resources.files("vibeview").joinpath(*parts).read_bytes()


def demo_qvf_bytes() -> bytes:
    """Return a minimal QVF made from the bundled project-authored water XYZ."""
    from vibeview.converters import xyz_to_qvf

    generated = xyz_to_qvf(bundled_example_bytes("water.xyz")).getvalue()
    source = io.BytesIO(generated)
    output = io.BytesIO()
    with (
        zipfile.ZipFile(source) as archive,
        zipfile.ZipFile(
            output,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as normalized,
    ):
        for name in sorted(archive.namelist()):
            member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            member.create_system = 3
            member.external_attr = 0o100644 << 16
            normalized.writestr(member, archive.read(name), compresslevel=9)
    return output.getvalue()


def _write_and_sync(handle: Any, payload: bytes) -> None:
    """Write one temporary payload completely before it can become visible."""
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())


def _publish_bytes(path: Path, payload: bytes, *, force: bool) -> None:
    """Publish bytes atomically, either replacing or refusing a destination."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise FileExistsError(path)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            _write_and_sync(handle, payload)
        if force:
            os.replace(temporary_path, path)
        else:
            try:
                # The completed temporary file is in the destination's
                # directory, so this is an atomic no-replace publication on
                # both macOS and Linux.
                os.link(temporary_path, path)
            except FileExistsError as exc:
                raise FileExistsError(path) from exc
            temporary_path.unlink()
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_demo(path: Path, *, force: bool = False) -> Path:
    """Write the bundled demo as QVF, refusing accidental replacement."""
    path = Path(path)
    _publish_bytes(path, demo_qvf_bytes(), force=force)
    return path


def copy_examples(destination: Path, *, force: bool = False) -> list[Path]:
    """Copy the authored XYZ, its generated QVF, and licensing note."""
    destination = Path(destination)
    payloads = {
        "water.xyz": bundled_example_bytes("water.xyz"),
        "water.qvf": demo_qvf_bytes(),
        "README.txt": bundled_example_bytes("README.txt"),
    }
    collisions = [destination / name for name in payloads if (destination / name).exists()]
    if collisions and not force:
        raise FileExistsError(collisions[0])
    destination.mkdir(parents=True, exist_ok=True)
    written = []
    for name, payload in payloads.items():
        path = destination / name
        _publish_bytes(path, payload, force=force)
        written.append(path)
    return written


def _desktop_source_path() -> Path:
    from vibeview.install_hints import source_project_dir

    project = source_project_dir()
    if project is not None:
        return project / "electron"
    return Path(__file__).resolve().parent.parent.parent / "electron"


def doctor_report() -> dict[str, Any]:
    """Collect JSON-serializable installation diagnostics.

    Only core-package and bundled-resource failures affect ``healthy``.
    Optional extras are capabilities, not health requirements.
    """
    core_packages = _requirement_report(_CORE_REQUIREMENTS)
    resources_report = {
        "schema_v1": _resource_available(("schema.json",)),
        "schema_v2": _resource_available(("schema_v2.json",)),
        "demo_water_xyz": _resource_available(_DEMO_XYZ),
        "examples_readme": _resource_available(_EXAMPLES_README),
    }
    python_supported = sys.version_info >= (3, 11)
    core_available = python_supported and all(row["available"] for row in core_packages)
    core_available = core_available and all(resources_report.values())

    from vibeview.install_hints import install_hint

    capabilities: dict[str, dict[str, Any]] = {
        "core": {
            "available": core_available,
            "required": True,
            "packages": core_packages,
            "missing": [row["distribution"] for row in core_packages if not row["available"]],
            "install_hint": install_hint(_CAPABILITY_EXTRAS["core"]),
            "note": _CAPABILITY_NOTES.get("core"),
        }
    }
    for name, requirements in _OPTIONAL_REQUIREMENTS.items():
        packages = _requirement_report(requirements)
        capabilities[name] = {
            "available": all(row["available"] for row in packages),
            "required": False,
            "packages": packages,
            "missing": [row["distribution"] for row in packages if not row["available"]],
            "install_hint": install_hint(_CAPABILITY_EXTRAS[name]),
            "note": _CAPABILITY_NOTES.get(name),
        }

    electron_source = _desktop_source_path().is_dir()
    desktop_missing: list[str] = []
    if not electron_source:
        desktop_missing.append("source-checkout electron directory")
    if not capabilities["browser"]["available"]:
        desktop_missing.append("browser extra")
    capabilities["desktop"] = {
        "available": not desktop_missing,
        "required": False,
        "source_checkout": electron_source,
        "missing": desktop_missing,
        "note": _CAPABILITY_NOTES.get("desktop"),
        "install_hint": (
            install_hint("viewer")
            if electron_source
            else (
                "use a source checkout, then run "
                "./scripts/install.sh --with-electron"
            )
        ),
    }

    return {
        "healthy": core_available,
        "vibeview": {"version": __version__},
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
            "supported": python_supported,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "resources": resources_report,
        "capabilities": capabilities,
    }


def format_report() -> dict[str, Any]:
    """Return normalized built-in, optional, and plugin importer capabilities."""
    from vibeview.converters import format_capabilities

    capabilities = format_capabilities()

    rows = []
    for capability in capabilities:
        rows.append(
            {
                "format_name": capability.format_name,
                "extensions": list(capability.extensions),
                "provider": capability.provider,
                "available": capability.available,
                "data_kinds": list(capability.data_kinds),
                "description": capability.description,
                "install_hint": capability.install_hint,
                "error": capability.error,
            }
        )
    return {"formats": rows}
