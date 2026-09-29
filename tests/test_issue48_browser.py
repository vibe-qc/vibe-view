"""Regressions for the browser-viewer rows in #48.

Each test names the #37 finding it pins. #48 lands in slices; this file
grows with them.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests._qvf_builders import build_qvf
from tests._served import served_viewer

_ROOT = Path(__file__).resolve().parent.parent
_DOCS = _ROOT / "docs"
_H2 = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"


@pytest.fixture
def viewer(tmp_path):
    """A fresh app per test: create_app rebinds trame's shared server, so an
    app kept across tests would be driven through another test's controllers.
    The archive is a copy, because a geometry export writes its file next to
    ``reader.path`` and must not land in tests/data."""
    import shutil

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(shutil.copy(_H2, tmp_path))
    try:
        yield create_app(reader)
    finally:
        reader.close()


def _doc(name: str) -> str:
    return (_DOCS / name).read_text(encoding="utf-8")


# ── #37-56 to #37-66: the manual describes the controls that exist ────────


def test_isosurface_toggle_is_documented_where_it_is():
    """#37-56: "Show isosurface" hides the computed wavefunction surface; it
    is not one of the Isosurface Controls."""
    browser = _doc("browser.md")
    volume = browser[browser.index("### `volume.*` and `basis.ao`") :]
    volume = volume[: volume.index("\n### ", 5)]
    assert "**Show isosurface**" not in volume
    wavefunction = browser[browser.index("### `wavefunction.gto`") :]
    assert "**Show isosurface** hides the most recently computed surface" in wavefunction
    served = served_viewer()
    tag = served[served.rindex("<", 0, served.index('label="Show isosurface"')) :]
    assert 'v-if="wf_surface_kind !== null"' in tag[: tag.index(">")]


def test_display_card_and_settings_are_described_correctly():
    """#37-57 / #37-62: the colour override is in the Display card; the
    background default is in Settings and the switch is on the toolbar."""
    browser = _doc("browser.md")
    assert "(in Settings)" not in browser
    assert "**Element colour override**, also in the Display card" in browser
    assert "**Orthographic projection**, **Dark background**" not in browser
    assert "*Dark background (default)*" in browser
    served = served_viewer()
    assert 'label="Dark background (default)"' in served
    assert 'title="Toggle dark/light background"' in served


def test_clip_plane_description_matches_the_clip():
    """#37-58: three simultaneous cuts, isosurface only."""
    import pyvista as pv

    from vibeview.app import _clip_to_planes

    browser = _doc("browser.md")
    assert "along X, Y or Z" not in browser
    assert "atoms and isosurfaces alike" not in browser
    assert "atoms and bonds stay whole" in browser
    cube = pv.ImageData(dimensions=(11, 11, 11), spacing=(0.1, 0.1, 0.1))
    cube = cube.cast_to_unstructured_grid()
    kept = _clip_to_planes(cube, 0.5, 0.5, 0.5, cube.bounds)
    # The high side of all three planes: one octant of the cube.
    assert kept.bounds[0] == pytest.approx(0.5) and kept.bounds[2] == pytest.approx(0.5)
    assert kept.bounds[4] == pytest.approx(0.5)


def test_session_file_name_and_video_options_match_the_code(viewer, tmp_path):
    """#37-59 / #37-60."""
    browser = _doc("browser.md")
    assert "`<name>.vibe-view-session.json` beside" in browser
    assert "frame rate and quality" not in browser
    served = served_viewer()
    assert 'label="Frame rate"' in served and "quality" not in served.lower().split(
        "export video"
    )[1][:3000]


def test_qtaim_is_not_described_as_clickable():
    """#37-61: on_pick handles measure, edit and residue picking only."""
    browser = _doc("browser.md")
    assert "click a point for its" not in browser
    assert "Clicking a point in the viewport does not select it" in browser


def test_editing_and_walkthrough_claims():
    """#37-63 / #37-64 / #37-65."""
    editing = _doc("editing.md")
    assert "at the molecule's centre when nothing is selected" not in editing
    assert "one\nbond length (1.5 Å) out from it" in editing
    assert "XYZ, CIF, CML or a Python input" in editing
    walkthrough = _doc("walkthrough.md")
    assert "caches it for subsequent" not in walkthrough
    assert "selecting the HOMO\nbookmark evaluates the HOMO again" in walkthrough
    served = served_viewer()
    assert "trigger__5', ['json']" not in served  # no JSON export exists


