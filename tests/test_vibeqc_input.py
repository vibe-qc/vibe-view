"""Reading and writing vibe-qc input files (input_parser, input_generator)."""

from __future__ import annotations

import ast


class TestInputParserEdgeCases:
    def test_parse_with_extra_kwargs(self):
        from vibeview.input_parser import parse_input_source

        source = """
from vibeqc import Atom, Molecule, run_job
mol = Molecule([Atom(6, [0,0,0])])
run_job(mol, basis="sto-3g", method="rhf", nroots=5, guess="sad")
"""
        result = parse_input_source(source)
        assert result.basis == "sto-3g"
        assert result.method == "rhf"

    def test_parse_with_atom_class(self):
        from vibeview.input_parser import parse_input_source

        # Test that various Atom constructor forms work
        source = """
from vibeqc import Atom, Molecule
mol = Molecule([Atom("C", [0,0,0], unit="angstrom")])
"""
        result = parse_input_source(source)
        assert len(result.atoms) >= 1

    def test_parse_with_tddft(self):
        from vibeview.input_parser import parse_input_source

        source = """
from vibeqc import Atom, Molecule, run_job
mol = Molecule([Atom(8, [0,0,0])])
run_job(mol, basis="6-31g", method="rks", tddft=True, nroots=10)
"""
        result = parse_input_source(source)
        assert result.method == "rks"


class TestInputGeneratorEdgeCases:
    def test_generate_with_charge_negative(self):
        from vibeview.input_generator import generate_input_script

        atoms = [{"symbol": "Cl", "position": [0, 0, 0], "atomic_number": 17}]
        params = {
            "atoms": atoms,
            "basis": "aug-cc-pvdz",
            "method": "rhf",
            "functional": "",
            "charge": -1,
            "multiplicity": 1,
            "title": "Cl-",
        }
        script = generate_input_script("single_point", params)
        assert "charge=-1" in script

    def test_generate_all_templates(self):
        from vibeview.input_generator import generate_input_script

        atoms = [{"symbol": "H", "position": [0, 0, 0], "atomic_number": 1}]
        base = {
            "atoms": atoms,
            "basis": "sto-3g",
            "method": "rhf",
            "functional": "",
            "charge": 0,
            "multiplicity": 1,
            "title": "test",
        }
        for tmpl in ["single_point", "optimization", "frequencies", "full", "periodic"]:
            params = base
            if tmpl == "periodic":
                params = {
                    **base,
                    "lattice_vectors": [
                        [5.0, 0.0, 0.0],
                        [0.0, 5.0, 0.0],
                        [0.0, 0.0, 5.0],
                    ],
                }
            script = generate_input_script(tmpl, params)
            assert "run_job" in script or "PeriodicSystem" in script
            assert len(script) > 100


