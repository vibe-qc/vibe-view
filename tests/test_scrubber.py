"""The trajectory and vibration scrubber: filmstrips of frame thumbnails that seek.

A trajectory or reaction path gets a strip of at most ``FILMSTRIP_FRAMES``
frame thumbnails under its slider; a vibration gets a phase slider over one
period and a strip of the mode at every second step. The thumbnails are
rendered off-screen in a background task after activation, a click or a
drag across them seeks, and a section switch cancels a strip still
rendering. Pause now freezes the motion at the nearest scrubber step.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import os
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.conftest import build_qvf
from vibeview.app import helpers as _helpers
from vibeview.app.playback import (
    FILMSTRIP_FRAMES,
    VIBRATION_PHASE_STEPS,
    filmstrip_frames,
    trajectory_filmstrip,
    vibration_filmstrip,
    vibration_phase,
    vibration_step,
)
from vibeview.headless import THUMBNAIL_SIZE, structure_thumbnail_writer
from vibeview.qvf import QVFReader

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

_ATOMS = [
    {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.1173]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.7572, -0.4692]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, -0.7572, -0.4692]},
]
_BASE = np.array([a["position"] for a in _ATOMS], dtype=np.float64)


def _structure_section():
    payload = json.dumps({"atoms": _ATOMS, "pbc": [False, False, False]}).encode()
    return ("structure", "structure", {"structure": ("json", "s.json", payload)})


def _trajectory_section(frames: int):
    coords = np.stack([_BASE + [0.3 * i, 0.0, 0.0] for i in range(frames)])
    meta = json.dumps(
        {
            "atoms": [{"symbol": a["symbol"], "atomic_number": a["atomic_number"]} for a in _ATOMS],
            "energies": [-74.9 - 0.01 * i for i in range(frames)],
            "n_frames": frames,
        }
    ).encode()
    members = {
        "metadata": ("json", "traj/meta.json", meta),
        "coords": ("binary", "traj/coords.bin", coords.tobytes(), "float64", [frames, 3, 3]),
    }
    return ("traj0", "trajectory", members)


def _vibration_section():
    disp = np.zeros((2, 3, 3))
    disp[0, 1] = [0.0, 0.1, 0.0]  # H2 stretches along y
    disp[1, 0] = [0.0, 0.0, 0.1]
    meta = json.dumps({"atoms": _ATOMS, "frequencies": [1000.0, 1500.0]}).encode()
    members = {
        "displacements": (
            "binary", "vib/d.bin", disp.astype("<f8").tobytes(), "float64", [2, 3, 3]
        ),
        "metadata": ("json", "vib/m.json", meta),
    }
    return ("vib0", "vibrations", members)


def _archive(tmp_path: Path, *sections) -> Path:
    return build_qvf(tmp_path / "scrub.qvf", [_structure_section(), *sections])


def _png_size(src: str) -> tuple[int, int]:
    assert src.startswith("data:image/png;base64,")
    image = Image.open(io.BytesIO(base64.b64decode(src.split(",", 1)[1])))
    return image.size


def _app(reader):
    from vibeview.app import create_app

    app = create_app(reader)
    app.controller.view_update = lambda *args, **kwargs: None
    return app


def _run_until(app, key: str, count: int, *, before=None, seconds: float = 90.0):
    """Run the app's background filmstrip task on a loop until ``count`` rows."""

    async def run():
        if before is not None:
            before()
        for _ in range(int(seconds / 0.05)):
            if len(getattr(app.state, key)) >= count:
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"{key} reached {len(getattr(app.state, key))} rows, not {count}")

    asyncio.run(run())


# ── the arithmetic ──────────────────────────────────────────────────────────