def test_launch_flags_are_described_as_they_behave():
    """#37-66."""
    browser = _doc("browser.md")
    assert "Open this section of the first file on startup" in browser
    assert "With one file it does nothing." in browser


# ── #37-42: dead code ─────────────────────────────────────────────────────


def test_dead_controllers_state_and_helpers_are_gone(viewer):
    import vibeview.app as app

    for name in ("file_picker_value", "show_param_panel_for_structure", "set_calc_method"):
        assert not getattr(viewer.controller, name).exists(), name
    for key in ("file_picker_value", "lazy_loading", "vq_jobs_dialog", "vq_submit_input"):
        assert not viewer.state.has(key), key
    assert not hasattr(app, "_status_color")
    served = served_viewer()
    assert "vq_submit_input" not in served and ".py input path" not in served
    monitor = served[served.rindex("<", 0, served.index("Live monitor (all states")) :]
    assert "change" not in monitor[: monitor.index(">")]


def test_switching_the_vq_monitor_on_refreshes_at_once(monkeypatch):
    """#37-42: the switch's `change=` never fired; the watcher now refreshes
    as soon as the monitor starts instead of after the first 5 s sleep. With
    vq masked, a refresh is visible as the missing-queue message."""
    import asyncio
    import sys

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    monkeypatch.setitem(sys.modules, "vq", None)
    monkeypatch.setitem(sys.modules, "vq.listing", None)
    reader = QVFReader(_H2)
    seen: list[str] = []

    def set_and_fire(state, **values):
        # A headless server is never "ready", so flush() skips watchers; run
        # them as a client change would (test_browser_app_lifecycle does too).
        for key, value in values.items():
            setattr(state, key, value)
        for key in values:
            for func, _translator in list(state._change_callbacks.get(key, [])):
                func(**{key: getattr(state, key)})

    async def drive():
        server = create_app(reader)
        server.state.vq_jobs_error = ""
        set_and_fire(server.state, vq_panel_open=True, vq_monitor_active=True)
        for _ in range(50):  # well under the poll's 5 s sleep
            await asyncio.sleep(0.01)
            if server.state.vq_jobs_error:
                break
        seen.append(server.state.vq_jobs_error)
        set_and_fire(server.state, vq_monitor_active=False)
        await asyncio.sleep(0)

    try:
        asyncio.run(drive())
    finally:
        reader.close()
    assert seen and "vibe-queue" in seen[0], seen


# ── #37-38 / #37-43 ───────────────────────────────────────────────────────


def test_one_title_map():
    """#37-38: the spectra panel's title comes from _KIND_TITLES."""
    from types import SimpleNamespace

    import vibeview.app as app

    for kind, title in app._KIND_TITLES.items():
        section = SimpleNamespace(kind=kind, label=None)
        assert app._section_title(section) == title
    assert app._section_title(SimpleNamespace(kind="spectra.nmr", label="Mine")) == "Mine"


def test_structure_section_is_identified_by_kind(tmp_path):
    """#37-43: a structure section with another id still opens the
    calculation-parameter panel."""
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    payload = json.dumps(
        {"atoms": [{"symbol": "He", "atomic_number": 2, "position": [0, 0, 0]}],
         "pbc": [False, False, False]}
    ).encode()
    path = build_qvf(
        tmp_path / "geometry.qvf",
        [("geometry", "structure", {"structure": ("json", "geometry/structure.json", payload)})],
    )
    reader = QVFReader(path)
    try:
        server = create_app(reader)
        server.state.show_param_panel = False
        server.controller.activate_section("geometry")
        assert server.state.show_param_panel is True
    finally:
        reader.close()


# ── #37-52: transparent screenshots without a per-pixel loop ──────────────


def test_white_to_transparent_matches_the_old_loop():
    from PIL import Image

    from vibeview.app import _white_to_transparent

    rng = np.random.default_rng(7)
    pixels = rng.integers(240, 256, size=(40, 30, 3), dtype=np.uint8)
    image = Image.fromarray(pixels)
    expected = []
    for item in np.array(image.convert("RGBA")).reshape(-1, 4).tolist():
        item = tuple(item)
        near_white = item[0] > 250 and item[1] > 250 and item[2] > 250
        expected.append((255, 255, 255, 0) if near_white else item)
    result = _white_to_transparent(image)
    assert result.mode == "RGBA"
    assert [tuple(p) for p in np.array(result).reshape(-1, 4).tolist()] == expected


