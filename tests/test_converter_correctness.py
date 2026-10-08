"""Converter and exporter correctness follow-ups to the #40 audit, tracked in #53.

Each test names the original #40 finding id so the verifier's matrix row
traces to it. Expected behaviour is derived from the format specifications
cited per test, not from the previous implementation.
"""

from __future__ import annotations

import io
import json
import math
import re
from pathlib import Path

import numpy as np
import pytest

from vibeview.converters import cif_to_qvf, gjf_to_qvf, gro_to_qvf, pdb_to_qvf
from vibeview.qvf import QVFReader


def _structure(buf: io.BytesIO):
    return QVFReader(buf).read_structure()


def _gro(atom_lines: list[str], box: str) -> bytes:
    body = "\n".join(atom_lines)
    return f"title\n{len(atom_lines):5d}\n{body}\n{box}\n".encode()


def _gro_atom(resnr: int, resname: str, name: str, nr: int, x: float = 0.0) -> str:
    # GROMACS .gro atom line: "%5d%-5s%5s%5d%8.3f%8.3f%8.3f" (GROMACS
    # reference manual, File formats, gro).
    return f"{resnr:5d}{resname:<5s}{name:>5s}{nr:5d}{x:8.3f}{0.0:8.3f}{0.0:8.3f}"


# ── #40-1: GRO atom names are force-field names, not element symbols ───────


def test_gro_protein_atom_names_are_not_read_as_elements() -> None:
    """#40-1: CA/HE1/ND2 minted calcium, helium and neodymium."""
    lines = [
        _gro_atom(1, "ALA", "CA", 1),
        _gro_atom(2, "HIS", "HE1", 2, 0.1),
        _gro_atom(3, "ASN", "ND2", 3, 0.2),
    ]
    s = _structure(gro_to_qvf(_gro(lines, "   1.00000   1.00000   1.00000")))
    assert [(a.symbol, a.atomic_number) for a in s.atoms] == [("C", 6), ("H", 1), ("N", 7)]


def test_gro_single_atom_ion_residues_keep_two_letter_elements() -> None:
    """#40-1: an ion is its own one-atom residue; NA/CL/CA there are ions,
    while the heme pyrrole nitrogen NA and an iron FE keep their identity."""
    lines = [
        _gro_atom(1, "NA", "NA", 1),
        _gro_atom(2, "CL", "CL", 2, 0.3),
        _gro_atom(3, "CA", "CA", 3, 0.6),
        _gro_atom(4, "HEM", "FE", 4, 0.9),
        _gro_atom(4, "HEM", "NA", 5, 1.2),
    ]
    s = _structure(gro_to_qvf(_gro(lines, "   2.00000   2.00000   2.00000")))
    assert [a.symbol for a in s.atoms] == ["Na", "Cl", "Ca", "Fe", "N"]


# ── #40-14: GRO box vectors, in the GROMACS order ──────────────────────────


def test_gro_triclinic_box_uses_the_gromacs_vector_order() -> None:
    """#40-14: GROMACS manual (File formats, gro): the box line is
    ``v1(x) v2(y) v3(z) v1(y) v1(z) v2(x) v2(z) v3(x) v3(y)`` in nm.
    ``3 3 3 0 0 1 0 1 1`` is v1=(3,0,0), v2=(1,3,0), v3=(1,1,3) nm; the
    reader kept only the diagonal."""
    s = _structure(gro_to_qvf(_gro([_gro_atom(1, "SOL", "OW", 1)],
                                   "3.0 3.0 3.0 0.0 0.0 1.0 0.0 1.0 1.0")))
    np.testing.assert_allclose(
        s.lattice_vectors, [[30.0, 0.0, 0.0], [10.0, 30.0, 0.0], [10.0, 10.0, 30.0]]
    )
    assert s.pbc == (True, True, True)


def test_gro_zero_box_is_not_periodic() -> None:
    """#40-14: a ``0 0 0`` box (no box) became pbc=True with a singular
    lattice, which the reader then refused."""
    s = _structure(gro_to_qvf(_gro([_gro_atom(1, "SOL", "OW", 1)], "0.0 0.0 0.0")))
    assert s.pbc == (False, False, False)
    assert s.lattice_vectors is None


