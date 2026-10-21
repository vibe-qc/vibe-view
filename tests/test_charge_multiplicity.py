"""Charge and multiplicity survive the trip into and out of vibe-view (#89).

vibe-sketch draws a structure it reads from a QVF by perceiving bonds from the
coordinates with the archive's ``provenance.charge``, so an ion vibe-view wrote
without one could not be drawn. It writes the charge into the XYZ comment line,
which vibe-view ignored. And the Calculation Parameters panel started every
file as a neutral singlet. Each source that states the values now records
them in ``provenance`` (spec § 3.2), and the panel starts from them.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from vibeview import converters


def _provenance(buffer) -> dict:
    with zipfile.ZipFile(buffer) as archive:
        return json.loads(archive.read("manifest.json")).get("provenance", {})


def _declared(buffer) -> tuple:
    provenance = _provenance(buffer)
    return provenance.get("charge"), provenance.get("multiplicity")


# ── XYZ: the extended-XYZ comment vibe-sketch writes ───────────────────────


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("charge=-1 multiplicity=1 species=m1 generator=vibe-sketch-0.1.0", (-1, 1)),
        ("Properties=species:S:1:pos:R:3 charge=2 multiplicity=3", (2, 3)),
        ('charge="-1" Multiplicity="2"', (-1, 2)),
        ("CHARGE=+1", (1, None)),
        ("water", (None, None)),
        ("", (None, None)),
        ("charge=1.5 multiplicity=0", (None, None)),
        ("surcharge=3 xmultiplicity=2", (None, None)),
    ],
)
def test_xyz_comment_charge_and_multiplicity(comment, expected):
    xyz = f"2\n{comment}\nO 0 0 0\nH 0 0 0.97\n".encode()
    assert _declared(converters.xyz_to_qvf(xyz)) == expected


def test_xyz_without_values_writes_no_provenance():
    """Not stating a charge is not the same as stating a neutral singlet."""
    assert _provenance(converters.xyz_to_qvf(b"1\n\nHe 0 0 0\n")) == {}


# ── SMILES: RDKit knows both ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("smiles", "expected"),
    [("CCO", (0, 1)), ("CC(=O)[O-]", (-1, 1)), ("[NH4+]", (1, 1)), ("[CH3]", (0, 2)),
     ("[O][O]", (0, 3))],
)
def test_smiles_records_formal_charge_and_radical_multiplicity(smiles, expected):
    pytest.importorskip("rdkit", reason="the [smiles] extra is not installed")
    assert _declared(converters.smiles_to_qvf(smiles)) == expected


# ── Gaussian input: the charge/multiplicity line ───────────────────────────


def test_gaussian_charge_multiplicity_line_is_kept():
    gjf = b"%chk=oh.chk\n#p hf/sto-3g\n\nhydroxyl radical anion\n\n-1 2\nO 0 0 0\nH 0 0 0.97\n\n"
    assert _declared(converters.gjf_to_qvf(gjf)) == (-1, 2)


# ── SDF/MOL: CTfile charges and radicals ───────────────────────────────────


def _molfile(atoms: list[tuple[str, int]], bonds: list[tuple[int, int, int]], props: list[str]):
    """A V2000 record: ``atoms`` are (symbol, ccc code), placed along x."""
    lines = ["", "  test", "", f"{len(atoms):3d}{len(bonds):3d}  0  0  0  0  0  0  0  0999 V2000"]
    for index, (symbol, ccc) in enumerate(atoms):
        coordinates = f"{index * 1.4:10.4f}{0.0:10.4f}{0.0:10.4f}"
        lines.append(f"{coordinates} {symbol:<3} 0{ccc:3d}" + "  0" * 10)
    lines += [f"{i:3d}{j:3d}{order:3d}  0" for i, j, order in bonds]
    lines += props + ["M  END", "$$$$"]
    return "\n".join(lines).encode()


def test_sdf_m_chg_records_the_net_charge():
    acetate = _molfile(
        [("C", 0), ("C", 0), ("O", 0), ("O", 0)],
        [(1, 2, 1), (2, 3, 2), (2, 4, 1)],
        ["M  CHG  1   4  -1"],
    )
    assert _declared(converters.sdf_to_qvf(acetate)) == (-1, None)


def test_sdf_atom_block_charge_codes_count_without_m_chg():
    # ccc 3 is +1 and 5 is -1 (CTfile V2000): a zwitterion, net zero, then a cation.
    zwitterion = _molfile([("N", 3), ("C", 0), ("O", 5)], [(1, 2, 1), (2, 3, 1)], [])
    assert _declared(converters.sdf_to_qvf(zwitterion)) == (0, None)
    cation = _molfile([("N", 3)], [], [])
    assert _declared(converters.sdf_to_qvf(cation)) == (1, None)


def test_sdf_m_chg_supersedes_the_atom_block():
    # The atom block says +1, M  CHG says -1: the property record wins.
    record = _molfile([("O", 3)], [], ["M  CHG  1   1  -1"])
    assert _declared(converters.sdf_to_qvf(record)) == (-1, None)


@pytest.mark.parametrize(("rad", "multiplicity"), [(1, 1), (2, 2), (3, 3)])
def test_sdf_m_rad_gives_the_multiplicity(rad, multiplicity):
    methyl = _molfile([("C", 0)], [], [f"M  RAD  1   1   {rad}"])
    assert _declared(converters.sdf_to_qvf(methyl)) == (0, multiplicity)


def test_sdf_atom_block_doublet_radical_code():
    methyl = _molfile([("C", 4)], [], [])
    assert _declared(converters.sdf_to_qvf(methyl)) == (0, 2)


def test_a_malformed_property_record_is_ignored_not_fatal():
    record = _molfile([("O", 0)], [], ["M  CHG  2   1"])
    assert _declared(converters.sdf_to_qvf(record)) == (0, None)


def test_builder_records_what_it_is_given_and_nothing_else():
    atoms = [(8, 0.0, 0.0, 0.0), (1, 0.0, 0.0, 0.97)]
    assert _declared(converters.atoms_to_qvf(atoms, charge=-1, multiplicity=1)) == (-1, 1)
    assert _provenance(converters.atoms_to_qvf(atoms)) == {}


# ── the Calculation Parameters panel ───────────────────────────────────────


def _app(*buffers):
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    server = create_app([QVFReader(buffer) for buffer in buffers])
    server.controller.view_update = lambda *a, **k: None
    return server


def _ion(charge: int, multiplicity: int):
    return converters.atoms_to_qvf(
        [(8, 0.0, 0.0, 0.0), (1, 0.0, 0.0, 0.97)], charge=charge, multiplicity=multiplicity
    )


def test_the_panel_starts_at_the_opened_files_charge():
    app = _app(_ion(-1, 2))
    assert (app.state.calc_charge, app.state.calc_multiplicity) == (-1, 2)


def test_the_panel_follows_the_file_shown_and_keeps_values_a_file_does_not_state():
    plain = converters.xyz_to_qvf(b"1\n\nHe 0 0 0\n")
    app = _app(plain, _ion(1, 1))
    assert (app.state.calc_charge, app.state.calc_multiplicity) == (0, 1)
    app.controller.switch_file(1)
    assert (app.state.calc_charge, app.state.calc_multiplicity) == (1, 1)
    app.state.calc_charge = 3  # the user's own choice
    app.controller.switch_file(0)
    assert app.state.calc_charge == 3, "a file that states nothing must not reset the panel"


def test_building_an_ion_from_smiles_sets_the_panel():
    pytest.importorskip("rdkit", reason="the [smiles] extra is not installed")
    app = _app(converters.xyz_to_qvf(b"1\n\nHe 0 0 0\n"))
    app.state.builder_smiles = "CC(=O)[O-]"
    app.controller.build_from_smiles()
    assert (app.state.calc_charge, app.state.calc_multiplicity) == (-1, 1)
