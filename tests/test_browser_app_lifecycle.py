"""Browser-viewer lifecycle contracts that build fresh apps (vibe-view#47).

Kept apart from ``test_browser_correctness.py``: every ``create_app`` shares
trame's singleton server, so a fresh app rebinds the controllers that the
other module's fixture drives.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_browser_correctness import _qvf


def _scene_hooks(plotter):
    """vibeview.app's hooks record for ``plotter``, looked up at call time:
    another test module reloads vibeview.app, and a name imported at
    collection would then read the old module's registry."""
    from vibeview.app import _scene_hooks as hooks

    return hooks(plotter)

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")


# ── #37-3: persisted settings seed a new viewer ──────────────────────


def _fresh_app(monkeypatch, settings: dict | None = None):
    import vibeview.settings as settings_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    if settings is not None:
        merged = dict(settings_mod.DEFAULTS)
        merged.update(settings)
        monkeypatch.setattr(settings_mod, "load_settings", lambda: dict(merged))
    path = _qvf()
    try:
        return create_app(QVFReader(path))
    finally:
        path.unlink(missing_ok=True)


def test_saved_settings_seed_the_viewer(monkeypatch):
    """#37-3: settings were saved but create_app hard-coded True/cpk_glossy/60."""
    app = _fresh_app(
        monkeypatch,
        {"dark_background": False, "material_preset": "matte", "auto_save_interval": 300},
    )
    state = app.state
    assert state.dark_background is False
    assert state.settings_dark_background is False
    assert state.material_preset == "matte"
    assert state.settings_material_preset == "matte"
    assert state.settings_auto_save_interval == 300
    # Restore the stock defaults on the shared server for later tests.
    _fresh_app(monkeypatch, {})


def test_auto_save_loop_uses_the_configured_interval(monkeypatch):
    """#37-3: the auto-save loop slept a literal 60 s whatever was configured."""
    import asyncio

    import vibeview.app as appmod

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay, *args, **kwargs):
        if delay >= 5:
            sleeps.append(delay)
            raise asyncio.CancelledError
        return await real_sleep(0)

    monkeypatch.setattr(appmod.asyncio, "sleep", fake_sleep)

    async def main():
        _fresh_app(monkeypatch, {"auto_save_interval": 300})
        for _ in range(5):
            await real_sleep(0)

    asyncio.run(main())
    assert sleeps and sleeps[0] == 300
    _fresh_app(monkeypatch, {})


# ── #37-4: viewer_defaults.auto_open renders what it opens ───────────