# ── #40-2: the wwPDB CRYST1 placeholder is not a crystal ───────────────────

_PDB_ATOMS = (
    "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
    "ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C\n"
)


def test_pdb_unit_cryst1_placeholder_is_not_periodic() -> None:
    """#40-2: wwPDB Format v3.3 §8 (CRYST1): a structure not determined by
    crystallography carries a = b = c = 1.0, alpha = beta = gamma = 90,
    space group P 1, Z = 1. That is a placeholder, not a 1 A cell."""
    pdb = "CRYST1    1.000    1.000    1.000  90.00  90.00  90.00 P 1           1\n" + _PDB_ATOMS
    s = _structure(pdb_to_qvf(pdb.encode()))
    assert s.pbc == (False, False, False)
    assert s.lattice_vectors is None


def test_pdb_real_cryst1_is_still_periodic() -> None:
    pdb = "CRYST1   50.000   50.000   50.000  90.00  90.00  90.00 P 1           1\n" + _PDB_ATOMS
    s = _structure(pdb_to_qvf(pdb.encode()))
    assert s.pbc == (True, True, True)


# ── #40-15: PDB MODEL/ENDMDL and alternate locations ───────────────────────


def test_pdb_reads_the_first_model_and_first_alternate_location() -> None:
    """#40-15: wwPDB v3.3 §9 (MODEL/ENDMDL: one coordinate set per model)
    and the ATOM record's altLoc, column 17. Two models with altLoc A/B
    each gave 6 overlaid atoms; one model, one location per atom is 2."""
    pdb = (
        "MODEL        1\n"
        "ATOM      1  N  AALA A   1       0.000   0.000   0.000  0.50  0.00           N\n"
        "ATOM      2  N  BALA A   1       0.500   0.000   0.000  0.50  0.00           N\n"
        "ATOM      3  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C\n"
        "ENDMDL\n"
        "MODEL        2\n"
        "ATOM      1  N  AALA A   1       0.000   0.000   9.000  0.50  0.00           N\n"
        "ATOM      2  N  BALA A   1       0.500   0.000   9.000  0.50  0.00           N\n"
        "ATOM      3  CA  ALA A   1       1.458   0.000   9.000  1.00  0.00           C\n"
        "ENDMDL\n"
        "END\n"
    )
    s = _structure(pdb_to_qvf(pdb.encode()))
    assert [(a.symbol, tuple(np.round(a.position, 3))) for a in s.atoms] == [
        ("N", (0.0, 0.0, 0.0)),
        ("C", (1.458, 0.0, 0.0)),
    ]


# ── #40-3: CIF 1.1 tokenization ─────────────────────────────────────────────

_CIF_HEAD = (
    "data_x\n_cell_length_a 5.0\n_cell_length_b 5.0\n_cell_length_c 5.0\n"
    "_cell_angle_alpha 90\n_cell_angle_beta 90\n_cell_angle_gamma 90\n"
    "loop_\n_atom_site_label\n_atom_site_type_symbol\n"
    "_atom_site_fract_x\n_atom_site_fract_y\n_atom_site_fract_z\n"
)


def _sites(buf: io.BytesIO) -> list[tuple[str, tuple[float, ...]]]:
    return [(a.symbol, tuple(np.round(a.position, 3))) for a in _structure(buf).atoms]


def test_cif_quoted_label_is_one_value() -> None:
    """#40-3: CIF 1.1 syntax: a quoted value is one token. ``'Na 1'`` was
    split in two and misaligned the row (IndexError)."""
    cif = _CIF_HEAD + "'Na 1' Na 0.0 0.0 0.0\nCl1 Cl 0.5 0.5 0.5\n"
    assert _sites(cif_to_qvf(cif.encode())) == [
        ("Na", (0.0, 0.0, 0.0)),
        ("Cl", (2.5, 2.5, 2.5)),
    ]


