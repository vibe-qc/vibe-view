"""Native ORCA preparation and queue contracts; no calculation is launched."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from vibeview.orca_input import OrcaInputSettings, generate_orca_input
from vibeview.qvf import QVFReader


@pytest.fixture
def reader(water_qvf):
    with QVFReader(water_qvf) as result:
        yield result


@pytest.fixture
def viewer(reader):
    from vibeview.app.editing import EditingControllers
    from vibeview.app.queue import QueueControllers

    state = SimpleNamespace(
        calc_engine="ORCA", calc_orca_method="PBE", calc_orca_basis="def2-SVP",
        calc_charge=0, calc_multiplicity=1, calc_template="single_point",
        calc_orca_nprocs="4", calc_orca_maxcore="1200", calc_orca_wall_minutes="30",
        calc_orca_preview="", vq_submit_dialog=True, vq_submitting=False,
        calc_orca_raman=False, calc_orca_vcd=False,
        vq_submit_container=True, vq_submit_live_checkpoint=True,
    )
    result = SimpleNamespace(
        server=SimpleNamespace(state=state), reader=reader,
        exports=SimpleNamespace(_offer_download=Mock()),
        files=SimpleNamespace(_activate_file=Mock(), _apply_auto_open=Mock()),
        _all_readers=[reader],
    )
    result.editing = EditingControllers(result)
    result.queue = QueueControllers(result)
    result.queue.vq_refresh_jobs = Mock()
    return result


@pytest.mark.parametrize("template,keyword", [
    ("single_point", "SP"), ("optimization", "Opt"),
    ("frequencies", "Freq"), ("full", "Opt Freq"),
])
def test_molecular_input_preserves_angstrom_geometry(reader, template, keyword):
    structure = reader.read_structure()
    text = generate_orca_input(structure, OrcaInputSettings(template=template))
    assert f"! PBE def2-SVP TightSCF {keyword}\n" in text
    assert "* xyz 0 1\n" in text
    rows = text.split("* xyz 0 1\n")[1].splitlines()[:-1]
    for atom, row in zip(structure.atoms, rows, strict=True):
        symbol, *coords = row.split()
        assert symbol == atom.symbol
        assert [float(x) for x in coords] == pytest.approx(atom.position, abs=1e-9)


def test_open_shell_charge_and_multiplicity(reader):
    text = generate_orca_input(reader.read_structure(), OrcaInputSettings(
        method="HF", charge=1, multiplicity=2,
    ))
    assert "! HF def2-SVP" in text
    assert "* xyz 1 2\n" in text


@pytest.mark.parametrize("raman,vcd", [(True, False), (False, True), (True, True)])
def test_frequency_spectroscopy_options(reader, raman, vcd):
    text = generate_orca_input(reader.read_structure(), OrcaInputSettings(
        template="full", raman=raman, vcd=vcd,
    ))
    assert ("%elprop Polar 1 end" in text) == raman
    assert ("%freq doVCD true end" in text) == vcd
    assert f"Opt {'AnFreq' if vcd else 'Freq'}\n" in text


@pytest.mark.parametrize("changes,match", [
    ({"charge": 1}, "electron count"),
    ({"multiplicity": 2}, "electron count"),
    ({"multiplicity": 0}, "Multiplicity"),
    ({"charge": 100}, "electron count"),
    ({"charge": 0.5}, "integer"),
    ({"nprocs": 0}, "Processes"),
    ({"nprocs": True}, "integer"),
    ({"maxcore": -1}, "MaxCore"),
    ({"template": "periodic"}, "calculation"),
    ({"method": "PBE\n%pal nprocs 100 end"}, "supported"),
    ({"basis": "def2-svp"}, "supported"),
    ({"raman": True}, "require a frequency"),
    ({"template": "optimization", "vcd": True}, "require a frequency"),
    ({"raman": "false"}, "booleans"),
])
def test_invalid_settings_rejected(reader, changes, match):
    with pytest.raises(ValueError, match=match):
        generate_orca_input(reader.read_structure(), replace(OrcaInputSettings(), **changes))


def test_periodicity_and_nonfinite_coordinates_rejected(reader):
    structure = reader.read_structure()
    with pytest.raises(ValueError, match="isolated molecules"):
        generate_orca_input(
            replace(structure, pbc=(True, False, False), dim=1), OrcaInputSettings()
        )
    structure.atoms[0].position[0] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        generate_orca_input(structure, OrcaInputSettings())


@pytest.mark.parametrize("field,value", [
    ("calc_charge", "0.5"), ("calc_multiplicity", "0"),
    ("calc_orca_nprocs", ""), ("calc_orca_maxcore", "1.1"),
])
def test_form_values_are_not_silently_coerced(viewer, field, value):
    setattr(viewer.server.state, field, value)
    with pytest.raises(ValueError):
        OrcaInputSettings.from_state(viewer.server.state)


def _install_queue(monkeypatch, binary, submit):
    config = ModuleType("vq.config")
    config.load_config = lambda: SimpleNamespace(programs={
        "orca": SimpleNamespace(binary=str(binary)),
    })
    submission = ModuleType("vq.submit")
    submission.submit_local = submit
    monkeypatch.setitem(sys.modules, "vq.config", config)
    monkeypatch.setitem(sys.modules, "vq.submit", submission)


@pytest.mark.parametrize("spectroscopy", [False, True])
def test_save_and_submission_share_edited_geometry_and_resources(
    viewer, tmp_path, monkeypatch, spectroscopy,
):
    if spectroscopy:
        viewer.server.state.calc_template = "full"
        viewer.server.state.calc_orca_raman = True
        viewer.server.state.calc_orca_vcd = True
    binary = tmp_path / "orca"
    binary.write_text("unused test executable")
    binary.chmod(0o700)
    seen = {}

    def submit(*, host, directory, **kwargs):
        seen.update(kwargs, host=host, directory=directory)
        seen["input"] = (Path(directory) / "calculation.inp").read_text()
        assert list(Path(directory).iterdir()) == [Path(directory) / "calculation.inp"]
        return "0123456789abcdef"

    _install_queue(monkeypatch, binary, submit)
    structure = viewer.reader.read_structure()
    positions = [(atom.position + [1.2, 2.3, 3.4]).tolist() for atom in structure.atoms]
    viewer.reader.set_edit_overlay(positions, [atom.symbol for atom in structure.atoms])
    viewer.editing.export_orca_input()
    saved, mime, name = viewer.exports._offer_download.call_args.args
    assert name == "calculation.inp" and mime == "text/plain"
    assert "1.2000000000" in saved.decode()

    viewer.queue.vq_submit_job()

    assert seen["input"] == saved.decode()
    assert ("%elprop Polar 1 end" in saved.decode()) == spectroscopy
    assert ("%freq doVCD true end" in saved.decode()) == spectroscopy
    assert seen["host"] == "localhost"
    assert seen["program"] == "orca"
    assert seen["command"] == [str(binary), "calculation.inp"]
    assert seen["cpus"] == 4 and seen["mem_mb"] == 6400
    assert seen["wall_time_seconds"] == 1800
    assert "%pal nprocs 4 end" in seen["input"]
    assert "%maxcore 1200" in seen["input"]
    assert "python" not in seen and "input_file" not in seen
    assert not Path(seen["directory"]).exists()
    assert not viewer.server.state.vq_submitting
    assert not viewer.server.state.vq_submit_dialog
    assert viewer.server.state.vq_panel_open
    assert "0123456789ab" in viewer.server.state.status_message


def test_missing_queue_never_runs_vibeqc_or_local_subprocess(viewer, monkeypatch):
    monkeypatch.setitem(sys.modules, "vq.config", None)
    popen = Mock(side_effect=AssertionError("must not launch locally"))
    monkeypatch.setattr("subprocess.Popen", popen)
    viewer.queue.vq_submit_job()
    assert "vq" in viewer.server.state.status_message.lower()
    assert "Submitted" not in viewer.server.state.status_message
    assert not viewer.server.state.vq_submitting
    assert viewer.server.state.vq_submit_dialog
    popen.assert_not_called()


def test_queue_rejection_cleans_staged_directory(viewer, monkeypatch, tmp_path):
    binary = tmp_path / "orca"
    binary.touch(mode=0o700)
    seen = []

    def reject(**kwargs):
        seen.append(Path(kwargs["directory"]))
        raise ValueError("not enough resources")

    _install_queue(monkeypatch, binary, reject)
    viewer.queue.vq_submit_job()
    assert seen and not seen[0].exists()
    assert "not enough resources" in viewer.server.state.status_message
    assert not viewer.server.state.vq_submitting
    assert viewer.server.state.vq_submit_dialog


def test_invalid_wall_time_blocks_submission(viewer, monkeypatch):
    submit = Mock(side_effect=AssertionError("must validate first"))
    _install_queue(monkeypatch, "/unused/orca", submit)
    viewer.server.state.calc_orca_wall_minutes = "0"
    viewer.queue.vq_submit_job()
    assert "Wall time" in viewer.server.state.status_message
    submit.assert_not_called()


def test_missing_registered_executable_blocks_submission(viewer, monkeypatch, tmp_path):
    submit = Mock()
    _install_queue(monkeypatch, tmp_path / "missing-orca", submit)
    viewer.queue.vq_submit_job()
    assert "Register" in viewer.server.state.status_message
    submit.assert_not_called()


def test_fetch_opens_one_orca_dataset_with_all_companions(viewer, monkeypatch, tmp_path):
    from tests.test_orca_dataset import _molecule, _properties

    results = tmp_path / "results"
    results.mkdir()
    (results / "calculation.inp").write_text("! HF def2-SVP")
    (results / "calculation.json").write_text(json.dumps(_molecule()))
    (results / "calculation.property.json").write_text(json.dumps(_properties()))
    fetch = ModuleType("vq.fetch")
    fetch.fetch_local = lambda *args: results
    monkeypatch.setitem(sys.modules, "vq.fetch", fetch)
    viewer.server.state.vq_fetch_output_dir = str(tmp_path)
    viewer.queue.open_vq_job("0123456789ab")
    assert len(viewer._all_readers) == 2, viewer.server.state.status_message
    loaded = viewer._all_readers[-1]
    try:
        kinds = {section.kind for section in loaded.sections}
        assert {"structure", "wavefunction.gto"} <= kinds
        assert any(kind.startswith("spectra.") for kind in kinds)
        assert loaded.source_path is not None
        assert "1 dataset(s) loaded" in viewer.server.state.status_message
        viewer.files._activate_file.assert_called_once_with(1)
    finally:
        loaded.close()


def test_failed_fetch_conversion_does_not_activate_old_reader(viewer, monkeypatch, tmp_path):
    (tmp_path / "broken.qvf").write_bytes(b"broken")
    fetch = ModuleType("vq.fetch")
    fetch.fetch_local = lambda *args: tmp_path
    monkeypatch.setitem(sys.modules, "vq.fetch", fetch)
    viewer.server.state.vq_fetch_output_dir = str(tmp_path)
    viewer.queue.open_vq_job("0123456789ab")
    assert "0 dataset(s) loaded" in viewer.server.state.status_message
    assert "1 failed" in viewer.server.state.status_message
    viewer.files._activate_file.assert_not_called()
