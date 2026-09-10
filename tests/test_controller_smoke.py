"""Every controller must at least be callable.

Rationale: 81 of vibe-view's ~113 registered controllers were named in no
test at all, and that gap hid a run of defects that a single call would have
exposed — session save died on ``list(None)``, ``@ctrl.set("save_session")``
was attached to the wrong function, ``save_session``/``load_session`` used
``Path`` without importing it, ``save_user_bookmark`` called a PyVista method
that does not exist. None were subtle; nothing had ever invoked them.

This is deliberately a smoke test, not a behaviour test: it asserts only that
each controller runs without raising. Behaviour lives in the focused tests
(test_bookmarks, test_design_refresh, …). Its job is to make "this code path
has never been executed" impossible to reach again.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

# Zero-argument controllers that are safe to invoke: no network, no long
# render, no writes outside a temp dir, nothing destructive to user data.
SAFE_ZERO_ARG = [
    "compute_bonds", "detect_symmetry", "clear_picks", "show_energy_diagram",
    "show_memory", "show_profile", "reset_element_colors", "toggle_atom_labels",
    "toggle_background", "toggle_dark_ui", "toggle_orthographic",
    "toggle_ssao", "toggle_reduce_detail", "toggle_scf_skip_guess",
    "toggle_scf_log_energy", "toggle_spectra_compare",
    "toggle_compare_mode", "toggle_compare_align", "toggle_color_by_esp",
    "toggle_file_watcher", "toggle_show_slice", "presentation_next",
    "presentation_prev", "close_load_dialog",
    "open_video_export_dialog", "toggle_mo_animation",
]

# (controller, args) for the argument-taking ones, using values the UI sends.
WITH_ARGS = [
    ("set_representation_style", ("space_filling",)),
    ("set_material_preset", ("matte",)),
    ("apply_view_preset", ("publication",)),
    ("update_colormap", ("viridis",)),
    ("update_isovalue", (0.03,)),
    ("update_opacity", (0.5,)),
    ("update_clip", ("x", 0.55)),
    ("update_mo_opacity", (0.8,)),
    ("set_periodic_replication", (1,)),
    ("update_replication", (2, 2, 1)),
    ("set_element_color", (6, "#00FF00")),
    ("use_library_recent", ("benzene",)),
    ("toggle_library_favorite", ("benzene",)),
    ("step_mo", (1,)),
    ("set_compare_highlight", (0,)),
    ("toggle_atom_labels", (True,)),
    ("apply_user_bookmark", ("does-not-exist",)),  # must report, not raise
    ("activate_section", ("structure",)),
    # These take a bool — the UI always sends "[$event]" — so they belong
    # here rather than in the zero-arg list. toggle_toon sat in the
    # zero-arg list while its switch sent an event, so the smoke test
    # passed while every real flip raised TypeError: test with the arity
    # the UI actually uses.
    ("toggle_mo_visibility", (True,)),
    ("toggle_color_by_charge", (False,)),
    ("toggle_toon", (True,)),
    ("open_element_picker", ("new",)),
    ("pick_element", ("N",)),
    # Arg counts here match each binding exactly; a mismatch is a real defect.
    ("vibration_changed", (0, 0.5)),        # UI: [vibration_mode, $event]
    ("update_fermi_bands", ([],)),          # UI: [$event]
]


def _qvf(atoms: list[dict] | None = None) -> Path:
    structure = json.dumps(
        {
            "atoms": atoms
            or [{"symbol": "He", "position": [0, 0, 0], "atomic_number": 2}],
            "pbc": [False, False, False],
        }
    ).encode()
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "smoke"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "s.json",
                        "format": "json",
                        "sha256": hashlib.sha256(structure).hexdigest(),
                    }
                },
            }
        ],
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)  # noqa: SIM115
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", structure)
    return Path(tmp.name)


def _capture_plotters(monkeypatch):
    """Record plotters constructed by one app without scanning GC state."""
    import vibeview.app as appmod

    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    monkeypatch.setattr(appmod.pv, "Plotter", create_plotter)
    return created


def _first_atom_property(plotter):
    name = next(
        name
        for name in plotter.actors
        if str(name).startswith("atom_") and "labels" not in str(name)
    )
    return plotter.actors[name].GetProperty()


@pytest.fixture(scope="module")
def viewer():
    os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    path = _qvf()
    app = create_app(QVFReader(path))
    app.controller.activate_section("structure")
    yield app
    # set_element_color writes a module-level override registry in
    # renderers.structure — global state that would otherwise leak into other
    # test modules and skew their "stock CPK colour" baseline.
    from vibeview.renderers.structure import set_color_overrides

    set_color_overrides(None)
    path.unlink(missing_ok=True)


@pytest.mark.parametrize("name", SAFE_ZERO_ARG)
def test_zero_arg_controller_runs(viewer, name):
    fn = getattr(viewer.controller, name, None)
    assert fn is not None, f"{name} is not registered"
    fn()


@pytest.mark.parametrize(("name", "args"), WITH_ARGS, ids=[c[0] for c in WITH_ARGS])
def test_controller_accepts_ui_arguments(viewer, name, args):
    fn = getattr(viewer.controller, name, None)
    assert fn is not None, f"{name} is not registered"
    fn(*args)


def test_export_py_generates_a_runnable_input_script(viewer):
    """The "Export vibe-qc input (.py)" button must produce a real script.

    It iterated ``sdata.symbols`` / ``sdata.positions``, but StructureData has
    neither — only ``.atoms``, each carrying ``.symbol`` / ``.position`` /
    ``.atomic_number`` — so the button raised AttributeError on every click.
    Fixing that surfaced a second mismatch: generate_input_script documents
    ``atomic_number`` per atom and emits ``Atom(Z, ...)`` from it, so omitting
    it merely moved the failure one step later.
    """
    import base64

    state, ctrl = viewer.state, viewer.controller
    ctrl.activate_section("structure")
    state.export_data = ""
    state.status_message = ""

    ctrl.export_py()

    data = state.export_data or ""
    assert data.startswith("data:"), f"no script produced: {state.status_message}"
    script = base64.b64decode(data.split(",", 1)[1]).decode()
    assert "from vibeqc import" in script
    assert "Atom(" in script, "no atoms emitted"
    assert "mol = Molecule([" in script
    assert "PeriodicSystem(" not in script
    compile(script, "<generated>", "exec")  # must be valid Python


def test_refused_delete_leaves_the_undo_stacks_untouched(viewer):
    """Refusing to delete every atom must not disturb undo/redo.

    edit_delete_selected pushed the undo entry and cleared the redo stack
    *before* checking whether the deletion was legal, so selecting all atoms
    and pressing delete refused the delete but still left a no-op undo entry
    behind and threw the redo stack away.
    """
    state, ctrl = viewer.state, viewer.controller
    ctrl.activate_section("structure")
    state.edit_mode = True
    state.edit_history = []
    state.edit_future = [{"positions": [], "symbols": []}]

    # the fixture holds a single atom, so selecting it is "delete everything"
    state.edit_selected = [0]
    ctrl.edit_delete_selected()

    assert "Cannot delete all atoms" in state.status_message
    assert state.edit_history == [], "refused delete still pushed an undo entry"
    assert len(state.edit_future) == 1, "refused delete discarded the redo stack"


def test_edit_paths_mark_the_undo_stacks_dirty():
    """Every edit path must mark ``edit_history``/``edit_future`` dirty.

    Both stacks are mutated in place (``append`` / ``pop`` / ``clear``). Trame's
    pushed snapshot aliases the very same list object, so the post-push
    comparison finds them equal and the client keeps its stale copy — the Undo
    and Redo buttons bind to ``edit_history.length === 0`` /
    ``edit_future.length === 0`` and would stay disabled after an edit.

    Two call sites already carried an explicit ``state.dirty(...)`` for exactly
    this reason; the remaining eight did not. This pins all of them.

    ``state.dirty = ...`` cannot be monkeypatched — trame's ``__setattr__``
    would create a *state key* named "dirty" instead of replacing the method —
    so the class attribute is patched instead.
    """
    os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    path = _qvf(
        [
            {"symbol": "O", "position": [0.0, 0.0, 0.0], "atomic_number": 8},
            {"symbol": "H", "position": [0.96, 0.0, 0.0], "atomic_number": 1},
            {"symbol": "H", "position": [-0.24, 0.93, 0.0], "atomic_number": 1},
        ]
    )
    app = create_app(QVFReader(path))
    state, ctrl = app.state, app.controller
    ctrl.activate_section("structure")

    marked: list[str] = []
    cls = type(state)
    real = cls.dirty
    cls.dirty = lambda self, *keys: (marked.extend(keys), real(self, *keys))[1]
    try:
        state.edit_mode = True
        for name, call, setup in (
            ("delete", ctrl.edit_delete_selected, lambda: setattr(state, "edit_selected", [2])),
            ("undo", ctrl.edit_undo, lambda: None),
            ("redo", ctrl.edit_redo, lambda: None),
        ):
            setup()
            marked.clear()
            call()
            assert {"edit_history", "edit_future"} <= set(marked), (
                f"{name} mutated the stacks in place without marking them dirty; "
                "the Undo/Redo buttons will not update client-side"
            )
    finally:
        cls.dirty = real
        path.unlink(missing_ok=True)


def test_video_export_clears_the_spinner_and_flushes():
    """Finishing a video export must turn the spinner off *on the client*.

    The export dialog is ``persistent`` on ``video_export_running`` and shows a
    spinner / disables its buttons while it is true. The old code ran the render
    in a bare ``threading.Thread`` and set ``video_export_running = False`` from
    inside it — but a plain thread's state writes are never pushed to the client
    (see ``_live_opt_on_done``: "nothing else pushes to the client"), so the
    dialog stayed open with the spinner turning forever and no status appeared,
    even though the file had been written.

    The render is stubbed so the test needs no ffmpeg; the point is the
    lifecycle, not the encode. ``flush`` is patched on the class because
    assigning to ``state.flush`` would just create a state key named "flush".
    """
    import asyncio

    os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
    import vibeview.animation as anim
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    fake = Path(tempfile.mkdtemp()) / "movie.mp4"
    fake.write_bytes(b"x")
    orig_render = anim.render_turntable
    anim.render_turntable = lambda *a, **k: fake

    path = _qvf()
    app = create_app(QVFReader(path))
    state, ctrl = app.state, app.controller

    flushed: list[int] = []
    cls = type(state)
    real_flush = cls.flush
    cls.flush = lambda self: (flushed.append(1), real_flush(self))[1]

    async def drive() -> None:
        state.video_export_kind = "turntable"
        state.video_export_format = "mp4"
        ctrl.do_video_export()
        assert state.video_export_running is True, "spinner should be on during export"
        for _ in range(100):
            await asyncio.sleep(0.02)
            if not state.video_export_running:
                break

    try:
        asyncio.new_event_loop().run_until_complete(drive())
        assert state.video_export_running is False, "spinner never cleared"
        assert "movie.mp4" in state.status_message, f"no status: {state.status_message!r}"
        assert flushed, "finalize never flushed — client would not see the result"
    finally:
        cls.flush = real_flush
        anim.render_turntable = orig_render
        path.unlink(missing_ok=True)


class TestContextMenuTargeting:
    """Right-click actions act on the spot under the cursor.

    The DOM contextmenu event only carries screen coordinates, which the
    server cannot unproject (the client owns the camera in VtkLocalView), so
    for a long time the menu was untargeted: the JS hardcoded
    ``context_menu_atom_idx = -1``, which made 'Select Atom' (gated on
    ``idx >= 0``) unreachable, and 'Add Atom Here' was a stub that just told
    the user to click elsewhere. The chain now runs off vtk.js hover picks
    (debounced client-side to fire when the pointer pauses — which it does
    right before a right-click): ``on_hover`` remembers the last world
    position server-side, and ``context_menu_opened`` snapshots it for the
    menu actions.
    """

    def _app(self):
        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        path = _qvf(
            [
                {"symbol": "O", "position": [0.0, 0.0, 0.0], "atomic_number": 8},
                {"symbol": "H", "position": [0.96, 0.0, 0.0], "atomic_number": 1},
                {"symbol": "H", "position": [-0.24, 0.93, 0.0], "atomic_number": 1},
            ]
        )
        reader = QVFReader(path)
        app = create_app(reader)
        app.controller.activate_section("structure")
        return app, reader, path

    def test_hover_drives_the_tooltip(self):
        app, _reader, path = self._app()
        try:
            state, ctrl = app.state, app.controller
            ctrl.on_hover({"worldPosition": [0.1, 0.05, 0.0]})
            assert state.hover_tooltip_visible is True
            assert state.hover_tooltip == "O · atom 0"
            ctrl.on_hover({"worldPosition": [8.0, 8.0, 8.0]})
            assert state.hover_tooltip_visible is False, "tooltip stuck on"
        finally:
            path.unlink(missing_ok=True)

    def test_right_click_over_an_atom_targets_it(self):
        app, _reader, path = self._app()
        try:
            state, ctrl = app.state, app.controller
            ctrl.on_hover({"worldPosition": [0.9, 0.05, 0.0]})  # near H, idx 1
            ctrl.context_menu_opened()
            assert state.context_menu_atom_idx == 1

            state.edit_mode = True
            state.edit_selected = []
            ctrl.context_select_atom()
            assert state.edit_selected == [1]

            # 'Add Atom Here' on top of an atom is refused, like on_pick
            ctrl.context_add_atom()
            assert "Too close" in state.status_message
        finally:
            path.unlink(missing_ok=True)

    def test_add_atom_here_places_and_is_undoable(self):
        app, reader, path = self._app()
        try:
            state, ctrl = app.state, app.controller
            state.edit_mode = True
            state.edit_history = []
            state.edit_future = []
            state.edit_new_element = "N"

            ctrl.on_hover({"worldPosition": [3.0, 0.0, 0.0]})  # empty space
            ctrl.context_menu_opened()
            assert state.context_menu_atom_idx == -1

            ctrl.context_add_atom()
            assert len(reader.read_structure().atoms) == 4
            assert "Added N atom" in state.status_message
            assert len(state.edit_history) == 1, "add must be undoable"

            ctrl.edit_undo()
            assert len(reader.read_structure().atoms) == 3
        finally:
            path.unlink(missing_ok=True)

    def test_add_atom_without_edit_mode_or_target_reports(self):
        app, _reader, path = self._app()
        try:
            state, ctrl = app.state, app.controller
            state.edit_mode = False
            ctrl.context_add_atom()
            assert "Enable edit mode" in state.status_message

            # No hover has happened yet on this app, so the menu opens
            # untargeted (the freshness window treats the initial timestamp
            # as stale) and Add Atom explains instead of guessing.
            state.edit_mode = True
            ctrl.context_menu_opened()
            assert state.context_menu_atom_idx == -1
            ctrl.context_add_atom()
            assert "right-click" in state.status_message
        finally:
            path.unlink(missing_ok=True)


class TestBuildToolControllers:
    """add_hydrogens / insert_fragment / build_supercell survive being used.

    All three had the undo-push right but had never been executed against a
    real scene. Driving them surfaced a crash family: coincident atoms
    (add_hydrogens double-placing a vertex; every FRAGMENTS entry putting its
    base atom exactly ON the anchor) fed a zero-length bond into _auto_bond,
    whose Cylinder(direction=0) raised ValueError('matrix must have finite
    values') and killed the whole scene rebuild.
    """

    def _app(self):
        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        path = _qvf(
            [
                {"symbol": "C", "position": [0.0, 0.0, 0.0], "atomic_number": 6},
                {"symbol": "H", "position": [1.09, 0.0, 0.0], "atomic_number": 1},
            ]
        )
        reader = QVFReader(path)
        app = create_app(reader)
        app.controller.activate_section("structure")
        app.state.edit_mode = True
        app.state.edit_history = []
        app.state.edit_future = []
        return app, reader, path

    def test_add_hydrogens_saturates_and_is_undoable(self):
        app, reader, path = self._app()
        try:
            state, ctrl = app.state, app.controller
            ctrl.add_hydrogens()  # crashed before the placement fix
            assert len(reader.read_structure().atoms) == 5
            assert "Added hydrogens" in state.status_message
            ctrl.edit_undo()
            assert len(reader.read_structure().atoms) == 2
        finally:
            path.unlink(missing_ok=True)

    def test_insert_fragment_on_selected_atom_does_not_crash(self):
        """Anchored insert was a guaranteed crash: every fragment's base atom
        sits at the relative origin, so it landed exactly on the anchor."""
        app, reader, path = self._app()
        try:
            state, ctrl = app.state, app.controller
            state.edit_selected = [0]
            ctrl.insert_fragment("CH3")
            assert "Inserted CH3" in state.status_message
            assert len(reader.read_structure().atoms) == 6  # 2 + C + 3H

            # unknown fragment reports instead of raising
            ctrl.insert_fragment("no-such-fragment")
            assert "Unknown fragment" in state.status_message
        finally:
            path.unlink(missing_ok=True)

    def test_build_supercell_replicates_and_is_undoable(self):
        app, reader, path = self._app()
        try:
            state, ctrl = app.state, app.controller
            state.build_supercell_nx = 2
            state.build_supercell_ny = 1
            state.build_supercell_nz = 1
            ctrl.build_supercell()
            assert len(reader.read_structure().atoms) == 4
            assert "Supercell 2x1x1" in state.status_message
            ctrl.edit_undo()
            assert len(reader.read_structure().atoms) == 2
        finally:
            path.unlink(missing_ok=True)


def test_toon_switch_applies_and_restores_shading():
    """The Toon switch must actually change the scene, both ways.

    Two stacked defects made it a no-op: the switch sends
    ``update_modelValue=(ctrl.toggle_toon, "[$event]")`` but the controller
    took zero arguments, so every flip raised TypeError before doing
    anything; and had it run, it flipped ``toon_mode`` that ``v_model``
    had already written, cancelling the toggle. Additionally, disabling
    blanket-reset every actor to Phong, though bonds and axes are not
    Phong by default — enable now saves each actor's shading and disable
    restores it.
    """
    import gc

    import pyvista as pv

    os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    path = _qvf(
        [
            {"symbol": "O", "position": [0.0, 0.0, 0.0], "atomic_number": 8},
            {"symbol": "H", "position": [0.96, 0.0, 0.0], "atomic_number": 1},
        ]
    )
    # Earlier tests in this module leave their apps' plotters alive, so
    # "any Plotter in gc" grabs a stale one — take the plotter that is NEW
    # after this create_app.
    seen = {id(o) for o in gc.get_objects() if isinstance(o, pv.Plotter)}
    app = create_app(QVFReader(path))
    state, ctrl = app.state, app.controller
    ctrl.activate_section("structure")
    plotter = next(
        o
        for o in gc.get_objects()
        if isinstance(o, pv.Plotter) and id(o) not in seen
    )

    def snapshot():
        out = {}
        for name, actor in plotter.actors.items():
            prop = actor.GetProperty() if hasattr(actor, "GetProperty") else None
            if prop is not None:
                out[name] = (prop.GetInterpolation(), int(prop.GetEdgeVisibility()))
        return out

    try:
        before = snapshot()
        state.toon_mode = True  # what v_model writes before the event fires
        ctrl.toggle_toon(True)  # the switch's actual call — used to raise
        assert state.toon_mode is True, "set-don't-flip: v_model value must stand"
        toon = snapshot()
        assert any(v != before.get(k) for k, v in toon.items()), (
            "toon changed no actor property"
        )
        assert all(v[0] == 0 for v in toon.values()), "flat shading not applied"

        state.toon_mode = False
        ctrl.toggle_toon(False)
        assert snapshot() == before, "disable must restore pre-toon shading exactly"
    finally:
        path.unlink(missing_ok=True)


def test_initial_scene_realizes_declared_material_preset(monkeypatch):
    """The default material state must describe the actors actually shown."""
    from vibeview.app import create_app
    from vibeview.material_presets import get_preset
    from vibeview.qvf import QVFReader

    path = _qvf()
    reader = QVFReader(path)
    created = _capture_plotters(monkeypatch)
    try:
        app = create_app(reader)
        assert len(created) == 1
        plotter = created[0]
        preset = get_preset("cpk_glossy")
        prop = _first_atom_property(plotter)

        assert app.state.material_preset == "cpk_glossy"
        assert plotter.renderer.GetBackground() == pytest.approx(
            preset.background_color, abs=1 / 255
        )
        assert prop.GetSpecular() == pytest.approx(preset.atom_specular)
        assert prop.GetSpecularPower() == pytest.approx(preset.atom_specular_power)
    finally:
        if created:
            created[0].close()
        reader.close()
        path.unlink(missing_ok=True)


def test_scene_rebuild_preserves_material_and_toon_appearance(monkeypatch):
    """Replacement actors must realize the still-active appearance state."""
    from vibeview.app import create_app
    from vibeview.material_presets import get_preset
    from vibeview.qvf import QVFReader

    path = _qvf(
        [
            {"symbol": "O", "position": [0.0, 0.0, 0.0], "atomic_number": 8},
            {"symbol": "H", "position": [0.96, 0.0, 0.0], "atomic_number": 1},
        ]
    )
    reader = QVFReader(path)
    created = _capture_plotters(monkeypatch)
    try:
        app = create_app(reader)
        plotter = created[0]
        state, ctrl = app.state, app.controller

        ctrl.set_material_preset("matte")
        bond_interpolation = next(
            actor.GetProperty().GetInterpolation()
            for name, actor in plotter.actors.items()
            if str(name).startswith("bond")
        )
        ctrl.toggle_toon(True)
        actor_before = next(
            actor
            for name, actor in plotter.actors.items()
            if str(name).startswith("atom_") and "labels" not in str(name)
        )

        ctrl.toggle_atom_labels(True)

        preset = get_preset("matte")
        prop = _first_atom_property(plotter)
        actor_after = next(
            actor
            for name, actor in plotter.actors.items()
            if str(name).startswith("atom_") and "labels" not in str(name)
        )
        assert actor_after is not actor_before
        assert state.material_preset == "matte"
        assert state.toon_mode is True
        assert plotter.renderer.GetBackground() == pytest.approx(
            preset.background_color, abs=1 / 255
        )
        assert prop.GetSpecular() == pytest.approx(0.1)
        assert prop.GetSpecularPower() == pytest.approx(10.0)
        assert prop.GetInterpolation() == 0
        assert prop.GetEdgeVisibility() == 1
        grid_prop = next(
            actor.GetProperty()
            for actor in plotter.actors.values()
            if type(actor).__name__ == "CubeAxesActor"
        )
        assert grid_prop.GetInterpolation() == 0

        # Changing the material while toon is active must refresh the saved
        # pre-toon edge state. After another rebuild, disabling the explicit
        # toon mode should reveal the material preset's own outline.
        ctrl.set_material_preset("toon")
        ctrl.toggle_atom_labels(False)
        ctrl.toggle_toon(False)
        prop = _first_atom_property(plotter)
        bond_prop = next(
            actor.GetProperty()
            for name, actor in plotter.actors.items()
            if str(name).startswith("bond")
        )
        current_grid_prop = next(
            actor.GetProperty()
            for actor in plotter.actors.values()
            if type(actor).__name__ == "CubeAxesActor"
        )
        assert prop.GetInterpolation() == 2
        assert prop.GetEdgeVisibility() == 1
        assert bond_prop.GetInterpolation() == bond_interpolation
        assert current_grid_prop.GetInterpolation() == 1
    finally:
        if created:
            created[0].close()
        reader.close()
        path.unlink(missing_ok=True)


def test_wavefunction_controllers_record_and_replay_surface_recipe(
    tables_qvf, monkeypatch
):
    """Only a successful explicit render may become the rebuild recipe."""
    import pyvista as pv

    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(tables_qvf)
    created = _capture_plotters(monkeypatch)
    calls = []
    try:
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        plotter = created[0]
        ctrl.activate_section("wf")

        monkeypatch.setattr(
            appmod,
            "_render_mo_volume",
            lambda _r, _p, _v, _s, _section, index, spin, **_kwargs: calls.append(
                ("mo", index, spin)
            ),
        )
        monkeypatch.setattr(
            appmod,
            "_render_wf_density",
            lambda _r, _p, _v, current, _section, **_kwargs: calls.append(
                ("density", current.wf_density_spin)
            ),
        )
        monkeypatch.setattr(
            appmod,
            "_render_wf_elf",
            lambda *_args, **_kwargs: calls.append(("elf",)),
        )
        monkeypatch.setattr(
            appmod,
            "_render_wf_nci",
            lambda *_args, **_kwargs: calls.append(("nci",)),
        )
        monkeypatch.setattr(
            appmod,
            "_render_wf_laplacian",
            lambda *_args, **_kwargs: calls.append(("laplacian",)),
        )

        ctrl.render_mo("restricted:0")
        assert state.wf_surface_kind == "mo"
        assert state.mo_visible is True
        ctrl.compute_wf_density(False)
        assert state.wf_surface_kind == "density"
        ctrl.compute_wf_density(True)
        assert state.wf_surface_kind == "spin_density"
        ctrl.compute_wf_elf()
        assert state.wf_surface_kind == "elf"
        ctrl.compute_wf_nci()
        assert state.wf_surface_kind == "nci"
        ctrl.compute_wf_laplacian()
        assert state.wf_surface_kind == "laplacian"
        assert calls == [
            ("mo", 0, "restricted"),
            ("density", False),
            ("density", True),
            ("elf",),
            ("nci",),
            ("laplacian",),
        ]

        state.wf_surface_kind = "elf"

        def fail_mo(*_args):
            plotter.add_mesh(pv.Sphere(), name="mo_iso_partial")
            raise RuntimeError("render failed")

        monkeypatch.setattr(appmod, "_render_mo_volume", fail_mo)
        ctrl.render_mo("restricted:0")
        assert state.wf_surface_kind == "elf"
        assert state.mo_visible is False
        assert not any(str(name).startswith("mo_iso_") for name in plotter.actors)

        state.wf_density_spin = False

        def fail_density(*_args, **_kwargs):
            plotter.add_mesh(pv.Sphere(), name="mo_iso_partial")
            raise RuntimeError("density failed")

        monkeypatch.setattr(appmod, "_render_wf_density", fail_density)
        ctrl.compute_wf_density(True)
        assert state.wf_surface_kind == "elf"
        assert state.wf_density_spin is False
        assert state.mo_visible is False
        assert not any(str(name).startswith("mo_iso_") for name in plotter.actors)

        replayed = []
        state.wf_surface_kind = "nci"
        state.mo_last_index = None
        state.mo_visible = False
        monkeypatch.setattr(
            appmod,
            "_replay_wavefunction_surface",
            lambda *_args: replayed.append("nci") or True,
            raising=False,
        )
        ctrl.toggle_mo_visibility(True)
        assert state.mo_visible is True
        assert replayed == ["nci"]

        def fail_rebuild_replay(*_args):
            plotter.add_mesh(pv.Sphere(), name="mo_iso_partial")
            raise RuntimeError("replay failed")

        monkeypatch.setattr(
            appmod, "_replay_wavefunction_surface", fail_rebuild_replay
        )
        ctrl.update_replication(1, 1, 1)
        assert state.status_message == (
            "Computed surface rebuild error: replay failed"
        )
        assert state.mo_visible is False
        assert not any(str(name).startswith("mo_iso_") for name in plotter.actors)
    finally:
        if created:
            created[0].close()
        reader.close()


def test_real_mo_surface_survives_scene_rebuild(tables_qvf, monkeypatch):
    """The shared recipe must recreate real actors, not only dispatch a call."""
    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(tables_qvf)
    created = _capture_plotters(monkeypatch)
    try:
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        plotter = created[0]
        ctrl.activate_section("wf")
        state.wf_n_per_dim = 18
        state.isovalue = 0.05
        ctrl.render_mo("restricted:0")
        before = {
            name: actor
            for name, actor in plotter.actors.items()
            if str(name).startswith("mo_iso_")
        }
        assert before

        ctrl.toggle_atom_labels(True)

        after = {
            name: actor
            for name, actor in plotter.actors.items()
            if str(name).startswith("mo_iso_")
        }
        assert after.keys() == before.keys()
        assert all(after[name] is not before[name] for name in after)
        assert state.wf_surface_kind == "mo"
        assert state.mo_visible is True

        ctrl.set_material_preset("matte")
        ctrl.toggle_toon(True)
        expected_appearance = {
            str(name): (
                actor.GetProperty().GetSpecular(),
                actor.GetProperty().GetInterpolation(),
                actor.GetProperty().GetEdgeVisibility(),
            )
            for name, actor in plotter.actors.items()
            if str(name).startswith("mo_iso_")
        }
        ctrl.toggle_mo_visibility(False)
        ctrl.toggle_mo_visibility(True)
        replayed_appearance = {
            str(name): (
                actor.GetProperty().GetSpecular(),
                actor.GetProperty().GetInterpolation(),
                actor.GetProperty().GetEdgeVisibility(),
            )
            for name, actor in plotter.actors.items()
            if str(name).startswith("mo_iso_")
        }
        assert expected_appearance
        assert replayed_appearance == expected_appearance

        pushed_surface_state = []
        monkeypatch.setattr(
            appmod,
            "_push_view",
            lambda current: pushed_surface_state.append(
                any(str(name).startswith("mo_iso_") for name in current.actors)
            ),
        )
        ctrl.activate_section("vib0")
        assert state.wf_surface_kind is None
        assert state.mo_visible is False
        assert not any(str(name).startswith("mo_iso_") for name in plotter.actors)
        assert pushed_surface_state and pushed_surface_state[-1] is False

        ctrl.activate_section("wf")
        assert state.wf_surface_kind is None
        assert not any(str(name).startswith("mo_iso_") for name in plotter.actors)
    finally:
        if created:
            created[0].close()
        reader.close()


def test_volume_activation_does_not_duplicate_viewport_push(
    showcase_qvf, monkeypatch
):
    """A mesh activator's push must satisfy the section-cleanup boundary."""
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(showcase_qvf)
    created = _capture_plotters(monkeypatch)
    try:
        app = create_app(reader)
        plotter = created[0]
        updates = []
        plotter._vibe_view_update = lambda: updates.append("push")

        app.controller.activate_section("vol_dens_0")

        assert updates == ["push"]
    finally:
        if created:
            created[0].close()
        reader.close()


@pytest.mark.parametrize(
    ("controller_action", "renderer_name"),
    [
        ("toggle_mo_animation", "_render_mo_volume"),
        ("compute_wf_density", "_render_wf_density"),
    ],
)
def test_section_switch_invalidates_inflight_computed_surface(
    tables_qvf, monkeypatch, controller_action, renderer_name
):
    """A computed render finishing after reset must never resurrect state."""
    import threading
    import time

    import pyvista as pv

    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(tables_qvf)
    created = _capture_plotters(monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    failures = []
    calls = []
    worker = None
    switch_worker = None
    try:
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        plotter = created[0]
        ctrl.activate_section("wf")
        state.wf_anim_speed = 0.01

        def blocking_render(*_args, **_kwargs):
            calls.append("render")
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test render was not released")
            plotter.add_mesh(pv.Sphere(), name="mo_iso_stale")
            return "stale animation frame"

        monkeypatch.setattr(appmod, renderer_name, blocking_render)

        def run_surface() -> None:
            try:
                getattr(ctrl, controller_action)()
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        worker = threading.Thread(target=run_surface, daemon=True)
        worker.start()
        assert entered.wait(timeout=5)

        switch_done = threading.Event()

        def switch_section() -> None:
            try:
                ctrl.activate_section("vib0")
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)
            finally:
                switch_done.set()

        switch_worker = threading.Thread(target=switch_section, daemon=True)
        switch_worker.start()
        # Invalidation is immediate, but VTK mutation must wait for the worker
        # render to leave the shared render lock.
        assert not switch_done.wait(timeout=0.05)
        assert state.wf_animating is False

        release.set()
        worker.join(timeout=5)
        switch_worker.join(timeout=5)
        assert not worker.is_alive()
        assert not switch_worker.is_alive()
        time.sleep(0.05)

        assert failures == []
        assert calls == ["render"]
        assert state.wf_section_id is None
        assert state.wf_surface_kind is None
        assert state.mo_visible is False
        assert state.wf_animating is False
        assert state.status_message.startswith("Loaded vibrations: vib0")
        assert not any(str(name).startswith("mo_iso_") for name in plotter.actors)
    finally:
        release.set()
        if worker is not None:
            worker.join(timeout=5)
        if switch_worker is not None:
            switch_worker.join(timeout=5)
        if created:
            created[0].close()
        reader.close()


def test_panel_reload_serializes_reader_swap_with_computed_surface(
    tables_qvf, monkeypatch
):
    """A light watcher refresh must not swap readers inside a render commit."""
    import threading
    import types

    import pyvista as pv

    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(tables_qvf)
    created = _capture_plotters(monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    failures = []
    render_worker = None
    watcher_worker = None
    try:
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        plotter = created[0]
        ctrl.activate_section("wf")
        pushes = []
        plotter._vibe_view_update = lambda: pushes.append(
            tuple(
                str(name)
                for name in plotter.actors
                if str(name).startswith("mo_iso_")
            )
        )

        def blocking_render(*_args, **_kwargs):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test render was not released")
            plotter.add_mesh(pv.Sphere(), name="mo_iso_current")
            return "render completed"

        monkeypatch.setattr(appmod, "_render_mo_volume", blocking_render)

        def render_surface() -> None:
            try:
                ctrl.render_mo("restricted:0")
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        render_worker = threading.Thread(target=render_surface, daemon=True)
        render_worker.start()
        assert entered.wait(timeout=5)

        event = types.SimpleNamespace(
            path=str(reader.path),
            is_qvf=True,
            added_sections=(),
            removed_sections=(),
            changed_sections=(),
            touched_sections=(),
        )
        watcher_done = threading.Event()

        def refresh_reader() -> None:
            try:
                ctrl.apply_watcher_event(event)
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)
            finally:
                watcher_done.set()

        watcher_worker = threading.Thread(target=refresh_reader, daemon=True)
        watcher_worker.start()
        assert not watcher_done.wait(timeout=0.05), (
            "reader swap escaped the computed-surface render lock"
        )

        release.set()
        render_worker.join(timeout=5)
        watcher_worker.join(timeout=5)
        assert not render_worker.is_alive()
        assert not watcher_worker.is_alive()
        assert failures == []
        assert state.wf_section_id == "wf"
        assert state.wf_surface_kind == "mo"
        assert state.mo_visible is True
        assert "mo_iso_current" in plotter.actors
        assert pushes == [("mo_iso_current",)]
    finally:
        release.set()
        if render_worker is not None:
            render_worker.join(timeout=5)
        if watcher_worker is not None:
            watcher_worker.join(timeout=5)
        if created:
            created[0].close()
        reader.close()


def test_stopping_inflight_animation_restores_committed_surface(
    tables_qvf, monkeypatch
):
    """Cancel after actor removal must not leave visible recipe actorless."""
    import threading

    import pyvista as pv

    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(tables_qvf)
    created = _capture_plotters(monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    failures = []
    calls = []
    animation_worker = None
    stop_worker = None
    try:
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        plotter = created[0]
        ctrl.activate_section("wf")
        state.wf_surface_kind = "mo"
        state.mo_last_index = 0
        state.mo_last_spin = "restricted"
        state.mo_visible = True
        state.volume_loaded = True
        plotter.add_mesh(pv.Sphere(), name="mo_iso_committed")

        def blocking_then_restore(*_args, **_kwargs):
            calls.append("render")
            if len(calls) == 1:
                appmod._remove_actors_by_prefix(plotter, "mo_iso_")
                entered.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("test render was not released")
                plotter.add_mesh(pv.Sphere(), name="mo_iso_canceled_frame")
                return "canceled animation frame"
            plotter.add_mesh(pv.Sphere(), name="mo_iso_restored")
            return "restored committed surface"

        monkeypatch.setattr(appmod, "_render_mo_volume", blocking_then_restore)

        def start_animation() -> None:
            try:
                ctrl.toggle_mo_animation()
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        animation_worker = threading.Thread(target=start_animation, daemon=True)
        animation_worker.start()
        assert entered.wait(timeout=5)

        stopped = threading.Event()

        def stop_animation() -> None:
            try:
                ctrl.toggle_mo_animation()
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)
            finally:
                stopped.set()

        stop_worker = threading.Thread(target=stop_animation, daemon=True)
        stop_worker.start()
        assert not stopped.wait(timeout=0.05), (
            "stop returned before the in-flight actor mutation settled"
        )

        release.set()
        animation_worker.join(timeout=5)
        stop_worker.join(timeout=5)
        assert not animation_worker.is_alive()
        assert not stop_worker.is_alive()
        assert failures == []
        assert calls == ["render", "render"]
        assert state.wf_animating is False
        assert state.wf_surface_kind == "mo"
        assert state.mo_last_index == 0
        assert state.mo_visible is True
        assert state.status_message == "MO animation stopped"
        assert "mo_iso_restored" in plotter.actors
        assert "mo_iso_canceled_frame" not in plotter.actors
    finally:
        release.set()
        if animation_worker is not None:
            animation_worker.join(timeout=5)
        if stop_worker is not None:
            stop_worker.join(timeout=5)
        if created:
            created[0].close()
        reader.close()


def test_animation_step_cannot_advance_new_wavefunction_picker(
    tables_qvf, monkeypatch
):
    """An old timer's epoch check and picker step form one atomic action."""
    import threading

    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(tables_qvf)
    reader.set_wavefunction_overlay("wf_second", reader.read_wavefunction_gto("wf"))
    created = _capture_plotters(monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    failures = []
    animation_worker = None
    switch_worker = None
    try:
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        ctrl.activate_section("wf_second")
        expected_default = state.wf_selected_mo
        ctrl.activate_section("wf")

        real_step = appmod._step_mo_selection

        def blocking_step(current_state, direction):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test picker step was not released")
            return real_step(current_state, direction)

        monkeypatch.setattr(appmod, "_step_mo_selection", blocking_step)
        monkeypatch.setattr(
            appmod,
            "_render_mo_volume",
            lambda *_args, **_kwargs: "animation frame",
        )

        def start_animation() -> None:
            try:
                ctrl.toggle_mo_animation()
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)

        animation_worker = threading.Thread(target=start_animation, daemon=True)
        animation_worker.start()
        assert entered.wait(timeout=5)

        switched = threading.Event()

        def switch_wavefunction() -> None:
            try:
                ctrl.activate_section("wf_second")
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)
            finally:
                switched.set()

        switch_worker = threading.Thread(target=switch_wavefunction, daemon=True)
        switch_worker.start()
        assert not switched.wait(timeout=0.05), (
            "section invalidation entered between timer epoch check and picker step"
        )

        release.set()
        animation_worker.join(timeout=5)
        switch_worker.join(timeout=5)
        assert not animation_worker.is_alive()
        assert not switch_worker.is_alive()
        assert failures == []
        assert state.wf_section_id == "wf_second"
        assert state.wf_selected_mo == expected_default
        assert state.wf_animating is False
    finally:
        release.set()
        if animation_worker is not None:
            animation_worker.join(timeout=5)
        if switch_worker is not None:
            switch_worker.join(timeout=5)
        if created:
            created[0].close()
        reader.close()