class TestInputParserIntegration:
    """Test the input parser + generator round-trip."""

    def test_parse_molecule_input(self):
        """Parse a real vibe-qc Molecule input."""
        from vibeview.input_parser import parse_input_source

        source = """
from vibeqc import Atom, Molecule, run_job

mol = Molecule([
    Atom(8, [0.0, 0.0, 0.0]),
    Atom(1, [0.0, 0.757, 0.586]),
    Atom(1, [0.0, -0.757, 0.586]),
])

run_job(mol, basis="cc-pvdz", method="rks", functional="pbe", charge=0, multiplicity=1)
"""
        result = parse_input_source(source)
        assert len(result.atoms) == 3
        assert result.basis == "cc-pvdz"
        assert result.method == "rks"
        assert result.functional == "pbe"
        assert result.charge == 0
        assert result.multiplicity == 1

    def test_parse_periodic_input(self):
        """Parse a periodic System input."""
        from vibeview.input_parser import parse_input_source

        source = """
from vibeqc import Atom, PeriodicSystem, run_periodic_job

sys = PeriodicSystem(dim=2, lattice=[[5.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0, 5.0]], unit_cell=[
    Atom(14, [0.0, 0.0, 0.0]),
    Atom(14, [0.25, 0.25, 0.25]),
])

run_periodic_job(sys, basis="sto-3g", method="rks", functional="pbe")
"""
        result = parse_input_source(source)
        assert result.is_periodic
        assert result.dimensionality == 2
        assert result.lattice_vectors is not None
        assert len(result.atoms) == 2

    def test_parse_empty_input(self):
        """Gracefully handle empty input."""
        from vibeview.input_parser import parse_input_source

        result = parse_input_source("")
        assert len(result.atoms) == 0
        assert result.basis is None

    def test_parse_input_file(self, tmp_path):
        """Parse from a file on disk."""
        from vibeview.input_parser import parse_input_file

        f = tmp_path / "test_input.py"
        f.write_text("""
from vibeqc import Atom, Molecule, run_job
mol = Molecule([Atom(6, [0,0,0])])
run_job(mol, basis="6-31g", method="rhf")
""")
        result = parse_input_file(str(f))
        assert len(result.atoms) == 1
        assert result.basis == "6-31g"

    def test_parse_library_periodic_builder(self):
        """The qc-input-library build_system pattern: fractional _ATOMS +
        _LATTICE_A rows in Angstrom + BOHR scaling resolve to cartesian
        bohr atoms and a column lattice."""
        from vibeview.input_parser import parse_input_source

        source = """
import numpy as np
import vibeqc as vq

BOHR = 1.8897261246257702
_LATTICE_A = np.array([[3.553, 0, 0], [0, 3.553, 0], [0, 0, 3.553]])
_ATOMS = [(6, (0.25, 0.25, 0.25))]
_MULTIPLICITY = 1
_BASIS = "pob-tzvp-rev2"
_METHOD = "RHF"


def build_system() -> vq.PeriodicSystem:
    lattice_a = _LATTICE_A * BOHR
    lattice = lattice_a.T.copy()
    atoms = [
        vq.Atom(int(z), (np.asarray(f) @ lattice_a).tolist())
        for z, f in _ATOMS
    ]
    return vq.PeriodicSystem(3, lattice, atoms, multiplicity=_MULTIPLICITY)


if __name__ == "__main__":
    system = build_system()
    run_periodic_job(system, vq.BasisSet(system.unit_cell_molecule(), "pob-tzvp-rev2"),
                     method="RHF", kpoints=[14, 14, 14])
"""
        result = parse_input_source(source)
        assert result.is_periodic
        assert len(result.atoms) == 1
        assert result.atoms[0]["atomic_number"] == 6
        bohr = 1.8897261246257702
        expected = 0.25 * 3.553 * bohr
        assert abs(result.atoms[0]["position"][0] - expected) < 1e-9
        assert result.lattice_vectors is not None
        assert abs(result.lattice_vectors[0][0] - 3.553 * bohr) < 1e-9
        assert result.basis == "pob-tzvp-rev2"
        assert result.method == "RHF"
        assert result.multiplicity == 1
        assert result.extra.get("kpoints") == [14, 14, 14]

    def test_parser_never_executes_input_code(self, tmp_path):
        """The evaluator only interprets literals — an input file's own
        code (file writes, imports, calls) must never run during parsing."""
        from vibeview.input_parser import parse_input_source

        marker = tmp_path / "executed"
        source = f"""
import os
from vibeqc import Atom, Molecule
payload = open({str(marker)!r}, "w")
payload.write("ran")
mol = Molecule([Atom(8, [0, 0, 0])])
"""
        result = parse_input_source(source)
        assert len(result.atoms) == 1
        assert not marker.exists()


class TestInputGeneratorIntegration:
    """Test the vibe-qc input script generator."""

    def test_generate_single_point(self):
        from vibeview.input_generator import generate_input_script

        atoms = [{"symbol": "H", "atomic_number": 1, "position": [0.0, 0.0, 0.0]}]
        params = {
            "atoms": atoms,
            "basis": "sto-3g",
            "method": "rhf",
            "functional": "",
            "charge": 0,
            "multiplicity": 1,
            "title": "H atom",
        }
        script = generate_input_script("single_point", params)
        assert "Molecule" in script
        assert "sto-3g" in script
        assert "run_job" in script
        assert "output_qvf=True" in script

    def test_generate_optimization(self):
        from vibeview.input_generator import generate_input_script

        atoms = [{"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.0]}]
        params = {
            "atoms": atoms,
            "basis": "6-31g(d)",
            "method": "rks",
            "functional": "pbe0",
            "charge": 0,
            "multiplicity": 3,
            "title": "O atom opt",
        }
        script = generate_input_script("optimization", params)
        assert "optimize=True" in script
        assert "pbe0" in script

    def test_generate_periodic(self):
        from vibeview.input_generator import generate_input_script

        atoms = [{"symbol": "Si", "atomic_number": 14, "position": [0.0, 0.0, 0.0]}]
        params = {
            "atoms": atoms,
            "lattice_vectors": [
                [5.43, 0.0, 0.0],
                [0.0, 5.43, 0.0],
                [0.0, 0.0, 5.43],
            ],
            "basis": "sto-3g",
            "method": "uks",
            "functional": "pbe",
            "charge": -1,
            "multiplicity": 2,
            "title": "Si bulk",
        }
        script = generate_input_script("periodic", params)
        periodic_call = next(
            node
            for node in ast.walk(ast.parse(script))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "PeriodicSystem"
        )
        assert ast.literal_eval(periodic_call.args[0]) == 3
        keywords = {kw.arg: ast.literal_eval(kw.value) for kw in periodic_call.keywords}
        assert keywords["charge"] == -1
        assert keywords["multiplicity"] == 2
        assert "cell = np.array([\n\n])" not in script
        assert "run_periodic_job" in script
