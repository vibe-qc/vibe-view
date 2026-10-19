"""A section that names a structure is drawn over that structure (#106).

QVF spec § 4.1: a section's ``structure_ref`` is the structure it describes.
In an archive with several structures, activating a volume whose reference
names a structure other than the shown one used to draw it over the wrong
geometry; the shown structure never changed. Now it is shown first, through
the same path a structure selection takes (#90), so an unsaved edit, the
undo history and the status line behave as on any structure switch. A
section that names no structure makes no statement and leaves the shown
structure alone.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests._qvf_corpus import qvf_corpus_dir, requires_qvf_corpus
from tests.test_issue90_browser import _atoms_drawn
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder, structure_payload

WATER = [
    {"symbol": "O", "position": [0.0, 0.0, 0.117], "atomic_number": 8},
    {"symbol": "H", "position": [0.0, 0.757, -0.469], "atomic_number": 1},
    {"symbol": "H", "position": [0.0, -0.757, -0.469], "atomic_number": 1},
]
AMMONIA = [
    {"symbol": "N", "position": [0.0, 0.0, 0.0], "atomic_number": 7},
    {"symbol": "H", "position": [0.0, 0.94, 0.38], "atomic_number": 1},
    {"symbol": "H", "position": [0.81, -0.47, 0.38], "atomic_number": 1},
    {"symbol": "H", "position": [-0.81, -0.47, 0.38], "atomic_number": 1},
]
MOLECULE = (False, False, False)


@pytest.fixture
def plotters(monkeypatch):
    """Every plotter the app creates, closed afterwards (as in the #90 tests)."""
    import vibeview.app.helpers as helpers

    made = []
    create = helpers.new_plotter

    def record(*args, **kwargs):
        plotter = create(*args, **kwargs)
        made.append(plotter)
        return plotter

    monkeypatch.setattr(helpers, "new_plotter", record)
    yield made
    for plotter in made:
        plotter.close()


@pytest.fixture
def open_app(plotters):
    """Open an archive in a browser app; returns (server, reader, plotter)."""
    readers: list[QVFReader] = []

    def _open(path):
        from vibeview.app import create_app

        reader = QVFReader(path)
        readers.append(reader)
        server = create_app([reader])
        server.controller.view_update = lambda *a, **k: None
        return server, reader, plotters[-1]

    yield _open
    for reader in readers:
        reader.close()


def _grid() -> dict:
    return {
        "origin": [-3.0, -3.0, -3.0],
        "voxel_vectors": [[0.75, 0, 0], [0, 0.75, 0], [0, 0, 0.75]],
        "shape": [8, 8, 8],
    }


def two_structure_archive(path):
    """``h2o`` first, ``nh3`` second; ``rho_nh3`` names ``nh3``, ``rho_h2o``
    names nothing, and ``rho_bad`` names a section that is not a structure."""
    builder = QVFArchiveBuilder(program="test", version="0", calculation="two")
    builder.add_json("h2o", "structure", "structure", structure_payload(WATER, MOLECULE))
    builder.add_json("nh3", "structure", "structure", structure_payload(AMMONIA, MOLECULE))
    data = np.full((8, 8, 8), 0.01, dtype=np.float32)
    data[4, 4, 4] = 1.0
    for section_id, extra in (
        ("rho_nh3", {"structure_ref": "nh3"}),
        ("rho_h2o", {}),
        ("rho_bad", {"structure_ref": "rho_nh3"}),
    ):
        builder.add_json(section_id, "volume.density", "grid", _grid(), section_extra=extra)
        builder.add_binary(section_id, "volume.density", "data", data)
    return builder.write(path)


# ── the reader ─────────────────────────────────────────────────────────────


def test_named_structure_ref_states_only_what_the_section_names(tmp_path):
    with QVFReader(two_structure_archive(tmp_path / "two.qvf")) as reader:
        assert reader.named_structure_ref("rho_nh3") == "nh3"
        assert reader.named_structure_ref("rho_h2o") is None
        assert reader.named_structure_ref("rho_bad") is None  # not a structure
        assert reader.named_structure_ref("nh3") == "nh3"  # a structure names itself
        # The reading default keeps its fallback to the first structure.
        assert reader.structure_ref_of("rho_h2o") == "h2o"


# ── the browser app ────────────────────────────────────────────────────────


def test_a_section_naming_another_structure_shows_that_structure_first(tmp_path, open_app):
    app, reader, plotter = open_app(two_structure_archive(tmp_path / "two.qvf"))
    assert reader.shown_structure_id == "h2o"
    assert _atoms_drawn(plotter) == 3

    app.controller.activate_section("rho_nh3")

    assert reader.shown_structure_id == "nh3"
    assert app.state.shown_structure_id == "nh3"
    assert _atoms_drawn(plotter) == 4
    assert "H₃N" in app.state.header_title
    assert app.state.selected_section == "rho_nh3"
    assert app.state.status_message == "Showing structure nh3 — Loaded volume: rho_nh3"


def test_a_section_naming_no_structure_leaves_the_shown_one_alone(tmp_path, open_app):
    app, reader, plotter = open_app(two_structure_archive(tmp_path / "two.qvf"))
    app.controller.activate_section("nh3")
    assert reader.shown_structure_id == "nh3"

    app.controller.activate_section("rho_h2o")  # makes no statement (spec § 4.1)

    assert reader.shown_structure_id == "nh3"
    assert _atoms_drawn(plotter) == 4
    assert app.state.status_message == "Loaded volume: rho_h2o"


def test_a_section_naming_the_shown_structure_does_not_switch(tmp_path, open_app):
    app, reader, plotter = open_app(two_structure_archive(tmp_path / "two.qvf"))
    app.controller.activate_section("nh3")
    before = app.state.status_message

    app.controller.activate_section("rho_nh3")

    assert reader.shown_structure_id == "nh3"
    assert app.state.status_message == "Loaded volume: rho_nh3"
    assert app.state.status_message != before


def test_a_reference_to_a_non_structure_is_ignored(tmp_path, open_app):
    app, reader, plotter = open_app(two_structure_archive(tmp_path / "two.qvf"))
    app.controller.activate_section("rho_bad")
    assert reader.shown_structure_id == "h2o"
    assert _atoms_drawn(plotter) == 3


def test_the_switch_keeps_an_unsaved_edit_with_its_structure(tmp_path, open_app):
    """The reference switch is the #90 structure switch, with its edit rules."""
    app, reader, plotter = open_app(two_structure_archive(tmp_path / "two.qvf"))
    state, ctrl = app.state, app.controller
    state.edit_mode = True
    state.edit_selected = [2]
    ctrl.edit_delete_selected()
    assert reader.edited_structure_ids == ["h2o"]
    assert state.edit_history

    ctrl.activate_section("rho_nh3")

    assert reader.shown_structure_id == "nh3"
    assert state.edit_history == [] and state.edit_future == []
    assert reader.edited_structure_ids == ["h2o"]
    assert "the edits of h2o are kept" in state.status_message
    ctrl.activate_section("h2o")
    assert len(reader.read_structure().atoms) == 2  # the edit is still there


@requires_qvf_corpus("multi_structure.qvf")
def test_the_corpus_wavefunction_is_drawn_over_its_own_structure(open_app):
    """The reported case: ``wf_nh3`` names ``nh3`` while ``h2o`` is first."""
    corpus = qvf_corpus_dir()
    assert corpus is not None
    app, reader, plotter = open_app(corpus / "multi_structure.qvf")
    assert reader.shown_structure_id == "h2o"

    app.controller.activate_section("wf_nh3")

    assert reader.shown_structure_id == "nh3"
    assert "H₃N" in app.state.header_title
    assert app.state.status_message.startswith("Showing structure nh3 — Loaded")

    app.controller.activate_section("rho_h2o")  # names h2o: back to water
    assert reader.shown_structure_id == "h2o"
    assert "H₂O" in app.state.header_title
