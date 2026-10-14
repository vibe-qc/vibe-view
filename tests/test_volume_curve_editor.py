"""The node-by-node opacity curve editor for volume mode.

The page widget (``app/tf_editor.py``) commits a node list through the
``update_volume_nodes`` trigger; the server clamps and sorts the nodes,
builds the custom curve, keeps the sliders as its sampling, and re-sends
the property only.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests._qvf_builders import build_qvf

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


def _archive(dest: Path) -> Path:
    shape = (16, 16, 16)
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
    data = np.exp(-(x**2 + y**2 + z**2) / 2.0).astype(np.float32).tobytes()
    return build_qvf(
        dest,
        [
            (
                "structure",
                "structure",
                {"structure": ("json", "sections/structure.json", _STRUCTURE)},
            ),
            (
                "vol_dens_0",
                "volume.density",
                {
                    "grid": ("json", "sections/vol_dens_0/grid.json", grid),
                    "data": (
                        "binary",
                        "sections/vol_dens_0/data.dat",
                        data,
                        "float32",
                        list(shape),
                    ),
                },
            ),
        ],
    )


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


def _array_digests(plotter) -> set[str]:
    from trame_vtk.modules.vtk import serializers as tv

    tv.initialize_serializers()
    ctx = tv.SynchronizationContext()
    tv.serialize(None, plotter.ren_win, tv.reference_id(plotter.ren_win), ctx, 0)
    return set(ctx.data_array_cache.keys())


def test_nodes_placed_one_by_one_make_a_custom_curve():
    from vibeview.renderers.volume_render import (
        RAMP_POSITIONS,
        VolumeRenderError,
        opacity_nodes_in_window,
        transfer_function_from_editor,
    )

    nodes = [[0.9, 0.2], [0.1, 1.0], [-5.0, 0.5], [0.5, 7.0], "junk", [None, 0.1]]
    assert opacity_nodes_in_window(nodes, (0.0, 1.0)) == [
        [0.0, 0.5], [0.1, 1.0], [0.5, 1.0], [0.9, 0.2]
    ], "clamped into the window and [0, 1], sorted, junk dropped"
    tf = transfer_function_from_editor(
        window=(0.0, 1.0), colormap="viridis", ramp=[], preset="custom", opacity_nodes=nodes
    )
    assert tf["preset"] == "custom" and tf["opacity"][1] == [0.1, 1.0]
    assert len(tf["ramp"]) == len(RAMP_POSITIONS)
    assert tf["ramp"][0] == pytest.approx(0.5) and tf["ramp"][-1] == pytest.approx(0.2)
    with pytest.raises(VolumeRenderError, match="two nodes"):
        transfer_function_from_editor(
            window=(0.0, 1.0), colormap="viridis", ramp=[], preset="custom",
            opacity_nodes=[[0.5, 0.5]],
        )


def test_the_trigger_rebuilds_the_curve_and_resends_no_voxels(tmp_path, monkeypatch):
    server, plotter, reader, viewer_state = _open(monkeypatch, _archive(tmp_path / "d.qvf"))
    try:
        state, ctrl = server.state, server.controller
        ctrl.activate_section("vol_dens_0")
        ctrl.set_volume_mode("volume")
        digests = _array_digests(plotter)
        lo, hi = state.vol_window
        mid = (lo + hi) / 2

        ctrl.update_volume_nodes([[hi, 0.1], [mid, 0.9], [lo, 0.0], [hi * 10, 0.3]])
        hints = viewer_state.get_volume_hints("vol_dens_0")
        assert hints.transfer_function["preset"] == "custom"
        assert hints.transfer_function["opacity"] == [[lo, 0.0], [mid, 0.9], [hi, 0.1], [hi, 0.3]]
        assert state.vol_preset == "custom"
        assert state.vol_tf["opacity"] == hints.transfer_function["opacity"]
        assert state.vol_ramp_3 == pytest.approx(
            hints.transfer_function["ramp"][3]
        ), "the sliders show the curve's sampling"
        prop = plotter.actors["volume_render_vol_dens_0"].GetProperty()
        assert prop.GetScalarOpacity(0).GetValue(127.5) == pytest.approx(0.9, abs=0.02)
        assert _array_digests(plotter) == digests, "a curve edit re-sends no voxels"

        # Too few nodes, or junk, change nothing but the status line.
        before = json.dumps(state.vol_tf)
        ctrl.update_volume_nodes([[mid, 0.5]])
        assert "two nodes" in state.status_message and json.dumps(state.vol_tf) == before
        ctrl.update_volume_nodes("junk")
        assert json.dumps(state.vol_tf) == before

        # Picking "Custom curve" in the preset select keeps the nodes.
        ctrl.set_volume_preset("custom")
        assert json.dumps(state.vol_tf) == before
        ctrl.set_volume_preset("ramp")
        assert state.vol_tf["preset"] == "ramp" and len(state.vol_tf["opacity"]) == 6
    finally:
        reader.close()


def test_the_page_carries_the_widget_and_the_trigger_is_registered():
    from tests._served import served_viewer
    from vibeview.app import tf_editor

    page = served_viewer()
    assert f'id="{tf_editor.TF_EDITOR_ID}"' in page
    script = tf_editor.tf_editor_script()
    assert tf_editor.TF_EDITOR_TRIGGER in script and tf_editor.TF_EDITOR_ID in script
    assert 'role="slider"' in script, "handles carry the slider role for the keyboard path"
    for key in ("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Delete", "Enter"):
        assert key in script, f"the keyboard path handles {key}"