# ── #37-R1 / #37-26 / #37-27 / #37-35 / #37-49 / #37-53: the section registry ─


def test_every_supported_kind_resolves_to_one_handler():
    """#37-R1: one table resolves a kind; there is no second dispatch."""
    from vibeview import app, kinds

    missing = sorted(k for k in kinds.SUPPORTED_KINDS if app._section_handler(k) is None)
    assert not missing, f"supported kinds that open nothing: {missing}"
    assert app._section_handler("volume.rdg") is app._SECTION_HANDLERS["volume.*"]
    assert app._section_handler("basis.ao") is app._SECTION_HANDLERS["volume.*"]
    assert app._section_handler("spectra.ir") is app._SECTION_HANDLERS["spectra.*"]
    assert app._section_handler("x-vendor.thing") is None
    # Only the kinds that restyle or replace the atoms skip the CPK restore,
    # and only animations and reciprocal space keep the structure hidden.
    no_cpk = {k for k, h in app._SECTION_HANDLERS.items() if not h.restores_cpk}
    assert no_cpk == {"atom_properties", "fermi_surface"}
    hidden = {k for k, h in app._SECTION_HANDLERS.items() if h.keeps_structure_hidden}
    assert hidden == {"trajectory", "reaction.path", "vibrations", "fermi_surface"}


def test_simple_panels_come_from_one_table():
    """#37-35: the panel activators are rows of _PANEL_SPECS."""
    from vibeview import app

    for name in ("_activate_citations", "_activate_nmr", "_activate_epr", "_activate_symmetry",
                 "_activate_job_spec", "_activate_bond_orders", "_activate_dos_coop",
                 "_activate_phonon_bands", "_activate_phonon_dos", "_activate_eos"):
        assert not hasattr(app, name), name
    assert set(app._PANEL_SPECS) <= set(app._SECTION_HANDLERS)
    assert set(app._PANEL_SPECS) <= app._two_d_panel_kinds()


def _showcase_app(showcase_qvf):
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(showcase_qvf)
    return reader, create_app(reader)


def test_leaving_atom_properties_restores_the_palette_once(showcase_qvf, monkeypatch):
    """#37-27 / #37-53: one CPK restore on leaving atom_properties, for a 2D
    panel and for the structure alike."""
    import vibeview.app as app

    reader, server = _showcase_app(showcase_qvf)
    calls: list[str] = []
    real = app._restore_cpk_atoms
    monkeypatch.setattr(
        app, "_restore_cpk_atoms", lambda *a, **k: (calls.append("cpk"), real(*a, **k))
    )
    try:
        for target in ("citations", "structure"):
            server.controller.activate_section("atom_props")
            assert server.state.atom_properties_active
            calls.clear()
            server.controller.activate_section(target)
            assert calls == ["cpk"], (target, calls)
            assert server.state.selected_section == target
    finally:
        reader.close()


def test_switching_between_2d_panels_does_not_reserialize_the_scene(showcase_qvf, monkeypatch):
    """#37-49: a chart click whose cleanup removed nothing pushes nothing;
    one that changed the scene still pushes."""
    import vibeview.app as app

    reader, server = _showcase_app(showcase_qvf)
    pushes: list[int] = []
    real = app._push_view
    monkeypatch.setattr(app, "_push_view", lambda plotter: (pushes.append(1), real(plotter)))
    try:
        server.controller.activate_section("citations")
        pushes.clear()
        server.controller.activate_section("scf_history")
        assert server.state.selected_section == "scf_history"
        assert pushes == [], "a 2D-to-2D switch serialized the 3D scene"
        server.controller.activate_section("atom_props")
        pushes.clear()
        server.controller.activate_section("citations")  # removes charge labels
        assert pushes, "a switch that changed the scene must push it"
    finally:
        reader.close()


