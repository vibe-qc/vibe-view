"""Unit-cell geometry shared by every front end and exporter.

The edge enumeration of the cell spanned by the *periodic* lattice vectors
was written four times (browser, terminal, POV-Ray, reaction path) with
two lattice conventions, the supercell translation grid three times, and
the minimum-image search lived in the browser renderer while the terminal
imported it privately. They live here once.

LATTICE CONVENTION: every function takes lattice vectors as **rows**
(``lattice[0]`` is a; ``cart = frac @ lattice``), which is how the
``structure`` section stores them. A ``reaction.path`` section stores
vectors as columns in bohr; :func:`reaction_lattice_rows_angstrom` converts
that once so no caller has to remember the transpose.

Axes outside ``pbc`` are synthesized bookkeeping for the AO integrals, not
cell edges: they are never drawn, never tiled and never wrapped. See
``StructureData``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

import numpy as np

from vibeview.qvf import clamp_replication
from vibeview.units import BOHR_TO_ANGSTROM

if TYPE_CHECKING:
    import pyvista as pv

Image = tuple[int, int, int]
ALL_PERIODIC: tuple[bool, bool, bool] = (True, True, True)


def periodic_vectors(
    lattice: Sequence[Sequence[float]] | np.ndarray, pbc: Sequence[bool] = ALL_PERIODIC
) -> np.ndarray:
    """The rows of ``lattice`` that are real cell edges, shape ``(n, 3)``."""
    rows = np.asarray(lattice, dtype=float).reshape(3, 3)
    keep = [i for i in range(3) if i < len(pbc) and pbc[i]]
    return rows[keep] if keep else np.zeros((0, 3))


def cell_edges(
    lattice: Sequence[Sequence[float]] | np.ndarray,
    pbc: Sequence[bool] = ALL_PERIODIC,
    origin: Sequence[float] | np.ndarray = (0.0, 0.0, 0.0),
) -> tuple[np.ndarray, np.ndarray]:
    """Start and end points of the cell's edges, each ``(n_edges, 3)``.

    Corners are every subset-sum of the periodic vectors and an edge joins
    two corners that differ by exactly one vector: 12 edges for a crystal,
    4 for a slab, 1 for a polymer, none for a molecule.
    """
    vecs = periodic_vectors(lattice, pbc)
    n = len(vecs)
    base = np.asarray(origin, dtype=float)
    if n == 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    corners = [
        base + sum((vecs[k] for k in range(n) if mask & (1 << k)), np.zeros(3))
        for mask in range(1 << n)
    ]
    starts, ends = [], []
    for mask in range(1 << n):
        for k in range(n):
            if not mask & (1 << k):
                starts.append(corners[mask])
                ends.append(corners[mask | (1 << k)])
    return np.array(starts), np.array(ends)


def cell_wireframe(
    lattice: Sequence[Sequence[float]] | np.ndarray,
    pbc: Sequence[bool] = ALL_PERIODIC,
    origin: Sequence[float] | np.ndarray = (0.0, 0.0, 0.0),
) -> pv.PolyData | None:
    """The cell's edges as one PyVista line set, or None for a molecule."""
    import pyvista as pv

    starts, ends = cell_edges(lattice, pbc, origin)
    if len(starts) == 0:
        return None
    points = np.vstack([starts, ends])
    n = len(starts)
    lines = np.column_stack([np.full(n, 2), np.arange(n), np.arange(n) + n]).ravel()
    return pv.PolyData(points, lines=lines)


def supercell_offsets(
    lattice: Sequence[Sequence[float]] | np.ndarray | None,
    pbc: Sequence[bool] | None,
    replication: Sequence[int],
) -> np.ndarray:
    """Translation vectors of a ``replication`` tiling, periodic axes only.

    Shape ``(n_images, 3)``; the first row is the zero offset. A molecule or
    a non-periodic axis is never tiled, whatever the requested count.
    """
    if lattice is None:
        return np.zeros((1, 3))
    source = list(pbc) if pbc is not None else list(ALL_PERIODIC)
    flags = (bool(source[0]), bool(source[1]), bool(source[2]))
    counts = clamp_replication(
        (int(replication[0]), int(replication[1]), int(replication[2])), flags
    )
    rows = np.asarray(lattice, dtype=float).reshape(3, 3)
    grid = np.array(list(np.ndindex(counts)), dtype=float)
    offsets: np.ndarray = np.asarray(grid @ rows, dtype=float)
    return offsets


