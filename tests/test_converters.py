"""Tests for vibe-view format converters (XYZ, CIF, cube → QVF)."""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.converters import (
    cif_to_qvf,
    cube_to_qvf,
    detect_format,
    gjf_to_qvf,
    gro_to_qvf,
    mol2_to_qvf,
    pdb_to_qvf,
    sdf_to_qvf,
    xyz_to_qvf,
)
from vibeview.qvf import QVFReader


class TestDetectFormat:
    def test_qvf(self) -> None:
        assert detect_format("file.qvf") == "qvf"
        assert detect_format(Path("path/to/file.QVF")) == "qvf"

    def test_xyz(self) -> None:
        assert detect_format("water.xyz") == "xyz"

    def test_cif(self) -> None:
        assert detect_format("diamond.cif") == "cif"

    def test_cube(self) -> None:
        assert detect_format("density.cube") == "cube"

    def test_pdb(self) -> None:
        assert detect_format("protein.pdb") == "pdb"

    def test_mol2(self) -> None:
        assert detect_format("ligand.mol2") == "mol2"

    def test_gjf(self) -> None:
        assert detect_format("input.gjf") == "gjf"
        assert detect_format("input.com") == "gjf"

    def test_gro(self) -> None:
        assert detect_format("md.gro") == "gro"

    def test_sdf(self) -> None:
        assert detect_format("mol.sdf") == "sdf"
        assert detect_format("mol.mol") == "sdf"

    def test_unknown(self) -> None:
        assert detect_format("file.txt") is None
        assert detect_format("file") is None


class TestXYZConverter:
    def test_water(self) -> None:
        xyz = (
            "3\nwater\nO   0.000   0.000   0.117\n"
            "H   0.757   0.000  -0.469\nH  -0.757   0.000  -0.469\n"
        )
        buf = xyz_to_qvf(xyz.encode())
        reader = QVFReader(buf)
        struct = reader.read_structure()
        assert len(struct.atoms) == 3
        assert struct.atoms[0].symbol == "O"
        assert struct.atoms[0].atomic_number == 8
        assert struct.atoms[1].symbol == "H"
        assert struct.pbc == (False, False, False)
        assert struct.lattice_vectors is None

    def test_from_path(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".xyz", mode="w", delete=False) as f:
            f.write("2\nHe dimer\nHe 0 0 0\nHe 1.5 0 0\n")
            path = Path(f.name)
        try:
            buf = xyz_to_qvf(path)
            reader = QVFReader(buf)
            struct = reader.read_structure()
            assert len(struct.atoms) == 2
        finally:
            path.unlink()

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            xyz_to_qvf(b"")

    def test_no_atoms_raises(self) -> None:
        with pytest.raises(ValueError, match="no atoms"):
            xyz_to_qvf(b"0\ncomment\n")

    def test_bytesio(self) -> None:
        buf_in = io.BytesIO(b"1\nHe\nHe 0 0 0\n")
        buf_out = xyz_to_qvf(buf_in)
        reader = QVFReader(buf_out)
        struct = reader.read_structure()
        assert len(struct.atoms) == 1


