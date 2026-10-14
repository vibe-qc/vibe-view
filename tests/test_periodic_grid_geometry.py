"""Periodic fields retain samples and exact physical lattice translations."""

from __future__ import annotations

import json
from contextlib import closing
from itertools import product
from types import SimpleNamespace

import numpy as np
import pytest
import pyvista as pv

from tests.conftest import build_qvf
from vibeview.app import _rebuild_volume
from vibeview.qvf import GridData, QVFReader
from vibeview.renderers.volume import (
    PeriodicGridAlignmentError,
    build_isosurface_mesh,
    replicate_mesh_for_periodic,
    replicate_volume_for_periodic,
)
from vibeview.viewer_defaults import ViewerState


@pytest.mark.parametrize("reverse", [False, True])
def test_aligned_skew_grid_preserves_world_coordinates_and_samples(reverse):
    voxel = np.array([[1.0, 0.0, 0.0], [0.5, 1.0, 0.0], [0.25, 0.5, 1.0]])
    if reverse:
        voxel[1] *= -1
    grid = GridData(np.array([1.0, 2.0, 3.0]), voxel, (4, 4, 4))
    data = np.arange(64).reshape(grid.shape)
    lattice = 4 * voxel * 0.529177210903
    lattice[1] *= -1 if reverse else 1
    original = data.copy()
    tiled, expanded = replicate_volume_for_periodic(data, grid, lattice, (1, 2, 0))
    assert expanded.shape == (12, 20, 4)
    np.testing.assert_array_equal(tiled[4:8, 8:12, :], original)
    # The centre block's origin stays at the original world coordinate.
    np.testing.assert_allclose(expanded.origin + np.array([4, 8, 0]) @ voxel, [1, 2, 3])
    for ix, iy in product(range(3), range(5)):
        np.testing.assert_array_equal(tiled[ix * 4 : (ix + 1) * 4, iy * 4 : (iy + 1) * 4], original)
        actual = expanded.origin + np.array([ix * 4 + 1, iy * 4 + 2, 3]) @ voxel
        expected = np.array([1.0, 2.0, 3.0]) + np.array([1.0, 2.0, 3.0]) @ voxel
        expected += (ix - 1) * 4 * voxel[0] + (iy - 2) * 4 * voxel[1]
        np.testing.assert_allclose(actual, expected)
    np.testing.assert_array_equal(data, original)
    np.testing.assert_array_equal(grid.origin, [1, 2, 3])


def test_consistent_endpoint_overlap_retains_every_original_sample():
    data = np.broadcast_to(np.array([1.0, 2.0, 3.0, 4.0, 1.0])[:, None, None], (5, 3, 3)).copy()
    grid = GridData(np.zeros(3), np.eye(3), data.shape)
    tiled, expanded = replicate_volume_for_periodic(
        data, grid, np.diag([4.0, 3.0, 3.0]) * 0.529177210903, (1, 0, 0)
    )
    np.testing.assert_array_equal(tiled[4:9], data)
    np.testing.assert_array_equal(tiled[:, 0, 0], [1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3, 4, 1])
    np.testing.assert_array_equal(expanded.origin, [-4, 0, 0])


@pytest.mark.parametrize("case", ["fractional", "nonaligned", "gap", "overlap"])
def test_unrepresentable_grid_tiling_is_explicit(case):
    data = np.ones((3, 3, 3))
    lattice = np.diag([3.0, 3.0, 3.0])
    if case == "fractional":
        lattice[0, 0] = 2.5
    elif case == "nonaligned":
        lattice[0, 1] = 1
    elif case == "gap":
        lattice[0, 0] = 4
    else:
        lattice[0, 0] = 2
        data[0] = 2
    grid = GridData(np.zeros(3), np.eye(3), data.shape)
    with pytest.raises(PeriodicGridAlignmentError):
        replicate_volume_for_periodic(data, grid, lattice * 0.529177210903, (1, 0, 0))