def _archive_with_auto_open(src: Path, dest: Path, section_ids: list[str]) -> Path:
    import json
    import zipfile

    with zipfile.ZipFile(src) as zin:
        manifest = json.loads(zin.read("manifest.json"))
        manifest.setdefault("viewer_defaults", {})["auto_open"] = section_ids
        with zipfile.ZipFile(dest, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "manifest.json":
                    data = json.dumps(manifest).encode()
                zout.writestr(item, data)
    return dest


def test_auto_open_volume_is_rendered(sample_qvf):
    """#37-4: auto-opening a volume set ``active_volume_id`` but left
    ``volume_loaded=False`` and drew no isosurface."""
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    app = create_app(QVFReader(sample_qvf))  # manifest auto_open = ["density"]
    state = app.state
    assert state.selected_section == "density"
    assert state.active_volume_id == "density"
    assert state.volume_loaded is True
    assert state.status_message == "Auto-opened: density"


def test_auto_open_uses_the_click_dispatch_for_every_kind(tables_qvf, tmp_path):
    """#37-4: kinds the hand-rolled copy did not list (vibrations here) were
    silently ignored; auto-open now runs activate_section."""
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    vib_id = next(
        s.id for s in QVFReader(tables_qvf).sections if s.kind == "vibrations"
    )
    path = _archive_with_auto_open(tables_qvf, tmp_path / "auto.qvf", [vib_id])
    app = create_app(QVFReader(path))
    state = app.state
    assert state.selected_section == vib_id
    assert state.vibration_n_modes > 0
    assert state.status_message == f"Auto-opened: {vib_id}"


# ── #37-5 / #37-6 / #37-7: file switch and sidebar state ────────────


def _archive(
    dest: Path,
    *,
    periodic: bool = False,
    orbitals: int = 0,
    partial_trajectory: bool = False,
) -> Path:
    """A small archive: structure, optional orbital volumes, optional
    partial (streaming) trajectory section."""
    import hashlib
    import json
    import zipfile

    import numpy as np

    def sha(b: bytes) -> str:
        return hashlib.sha256(b).hexdigest()

    atoms = [
        {"symbol": "Na", "position": [0.0, 0.0, 0.0], "atomic_number": 11},
        {"symbol": "Cl", "position": [2.8, 0.0, 0.0], "atomic_number": 17},
    ]
    structure = {"atoms": atoms, "pbc": [periodic] * 3}
    if periodic:
        structure["lattice_vectors"] = [[5.6, 0, 0], [0, 5.6, 0], [0, 0, 5.6]]
    sbytes = json.dumps(structure).encode()
    files = {"s.json": sbytes}
    sections = [
        {
            "id": "structure",
            "kind": "structure",
            "members": {"structure": {"path": "s.json", "format": "json", "sha256": sha(sbytes)}},
        }
    ]
    grid = json.dumps(
        {
            "origin": [-2, -2, -2],
            "voxel_vectors": [[0.5, 0, 0], [0, 0.5, 0], [0, 0, 0.5]],
            "shape": [8, 8, 8],
        }
    ).encode()
    data = (np.random.default_rng(0).standard_normal((8, 8, 8)) * 0.1).astype(np.float32).tobytes()
    for k in range(orbitals):
        files[f"mo{k}.grid.json"] = grid
        files[f"mo{k}.dat"] = data
        sections.append(
            {
                "id": f"mo{k}",
                "kind": "volume.orbital",
                "members": {
                    "grid": {"path": f"mo{k}.grid.json", "format": "json", "sha256": sha(grid)},
                    "data": {
                        "path": f"mo{k}.dat",
                        "format": "binary",
                        "dtype": "float32",
                        "shape": [8, 8, 8],
                        "sha256": sha(data),
                    },
                },
            }
        )
    if partial_trajectory:
        meta = json.dumps(
            {"atoms": [{"symbol": a["symbol"], "atomic_number": a["atomic_number"]} for a in atoms],
             "energies": [-1.0, -1.1]}
        ).encode()
        coords = np.array([[a["position"] for a in atoms]] * 2, dtype=np.float64).tobytes()
        files["traj.json"] = meta
        files["traj.bin"] = coords
        sections.append(
            {
                "id": "opt",
                "kind": "trajectory",
                "partial": True,
                "members": {
                    "metadata": {"path": "traj.json", "format": "json", "sha256": sha(meta)},
                    "coords": {
                        "path": "traj.bin",
                        "format": "binary",
                        "dtype": "float64",
                        "shape": [2, len(atoms), 3],
                        "sha256": sha(coords),
                    },
                },
            }
        )
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": dest.stem},
        "sections": sections,
    }
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, payload in files.items():
            zf.writestr(name, payload)
    return dest


def _orbital_title(state) -> str:
    return next(e["title"] for e in state.sidebar_entries if "Orbitals" in e["title"])


def test_orbital_group_title_is_right_after_a_file_switch(tmp_path):
    """#37-6: the file-switch rebuild titled a crystal's orbital group with
    the reset ``is_periodic=False`` ("Molecular Orbitals")."""
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    mol = QVFReader(_archive(tmp_path / "mol.qvf", orbitals=2))
    xtal = QVFReader(_archive(tmp_path / "xtal.qvf", periodic=True, orbitals=2))
    app = create_app([mol, xtal])
    assert _orbital_title(app.state).startswith("Molecular")
    app.controller.switch_file(1)
    assert _orbital_title(app.state).startswith("Crystalline")
    app.controller.switch_file(0)
    assert _orbital_title(app.state).startswith("Molecular")


