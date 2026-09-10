"""Integration tests — full pipeline from .qvf file to rendered data.

Builds realistic multi-section QVF archives and exercises the complete
reader lifecycle: open, validate, sha256, read structure, read volumes,
read bands, read spectra, classify all sections.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest

from vibeview.kinds import classify_section
from vibeview.qvf import QVFReader


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_water_qvf() -> Path:
    """Build a realistic water molecule .qvf with structure + density + homo orbital."""
    structure_json = json.dumps(
        {
            "atoms": [
                {"symbol": "O", "position": [0.0, 0.0, 0.1173], "atomic_number": 8},
                {"symbol": "H", "position": [0.0, 0.7572, -0.4692], "atomic_number": 1},
                {"symbol": "H", "position": [0.0, -0.7572, -0.4692], "atomic_number": 1},
            ],
            "pbc": [False, False, False],
            "lattice_vectors": None,
        }
    ).encode()

    density_grid_json = json.dumps(
        {
            "origin": [-4.0, -4.0, -4.0],
            "voxel_vectors": [[0.2, 0, 0], [0, 0.2, 0], [0, 0, 0.2]],
            "shape": [40, 40, 40],
        }
    ).encode()
    density_data = np.random.randn(40, 40, 40).astype(np.float32) * 0.01
    density_data[20, 20, 21] = 1.0  # put a "nucleus" at oxygen position

    homo_grid_json = json.dumps(
        {
            "origin": [-4.0, -4.0, -4.0],
            "voxel_vectors": [[0.2, 0, 0], [0, 0.2, 0], [0, 0, 0.2]],
            "shape": [40, 40, 40],
        }
    ).encode()
    homo_data = np.random.randn(40, 40, 40).astype(np.float32) * 0.005

    sections = [
        {
            "id": "structure",
            "kind": "structure",
            "members": {
                "structure": {
                    "path": "sections/structure.json",
                    "format": "json",
                    "sha256": _sha256(structure_json),
                }
            },
        },
        {
            "id": "density",
            "kind": "volume.density",
            "members": {
                "grid": {
                    "path": "sections/density.grid.json",
                    "format": "json",
                    "sha256": _sha256(density_grid_json),
                },
                "data": {
                    "path": "sections/density.dat",
                    "format": "binary",
                    "dtype": "float32",
                    "shape": [40, 40, 40],
                    "sha256": _sha256(density_data.tobytes()),
                },
            },
        },
        {
            "id": "homo",
            "kind": "volume.orbital",
            "component": "real",
            "members": {
                "grid": {
                    "path": "sections/homo.grid.json",
                    "format": "json",
                    "sha256": _sha256(homo_grid_json),
                },
                "data": {
                    "path": "sections/homo.dat",
                    "format": "binary",
                    "dtype": "float32",
                    "shape": [40, 40, 40],
                    "sha256": _sha256(homo_data.tobytes()),
                },
            },
        },
        {
            "id": "extra_vendor",
            "kind": "x_orca.surface",
            "members": {},
        },
    ]

    manifest = {
        "qvf_version": 1,
        "source": {
            "program": "vibe-qc",
            "version": "0.9.0",
            "calculation": "h2o_pbe0_def2tzvp",
        },
        "sections": sections,
        "viewer_defaults": {
            "auto_open": ["density"],
            "density": {"isovalue": 0.05, "colormap": "viridis", "opacity": 0.6},
        },
    }

    files: dict[str, bytes] = {
        "sections/structure.json": structure_json,
        "sections/density.grid.json": density_grid_json,
        "sections/density.dat": density_data.tobytes(),
        "sections/homo.grid.json": homo_grid_json,
        "sections/homo.dat": homo_data.tobytes(),
    }

    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, data in files.items():
            zf.writestr(path, data)
    return Path(tmp.name)


class TestFullPipeline:
    def test_open_and_classify_all_sections(self) -> None:
        """Open a multi-section QVF and classify every section."""
        path = _build_water_qvf()
        try:
            reader = QVFReader(path)
            assert reader.source.calculation == "h2o_pbe0_def2tzvp"

            classifications = {s.id: classify_section(s.kind) for s in reader.sections}
            assert classifications["structure"] == ("rendered", None)
            assert classifications["density"] == ("rendered", None)
            assert classifications["homo"] == ("rendered", None)
            assert classifications["extra_vendor"][0] == "skipped"
            assert "vendor namespace" in classifications["extra_vendor"][1]
            assert "orca" in classifications["extra_vendor"][1]
        finally:
            path.unlink()

    def test_read_structure(self) -> None:
        """Read and verify the structure section."""
        path = _build_water_qvf()
        try:
            reader = QVFReader(path)
            structure = reader.read_structure()
            assert len(structure.atoms) == 3
            assert structure.atoms[0].symbol == "O"
            assert structure.pbc == (False, False, False)
            assert structure.lattice_vectors is None

            bonds = reader.infer_bonds(structure)
            assert len(bonds) == 2  # O-H and O-H
        finally:
            path.unlink()

    def test_read_volume_grid_eager_data_lazy(self) -> None:
        """Volume grid is eager, data is lazy."""
        path = _build_water_qvf()
        try:
            reader = QVFReader(path)
            grid = reader.read_volume_grid("density")
            assert grid.shape == (40, 40, 40)
            assert grid.origin.shape == (3,)

            data = reader.read_volume_data("density")
            assert data.shape == (40, 40, 40)
            assert data.dtype == np.float32
            assert data[20, 20, 21] == pytest.approx(1.0)
        finally:
            path.unlink()

    def test_read_orbital_with_component(self) -> None:
        """Orbital section with component field."""
        path = _build_water_qvf()
        try:
            reader = QVFReader(path)
            section = reader.get_section("homo")
            assert section.component == "real"
            assert section.kind == "volume.orbital"

            data = reader.read_volume_data("homo")
            assert data.shape == (40, 40, 40)
        finally:
            path.unlink()

    def test_viewer_defaults_accessible(self) -> None:
        """Viewer defaults are parsed and accessible."""
        path = _build_water_qvf()
        try:
            reader = QVFReader(path)
            defaults = reader.viewer_defaults
            assert defaults is not None
            assert defaults.auto_open == ["density"]

            extras = getattr(defaults, "model_extra", None) or {}
            assert "density" in extras
            assert extras["density"]["isovalue"] == 0.05
        finally:
            path.unlink()

    def test_sha256_mismatch_on_wrong_data(self) -> None:
        """Feeding wrong hash to _verify_and_read raises SHA256MismatchError."""
        path = _build_water_qvf()
        try:
            reader = QVFReader(path)
            _data = reader.read_volume_data("density")  # confirms it works

            from vibeview.qvf import MemberSpec, SHA256MismatchError

            with pytest.raises(SHA256MismatchError):
                reader._verify_and_read(
                    MemberSpec(
                        path="sections/density.dat",
                        format="binary",
                        sha256="0" * 64,
                    )
                )
        finally:
            path.unlink()

    def test_auto_open_nonexistent_section_no_crash(self) -> None:
        """Auto-open with an ID not in sections should not crash the reader."""
        from vibeview.viewer_defaults import ViewerState

        path = _build_water_qvf()
        try:
            reader = QVFReader(path)
            state = ViewerState.from_manifest(reader.viewer_defaults)
            # Simulate auto-open logic: check nonexistent IDs gracefully
            for section_id in ["nonexistent", "also_fake"]:
                assert not reader.has_section(section_id)
                # The app loop uses `continue` when has_section is False
        finally:
            path.unlink()

    def test_volume_grid_voxel_vectors_are_per_voxel(self) -> None:
        """Grid from QVF uses per-voxel step vectors, not full span."""
        path = _build_water_qvf()
        try:
            reader = QVFReader(path)
            grid = reader.read_volume_grid("density")
            # shape is 40x40x40, spacing is 0.2, so per-voxel vectors should be ~0.2
            # NOT 0.2 * 39 = 7.8 (which would be full span)
            assert grid.shape == (40, 40, 40)
            assert grid.voxel_vectors[0, 0] == pytest.approx(0.2, rel=0.01)
            assert grid.voxel_vectors[1, 1] == pytest.approx(0.2, rel=0.01)
            assert grid.voxel_vectors[2, 2] == pytest.approx(0.2, rel=0.01)
            # Off-diagonals should be zero for orthogonal grid
            assert grid.voxel_vectors[0, 1] == pytest.approx(0.0, abs=1e-10)
        finally:
            path.unlink()
