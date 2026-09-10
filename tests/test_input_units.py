"""Unit-contract tests for the vibe-qc .py input round-trip.

QVF structures are Å; vibe-qc inputs are bohr with lattice COLUMNS as
vectors. The 2026-07-02 audit found both directions violated: generated
scripts embedded Å as bohr (geometries shrunk 0.529x when run) and
parsed inputs stored bohr as Å (geometries inflated 1.89x when viewed).
"""

from __future__ import annotations

import ast
import math
import tempfile
from pathlib import Path

import pytest

_BOHR = 0.529177210903

WATER_ANG = [
    {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.036297]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.754724, -0.536742]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, -0.754724, -0.536742]},
]


def _periodic_system_dimension(script: str) -> int:
    """Return the literal dimension from the generated PeriodicSystem call."""
    tree = ast.parse(script)
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "PeriodicSystem"
    )
    return int(ast.literal_eval(call.args[0]))


class TestGeneratedInputUnits:
    def test_positions_emitted_in_bohr(self) -> None:
        from vibeview.input_generator import generate_input

        script = generate_input(WATER_ANG)
        # O-H distance must be ~1.79 bohr (0.948 Å), not 0.948 verbatim.
        for line in script.splitlines():
            if line.strip().startswith("Atom(1,"):
                coords = eval(line.split("Atom(1, ")[1].split("])")[0] + "]")
                oh = math.dist(coords, [c / _BOHR for c in WATER_ANG[0]["position"]])
                assert 1.7 < oh < 1.9, f"O-H {oh} not in bohr"
                break

    def test_molecular_template_still_roundtrips_charge_and_multiplicity(self) -> None:
        from vibeview.input_generator import generate_input
        from vibeview.input_parser import parse_input_source

        script = generate_input(
            WATER_ANG,
            method="uks",
            charge=1,
            multiplicity=2,
        )
        parsed = parse_input_source(script)

        assert not parsed.is_periodic
        assert parsed.method == "uks"
        assert parsed.charge == 1
        assert parsed.multiplicity == 2

    def test_periodic_template_transposes_cell_and_honours_dimension(self) -> None:
        from vibeview.input_generator import generate_input
        from vibeview.input_parser import parse_input_source

        lattice = [[4.0, 0.5, 0.0], [1.0, 5.0, 0.25], [0.0, 2.0, 6.0]]
        script = generate_input(
            [
                {"symbol": "C", "atomic_number": 6, "position": [0.0, 0.0, 0.0]},
                {"symbol": "C", "atomic_number": 6, "position": [1.4, 0.0, 0.0]},
            ],
            lattice_vectors=lattice,
            dimensionality=2,
            template="periodic",
            method="uks",
            charge=-1,
            multiplicity=2,
        )
        assert "BasisSet" in script.split("run_periodic_job")[0], "BasisSet import missing"
        assert _periodic_system_dimension(script) == 2
        periodic_call = next(
            node
            for node in ast.walk(ast.parse(script))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "PeriodicSystem"
        )
        keywords = {kw.arg: ast.literal_eval(kw.value) for kw in periodic_call.keywords}
        assert keywords["charge"] == -1
        assert keywords["multiplicity"] == 2

        parsed = parse_input_source(script)
        assert parsed.dimensionality == 2
        assert parsed.charge == -1
        assert parsed.multiplicity == 2
        expected_columns_bohr = [[lattice[j][i] / _BOHR for j in range(3)] for i in range(3)]
        assert parsed.lattice_vectors is not None
        for actual, expected in zip(parsed.lattice_vectors, expected_columns_bohr, strict=True):
            assert actual == pytest.approx(expected, abs=1e-6)
        compile(script, "<generated>", "exec")  # must at least be valid Python

    def test_periodic_template_honours_dimension_and_defaults_to_three(self) -> None:
        from vibeview.input_generator import generate_input
        from vibeview.input_parser import parse_input_source

        atoms = [{"symbol": "Na", "atomic_number": 11, "position": [0.0, 0.0, 0.0]}]
        lattice = [[4.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0, 6.0]]
        script_1d = generate_input(
            atoms,
            lattice_vectors=lattice,
            dimensionality=1,
            template="periodic",
        )
        script_default = generate_input(
            atoms,
            lattice_vectors=lattice,
            template="periodic",
        )

        assert _periodic_system_dimension(script_1d) == 1
        assert _periodic_system_dimension(script_default) == 3
        parsed_default = parse_input_source(script_default)
        assert parsed_default.charge == 0
        assert parsed_default.multiplicity == 1

    @pytest.mark.parametrize("dimensionality", [True, 0, 4, 1.5, "2"])
    def test_periodic_template_rejects_invalid_dimension(self, dimensionality: object) -> None:
        from vibeview.input_generator import generate_input

        with pytest.raises(ValueError, match="dimensionality.*integer from 1 to 3"):
            generate_input(
                [{"symbol": "Na", "atomic_number": 11, "position": [0.0, 0.0, 0.0]}],
                lattice_vectors=[
                    [4.0, 0.0, 0.0],
                    [0.0, 5.0, 0.0],
                    [0.0, 0.0, 6.0],
                ],
                dimensionality=dimensionality,  # type: ignore[arg-type]
                template="periodic",
            )

    def test_periodic_template_requires_lattice(self) -> None:
        from vibeview.input_generator import generate_input

        with pytest.raises(ValueError, match="requires lattice vectors"):
            generate_input(WATER_ANG, template="periodic")

    def test_py_to_qvf_converts_bohr_to_angstrom(self) -> None:
        from vibeview.converters import py_to_qvf
        from vibeview.qvf import QVFReader

        src = (
            "from vibeqc import Atom, Molecule, run_job\n"
            "mol = Molecule([Atom(8, [0.0, 0.0, 0.0]), Atom(1, [0.0, 0.0, 1.79])])\n"
        )
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(src)
        s = QVFReader(py_to_qvf(f.name)).read_structure()
        oh = math.dist(list(s.atoms[0].position), list(s.atoms[1].position))
        assert 0.90 < oh < 1.0, f"O-H {oh} Å — bohr not converted"
        Path(f.name).unlink()

    def test_generated_script_roundtrips_through_parser(self) -> None:
        from vibeview.converters import py_to_qvf
        from vibeview.input_generator import generate_input
        from vibeview.qvf import QVFReader

        script = generate_input(WATER_ANG)
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(script)
        s = QVFReader(py_to_qvf(f.name)).read_structure()
        oh = math.dist(list(s.atoms[0].position), list(s.atoms[1].position))
        expected = math.dist(WATER_ANG[0]["position"], WATER_ANG[1]["position"])
        assert abs(oh - expected) < 1e-5
        Path(f.name).unlink()


