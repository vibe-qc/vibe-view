"""Render as: Volume, step 2 of the direct volume rendering design.

The volume branch of ``_rebuild_volume`` replaces the section's contour
with one volume actor drawn through a transfer function, falls back to the
isosurface when the payload refuses the grid, and survives the material
replay, toon and scene rebuilds. The serializer ships the actor with its
functions and one uint8 array, and a transfer-function change re-sends no
voxels.
"""

from __future__ import annotations

import json
import re
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


def _volume_archive(
    dest: Path, shape: tuple[int, int, int], *, signed: bool = False, skew: float = 0.0
) -> Path:
    axes = [np.linspace(-4.0, 4.0, n) for n in shape]
    spacing = [8.0 / (n - 1) for n in shape]
    vectors = [[spacing[0], skew, 0.0], [0.0, spacing[1], 0.0], [0.0, 0.0, spacing[2]]]
    grid = {"origin": [-4.0, -4.0, -4.0], "voxel_vectors": vectors, "shape": list(shape)}
    x, y, z = np.meshgrid(*axes, indexing="ij")

    def blob(cx: float) -> np.ndarray:
        return np.exp(-((x - cx) ** 2 + y**2 + z**2) / 2.0)

    field = (blob(1.0) - blob(-1.0)) if signed else blob(0.0)
    kind, sid = ("volume.spin", "vol_spin_0") if signed else ("volume.density", "vol_dens_0")
    return build_qvf(
        dest,
        [
            (
                "structure",
                "structure",
                {"structure": ("json", "sections/structure.json", _STRUCTURE)},
            ),
            (
                sid,
                kind,
                {
                    "grid": ("json", f"sections/{sid}/grid.json", json.dumps(grid).encode()),
                    "data": (
                        "binary",
                        f"sections/{sid}/data.dat",
                        field.astype(np.float32).tobytes(),
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
    return server, created[0], reader


def _volume_family(plotter) -> list[str]:
    return sorted(str(n) for n in plotter.actors if str(n).startswith("volume_"))


# ── The browser mode switch ───────────────────────────────────────────────


def test_render_as_volume_swaps_the_contour_for_one_volume_actor(tmp_path, monkeypatch):
    import vibeview.app as app
    from vibeview.renderers.volume_render import is_volume_actor

    server, plotter, reader = _open(monkeypatch, _volume_archive(tmp_path / "d.qvf", (24, 20, 16)))
    try:
        state, ctrl = server.state, server.controller
        ctrl.activate_section("vol_dens_0")
        assert state.vol_mode == "isosurface"
        assert _volume_family(plotter) == ["volume_vol_dens_0"]

        pushes: list[int] = []
        real = app._push_view
        monkeypatch.setattr(app._helpers, "_push_view", lambda p: (pushes.append(1), real(p)))
        ctrl.set_volume_mode(["volume"])  # as the picker sends it
        assert pushes == [1], "one rebuild, one push"
        assert state.vol_mode == "volume"
        assert _volume_family(plotter) == ["volume_render_vol_dens_0"]
        actor = plotter.actors["volume_render_vol_dens_0"]
        assert is_volume_actor(actor)
        assert actor.GetPickable() == 0
        assert state.vol_sent_shape == [24, 20, 16]
        assert state.vol_range[0] == 0.0 and 0.5 < state.vol_range[1] < 1.5
        assert state.vol_tf["preset"] == "density-shell"
        assert state.vol_tf["colormap"] == state.colormap
        assert "24×20×16 voxels" in state.status_message

        pushes.clear()
        ctrl.set_volume_mode("isosurface")
        assert pushes == [1]
        assert _volume_family(plotter) == ["volume_vol_dens_0"]
        assert state.vol_mode == "isosurface"
    finally:
        reader.close()


def test_colormap_and_opacity_reshape_the_transfer_function(tmp_path, monkeypatch):
    server, plotter, reader = _open(monkeypatch, _volume_archive(tmp_path / "d.qvf", (16, 16, 16)))
    try:
        state, ctrl = server.state, server.controller
        ctrl.activate_section("vol_dens_0")
        ctrl.set_volume_mode("volume")
        before = state.vol_tf["color"]
        ctrl.update_colormap("plasma")
        assert state.vol_tf["colormap"] == "plasma"
        assert state.vol_tf["color"] != before
        # The contour's Opacity slider is hidden in volume mode and leaves the
        # function alone; the ramp sliders are the opacity.
        nodes_before = list(state.vol_tf["opacity"])
        ctrl.update_opacity(0.3)
        assert state.vol_tf["opacity"] == nodes_before
        ctrl.update_volume_ramp(5, 0.3)
        assert state.vol_tf["opacity"][-1][1] == pytest.approx(0.3)
        prop = plotter.actors["volume_render_vol_dens_0"].GetProperty()
        pwf = prop.GetScalarOpacity(0)
        assert pwf.GetValue(255.0) == pytest.approx(0.3, abs=1e-6)
        assert pwf.GetValue(0.0) == pytest.approx(0.0, abs=1e-6)
        assert _volume_family(plotter) == ["volume_render_vol_dens_0"]
    finally:
        reader.close()


def test_volume_mode_survives_materials_toon_and_rebuilds(tmp_path, monkeypatch):
    server, plotter, reader = _open(monkeypatch, _volume_archive(tmp_path / "d.qvf", (16, 16, 16)))
    try:
        state, ctrl = server.state, server.controller
        ctrl.activate_section("vol_dens_0")
        ctrl.set_volume_mode("volume")
        ctrl.set_material_preset("matte")
        assert _volume_family(plotter) == ["volume_render_vol_dens_0"]
        ctrl.toggle_toon(True)
        assert state.toon_mode is True, "toon skips the volume property instead of failing"
        assert _volume_family(plotter) == ["volume_render_vol_dens_0"]
        ctrl.toggle_toon(False)
        ctrl.toggle_atom_labels(True)  # a full scene rebuild
        assert state.vol_mode == "volume"
        assert _volume_family(plotter) == ["volume_render_vol_dens_0"]
        ctrl.apply_view_preset("publication")
        assert _volume_family(plotter) == ["volume_render_vol_dens_0"]
    finally:
        reader.close()


def test_a_skewed_grid_is_resampled_and_the_status_line_says_so(tmp_path, monkeypatch):
    server, plotter, reader = _open(
        monkeypatch, _volume_archive(tmp_path / "s.qvf", (16, 16, 16), skew=0.1)
    )
    try:
        state, ctrl = server.state, server.controller
        ctrl.activate_section("vol_dens_0")
        ctrl.set_volume_mode("volume")
        assert state.vol_mode == "volume"
        assert "skewed" in state.status_message and "resampled" in state.status_message
        assert _volume_family(plotter) == ["volume_render_vol_dens_0"]
        assert state.vol_sent_shape is not None and max(state.vol_sent_shape) <= 128
    finally:
        reader.close()


def test_a_field_without_finite_samples_falls_back_to_the_isosurface(tmp_path, monkeypatch):
    import numpy as np

    server, plotter, reader = _open(monkeypatch, _volume_archive(tmp_path / "d.qvf", (8, 8, 8)))
    try:
        import vibeview.renderers.volume as volume_module

        state, ctrl = server.state, server.controller
        ctrl.activate_section("vol_dens_0")
        real_load = volume_module.VolumeRenderer.load_data
        monkeypatch.setattr(
            volume_module.VolumeRenderer, "load_data",
            lambda self: np.full_like(real_load(self), np.nan),
        )
        ctrl.set_volume_mode("volume")
        assert state.vol_mode == "isosurface"
        assert "finite" in state.status_message and "isosurface" in state.status_message
        assert state.vol_sent_shape is None
    finally:
        reader.close()


def test_a_large_grid_is_capped_and_the_status_line_says_so(tmp_path, monkeypatch):
    server, _plotter, reader = _open(
        monkeypatch, _volume_archive(tmp_path / "big.qvf", (300, 16, 16))
    )
    try:
        state, ctrl = server.state, server.controller
        ctrl.activate_section("vol_dens_0")
        ctrl.set_volume_mode("volume")
        assert state.vol_mode == "volume"
        assert state.vol_sent_shape == [128, 7, 7]
        assert "reduced from 300×16×16" in state.status_message
    finally:
        reader.close()


def test_entering_volume_mode_turns_the_clip_off(tmp_path, monkeypatch):
    server, plotter, reader = _open(monkeypatch, _volume_archive(tmp_path / "d.qvf", (16, 16, 16)))
    try:
        state, ctrl = server.state, server.controller
        ctrl.activate_section("vol_dens_0")
        ctrl.toggle_clip(True)
        assert state.clip_enabled is True
        ctrl.set_volume_mode("volume")
        assert state.clip_enabled is False
        assert state.vol_mode == "volume"
        assert _volume_family(plotter) == ["volume_render_vol_dens_0"]
        assert not any(str(n).startswith("_volume") for n in plotter.actors), "no clip actors"
    finally:
        reader.close()


def test_signed_kinds_start_with_the_lobe_ramp(tmp_path, monkeypatch):
    server, _plotter, reader = _open(
        monkeypatch, _volume_archive(tmp_path / "spin.qvf", (16, 16, 16), signed=True)
    )
    try:
        state, ctrl = server.state, server.controller
        ctrl.activate_section("vol_spin_0")
        ctrl.set_volume_mode("volume")
        assert state.vol_mode == "volume"
        assert state.vol_tf["preset"] == "signed-lobes"
        assert state.vol_range[0] == -state.vol_range[1]
        nodes = state.vol_tf["opacity"]
        assert [a for _x, a in nodes[2:4]] == [0.0, 0.0], "nothing is drawn around zero"
        assert nodes[0][1] > 0 and nodes[-1][1] > 0, "both lobes are drawn"
    finally:
        reader.close()


# ── The transfer function and the serializer contract ─────────────────────


def test_default_transfer_function_is_json_in_data_units():
    from vibeview.renderers.volume_render import default_transfer_function, preset_for_kind

    tf = default_transfer_function("volume.density", (0.0, 0.4), colormap="viridis", opacity=0.6)
    assert tf["preset"] == "density-shell" and tf["range"] == [0.0, 0.4]
    assert all(0.0 <= node[0] <= 0.4 for node in tf["color"])
    assert all(0.0 <= node[0] <= 0.4 for node in tf["opacity"])
    assert tf["color"][0][0] == 0.0 and tf["color"][-1][0] == 0.4
    assert max(a for _x, a in tf["opacity"]) == pytest.approx(0.6)
    assert json.loads(json.dumps(tf)) == tf
    assert preset_for_kind("volume.elf") == "elf-basins"
    assert preset_for_kind("volume.orbital") == "signed-lobes"
    assert preset_for_kind("volume.potential") == "potential"
    assert preset_for_kind("volume.generic") == "ramp"
    unknown = default_transfer_function("volume.generic", (0.0, 1.0), colormap="no-such-map")
    assert unknown["color"], "an unknown colormap falls back rather than failing"


def test_the_serializer_ships_the_volume_and_a_transfer_function_edit_sends_no_voxels():
    import pyvista as pv
    from trame_vtk.modules.vtk import serializers as tv

    from vibeview.qvf import GridData
    from vibeview.renderers.volume_render import (
        SCALARS,
        add_volume_actor,
        default_transfer_function,
        transfer_functions,
        volume_actor_name,
        volume_image,
    )

    shape = (12, 10, 8)
    grid = GridData(
        origin=np.array([-2.0, -2.0, -2.0]),
        voxel_vectors=np.diag([0.4, 0.4, 0.4]),
        shape=shape,
        units="angstrom",
    )
    axes = [np.linspace(-2, 2, n) for n in shape]
    x, y, z = np.meshgrid(*axes, indexing="ij")
    field = np.exp(-(x**2 + y**2 + z**2)).astype(np.float32)
    volume = volume_image(field, grid, "volume.density")
    tf = default_transfer_function("volume.density", volume.range)

    tv.initialize_serializers()
    p = pv.Plotter(off_screen=True)
    actor = add_volume_actor(p, volume, tf, name=volume_actor_name("d"))
    try:

        def payload() -> tuple[str, set[str]]:
            ctx = tv.SynchronizationContext()
            blob = json.dumps(
                tv.serialize(None, p.ren_win, tv.reference_id(p.ren_win), ctx, 0),
                sort_keys=True,
            )
            return blob, set(ctx.data_array_cache.keys())

        blob, arrays = payload()
        assert "vtkVolume" in blob
        assert "vtkVolumeMapper" in blob, "every VTK volume mapper aliases to the vtk.js one"
        assert "vtkColorTransferFunction" in blob and "vtkPiecewiseFunction" in blob
        assert "vtkImageData" in blob
        assert len(arrays) == 1, "one uint8 array and nothing else"
        ctx = tv.SynchronizationContext()
        tv.serialize(None, p.ren_win, tv.reference_id(p.ren_win), ctx, 0)
        (cached,) = ctx.data_array_cache.values()
        voxels = np.asarray(cached["array"])  # the cache entry wraps the array
        assert voxels.dtype == np.uint8
        assert voxels.size == int(np.prod(shape))
        assert re.search(rf'"{SCALARS}"', blob)

        # Edit the opacity ramp: the property changes, the voxels do not.
        edited = dict(tf)
        edited["opacity"] = [[node[0], node[1] * 0.5] for node in tf["opacity"]]
        ctf, pwf = transfer_functions(edited)
        actor.GetProperty().SetColor(0, ctf)
        actor.GetProperty().SetScalarOpacity(0, pwf)
        blob2, arrays2 = payload()
        assert arrays2 == arrays, "the array digest is unchanged, so the client keeps it"
        assert blob2 != blob
    finally:
        p.close()
