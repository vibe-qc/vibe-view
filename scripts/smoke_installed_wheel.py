#!/usr/bin/env python3
"""Smoke-test vibe-view from a clean, non-editable wheel installation."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.resources
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run_cli(executable: Path, *arguments: str) -> str:
    proc = subprocess.run(
        [str(executable), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{' '.join((str(executable), *arguments))} failed with "
            f"status {proc.returncode}:\n{proc.stdout}{proc.stderr}"
        )
    return proc.stdout + proc.stderr


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="checkout src directory that imports must not resolve below",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    source_root = args.source_root.resolve()

    import vibeview
    from vibeview import cli

    package_path = Path(vibeview.__file__).resolve()
    if package_path.is_relative_to(source_root):
        raise RuntimeError(f"editable/source import escaped wheel smoke: {package_path}")

    distribution_version = importlib.metadata.version("vibeview")
    if vibeview.__version__ != distribution_version:
        raise RuntimeError(
            f"module version {vibeview.__version__!r} does not match wheel metadata "
            f"{distribution_version!r}"
        )

    package_files = importlib.resources.files("vibeview")
    for schema_name in ("schema.json", "schema_v2.json"):
        schema = json.loads(package_files.joinpath(schema_name).read_text(encoding="utf-8"))
        if not isinstance(schema, dict) or "$schema" not in schema:
            raise RuntimeError(f"packaged {schema_name} is not a JSON Schema")
    examples = package_files.joinpath("resources", "examples")
    readme_resource = examples.joinpath("README.txt")
    xyz_resource = examples.joinpath("water.xyz")
    if not readme_resource.is_file() or "Mozilla Public License 2.0" not in (
        readme_resource.read_text(encoding="utf-8")
    ):
        raise RuntimeError("wheel is missing the licensed example README resource")
    if not xyz_resource.is_file() or not xyz_resource.read_text(encoding="utf-8").startswith("3\n"):
        raise RuntimeError("wheel is missing the project-authored water XYZ resource")

    # Keep the venv-facing interpreter path: resolving its symlink would jump
    # to the base Python installation and look for the console script there.
    scripts_dir = Path(sys.executable).parent
    executable = scripts_dir / ("vibe-view.exe" if sys.platform == "win32" else "vibe-view")
    if not executable.is_file():
        raise RuntimeError(f"wheel did not install the console command at {executable}")
    version_output = _run_cli(executable, "--version")
    if distribution_version not in version_output:
        raise RuntimeError("vibe-view --version does not report the installed metadata version")

    # Core, browser, and terminal entry points must all be importable and have
    # working Click surfaces from the installed wheel.  Their interactive
    # event loops are intentionally not started in a release smoke test.
    import textual  # noqa: F401
    import trame  # noqa: F401
    import trame_vtk  # noqa: F401
    import trame_vuetify  # noqa: F401
    import uvicorn  # noqa: F401

    import vibeview.app  # noqa: F401
    import vibeview.tui.app  # noqa: F401

    for arguments in (
        ("--help",),
        ("show", "--help"),
        ("open", "--help"),
        ("serve", "--help"),
        ("tui", "--help"),
        ("desktop", "--help"),
    ):
        _run_cli(executable, *arguments)

    # Exercise the first-run contract from a directory unrelated to either the
    # checkout or venv.  This catches accidental source-relative resource
    # lookups and editable-install leakage.
    previous_cwd = Path.cwd()
    try:
        with tempfile.TemporaryDirectory(prefix="vibeview-wheel-smoke-") as temporary:
            neutral_cwd = Path(temporary)
            os.chdir(neutral_cwd)

            doctor = json.loads(_run_cli(executable, "doctor", "--json"))
            if not doctor.get("healthy"):
                raise RuntimeError("doctor --json reports an unhealthy wheel install")
            for capability in ("browser", "tui", "ase_import"):
                if not doctor["capabilities"][capability]["available"]:
                    raise RuntimeError(f"doctor reports unavailable {capability} capability")
            desktop = doctor["capabilities"]["desktop"]
            if desktop["available"] or desktop["source_checkout"]:
                raise RuntimeError("wheel smoke unexpectedly claims source-backed desktop support")

            formats = json.loads(_run_cli(executable, "formats", "--json"))["formats"]
            by_name = {row["format_name"]: row for row in formats}
            for format_name in ("qvf", "xyz", "ase"):
                if format_name not in by_name or not by_name[format_name]["available"]:
                    raise RuntimeError(f"formats --json does not expose ready {format_name}")

            demo = neutral_cwd / "demo.qvf"
            _run_cli(executable, "demo", "--output", str(demo))
            if not demo.is_file():
                raise RuntimeError("demo did not create its QVF")
            _run_cli(executable, "validate", str(demo))

            copied = neutral_cwd / "examples"
            _run_cli(executable, "examples", "--copy", str(copied))
            expected_examples = {"README.txt", "water.qvf", "water.xyz"}
            if {path.name for path in copied.iterdir()} != expected_examples:
                raise RuntimeError("examples --copy produced an incomplete inventory")

            imported = neutral_cwd / "imported-water.qvf"
            _run_cli(
                executable,
                "import",
                str(copied / "water.xyz"),
                "--output",
                str(imported),
            )
            if not imported.is_file():
                raise RuntimeError("import did not persist the copied XYZ as QVF")
            _run_cli(executable, "validate", str(imported))
    finally:
        os.chdir(previous_cwd)

    # The Electron application remains source-checkout-only in this milestone.
    # Assert that boundary explicitly so CI cannot be mistaken for a packaged
    # desktop-app launch test.
    electron_dir = cli._electron_dir()
    if electron_dir.exists():
        raise RuntimeError(
            f"unexpected Electron source directory beside installed wheel: {electron_dir}"
        )

    print(f"wheel import: {package_path}")
    print(f"core/browser/TUI/ASE onboarding: OK ({distribution_version})")
    print("desktop: CLI surface OK; Electron app remains source-checkout-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
