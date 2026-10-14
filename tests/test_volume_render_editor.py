"""The transfer-function editor, step 3 of the direct volume rendering design.

The render mode and the transfer function are per-section hints: a producer
sets them in ``viewer_defaults``, the editor's preset, window and ramp
write them, bookmarks and sessions carry them, and activation restores
them. Editing the ramp re-sends no voxels; moving the window re-quantises.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests._qvf_builders import sha256, write_archive

_STRUCTURE = json.dumps(
    {
        "atoms": [
            {"symbol": "O", "position": [0.0, 0.0, 0.0], "atomic_number": 8},
            {"symbol": "H", "position": [0.0, 0.757, 0.587], "atomic_number": 1},
            {"symbol": "H", "position": [0.0, -0.757, 0.587], "atomic_number": 1},
        ],
        "pbc": [False, False, False],
    }
).encode()


def _volume_section(sid: str, kind: str, shape, *, signed: bool):
    axes = [np.linspace(-4.0, 4.0, n) for n in shape]
    spacing = [8.0 / (n - 1) for n in shape]
    grid = json.dumps(
        {
            "origin": [-4.0, -4.0, -4.0],
            "voxel_vectors": [[spacing[0], 0, 0], [0, spacing[1], 0], [0, 0, spacing[2]]],
            "shape": list(shape),
        }
    ).encode()
    x, y, z = np.meshgrid(*axes, indexing="ij")

    def blob(cx: float) -> np.ndarray:
        return np.exp(-((x - cx) ** 2 + y**2 + z**2) / 2.0)

    field = (blob(1.0) - blob(-1.0)) if signed else blob(0.0)
    data = field.astype(np.float32).tobytes()
    section = {
        "id": sid,
        "kind": kind,
        "members": {
            "grid": {"path": f"sections/{sid}/grid.json", "format": "json", "sha256": sha256(grid)},
            "data": {
                "path": f"sections/{sid}/data.dat",
                "format": "binary",
                "sha256": sha256(data),
                "dtype": "float32",
                "shape": list(shape),
            },
        },
    }
    return section, {f"sections/{sid}/grid.json": grid, f"sections/{sid}/data.dat": data}


def _archive(dest: Path, *, viewer_defaults: dict | None = None, shape=(16, 16, 16)) -> Path:
    structure = {
        "id": "structure",
        "kind": "structure",
        "members": {
            "structure": {
                "path": "sections/structure.json",
                "format": "json",
                "sha256": sha256(_STRUCTURE),
            }
        },
    }
    files = {"sections/structure.json": _STRUCTURE}
    dens, dens_files = _volume_section("vol_dens_0", "volume.density", shape, signed=False)
    spin, spin_files = _volume_section("vol_spin_0", "volume.spin", shape, signed=True)
    files.update(dens_files)
    files.update(spin_files)
    return write_archive([structure, dens, spin], files, dest=dest, viewer_defaults=viewer_defaults)


def _open(monkeypatch, path: Path):
    import vibeview.app as appmod
    from vibeview.qvf import QVFReader

    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    monkeypatch.setattr(appmod.pv, "Plotter", create_plotter)
    reader = QVFReader(path)
    server = appmod.create_app(reader)
    server.controller.view_update = lambda *_args, **_kwargs: None
    viewer = server.controller.update_isovalue.func.__self__.viewer
    return server, created[0], reader, viewer.viewer_state


def _volume_family(plotter) -> list[str]:
    return sorted(str(n) for n in plotter.actors if str(n).startswith("volume_"))


def _array_digests(plotter) -> set[str]:
    from trame_vtk.modules.vtk import serializers as tv

    tv.initialize_serializers()
    ctx = tv.SynchronizationContext()
    tv.serialize(None, plotter.ren_win, tv.reference_id(plotter.ren_win), ctx, 0)
    return set(ctx.data_array_cache.keys())


# ── Hints ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("node_key", ["color", "opacity"])
def test_malformed_node_list_does_not_prevent_browser_startup(tmp_path, monkeypatch, node_key):
    transfer_function = {
        "range": [0, 1],
        "color": [[0, 0, 0, 0], [1, 1, 1, 1]],
        "opacity": [[0, 0], [1, 1]],
        node_key: 3,
    }
    path = _archive(
        tmp_path / "malformed-hint.qvf",
        viewer_defaults={
            "vol_dens_0": {"render": "volume", "transfer_function": transfer_function},
            "vol_spin_0": {"isovalue": 0.02},
        },
    )
    server, plotter, reader, viewer_state = _open(monkeypatch, path)
    try:
        assert viewer_state.get_volume_hints("vol_dens_0").transfer_function is None
        assert viewer_state.get_volume_hints("vol_spin_0").isovalue == 0.02
        server.controller.activate_section("vol_dens_0")
        assert server.state.vol_mode == "volume"
        assert server.state.vol_tf["preset"] == "density-shell"
        assert _volume_family(plotter) == ["volume_render_vol_dens_0"]
    finally:
        reader.close()
        plotter.close()


def test_producer_hints_carry_the_mode_and_a_validated_transfer_function():
    from vibeview.qvf import ViewerDefaults
    from vibeview.viewer_defaults import ViewerState

    good = {
        "range": [0.0, 0.5],
        "color": [[0.0, 0.1, 0.2, 0.3], [0.5, 0.9, 0.8, 0.7]],
        "opacity": [[0.5, 0.9], [0.0, 0.0]],
        "preset": "density-shell",
    }
    defaults = ViewerDefaults.model_validate(
        {
            "auto_open": [],
            "dens": {"render": "volume", "transfer_function": good, "opacity": 0.8},
            "spin": {"render": "VOLUME "},
            "elf": {"render": "contour", "transfer_function": {"range": [1, 0]}},
            "bad": {"transfer_function": {"range": [0, 1], "color": [[0, 2, 0, 0]], "opacity": []}},
        }
    )
    kinds = {
        "dens": "volume.density",
        "spin": "volume.spin",
        "elf": "volume.elf",
        "bad": "volume.generic",
    }
    state = ViewerState.from_manifest(defaults, kinds)
    dens = state.get_volume_hints("dens")
    assert dens.render == "volume" and dens.opacity == 0.8 and dens.isovalue == 0.05
    assert dens.transfer_function is not None
    assert dens.transfer_function["opacity"] == [[0.0, 0.0], [0.5, 0.9]], "nodes are sorted"
    assert dens.transfer_function["preset"] == "density-shell"
    assert state.get_volume_hints("spin").render == "volume", "case and spaces are forgiven"
    elf = state.get_volume_hints("elf")
    assert elf.render == "isosurface" and elf.transfer_function is None
    assert state.get_volume_hints("bad").transfer_function is None
    assert state.get_volume_hints("unmentioned", kind="volume.density").render == "isosurface"


def test_validate_transfer_function_cleans_or_refuses():
    from vibeview.viewer_defaults import render_mode, validate_transfer_function

    assert validate_transfer_function(None) is None
    assert validate_transfer_function({"range": [0, 1]}) is None
    clean = validate_transfer_function(
        {
            "range": ["0", 1],
            "color": [[1, 0, 0, 0], [0, 1, 1, 1], "junk", [0.5, 2, 0, 0]],
            "opacity": [[1, 1], [0, 0], [0.5, 7]],
            "colormap": "custom",
            "preset": 3,
        }
    )
    assert clean == {
        "range": [0.0, 1.0],
        "color": [[0.0, 1.0, 1.0, 1.0], [1.0, 0.0, 0.0, 0.0]],
        "opacity": [[0.0, 0.0], [1.0, 1.0]],
        "colormap": "custom",
    }
    assert render_mode("Volume") == "volume" and render_mode("surface") == "isosurface"


# ── The editor model ──────────────────────────────────────────────────────


def test_ramps_round_trip_through_the_six_sliders():
    from vibeview.renderers.volume_render import (
        RAMP_POSITIONS,
        ramp_for_preset,
        ramp_from_transfer_function,
        transfer_function_from_editor,
    )

    assert ramp_for_preset("signed-lobes")[0] == 1.0
    assert ramp_for_preset("signed-lobes")[2:4] == [0.0, 0.0], "the zero gap sits on sliders"
    assert ramp_for_preset("elf-basins")[:3] == [0.0, 0.0, 0.0]
    ramp = [0.0, 0.1, 0.5, 0.9, 0.2, 1.0]
    tf = transfer_function_from_editor(
        window=(-1.0, 3.0), colormap="plasma", ramp=ramp, preset="ramp"
    )
    assert [x for x, _a in tf["opacity"]] == [-1.0 + t * 4.0 for t in RAMP_POSITIONS]
    assert ramp_from_transfer_function(tf) == pytest.approx(ramp)
    assert tf["color"][0][0] == -1.0 and tf["color"][-1][0] == 3.0
    custom = transfer_function_from_editor(
        window=(0.0, 1.0), colormap="custom", ramp=ramp, preset="ramp",
        color=[[0.0, 1, 0, 0], [1.0, 0, 0, 1]],
    )
    assert custom["color"] == [[0.0, 1.0, 0.0, 0.0], [1.0, 0.0, 0.0, 1.0]]


def test_resolve_keeps_a_saved_shape_across_a_window_change():
    from vibeview.renderers.volume_render import resolve_transfer_function

    saved = {
        "range": [0.0, 1.0],
        "color": [[0.0, 0, 0, 0], [1.0, 1, 1, 1]],
        "opacity": [[0.0, 0.0], [0.5, 1.0], [1.0, 0.0]],
        "preset": "ramp",
        "colormap": "viridis",
    }
    tf = resolve_transfer_function(
        kind="volume.density", window=(0.0, 2.0), colormap="viridis", opacity=0.6, existing=saved
    )
    assert tf["range"] == [0.0, 2.0]
    assert tf["ramp"][3] == pytest.approx(0.8), "the peak at 0.5 of the old window sits at 1.0 now"
    fresh = resolve_transfer_function(
        kind="volume.spin", window=(-1.0, 1.0), colormap="RdBu", opacity=0.5, existing=None
    )
    assert fresh["preset"] == "signed-lobes" and max(fresh["ramp"]) == pytest.approx(0.5)


# ── The browser ───────────────────────────────────────────────────────────


def test_a_producer_hint_opens_the_section_as_a_volume(tmp_path, monkeypatch):
    path = _archive(
        tmp_path / "hinted.qvf",
        viewer_defaults={
            "vol_dens_0": {
                "render": "volume",
                "transfer_function": {
                    "range": [0.0, 0.5],
                    "colormap": "custom",
                    "color": [[0.0, 0.0, 0.0, 1.0], [0.5, 1.0, 0.0, 0.0]],
                    "opacity": [[0.0, 0.0], [0.5, 0.9]],
                },
            }
        },
    )
    server, plotter, reader, viewer_state = _open(monkeypatch, path)
    try:
        state, ctrl = server.state, server.controller
        ctrl.activate_section("vol_dens_0")
        assert state.vol_mode == "volume"
        assert _volume_family(plotter) == ["volume_render_vol_dens_0"]
        assert state.vol_range == [0.0, 0.5], "the hinted window is the quantisation range"
        assert state.vol_tf["colormap"] == "custom"
        assert state.vol_tf["color"][0][1:] == [0.0, 0.0, 1.0], "the producer's palette is kept"
        assert state.vol_tf["opacity"] == [[0.0, 0.0], [0.5, 0.9]], "its nodes are kept exactly"
        assert state.vol_ramp_5 == pytest.approx(0.9)
        # The other section keeps its own, default, mode.
        ctrl.activate_section("vol_spin_0")
        assert state.vol_mode == "isosurface"
        assert _volume_family(plotter) == ["volume_vol_spin_0_neg", "volume_vol_spin_0_pos"]
        ctrl.activate_section("vol_dens_0")
        assert state.vol_mode == "volume"
        # Picking a colormap replaces the custom palette.
        ctrl.update_colormap("plasma")
        assert state.vol_tf["colormap"] == "plasma"
        assert viewer_state.get_volume_hints("vol_dens_0").transfer_function["colormap"] == "plasma"
    finally:
        reader.close()


def test_the_editor_writes_the_section_hint_and_only_the_window_resends(tmp_path, monkeypatch):
    server, plotter, reader, viewer_state = _open(monkeypatch, _archive(tmp_path / "e.qvf"))
    try:
        state, ctrl = server.state, server.controller
        ctrl.activate_section("vol_dens_0")
        ctrl.set_volume_mode("volume")
        hints = viewer_state.get_volume_hints("vol_dens_0")
        assert hints.render == "volume"
        assert [state.vol_window_low, state.vol_window_high] == state.vol_window
        assert state.vol_window_bounds[0] <= state.vol_window[0] < state.vol_window[1]
        assert hints.transfer_function["ramp"] == pytest.approx(
            [float(getattr(state, f"vol_ramp_{i}")) for i in range(6)]
        )
        digests = _array_digests(plotter)

        ctrl.update_volume_ramp([2], [0.9])  # as the slider sends it
        assert state.vol_ramp_2 == 0.9
        assert hints.transfer_function["opacity"][2][1] == pytest.approx(0.9)
        prop = plotter.actors["volume_render_vol_dens_0"].GetProperty()
        x = hints.transfer_function["opacity"][2][0]
        lo, hi = hints.transfer_function["range"]
        level = (x - lo) / (hi - lo) * 255
        assert prop.GetScalarOpacity(0).GetValue(level) == pytest.approx(0.9, abs=0.02)
        assert _array_digests(plotter) == digests, "a ramp edit re-sends no voxels"

        ctrl.set_volume_preset("elf-basins")
        assert state.vol_preset == "elf-basins"
        assert hints.transfer_function["preset"] == "elf-basins"
        assert state.vol_ramp_0 == 0.0 and state.vol_ramp_5 == 1.0
        assert _array_digests(plotter) == digests

        high_before = state.vol_window[1]
        ctrl.update_volume_window("high", high_before * 0.5)
        assert state.vol_window[1] == pytest.approx(high_before * 0.5)
        assert state.vol_range[1] == pytest.approx(high_before * 0.5)
        assert hints.transfer_function["range"][1] == pytest.approx(high_before * 0.5)
        assert _array_digests(plotter) != digests, "a window change re-quantises the grid"
        # The ends never cross and stay within the field's bounds.
        ctrl.update_volume_window("low", 1e9)
        assert state.vol_window[0] < state.vol_window[1]
        assert state.vol_window[1] <= state.vol_window_bounds[1]
    finally:
        reader.close()


def test_bookmarks_and_sessions_carry_the_mode_and_the_function(tmp_path, monkeypatch):
    server, plotter, reader, viewer_state = _open(monkeypatch, _archive(tmp_path / "s.qvf"))
    try:
        state, ctrl = server.state, server.controller
        ctrl.activate_section("vol_dens_0")
        ctrl.set_volume_mode("volume")
        ctrl.set_volume_preset("ramp")
        ctrl.update_volume_ramp(1, 0.7)
        edited = json.loads(json.dumps(state.vol_tf))
        ctrl.save_user_bookmark("cloud")
        session_path = tmp_path / "cloud.vibe-view-session.json"
        ctrl.save_session(str(session_path))

        ctrl.set_volume_preset("density-shell")
        ctrl.set_volume_mode("isosurface")
        assert _volume_family(plotter) == ["volume_vol_dens_0"]
        ctrl.apply_user_bookmark("cloud")
        assert state.vol_mode == "volume"
        assert _volume_family(plotter) == ["volume_render_vol_dens_0"]
        assert np.allclose(state.vol_tf["opacity"], edited["opacity"])
        assert state.vol_preset == "ramp"

        ctrl.set_volume_mode("isosurface")
        ctrl.update_volume_ramp(1, 0.1)
        ctrl.load_session(str(session_path))
        assert state.vol_mode == "volume"
        assert _volume_family(plotter) == ["volume_render_vol_dens_0"]
        assert state.vol_ramp_1 == pytest.approx(0.7)
        saved = json.loads(session_path.read_text())
        assert saved["render"] == "volume" and saved["transfer_function"]["ramp"][1] == 0.7
    finally:
        reader.close()