def test_cif_loop_row_may_span_lines() -> None:
    """#40-3: CIF 1.1: loop values are a whitespace-separated stream, so a
    packet may continue on the next line ("no atom sites found" before)."""
    cif = _CIF_HEAD + "Na1 Na\n 0.0 0.0 0.0\nCl1 Cl\n 0.5 0.5 0.5\n"
    assert _sites(cif_to_qvf(cif.encode())) == [
        ("Na", (0.0, 0.0, 0.0)),
        ("Cl", (2.5, 2.5, 2.5)),
    ]


def test_cif_semicolon_text_field_and_comment_do_not_shift_values() -> None:
    """CIF 1.1: a ';' text field is one value and '#' starts a comment."""
    cif = (
        "data_x\n_publ_section_title\n;\nA title with _underscores and loop_\n;\n"
        + _CIF_HEAD.split("\n", 1)[1]
        + "Na1 Na 0.0 0.0 0.0 # sodium\n"
    )
    assert _sites(cif_to_qvf(cif.encode())) == [("Na", (0.0, 0.0, 0.0))]


def test_cif_malformed_coordinate_is_an_error_naming_the_site() -> None:
    """#40-3: a bad coordinate after a good row raised IndexError."""
    cif = _CIF_HEAD + "Na1 Na 0.0 0.0 0.0\nCl1 Cl abc 0.5 0.5\n"
    with pytest.raises(ValueError, match="Cl1"):
        cif_to_qvf(cif.encode())


def test_cif_incomplete_loop_is_an_error() -> None:
    """CIF 1.1: a loop's value count is a multiple of its data names."""
    cif = _CIF_HEAD + "Na1 Na 0.0 0.0 0.0\nCl1 Cl 0.5 0.5\n"
    with pytest.raises(ValueError, match="loop"):
        cif_to_qvf(cif.encode())


def test_cif_type_symbol_with_oxidation_state() -> None:
    """CIF core dictionary, _atom_site_type_symbol: e.g. ``Na1+``, ``O2-``."""
    cif = _CIF_HEAD + "Na1 Na1+ 0.0 0.0 0.0\nO1 O2- 0.5 0.5 0.5\n"
    assert [s for s, _ in _sites(cif_to_qvf(cif.encode()))] == ["Na", "O"]


# ── #40-6: Gaussian element labels ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "symbol"),
    [("C1", "C"), ("6", "C"), ("C(Fragment=1)", "C"), ("C-CA-0.25", "C"), ("Cl2", "Cl")],
)
def test_gjf_labelled_atoms_are_kept(label: str, symbol: str) -> None:
    """#40-6: Gaussian "Molecule Specification": the element label is the
    chemical symbol or the atomic number, optionally followed by other
    alphanumeric characters (C1, C2, ...), by parenthesised parameters
    (``Fragment=1``), or by MM atom type and charge (``C-CA-0.25``). Every
    one of these was silently dropped."""
    gjf = f"%mem=1GB\n#p B3LYP\n\ntitle\n\n0 1\n{label} 0.0 0.0 0.0\nH 1.0 0.0 0.0\n\n"
    s = _structure(gjf_to_qvf(gjf.encode()))
    assert [a.symbol for a in s.atoms] == [symbol, "H"]


def test_gjf_freeze_code_column() -> None:
    """Gaussian: ``Element-label [Freeze-code] x y z``."""
    gjf = "#p opt\n\ntitle\n\n0 1\nC -1 0.0 0.0 0.0\nH 0 1.0 0.0 0.0\n\n"
    s = _structure(gjf_to_qvf(gjf.encode()))
    assert [(a.symbol, tuple(a.position)) for a in s.atoms] == [
        ("C", (0.0, 0.0, 0.0)),
        ("H", (1.0, 0.0, 0.0)),
    ]


def test_gjf_unreadable_atom_line_is_an_error() -> None:
    """#40-6: an atom line that is not a Cartesian element line (here a
    Z-matrix) must not be dropped silently."""
    gjf = "#p sp\n\ntitle\n\n0 1\nO\nH 1 0.96\nH 1 0.96 2 104.5\n\n"
    with pytest.raises(ValueError, match="line"):
        gjf_to_qvf(gjf.encode())


# ── #40-11: point groups do not depend on orientation ──────────────────────


