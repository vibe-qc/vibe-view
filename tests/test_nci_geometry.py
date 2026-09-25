"""NCI contours preserve coordinates and interpolate their color field."""

from __future__ import annotations

import json
from contextlib import closing
from types import SimpleNamespace

import numpy as np
import pytest
import pyvista as pv

from tests.conftest import build_qvf
from vibeview.app import _render_nci_surface
from vibeview.qvf import GridData, QVFReader
from vibeview.renderers.nci import build_nci_mesh
from vibeview.renderers.volume import build_isosurface_mesh
from vibeview.viewer_defaults import ViewerState


def _plane_fields(skew=False, flipped_axes=()):
    voxel = np.array([[1.0, 0.0, 0.0], [0.5 if skew else 0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    origin = np.array([1.0, 2.0, 3.0])
    for axis in flipped_axes:
        origin += 4 * voxel[axis]
        voxel[axis] *= -1
    grid = GridData(origin, voxel, (5, 5, 5))
    xyz = origin + np.stack(np.indices(grid.shape), axis=-1) @ voxel
    rdg = xyz[..., 0] - 0.5 * xyz[..., 1]
    color = 2 * xyz[..., 0] - 3 * xyz[..., 1] + 0.5 * xyz[..., 2] - 1
    return grid, rdg, color


@pytest.mark.parametrize("skew", [False, True])
@pytest.mark.parametrize("flipped_axes", [(), (0,), (1,), (2,), (0, 1, 2)])
def test_nci_full_voxel_geometry_and_color_interpolation(skew, flipped_axes):
    grid, rdg, color = _plane_fields(skew, flipped_axes)
    before = (rdg.copy(), color.copy(), grid.origin.copy(), grid.voxel_vectors.copy())
    mesh = build_nci_mesh(rdg, color, grid, 1.5)
    assert mesh is not None and mesh.n_points > 0
    points = mesh.points / 0.529177210903
    np.testing.assert_allclose(points[:, 0] - 0.5 * points[:, 1], 1.5, atol=1e-6)
    np.testing.assert_allclose(mesh["rdg"], 1.5, atol=1e-6)
    np.testing.assert_allclose(
        mesh["color"],
        2 * points[:, 0] - 3 * points[:, 1] + 0.5 * points[:, 2] - 1,
        atol=5e-6,
    )
    for actual, original in zip((rdg, color, grid.origin, grid.voxel_vectors), before, strict=True):
        np.testing.assert_array_equal(actual, original)


@pytest.mark.parametrize("colored", [False, True])
def test_nci_empty_mesh_and_pbc_replication(colored):
    grid, rdg, color = _plane_fields(skew=True)
    field = color if colored else None
    lattice = np.array([[6.0, 0.0, 0.0], [2.0, 7.0, 0.0], [0.0, 0.0, 8.0]])
    kwargs = dict(replication=(2, 3, 1), lattice_vectors=lattice, pbc=(True, False, True))
    assert build_nci_mesh(rdg, field, grid, 100, **kwargs) is None
    single = build_nci_mesh(rdg, field, grid, 1.5)
    repeated = build_nci_mesh(rdg, field, grid, 1.5, **kwargs)
    assert single is not None and repeated is not None
    assert repeated.n_points == 2 * single.n_points
    np.testing.assert_allclose(
        repeated.bounds, [single.bounds[0], single.bounds[1] + 6, *single.bounds[2:]], atol=1e-6
    )
    assert ("color" in repeated.point_data) == colored
    if colored:
        np.testing.assert_allclose(np.sort(repeated["color"]), np.sort(np.tile(single["color"], 2)))


@pytest.mark.parametrize("grid_units", ["bohr", "angstrom"])
def test_shared_extra_arrays_roll_with_field_in_declared_units(grid_units):
    shape = (8, 3, 3)
    scale = 0.529177210903 if grid_units == "angstrom" else 1.0
    grid = GridData(np.zeros(3), np.eye(3) * scale, shape)
    data = np.zeros(shape)
    data[0] = 1
    color = 3 * data + np.indices(shape)[1]
    original = color.copy()
    lattice = np.diag([8.0, 3.0, 3.0]) * 0.529177210903
    mesh = build_isosurface_mesh(
        data,
        grid,
        0.5,
        grid_units=grid_units,
        extra_point_arrays={"color": color},
        wrap_to_center=True,
        lattice_vectors=lattice,
        pbc=(True, False, False),
    )
    assert mesh is not None and mesh.n_points > 0
    points = mesh.points / 0.529177210903
    np.testing.assert_allclose(mesh["color"], 1.5 + points[:, 1], atol=1e-6)
    np.testing.assert_allclose([points[:, 0].min(), points[:, 0].max()], [3.5, 4.5], atol=1e-6)
    np.testing.assert_array_equal(color, original)
    assert np.count_nonzero(data) == 9 and data[0, 0, 0] == 1


@pytest.mark.parametrize(
    "extras", [{"color": np.zeros((2, 2, 2))}, {"values": np.zeros((5, 5, 5))}]
)
def test_extra_arrays_reject_bad_shape_or_primary_name(extras):
    grid, data, _ = _plane_fields()
    with pytest.raises(ValueError, match="shape|values"):
        build_isosurface_mesh(data, grid, 1.5, extra_point_arrays=extras)


def _volume_entry(sid, kind, grid, data):
    metadata = json.dumps(
        {
            "origin": grid.origin.tolist(),
            "voxel_vectors": grid.voxel_vectors.tolist(),
            "shape": list(grid.shape),
        }
    ).encode()
    return (
        sid,
        kind,
        {
            "grid": ("json", f"{sid}.json", metadata),
            "data": (
                "binary",
                f"{sid}.bin",
                data.astype("<f8").tobytes(),
                "float64",
                list(grid.shape),
            ),
        },
    )


def test_browser_density_presence_keeps_same_nci_replication(tmp_path):
    grid = GridData(
        np.zeros(3), np.array([[1.0, 0.0, 0.0], [0.5, 1.0, 0.0], [0.0, 0.0, 1.0]]), (5, 5, 5)
    )
    ii, jj, kk = np.indices(grid.shape, dtype=float)
    rdg = (ii - 2) ** 2 + (jj - 2) ** 2 + (kk - 2) ** 2
    rho = np.exp(-rdg / 4)
    lattice = np.diag([5.0, 7.0, 9.0])
    structure = json.dumps(
        {
            "atoms": [{"symbol": "H", "atomic_number": 1, "position": [0.0, 0.0, 0.0]}],
            "lattice_vectors": lattice.tolist(),
            "pbc": [True, False, True],
        }
    ).encode()
    meshes = []
    for colored in [False, True]:
        entries = [
            ("structure", "structure", {"structure": ("json", "structure.json", structure)}),
            _volume_entry("rdg", "volume.rdg", grid, rdg),
        ]
        if colored:
            entries.append(_volume_entry("rho", "volume.density", grid, rho))
        path = build_qvf(tmp_path / f"field-{colored}.qvf", entries)
        with QVFReader(path) as reader, closing(pv.Plotter(off_screen=True)) as plotter:
            state = SimpleNamespace()
            _render_nci_surface(
                reader,
                plotter,
                ViewerState(),
                state,
                reader.get_section("rdg"),
                2.5,
                lattice,
                (2, 3, 1),
            )
            mesh = plotter.actors["volume_rdg"].mapper.dataset.copy()
            assert ("color" in mesh.point_data) == colored
            assert "Loaded NCI" in state.status_message
            meshes.append(mesh)
    assert meshes[0].n_points == meshes[1].n_points
    np.testing.assert_allclose(meshes[0].bounds, meshes[1].bounds, atol=1e-6)
    np.testing.assert_allclose(
        np.sort(meshes[0].points, axis=0), np.sort(meshes[1].points, axis=0), atol=1e-6
    )
    assert meshes[1].bounds[1] - meshes[1].bounds[0] > 5
    assert meshes[1].bounds[3] - meshes[1].bounds[2] < 3
    assert meshes[1].n_points == 108


@pytest.mark.parametrize("mismatch", ["origin", "voxel_vectors"])
def test_browser_density_requires_same_coordinates_not_only_shape(tmp_path, mismatch):
    grid, rdg, _ = _plane_fields()
    density_grid = GridData(grid.origin.copy(), grid.voxel_vectors.copy(), grid.shape)
    if mismatch == "origin":
        density_grid.origin[0] += 1
    else:
        density_grid.voxel_vectors = density_grid.voxel_vectors[[1, 0, 2]]
    rho = np.ones(grid.shape) * 0.01
    path = build_qvf(
        tmp_path / "mismatch.qvf",
        [
            _volume_entry("rdg", "volume.rdg", grid, rdg),
            _volume_entry("rho", "volume.density", density_grid, rho),
        ],
    )
    with QVFReader(path) as reader, closing(pv.Plotter(off_screen=True)) as plotter:
        state = SimpleNamespace()
        _render_nci_surface(
            reader, plotter, ViewerState(), state, reader.get_section("rdg"), 1.5, None, (1, 1, 1)
        )
        mesh = plotter.actors["volume_rdg"].mapper.dataset
        assert "density grid mismatch; uncolored" in state.status_message
        assert "color" not in mesh.point_data
        points = mesh.points / 0.529177210903
        np.testing.assert_allclose(points[:, 0] - 0.5 * points[:, 1], 1.5, atol=1e-6)


def test_public_wrap_keeps_multiple_axes_and_bohr_contract():
    from vibeview.renderers.volume import wrap_grid_to_center

    grid = GridData(np.zeros(3), np.eye(3), (8, 8, 8))
    data = np.zeros(grid.shape)
    data[0, 1, 7] = 1
    expected = np.zeros(grid.shape)
    expected[4, 4, 4] = 1
    result, rolled = wrap_grid_to_center(
        data, grid, (True, True, True), np.eye(3) * 8 * 0.529177210903
    )
    assert rolled
    np.testing.assert_array_equal(result, expected)
    assert data[0, 1, 7] == 1 and np.count_nonzero(data) == 1