class TestCIFConverter:
    @staticmethod
    def _coordinate_input(family: str, rows: list[str], axes: str = "xyz") -> str:
        return (
            "data_sites\n"
            "_cell_length_a 8\n_cell_length_b 9\n_cell_length_c 10\n"
            "_cell_angle_alpha 90\n_cell_angle_beta 90\n_cell_angle_gamma 90\n"
            "loop_\n_atom_site_label\n_atom_site_type_symbol\n"
            + "".join(f"_atom_site_{family}_{axis}\n" for axis in axes)
            + "\n".join(rows) + "\n"
        )

    @pytest.mark.parametrize("family", ["fract", "Cartn"])
    @pytest.mark.parametrize("axis", range(3))
    @pytest.mark.parametrize("value", ["?", ".", "nan", "inf", "-inf", "1e309"])
    def test_invalid_site_coordinate_refuses_archive(self, tmp_path, family, axis, value):
        coordinates = ["0.25", "0.5", "0.75"]
        coordinates[axis] = value
        text = self._coordinate_input(
            family, ["O1 O 0 0 0", "H1 H " + " ".join(coordinates), "H2 H 1 1 1"]
        )
        with pytest.raises(ValueError, match="CIF atom site H1.*coordinate"):
            cif_to_qvf(text.encode())

        source = tmp_path / "unknown.cif"
        output = tmp_path / "output.qvf"
        source.write_text(text, encoding="utf-8")
        output.write_bytes(b"existing output")
        result = CliRunner().invoke(main, ["import", str(source), "-o", str(output), "--force"])
        assert result.exit_code == 1, result.output
        assert "CIF atom site H1" in result.output
        assert output.read_bytes() == b"existing output"

    @pytest.mark.parametrize("family", ["fract", "Cartn"])
    @pytest.mark.parametrize("missing_axis", "xyz")
    def test_missing_coordinate_column_is_an_error(self, family, missing_axis):
        axes = "xyz".replace(missing_axis, "")
        text = self._coordinate_input(family, ["H1 H 0.25 0.75"], axes)
        with pytest.raises(ValueError, match="CIF atom site H1.*coordinate"):
            cif_to_qvf(text.encode())

    @pytest.mark.parametrize("family", ["fract", "Cartn"])
    def test_zero_and_uncertainty_coordinates_still_import(self, family):
        text = self._coordinate_input(family, ["O1 O 0.0(2) -0.25(3) 5E-1(1)"])
        with QVFReader(cif_to_qvf(text.encode())) as reader:
            atoms = reader.read_structure().atoms
            assert [atom.symbol for atom in atoms] == ["O"]
            expected = [0, -2.25, 5] if family == "fract" else [0, -0.25, 0.5]
            assert atoms[0].position == pytest.approx(expected)

    def test_complete_cartesian_alternative_is_converted_per_site(self):
        text = self._coordinate_input("fract", []).replace(
            "_atom_site_fract_z\n",
            "_atom_site_fract_z\n"
            "_atom_site_Cartn_x\n_atom_site_Cartn_y\n_atom_site_Cartn_z\n",
        )
        text += "O1 O 0.25 0.5 0.75 . . .\nH1 H ? ? ? 1 2 3\n"
        with QVFReader(cif_to_qvf(text.encode())) as reader:
            atoms = reader.read_structure().atoms
            assert [atom.symbol for atom in atoms] == ["O", "H"]
            assert atoms[0].position == pytest.approx([2, 4.5, 7.5])
            assert atoms[1].position == pytest.approx([1, 2, 3])

    @pytest.mark.parametrize("later_sites", [False, True])
    @pytest.mark.parametrize("cell_after_sites", [False, True])
    def test_multiple_data_blocks_keep_first_structure_cell(
        self, tmp_path, later_sites, cell_after_sites
    ):
        first = self._coordinate_input("fract", ["C1 C 0.25 0.5 0.75"])
        if cell_after_sites:
            header, sites = first.split("loop_\n", 1)
            first = "data_sites\nloop_\n" + sites + header.split("\n", 1)[1]
        later = self._coordinate_input("fract", ["O1 O 0 0 0"])
        if not later_sites:
            later = later.split("loop_\n", 1)[0]
        later = later.replace("data_sites", "DaTa_other")
        for axis, length in zip("abc", [8, 9, 10], strict=True):
            later = later.replace(f"_cell_length_{axis} {length}\n",
                                  f"_cell_length_{axis} {length * 10}\n")
        source = tmp_path / "structures.cif"
        output = tmp_path / "output.qvf"
        source.write_text(first + later, encoding="utf-8")

        result = CliRunner().invoke(main, ["import", str(source), "-o", str(output)])

        assert result.exit_code == 0, result.output
        with QVFReader(output) as reader:
            structure = reader.read_structure()
            assert [atom.symbol for atom in structure.atoms] == ["C"]
            assert structure.atoms[0].position == pytest.approx([2, 4.5, 7.5])
            for vector, expected in zip(
                structure.lattice_vectors, [[8, 0, 0], [0, 9, 0], [0, 0, 10]], strict=True
            ):
                assert vector == pytest.approx(expected)

    @pytest.mark.parametrize("metadata_position", ["none", "before", "after", "both"])
    def test_molecular_sites_do_not_inherit_another_blocks_cell(self, metadata_position):
        periodic = self._coordinate_input("Cartn", ["C1 C 1 2 3"])
        header, sites = periodic.split("loop_\n", 1)
        metadata = header.replace("data_sites", "data_metadata")
        molecule = "data_molecule\nloop_\n" + sites
        text = (
            (metadata if metadata_position in ("before", "both") else "")
            + molecule
            + (metadata.replace("data_metadata", "data_later")
               if metadata_position in ("after", "both") else "")
        )
        with QVFReader(cif_to_qvf(text.encode())) as reader:
            structure = reader.read_structure()
            assert structure.pbc == (False, False, False)
            assert structure.atoms[0].position == pytest.approx([1, 2, 3])

    @pytest.mark.parametrize("delimiter", ["'", '"', ";"])
    def test_data_header_inside_text_is_not_a_block_boundary(self, delimiter):
        text = self._coordinate_input("fract", ["C1 C 0.25 0.5 0.75"])
        value = ("\n;\ndata_other\n;\n" if delimiter == ";"
                 else f" {delimiter}data_other{delimiter}\n")
        text = text.replace("_cell_length_b 9\n", f"_note{value}_cell_length_b 9\n")
        with QVFReader(cif_to_qvf(text.encode())) as reader:
            assert reader.read_structure().atoms[0].position == pytest.approx([2, 4.5, 7.5])

    def test_diamond_loop(self) -> None:
        """CIF with loop_ construct — standard crystallographic format."""
        cif = """data_diamond
_cell_length_a 3.567
_cell_length_b 3.567
_cell_length_c 3.567
_cell_angle_alpha 90.0
_cell_angle_beta 90.0
_cell_angle_gamma 90.0
loop_
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
C 0.000 0.000 0.000
C 0.250 0.250 0.250
"""
        buf = cif_to_qvf(cif.encode())
        reader = QVFReader(buf)
        struct = reader.read_structure()
        assert len(struct.atoms) == 2
        assert struct.pbc == (True, True, True)
        assert struct.lattice_vectors is not None
        # Cubic 3.567 Å cell.
        assert abs(struct.lattice_vectors[0, 0] - 3.567) < 0.001

    def test_no_atoms_raises(self) -> None:
        cif = "data_empty\n_cell_length_a 5.0\n_cell_length_b 5.0\n_cell_length_c 5.0\n"
        with pytest.raises(ValueError, match="no atom"):
            cif_to_qvf(cif.encode())

    def test_molecular_cif_no_cell(self) -> None:
        """CIF with atoms but no cell parameters → molecular structure."""
        cif = """data_mol
loop_
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
C 0.0 0.0 0.0
H 0.0 0.0 1.09
"""
        buf = cif_to_qvf(cif.encode())
        reader = QVFReader(buf)
        struct = reader.read_structure()
        assert len(struct.atoms) == 2
        # No cell → molecular, not periodic.
        assert struct.pbc == (False, False, False)


