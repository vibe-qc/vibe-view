"""Offscreen rendering test — verifies the Trame app can be created
in a headless/CI environment without a display server.

Uses PyVista's off_screen mode and a minimal QVF file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

import pytest


def _make_minimal_qvf() -> Path:
    """Create a .qvf with just a structure section (no volumes)."""
    structure = json.dumps(
        {
            "atoms": [{"symbol": "He", "position": [0, 0, 0], "atomic_number": 2}],
            "pbc": [False, False, False],
        }
    ).encode()
    sections = [
        {
            "id": "structure",
            "kind": "structure",
            "members": {
                "structure": {
                    "path": "sections/structure.json",
                    "format": "json",
                    "sha256": hashlib.sha256(structure).hexdigest(),
                }
            },
        }
    ]
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "test"},
        "sections": sections,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("sections/structure.json", structure)
    return Path(tmp.name)


class TestOffscreenAppCreation:
    """Verify the Trame app can be created in headless mode."""

    def test_create_app_offscreen(self) -> None:
        """create_app() should not crash when built with a minimal QVF."""
        path = _make_minimal_qvf()
        try:
            # Ensure offscreen mode is set before VTK initializes
            os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

            from vibeview.app import create_app
            from vibeview.qvf import QVFReader

            reader = QVFReader(path)
            app = create_app(reader)
            # If we got here without ImportError or VTK error, success
            assert app is not None
            reader.close()
        finally:
            path.unlink()

    def test_imports_are_clean(self) -> None:
        """All vibeview modules should import without crashing."""
        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

        import vibeview
        import vibeview.banner
        import vibeview.cli
        import vibeview.kinds
        import vibeview.qvf
        import vibeview.renderers
        import vibeview.renderers.bands
        import vibeview.renderers.spectra
        import vibeview.renderers.structure
        import vibeview.renderers.trajectory
        import vibeview.renderers.vibrations
        import vibeview.renderers.volume
        import vibeview.viewer_defaults

        # Structural, not a literal: this test is about imports being
        # clean, and a pinned version string here turns every release
        # into a test edit. That __version__ agrees with
        # pyproject.toml, package.json and package-lock.json is
        # test_install_electron.py::test_desktop_version_files_stay_in_sync.
        assert re.fullmatch(r"\d+\.\d+\.\d+", vibeview.__version__), (
            f"__version__ is not a release version: {vibeview.__version__!r}"
        )


class TestInstallSmoke:
    """Verify the package is correctly installed."""

    def test_vibeview_is_importable(self) -> None:
        import importlib.util

        spec = importlib.util.find_spec("vibeview")
        assert spec is not None, (
            "vibeview package not found. Install with: pip install -e vibe-view/"
        )

    def test_cli_entry_point_registered(self) -> None:
        """The vibe-view CLI entry point should be discoverable."""
        import importlib.metadata

        eps = importlib.metadata.entry_points(group="console_scripts")
        names = {ep.name for ep in eps}
        assert "vibe-view" in names, (
            "vibe-view CLI not registered. Install with: pip install -e vibe-view/"
        )