def minimum_image(
    starts: np.ndarray,
    ends: np.ndarray,
    lattice: Sequence[Sequence[float]] | np.ndarray,
    pbc: Sequence[bool] = ALL_PERIODIC,
) -> np.ndarray:
    """For each start/end pair, the periodic image of ``end`` nearest ``start``.

    Vectorised over ``(n, 3)`` arrays. Rounding the fractional offset alone
    only yields the nearest image in near-orthogonal cells; in a skewed
    (hexagonal, triclinic) cell it can pick a farther one, so the rounded
    guess is refined by searching the surrounding images. Only periodic
    axes are wrapped or searched.
    """
    rows = np.asarray(lattice, dtype=float).reshape(3, 3)
    inverse = np.linalg.inv(rows)
    a = np.asarray(starts, dtype=float).reshape(-1, 3)
    b = np.asarray(ends, dtype=float).reshape(-1, 3)
    flags = np.array([bool(v) for v in pbc] + [False] * (3 - len(pbc)))[:3]
    frac = (b - a) @ inverse
    shift = np.where(flags, np.round(frac), 0.0)
    base = a + (frac - shift) @ rows
    best = base.copy()
    best_d2 = np.sum((best - a) ** 2, axis=1)
    spans = [(-1, 0, 1) if flag else (0,) for flag in flags]
    for da in spans[0]:
        for db in spans[1]:
            for dc in spans[2]:
                if (da, db, dc) == (0, 0, 0):
                    continue
                candidate = base + np.array([da, db, dc], dtype=float) @ rows
                d2 = np.sum((candidate - a) ** 2, axis=1)
                closer = d2 < best_d2 - 1e-12
                best[closer] = candidate[closer]
                best_d2[closer] = d2[closer]
    nearest: np.ndarray = best
    return nearest


def bond_far_endpoints(
    positions: np.ndarray,
    bonds: Sequence[Sequence[object]],
    lattice: Sequence[Sequence[float]] | np.ndarray | None,
    pbc: Sequence[bool] = ALL_PERIODIC,
) -> tuple[np.ndarray, np.ndarray]:
    """Start and end points of ``bonds``, ends placed where they are drawn.

    A bond is ``(i, j, order, image)``; a shorter tuple has no stated image.

    A nonzero image is authoritative: inference recorded which periodic copy
    of atom *j* the bond reaches, and two bonds between the same pair can
    carry different images. A zero image means "not stated" rather than
    "in-cell" (explicit bonds carry none), so those ends fall back to the
    minimum image, which is a no-op for a bond that really is in-cell.
    """
    pos = np.asarray(positions, dtype=float).reshape(-1, 3)
    if not bonds:
        return np.zeros((0, 3)), np.zeros((0, 3))
    i = np.asarray([int(b[0]) for b in bonds], dtype=np.int64)  # type: ignore[call-overload]
    j = np.asarray([int(b[1]) for b in bonds], dtype=np.int64)  # type: ignore[call-overload]
    stated_images = [
        tuple(int(v) for v in cast(Sequence[int], b[3]))
        if len(b) > 3 and b[3] is not None
        else (0, 0, 0)
        for b in bonds
    ]
    images = np.asarray(stated_images, dtype=np.float64).reshape(-1, 3)
    starts = pos[i]
    ends = pos[j].copy()
    if lattice is None or not any(pbc):
        return starts, ends
    rows = np.asarray(lattice, dtype=float).reshape(3, 3)
    stated = np.any(images != 0, axis=1)
    ends[stated] = ends[stated] + images[stated] @ rows
    if (~stated).any():
        ends[~stated] = minimum_image(starts[~stated], ends[~stated], rows, pbc)
    return starts, ends


def reaction_lattice_rows_angstrom(lattice_columns_bohr: np.ndarray | None) -> np.ndarray | None:
    """A ``reaction.path`` lattice (columns, bohr) as rows in Å, or None."""
    if lattice_columns_bohr is None:
        return None
    return np.asarray(lattice_columns_bohr, dtype=float).reshape(3, 3).T * BOHR_TO_ANGSTROM