def test_streaming_subtitle_on_first_open(tmp_path):
    """#37-7: the "· streaming" subtitle existed only on the reload path."""
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(_archive(tmp_path / "live.qvf", partial_trajectory=True))
    assert reader.section_is_partial("opt")
    app = create_app(reader)
    entry = next(e for e in app.state.sidebar_entries if e["id"] == "opt")
    assert entry["subtitle"].endswith("· streaming")


def test_qa_warnings_do_not_survive_a_file_switch(tmp_path, monkeypatch):
    """#37-5: warnings were assigned only when non-empty, so file A's
    "SCF did not converge" stayed on screen for a clean file B."""
    import vibeview.app as appmod
    from vibeview.qvf import QVFReader

    bad = QVFReader(_archive(tmp_path / "bad.qvf"))
    good = QVFReader(_archive(tmp_path / "good.qvf"))
    monkeypatch.setattr(
        appmod,
        "_qa_validate",
        lambda r: ["SCF did not converge"] if r is bad else [],
    )
    app = appmod.create_app([bad, good])
    assert app.state.qa_warnings == ["SCF did not converge"]
    app.controller.switch_file(1)
    assert app.state.qa_warnings == []


# ── #37-13 / #37-14: state watchers ──────────────────────────────────


def _callbacks(app, key: str) -> list:
    return list(app.state._change_callbacks.get(key, []))


def _set_and_fire(state, **values) -> None:
    """Set keys and run their @state.change watchers, as a client change
    would (a headless server is never "ready", so flush() skips them)."""
    for key, value in values.items():
        setattr(state, key, value)
    for key in values:
        for func, _translator in list(state._change_callbacks.get(key, [])):
            func(**{key: getattr(state, key)})


def test_state_watchers_do_not_accumulate_across_create_app():
    """#37-13: each create_app appended another ``trajectory_playing``
    (and vq, MO-click, …) watcher to trame's singleton server."""
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    paths = [_qvf(), _qvf()]
    try:
        app1 = create_app(QVFReader(paths[0]))
        counts = {k: len(_callbacks(app1, k)) for k in
                  ("trajectory_playing", "vibration_playing", "mo_click_request",
                   "vq_monitor_active", "relocalize_source")}
        app2 = create_app(QVFReader(paths[1]))
        for key, n in counts.items():
            assert len(_callbacks(app2, key)) == n == 1, key
    finally:
        for p in paths:
            p.unlink(missing_ok=True)


def test_vq_monitor_restarts_when_the_drawer_reopens(monkeypatch):
    """#37-14: the poll loop exits when the drawer closes and nothing
    watched ``vq_panel_open``, so reopening it left monitoring dead."""
    import asyncio

    import vibeview.app as appmod
    from vibeview.qvf import QVFReader

    real_sleep = asyncio.sleep
    polls: list[int] = []

    async def fake_sleep(delay, *args, **kwargs):
        if delay == 5:
            polls.append(1)
        await real_sleep(0.001)

    monkeypatch.setattr(appmod.asyncio, "sleep", fake_sleep)
    path = _qvf()

    async def drive():
        app = appmod.create_app(QVFReader(path))
        state = app.state
        assert "vq_panel_open" in state._change_callbacks
        _set_and_fire(state, vq_panel_open=True, vq_monitor_active=True)
        await real_sleep(0.05)
        assert polls, "monitor never polled"
        _set_and_fire(state, vq_panel_open=False)
        await real_sleep(0.05)
        stopped_at = len(polls)
        await real_sleep(0.05)
        assert len(polls) <= stopped_at + 1, "poll kept running with the drawer closed"
        _set_and_fire(state, vq_panel_open=True)
        await real_sleep(0.05)
        assert len(polls) > stopped_at + 1, "monitor did not restart on reopen"
        _set_and_fire(state, vq_monitor_active=False)
        await real_sleep(0.02)

    try:
        asyncio.run(drive())
    finally:
        path.unlink(missing_ok=True)


