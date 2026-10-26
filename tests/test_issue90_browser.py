"""The browser shows every structure of an archive, none of them by id (#90).

A vibe-sketch document holds one ``structure`` section per species and none
called ``structure``; the viewport stayed empty. The browser now opens on
the first structure (QVF spec § 4.1), shows another one when its sidebar row
is selected, keeps an unsaved edit with the structure it was made on, names
the species by their label and name (§ 5.1), seeds the calculation panel
from the shown structure, and lists ``reaction.scheme`` and ``preview``
sections as details that never reach the 3D scene.
"""

from __future__ import annotations

import pytest

from tests.test_issue90_reader import SCHEME, species_archive
from vibeview.qvf import QVFReader


@pytest.fixture
def plotters(monkeypatch):
    """Every plotter the app creates, closed afterwards."""
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
    """Open archives in a browser app; returns (server, reader, plotter)."""
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


def _atoms_drawn(plotter) -> int:
    return sum(1 for name in plotter.actors if str(name).startswith("atom_"))


def test_a_sketch_document_opens_on_its_first_structure(tmp_path, open_app):
    app, reader, plotter = open_app(species_archive(tmp_path / "sketch.qvf", identity=False))
    assert _atoms_drawn(plotter) == 3
    assert not any("No structure section" in w for w in app.state.qa_warnings)
    titles = {e["id"]: e["title"] for e in app.state.sidebar_entries}
    assert titles["structure_m2"] == "Structure · structure_m2"
    assert app.state.shown_structure_id == "structure_m1"


def test_auto_open_shows_the_first_structure_it_lists(tmp_path, open_app):
    path = species_archive(
        tmp_path / "a.qvf", viewer_defaults={"auto_open": ["structure_m3", "structure_m2"]}
    )
    app, reader, plotter = open_app(path)
    assert reader.shown_structure_id == "structure_m3"
    assert _atoms_drawn(plotter) == 2
    assert (app.state.calc_charge, app.state.calc_multiplicity) == (0, 2)


def test_species_are_listed_by_label_and_name(tmp_path, open_app):
    app, _reader, _plotter = open_app(species_archive(tmp_path / "a.qvf", scheme=SCHEME))
    entries = {e["id"]: e for e in app.state.sidebar_entries}
    assert entries["structure_m1"]["title"] == "1 · water"
    assert entries["structure_m2"]["title"] == "2 · ammonium"
    assert "charge +1" in entries["structure_m2"]["subtitle"]
    assert "multiplicity 2" in entries["structure_m3"]["subtitle"]
    assert entries["scheme"]["title"] == "Proton shuffle"
    assert not entries["scheme"]["disabled"]


def test_selecting_a_structure_shows_it_and_seeds_the_panel(tmp_path, open_app):
    app, reader, plotter = open_app(species_archive(tmp_path / "a.qvf"))
    assert (app.state.calc_charge, app.state.calc_multiplicity) == (0, 1)
    app.controller.activate_section("structure_m2")
    assert reader.shown_structure_id == "structure_m2"
    assert app.state.shown_structure_id == "structure_m2"
    assert _atoms_drawn(plotter) == 5
    assert (app.state.calc_charge, app.state.calc_multiplicity) == (1, 1)
    assert app.state.status_message == "Showing structure 2 · ammonium"
    assert app.state.show_param_panel is True
    assert "₄" in app.state.header_title  # H₄N, the formula of the shown species
    app.controller.activate_section("structure_m3")
    assert _atoms_drawn(plotter) == 2
    assert app.state.calc_multiplicity == 2


def test_an_unsaved_edit_stays_with_its_structure(tmp_path, open_app):
    app, reader, plotter = open_app(species_archive(tmp_path / "a.qvf"))
    state, ctrl = app.state, app.controller
    state.edit_mode = True
    state.edit_selected = [2]
    ctrl.edit_delete_selected()
    assert reader.edited_structure_ids == ["structure_m1"]
    assert state.edit_history
    assert _atoms_drawn(plotter) == 2

    ctrl.activate_section("structure_m2")
    # Atom-indexed edit state ends with the outgoing atoms, as on a file switch.
    assert state.edit_history == [] and state.edit_future == []
    assert state.edit_selected == []
    assert len(reader.read_structure().atoms) == 5
    assert _atoms_drawn(plotter) == 5
    assert "the edits of 1 · water are kept" in state.status_message

    ctrl.activate_section("structure_m1")
    assert len(reader.read_structure().atoms) == 2
    assert _atoms_drawn(plotter) == 2
    assert state.status_message.endswith("(with your unsaved edits)")


def test_the_scheme_panel_lists_steps_by_species(tmp_path, open_app):
    app, _reader, plotter = open_app(species_archive(tmp_path / "a.qvf", scheme=SCHEME))
    before = set(plotter.actors)
    app.controller.activate_section("scheme")
    html = app.state.properties_html
    assert app.state.properties_title == "Proton shuffle"
    assert "2 × ammonium (2) ⇌ water (1)" in html
    assert "water (1) ⇒ hydroxyl (3)" in html  # retrosynthetic: target first
    assert "hydroxyl (3)" in html and "THF; 2 h" in html and "87%" in html
    assert "[NH4+]" in html
    assert set(plotter.actors) == before


def test_the_preview_is_a_picture_never_drawn(tmp_path, open_app):
    app, _reader, plotter = open_app(species_archive(tmp_path / "a.qvf", preview=True))
    before = set(plotter.actors)
    app.controller.activate_section("preview")
    assert "data:image/png;base64," in app.state.properties_html
    assert "image/svg+xml" in app.state.properties_html  # listed, not rendered
    assert "<svg" not in app.state.properties_html
    assert set(plotter.actors) == before


def _reload(app, path, rewrite):
    """Rewrite the watched archive and apply the watcher's event to the app."""
    from vibeview.file_watcher import QVFChangeTracker

    tracker = QVFChangeTracker(path, settle_delay=0.0)
    rewrite()
    tracker.poll()
    event = tracker.poll()
    assert event is not None
    app.controller.apply_watcher_event(event)
    return event


def test_a_hot_reload_keeps_the_structure_on_show(tmp_path, open_app):
    from tests.test_issue90_reader import SPECIES

    path = species_archive(tmp_path / "live.qvf")
    app, _reader, plotter = open_app(path)
    app.controller.activate_section("structure_m2")
    moved = [(sid, [(s, z, [x + 1.0, y, w]) for s, z, (x, y, w) in atoms], ident)
             for sid, atoms, ident in SPECIES]
    event = _reload(app, path, lambda: species_archive(path, species=moved))
    assert "structure_m1" in event.changed_sections  # a full reload, not panel-only
    assert app.state.shown_structure_id == "structure_m2"
    assert _atoms_drawn(plotter) == 5
    assert (app.state.calc_charge, app.state.calc_multiplicity) == (1, 1)


def test_a_panel_only_reload_keeps_every_structures_edit(tmp_path, open_app):
    path = species_archive(tmp_path / "live.qvf", scheme=SCHEME)
    app, _reader, plotter = open_app(path)
    app.state.edit_selected = [2]
    app.controller.edit_delete_selected()
    app.controller.activate_section("structure_m2")
    changed = {"steps": [dict(SCHEME["steps"][0], yield_percent=90, yield_text="90%")]}
    event = _reload(app, path, lambda: species_archive(path, scheme=changed))
    assert list(event.changed_sections) == ["scheme"]
    assert app.state.shown_structure_id == "structure_m2"
    app.controller.activate_section("structure_m1")
    assert app.state.status_message.endswith("(with your unsaved edits)")
    assert _atoms_drawn(plotter) == 2
