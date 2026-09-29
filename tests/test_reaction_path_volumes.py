"""Reaction-path volume playback preserves both signs and replaces old lobes."""

from __future__ import annotations

import json
from contextlib import closing
from types import SimpleNamespace

import numpy as np
import pytest
import pyvista as pv

from tests._qvf_builders import build_qvf
from vibeview.app import _render_trajectory_atoms
from vibeview.qvf import QVFReader
from vibeview.renderers.palette import FRAME_VOLUME, ISO_NEGATIVE, ISO_POSITIVE
from vibeview.renderers.reaction import ReactionPathRenderer


def _path_archive(tmp_path, fields, *, frame_indices=None):
    fields = np.asarray(fields, dtype="<f8")
    n = fields.shape[1]
    indices = frame_indices or list(range(len(fields)))
    coords = np.zeros((indices[-1] + 1, 1, 3), dtype="<f8")
    meta = json.dumps({
        "atoms": [{"symbol": "H", "atomic_number": 1}],
        "waypoints": [],
        "volume_frame_index": indices,
        "volume_isovalue": 0.2,
    }).encode()
    grid = json.dumps({
        "origin": [-2.0] * 3,
        "voxel_vectors": (np.eye(3) * (4.0 / (n - 1))).tolist(),
        "shape": [n] * 3,
    }).encode()
    return build_qvf(tmp_path / "path.qvf", [("path", "reaction.path", {
        "metadata": ("json", "meta.json", meta),
        "coords": ("binary", "coords.bin", coords.tobytes(), "float64", list(coords.shape)),
        "volume_grid": ("json", "grid.json", grid),
        "frame_volumes": (
            "binary", "volumes.bin", fields.tobytes(), "float64", list(fields.shape),
        ),
    })])


@pytest.fixture
def orbital():
    x, y, z = np.meshgrid(*([np.linspace(-2, 2, 20)] * 3), indexing="ij")
    return y * np.exp(-(x*x + y*y + z*z))


def _assert_lobe(plotter, sign, color, y_sign):
    actor = plotter.actors[f"traj_volume_{sign}"]
    assert actor.mapper.dataset.n_points > 0
    assert actor.mapper.dataset.center[1] * y_sign > 0.1
    assert actor.prop.color.float_rgb == pytest.approx(pv.Color(color).float_rgb)
    assert not actor.mapper.scalar_visibility
    assert actor.prop.opacity == pytest.approx(0.45)


def test_two_frame_orbital_draws_both_lobes_and_updates_signs(tmp_path, orbital):
    with (
        QVFReader(_path_archive(tmp_path, [orbital, -orbital])) as reader,
        closing(pv.Plotter(off_screen=True)) as plotter,
    ):
        state = SimpleNamespace(selected_section="path", trajectory_frame=0)
        for frame, sign in ((0, 1), (1, -1), (0, 1)):
            state.trajectory_frame = frame
            _render_trajectory_atoms(reader, plotter, state)
            _assert_lobe(plotter, "pos", ISO_POSITIVE, sign)
            _assert_lobe(plotter, "neg", ISO_NEGATIVE, -sign)
            assert "traj_volume_0" not in plotter.actors


@pytest.mark.parametrize("next_field", ["density", "negative", "empty"])
def test_frame_change_removes_absent_lobes(tmp_path, orbital, next_field):
    field = {"density": np.abs(orbital), "negative": -np.abs(orbital),
             "empty": np.zeros_like(orbital)}[next_field]
    with (
        QVFReader(_path_archive(tmp_path, [orbital, field])) as reader,
        closing(pv.Plotter(off_screen=True)) as plotter,
    ):
        state = SimpleNamespace(selected_section="path", trajectory_frame=0)
        _render_trajectory_atoms(reader, plotter, state)
        state.trajectory_frame = 1
        _render_trajectory_atoms(reader, plotter, state)
        names = {name for name in plotter.actors if name.startswith("traj_volume_")}
        expected = {"density": {"traj_volume_0"}, "negative": {"traj_volume_neg"},
                    "empty": set()}[next_field]
        assert names == expected
        if next_field == "density":
            assert plotter.actors["traj_volume_0"].prop.color.float_rgb == pytest.approx(
                pv.Color(FRAME_VOLUME).float_rgb,
            )


def test_signed_meshes_are_cached_per_emitted_slab(tmp_path, orbital, monkeypatch):
    from vibeview.renderers import volume

    calls = []
    original = volume.build_isosurface_mesh

    def counting(*args, **kwargs):
        calls.append(args[2])
        return original(*args, **kwargs)

    monkeypatch.setattr(volume, "build_isosurface_mesh", counting)
    path = _path_archive(tmp_path, [orbital, -orbital], frame_indices=[0, 2])
    with QVFReader(path) as reader:
        renderer = ReactionPathRenderer(reader.get_section("path"), reader)
        first = renderer.volume_surfaces_for_frame(0)
        held = renderer.volume_surfaces_for_frame(1)
        assert len(first) == len(held) == 2
        assert all(a[2] is b[2] for a, b in zip(first, held, strict=True))
        assert len(calls) == 2
        second = renderer.volume_surfaces_for_frame(2)
        assert len(calls) == 4
        assert [s[:2] for s in first] == [s[:2] for s in second] == [
            ("pos", ISO_POSITIVE), ("neg", ISO_NEGATIVE),
        ]
        for old, new in zip(first, second, strict=True):
            assert old[2].center[1] * new[2].center[1] < 0
        revisited = renderer.volume_surfaces_for_frame(0)
        assert all(a[2] is b[2] for a, b in zip(first, revisited, strict=True))
        assert len(calls) == 4

        # Positive and negative contour levels must occupy distinct cache
        # entries even when their magnitudes match, including custom levels.
        positive = renderer.volume_mesh_for_frame(0, 0.3)
        negative = renderer.volume_mesh_for_frame(0, -0.3)
        assert positive.center[1] > 0.1
        assert negative.center[1] < -0.1
        assert renderer.volume_mesh_for_frame(1, 0.3) is positive
        assert renderer.volume_mesh_for_frame(1, -0.3) is negative
        assert len(calls) == 6
