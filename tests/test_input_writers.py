"""Manual-backed native syntax, physical metadata and panel/queue refusal contracts."""

from __future__ import annotations

import io
import json
import sys
import zipfile
from dataclasses import replace
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from vibeview.input_writers import PROGRAMS, TASKS, InputSettings, write_input
from vibeview.qvf import Atom, StructureData


@pytest.fixture
def water():
    return StructureData(
        atoms=[
            Atom(symbol="O", atomic_number=8, position=[0.0, 0.0, 0.0]),
            Atom(symbol="H", atomic_number=1, position=[0.758, 0.0, 0.504]),
            Atom(symbol="H", atomic_number=1, position=[-0.758, 0.0, 0.504]),
        ],
        lattice_vectors=[[12.0, 0.0, 0.0], [0.0, 12.0, 0.0], [0.0, 0.0, 12.0]],
        pbc=(False, False, False),
        bonds=None,
    )


def settings(program, **kw):
    return InputSettings(program, potentials={"O": "GTH-PBE-q6", "H": "GTH-PBE-q1"}, **kw)


@pytest.mark.parametrize("program", PROGRAMS[:-1])
@pytest.mark.parametrize("task", TASKS)
def test_native_tasks_and_spin(water, program, task):
    if program == "xtb" and task == "tddft":
        with pytest.raises(ValueError, match="TD-DFT"):
            write_input(water, settings(program, task=task))
        return
    bundle = write_input(water, settings(program, task=task, charge=1, multiplicity=2))
    text = bundle.files[bundle.primary]
    markers = {
        "Gaussian": "1 2\n",
        "Psi4": "1 2\n",
        "NWChem": "charge 1\n",
        "Q-Chem": "1 2\n",
        "GAMESS": "ICHARG=1 MULT=2",
        "CP2K": "CHARGE 1\n",
    }
    if program == "xtb":
        assert bundle.command[bundle.command.index("--uhf") + 1] == "1"
        assert bundle.command[bundle.command.index("--chrg") + 1] == "1"
    else:
        assert markers[program] in text
    assert ".758000000000" in text
    with zipfile.ZipFile(io.BytesIO(bundle.archive())) as archive:
        assert set(archive.namelist()) == set(bundle.files)
        assert archive.read(bundle.primary).decode() == text


@pytest.mark.parametrize(
    ("program", "marker"),
    [
        ("Gaussian", "O -1 0.000000000000"),
        ("Psi4", "1 xyz\n3 xyz"),
        ("NWChem", "fix atom 1 3"),
        ("Q-Chem", "FIXED\n1 XYZ\n3 XYZ\nENDFIXED"),
        ("GAMESS", "IFREEZ(1)=1,2,3,7,8,9"),
        ("CP2K", "LIST 1 3"),
        ("xtb", "atoms: 1,3"),
    ],
)
def test_manual_frozen_numbering(water, program, marker):
    bundle = write_input(water, settings(program, task="optimization", frozen=(2, 0, 2)))
    assert marker in "\n".join(bundle.files.values())


@pytest.mark.parametrize("program", PROGRAMS[:-1])
def test_raw_text_and_refusals(water, program):
    raw = "expert_option\nsecond_line"
    bundle = write_input(water, settings(program, raw_keywords=raw))
    assert raw in "\n".join(bundle.files.values())
    for changes in (
        {"charge": 0.5},
        {"multiplicity": 2},
        {"frozen": (3,)},
        {"frozen": (0,), "task": "frequencies"},
        {"method": "PBE\nBAD"},
    ):
        with pytest.raises(ValueError):
            write_input(water, replace(settings(program), **changes))


def test_poscar_species_reordering_preserves_freezes(water):
    from ase.io import read

    water.pbc = [True, True, True]
    water.atoms = [water.atoms[1], water.atoms[0], water.atoms[2]]
    bundle = write_input(water, settings("VASP", frozen=(2,)))
    atoms = read(io.StringIO(bundle.files["POSCAR"]), format="vasp")
    assert atoms.get_chemical_symbols() == ["H", "H", "O"]
    np.testing.assert_allclose(atoms.positions, [water.atoms[i].position for i in (0, 2, 1)])
    assert json.loads(bundle.files["atom-order.json"])["original_zero_based_indices"] == [0, 2, 1]
    assert atoms.constraints[0].get_indices().tolist() == [1]
    np.testing.assert_allclose(atoms.cell.array, water.lattice_vectors)
    for kw in ({"charge": 2}, {"multiplicity": 3}, {"method": "PBE"}, {"task": "optimization"}):
        with pytest.raises(ValueError):
            write_input(water, settings("VASP", **kw))


