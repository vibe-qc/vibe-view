"""Minimum-image roll-to-center for periodic (cyclic-cluster) grids.

A Wannier function / crystalline orbital centred near a cell face wraps to
the opposite face on the torus and reads as broken. ``wrap_grid_to_center``
rolls the periodic grid so the feature sits whole in the middle of the cell.
"""

from __future__ import annotations

import numpy as np

from vibeview.qvf import GridData
from vibeview.renderers.volume import _circular_centroid_index, wrap_grid_to_center

_BOHR = 0.529177210903


def _torus_grid(n, L_ang, w_ang=5.0, nt=9):
    """Orthogonal torus grid: n voxels over period L_ang along x (so
    n*voxel == L, a true torus), nt voxels over w_ang along y and z."""
    step_x = L_ang / n / _BOHR  # bohr, so n*step*_BOHR == L_ang
    step_t = w_ang / nt / _BOHR
    voxel = np.diag([step_x, step_t, step_t])
    origin = np.zeros(3)
    return GridData(origin=origin, voxel_vectors=voxel, shape=(n, nt, nt))


def _blob_at_frac(n, nt, fx):
    """Gaussian blob on the x-torus centred at fractional position fx,
    uniform in y,z. Periodic distance so a near-face blob wraps."""
    k = np.arange(n)
    d = np.abs(((k - fx * n + n / 2) % n) - n / 2)  # periodic index distance
    prof = np.exp(-(d**2) / (2 * (n * 0.05) ** 2))
    return np.broadcast_to(prof[:, None, None], (n, nt, nt)).copy()


def test_circular_centroid_handles_wraparound():
    n = 40
    # A blob straddling index 0 (half near 0, half near n-1).
    prof = np.zeros(n)
    prof[[0, 1, n - 1, n - 2]] = 1.0
    idx, R = _circular_centroid_index(prof)
    # Circular mean sits near 0 / n, NOT near the naive linear mean (~n/2).
    assert min(idx, n - idx) < 2.0
    assert R > 0.9  # sharply localized


def test_uniform_field_has_zero_resultant():
    idx, R = _circular_centroid_index(np.ones(50))
    assert R < 1e-6  # delocalized: no meaningful centre


def test_wrap_recenters_face_straddling_blob():
    n, nt, L = 60, 9, 10.0
    grid = _torus_grid(n, L)
    data = _blob_at_frac(n, nt, fx=0.97)  # near the +x face, wraps to -x
    lattice = np.array([[L, 0, 0], [0, 5.0, 0], [0, 0, 5.0]], float)

    # Before: amplitude piles up at BOTH faces (split / broken).
    xprof = np.abs(data).sum(axis=(1, 2))
    assert xprof[0] > 0.5 * xprof.max() and xprof[-1] > 0.5 * xprof.max()

    out, rolled = wrap_grid_to_center(data, grid, [True, False, False], lattice)
    assert rolled
    xo = np.abs(out).sum(axis=(1, 2))
    # After: single contiguous blob centred; both faces now near-empty.
    assert np.argmax(xo) == n // 2 or abs(int(np.argmax(xo)) - n // 2) <= 1
    assert xo[0] < 0.1 * xo.max() and xo[-1] < 0.1 * xo.max()
    assert np.isclose(out.sum(), data.sum())  # roll conserves mass


def test_delocalized_field_not_rolled():
    n, nt, L = 40, 9, 8.0
    grid = _torus_grid(n, L)
    data = np.ones((n, nt, nt))  # uniform crystalline-orbital-like field
    lattice = np.array([[L, 0, 0], [0, 5.0, 0], [0, 0, 5.0]], float)
    out, rolled = wrap_grid_to_center(data, grid, [True, False, False], lattice)
    assert not rolled
    assert np.array_equal(out, data)


def test_molecular_grid_not_rolled():
    """A grid that does NOT span the cell (molecular cube, n*voxel < L) is
    left untouched — rolling it would wrap padding into the field."""
    n, nt, L = 60, 9, 10.0
    grid = _torus_grid(n, L)  # spans exactly L...
    lattice = np.array([[2.0 * L, 0, 0], [0, 5.0, 0], [0, 0, 5.0]], float)  # ...cell is 2L
    data = _blob_at_frac(n, nt, fx=0.97)
    out, rolled = wrap_grid_to_center(data, grid, [True, False, False], lattice)
    assert not rolled
    assert np.array_equal(out, data)


def test_non_periodic_axis_untouched():
    n, nt, L = 50, 9, 9.0
    grid = _torus_grid(n, L)
    data = _blob_at_frac(n, nt, fx=0.98)
    lattice = np.array([[L, 0, 0], [0, 5.0, 0], [0, 0, 5.0]], float)
    # pbc all False -> nothing rolls even though a blob straddles.
    out, rolled = wrap_grid_to_center(data, grid, [False, False, False], lattice)
    assert not rolled
    assert np.array_equal(out, data)


def test_build_isosurface_mesh_wrap_recenters_contour():
    """End-to-end through the contour path: a torus blob at the +x face
    contours as two face-hugging pieces with wrap off, and one centred
    blob with wrap on (the make_mesh/build_isosurface_mesh threading)."""
    from vibeview.renderers.volume import build_isosurface_mesh

    n, nt, L = 60, 11, 10.0
    grid = _torus_grid(n, L, w_ang=6.0, nt=nt)
    data = _blob_at_frac(n, nt, fx=0.96)
    lattice = np.array([[L, 0, 0], [0, 6.0, 0], [0, 0, 6.0]], float)

    off = build_isosurface_mesh(data, grid, 0.4, lattice_vectors=lattice)
    on = build_isosurface_mesh(
        data, grid, 0.4, lattice_vectors=lattice,
        wrap_to_center=True, pbc=[True, False, False],
    )
    assert off is not None and on is not None and off.n_points > 0 and on.n_points > 0
    # Wrap off: the surface straddles both faces (x spans nearly the whole cell).
    assert off.bounds[0] < 0.15 * L and off.bounds[1] > 0.85 * L
    # Wrap on: one blob near the cell centre (x-centroid ~ L/2, tight).
    cx = float(on.points[:, 0].mean())
    assert abs(cx - L / 2) < 0.15 * L
    assert (on.bounds[1] - on.bounds[0]) < 0.6 * L
