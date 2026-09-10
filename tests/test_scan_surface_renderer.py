"""ScanSurfaceRenderer tests (W2 / scan.surface kind).

Builds a synthetic `scan.surface` archive and exercises the reader +
renderer: axis/energy round-trip, min-node detection, PNG heat-map
output, and the optional per-node geometries member.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np

from vibeview.qvf import QVFReader
from vibeview.renderers.scan_surface import ScanSurfaceRenderer


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _scan_surface_qvf(with_geometries: bool = False) -> tuple[Path, str]:
    n_a, n_b = 3, 4
    axis_a = np.linspace(1.4, 2.0, n_a).astype(np.float64)
    axis_b = np.linspace(1.6, 2.2, n_b).astype(np.float64)
    # A bowl with its minimum at (ia=1, ib=2).
    ii, jj = np.meshgrid(np.arange(n_a), np.arange(n_b), indexing="ij")
    energies = ((ii - 1) ** 2 + (jj - 2) ** 2).astype(np.float64) * -1.0
    energies = energies - energies.min()  # min at (1,2) == 0
    energies = -energies  # make (1,2) the lowest (most negative)

    meta_dict = {
        "shape": [n_a, n_b],
        "coordinate_a_label": "bond 0–1",
        "coordinate_a_unit": "bohr",
        "coordinate_b_label": "angle 1–0–2",
        "coordinate_b_unit": "rad",
    }
    members = {
        "metadata": ("scan/meta.json", None),
        "axis_a": ("scan/a.bin", axis_a.tobytes(), "float64", [n_a]),
        "axis_b": ("scan/b.bin", axis_b.tobytes(), "float64", [n_b]),
        "energies": ("scan/e.bin", energies.tobytes(), "float64", [n_a, n_b]),
    }
    geo_bytes = None
    if with_geometries:
        geo = np.zeros((n_a * n_b, 3, 3), dtype=np.float64)
        geo_bytes = geo.tobytes()
        meta_dict["atoms"] = [
            {"symbol": "O", "atomic_number": 8},
            {"symbol": "H", "atomic_number": 1},
            {"symbol": "H", "atomic_number": 1},
        ]

    meta = json.dumps(meta_dict).encode()
    section_members = {
        "metadata": {
            "path": "scan/meta.json",
            "format": "json",
            "sha256": _sha256(meta),
        },
        "axis_a": {
            "path": "scan/a.bin", "format": "binary",
            "dtype": "float64", "shape": [n_a], "sha256": _sha256(axis_a.tobytes()),
        },
        "axis_b": {
            "path": "scan/b.bin", "format": "binary",
            "dtype": "float64", "shape": [n_b], "sha256": _sha256(axis_b.tobytes()),
        },
        "energies": {
            "path": "scan/e.bin", "format": "binary",
            "dtype": "float64", "shape": [n_a, n_b],
            "sha256": _sha256(energies.tobytes()),
        },
    }
    if with_geometries:
        section_members["geometries"] = {
            "path": "scan/geo.bin", "format": "binary",
            "dtype": "float64", "shape": [n_a * n_b, 3, 3],
            "sha256": _sha256(geo_bytes),
        }

    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "t"},
        "sections": [
            {"id": "scan", "kind": "scan.surface", "members": section_members}
        ],
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("scan/meta.json", meta)
        zf.writestr("scan/a.bin", axis_a.tobytes())
        zf.writestr("scan/b.bin", axis_b.tobytes())
        zf.writestr("scan/e.bin", energies.tobytes())
        if with_geometries:
            zf.writestr("scan/geo.bin", geo_bytes)
    return Path(tmp.name), "scan"


class TestScanSurfaceReader:
    def test_axes_and_energies_round_trip(self):
        path, sid = _scan_surface_qvf()
        try:
            reader = QVFReader(path)
            data = reader.read_scan_surface(sid)
            assert data.axis_a.shape == (3,)
            assert data.axis_b.shape == (4,)
            assert data.energies.shape == (3, 4)
            assert data.coordinate_a_label == "bond 0–1"
            assert data.coordinate_a_unit == "bohr"
            assert data.coordinate_b_label == "angle 1–0–2"
            assert data.geometries is None
            assert data.atoms is None
        finally:
            path.unlink()

    def test_geometries_and_atoms_present(self):
        path, sid = _scan_surface_qvf(with_geometries=True)
        try:
            reader = QVFReader(path)
            data = reader.read_scan_surface(sid)
            assert data.geometries is not None
            assert data.geometries.shape == (12, 3, 3)
            assert data.atoms is not None
            assert [a.symbol for a in data.atoms] == ["O", "H", "H"]
        finally:
            path.unlink()


class TestScanSurfaceRenderer:
    def test_shape_and_min_node(self):
        path, sid = _scan_surface_qvf()
        try:
            reader = QVFReader(path)
            renderer = ScanSurfaceRenderer(reader.get_section(sid), reader)
            assert renderer.shape == (3, 4)
            assert renderer.min_node() == (1, 2)
        finally:
            path.unlink()

    def test_render_surface_produces_png(self):
        path, sid = _scan_surface_qvf()
        try:
            reader = QVFReader(path)
            renderer = ScanSurfaceRenderer(reader.get_section(sid), reader)
            png = renderer.render_surface(current_node=(0, 0))
            assert png.startswith(b"\x89PNG")
            # Also works without a current-node marker.
            assert renderer.render_surface().startswith(b"\x89PNG")
        finally:
            path.unlink()