def test_filmstrip_frames_tile_the_trajectory():
    assert filmstrip_frames(0) == []
    assert filmstrip_frames(1) == [(0, 0)]
    assert filmstrip_frames(5) == [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4)]
    long = filmstrip_frames(100)
    assert len(long) == FILMSTRIP_FRAMES
    assert long[0][0] == 0 and long[-1] == (99, 99)
    for (_frame, last), (next_frame, _next_last) in zip(long, long[1:], strict=False):
        assert last + 1 == next_frame, "the ranges tile the trajectory"
    assert filmstrip_frames(100, limit=4) == [(0, 32), (33, 65), (66, 98), (99, 99)]


def test_vibration_phase_and_step_round_trip():
    for step in range(VIBRATION_PHASE_STEPS):
        assert vibration_step(vibration_phase(step)) == step
    assert vibration_phase(0) == 0.0
    assert vibration_phase(VIBRATION_PHASE_STEPS // 2) == pytest.approx(math.pi)
    assert vibration_phase(VIBRATION_PHASE_STEPS) == 0.0, "wraps"
    assert vibration_step(2 * math.pi - 1e-9) == 0


# ── the thumbnails ──────────────────────────────────────────────────────────


def test_thumbnail_writer_returns_small_pngs_and_leaves_the_structure_alone(tmp_path):
    path = _archive(tmp_path)
    with QVFReader(path) as reader:
        before = [a.position.copy() for a in reader.read_structure().atoms]
        with structure_thumbnail_writer(reader) as thumbnail:
            assert thumbnail is not None
            rest = thumbnail(None)
            moved = thumbnail(_BASE + [1.5, 0.0, 0.0])
        after = [a.position for a in reader.read_structure().atoms]
    for png in (rest, moved):
        assert png.startswith(b"\x89PNG")
        assert Image.open(io.BytesIO(png)).size == THUMBNAIL_SIZE
    assert rest != moved, "the atoms moved in the picture"
    assert all(np.allclose(b, a) for b, a in zip(before, after, strict=True))


def test_trajectory_filmstrip_rows(tmp_path):
    path = _archive(tmp_path, _trajectory_section(5))
    with QVFReader(path) as reader:
        rows = list(trajectory_filmstrip(reader, reader.get_section("traj0")))
    assert [(r["frame"], r["last"]) for r in rows] == [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4)]
    assert [r["label"] for r in rows][:2] == ["Frame 1", "Frame 2"]
    assert all(_png_size(r["src"]) == THUMBNAIL_SIZE for r in rows)
    assert rows[0]["src"] != rows[-1]["src"]


def test_vibration_filmstrip_rows(tmp_path):
    path = _archive(tmp_path, _vibration_section())
    with QVFReader(path) as reader:
        rows = list(vibration_filmstrip(reader, reader.get_section("vib0"), 0, 1.0))
        none = list(vibration_filmstrip(reader, reader.get_section("vib0"), 7, 1.0))
    assert none == []
    assert [(r["step"], r["last"]) for r in rows] == [
        (s, s + 1) for s in range(0, VIBRATION_PHASE_STEPS, 2)
    ]
    assert rows[0]["label"] == f"Phase 0/{VIBRATION_PHASE_STEPS}"
    assert all(_png_size(r["src"]) == THUMBNAIL_SIZE for r in rows)


def test_filmstrip_needs_a_structure_whose_atoms_line_up(tmp_path):
    only = build_qvf(tmp_path / "bare.qvf", [_trajectory_section(3)])
    with QVFReader(only) as reader:
        assert list(trajectory_filmstrip(reader, reader.get_section("traj0"))) == []


# ── the browser ─────────────────────────────────────────────────────────────


def test_activation_renders_the_filmstrip_in_the_background_and_a_switch_clears_it(tmp_path):
    path = _archive(tmp_path, _trajectory_section(5))
    reader = QVFReader(path)
    try:
        app = _app(reader)
        _run_until(
            app, "trajectory_thumbnails", 5,
            before=lambda: app.controller.activate_section("traj0"),
        )
        rows = app.state.trajectory_thumbnails
        assert [r["frame"] for r in rows] == [0, 1, 2, 3, 4]
        app.controller.trajectory_frame(3)
        assert app.state.trajectory_frame == 3

        app.controller.activate_section("structure")
        assert app.state.trajectory_thumbnails == []
    finally:
        reader.close()