def test_panel_reload_preserves_in_memory_wavefunction_overlay(
    tables_qvf, monkeypatch
):
    """A 2D-only archive refresh must retain session re-localization data."""
    import types

    import pyvista as pv

    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(tables_qvf)
    overlay_id = "wf_relocalized_test"
    overlay = reader.read_wavefunction_gto("wf")
    reader.set_wavefunction_overlay(overlay_id, overlay)
    created = _capture_plotters(monkeypatch)
    calls = []
    try:
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        plotter = created[0]
        ctrl.activate_section(overlay_id)

        event = types.SimpleNamespace(
            path=str(reader.path),
            is_qvf=True,
            added_sections=(),
            removed_sections=(),
            changed_sections=(),
            touched_sections=(),
        )
        ctrl.apply_watcher_event(event)

        def render_transferred(active_reader, current_plotter, *_args, **_kwargs):
            calls.append(active_reader.read_wavefunction_gto(overlay_id))
            current_plotter.add_mesh(pv.Sphere(), name="mo_iso_transferred")
            return "rendered transferred overlay"

        monkeypatch.setattr(appmod, "_render_mo_volume", render_transferred)
        ctrl.render_mo("restricted:0")

        assert len(calls) == 1
        assert calls[0] is overlay
        assert state.wf_section_id == overlay_id
        assert state.wf_surface_kind == "mo"
        assert state.mo_visible is True
        assert "mo_iso_transferred" in plotter.actors
        assert any(entry["id"] == overlay_id for entry in state.sidebar_entries)
    finally:
        if created:
            created[0].close()
        reader.close()