class TestParserKeywordForms:
    def test_molecule_keyword_atoms_charge_multiplicity(self) -> None:
        from vibeview.input_parser import parse_input_source

        res = parse_input_source(
            "from vibeqc import Atom, Molecule\n"
            "mol = Molecule(atoms=[Atom(8, [0, 0, 0]), Atom(1, [0, 0, 1.8])],"
            " charge=1, multiplicity=2)\n"
        )
        assert len(res.atoms) == 2
        assert res.charge == 1
        assert res.multiplicity == 2

    def test_periodic_keyword_form_and_variable_lattice(self) -> None:
        from vibeview.input_parser import parse_input_source

        res = parse_input_source(
            "import numpy as np\n"
            "from vibeqc import Atom, PeriodicSystem\n"
            "cell = np.array([[8.0, 0.0, 0.0], [0.0, 8.0, 0.0], [0.0, 0.0, 8.0]])\n"
            "s = PeriodicSystem(dim=3, lattice=cell, unit_cell=[Atom(6, [0, 0, 0])],"
            " charge=-1, multiplicity=2)\n"
        )
        assert res.is_periodic
        assert len(res.atoms) == 1
        assert res.lattice_vectors == [[8.0, 0.0, 0.0], [0.0, 8.0, 0.0], [0.0, 0.0, 8.0]]
        assert res.charge == -1
        assert res.multiplicity == 2
