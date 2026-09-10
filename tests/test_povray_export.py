"""Tests for POV-Ray export module."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_qvf(
    sections: list[dict],
    files: dict[str, bytes] | None = None,
) -> Path:
    """Create a minimal valid .qvf file in a temp directory."""
    manifest = {
        "qvf_version": 1,
        "source": {
            "program": "vibe-qc",
            "version": "0.9.0",
            "calculation": "test",
        },
        "sections": sections,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        if files:
            for path, data in files.items():
                zf.writestr(path, data)
    return Path(tmp.name)


@pytest.fixture
def sample_qvf() -> Path:
    """Create a QVF with a water-like molecule for testing."""
    structure = json.dumps(
        {
            "atoms": [
                {"symbol": "O", "position": [0.000, 0.000, 0.117], "atomic_number": 8},
                {"symbol": "H", "position": [0.757, 0.000, -0.469], "atomic_number": 1},
                {"symbol": "H", "position": [-0.757, 0.000, -0.469], "atomic_number": 1},
            ],
            "pbc": [False, False, False],
        }
    ).encode()
    path = _make_qvf(
        [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "sections/structure.json",
                        "format": "json",
                        "sha256": _sha256(structure),
                    }
                },
            }
        ],
        files={"sections/structure.json": structure},
    )
    return path


class TestPOVRayExport:
    """Test the POV-Ray scene export."""

    def test_import(self) -> None:
        """Module imports cleanly."""
        from vibeview.povray_export import export_povray

        assert callable(export_povray)

    def test_export_creates_file(self, sample_qvf: Path) -> None:
        """Export creates a .pov file with expected content."""
        from vibeview.povray_export import export_povray
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        with tempfile.NamedTemporaryFile(suffix=".pov", delete=False) as f:
            path = f.name

        try:
            scene = export_povray(reader, path, style="ball_and_stick")
            assert Path(path).exists()
            content = Path(path).read_text()
            assert "camera" in content
            assert "light_source" in content
            assert "sphere" in content
            assert "finish" in content
            # Scene string should match file content
            assert scene == content
        finally:
            Path(path).unlink(missing_ok=True)

    def test_export_styles(self, sample_qvf: Path) -> None:
        """All four styles produce valid .pov files."""
        from vibeview.povray_export import export_povray
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        for style in ["ball_and_stick", "space_filling", "sticks_only", "wireframe"]:
            with tempfile.NamedTemporaryFile(suffix=".pov", delete=False) as f:
                path = f.name
            try:
                export_povray(reader, path, style=style)
                content = Path(path).read_text()
                assert "camera" in content
                assert "sphere" in content
            finally:
                Path(path).unlink(missing_ok=True)

    def test_detect_bonds(self) -> None:
        """Bond detection finds bonds between close atoms."""
        from vibeview.povray_export import _detect_bonds

        # H2 molecule: two H atoms at ~0.74 A
        positions = np.array([[0.0, 0.0, 0.0], [0.74, 0.0, 0.0]])
        z_list = [1, 1]  # H-H
        bonds = _detect_bonds(positions, z_list)
        assert len(bonds) > 0

        # Far apart atoms
        positions_far = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]])
        bonds_far = _detect_bonds(positions_far, z_list)
        assert len(bonds_far) == 0

    def test_cpk_colors(self) -> None:
        """CPK colour map has entries for common elements."""
        from vibeview.povray_export import CPK_COLORS, _cpk_rgb

        assert 1 in CPK_COLORS  # H
        assert 6 in CPK_COLORS  # C
        assert 7 in CPK_COLORS  # N
        assert 8 in CPK_COLORS  # O

        color = _cpk_rgb(6)
        assert "<" in color
        assert ">" in color

    def test_unknown_element_falls_back_to_grey(self) -> None:
        """Elements not in CPK table get a neutral grey."""
        from vibeview.povray_export import _cpk_rgb

        color = _cpk_rgb(999)
        assert "0.700" in color  # the fallback grey component

    def test_bond_cylinder_output(self) -> None:
        """_bond_cylinder produces valid POV-Ray cylinder syntax."""
        from vibeview.povray_export import _bond_cylinder

        result = _bond_cylinder(
            np.array([0.0, 0.0, 0.0]),
            np.array([1.0, 0.0, 0.0]),
            radius=0.1,
        )
        assert "cylinder" in result
        assert "pigment" in result
        assert "finish" in result

    def test_bond_cylinder_zero_length(self) -> None:
        """Zero-length bond returns empty string."""
        from vibeview.povray_export import _bond_cylinder

        result = _bond_cylinder(
            np.array([0.0, 0.0, 0.0]),
            np.array([0.0, 0.0, 0.0]),
        )
        assert result == ""

    def test_atom_sphere_output(self) -> None:
        """_atom_sphere produces valid POV-Ray sphere syntax."""
        from vibeview.povray_export import _atom_sphere

        pos = np.array([0.0, 0.0, 0.0])
        result = _atom_sphere(pos, z=6, radius=0.5)
        assert "sphere" in result
        assert "pigment" in result
        assert "atom_finish" in result

    def test_vdw_radius_exceeds_covalent(self) -> None:
        """Regression: space_filling claimed vdW but used covalent radii."""
        from vibeview.povray_export import _COVALENT_RADII, _vdw_radius

        # vdW radius exceeds covalent for tabulated elements.
        assert _vdw_radius(8) == 1.52
        assert _vdw_radius(8) > _COVALENT_RADII[8]
        # 1.75x covalent fallback for untabulated elements.
        assert _vdw_radius(53) == _COVALENT_RADII[53] * 1.75

    def test_space_filling_uses_vdw_radii(self, sample_qvf: Path) -> None:
        """space_filling spheres must be sized by vdW radii (e.g. O -> 1.52)."""
        import tempfile

        from vibeview.povray_export import export_povray
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        with tempfile.NamedTemporaryFile(suffix=".pov", delete=False) as f:
            path = f.name
        try:
            pov = export_povray(reader, path, style="space_filling")
            # The O sphere radius should be the vdW value 1.5200, not the
            # covalent 0.6600.
            assert "1.5200" in pov
            assert ", 0.6600" not in pov
        finally:
            Path(path).unlink(missing_ok=True)

    def test_isosurface_deadcode_removed(self) -> None:
        """Regression: dead _iso_surface_mesh + unused include_isosurfaces
        param were removed."""
        import inspect

        import vibeview.povray_export as pov

        assert not hasattr(pov, "_iso_surface_mesh")
        sig = inspect.signature(pov.export_povray)
        assert "include_isosurfaces" not in sig.parameters

    def test_export_periodic_cell(self) -> None:
        """Periodic structure includes unit cell wireframe."""
        from vibeview.povray_export import export_povray
        from vibeview.qvf import QVFReader

        structure = json.dumps(
            {
                "atoms": [
                    {"symbol": "Si", "position": [0.0, 0.0, 0.0], "atomic_number": 14},
                ],
                "pbc": [True, True, True],
                "lattice_vectors": [
                    [2.715, 2.715, 0.000],
                    [0.000, 2.715, 2.715],
                    [2.715, 0.000, 2.715],
                ],
            }
        ).encode()
        path = _make_qvf(
            [
                {
                    "id": "structure",
                    "kind": "structure",
                    "members": {
                        "structure": {
                            "path": "sections/structure.json",
                            "format": "json",
                            "sha256": _sha256(structure),
                        }
                    },
                }
            ],
            files={"sections/structure.json": structure},
        )
        try:
            reader = QVFReader(path)
            with tempfile.NamedTemporaryFile(suffix=".pov", delete=False) as f:
                pov_path = f.name
            try:
                export_povray(reader, pov_path)
                content = Path(pov_path).read_text()
                assert "Unit cell" in content
                # Should have 12 cell edges (cylinders)
                assert content.count("cylinder") >= 12
            finally:
                Path(pov_path).unlink(missing_ok=True)
        finally:
            path.unlink()

    def test_export_with_explicit_bonds(self) -> None:
        """Explicit bonds from a bonds section are used."""
        from vibeview.povray_export import export_povray
        from vibeview.qvf import QVFReader

        structure = json.dumps(
            {
                "atoms": [
                    {"symbol": "H", "position": [0.0, 0.0, 0.0], "atomic_number": 1},
                    {"symbol": "H", "position": [0.74, 0.0, 0.0], "atomic_number": 1},
                ],
                "pbc": [False, False, False],
            }
        ).encode()
        bonds = json.dumps({"pairs": [{"i": 0, "j": 1, "order": 1.0}]}).encode()
        path = _make_qvf(
            [
                {
                    "id": "structure",
                    "kind": "structure",
                    "members": {
                        "structure": {
                            "path": "sections/structure.json",
                            "format": "json",
                            "sha256": _sha256(structure),
                        }
                    },
                },
                {
                    "id": "bonds0",
                    "kind": "bonds",
                    "members": {
                        "bonds": {
                            "path": "bonds/connectivity.json",
                            "format": "json",
                            "sha256": _sha256(bonds),
                        }
                    },
                },
            ],
            files={
                "sections/structure.json": structure,
                "bonds/connectivity.json": bonds,
            },
        )
        try:
            reader = QVFReader(path)
            with tempfile.NamedTemporaryFile(suffix=".pov", delete=False) as f:
                pov_path = f.name
            try:
                export_povray(reader, pov_path)
                content = Path(pov_path).read_text()
                # Bonds section in output
                assert "Bonds" in content
                assert "cylinder" in content
            finally:
                Path(pov_path).unlink(missing_ok=True)
        finally:
            path.unlink()

    def test_export_no_structure_graceful(self) -> None:
        """Exporting a QVF with no structure section is graceful."""
        from vibeview.povray_export import export_povray
        from vibeview.qvf import QVFReader

        path = _make_qvf([])
        try:
            reader = QVFReader(path)
            with tempfile.NamedTemporaryFile(suffix=".pov", delete=False) as f:
                pov_path = f.name
            try:
                scene = export_povray(reader, pov_path)
                assert "No structure data available" in scene
            finally:
                Path(pov_path).unlink(missing_ok=True)
        finally:
            path.unlink()

    def test_export_from_qvf_convenience(self, sample_qvf: Path) -> None:
        """export_from_qvf convenience wrapper produces a .pov file."""
        from vibeview.povray_export import export_from_qvf

        with tempfile.TemporaryDirectory() as tmpdir:
            out = export_from_qvf(str(sample_qvf), output_dir=tmpdir)
            assert out.endswith(".pov")
            assert Path(out).exists()
            content = Path(out).read_text()
            assert "camera" in content