class TestPeriodicTablePicker:
    """The periodic-table element picker (replaces the two dropdowns)."""

    def test_layout_covers_h_through_cm_without_collisions(self):
        from vibeview.app import _periodic_table_cells

        cells = _periodic_table_cells()
        assert len(cells) == 96
        by_symbol = {sym: (row, col) for _z, sym, row, col in cells}
        # canonical anchors of the 18-column layout
        assert by_symbol["H"] == (1, 1)
        assert by_symbol["He"] == (1, 18)
        assert by_symbol["B"] == (2, 13)
        assert by_symbol["La"] == (9, 3)   # detached f-block rows
        assert by_symbol["Hf"] == (6, 4)
        assert by_symbol["Cm"] == (10, 10)
        positions = {(row, col) for _z, _s, row, col in cells}
        assert len(positions) == len(cells), "two elements share a grid cell"

    def test_pick_flows_new_and_change(self):
        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        path = _qvf(
            [
                {"symbol": "C", "position": [0.0, 0.0, 0.0], "atomic_number": 6},
                {"symbol": "H", "position": [1.09, 0.0, 0.0], "atomic_number": 1},
            ]
        )
        reader = QVFReader(path)
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        ctrl.activate_section("structure")
        try:
            # 'new' target sets the element used for added atoms
            ctrl.open_element_picker("new")
            assert state.element_picker_open is True
            ctrl.pick_element("N")
            assert state.edit_new_element == "N"
            assert state.element_picker_open is False

            # 'change' target routes through edit_change_element (undoable)
            state.edit_mode = True
            state.edit_selected = [1]
            state.edit_history = []
            ctrl.open_element_picker(["change"])  # UI sends ['change']
            ctrl.pick_element(["O"])              # UI sends ['O']
            assert [a.symbol for a in reader.read_structure().atoms] == ["C", "O"]
            assert len(state.edit_history) == 1, "change must be undoable"
            ctrl.edit_undo()
            assert [a.symbol for a in reader.read_structure().atoms] == ["C", "H"]

            # no selection: honest message, nothing mutated
            state.edit_selected = []
            ctrl.open_element_picker("change")
            ctrl.pick_element("F")
            assert "Select atoms" in state.status_message
        finally:
            path.unlink(missing_ok=True)


