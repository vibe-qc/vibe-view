"""renderers.cell is the one unit-cell geometry for every front end."""

from __future__ import annotations

import numpy as np
import pytest

from vibeview.renderers import cell

CUBIC = np.eye(3) * 10.0
HEX = np.array([[1.0, 0.0, 0.0], [-0.5, np.sqrt(3) / 2, 0.0], [0.0, 0.0, 5.0]]) * 4.0


@pytest.mark.parametrize(
    ("pbc", "n_edges"),
    [
        ((True, True, True), 12),
        ((True, True, False), 4),
        ((True, False, False), 1),
        ((False,) * 3, 0),
    ],
)
def test_cell_edges_count_follows_the_periodic_axes(pbc, n_edges) -> None:
    starts, ends = cell.cell_edges(CUBIC, pbc)
    assert starts.shape == (n_edges, 3) and ends.shape == (n_edges, 3)
    if n_edges:
        # Every edge is one lattice vector long.
        assert np.allclose(np.linalg.norm(ends - starts, axis=1), 10.0)


def test_cell_edges_origin_and_wireframe() -> None:
    starts, _ends = cell.cell_edges(CUBIC, origin=(1.0, 2.0, 3.0))
    assert np.allclose(starts.min(axis=0), (1.0, 2.0, 3.0))
    mesh = cell.cell_wireframe(CUBIC)
    assert mesh is not None and mesh.n_lines == 12 and mesh.n_points == 24
    assert cell.cell_wireframe(CUBIC, (False, False, False)) is None


def test_supercell_offsets() -> None:
    offsets = cell.supercell_offsets(CUBIC, (True, True, True), (2, 1, 3))
    assert offsets.shape == (6, 3)
    assert np.allclose(offsets[0], 0.0)
    assert np.allclose(offsets[-1], (10.0, 0.0, 20.0))
    # A slab is never tiled along its synthesized normal.
    assert cell.supercell_offsets(CUBIC, (True, True, False), (2, 2, 2)).shape == (4, 3)
    assert cell.supercell_offsets(None, None, (3, 3, 3)).shape == (1, 3)


def test_minimum_image_wraps_only_periodic_axes_and_handles_skew() -> None:
    starts = np.array([[0.5, 5.0, 5.0]])
    ends = np.array([[9.5, 5.0, 5.0]])
    wrapped = cell.minimum_image(starts, ends, CUBIC)
    assert np.allclose(wrapped, [[-0.5, 5.0, 5.0]])
    unwrapped = cell.minimum_image(starts, ends, CUBIC, (False, True, True))
    assert np.allclose(unwrapped, ends)
    # Hexagonal cell: the naive rounded image is farther than the true one.
    a = np.array([[0.0, 0.0, 0.0]])
    b = np.array([[0.0, 0.0, 0.0]]) + 0.55 * HEX[0] + 0.55 * HEX[1]
    nearest = cell.minimum_image(a, b, HEX)
    naive = b - np.round((b - a) @ np.linalg.inv(HEX)) @ HEX
    assert np.linalg.norm(nearest - a) <= np.linalg.norm(naive - a) + 1e-9


def test_bond_far_endpoints_honour_images_and_fall_back_to_minimum_image() -> None:
    positions = np.array([[0.5, 5.0, 5.0], [9.5, 5.0, 5.0]])
    stated = [(0, 1, 1.0, (-1, 0, 0))]
    starts, ends = cell.bond_far_endpoints(positions, stated, CUBIC)
    assert np.allclose(ends, [[-0.5, 5.0, 5.0]])
    unstated = [(0, 1, 1.0, (0, 0, 0))]
    _starts, ends = cell.bond_far_endpoints(positions, unstated, CUBIC)
    assert np.allclose(ends, [[-0.5, 5.0, 5.0]])
    _starts, ends = cell.bond_far_endpoints(positions, unstated, None)
    assert np.allclose(ends, [[9.5, 5.0, 5.0]])
    assert cell.bond_far_endpoints(positions, [], CUBIC)[0].shape == (0, 3)


def test_reaction_lattice_conversion() -> None:
    columns_bohr = np.array([[1.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 3.0]]).T
    rows = cell.reaction_lattice_rows_angstrom(columns_bohr)
    assert rows is not None
    assert np.allclose(rows[1], (0.0, 2.0 * cell.BOHR_TO_ANGSTROM, 0.0))
    assert cell.reaction_lattice_rows_angstrom(None) is None
