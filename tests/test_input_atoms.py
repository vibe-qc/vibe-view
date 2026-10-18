"""Static Python imports must resolve every atom without inventing geometry."""

import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.input_parser import parse_input_source
from vibeview.qvf import QVFReader


def _system(atoms: str, periodic: bool, keyword: bool) -> str:
    if periodic:
        argument = f"unit_cell={atoms}" if keyword else atoms
        return f"PeriodicSystem(2, [[8,0,0],[0,9,0],[0,0,10]], {argument})"
    argument = f"atoms={atoms}" if keyword else atoms
    return f"Molecule({argument})"


@pytest.mark.parametrize("periodic", [False, True])
@pytest.mark.parametrize("keyword", [False, True])
@pytest.mark.parametrize(
    "atoms",
    [
        "[Atom(8, build_position())]",
        "[Atom(8, missing_position)]",
        "[Atom(Z=8, xyz=build_position())]",
        "[Atom(Z=missing_z, xyz=[1, 2, 3])]",
        "[oxygen, Atom(1, build_position())]",
        "[Atom(8, [1, 2, 3]), build_atom()]",
    ],
)
def test_unresolved_literal_atoms_refuse_archive(tmp_path, periodic, keyword, atoms):
    source = tmp_path / "input.py"
    output = tmp_path / "output.qvf"
    marker = tmp_path / "executed"
    source.write_text(
        "def build_position():\n"
        f"    open({str(marker)!r}, 'w').write('ran')\n"
        "    return [1, 2, 3]\n"
        "def build_atom():\n"
        "    return Atom(1, [4, 5, 6])\n"
        "oxygen = Atom(8, [7, 8, 9])\n"
        f"system = {_system(atoms, periodic, keyword)}\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["import", str(source), "-o", str(output)])

    assert result.exit_code == 1, result.output
    assert "atom list" in result.output and "statically" in result.output
    assert not output.exists()
    assert not marker.exists()
    with pytest.raises(ValueError, match="atom list.*statically"):
        parse_input_source(source.read_text(encoding="utf-8"))


@pytest.mark.parametrize("periodic", [False, True])
@pytest.mark.parametrize("keyword", [False, True])
def test_resolved_literal_atoms_keep_identity_coordinates_and_order(tmp_path, periodic, keyword):
    source = tmp_path / "input.py"
    output = tmp_path / "output.qvf"
    atoms = "[oxygen, Atom(1, np.asarray(position) * scale)]"
    source.write_text(
        "scale = 2\n"
        "oxygen = Atom('O', [1, 2, 3])\n"
        "position = (4, -5, 6)\n"
        f"system = {_system(atoms, periodic, keyword)}\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["import", str(source), "-o", str(output)])

    assert result.exit_code == 0, result.output
    with QVFReader(output) as reader:
        structure = reader.read_structure()
        assert [atom.symbol for atom in structure.atoms] == ["O", "H"]
        for atom, position in zip(structure.atoms, ([1, 2, 3], [8, -10, 12]), strict=True):
            assert atom.position == pytest.approx([c * 0.529177210903 for c in position])
        assert structure.pbc == ((True, True, False) if periodic else (False,) * 3)


@pytest.mark.parametrize("periodic", [False, True])
def test_empty_literal_atom_list_still_parses(periodic):
    assert parse_input_source(f"system = {_system('[]', periodic, False)}").atoms == []


@pytest.mark.parametrize("periodic", [False, True])
@pytest.mark.parametrize("comprehension", [False, True])
@pytest.mark.parametrize("arguments", ["z, pos", "z, xyz=pos", "Z=z, xyz=pos", "xyz=pos, Z=z"])
def test_atom_constructor_arguments_import_through_cli(
    tmp_path, periodic, comprehension, arguments
):
    source = tmp_path / "input.py"
    output = tmp_path / "output.qvf"
    expression = (
        f"[vq.Atom({arguments}) for z, pos in rows]"
        if comprehension
        else f"[vq.Atom({arguments}), Atom(1, [4, 5, -6])]"
    )
    source.write_text(
        "import vibeqc as vq\n"
        "from vibeqc import Atom, Molecule, PeriodicSystem\n"
        "raise RuntimeError('input code must not run')\n"
        "z = 8\n"
        "pos = [1, -2, 3]\n"
        "rows = [(8, [1, -2, 3]), (1, [4, 5, -6])]\n"
        f"atoms = {expression}\n"
        f"system = {_system('atoms', periodic, True)}\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["import", str(source), "-o", str(output)])

    assert result.exit_code == 0, result.output
    parsed = parse_input_source(source.read_text(encoding="utf-8"))
    assert [atom["atomic_number"] for atom in parsed.atoms] == [8, 1]
    assert [atom["position"] for atom in parsed.atoms] == [[1, -2, 3], [4, 5, -6]]
    with QVFReader(output) as reader:
        structure = reader.read_structure()
        assert [atom.symbol for atom in structure.atoms] == ["O", "H"]
        for atom, position in zip(structure.atoms, ([1, -2, 3], [4, 5, -6]), strict=True):
            assert atom.position == pytest.approx([c * 0.529177210903 for c in position])
        assert structure.pbc == ((True, True, False) if periodic else (False,) * 3)
