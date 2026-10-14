"""Tests for Blender export module."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from tests._qvf_builders import vibe_qc_source, write_archive


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_qvf(sections: list[dict], files: dict[str, bytes] | None = None) -> Path:
    return write_archive(sections, files, source=vibe_qc_source("0.9.0", "test"))


@pytest.fixture
def water_structure_qvf() -> Path:
    """A structure-only water archive. Named apart from conftest's
    ``sample_qvf`` (water plus a density grid), which it used to shadow."""
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


class TestBlenderExport:
    """Test the Blender scene export."""

    def test_import(self) -> None:
        """Module imports cleanly."""
        from vibeview.blender_export import export_blender_script

        assert callable(export_blender_script)

    def test_generated_script_is_valid_python(self, water_structure_qvf: Path) -> None:
        """Regression: the generated script must be valid Python and its
        embedded data must round-trip through json.loads (bug: raw json.dumps
        emitted ``null`` / ``[false,...]`` as Python literals, and json.loads
        was called on already-decoded lists)."""
        import json as _json

        from vibeview.blender_export import export_blender_script
        from vibeview.qvf import QVFReader

        reader = QVFReader(water_structure_qvf)
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            path = f.name
        try:
            src = export_blender_script(reader, path, style="ball_and_stick")
            # Whole file must compile as Python.
            compile(src, path, "exec")
            # Exec just the embedded-data assignments and re-parse them the way
            # the body does.
            g: dict = {"json": _json}
            for line in src.splitlines():
                if any(
                    line.startswith(name + " =")
                    for name in ("ATOM_DATA", "SYMBOLS", "LATTICE_VECTORS", "HAS_PBC")
                ):
                    exec(line, g)
            atoms = _json.loads(g["ATOM_DATA"])
            symbols = _json.loads(g["SYMBOLS"])
            assert len(atoms) == 3
            assert symbols == ["O", "H", "H"]
            assert _json.loads(g["LATTICE_VECTORS"]) is None
            assert _json.loads(g["HAS_PBC"]) == [False, False, False]
        finally:
            Path(path).unlink(missing_ok=True)

    def test_export_creates_file(self, water_structure_qvf: Path) -> None:
        """Export creates a .py file with expected content."""
        from vibeview.blender_export import export_blender_script
        from vibeview.qvf import QVFReader

        reader = QVFReader(water_structure_qvf)
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            path = f.name

        try:
            script = export_blender_script(reader, path, style="ball_and_stick")
            assert Path(path).exists()
            content = Path(path).read_text()
            # Header elements
            assert "import bpy" in content
            assert "import mathutils" in content
            assert "CYCLES" in content
            assert "camera" in content.lower()
            # Lighting
            assert "add_light" in content
            assert "Key" in content
            assert "Fill" in content
            assert "Rim" in content
            # Materials
            assert "Principled BSDF" in content
            assert "create_material" in content
            # Data
            assert "ATOM_DATA" in content
            assert "BOND_DATA" in content
            # Scene string should match file content
            assert script == content
        finally:
            Path(path).unlink(missing_ok=True)

    def test_export_styles(self, water_structure_qvf: Path) -> None:
        """All four styles produce valid scripts."""
        from vibeview.blender_export import export_blender_script
        from vibeview.qvf import QVFReader

        reader = QVFReader(water_structure_qvf)
        for style in ["ball_and_stick", "space_filling", "sticks_only", "wireframe"]:
            with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
                path = f.name
            try:
                export_blender_script(reader, path, style=style)
                content = Path(path).read_text()
                assert "import bpy" in content
                assert "ATOM_DATA" in content
                # Style name appears in auto-generated comment
                assert style in content
            finally:
                Path(path).unlink(missing_ok=True)

    def test_cpk_colors(self) -> None:
        """The script carries the viewer's CPK colours for the elements present (#54)."""
        from vibeview.blender_export import _cpk_rgb_floats, _element_table
        from vibeview.elements import cpk_hex
        from vibeview.renderers import palette

        table = _element_table([1, 6, 6, 8, 999], _cpk_rgb_floats)
        assert set(table) == {1, 6, 8}  # unknown elements keep the script's fallback
        r, g, b = palette.rgb(cpk_hex(6))
        assert table[6] == (round(r / 255, 3), round(g / 255, 3), round(b / 255, 3))

    def test_export_no_structure_graceful(self) -> None:
        """Exporting a QVF with no structure section produces a graceful
        error script."""
        from vibeview.blender_export import export_blender_script
        from vibeview.qvf import QVFReader

        path = _make_qvf([])
        try:
            reader = QVFReader(path)
            with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
                py_path = f.name
            try:
                script = export_blender_script(reader, py_path)
                assert "No structure data available" in script
            finally:
                Path(py_path).unlink(missing_ok=True)
        finally:
            path.unlink()

    def test_export_from_qvf_convenience(self, water_structure_qvf: Path) -> None:
        """export_from_qvf convenience wrapper produces a .py file."""
        from vibeview.blender_export import export_from_qvf

        with tempfile.TemporaryDirectory() as tmpdir:
            out = export_from_qvf(str(water_structure_qvf), output_dir=tmpdir)
            assert out.endswith("_blender.py")
            assert Path(out).exists()
            content = Path(out).read_text()
            assert "import bpy" in content
            assert "ATOM_DATA" in content

    def test_export_with_explicit_bonds(self) -> None:
        """Explicit bonds from a bonds section appear in the generated script."""
        from vibeview.blender_export import export_blender_script
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
        qvf_path = _make_qvf(
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
            reader = QVFReader(qvf_path)
            with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
                py_path = f.name
            try:
                export_blender_script(reader, py_path)
                content = Path(py_path).read_text()
                # Should contain bond data with explicit pairs
                assert "BOND_DATA" in content
                assert "[0, 1]" in content  # bond pair in JSON
            finally:
                Path(py_path).unlink(missing_ok=True)
        finally:
            qvf_path.unlink()

    def test_export_periodic_cell(self) -> None:
        """Periodic structure script includes unit cell lattice data."""
        from vibeview.blender_export import export_blender_script
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
        qvf_path = _make_qvf(
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
            reader = QVFReader(qvf_path)
            with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
                py_path = f.name
            try:
                export_blender_script(reader, py_path)
                content = Path(py_path).read_text()
                assert "LATTICE_VECTORS" in content
                assert "HAS_PBC" in content
                assert "true" in content  # PBC flags serialised as JSON booleans
                assert "cell_edge" in content  # unit cell rendering code
                # The cell-edge loop iterates 12 times with primitive_cylinder_add inside
                assert "edge_pairs" in content
                assert "for ei, (si, sj) in enumerate(edge_pairs)" in content
            finally:
                Path(py_path).unlink(missing_ok=True)
        finally:
            qvf_path.unlink()

    def test_molecule_centered(self, water_structure_qvf: Path) -> None:
        """Generated positions should be centred around origin."""
        from vibeview.blender_export import export_blender_script
        from vibeview.qvf import QVFReader

        reader = QVFReader(water_structure_qvf)
        script = export_blender_script(reader, "/dev/null")

        # The structure data should not appear at raw coords
        assert "0.117" not in script.split("ATOM_DATA")[1].split("BOND_DATA")[0]

    def test_space_filling_has_no_bonds(self, water_structure_qvf: Path) -> None:
        """Space-filling style produces empty bond data."""
        from vibeview.blender_export import export_blender_script
        from vibeview.qvf import QVFReader

        reader = QVFReader(water_structure_qvf)
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            path = f.name
        try:
            export_blender_script(reader, path, style="space_filling")
            content = Path(path).read_text()
            # Bond data is embedded as a JSON *string literal* (valid Python)
            # that the body decodes with json.loads; empty for space-filling.
            assert 'BOND_DATA = "[]"' in content
        finally:
            Path(path).unlink(missing_ok=True)

    def test_rendersettings_present(self, water_structure_qvf: Path) -> None:
        """Generated script includes Cycles render settings."""
        from vibeview.blender_export import export_blender_script
        from vibeview.qvf import QVFReader

        reader = QVFReader(water_structure_qvf)
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            path = f.name
        try:
            export_blender_script(reader, path, width=1024, height=768)
            content = Path(path).read_text()
            assert "resolution_x = 1024" in content
            assert "resolution_y = 768" in content
            assert "cycles.samples = 256" in content
            assert "cycles.use_denoising = True" in content
            assert "film_transparent = True" in content
        finally:
            Path(path).unlink(missing_ok=True)