def test_vibration_seek_freezes_the_mode_and_the_strip_follows_the_mode(tmp_path):
    path = _archive(tmp_path, _vibration_section())
    reader = QVFReader(path)
    try:
        app = _app(reader)
        _run_until(
            app, "vibration_thumbnails", VIBRATION_PHASE_STEPS // 2,
            before=lambda: app.controller.activate_section("vib0"),
        )
        first = [r["src"] for r in app.state.vibration_thumbnails]

        app.controller.vibration_seek(4)
        assert app.state.vibration_playing is False
        assert app.state.vibration_phase_step == 4
        assert app.state.vibration_phase == pytest.approx(math.pi / 2)
        app.controller.vibration_seek(VIBRATION_PHASE_STEPS + 1)
        assert app.state.vibration_phase_step == 1, "wraps"

        # A new mode or amplitude re-renders the strip for that mode.
        _run_until(
            app, "vibration_thumbnails", VIBRATION_PHASE_STEPS // 2,
            before=lambda: app.controller.vibration_changed(1, 2.0),
        )
        assert [r["src"] for r in app.state.vibration_thumbnails] != first
        assert app.state.vibration_phase_step == 1, "the frozen phase is kept"

        # The keys step the phase on the server, wrapping, and the mode, clamped.
        app.controller.vibration_step_phase(-1)
        app.controller.vibration_step_phase(-1)
        assert app.state.vibration_phase_step == VIBRATION_PHASE_STEPS - 1
        app.controller.vibration_step_mode(1)
        assert app.state.vibration_mode == 1, "already the last mode"
        app.controller.vibration_step_mode(-1)
        assert app.state.vibration_mode == 0
        app.controller.vibration_step_mode(-1)
        assert app.state.vibration_mode == 0, "clamped at the first"
    finally:
        reader.close()


def test_a_newer_strip_supersedes_one_still_rendering(tmp_path):
    path = _archive(tmp_path, _trajectory_section(5))
    reader = QVFReader(path)
    try:
        app = _app(reader)

        def twice():
            app.controller.activate_section("traj0")
            app.controller.activate_section("structure")
            app.controller.activate_section("traj0")

        _run_until(app, "trajectory_thumbnails", 5, before=twice)
        rows = app.state.trajectory_thumbnails
        assert [r["frame"] for r in rows] == [0, 1, 2, 3, 4], "one strip, in order"
    finally:
        reader.close()


def test_the_template_carries_both_filmstrips_and_the_phase_slider(tmp_path):
    path = _archive(tmp_path, _trajectory_section(2))
    reader = QVFReader(path)
    try:
        template = _app(reader).state["trame__template_main"]
    finally:
        reader.close()
    assert 'aria-label="Trajectory filmstrip"' in template
    assert 'aria-label="Vibration filmstrip"' in template
    assert template.count("@pointermove=\"$event.buttons === 1 && typeof _vvScrub") == 2
    assert template.count('data-index="t.frame"') == 1
    assert template.count('data-index="t.step"') == 1
    assert 'v-for="(t, i) in trajectory_thumbnails"' in template
    assert 'v-for="(t, i) in vibration_thumbnails"' in template
    sliders = [line for line in template.splitlines() if 'v-model="vibration_phase_step"' in line]
    assert len(sliders) == 1 and 'aria-label="Vibration phase"' in sliders[0]


# ── the energy profile seeks ────────────────────────────────────────────────


def _fraction_of(renderer, frame: int) -> float:
    """The image x fraction where ``frame`` is plotted, from the recorded axes."""
    x0, x1, lo, hi = renderer._energy_axis
    x = renderer._frame_x_values(len(renderer.load().energies))[frame]
    return x0 + (x - lo) / (hi - lo) * (x1 - x0)