def test_energy_window_redraw_uses_the_registry(showcase_qvf, monkeypatch):
    """#37-26: _redraw_bands_panel resolves the kind like a click does, and
    leaves any other panel alone."""
    import vibeview.app as app

    reader, server = _showcase_app(showcase_qvf)
    opened: list[str] = []
    monkeypatch.setattr(app, "_activate_bands", lambda *a, **k: opened.append("bands"))
    try:
        server.controller.activate_section("citations")
        server.controller.update_bands_energy_window()
        assert opened == []
    finally:
        reader.close()


# ── #37-R4 / #37-31: one appearance authority ─────────────────────────────


def test_app_types_no_colour_literals():
    """#37-R4: the viewer's colours come from renderers/palette.py; app.py
    names them instead of retyping hex strings (the 29 sites !56 left)."""
    import re

    from tests._sources import app_source

    source = app_source()
    assert re.findall(r'"#[0-9A-Fa-f]{6}"', source) == []


def test_bond_order_legend_and_bonds_share_one_table():
    from vibeview.renderers import palette
    from vibeview.renderers.structure import _bond_order_color

    for order, colour in palette.BOND_ORDER_ANCHORS:
        assert _bond_order_color(order).lower() == colour.lower()
    served = served_viewer()
    for order, colour in palette.BOND_ORDER_ANCHORS:
        assert f'color="{colour}"' in served, (order, colour)


def test_background_helpers_are_the_one_authority(viewer):
    from vibeview import app
    from vibeview.renderers import palette

    state, ctrl = viewer.state, viewer.controller
    ctrl.toggle_background()
    dark = bool(state.dark_background)
    assert state.background_override == (
        palette.DARK_BACKGROUND if dark else palette.LIGHT_BACKGROUND
    )
    assert app._scene_background(state) == state.background_override
    state.background_override = None
    state.dark_background = False
    assert app._scene_background(state) == palette.LIGHT_BACKGROUND
    ctrl.toggle_background()
    assert state.background_override == palette.DARK_BACKGROUND


@pytest.mark.parametrize("saved_dark", [True, False])
def test_saved_background_preference_wins_at_startup(saved_dark, monkeypatch):
    """#37-R4: "Dark background (default)" off used to be ignored whenever the
    material preset's own background was dark, which the default one is."""
    import vibeview.settings as settings
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader
    from vibeview.renderers import palette

    saved = dict(settings.DEFAULTS, dark_background=saved_dark)
    monkeypatch.setattr(settings, "load_settings", lambda: dict(saved))
    reader = QVFReader(_H2)
    try:
        state = create_app(reader).state
        assert bool(state.dark_background) is saved_dark
        if not saved_dark:
            assert state.background_override == palette.LIGHT_BACKGROUND
    finally:
        reader.close()


# ── #37-R3 / #37-34: scene hooks and the undo record ──────────────────────


def test_scene_hooks_are_one_record_per_plotter():
    import pyvista as pv

    from vibeview.app import _scene_hooks

    first, second = pv.Plotter(off_screen=True), pv.Plotter(off_screen=True)
    try:
        assert _scene_hooks(first) is _scene_hooks(first)
        assert _scene_hooks(first) is not _scene_hooks(second)
        _scene_hooks(first).push_generation += 1
        assert _scene_hooks(second).push_generation == 0
        assert not hasattr(first, "_vibe_view_update")
    finally:
        first.close()
        second.close()


def test_every_edit_records_one_undo_entry(viewer):
    """#37-34 / #37-R5: _record_undo is the one copy of the ritual; an edit
    leaves exactly one entry and forks away the redo stack."""
    state, ctrl = viewer.state, viewer.controller
    state.edit_mode = True
    state.edit_history, state.edit_future = [], [{"positions": [[0.0, 0.0, 0.0]]}]
    state.edit_selected = [0]
    ctrl.add_hydrogens()
    assert len(state.edit_history) >= 1
    assert state.edit_future == []
    before = len(state.edit_history)
    ctrl.edit_undo()
    assert len(state.edit_history) == before - 1 and len(state.edit_future) == 1
    ctrl.edit_redo()
    assert len(state.edit_history) == before and state.edit_future == []


