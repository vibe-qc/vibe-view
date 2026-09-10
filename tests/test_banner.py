"""Tests for the summary banner printed at file-open time."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

from vibeview.banner import format_banner
from vibeview.qvf import QVFReader


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_qvf(sections: list[dict], source_name: str = "test") -> Path:
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": source_name},
        "sections": sections,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
    return Path(tmp.name)


def _structure_section(sid: str = "structure") -> tuple[dict, dict[str, bytes]]:
    """Minimal valid `structure` section + the zip entries it references."""
    payload = json.dumps(
        {
            "atoms": [{"symbol": "H", "position": [0, 0, 0], "atomic_number": 1}],
            "pbc": [False, False, False],
        }
    ).encode()
    section = {
        "id": sid,
        "kind": "structure",
        "members": {
            "structure": {
                "path": f"sections/{sid}.json",
                "format": "json",
                "sha256": _sha256(payload),
            },
        },
    }
    return section, {f"sections/{sid}.json": payload}


def _density_section(sid: str = "density") -> tuple[dict, dict[str, bytes]]:
    """Minimal valid `volume.density` section + zip entries."""
    grid = json.dumps(
        {"origin": [0, 0, 0], "voxel_vectors": [[0.2, 0, 0], [0, 0.2, 0], [0, 0, 0.2]], "shape": [4, 4, 4]}
    ).encode()
    import numpy as np  # local import to keep banner imports minimal

    data = np.zeros((4, 4, 4), dtype=np.float32).tobytes()
    section = {
        "id": sid,
        "kind": "volume.density",
        "members": {
            "grid": {"path": f"sections/{sid}.grid.json", "format": "json", "sha256": _sha256(grid)},
            "data": {
                "path": f"sections/{sid}.dat",
                "format": "binary",
                "dtype": "float32",
                "shape": [4, 4, 4],
                "sha256": _sha256(data),
            },
        },
    }
    return section, {
        f"sections/{sid}.grid.json": grid,
        f"sections/{sid}.dat": data,
    }


def _make_qvf_with_files(
    sections: list[dict], files: dict[str, bytes], source_name: str = "test"
) -> Path:
    """Like ``_make_qvf`` but also writes zip member payloads."""
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": source_name},
        "sections": sections,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, data in files.items():
            zf.writestr(path, data)
    return Path(tmp.name)


class TestBanner:
    def test_banner_includes_filename(self) -> None:
        path = _make_qvf([])
        try:
            reader = QVFReader(path)
            banner = format_banner(reader)
            assert path.name in banner
        finally:
            path.unlink()

    def test_banner_includes_source_info(self) -> None:
        path = _make_qvf([], source_name="h2o_pbe0")
        try:
            reader = QVFReader(path)
            banner = format_banner(reader)
            assert "vibe-qc" in banner
            assert "0.9.0" in banner
            assert "h2o_pbe0" in banner
        finally:
            path.unlink()

    def test_banner_lists_rendered_section(self) -> None:
        sec, files = _structure_section()
        path = _make_qvf_with_files([sec], files)
        try:
            reader = QVFReader(path)
            banner = format_banner(reader)
            assert "structure" in banner
            assert "rendered" in banner
        finally:
            path.unlink()

    # Note: there used to be a `test_banner_lists_skipped_section`
    # test that fed a canonical kind without a renderer and asserted
    # the banner labelled it "skipped, unsupported". The
    # canonical-but-not-rendered niche is now occupied by *deferred*
    # kinds (writer-emitted + schema-valid, renderer pending —
    # vibeview.kinds.DEFERRED_KINDS), which the banner labels
    # "skipped, not yet rendered". That classification is pinned in
    # `tests/test_kind_drift.py` and `tests/test_kinds.py`; the
    # vendor-namespace skipped path is covered by
    # `test_banner_lists_vendor_namespace` below.

    def test_banner_lists_vendor_namespace(self) -> None:
        path = _make_qvf(
            [
                {"id": "orca_ext", "kind": "x_orca.orbitals", "members": {}},
            ]
        )
        try:
            reader = QVFReader(path)
            banner = format_banner(reader)
            assert "orca_ext" in banner
            assert "skipped" in banner
            assert "orca" in banner
        finally:
            path.unlink()

    def test_banner_shows_section_count(self) -> None:
        struct_sec, sf = _structure_section()
        dens_sec, df = _density_section()
        # Vendor namespace = the only realistic skipped-section
        # source now that every canonical kind has a renderer.
        skip_sec = {"id": "unknown", "kind": "x_acme.thing", "members": {}}
        path = _make_qvf_with_files(
            [struct_sec, dens_sec, skip_sec],
            {**sf, **df},
        )
        try:
            reader = QVFReader(path)
            banner = format_banner(reader)
            assert "2 section(s) will be rendered" in banner
            assert "1 skipped" in banner
        finally:
            path.unlink()

    def test_banner_handles_empty_sections(self) -> None:
        path = _make_qvf([])
        try:
            reader = QVFReader(path)
            banner = format_banner(reader)
            assert "0 section(s) will be rendered" in banner
        finally:
            path.unlink()

    def test_banner_has_box_drawing_border(self) -> None:
        path = _make_qvf([])
        try:
            reader = QVFReader(path)
            banner = format_banner(reader)
            assert "╔" in banner
            assert "╚" in banner
        finally:
            path.unlink()