class TestCubeConverter:
    def test_minimal_cube(self) -> None:
        """5³ grid with a single peak at centre."""
        import numpy as np

        n = 5
        data = np.zeros((n, n, n), dtype=np.float32)
        data[2, 2, 2] = 1.0
        # Build cube lines.
        lines = ["test cube", "units: bohr"]
        lines.append("  1    0.0    0.0    0.0")
        for _n in (n, n, n):
            lines.append(f"  {_n}    0.5    0.0    0.0")
        lines.append("    6    6.0    0.0    0.0    0.0")
        flat = data.ravel(order="F")
        for i in range(0, len(flat), 6):
            lines.append(" ".join(f"{x:.6E}" for x in flat[i : i + 6]))
        cube_text = "\n".join(lines)

        buf = cube_to_qvf(cube_text.encode())
        reader = QVFReader(buf)
        sections = list(reader.sections)
        kinds = {s.kind for s in sections}
        assert "structure" in kinds
        assert "volume.density" in kinds

        struct = reader.read_structure()
        assert len(struct.atoms) == 1
        assert struct.atoms[0].symbol == "C"

        grid = reader.read_volume_grid("vol_0")
        assert grid.shape == (5, 5, 5)

        voldata = reader.read_volume_data("vol_0")
        assert abs(float(voldata.max()) - 1.0) < 0.01

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            cube_to_qvf(b"")


