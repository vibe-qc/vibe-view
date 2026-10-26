"""ORCA calculation identity through import, CLI and browser file loading."""

from __future__ import annotations

import base64
import contextlib
import glob
import json
import os
import types
from pathlib import Path

import pytest
from click.testing import CliRunner

from vibeview import cli
from vibeview.converters import convert_to_qvf
from vibeview.import_workflow import collect_import_inputs, execute_import, plan_imports
from vibeview.qvf import QVFReader


def _wavefunction() -> bytes:
    return json.dumps({"Molecule": {
        "Atoms": [{"ElementNumber": 2, "Coords": [0, 0, 0],
                   "Basis": [{"Shell": "s", "Exponents": [1.0], "Coefficients": [1.0]}]}],
        "Charge": 0, "Multiplicity": 1, "CoordinateUnits": "Angs", "HFTyp": "RHF",
        "MolecularOrbitals": {"EnergyUnit": "Eh", "MOs": [
            {"MOCoefficients": [1.0], "Occupancy": 2.0, "OrbitalEnergy": -0.5},
        ]},
    }}).encode()


def _calculation(directory: Path, name: str = "job.1") -> tuple[Path, Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    properties = directory / f"{name}.property.json"
    properties.write_text(json.dumps({
        "Calculation_Info": {"Charge": 0, "Mult": 1, "NumOfElectrons": 2},
        "Geometries": [{"Geometry": {
            "NAtoms": 1,
            "Coordinates": {"Type": "Cartesians", "Units": "angstrom",
                            "Cartesians": [["He", 0, 0, 0]]},
        }}],
    }))
    orbitals = directory / f"{name}.json"
    orbitals.write_bytes(_wavefunction())
    spectrum = directory / f"{name}.mcd"
    spectrum.write_text("10000 -0.2 0.5\n")
    return properties, orbitals, spectrum


def test_import_groups_explicit_companions_and_names_the_calculation(tmp_path: Path) -> None:
    sources = _calculation(tmp_path)

    plans = plan_imports(sources)

    assert len(plans) == 1
    assert plans[0].source == sources[0]
    assert plans[0].destination == tmp_path / "job.1.qvf"
    result = execute_import(plans[0])
    assert {"structure", "wavefunction.gto", "spectra.generic"} <= set(result.section_kinds)


def test_directory_import_keeps_distinct_calculations_and_ignores_guess(tmp_path: Path) -> None:
    _calculation(tmp_path / "one")
    _calculation(tmp_path / "two")
    guess = tmp_path / "one" / "job.1.TDDFTGuess.gbw"
    guess.write_bytes(b"auxiliary orbital guess")

    plans = plan_imports([tmp_path], output=tmp_path / "imported")

    assert len(plans) == 2
    assert [plan.destination.name for plan in plans] == ["job.1.qvf", "job.1_2.qvf"]
    explicit, _ = collect_import_inputs([guess])
    assert explicit == [guess]


@pytest.mark.parametrize("scan_kind", ["directory", "glob"])
def test_gui_scans_deduplicate_companions_before_candidate_limit(
    tmp_path: Path, scan_kind: str,
) -> None:
    from vibeview.app.helpers import _scan_gui_directory, _scan_gui_glob

    sources = _calculation(tmp_path)
    (tmp_path / "job.1.TDDFTGuess.gbw").write_bytes(b"auxiliary orbital guess")
    if scan_kind == "directory":
        result = _scan_gui_directory(tmp_path, recursive=False, max_candidates=2)
    else:
        result = _scan_gui_glob(str(tmp_path / "*"), recursive=False, max_candidates=2)

    assert len(result.candidates) == 1
    assert result.candidates[0] in sources
    assert result.truncated is False


@pytest.mark.parametrize("scan_kind", ["import", "directory", "glob"])
@pytest.mark.parametrize("first_seen", ["transition", "stale_text"])
def test_automatic_scans_choose_fresh_properties_and_aggregate_spectra(
    tmp_path: Path, monkeypatch, scan_kind: str, first_seen: str,
) -> None:
    import vibeview.import_workflow as workflow
    from vibeview.app.helpers import _scan_gui_directory, _scan_gui_glob

    properties, orbitals, standalone = _calculation(tmp_path)
    standalone.unlink()
    stale = tmp_path / "job.1.property.txt"
    stale.write_text('''$Geometry
   &GeometryIndex 1
   &NAtoms [&Type "Integer"] 1
   &CartesianCoordinates [&Type "Coordinates", &Dim(1,4), &Units "Angstrom"]
     He 1.0 0.0 0.0
$End
''')
    os.utime(stale, (1000, 1000))
    os.utime(properties, (2000, 2000))
    aggregate = tmp_path / "job.1.cis-el.dipole-length.1.mcd"
    transition = tmp_path / "job.1.cis-el.dipole-length.1.0_1.mcd"
    aggregate.write_text("10000 -0.2 0.5\n20000 0.1 0.4\n")
    transition.write_text("10000 -0.2 0.5\n")
    ordered = [transition, stale, orbitals, aggregate, properties]
    if first_seen == "stale_text":
        ordered[0], ordered[1] = ordered[1], ordered[0]

    if scan_kind == "import":
        monkeypatch.setattr(workflow, "_visible_files", lambda *args, **kwargs: iter(ordered))
        candidates, _ = collect_import_inputs([tmp_path])
    elif scan_kind == "glob":
        monkeypatch.setattr(glob, "iglob", lambda *args, **kwargs: map(str, ordered))
        candidates = list(_scan_gui_glob(str(tmp_path / "*"), recursive=False).candidates)
    else:
        original_scandir = os.scandir
        positions = {str(path): index for index, path in enumerate(ordered)}

        @contextlib.contextmanager
        def ordered_scandir(directory):
            with original_scandir(directory) as entries:
                yield iter(sorted(entries, key=lambda entry: positions.get(entry.path, 99)))

        monkeypatch.setattr(os, "scandir", ordered_scandir)
        candidates = list(_scan_gui_directory(tmp_path, recursive=False).candidates)

    assert candidates == [properties]
    with QVFReader(convert_to_qvf(candidates[0])) as reader:
        assert reader.read_structure().atoms[0].position[0] == 0.0
        spectra = [section for section in reader.sections if section.kind == "spectra.generic"]
        assert len(spectra) == 2
        record = reader.provenance["orca_calculation"]
        assert aggregate.name in record["files"]
        assert transition.name in record["skipped_representations"]
        assert stale.name in record["skipped_representations"]

    # Explicit input retains the user's representation/transition choice.
    assert collect_import_inputs([stale])[0] == [stale]
    assert collect_import_inputs([transition])[0] == [transition]


def _file_controller():
    """Exercise file-reader adoption without creating a native render window."""
    from vibeview.app.files import FilesControllers

    state = types.SimpleNamespace(open_path="", open_recursive=False, status_message="")
    readers = []
    viewer = types.SimpleNamespace(
        server=types.SimpleNamespace(state=state),
        _all_readers=readers,
        files=types.SimpleNamespace(
            _activate_file=lambda index: None,
            _apply_auto_open=lambda: None,
            _adopt_reader=readers.append,
        ),
    )
    return FilesControllers(viewer), viewer, state


def test_gui_open_keeps_one_named_dataset_when_another_companion_is_opened(tmp_path: Path) -> None:
    from vibeview.app.helpers import _job_name

    sources = _calculation(tmp_path)
    controller, viewer, state = _file_controller()
    try:
        state.open_path = str(sources[0])
        controller.open_path()
        assert len(viewer._all_readers) == 1
        reader = viewer._all_readers[0]
        assert reader.path is None
        assert reader.source_path == sources[0]
        assert _job_name(reader) == "job.1"
        assert {"structure", "wavefunction.gto", "spectra.generic"} <= {
            section.kind for section in reader.sections
        }

        state.open_path = str(sources[1])
        controller.open_path()
        assert len(viewer._all_readers) == 1
        assert "already open" in state.status_message
    finally:
        for reader in viewer._all_readers:
            reader.close()


@pytest.mark.parametrize("server_filename", [False, True])
def test_gui_json_upload_probes_bytes_and_has_no_server_companions(
    tmp_path: Path, server_filename: bool,
) -> None:
    _, orbitals, _ = _calculation(tmp_path)
    controller, viewer, state = _file_controller()
    state.load_file_name = str(orbitals) if server_filename else "uploaded.json"
    state.load_file_bytes = base64.b64encode(_wavefunction()).decode()
    try:
        controller.load_file_from_bytes()
        assert len(viewer._all_readers) == 1, state.status_message
        reader = viewer._all_readers[0]
        assert reader.path is None
        assert reader.source_path is None
        assert any(section.kind == "wavefunction.gto" for section in reader.sections)
        assert not any(section.kind.startswith("spectra.") for section in reader.sections)
    finally:
        for reader in viewer._all_readers:
            reader.close()


def test_gui_open_reports_the_failing_calculation_reason(tmp_path: Path) -> None:
    source = tmp_path / "broken.property.json"
    source.write_text("invalid json")
    controller, viewer, state = _file_controller()
    state.open_path = str(source)

    controller.open_path()

    assert not viewer._all_readers
    assert "broken.property.json" in state.status_message
    assert "ORCA" in state.status_message
    assert "unreadable" in state.status_message


def test_cli_open_deduplicates_companions_and_closes_one_reader(
    tmp_path: Path, monkeypatch,
) -> None:
    sources = _calculation(tmp_path)
    opened = []
    closed = []
    real_close = QVFReader.close

    def close(reader):
        closed.append(reader)
        real_close(reader)

    def create_app(readers):
        opened.extend(readers)
        return types.SimpleNamespace(state=types.SimpleNamespace())

    monkeypatch.setattr(QVFReader, "close", close)
    monkeypatch.setattr(cli, "_load_app", lambda: (create_app, lambda *args, **kwargs: None))
    monkeypatch.setattr(cli, "_abort_if_port_in_use", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_announce_when_bound", lambda *args, **kwargs: None)

    result = CliRunner().invoke(cli.main, [
        "open", *(str(source) for source in sources), "--no-browser",
        "--log-file", str(tmp_path / "viewer.log"),
    ])

    assert result.exit_code == 0, result.output
    assert len(opened) == 1
    assert opened[0].source_path == sources[0]
    assert closed.count(opened[0]) == 1