def test_inactive_lattice_axis_does_not_restrict_exact_tiling():
    data = np.ones((3, 3, 3))
    grid = GridData(np.zeros(3), np.eye(3), data.shape)
    lattice = np.array([[3.0, 0.0, 0.0], [0.7, 100.0, 0.1], [0.0, 0.0, 3.0]])
    tiled, _ = replicate_volume_for_periodic(data, grid, lattice * 0.529177210903, (1, 0, 1))
    assert tiled.shape == (9, 3, 9)
    assert np.all(tiled == 1)


def _archive(tmp_path, signed, lattice):
    grid = GridData(np.array([1.0, 2.0, 3.0]), np.diag([0.5, 0.5, -0.5]), (9, 5, 5))
    ii, jj, kk = np.indices(grid.shape)
    first = np.exp(-((ii - 2) ** 2 + (jj - 2) ** 2 + (kk - 2) ** 2))
    second = np.exp(-((ii - 6) ** 2 + (jj - 2) ** 2 + (kk - 2) ** 2))
    data = first - second if signed else first + second
    kind = "volume.orbital" if signed else "volume.density"
    structure = json.dumps(
        {
            "atoms": [{"symbol": "H", "atomic_number": 1, "position": [1.0, 1.0, 1.0]}],
            "lattice_vectors": lattice.tolist(),
            "pbc": [True, False, True],
        }
    ).encode()
    metadata = json.dumps(
        {
            "origin": grid.origin.tolist(),
            "voxel_vectors": grid.voxel_vectors.tolist(),
            "shape": list(grid.shape),
        }
    ).encode()
    path = build_qvf(
        tmp_path / "periodic.qvf",
        [
            ("structure", "structure", {"structure": ("json", "structure.json", structure)}),
            (
                "field",
                kind,
                {
                    "grid": ("json", "grid.json", metadata),
                    "data": (
                        "binary",
                        "field.bin",
                        data.astype("<f8").tobytes(),
                        "float64",
                        list(grid.shape),
                    ),
                },
            ),
        ],
    )
    return path, grid, data, kind


def _assert_same_points(actual, expected):
    # Merging can share coincident vertices. Check physical coverage in both
    # directions without assuming vertex order or duplicate storage.
    distances = np.linalg.norm(actual[:, None, :] - expected[None, :, :], axis=-1)
    assert np.max(np.min(distances, axis=0)) < 2e-6
    assert np.max(np.min(distances, axis=1)) < 2e-6


@pytest.mark.parametrize("signed", [False, True])
@pytest.mark.parametrize("alignment", ["exact", "fractional", "nonaligned", "gap", "overlap"])
def test_browser_uses_exact_centered_pbc_copies(tmp_path, monkeypatch, signed, alignment):
    # Literal world-space periods; each unsupported lane must reach the caller fallback.
    lattice = np.diag([5.0, 7.0, 4.0])
    if alignment == "exact":
        lattice = np.diag([4.5, 2.5, -2.5]) * 0.529177210903
    elif alignment == "nonaligned":
        lattice[0] = [5.0, 1.0, 0.0]
    elif alignment == "gap":
        lattice = np.diag([12.0, 10.0, 12.0]) * 0.529177210903
    elif alignment == "overlap":
        lattice = np.diag([2.0, 10.0, 2.0]) * 0.529177210903
    path, grid, data, kind = _archive(tmp_path, signed, lattice)
    state = SimpleNamespace(
        periodic_replication=1,
        is_periodic=True,
        reduce_detail=False,
        clip_enabled=False,
        extra_isosurfaces=[],
        status_message="",
    )
    vs = ViewerState()
    vs.replication = (3, 4, 2)  # The periodic control supersedes these counts.
    hints = vs.get_volume_hints("field", kind=kind)
    hints.isovalue = 0.25
    with QVFReader(path) as reader, closing(pv.Plotter(off_screen=True)) as plotter:
        _rebuild_volume(reader, plotter, vs, state, "field")
        assert ("grid)" if alignment == "exact" else "mesh copies") in state.status_message
        keys = (
            [("pos", np.clip(data, 0, None)), ("neg", np.clip(-data, 0, None))]
            if signed
            else [("", data)]
        )
        for label, values in keys:
            single = build_isosurface_mesh(values, grid, 0.25)
            assert single is not None
            actor = plotter.actors[f"volume_field{'_' + label if label else ''}"]
            actual = actor.mapper.dataset
            expected = np.concatenate(
                [
                    single.points + ix * lattice[0] + iz * lattice[2]
                    for ix, iz in product([-1, 0, 1], repeat=2)
                ]
            )
            _assert_same_points(actual.points, expected)
            assert actual.n_cells == single.n_cells * 9
            if signed:
                np.testing.assert_allclose(
                    actor.prop.color.float_rgb,
                    [0.2, 0.4, 0.8] if label == "pos" else [0.8, 0.2, 0.2],
                )
        cached = vs.get_cached_mesh("field", 0.25, periodic_replication=1)
        assert cached is not None
        # An appearance change reuses exactly those physical copies.
        monkeypatch.setattr(
            "vibeview.renderers.volume.VolumeRenderer.load_data",
            lambda self: pytest.fail("cache missed"),
        )
        hints.opacity = 0.4
        _rebuild_volume(reader, plotter, vs, state, "field")
        assert vs.get_cached_mesh("field", 0.25, periodic_replication=1) is cached
        assert all(
            actor.prop.opacity == 0.4
            for name, actor in plotter.actors.items()
            if name.startswith("volume_field")
        )


