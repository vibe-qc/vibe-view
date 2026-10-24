"""Reading and writing vibe-qc input files (input_parser, input_generator)."""

from __future__ import annotations

import ast

import pytest


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


@pytest.mark.parametrize(
    "template", ["single_point", "optimization", "frequencies", "full", "periodic"]
)
@pytest.mark.parametrize("live_checkpoint", [False, True])
def test_generated_input_preserves_string_literals(template, live_checkpoint):
    from vibeview.input_generator import generate_input_script
    from vibeview.input_parser import parse_input_source

    text = 'quoted "name" and \'apostrophe\' with \\backslash\nnext line'
    params = {
        "atoms": [{"atomic_number": 8, "position": [1, 2, 3]}],
        "basis": text,
        "method": text,
        "functional": text,
        "output": text,
        "live_checkpoint": live_checkpoint,
    }
    if template == "periodic":
        params["lattice_vectors"] = [[8, 0, 0], [0, 9, 0], [0, 0, 10]]
    script = generate_input_script(template, params)

    compile(script, "<generated input>", "exec")
    tree = ast.parse(script)
    runner = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("run_job", "run_periodic_job")
    )
    kwargs = {kw.arg: kw.value for kw in runner.keywords}
    for name in ("method", "functional", "output"):
        assert ast.literal_eval(kwargs[name]) == text
    if template == "periodic":
        basis = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "BasisSet"
        )
        assert ast.literal_eval(basis.args[1]) == text
    else:
        assert ast.literal_eval(kwargs["basis"]) == text
    assert ("checkpoint_qvf" in kwargs) == live_checkpoint
    parsed = parse_input_source(script)
    assert parsed.output == text
    assert parsed.method == text
    assert parsed.functional == text


@pytest.mark.parametrize(
    "template", ["single_point", "optimization", "frequencies", "full", "periodic"]
)
@pytest.mark.parametrize("line_break", ["\n", "\r", "\r\n"])
def test_multiline_input_title_remains_a_comment(template, line_break):
    from vibeview.input_generator import generate_input

    script = generate_input(
        [{"atomic_number": 8, "position": [0, 0, 0]}],
        template=template,
        title=f"Water calculation{line_break}Second title line",
        lattice_vectors=[[8, 0, 0], [0, 9, 0], [0, 0, 10]] if template == "periodic" else None,
    )

    compile(script, "<generated input>", "exec")
    assert script.splitlines()[0].startswith("# Water calculation")
    assert script.splitlines()[1].startswith("# Second title line")


# ── generated scripts run against vibe-qc's call signatures (#85) ───────────
#
# The templates passed charge and multiplicity to run_job, which is
# keyword-only and takes neither, so every generated molecular script stopped
# with a TypeError, and an ion would have run neutral. The stand-ins below
# copy the signatures on vibe-qc's main: Molecule and PeriodicSystem from
# cpp/src/bindings.cpp, run_job from python/vibeqc/runner.py and
# run_periodic_job from python/vibeqc/periodic_runner.py. They list only the
# keywords the templates use. Before a template gains a keyword, check that
# vibe-qc's function has it, then add it here.

_MOLECULAR_TEMPLATES = ("single_point", "optimization", "frequencies", "full")


def _fake_vibeqc(calls: list[tuple[str, object]]):
    import types

    module = types.ModuleType("vibeqc")

    class Atom:
        def __init__(self, atomic_number, position):
            self.atomic_number = atomic_number
            self.position = position

    class Molecule:
        def __init__(self, atoms, charge=0, multiplicity=1):
            self.atoms = list(atoms)
            self.charge = charge
            self.multiplicity = multiplicity
            calls.append(("Molecule", self))

    class PeriodicSystem:
        def __init__(self, dim, lattice, unit_cell, charge=0, multiplicity=1):
            self.dim = dim
            self.unit_cell = list(unit_cell)
            self.charge = charge
            self.multiplicity = multiplicity
            calls.append(("PeriodicSystem", self))

        def unit_cell_molecule(self):
            return Molecule(self.unit_cell, self.charge, self.multiplicity)

    class BasisSet:
        def __init__(self, molecule, name):
            self.name = name

    def run_job(
        molecule, *, basis=None, method="auto", functional=None, output="output",
        output_qvf=False, write_molden_file=None, optimize=False, hessian=False,
        write_cube=False, checkpoint_qvf=None, checkpoint_every=None,
    ):
        calls.append(("run_job", {"molecule": molecule, "basis": basis}))

    def run_periodic_job(
        system, *, basis=None, method="auto", functional=None, output="output",
        output_qvf=False, write_density=False, checkpoint_qvf=None,
        checkpoint_every=None,
    ):
        calls.append(("run_periodic_job", {"system": system}))

    module.Atom = Atom
    module.Molecule = Molecule
    module.PeriodicSystem = PeriodicSystem
    module.BasisSet = BasisSet
    module.run_job = run_job
    module.run_periodic_job = run_periodic_job
    return module


def _run_generated(script: str, monkeypatch) -> list[tuple[str, object]]:
    import sys

    calls: list[tuple[str, object]] = []
    monkeypatch.setitem(sys.modules, "vibeqc", _fake_vibeqc(calls))
    exec(compile(script, "<generated vibe-qc input>", "exec"), {"__name__": "__generated__"})
    return calls


_ION = [
    {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.0]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.0, 0.97]},
]


@pytest.mark.parametrize("live_checkpoint", [False, True])
@pytest.mark.parametrize("template", _MOLECULAR_TEMPLATES)
def test_molecular_script_runs_and_the_molecule_carries_charge(
    template, live_checkpoint, monkeypatch
):
    """Hydroxide, charge -1, as a doublet so the two values cannot be confused."""
    from vibeview.input_generator import generate_input

    script = generate_input(
        _ION, method="uhf", charge=-1, multiplicity=2, template=template,
        live_checkpoint=live_checkpoint,
    )
    calls = _run_generated(script, monkeypatch)

    (molecule,) = [obj for name, obj in calls if name == "Molecule"]
    assert (molecule.charge, molecule.multiplicity) == (-1, 2)
    assert [a.atomic_number for a in molecule.atoms] == [8, 1]
    (job,) = [obj for name, obj in calls if name == "run_job"]
    assert job["molecule"] is molecule


@pytest.mark.parametrize("live_checkpoint", [False, True])
def test_periodic_script_runs_and_the_system_carries_charge(live_checkpoint, monkeypatch):
    from vibeview.input_generator import generate_input

    script = generate_input(
        _ION, charge=1, multiplicity=3, template="periodic",
        lattice_vectors=[[5.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0, 5.0]],
        live_checkpoint=live_checkpoint,
    )
    calls = _run_generated(script, monkeypatch)

    (system,) = [obj for name, obj in calls if name == "PeriodicSystem"]
    assert (system.charge, system.multiplicity) == (1, 3)
    (job,) = [obj for name, obj in calls if name == "run_periodic_job"]
    assert job["system"] is system


@pytest.mark.parametrize("template", _MOLECULAR_TEMPLATES)
def test_run_job_call_binds_to_the_installed_vibeqc(template):
    """With vibe-qc installed, check the real signature instead of the stand-in."""
    import inspect

    vibeqc = pytest.importorskip("vibeqc", reason="vibe-qc is not installed")
    from vibeview.input_generator import generate_input

    script = generate_input(_ION, charge=-1, multiplicity=2, template=template)
    (call,) = [
        node for node in ast.walk(ast.parse(script))
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "run_job"
    ]
    keywords = {kw.arg: None for kw in call.keywords}
    inspect.signature(vibeqc.run_job).bind(None, **keywords)