def test_a_geometry_rebuild_without_relocalized_orbitals_pushes_nothing(monkeypatch):
    """#37-47: every live-optimization step rebuilds the geometry and then
    pushes once; the relocalization invalidation it triggers pushed a second
    time even when there was nothing relocalized to clear."""
    import pyvista as pv

    import vibeview.app as app
    from vibeview.qvf import QVFReader

    created: list[pv.Plotter] = []
    plotter_type = app.pv.Plotter

    def create_plotter(*args, **kwargs):
        created.append(plotter_type(*args, **kwargs))
        return created[-1]

    monkeypatch.setattr(app.pv, "Plotter", create_plotter)
    reader = QVFReader(_H2)
    try:
        app.create_app(reader)
        plotter = created[0]
        pushes: list[int] = []
        monkeypatch.setattr(app, "_push_view", lambda p: pushes.append(1))
        positions = [a.position.tolist() for a in reader.read_structure().atoms]
        positions[0][0] += 0.05
        app._rebuild_structure_from_positions(reader, plotter, positions)
        assert pushes == []
    finally:
        reader.close()


def test_the_editor_draws_with_the_structure_renderer(monkeypatch):
    """#37-46 / #37-R5: an edited structure keeps the representation the
    user chose; the editor used to draw its own balls and sticks."""
    import pyvista as pv

    import vibeview.app as app
    from vibeview.qvf import QVFReader

    created: list[pv.Plotter] = []
    plotter_type = app.pv.Plotter

    def create_plotter(*args, **kwargs):
        created.append(plotter_type(*args, **kwargs))
        return created[-1]

    monkeypatch.setattr(app.pv, "Plotter", create_plotter)
    reader = QVFReader(_H2)
    try:
        server = app.create_app(reader)
        plotter = created[0]
        server.state.representation_style = "space_filling"
        positions = [a.position.tolist() for a in reader.read_structure().atoms]
        positions[0][0] += 0.05
        app._rebuild_structure_from_positions(reader, plotter, positions)
        names = [str(n) for n in plotter.actors]
        assert not any(n.startswith("structure_") for n in names), names
        assert any(n.startswith("atom_") for n in names), names
        assert not any(n.startswith(("bond_", "bonds_")) for n in names), names
        np.testing.assert_allclose(reader.read_structure().atoms[0].position, positions[0])
    finally:
        reader.close()


# ── #37-55: no client-side poller runs forever ────────────────────────────


def test_client_scripts_re_run_on_dom_change_not_on_a_timer(viewer):
    """#37-55: the resize handles and the theme re-ran every 1 s and 400 ms
    for the life of the page. They now re-run on a DOM change (and the theme
    on a ui_dark_mode watch); a timer is only the fallback where
    MutationObserver is missing."""
    import re

    from tests._served import served_document

    served = served_document(viewer)
    scripts = re.findall(r"<script>(.*?)</script>", served, re.S)
    assert scripts
    pollers = [
        m.group(0)
        for script in scripts
        for m in re.finditer(r"setInterval\(\w+,\s*\d+\)", script)
        if "MutationObserver==='undefined'" not in script[max(0, m.start() - 80) : m.start()]
    ]
    assert pollers == [], pollers
    assert served.count("window._vvOnDomChange(") >= 3
    assert "t.state.watch(['ui_dark_mode'],apply)" in served


def test_every_served_script_parses(viewer, tmp_path):
    """The inline scripts are Python string concatenations; a missing quote
    or brace is only caught by a JavaScript parser. Node is a real
    environment gate: the served-DOM lane needs it too."""
    import re
    import shutil
    import subprocess

    from tests._served import served_document

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    scripts = re.findall(r"<script>(.*?)</script>", served_document(viewer), re.S)
    for index, script in enumerate(scripts):
        path = tmp_path / f"script_{index}.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, (index, result.stderr[:500])


# ── #37-39: one download path ─────────────────────────────────────────────


def test_every_download_is_listed_and_the_input_script_too(viewer):
    """#37-39: the Python-input export skipped Recent Exports and the
    payload reset; export_ready was set four times and read nowhere."""
    state, ctrl = viewer.state, viewer.controller
    assert not state.has("export_ready")
    state.download_history = []
    ctrl.activate_section("structure")
    ctrl.export_geometry("xyz")
    ctrl.export_py()
    names = [entry["name"] for entry in state.download_history]
    assert names[:2] == ["vibe-qc input script", "Export (XYZ)"]
    assert state.export_filename == "vibe-qc_input.py"
    assert state.export_data.startswith("data:text/plain;base64,")


# ── #37-30: one guard for the volume controls ─────────────────────────────