# ── #37-10 / #37-11: HQ render and turntable ─────────────────────────


def test_hq_render_progress_reaches_the_client_and_holds_the_render_lock(monkeypatch):
    """#37-10: the worker wrote progress into state from its thread and
    nothing flushed it. #37-11: it rendered the live plotter without the
    render lock a section switch takes."""
    import asyncio
    import threading
    import time

    import vibeview.app as appmod
    import vibeview.raytrace as raytrace
    from vibeview.qvf import QVFReader

    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    monkeypatch.setattr(appmod.pv, "Plotter", create_plotter)
    lock_free_during_render: list[bool] = []

    def fake_render(plotter, output_path, *, progress_callback=None, **kwargs):
        lock = _scene_hooks(plotter).wf_render_lock
        probe: list[bool] = []

        def other_thread():
            got = lock.acquire(blocking=False)
            probe.append(got)
            if got:
                lock.release()

        t = threading.Thread(target=other_thread)
        t.start()
        t.join()
        lock_free_during_render.extend(probe)
        progress_callback(0.5, "half way")
        time.sleep(0.6)
        Path(output_path).write_bytes(b"\x89PNG fake")

    monkeypatch.setattr(raytrace, "render_high_quality", fake_render)

    flushed_progress: list[float] = []
    path = _qvf()

    async def drive():
        app = appmod.create_app(QVFReader(path))
        state = app.state
        cls = type(state)
        real_flush = cls.flush

        def spy(self):
            flushed_progress.append(float(self.hq_render_progress or 0.0))
            return real_flush(self)

        monkeypatch.setattr(cls, "flush", spy)
        state.hq_render_resolution = "64x48"
        app.controller.start_hq_render()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if not state.hq_render_running:
                break
        assert state.hq_render_running is False
        assert state.hq_render_progress_msg.startswith("Done")

    try:
        asyncio.run(drive())
    finally:
        path.unlink(missing_ok=True)
    assert lock_free_during_render == [False], "render ran without the render lock"
    assert 0.5 in flushed_progress, "worker progress never reached a flush"


def test_turntable_restores_the_camera(tmp_path):
    """#37-11: render_turntable left the live camera at the last frame's azimuth."""
    import pyvista as pv

    from vibeview.animation import render_turntable

    pl = pv.Plotter(off_screen=True, window_size=[64, 48])
    pl.add_mesh(pv.Cube(x_length=2.0))
    pl.camera_position = [(5.0, 1.0, 2.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)]
    before = [tuple(round(c, 9) for c in v) for v in pl.camera_position]
    render_turntable(pl, tmp_path / "spin", num_frames=4, resolution=(64, 48), format="frames")
    after = [tuple(round(c, 9) for c in v) for v in pl.camera_position]
    assert after == before
    assert pl.camera.azimuth == 0
    pl.close()


# ── #37-40 / #37-51: background tasks and delivered payloads ─────────


def test_controllers_that_schedule_tasks_work_without_an_event_loop(monkeypatch):
    """#37-40: ``asyncio.ensure_future`` with no running loop raised
    (Python 3.14) after the controller's work, so a headless screenshot
    or video export crashed at its very end."""
    import vibeview.animation as anim
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    movie = Path(_qvf().with_suffix(".mp4"))
    movie.write_bytes(b"x")
    monkeypatch.setattr(anim, "render_turntable", lambda *a, **k: movie)
    path = _qvf()
    try:
        app = create_app(QVFReader(path))
        state, ctrl = app.state, app.controller
        ctrl.activate_section("structure")
        ctrl.save_screenshot()
        assert state.screenshot_ready is True
        state.video_export_kind = "turntable"
        state.video_export_format = "mp4"
        ctrl.do_video_export()
        # Without a loop the finalizer runs to completion.
        assert state.video_export_running is False
        assert movie.name in state.status_message
    finally:
        path.unlink(missing_ok=True)
        movie.unlink(missing_ok=True)