def test_frame_at_fraction_maps_a_click_through_the_axes(tmp_path):
    from vibeview.renderers.trajectory import TrajectoryRenderer

    path = _archive(tmp_path, _trajectory_section(5))
    with QVFReader(path) as reader:
        renderer = TrajectoryRenderer(reader.get_section("traj0"), reader)
        assert renderer.frame_at_fraction(0.5) is None, "nothing drawn yet"
        assert renderer.render_energy_plot(0).startswith(b"\x89PNG")
        x0, x1, lo, hi = renderer._energy_axis
        assert 0.0 < x0 < x1 < 1.0 and lo < 0 < 4 < hi
        for frame in range(5):
            assert renderer.frame_at_fraction(_fraction_of(renderer, frame)) == frame
        assert renderer.frame_at_fraction(0.0) == 0, "left of the axes: the first frame"
        assert renderer.frame_at_fraction(1.0) == 4, "right of them: the last"


def test_reaction_path_seeks_along_its_coordinate(monkeypatch):
    from vibeview.renderers.reaction import ReactionPathRenderer

    renderer = ReactionPathRenderer.__new__(ReactionPathRenderer)
    renderer._frame_pngs = {}
    data = types.SimpleNamespace(
        energies=np.array([0.0, 1.0, 0.5]), reaction_coordinate=[0.0, 0.5, 2.0], waypoints=[]
    )
    monkeypatch.setattr(renderer, "load", lambda: data)
    monkeypatch.setattr(renderer, "_has_reaction_coordinate", lambda: True)
    assert renderer._frame_x_values(3).tolist() == [0.0, 0.5, 2.0]
    renderer._energy_axis = (0.1, 0.9, 0.0, 2.0)
    # 0.6 of the axes is x = 1.2, nearest the third point at 2.0? No: |1.2-0.5| = 0.7,
    # |1.2-2.0| = 0.8, so the second point wins.
    assert renderer.frame_at_fraction(0.1 + 0.6 * 0.8) == 1
    assert renderer.frame_at_fraction(0.9) == 2


def test_clicking_the_energy_profile_seeks(tmp_path):
    path = _archive(tmp_path, _trajectory_section(5))
    reader = QVFReader(path)
    try:
        app = _app(reader)
        app.controller.activate_section("traj0")
        assert app.state.trajectory_energy_image
        renderer = _helpers._frame_plot_renderer(reader, reader.get_section("traj0"))
        app.controller.seek_energy_profile(_fraction_of(renderer, 3))
        assert app.state.trajectory_frame == 3
        app.controller.seek_energy_profile("not a number")
        assert app.state.trajectory_frame == 3
        app.controller.activate_section("structure")
        app.controller.seek_energy_profile(0.5)
        assert app.state.trajectory_frame == 0, "no trajectory on show: a no-op"
    finally:
        reader.close()


def test_the_template_wires_the_profile_click_and_the_playback_keys(tmp_path):
    path = _archive(tmp_path, _trajectory_section(2))
    reader = QVFReader(path)
    try:
        app = _app(reader)
        template = app.state["trame__template_main"]
        keys = app.state.shortcut_keys
    finally:
        reader.close()
    assert template.count("[$event.offsetX / $event.currentTarget.clientWidth]") == 1
    # The shortcut script is a page script, not part of the Vue template.
    from tests._served import served_viewer

    source = served_viewer()
    for needle in (
        "t.trigger('trajectory_play_toggle')",
        "t.trigger('vibration_play_toggle')",
        "t.trigger('trajectory_step',[d])",
        "t.trigger('vibration_step_phase',[d])",
        "t.trigger('vibration_step_mode',[k==='>'?1:-1])",
        "if(target&&target.tagName==='BUTTON')return;",
    ):
        assert needle in source, needle
    assert list(keys)[list(keys).index("p") + 1 : list(keys).index("p") + 4] == [
        "Space", "[ / ]", "< / >"
    ]