def test_palette_open_section_and_export_actions():
    """Ctrl/Cmd+K covers sections and export formats, not just view toggles.

    The palette's action list used to be purely static; it now carries one
    'Open section' entry per section of the loaded file (rebuilt on file
    reload) and the export formats of the toolbar Export menu. The 'open:'
    ids dispatch through activate_section, everything else through the
    same handlers the menus use.
    """
    import asyncio

    os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
    # export handlers schedule a flag-reset coroutine; give the bare test
    # process the event loop the real app always has
    asyncio.set_event_loop(asyncio.new_event_loop())
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    path = _qvf()
    app = create_app(QVFReader(path))
    state, ctrl = app.state, app.controller
    try:
        titles = {a["id"]: a["title"] for a in state.palette_actions}
        assert "open:structure" in titles, "per-section palette entry missing"
        assert "exp_xyz" in titles and "exp_py" in titles

        state.palette_open = True
        ctrl.run_palette_action("open:structure")
        assert state.selected_section == "structure"
        assert state.palette_open is False, "palette must close after dispatch"

        state.export_data = ""
        ctrl.run_palette_action(["exp_xyz"])  # UI sends [a.id]
        assert state.export_data, "export action produced no download"
    finally:
        path.unlink(missing_ok=True)


def test_edit_symmetrize_controller_round_trip():
    """The Symmetrize button projects the geometry, is undoable, and
    refuses honestly on an asymmetric molecule."""
    os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
    import numpy as np

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    path = _qvf(
        [
            {"symbol": "O", "position": [0.01, 0.02, 0.13], "atomic_number": 8},
            {"symbol": "H", "position": [0.77, -0.02, -0.47], "atomic_number": 1},
            {"symbol": "H", "position": [-0.75, 0.01, -0.49], "atomic_number": 1},
        ]
    )
    reader = QVFReader(path)
    app = create_app(reader)
    state, ctrl = app.state, app.controller
    ctrl.activate_section("structure")
    try:
        state.edit_history = []
        state.edit_future = []
        before = np.array([a.position for a in reader.read_structure().atoms])

        ctrl.edit_symmetrize()
        assert "Symmetrized" in state.status_message, state.status_message
        assert "C2v" in state.status_message
        after = np.array([a.position for a in reader.read_structure().atoms])
        assert not np.allclose(before, after), "geometry unchanged"
        d = [np.linalg.norm(after[0] - after[1]), np.linalg.norm(after[0] - after[2])]
        assert abs(d[0] - d[1]) < 1e-9, "not exactly C2v after symmetrize"
        assert len(state.edit_history) == 1, "must be undoable"

        ctrl.edit_undo()
        restored = np.array([a.position for a in reader.read_structure().atoms])
        assert np.allclose(restored, before, atol=1e-12)
    finally:
        path.unlink(missing_ok=True)
