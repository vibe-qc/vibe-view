"""Opaque origin preservation and atomic geometry-return transport (#136)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibeview.geometry_edit import geometry_archive
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import structure_archive
from vibeview.sketch_return import origin_bytes, record_relaxation, send_geometry

ORIGIN = b'{ "schema": "vibe-sketch-origin/1", "document": "drawing", "atoms": ["a0"] }'


def source(*, origin: bool = True) -> QVFReader:
    builder = structure_archive(
        [{"symbol": "O", "atomic_number": 8, "position": [0, 0, 0]}],
        [False, False, False], bonds=[], charge=-1, multiplicity=2,
    )
    builder.manifest["sections"][0].update(charge=-1, multiplicity=2, smiles="[O-]")
    if origin:
        builder.add_json_bytes("origin", "x_vibesketch.origin", "origin", ORIGIN)
    return QVFReader(builder.to_bytesio())


def test_save_keeps_origin_bytes_graph_metadata_and_edited_positions() -> None:
    with source() as reader:
        structure = reader.read_structure()
        structure.atoms[0].position[0] = 1.25
        saved = geometry_archive(structure, provenance=reader.provenance, source_reader=reader)
    with QVFReader(saved) as result:
        assert origin_bytes(result) == ORIGIN
        assert result.read_structure().atoms[0].position[0] == 1.25
        assert result.read_structure().bonds == []
        section = next(s for s in result.sections if s.kind == "structure")
        assert section.model_extra == {"charge": -1, "multiplicity": 2, "smiles": "[O-]"}
        assert result.provenance["x_vibeview"] == {"edits": "geometry", "relaxation": None}


def test_return_publishes_complete_archive_then_record(tmp_path: Path) -> None:
    with source() as reader:
        target = send_geometry(reader, tmp_path)
    record = json.loads(target.with_suffix(".json").read_text())
    assert record == {"schema": "vibe-sketch-view-return/1", "path": str(target)}
    assert not list(tmp_path.glob("*.tmp"))
    with QVFReader(target) as returned:
        assert origin_bytes(returned) == ORIGIN


def test_missing_peer_is_not_recreated(tmp_path: Path) -> None:
    missing = tmp_path / "gone"
    with source() as reader, pytest.raises(ValueError, match="Save geometry"):
        send_geometry(reader, missing)
    assert not missing.exists()


def test_no_origin_refuses_without_writing(tmp_path: Path) -> None:
    with source(origin=False) as reader, pytest.raises(ValueError, match="no vibe-sketch origin"):
        send_geometry(reader, tmp_path)
    assert not list(tmp_path.iterdir())


def test_relaxation_records_actual_events(tmp_path: Path) -> None:
    with source() as reader:
        record_relaxation(reader, {"engine": "example", "step": 4, "converged": False})
        record_relaxation(reader, {"steps": 4, "converged": True})
        target = send_geometry(reader, tmp_path)
    with QVFReader(target) as returned:
        assert returned.provenance["x_vibeview"]["relaxation"] == {
            "engine": "example", "step": 4, "steps": 4, "converged": True,
        }


def test_browser_return_and_missing_directory_message(tmp_path: Path) -> None:
    from vibeview.app import create_app

    with source() as reader:
        app = create_app(reader, return_dir=tmp_path)
        assert app.state.sketch_return_available
        app.controller.send_to_sketch()
        assert len(list(tmp_path.glob("*.json"))) == 1
        assert "sent back" in app.state.status_message
        for item in tmp_path.iterdir():
            item.unlink()
        tmp_path.rmdir()
        app.controller.send_to_sketch()
        assert "Save geometry" in app.state.status_message


@pytest.mark.parametrize("options", [{}, {"view": "mode", "mode": 7},
                                    {"view": "orbitals", "orbital": "HOMO"}])
def test_optional_result_handoff_fields(tmp_path: Path, options: dict[str, object]) -> None:
    from vibeview.sketch import read_handoff_view

    record = tmp_path / "record.json"
    record.write_text(json.dumps({"schema": "vibe-view-sketch-handoff/1", **options}))
    assert read_handoff_view(record) == options


@pytest.mark.parametrize("options", [{"mode": 0}, {"mode": True}, {"view": "unknown"},
                                    {"orbital": ""}, {"orbital": 2}])
def test_invalid_result_handoff_refuses(tmp_path: Path, options: dict[str, object]) -> None:
    from vibeview.sketch import SketchHandoffError, read_handoff_view

    record = tmp_path / "record.json"
    record.write_text(json.dumps(options))
    with pytest.raises(SketchHandoffError):
        read_handoff_view(record)


def test_received_result_handoff_activates_the_requested_mode() -> None:
    from types import SimpleNamespace

    from vibeview.app import create_app

    water = Path(__file__).parent.parent / "docs/examples/orca-water/water-frequencies.qvf"
    with source() as reader:
        app = create_app(reader)
        viewer = app.controller.send_to_sketch.func.__self__.viewer
        viewer._sketch_session = SimpleNamespace(
            receive=lambda: [water], view_options={water: {"view": "mode", "mode": 7}},
            running=True,
        )
        viewer.files.poll_sketch()
        assert app.state.selected_section == "orca_vibrations"
        assert app.state.vibration_mode == 6
        viewer._sketch_session = None