def test_skew_periodic_cell_and_no_silent_molecular_fallback(water):
    cell = np.array([[8.0, 0.0, 0.0], [2.0, 9.0, 0.0], [1.0, 3.0, 10.0]])
    water.lattice_vectors = cell
    water.pbc = [True, True, True]
    for program in ("Psi4", "Q-Chem", "GAMESS"):
        with pytest.raises(ValueError, match="periodic"):
            write_input(water, settings(program))
    gaussian = write_input(water, settings("Gaussian")).files["calculation.gjf"]
    assert gaussian.count("Tv ") == 3
    cp2k = write_input(water, settings("CP2K")).files["calculation.inp"]
    assert "C 1.000000000000 3.000000000000 10.000000000000" in cp2k
    assert "PERIODIC XYZ" in cp2k
    nwchem = write_input(water, settings("NWChem", basis="PLANE_WAVE", cutoff=80))
    assert "cutoff 40" in nwchem.files[nwchem.primary]
    assert "task pspw energy" in nwchem.files[nwchem.primary]
    frac = np.array(water.atoms[1].position) @ np.linalg.inv(cell)
    assert "H " + " ".join(f"{x:.12f}" for x in frac) in nwchem.files[nwchem.primary]
    with pytest.raises(ValueError, match="PLANE_WAVE"):
        write_input(water, settings("NWChem"))
    with pytest.raises(ValueError, match="GFN-FF"):
        write_input(water, settings("xtb"))
    xtb = write_input(water, settings("xtb", method="GFN-FF"))
    assert "$periodic 3\n$lattice angs" in xtb.files["coord"]
    assert "--gfnff" in xtb.command


def test_required_potentials_and_finite_geometry(water):
    with pytest.raises(ValueError, match="pseudopotential"):
        write_input(water, InputSettings("CP2K"))
    water.atoms[0].position = [float("nan"), 0.0, 0.0]
    with pytest.raises(ValueError, match="finite"):
        write_input(water, InputSettings("Gaussian"))


def panel(water):
    state = SimpleNamespace(
        calc_engine="Psi4",
        calc_writer_method="",
        calc_writer_basis="",
        calc_charge=0,
        calc_multiplicity=1,
        calc_writer_task="optimization",
        frozen_atoms=[0],
        calc_writer_raw="set scf_type pk",
        calc_writer_states=5,
        calc_writer_potentials="{}",
        calc_writer_cutoff=400,
        calc_writer_preview="stale",
        calc_orca_wall_minutes=15,
    )
    return SimpleNamespace(
        server=SimpleNamespace(state=state),
        reader=SimpleNamespace(read_structure=lambda: water),
        exports=Mock(),
        queue=Mock(),
    )


def test_panel_preview_save_and_failed_refresh(water):
    from vibeview.input_writer_panel import preview, save

    viewer = panel(water)
    bundle = preview(viewer)
    assert bundle and "1 xyz" in viewer.server.state.calc_writer_preview
    save(viewer)
    data = viewer.exports._offer_download.call_args.args[0]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.read("calculation.dat").decode() == bundle.files["calculation.dat"]
    viewer.server.state.calc_multiplicity = 2
    assert preview(viewer) is None
    assert viewer.server.state.calc_writer_preview == ""


def test_registered_queue_exact_staging_and_save_only(water, monkeypatch, tmp_path):
    from vibeview.input_writer_panel import submit

    viewer = panel(water)
    binary = tmp_path / "psi4"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    config = ModuleType("vq.config")
    config.load_config = lambda: SimpleNamespace(
        programs={"psi4": SimpleNamespace(binary=str(binary))}
    )
    submission = ModuleType("vq.submit")
    captured = {}

    def submit_local(**kw):
        from pathlib import Path

        captured.update(kw)
        captured["text"] = (Path(kw["directory"]) / "calculation.dat").read_text()
        return "abc123"

    submission.submit_local = submit_local
    monkeypatch.setitem(sys.modules, "vq", ModuleType("vq"))
    monkeypatch.setitem(sys.modules, "vq.config", config)
    monkeypatch.setitem(sys.modules, "vq.submit", submission)
    submit(viewer)
    assert captured["program"] == "psi4"
    assert captured["command"] == [str(binary), "calculation.dat", "calculation.out"]
    assert captured["wall_time_seconds"] == 900
    assert "1 xyz" in captured["text"]
    from pathlib import Path

    assert not Path(captured["directory"]).exists()
    binary.unlink()
    with pytest.raises(ValueError, match="Register"):
        submit(viewer)
    viewer.server.state.calc_engine = "Gaussian"
    with pytest.raises(ValueError, match="save-only"):
        submit(viewer)