class TestPDBConverter:
    def test_alanine(self) -> None:
        pdb = """ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C
ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00           C
ATOM      4  O   ALA A   1       1.222   2.367   0.000  1.00  0.00           O
END
"""
        buf = pdb_to_qvf(pdb.encode())
        reader = QVFReader(buf)
        struct = reader.read_structure()
        assert len(struct.atoms) == 4
        assert struct.atoms[0].symbol == "N"
        assert struct.atoms[0].atomic_number == 7
        assert struct.pbc == (False, False, False)

    def test_with_cryst1(self) -> None:
        pdb = """CRYST1   50.000   50.000   50.000  90.00  90.00  90.00 P 1           1
ATOM      1  CA  CAL A   1      25.000  25.000  25.000  1.00  0.00           C
END
"""
        buf = pdb_to_qvf(pdb.encode())
        reader = QVFReader(buf)
        struct = reader.read_structure()
        assert len(struct.atoms) == 1
        assert struct.pbc == (True, True, True)
        assert struct.lattice_vectors is not None
        assert abs(struct.lattice_vectors[0][0] - 50.0) < 0.01

    def test_no_atoms_raises(self) -> None:
        with pytest.raises(ValueError, match="no ATOM"):
            pdb_to_qvf(b"HEADER test\nEND\n")


