"""Installation remediation that works before and after public-index release."""

from __future__ import annotations

import json
import shlex
import sys
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname


def _is_source_project(path: Path) -> bool:
    return (path / "pyproject.toml").is_file() and (path / "scripts").is_dir()


def _file_url_path(url: str) -> Path:
    """Convert one PEP 610 file URL using the host platform's path rules."""
    parsed = urlparse(url)
    try:
        # Python 3.14+ accepts the complete URL and handles drive letters,
        # escaping, localhost, and UNC authorities for the host platform.
        converted = url2pathname(url, require_scheme=True)
    except TypeError:
        # Python 3.11-3.13 take only the URL path. Preserve a non-local
        # authority as the leading component of a UNC/network path.
        raw_path = parsed.path
        if parsed.netloc not in ("", "localhost"):
            raw_path = f"//{parsed.netloc}{raw_path}"
        converted = url2pathname(raw_path)
    return Path(converted)


def _direct_source_install() -> tuple[Path, bool] | None:
    """Return a local PEP 610 source directory and its editable flag."""
    try:
        direct_url = metadata.distribution("vibeview").read_text("direct_url.json")
        if direct_url is None:
            return None
        record = json.loads(direct_url)
        parsed = urlparse(record.get("url", ""))
        if parsed.scheme != "file":
            return None
        project = _file_url_path(record["url"]).resolve()
        if not _is_source_project(project):
            return None
        editable = bool(record.get("dir_info", {}).get("editable", False))
        return project, editable
    except (
        AttributeError,
        json.JSONDecodeError,
        metadata.PackageNotFoundError,
        OSError,
        TypeError,
        ValueError,
    ):
        return None


def source_project_dir() -> Path | None:
    """Return the local vibe-view project root for a source install.

    A regular ``pip install <checkout>`` copies Python into site-packages, so
    walking upward from ``__file__`` cannot find its source-only Electron
    companion. PEP 610's ``direct_url.json`` retains that checkout path and
    works for both regular and editable local installs.
    """
    candidate = Path(__file__).resolve().parents[2]
    if _is_source_project(candidate):
        return candidate
    direct_install = _direct_source_install()
    return direct_install[0] if direct_install is not None else None


def _source_install_is_editable(project: Path) -> bool:
    direct_install = _direct_source_install()
    if direct_install is not None and direct_install[0] == project.resolve():
        return direct_install[1]
    # A directly imported source tree (or a test-provided source path) has no
    # reliable installed record. Preserve its development-friendly behavior.
    return True


def distribution_requirement(extra: str | None = None) -> str:
    """Return the canonical distribution requirement (not the CLI name)."""
    return f"vibeview[{extra}]" if extra else "vibeview"


def hosted_wheel_url() -> str:
    """Return the standalone-site publication URL for this version.

    Release publication must stage this wheel; constructing the URL does not
    imply that a wheel is already available. See the installation guide.
    """
    from vibeview import __version__

    filename = f"vibeview-{__version__}-py3-none-any.whl"
    return f"https://vibe-qc.com/vibe-view/docs/_static/downloads/{filename}"


def install_hint(extra: str | None = None) -> str:
    """Return a copy-paste command appropriate for this installation.

    Local source installs point pip back at their checkout and preserve their
    editable/non-editable mode. Installed wheels use the same-version hosted
    wheel, including a PEP 508 extra, so remediation works even before
    ``vibeview`` is available on a public package index.
    """
    python = shlex.quote(sys.executable)
    requirement = distribution_requirement(extra)
    project = source_project_dir()
    if project is not None:
        source_requirement = f"{project}{f'[{extra}]' if extra else ''}"
        editable = "-e " if _source_install_is_editable(project) else ""
        return f"{python} -m pip install {editable}{shlex.quote(source_requirement)}"
    direct_requirement = f"{requirement} @ {hosted_wheel_url()}"
    return f"{python} -m pip install {shlex.quote(direct_requirement)}"


def queue_missing_message(action: str = "") -> str:
    """Return the remediation shown when ``vq`` (vibe-queue) is not importable.

    Every other optional extra surfaces :func:`install_hint` when it is
    missing; the queue sites used to say only "vq is not installed", and one
    of them pointed at ``pip install -e vibe-queue/`` — a monorepo-relative
    path that resolves to nothing since the 2026-09 split.

    ``vq`` is not on a package index, so the extra alone is not actionable:
    it resolves only against a vibe-queue checkout that is already installed.
    Say both, in that order.
    """
    lead = "vq (vibe-queue) is not installed"
    if action:
        lead = f"{lead} — cannot {action}"
    return (
        f"{lead}. Install vibe-queue from its checkout "
        f"(pip install <path-to-vibe-queue>), then: {install_hint('queue')}"
    )