def test_volume_controls_share_one_guard_and_push_once(showcase_qvf, monkeypatch):
    import vibeview.app as app

    reader, server = _showcase_app(showcase_qvf)
    state, ctrl = server.state, server.controller
    try:
        ctrl.activate_section("vol_dens_0")
        assert state.volume_loaded
        pushes: list[int] = []
        real = app._push_view
        monkeypatch.setattr(app, "_push_view", lambda p: (pushes.append(1), real(p)))
        ctrl.toggle_wrap_periodic(True)
        assert len(pushes) == 1, pushes  # it rebuilt, then pushed a second time
        pushes.clear()
        ctrl.update_opacity(0.3)
        assert len(pushes) == 1
        assert server.state.opacity == 0.3
        # No volume active: every control is a no-op, including the hint write.
        ctrl.activate_section("citations")
        pushes.clear()
        for call in (lambda: ctrl.update_isovalue(0.02), lambda: ctrl.toggle_reduce_detail(True),
                     lambda: ctrl.add_extra_isosurface()):
            call()
        assert pushes == []
    finally:
        reader.close()


# ── #37-29: uploads use the converters' table ─────────────────────────────


def test_upload_converts_through_the_shared_table(tmp_path):
    """#37-29: a vibe-qc input upload used to be "Unsupported format"; the
    upload's private table missed every format it did not list."""
    import base64

    from vibeview.app import create_app
    from vibeview.converters import convert_bytes_to_qvf
    from vibeview.input_generator import _structure_calculation_params, generate_input_script
    from vibeview.qvf import QVFReader

    reader = QVFReader(_H2)
    try:
        params = {**_structure_calculation_params(reader.read_structure()), "basis": "sto-3g",
                  "method": "rhf", "charge": 0, "multiplicity": 1, "title": "t"}
        script = generate_input_script("single_point", params)
        assert len(QVFReader(convert_bytes_to_qvf(script.encode(), "h2.py")).read_structure()
                   .atoms) == 2
        xyz = b"2\nH2\nH 0 0 0\nH 0 0 0.74\n"
        assert len(QVFReader(convert_bytes_to_qvf(xyz, "h2.xyz")).read_structure().atoms) == 2
        with pytest.raises(ValueError):
            convert_bytes_to_qvf(b"", "notes.unknownext")

        server = create_app(reader)
        server.state.load_file_bytes = base64.b64encode(script.encode()).decode()
        server.state.load_file_name = "h2.py"
        server.controller.trigger_fn("load_file_from_bytes")()
        assert server.state.status_message.startswith("Loaded h2.py"), server.state.status_message
        assert len(server.state.file_names) == 2
    finally:
        reader.close()


def test_every_new_file_opens_in_its_own_representation():
    """#37-28: only the Files dropdown re-picked the representation; an
    upload kept whatever the previous file was showing."""
    import base64

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(_H2)
    try:
        server = create_app(reader)
        server.state.representation_style = "space_filling"
        server.state.load_file_bytes = base64.b64encode(b"2\nH2\nH 0 0 0\nH 0 0 0.74\n").decode()
        server.state.load_file_name = "h2.xyz"
        server.controller.trigger_fn("load_file_from_bytes")()
        assert server.state.active_file_idx == 1
        assert server.state.representation_style == "ball_and_stick"
        assert server.state.file_names[1] == "h2"
    finally:
        reader.close()


# ── #37-48: a view preset is one rebuild ──────────────────────────────────


@pytest.mark.parametrize("preset", ["publication", "presentation", "analysis"])
def test_a_view_preset_rebuilds_and_pushes_once(preset, monkeypatch):
    import vibeview.app as app
    from vibeview.qvf import QVFReader

    reader = QVFReader(_H2)
    try:
        server = app.create_app(reader)
        rebuilds: list[int] = []
        pushes: list[int] = []
        real_rebuild, real_push = app._rebuild_scene, app._push_view
        monkeypatch.setattr(
            app, "_rebuild_scene", lambda *a, **k: (rebuilds.append(1), real_rebuild(*a, **k))[1]
        )
        monkeypatch.setattr(app, "_push_view", lambda p: (pushes.append(1), real_push(p)))
        server.controller.apply_view_preset(preset)
        assert (len(rebuilds), len(pushes)) == (1, 1)
        state = server.state
        expected = {"publication": ("scientific", False, False),
                    "presentation": ("cpk_glossy", True, False),
                    "analysis": ("matte", False, True)}[preset]
        assert (state.material_preset, bool(state.show_atom_labels)) == (expected[0], expected[2])
        assert state.view_preset == preset
    finally:
        reader.close()