def test_delivered_downloads_are_dropped_from_state():
    """#37-51: the reset cleared only the *_ready flags; the base64 payloads
    stayed in state (and were resent) forever."""
    import asyncio

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    path = _qvf()

    async def drive():
        app = create_app(QVFReader(path))
        state, ctrl = app.state, app.controller
        ctrl.activate_section("structure")
        ctrl.export_geometry("xyz")
        assert state.export_data
        ctrl.save_screenshot()
        assert state.screenshot_data
        await asyncio.sleep(0.8)
        assert state.export_data is None
        assert state.screenshot_ready is False and state.screenshot_data is None

    try:
        asyncio.run(drive())
    finally:
        path.unlink(missing_ok=True)


# ── #37-24: `open --section` / `--auto-compare` run their controllers ─


def _run_open(monkeypatch, tmp_path, args):
    from click.testing import CliRunner

    import vibeview.cli as cli
    import vibeview.recent as recent
    from vibeview.app import create_app

    apps = []

    def capture(readers):
        app = create_app(readers)
        apps.append(app)
        return app

    monkeypatch.setattr(cli, "_load_app", lambda: (capture, lambda *a, **k: None))
    monkeypatch.setattr(cli, "_abort_if_port_in_use", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_announce_when_bound", lambda *a, **k: None)
    monkeypatch.setattr(recent, "add_recent", lambda *a, **k: None)
    result = CliRunner().invoke(
        cli.main,
        ["open", "--no-browser", "--log-file", str(tmp_path / "vv.log"), *args],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    return apps[-1]


def test_open_section_activates_it(monkeypatch, tmp_path, sample_qvf):
    """#37-24: ``--section`` set selected_section without activating it."""
    import json
    import zipfile

    # Strip the archive's own auto_open so only --section can render it.
    src = sample_qvf
    dest = tmp_path / "plain.qvf"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["viewer_defaults"]["auto_open"] = []
                data = json.dumps(manifest).encode()
            zout.writestr(item, data)
    app = _run_open(monkeypatch, tmp_path, ["--section", "density", str(dest)])
    assert app.state.selected_section == "density"
    assert app.state.volume_loaded is True


def test_open_auto_compare_draws_the_overlay(monkeypatch, tmp_path):
    """#37-24: ``--auto-compare`` flipped compare_mode but drew no overlay."""
    a = _archive(tmp_path / "a.qvf")
    b = _archive(tmp_path / "b.qvf", periodic=True)
    app = _run_open(monkeypatch, tmp_path, ["--auto-compare", str(a), str(b)])
    assert app.state.compare_mode is True
    assert len(app.state.compare_legend) == 2
    assert app.state.structure_hidden is True


# ── #37-32: one SSAO configuration on every rebuild path ─────────────


def test_ssao_is_configured_the_same_on_every_path(monkeypatch, tmp_path):
    """#37-32: startup, file switch and toggle used the pass defaults while
    the scene rebuild passed radius=2.0, samples=16, so exported images
    changed after an unrelated representation change."""
    import inspect

    import vibeview.material_presets as mp
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    defaults = {
        k: p.default for k, p in inspect.signature(mp.ambient_occlusion_pass).parameters.items()
        if p.default is not inspect.Parameter.empty
    }
    calls: list[tuple] = []

    def spy(plotter, **kwargs):
        calls.append(tuple(sorted({**defaults, **kwargs}.items())))
        return True

    monkeypatch.setattr(mp, "ambient_occlusion_pass", spy)
    a = QVFReader(_archive(tmp_path / "a.qvf"))
    b = QVFReader(_archive(tmp_path / "b.qvf"))
    app = create_app([a, b])
    state, ctrl = app.state, app.controller
    state.ssao_enabled = True
    ctrl.set_representation_style("space_filling")
    ctrl.switch_file(1)
    ctrl.toggle_ssao(False)
    ctrl.toggle_ssao(True)
    assert len(calls) >= 4
    assert len(set(calls)) == 1, calls


# ── #37-16: saved views keep representation, material and surface ────


def _mo_actors(plotter) -> list[str]:
    return [str(n) for n in plotter.actors if str(n).startswith("mo_iso_")]


@pytest.mark.parametrize("via", ["session", "bookmark"])
def test_saved_view_restores_the_computed_surface_and_look(tables_qvf, tmp_path, monkeypatch, via):
    """#37-16: a saved session/bookmark carried camera, section and volume
    hints only; the computed HOMO, representation and material were lost."""
    import vibeview.app as appmod
    from vibeview.qvf import QVFReader

    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    monkeypatch.setattr(appmod.pv, "Plotter", create_plotter)
    app = appmod.create_app(QVFReader(tables_qvf))
    plotter = created[0]
    state, ctrl = app.state, app.controller
    wf_id = next(s["id"] for s in state.section_list if s["kind"] == "wavefunction.gto")
    ctrl.activate_section(wf_id)
    homo = appmod._frontier_row(state.wf_mo_rows, "HOMO")
    ctrl.render_mo(homo["value"])
    assert state.wf_surface_kind == "mo" and _mo_actors(plotter)
    saved_index = state.mo_last_index
    ctrl.set_representation_style("space_filling")
    ctrl.set_material_preset("matte")
    session = tmp_path / "view.json"
    if via == "session":
        ctrl.save_session(str(session))
    else:
        ctrl.save_user_bookmark("homo view")

    ctrl.set_representation_style("ball_and_stick")
    ctrl.set_material_preset("cpk_glossy")
    ctrl.activate_section("structure")
    assert not _mo_actors(plotter) and state.wf_surface_kind is None

    if via == "session":
        ctrl.load_session(str(session))
    else:
        ctrl.apply_user_bookmark("homo view")
    assert state.selected_section == wf_id
    assert state.representation_style == "space_filling"
    assert state.material_preset == "matte"
    assert state.wf_surface_kind == "mo"
    assert state.mo_last_index == saved_index
    assert _mo_actors(plotter), "the saved orbital surface was not re-rendered"


# ── #37-17: Add Hydrogens honours the selection ──────────────────────

_C2 = [
    {"symbol": "C", "position": [0.0, 0.0, 0.0], "atomic_number": 6},
    {"symbol": "C", "position": [1.54, 0.0, 0.0], "atomic_number": 6},
]


def test_add_hydrogens_only_saturates_the_selection():
    """#37-17: the button (enabled only with a selection) saturated every
    atom; build_tools.add_hydrogens had no selection parameter."""
    from vibeview.build_tools import add_hydrogens

    assert len(add_hydrogens([dict(a) for a in _C2])) == 8
    assert len(add_hydrogens([dict(a) for a in _C2], only=[0])) == 5


def test_add_hydrogens_controller_uses_edit_selection():
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    path = _qvf(_C2)
    try:
        reader = QVFReader(path)
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        ctrl.activate_section("structure")
        state.edit_mode = True
        state.edit_history = []
        state.edit_future = []
        state.edit_selected = [1]
        ctrl.add_hydrogens()
        atoms = reader.read_structure().atoms
        assert len(atoms) == 5
        hydrogens = [a for a in atoms if a.symbol == "H"]
        # All three new H sit on the selected carbon (x = 1.54), none on atom 0.
        assert all(abs(h.position[0] - 1.54) < 1.2 and h.position[0] > 1.0 for h in hydrogens)
    finally:
        path.unlink(missing_ok=True)
