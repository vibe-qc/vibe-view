"""Static Python imports must not invent a periodic lattice."""

import numpy as np
import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.converters import py_to_qvf
from vibeview.input_parser import parse_input_source
from vibeview.qvf import QVFReader


@pytest.mark.parametrize("lattice", ["build_cell()", "missing_cell", "None", "np.eye(3)"])
def test_unresolved_lattice_preserves_partial_parse_but_refuses_archive(tmp_path, lattice):
    source = (
        "from vibeqc import Atom, PeriodicSystem\n"
        f"system = PeriodicSystem(2, {lattice}, [Atom(6, [1, 2, 3])])\n"
    )
    parsed = parse_input_source(source)
    assert parsed.is_periodic
    assert parsed.dimensionality == 2
    assert parsed.lattice_vectors is None
    assert parsed.atoms[0]["position"] == [1, 2, 3]
    path = tmp_path / "input.py"
    path.write_text(source)
    with pytest.raises(ValueError, match="periodic lattice.*statically"):
        py_to_qvf(path)


def test_cli_import_reports_unresolved_lattice_without_writing_archive(tmp_path):
    source = tmp_path / "input.py"
    output = tmp_path / "output.qvf"
    source.write_text("s = PeriodicSystem(3, unknown_cell, [Atom(6, [0, 0, 0])])\n")
    result = CliRunner().invoke(main, ["import", str(source), "-o", str(output)])
    assert result.exit_code != 0
    assert "periodic lattice" in result.output
    assert not output.exists()


@pytest.mark.parametrize("dim", [1, 2, 3])
def test_explicit_skew_lattice_retains_columns_bohr_contract(tmp_path, dim):
    columns = [[8, 1, 0], [0, 9, 2], [1, 0, 10]]
    source = tmp_path / "input.py"
    source.write_text(
        f"cell = {columns!r}\n"
        f"s = PeriodicSystem({dim}, cell, [Atom(6, [1, 2, 3])])\n"
    )
    with QVFReader(py_to_qvf(source)) as reader:
        structure = reader.read_structure()
        np.testing.assert_allclose(structure.lattice_vectors,
                                   np.asarray(columns).T * 0.529177210903)
        np.testing.assert_allclose(structure.atoms[0].position,
                                   np.array([1, 2, 3]) * 0.529177210903)
        assert structure.pbc == tuple(i < dim for i in range(3))


def test_explicit_identity_lattice_is_preserved(tmp_path):
    source = tmp_path / "input.py"
    source.write_text(
        "s = PeriodicSystem(3, [[1,0,0],[0,1,0],[0,0,1]], [Atom(6,[0,0,0])])\n"
    )
    with QVFReader(py_to_qvf(source)) as reader:
        np.testing.assert_allclose(reader.read_structure().lattice_vectors,
                                   np.eye(3) * 0.529177210903)


def test_unknown_function_is_not_executed(tmp_path):
    marker = tmp_path / "executed"
    source = (
        "def build_cell():\n"
        f"    open({str(marker)!r}, 'w').write('unsafe')\n"
        "    return [[8,0,0],[0,8,0],[0,0,8]]\n"
        "s = PeriodicSystem(3, build_cell(), [Atom(6,[0,0,0])])\n"
    )
    parsed = parse_input_source(source)
    assert parsed.lattice_vectors is None
    assert not marker.exists()
