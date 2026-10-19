"""Bookmarks, slides and sessions restore the structure that was on show (#107).

In an archive with several structures, a saved view recorded only the
selected section. Once a section that names no structure is selected over
the second structure (a normal state since #106), restoring the view landed
on the first structure. Saved views now carry ``shown_structure`` and restore
it first, through the structure-switch path, before the saved section.
Views from before the field restore as they did.
"""

from __future__ import annotations

import json

import pytest

from tests.test_issue106_structure_ref import _atoms_drawn, two_structure_archive
from vibeview.qvf import QVFReader


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


def _on_ammonia_with_a_plain_volume(app):
    """nh3 on show, with the volume that names no structure selected."""
    app.controller.activate_section("nh3")
    app.controller.activate_section("rho_h2o")
    assert app.state.shown_structure_id == "nh3"
    assert app.state.selected_section == "rho_h2o"


def test_a_session_restores_the_structure_that_was_on_show(tmp_path, open_app):
    archive = two_structure_archive(tmp_path / "two.qvf")
    app, _reader, _plotter = open_app(archive)
    _on_ammonia_with_a_plain_volume(app)
    path = tmp_path / "s.vibe-view-session.json"
    app.controller.save_session(str(path))
    saved = json.loads(path.read_text())
    assert saved["shown_structure"] == "nh3"
    assert saved["active_section"] == "rho_h2o"

    fresh, reader, plotter = open_app(archive)
    assert reader.shown_structure_id == "h2o"
    fresh.controller.load_session(str(path))

    assert reader.shown_structure_id == "nh3"
    assert fresh.state.shown_structure_id == "nh3"
    assert fresh.state.selected_section == "rho_h2o"
    assert _atoms_drawn(plotter) == 4
    assert "H₃N" in fresh.state.header_title
    assert fresh.state.status_message.startswith("Session loaded")


def test_a_bookmark_restores_the_structure_that_was_on_show(tmp_path, open_app):
    app, reader, plotter = open_app(two_structure_archive(tmp_path / "two.qvf"))
    _on_ammonia_with_a_plain_volume(app)
    app.controller.save_user_bookmark("on ammonia")
    assert app.state.user_bookmarks[0]["shown_structure"] == "nh3"
    app.controller.activate_section("h2o")
    assert reader.shown_structure_id == "h2o"

    app.controller.apply_user_bookmark("on ammonia")

    assert reader.shown_structure_id == "nh3"
    assert app.state.selected_section == "rho_h2o"
    assert _atoms_drawn(plotter) == 4
    assert app.state.status_message == "Restored: on ammonia"


def test_slides_restore_each_bookmarks_structure(tmp_path, open_app):
    app, reader, _plotter = open_app(two_structure_archive(tmp_path / "two.qvf"))
    _on_ammonia_with_a_plain_volume(app)
    app.controller.save_user_bookmark("ammonia")
    app.controller.activate_section("h2o")
    app.controller.save_user_bookmark("water")

    app.controller.toggle_presentation()  # opens on the first slide
    try:
        assert app.state.presentation_mode is True
        assert reader.shown_structure_id == "nh3"
        app.controller.presentation_next()
        assert reader.shown_structure_id == "h2o"
        app.controller.presentation_prev()
        assert reader.shown_structure_id == "nh3"
    finally:
        app.controller.toggle_presentation()


def _session_file(tmp_path, **fields) -> str:
    path = tmp_path / "s.vibe-view-session.json"
    path.write_text(json.dumps({"version": 1, "user_bookmarks": [], "library_favorites": [],
                                **fields}))
    return str(path)


def test_an_older_session_without_the_field_restores_as_before(tmp_path, open_app):
    app, reader, _plotter = open_app(two_structure_archive(tmp_path / "two.qvf"))
    app.controller.load_session(_session_file(tmp_path, active_section="rho_h2o"))
    assert reader.shown_structure_id == "h2o"
    assert app.state.selected_section == "rho_h2o"


@pytest.mark.parametrize("shown", ["no_such_section", "rho_nh3", 7, None])
def test_a_saved_id_that_is_not_a_structure_changes_nothing(tmp_path, open_app, shown):
    app, reader, _plotter = open_app(two_structure_archive(tmp_path / "two.qvf"))
    app.controller.load_session(
        _session_file(tmp_path, active_section="rho_h2o", shown_structure=shown)
    )
    assert reader.shown_structure_id == "h2o"
    assert app.state.status_message.startswith("Session loaded")


def test_the_saved_sections_own_reference_still_wins(tmp_path, open_app):
    """A saved section that names a structure (#106) is drawn over that one."""
    app, reader, _plotter = open_app(two_structure_archive(tmp_path / "two.qvf"))
    app.controller.load_session(
        _session_file(tmp_path, active_section="rho_nh3", shown_structure="h2o")
    )
    assert reader.shown_structure_id == "nh3"
    assert app.state.selected_section == "rho_nh3"


def test_the_switch_on_restore_keeps_an_unsaved_edit_with_its_structure(tmp_path, open_app):
    app, reader, _plotter = open_app(two_structure_archive(tmp_path / "two.qvf"))
    state, ctrl = app.state, app.controller
    _on_ammonia_with_a_plain_volume(app)
    ctrl.save_user_bookmark("on ammonia")
    ctrl.activate_section("h2o")
    state.edit_mode = True
    state.edit_selected = [2]
    ctrl.edit_delete_selected()
    assert reader.edited_structure_ids == ["h2o"]

    ctrl.apply_user_bookmark("on ammonia")

    assert reader.shown_structure_id == "nh3"
    assert reader.edited_structure_ids == ["h2o"]
    assert state.edit_history == []
    ctrl.activate_section("h2o")
    assert len(reader.read_structure().atoms) == 2