def test_xtb_queue_runner_and_optional_dependency(water, monkeypatch, tmp_path):
    from pathlib import Path

    import vibeview.input_writer_panel as panel_module
    from vibeview.input_writer_panel import submit

    viewer = panel(water)
    viewer.server.state.calc_engine = "xtb"
    viewer.server.state.calc_writer_raw = ""
    binary = tmp_path / "xtb"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    config = ModuleType("vq.config")
    config.load_config = lambda: SimpleNamespace(
        programs={"xtb": SimpleNamespace(binary=str(binary))}
    )
    submission = ModuleType("vq.submit")
    captured = {}

    def submit_local(**kw):
        captured.update(kw)
        captured["request"] = json.loads((Path(kw["directory"]) / "xtb-job.json").read_text())
        return "abc123"

    submission.submit_local = submit_local
    monkeypatch.setitem(sys.modules, "vq", ModuleType("vq"))
    monkeypatch.setitem(sys.modules, "vq.config", config)
    monkeypatch.setitem(sys.modules, "vq.submit", submission)
    monkeypatch.setattr(
        panel_module.importlib, "import_module", lambda name: SimpleNamespace(run_xtb=lambda: None)
    )
    submit(viewer)
    assert captured["command"] == [
        sys.executable, "-m", "qvf", "run-xtb", "xtb-job.json", "--binary", str(binary)
    ]
    assert captured["program"] == "xtb" and captured["cpus"] == 1
    assert captured["request"]["outputs"] == dict(orbitals=True, bond_orders=True, density=False)
    assert not Path(captured["directory"]).exists()
    def absent(name):
        raise ImportError("absent adapter")
    monkeypatch.setattr(panel_module.importlib, "import_module", absent)
    captured.clear()
    with pytest.raises(ValueError, match="qvf-writer"):
        submit(viewer)
    assert not captured


def test_xtb_output_selection_and_gfnff_native_route(water):
    bundle = write_input(
        water, settings("xtb", xtb_orbitals=False, xtb_bond_orders=False, xtb_density=True)
    )
    assert "json=true" in bundle.files["xcontrol"] and "density=true" in bundle.files["xcontrol"]
    assert "--molden" not in bundle.command and "--wbo" not in bundle.command
    assert json.loads(bundle.files["xtb-job.json"])["outputs"] == dict(
        orbitals=False, bond_orders=False, density=True
    )
    ff = write_input(water, settings("xtb", method="GFN-FF"))
    assert "xtb-job.json" not in ff.files and "--gfnff" in ff.command


@pytest.mark.parametrize("program", PROGRAMS)
def test_reviewed_manual_reference_inputs(water, program):
    from pathlib import Path

    water.pbc = (program == "VASP",) * 3
    bundle = write_input(
        water,
        settings(
            program, task="single_point" if program == "VASP" else "optimization", frozen=(0, 2)
        ),
    )
    folder = Path(__file__).parent / "fixtures" / "input_writers" / program.lower().replace("-", "")
    for name, text in bundle.files.items():
        if name != "README.txt":
            assert text == (folder / name).read_text()


@pytest.mark.parametrize(
    "task,markers",
    [
        (
            "single_point",
            [
                " SP",
                "energy('pbe0')",
                "task dft energy",
                "JOBTYPE sp",
                "RUNTYP=ENERGY",
                "RUN_TYPE ENERGY",
            ],
        ),
        (
            "optimization",
            [
                " Opt",
                "optimize('pbe0')",
                "task dft optimize",
                "JOBTYPE opt",
                "RUNTYP=OPTIMIZE",
                "RUN_TYPE GEO_OPT",
            ],
        ),
        (
            "frequencies",
            [
                " Freq",
                "frequency('pbe0')",
                "task dft frequencies",
                "JOBTYPE freq",
                "RUNTYP=HESSIAN",
                "RUN_TYPE VIBRATIONAL_ANALYSIS",
            ],
        ),
        (
            "tddft",
            [
                "TD(NStates=5)",
                "states=5, tda=False",
                "task tddft energy",
                "CIS_N_ROOTS 5\nRPA TRUE",
                "TDDFT=EXCITE",
                "&TDDFPT T\n   NSTATES 5",
            ],
        ),
    ],
)
def test_reference_task_keywords(water, task, markers):
    for program, marker in zip(PROGRAMS[:6], markers, strict=True):
        bundle = write_input(water, settings(program, task=task))
        assert marker in bundle.files[bundle.primary]


def test_cp2k_partial_periodic_solver_and_nesting(water):
    water.pbc = (True, True, False)
    bundle = write_input(water, settings("CP2K", task="optimization", frozen=(0,)))
    text = bundle.files[bundle.primary]
    assert "PERIODIC XY" in text and "POISSON_SOLVER ANALYTIC" in text
    stack = []
    for line in text.splitlines():
        words = line.strip().split()
        if not words:
            continue
        if words[0] == "&END":
            assert stack.pop() == words[1]
        elif words[0].startswith("&"):
            stack.append(words[0][1:])
    assert stack == []