# ── #37-50: playback reuses one renderer ──────────────────────────────────


def test_trajectory_playback_reads_the_trajectory_once(water_qvf, monkeypatch):
    from types import SimpleNamespace

    import vibeview.app as app
    from vibeview.qvf import QVFReader

    reader = QVFReader(water_qvf)
    try:
        reads: list[str] = []
        real = reader.read_trajectory
        monkeypatch.setattr(
            reader, "read_trajectory", lambda sid: (reads.append(sid), real(sid))[1]
        )
        state = SimpleNamespace(selected_section="traj0", trajectory_frame=0,
                                trajectory_energy_image=None)
        for frame in (0, 1, 2, 1, 0):
            state.trajectory_frame = frame
            app._update_trajectory_plot(reader, state)
            assert state.trajectory_energy_image
        assert reads == ["traj0"]
    finally:
        reader.close()


# ── #37-36: computed surfaces share one drawer and follow the slider ──────


def test_computed_surfaces_keep_their_base_opacity_and_follow_the_slider(tables_qvf):
    import types

    import pyvista as pv

    import vibeview.app as app
    from vibeview.qvf import QVFReader
    from vibeview.viewer_defaults import ViewerState

    reader = QVFReader(tables_qvf)
    try:
        section = next(s for s in reader.sections if s.kind == "wavefunction.gto")
        plotter = pv.Plotter(off_screen=True)
        state = types.SimpleNamespace(
            wf_density_spin=False, wf_n_per_dim=20, isovalue=0.02, mo_opacity=1.0,
            mo_visible=False, volume_loaded=False, status_message="",
        )
        app._render_wf_density(reader, plotter, ViewerState(), state, section)
        actor = plotter.actors["mo_iso_density"]
        assert actor.GetProperty().GetOpacity() == pytest.approx(0.55)
        # The slider's retint reaches the density, scaled from its own base.
        for name, a in plotter.actors.items():
            if str(name).startswith(app._Actors.MO_SURFACE):
                a.GetProperty().SetOpacity(app._base_opacity(plotter, str(name)) * 0.5)
        assert actor.GetProperty().GetOpacity() == pytest.approx(0.275)
        plotter.close()
    finally:
        reader.close()


# ── #37-20 / #37-33 / #37-R2: one replication writer, one scene reset ────


def _two_file_app(monkeypatch):
    import pyvista as pv

    import vibeview.app as app
    from vibeview.qvf import QVFReader

    created: list[pv.Plotter] = []
    plotter_type = app.pv.Plotter

    def create_plotter(*args, **kwargs):
        created.append(plotter_type(*args, **kwargs))
        return created[-1]

    monkeypatch.setattr(app.pv, "Plotter", create_plotter)
    readers = [QVFReader(_H2), QVFReader(_H2)]
    return readers, app.create_app(readers), created[0]


def test_a_file_switch_drops_a_stale_ssao_pass(monkeypatch):
    readers, server, plotter = _two_file_app(monkeypatch)
    try:
        server.controller.toggle_ssao(True)
        if not server.state.ssao_enabled:
            pytest.skip("this VTK build has no SSAO pass")
        assert plotter.renderer.GetPass() is not None
        server.state.ssao_enabled = False  # switched off without its controller
        server.controller.switch_file(1)
        assert plotter.renderer.GetPass() is None
    finally:
        for reader in readers:
            reader.close()


def test_replication_has_one_writer(monkeypatch):
    readers, server, _plotter = _two_file_app(monkeypatch)
    try:
        state = server.state
        state.pbc_a = state.pbc_b = state.pbc_c = True
        server.controller.update_replication(2, 1, 3)
        assert (state.replication_nx, state.replication_ny, state.replication_nz) == (2, 1, 3)
        server.controller.switch_file(1)
        # The incoming file's (1, 1, 1) reaches the inputs and the renderers alike.
        assert (state.replication_nx, state.replication_ny, state.replication_nz) == (1, 1, 1)
    finally:
        for reader in readers:
            reader.close()