def _rotation(axis: tuple[float, float, float], angle: float) -> np.ndarray:
    k = np.asarray(axis, dtype=float)
    k /= np.linalg.norm(k)
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(angle) * kx + (1 - math.cos(angle)) * kx @ kx


_WATER = (["O", "H", "H"], np.array([[0, 0, 0.1173], [0, 0.7572, -0.4692], [0, -0.7572, -0.4692]]))
_ETHYLENE = (
    ["C", "C", "H", "H", "H", "H"],
    np.array([[0.6695, 0, 0], [-0.6695, 0, 0], [1.2321, 0.9289, 0], [1.2321, -0.9289, 0],
              [-1.2321, 0.9289, 0], [-1.2321, -0.9289, 0]]),
)


_AMMONIA = (
    ["N", "H", "H", "H"],
    np.array([[0, 0, 0]] + [[0.94 * math.cos(2 * math.pi * k / 3),
                             0.94 * math.sin(2 * math.pi * k / 3), -0.38] for k in range(3)]),
)
_BENZENE = (
    ["C"] * 6 + ["H"] * 6,
    np.array([[r * math.cos(math.pi * k / 3), r * math.sin(math.pi * k / 3), 0]
              for r in (1.397, 2.481) for k in range(6)]),
)


@pytest.mark.parametrize(
    ("molecule", "expected"),
    [(_WATER, "C2v"), (_ETHYLENE, "D2h"), (_AMMONIA, "C3v"), (_BENZENE, "D6h")],
)
@pytest.mark.parametrize("angle", [0.0, 0.9, 2.3])
def test_point_group_is_orientation_independent(molecule, expected: str, angle: float) -> None:
    """#40-11: water rotated 0.9 rad about (1, 1, 0.3) came out C1 because
    the mirror and C2 tests used Cartesian planes and axes only."""
    from vibeview.symmetry import detect_point_group

    symbols, positions = molecule
    rotated = positions @ _rotation((1.0, 1.0, 0.3), angle).T + np.array([0.3, -1.2, 2.0])
    assert detect_point_group(list(symbols), rotated).symbol == expected


# ── #40-17 / #40-19: exporters ──────────────────────────────────────────────


def _archive_path(
    tmp_path: Path, atoms, *, pbc=(False, False, False), lattice=None, bonds=None
) -> Path:
    from vibeview.qvf_writer import structure_archive

    payload = [
        {"symbol": s, "atomic_number": z, "position": list(p)} for s, z, p in atoms
    ]
    path = tmp_path / "s.qvf"
    path.write_bytes(
        structure_archive(payload, list(pbc), lattice, bonds=bonds).to_bytesio().getvalue()
    )
    return path


# Two Cl atoms bonded only across the x face of a 10 A cell (1.0 A apart).
_CL2_ACROSS_FACE = [("Cl", 17, (0.5, 5.0, 5.0)), ("Cl", 17, (9.5, 5.0, 5.0))]


def test_export_pdf_docstring_describes_the_pdf_it_writes() -> None:
    """#40-17: the docstring said "raster PNG" while the output is %PDF."""
    from vibeview.export_pdf import export_pdf

    first = (export_pdf.__doc__ or "").strip().splitlines()[0]
    assert "PNG" not in first and "PDF" in first


def _pixel(path: Path, xy: tuple[float, float]) -> tuple[int, int, int]:
    from PIL import Image

    with Image.open(path) as img:
        return img.convert("RGB").getpixel((int(round(xy[0])), int(round(xy[1]))))


def _fallback(tmp_path: Path, qvf: Path, style: str, name: str) -> Path:
    from vibeview.export_pdf import _export_pdf_fallback_pil

    out = tmp_path / name
    with QVFReader(qvf) as reader:
        _export_pdf_fallback_pil(reader, str(out), 4.0, 3.0, style, "#ffffff", 100)
    return out


