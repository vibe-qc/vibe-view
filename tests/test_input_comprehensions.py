"""Static input import must preserve Python atom-selection semantics."""

import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.converters import py_to_qvf
from vibeview.input_parser import parse_input_source
from vibeview.qvf import QVFReader


@pytest.mark.parametrize(
    "selection",
    [
        "if keep",
        "if z == 8",
        "if 1 < z <= 8",
        "if z in (6, 8)",
        "if z not in (1, 6)",
        "if keep and not z == 1",
        "if z == 8 or unknown_predicate()",
        "if keep if z != 1",
    ],
)
@pytest.mark.parametrize("periodic", [False, True])
def test_filtered_atoms_roundtrip_through_cli(tmp_path, selection, periodic):
    source = tmp_path / "input.py"
    output = tmp_path / "output.qvf"
    constructor = (
        "PeriodicSystem(2, [[8,0,0],[0,9,0],[0,0,10]], atoms)"
        if periodic
        else "Molecule(atoms)"
    )
    # The `or` case bypasses the unknown predicate in the kept row.
    entries = "[(8, [1, 2, 3], True), (1, [0, 0, 1.8], False)]"
    if "unknown_predicate" in selection:
        entries = "[(8, [1, 2, 3], True)]"
    source.write_text(
        f"entries = {entries}\n"
        f"atoms = [Atom(z, pos) for z, pos, keep in entries {selection}]\n"
        f"system = {constructor}\n"
    )

    result = CliRunner().invoke(main, ["import", str(source), "-o", str(output)])

    assert result.exit_code == 0, result.output
    with QVFReader(output) as reader:
        structure = reader.read_structure()
        assert [atom.symbol for atom in structure.atoms] == ["O"]
        assert structure.atoms[0].position == pytest.approx(
            [coord * 0.529177210903 for coord in [1, 2, 3]]
        )
        assert structure.pbc == ((True, True, False) if periodic else (False,) * 3)


def test_comprehension_target_does_not_overwrite_molecular_charge(tmp_path):
    source = tmp_path / "input.py"
    source.write_text(
        "charge = -1\n"
        "atoms = [Atom(8, [0, 0, charge]) for charge in [0, 2]]\n"
        "molecule = Molecule(atoms, charge=charge)\n"
    )

    parsed = parse_input_source(source.read_text())
    assert parsed.charge == -1
    assert [atom["position"][2] for atom in parsed.atoms] == [0, 2]
    with QVFReader(py_to_qvf(source)) as reader:
        assert reader.provenance["charge"] == -1


def test_nested_generators_preserve_order_and_inner_filters(tmp_path):
    source = tmp_path / "input.py"
    source.write_text(
        "rows = [(8, [0, 4, 2]), (1, [6, 8])]\n"
        "atoms = [Atom(z, [x, 0, 0]) for z, xs in rows if z == 8 "
        "for x in xs if x > 0]\n"
        "molecule = Molecule(atoms)\n"
    )

    with QVFReader(py_to_qvf(source)) as reader:
        atoms = reader.read_structure().atoms
        assert [atom.symbol for atom in atoms] == ["O", "O"]
        for atom, x in zip(atoms, [4, 2], strict=True):
            assert atom.position == pytest.approx([x * 0.529177210903, 0, 0])


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("periodic", [False, True])
def test_unknown_filter_refuses_archive_without_running_input(tmp_path, inline, periodic):
    source = tmp_path / "input.py"
    output = tmp_path / "output.qvf"
    marker = tmp_path / "executed"
    expression = "[Atom(z, pos) for z, pos in rows if select(z)]"
    argument = expression if inline else "atoms"
    constructor = (
        f"PeriodicSystem(3, [[8,0,0],[0,8,0],[0,0,8]], {argument})"
        if periodic
        else f"Molecule({argument})"
    )
    source.write_text(
        "def select(z):\n"
        f"    open({str(marker)!r}, 'w').write('ran')\n"
        "    return True\n"
        "rows = [(8, [0, 0, 0])]\n"
        # A failed reassignment must not leave an earlier atom list in scope.
        "atoms = [Atom(1, [0, 0, 0])]\n"
        f"atoms = {expression}\n"
        f"system = {constructor}\n"
    )

    result = CliRunner().invoke(main, ["import", str(source), "-o", str(output)])

    assert result.exit_code != 0
    assert "atom list" in result.output and "statically" in result.output
    assert not output.exists()
    assert not marker.exists()


def test_false_filter_short_circuits_unknown_condition_and_element():
    parsed = parse_input_source(
        "atoms = [Atom(8, unknown_position()) for x in [1] "
        "if False and unknown_predicate()]\n"
        "molecule = Molecule(atoms)\n"
    )
    assert parsed.atoms == []


def test_skipped_row_does_not_resolve_atom_coordinates():
    parsed = parse_input_source(
        "rows = [(8, [1, 2, 3], True), (1, None, False)]\n"
        "atoms = [Atom(z, pos) for z, pos, keep in rows if keep]\n"
        "molecule = Molecule(atoms)\n"
    )
    assert [atom["symbol"] for atom in parsed.atoms] == ["O"]
    assert parsed.atoms[0]["position"] == [1, 2, 3]


def test_unrelated_unknown_comprehension_does_not_block_literal_geometry():
    parsed = parse_input_source(
        "unused = [x for x in unknown_values() if unknown_predicate(x)]\n"
        "molecule = Molecule([Atom(8, [0, 0, 0])])\n"
    )
    assert [atom["symbol"] for atom in parsed.atoms] == ["O"]