class TestGJFConverter:
    @pytest.mark.parametrize("newline", ["\n", "\r\n"])
    @pytest.mark.parametrize("title", ["", "water", "0 1", "1 2", "scan\n0 1", "0 1\nscan"])
    def test_title_is_separate_from_charge_multiplicity(self, title, newline) -> None:
        source = (
            f"%mem=1GB\n#p HF/sto-3g\n  scf=tight\n\n{title}\n \t\n"
            "-1 2\nO 1.0 2.0 3.0\n\n"
        ).replace("\n", newline)

        with QVFReader(gjf_to_qvf(source.encode())) as reader:
            structure = reader.read_structure()
            assert [atom.symbol for atom in structure.atoms] == ["O"]
            assert structure.atoms[0].position == pytest.approx([1, 2, 3])
            assert reader.provenance["charge"] == -1
            assert reader.provenance["multiplicity"] == 2

    @pytest.mark.parametrize("suffix", ["gjf", "com"])
    def test_cli_import_numeric_title_keeps_the_geometry(self, tmp_path, suffix) -> None:
        from click.testing import CliRunner

        from vibeview.cli import main

        source = tmp_path / f"input.{suffix}"
        output = tmp_path / "output.qvf"
        source.write_text(
            "#p HF/sto-3g\n\n0 1\n\n-1 2\nO 1 2 3\n\n"
            "--Link1--\n#p HF/sto-3g\n\nlater job\n\n0 1\nHe 5 6 7\n\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(main, ["import", str(source), "-o", str(output)])

        assert result.exit_code == 0, result.output
        with QVFReader(output) as reader:
            structure = reader.read_structure()
            assert [atom.symbol for atom in structure.atoms] == ["O"]
            assert structure.atoms[0].position == pytest.approx([1, 2, 3])
            assert reader.provenance["charge"] == -1
            assert reader.provenance["multiplicity"] == 2

    def test_water(self) -> None:
        gjf = """%mem=1GB
#p B3LYP/6-31G*

water

0 1
O    0.000000    0.000000    0.117000
H    0.757000    0.000000   -0.469000
H   -0.757000    0.000000   -0.469000

"""
        buf = gjf_to_qvf(gjf.encode())
        reader = QVFReader(buf)
        struct = reader.read_structure()
        assert len(struct.atoms) == 3
        assert struct.atoms[0].symbol == "O"
        assert struct.pbc == (False, False, False)

    def test_no_atoms_raises(self) -> None:
        with pytest.raises(ValueError, match="no atoms"):
            gjf_to_qvf(b"%mem=1GB\n#p B3LYP\n\n0 1\n\n")


class TestGROConverter:
    def test_water(self) -> None:
        gro = """water
    3
    1WATER  OW1    1   0.000   0.000   0.012
    1WATER  HW2    2   0.076   0.000  -0.047
    1WATER  HW3    3  -0.076   0.000  -0.047
   3.00000   3.00000   3.00000
"""
        buf = gro_to_qvf(gro.encode())
        reader = QVFReader(buf)
        struct = reader.read_structure()
        assert len(struct.atoms) == 3
        assert struct.pbc == (True, True, True)
        assert struct.lattice_vectors is not None

    def test_too_short_raises(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            gro_to_qvf(b"title\n1\n")


class TestSDFConverter:
    def test_water_with_bonds(self) -> None:
        sdf = (
            "water\n"
            "  -OEChem-06082515412D\n"
            "\n"
            "  3  2  0  0  0  0  0  0  0  0999 V2000\n"
            "    0.0000    0.0000    0.1170 O   0  0  0  0  0  0  0  0  0  0  0  0\n"
            "    0.7570    0.0000   -0.4690 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
            "   -0.7570    0.0000   -0.4690 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
            "  1  2  1  0  0  0  0\n"
            "  1  3  1  0  0  0  0\n"
            "M  END\n"
            "$$$$\n"
        )
        buf = sdf_to_qvf(sdf.encode())
        reader = QVFReader(buf)
        struct = reader.read_structure()
        assert len(struct.atoms) == 3
        assert struct.bonds == [(0, 1, 1.0, (0, 0, 0)), (0, 2, 1.0, (0, 0, 0))]
        assert struct.atoms[0].symbol == "O"

    def test_no_counts_raises(self) -> None:
        with pytest.raises(ValueError, match="no counts"):
            sdf_to_qvf(b"title\nprog\ncomment\nnot counts\n")

    def test_counts_line_fixed_columns(self) -> None:
        """V2000 counts must be sliced from the raw line, not a strip()ed
        copy (regression: '  8 12' crashed on int('8 1'); '  8100'
        parsed n_bonds=0)."""
        atom = "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0"
        bond = "  1  2  1  0"

        # 8 atoms / 12 bonds: strip() turned '  8 12' into '8 12' → int('8 1').
        sdf = "\n".join(
            ["m", "  prog", "", "  8 12  0  0  0  0  0  0  0  0999 V2000"]
            + [atom] * 8
            + [bond] * 12
            + ["M  END", "$$$$"]
        )
        reader = QVFReader(sdf_to_qvf(sdf.encode()))
        struct = reader.read_structure()
        assert len(struct.atoms) == 8
        assert len(struct.bonds) == 12

        # 8 atoms / 100 bonds: strip() shifted columns → n_bonds parsed as 0.
        sdf = "\n".join(
            ["m", "  prog", "", "  8100  0  0  0  0  0  0  0  0999 V2000"]
            + [atom] * 8
            + [bond] * 100
            + ["M  END", "$$$$"]
        )
        reader = QVFReader(sdf_to_qvf(sdf.encode()))
        struct = reader.read_structure()
        assert len(struct.atoms) == 8
        assert len(struct.bonds) == 100


class TestMOCubeDsetLine:
    def test_mo_cube_skips_dset_line(self) -> None:
        """MO cubes (negative n_atoms) carry a DSET/MO-index record after
        the atom block; its integers must not leak into the volume data
        (regression: first values became [1., 5., ...])."""
        import numpy as np

        cube = """MO cube
Molecular orbital 5
   -1    0.000000    0.000000    0.000000
    2    0.500000    0.000000    0.000000
    2    0.000000    0.500000    0.000000
    2    0.000000    0.000000    0.500000
    1    1.000000    0.000000    0.000000    0.000000
    1    5
  0.11111  0.22222  0.33333  0.44444
  0.55555  0.66666  0.77777  0.88888
"""
        reader = QVFReader(cube_to_qvf(cube.encode()))
        # Auto-detected as orbital from the comments.
        assert {s.kind for s in reader.sections} >= {"structure", "volume.orbital"}
        data = reader.read_volume_data("vol_0")
        assert np.allclose(
            data.ravel()[:4], [0.11111, 0.22222, 0.33333, 0.44444], atol=1e-5
        )


class TestUnknownElementAtomicNumber:
    """Every converter must always emit atomic_number (0 for unknown
    symbols) — QVFReader.read_structure reads it unconditionally
    (regression: raw KeyError on unrecognized elements)."""

    def _assert_reads(self, buf) -> None:
        reader = QVFReader(buf)
        struct = reader.read_structure()
        assert struct.atoms[0].atomic_number == 0

    def test_xyz_unknown_element(self) -> None:
        self._assert_reads(xyz_to_qvf(b"1\ncomment\nXx 0.0 0.0 0.0\n"))

    def test_cif_unknown_element(self) -> None:
        cif = """data_x
_cell_length_a 5.0
_cell_length_b 5.0
_cell_length_c 5.0
_cell_angle_alpha 90.0
_cell_angle_beta 90.0
_cell_angle_gamma 90.0
loop_
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Xx 0.0 0.0 0.0
"""
        self._assert_reads(cif_to_qvf(cif.encode()))

    def test_pdb_unknown_element(self) -> None:
        pdb = (
            "ATOM      1  Q   UNK A   1       0.000   0.000   0.000"
            "  1.00  0.00           Q \nEND\n"
        )
        self._assert_reads(pdb_to_qvf(pdb.encode()))

    def test_mol2_unknown_element(self) -> None:
        mol2 = """@<TRIPOS>MOLECULE
m
1 0
@<TRIPOS>ATOM
1 Qq1 0.0 0.0 0.0 Qq
"""
        self._assert_reads(mol2_to_qvf(mol2.encode()))


class TestPDBElementParsing:
    """PDB element typing, including files with no element column.

    Columns 77-78 carry the element but are absent from many real files
    (older entries, MD engine output). The fallback must use the
    convention that gives columns 13-16 their meaning: the element is
    **right-justified in columns 13-14**, so a blank column 13 marks a
    one-letter element.

    Before this was honoured, a 150k-atom protein came out with 885
    "calcium" atoms (it contains none — they are backbone alpha-carbons)
    and ~36,000 atoms typed from hydrogen names as Hr/Hs/Hg/Ha/Hb/Hd —
    Hg being mercury. Wrong colours, wrong radii, wrong bonding, and
    wrong chemistry in anything exported downstream.
    """

    @staticmethod
    def _record(atom_name: str, element_col: str = "") -> str:
        body = (
            "ATOM      1 "
            + atom_name.ljust(4)
            + " ALA A   1      45.890  60.370  12.760  1.00  0.00"
        )
        return body.ljust(76) + element_col

    def test_backbone_alpha_carbon_is_carbon_not_calcium(self):
        """The single distinguishing column: ' CA ' vs 'CA  '."""
        from vibeview.converters import _pdb_element

        assert _pdb_element(self._record(" CA ")) == "C"
        assert _pdb_element(self._record("CA  ")) == "Ca"

    def test_hydrogen_names_never_become_metals_or_noble_gases(self):
        """A real two-letter element leaves columns 15-16 blank; a
        hydrogen spends them on its position suffix."""
        from vibeview.converters import _pdb_element

        for name in ("HG21", "HE1 ", "HD1 ", "HR1 ", "HB2 ", "1HB "):
            assert _pdb_element(self._record(name)) == "H", name
        # ...while the genuine elements still resolve
        assert _pdb_element(self._record("HG  ")) == "Hg"
        assert _pdb_element(self._record("HE  ")) == "He"

    def test_genuine_two_letter_elements_survive(self):
        from vibeview.converters import _pdb_element

        assert _pdb_element(self._record("FE  ")) == "Fe"
        assert _pdb_element(self._record("FE1 ")) == "Fe"
        assert _pdb_element(self._record("ZN  ")) == "Zn"

    def test_element_column_wins_when_present(self):
        from vibeview.converters import _pdb_element

        # An explicit element column overrides any name-based guess.
        assert _pdb_element(self._record(" CA ", " C")) == "C"
        assert _pdb_element(self._record("CA  ", "CA")) == "Ca"

    def test_unknown_symbols_are_preserved_not_dropped(self):
        """Unknown elements survive with atomic_number 0.

        The element table disambiguates candidate readings; it must not
        become a filter, or a file with an exotic label silently loses
        atoms (see TestUnknownElementAtomicNumber).
        """
        from vibeview.converters import _pdb_element

        assert _pdb_element(self._record("XX  ")) == "Xx"

    def test_protein_without_element_column_round_trips(self, tmp_path):
        """End to end: a small element-column-free protein fragment."""
        import collections

        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        names = (" N  ", " CA ", " C  ", " O  ", " CB ",
                 " HA ", "HB1 ", "HB2 ", "HB3 ")
        lines = [
            # exact PDB columns: 1-6 record, 7-11 serial, 13-16 name,
            # 18-20 resName, 22 chain, 23-26 resSeq, 31-54 xyz
            "ATOM  "
            + f"{i:>5}"
            + " "
            + name
            + " "
            + "ALA"
            + " "
            + "A"
            + f"{1:>4}"
            + "    "
            + f"{float(i):8.3f}{0.0:8.3f}{0.0:8.3f}"
            + f"{1.0:6.2f}{0.0:6.2f}"
            for i, name in enumerate(names, start=1)
        ]
        pdb = tmp_path / "frag.pdb"
        pdb.write_text("\n".join(lines) + "\n")

        out = tmp_path / "frag.qvf"
        out.write_bytes(pdb_to_qvf(pdb).getvalue())
        atoms = QVFReader(out).read_structure().atoms

        assert len(atoms) == 9, "atoms were dropped"
        counts = collections.Counter(a.symbol for a in atoms)
        assert counts == {"C": 3, "N": 1, "O": 1, "H": 4}, counts
        assert "Ca" not in counts
        # atomic_number must agree with the symbol, not be left at 0
        assert all(a.atomic_number > 0 for a in atoms)


class TestPDBResidueModel:
    """D1: residue/chain identity survives PDB -> QVF -> reader.

    The cartoon work (roadmap workstream D) is built on this: atom_name
    picks the backbone trace, chain_id + residue_seq order it,
    residue_name colours it. The fields are optional, so structures
    without them are unaffected.
    """

    @staticmethod
    def _pdb(tmp_path, records):
        """records: (name, resName, chain, resSeq, x)"""
        lines = []
        for i, (name, res, chain, seq, x) in enumerate(records, start=1):
            lines.append(
                "ATOM  "
                + f"{i:>5}"
                + " "
                + name.ljust(4)
                + " "
                + res.ljust(3)
                + " "
                + (chain or " ")
                + f"{seq:>4}"
                + "    "
                + f"{float(x):8.3f}{0.0:8.3f}{0.0:8.3f}"
                + f"{1.0:6.2f}{0.0:6.2f}"
            )
        p = tmp_path / "m.pdb"
        p.write_text("\n".join(lines) + "\n")
        return p

    def _read(self, tmp_path, records):
        from vibeview.converters import pdb_to_qvf
        from vibeview.qvf import QVFReader

        out = tmp_path / "m.qvf"
        out.write_bytes(pdb_to_qvf(self._pdb(tmp_path, records)).getvalue())
        return QVFReader(out).read_structure()

    def test_identity_round_trips(self, tmp_path):
        sd = self._read(tmp_path, [
            (" N  ", "ALA", "A", 1, 0.0),
            (" CA ", "ALA", "A", 1, 1.0),
            (" CA ", "GLY", "A", 2, 2.0),
        ])
        assert sd.has_residues
        ca = sd.atoms[1]
        assert (ca.atom_name, ca.residue_name, ca.residue_seq, ca.chain_id) == (
            "CA", "ALA", 1, "A"
        )
        assert ca.symbol == "C"  # not calcium

    def test_chains_group_by_contiguous_run_not_by_number(self, tmp_path):
        """PDB residue numbers are 4 columns and wrap at 9999, so a
        solvated file reuses them. Grouping globally merged unrelated
        molecules that shared a number; grouping by contiguous run is
        what the format actually guarantees."""
        sd = self._read(tmp_path, [
            (" CA ", "ALA", "A", 1, 0.0),
            (" CA ", "GLY", "A", 2, 1.0),
            (" O  ", "WAT", "A", 1, 9.0),   # number 1 reused by a water
        ])
        residues = sd.chains()["A"]
        assert len(residues) == 3, residues
        names = [{sd.atoms[i].residue_name for i in idx} for _s, idx in residues]
        assert names == [{"ALA"}, {"GLY"}, {"WAT"}], names

    def test_backbone_trace_is_ca_only_and_in_file_order(self, tmp_path):
        sd = self._read(tmp_path, [
            (" N  ", "ALA", "A", 1, 0.0),
            (" CA ", "ALA", "A", 1, 1.0),
            (" C  ", "ALA", "A", 1, 2.0),
            (" CA ", "GLY", "A", 2, 3.0),
        ])
        trace = sd.backbone_trace()
        assert trace.shape == (2, 3)
        assert list(trace[:, 0]) == [1.0, 3.0]

    def test_per_chain_trace_does_not_jump_between_chains(self, tmp_path):
        sd = self._read(tmp_path, [
            (" CA ", "ALA", "A", 1, 0.0),
            (" CA ", "GLY", "B", 1, 5.0),
        ])
        assert sd.backbone_trace("A").shape == (1, 3)
        assert sd.backbone_trace("B").shape == (1, 3)
        assert sd.backbone_trace().shape == (2, 3)

    def test_structures_without_residues_are_unaffected(self):
        """An XYZ has no biomolecular identity and must not gain any."""
        import tempfile
        from pathlib import Path

        from vibeview.converters import xyz_to_qvf
        from vibeview.qvf import QVFReader

        buf = xyz_to_qvf(b"2\nc\nO 0 0 0\nH 0 0 1\n")
        p = Path(tempfile.mktemp(suffix=".qvf"))
        p.write_bytes(buf.getvalue())
        sd = QVFReader(p).read_structure()
        assert sd.has_residues is False
        assert sd.chains() == {}
        assert sd.backbone_trace().shape == (0, 3)
        assert all(a.atom_name is None for a in sd.atoms)
        p.unlink()


class TestConvertersExtended:
    def test_symbol_to_z(self):
        from vibeview.converters import _SYMBOL_TO_Z

        assert _SYMBOL_TO_Z["H"] == 1
        assert _SYMBOL_TO_Z["C"] == 6
        assert _SYMBOL_TO_Z["O"] == 8
        assert _SYMBOL_TO_Z["He"] == 2

    def test_xyz_to_qvf_with_helium(self):
        from vibeview.converters import xyz_to_qvf

        xyz = b"1\n\nHe  0.0  0.0  0.0\n"
        buf = xyz_to_qvf(xyz)
        import zipfile

        zf = zipfile.ZipFile(buf)
        assert "manifest.json" in zf.namelist()

    def test_xyz_to_qvf_water(self):
        from vibeview.converters import xyz_to_qvf

        xyz = (
            b"3\n\nO   0.000   0.000   0.117\n"
            b"H   0.000   0.757  -0.469\nH   0.000  -0.757  -0.469\n"
        )
        buf = xyz_to_qvf(xyz)
        import zipfile

        zf = zipfile.ZipFile(buf)
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["qvf_version"] == 1


class TestConverterEdgeCases:
    def test_xyz_to_qvf_empty_raises(self):
        from vibeview.converters import xyz_to_qvf

        # XYZ with 0 atoms should raise ValueError
        with pytest.raises(ValueError, match="no atoms"):
            xyz_to_qvf(b"0\n\n")

    def test_xyz_to_qvf_large(self):
        from vibeview.converters import xyz_to_qvf

        # 100-atom molecule
        xyz = b"100\nLarge molecule\n"
        for i in range(100):
            xyz += f"C    {i * 1.5:.6f}    0.000000    0.000000\n".encode()
        buf = xyz_to_qvf(xyz)
        import zipfile

        zf = zipfile.ZipFile(buf)
        assert "manifest.json" in zf.namelist()


class TestConverterFormats:
    """Test format converters for import."""

    def test_xyz_to_qvf(self):
        from vibeview.converters import xyz_to_qvf

        xyz_content = b"""3
Water molecule
O    0.000000    0.000000    0.117790
H    0.000000    0.755450   -0.471160
H    0.000000   -0.755450   -0.471160
"""
        buf = xyz_to_qvf(xyz_content)
        assert buf is not None
        # Verify it's a valid zip
        import zipfile

        zf = zipfile.ZipFile(buf)
        names = zf.namelist()
        assert "manifest.json" in names