def test_pdf_fallback_draws_bonds_and_honours_style(tmp_path: Path) -> None:
    """#40-17: the PIL fallback drew no bonds and ignored ``style``."""
    qvf = _archive_path(
        tmp_path,
        [("C", 6, (-2.0, 0.0, 0.0)), ("C", 6, (2.0, 0.0, 0.0))],
        bonds=[{"i": 0, "j": 1, "order": 1.0}],
    )
    # The two atoms lie on the drawing's horizontal centre line, so the
    # canvas centre is the bond's midpoint, 2 A from either atom.
    centre = (4.0 * 100 / 2, 3.0 * 100 / 2)
    stick = _pixel(_fallback(tmp_path, qvf, "ball_and_stick", "bs.png"), centre)
    none = _pixel(_fallback(tmp_path, qvf, "space_filling", "sf.png"), centre)
    assert stick != (255, 255, 255)
    assert none == (255, 255, 255)


def test_pdf_fallback_uses_the_exporters_colour_table(tmp_path: Path) -> None:
    """#40-17: the fallback had its own 8-element table (Na drew grey)."""
    from vibeview.elements import cpk_hex

    qvf = _archive_path(tmp_path, [("Na", 11, (-2.0, 0.0, 0.0)), ("Na", 11, (2.0, 0.0, 0.0))])
    out = _fallback(tmp_path, qvf, "ball_and_stick", "na.png")
    from PIL import Image

    with Image.open(out) as img:
        colours = {c for _n, c in img.convert("RGB").getcolors(maxcolors=1 << 16)}
    hexa = cpk_hex(11).lstrip("#")
    assert tuple(int(hexa[k : k + 2], 16) for k in (0, 2, 4)) in colours


def test_svg_draws_a_bond_through_a_periodic_face(tmp_path: Path) -> None:
    """#40-19: the SVG export dropped image bonds (1 bond, 0 lines)."""
    from vibeview.bonds import structure_bonds
    from vibeview.export_svg import export_svg

    qvf = _archive_path(tmp_path, _CL2_ACROSS_FACE, pbc=(True, True, True),
                        lattice=(np.eye(3) * 10.0).tolist())
    with QVFReader(qvf) as reader:
        expected = structure_bonds(reader.read_structure())
        svg = export_svg(reader, str(tmp_path / "cl2.svg"))
    assert len(expected) == 1 and any(expected[0][3])
    assert svg.count("<line ") == 1


def test_html_carries_the_image_endpoint_of_a_periodic_bond(tmp_path: Path) -> None:
    """#40-19: the HTML export joined atoms by index and dropped image bonds."""
    from vibeview.export_html import export_html

    qvf = _archive_path(tmp_path, _CL2_ACROSS_FACE, pbc=(True, True, True),
                        lattice=(np.eye(3) * 10.0).tolist())
    with QVFReader(qvf) as reader:
        page = export_html(reader, tmp_path / "cl2.html").read_text()
    data = json.loads(re.search(r"const data = (\{.*?\});\n", page).group(1))
    assert len(data["bonds"]) == 1
    i, j, end = data["bonds"][0]
    start = np.asarray(data["atoms"][i]["position"])
    assert float(np.linalg.norm(np.asarray(end) - start)) == pytest.approx(1.0, abs=1e-6)


def test_blender_script_carries_the_image_endpoint_of_a_periodic_bond(tmp_path: Path) -> None:
    """#40-19: the Blender script dropped image bonds."""
    from vibeview.blender_export import export_blender_script

    qvf = _archive_path(tmp_path, _CL2_ACROSS_FACE, pbc=(True, True, True),
                        lattice=(np.eye(3) * 10.0).tolist())
    with QVFReader(qvf) as reader:
        script = export_blender_script(reader, str(tmp_path / "cl2.py"))
    text = script if "BOND_DATA" in str(script) else (tmp_path / "cl2.py").read_text()
    literal = re.search(r"^BOND_DATA = (.*)$", text, re.M).group(1)
    bonds = json.loads(json.loads(literal))
    atoms = json.loads(json.loads(re.search(r"^ATOM_DATA = (.*)$", text, re.M).group(1)))
    assert len(bonds) == 1
    i, _j, end = bonds[0]
    length = float(np.linalg.norm(np.asarray(end) - np.asarray(atoms[i])))
    assert length == pytest.approx(1.0, abs=1e-6)