def test_centered_mesh_copies_preserve_inputs_and_empty_meshes():
    lattice = np.array([[3.0, 1.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 5.0]])
    assert replicate_mesh_for_periodic(None, lattice, (1, 1, 1)) is None
    empty = pv.PolyData()
    assert replicate_mesh_for_periodic(empty, lattice, (1, 1, 1)) is empty
    original = pv.Sphere(radius=0.2, theta_resolution=8, phi_resolution=8)
    before = original.points.copy()
    result = replicate_mesh_for_periodic(original, lattice, (2, 0, 1))
    assert result is not None
    expected = np.concatenate(
        [
            before + ix * lattice[0] + iz * lattice[2]
            for ix, iz in product(range(-2, 3), range(-1, 2))
        ]
    )
    _assert_same_points(result.points, expected)
    np.testing.assert_array_equal(original.points, before)
    assert replicate_mesh_for_periodic(original, lattice, (0, 0, 0)) is original


@pytest.mark.parametrize("signed", [False, True])
def test_browser_replication_count_and_isovalue_rebuild_cached_geometry(tmp_path, signed):
    lattice = np.array([[5.0, 1.0, 0.0], [0.0, 7.0, 0.0], [0.0, 0.0, 4.0]])
    path, grid, data, kind = _archive(tmp_path, signed, lattice)
    state = SimpleNamespace(
        periodic_replication=1,
        is_periodic=True,
        reduce_detail=False,
        clip_enabled=False,
        extra_isosurfaces=[],
        status_message="",
    )
    vs = ViewerState()
    hints = vs.get_volume_hints("field", kind=kind)
    hints.isovalue = 0.25
    with QVFReader(path) as reader, closing(pv.Plotter(off_screen=True)) as plotter:
        _rebuild_volume(reader, plotter, vs, state, "field")
        previous = vs.get_cached_mesh("field", 0.25, periodic_replication=1)
        state.periodic_replication = 2
        hints.isovalue = 0.5
        _rebuild_volume(reader, plotter, vs, state, "field")
        assert vs.get_cached_mesh("field", 0.5, periodic_replication=2) is not previous
        single = build_isosurface_mesh(np.clip(data, 0, None), grid, 0.5)
        assert single is not None
        expected = np.concatenate(
            [
                single.points + ix * lattice[0] + iz * lattice[2]
                for ix, iz in product(range(-2, 3), repeat=2)
            ]
        )
        actor = plotter.actors["volume_field_pos" if signed else "volume_field"]
        _assert_same_points(actor.mapper.dataset.points, expected)
