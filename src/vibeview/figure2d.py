"""The flat structure figure shared by the SVG and PDF exports.

Both draw the same picture: atoms as CPK-coloured circles on the two
leading principal axes of the structure, bonds as lines, sized by a
style. Colours and radii come from :mod:`vibeview.elements`, the tables
the viewer draws with, so a figure and a screenshot agree on every element.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from vibeview.bonds import bond_endpoints, structure_bonds
from vibeview.elements import UNKNOWN_COLOR, covalent_radius, cpk_hex, vdw_radius

#: Colour of every bond line in the flat figures.
BOND_COLOR = "#666666"

#: Drawn atom radius as a fraction of the covalent radius (ball-and-stick).
BALL_FRACTION = 0.45
#: Drawn atom radius as a fraction of the van der Waals radius (space-filling).
SPACE_FILLING_FRACTION = 0.8
#: Drawn atom radius, in Å, for the stick styles.
STICK_ATOM_RADIUS = 0.06

Circle = tuple[np.ndarray, float, str]
Segment = tuple[np.ndarray, np.ndarray]


def projection(positions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Centre and the two leading PCA axes (columns) of an atom cloud.

    A fixed XY projection collapses a planar molecule lying in a plane that
    contains z (water in the y-z plane becomes a vertical line). Projecting
    onto the two largest-variance axes keeps any planar molecule spread out
    in the drawing plane. Zero or one atom projects onto the x-y plane.
    """
    pos = np.asarray(positions, dtype=float).reshape(-1, 3)
    if pos.shape[0] < 2:
        return (pos.mean(axis=0) if len(pos) else np.zeros(3)), np.eye(3)[:, :2]
    centre = pos.mean(axis=0)
    centred = pos - centre
    _evals, evecs = np.linalg.eigh(centred.T @ centred)  # ascending eigenvalues
    return centre, evecs[:, ::-1][:, :2]


def atom_radius(atomic_number: int, style: str) -> float:
    """Drawn radius in Å for one atom in ``style``."""
    if style == "space_filling":
        return vdw_radius(atomic_number) * SPACE_FILLING_FRACTION
    if style == "ball_and_stick":
        return covalent_radius(atomic_number, 0.76) * BALL_FRACTION
    return STICK_ATOM_RADIUS


def flat_figure(sdata: Any, style: str) -> tuple[list[Circle], list[Segment]]:
    """Projected atoms ``(xy, radius, colour)`` and bond segments ``(xy, xy)``.

    Bonds are the viewer's connectivity; a bond through a periodic cell face
    is drawn to the image it reaches, as the viewer and the POV-Ray export
    draw it, rather than left out. Space-filling draws no bonds.
    """
    atoms = sdata.atoms
    positions = np.array([a.position for a in atoms], dtype=float).reshape(len(atoms), 3)
    centre, axes = projection(positions)
    coords2d = (positions - centre) @ axes
    numbers = [int(atom.atomic_number or 0) for atom in atoms]
    circles = [
        (coords2d[k], atom_radius(z, style), cpk_hex(z)) for k, z in enumerate(numbers)
    ]
    segments: list[Segment] = []
    if style != "space_filling":
        lattice = getattr(sdata, "lattice_vectors", None)
        for bond in structure_bonds(sdata):
            start, end = bond_endpoints(positions, bond, lattice)
            segments.append(((start - centre) @ axes, (end - centre) @ axes))
    return circles, segments


def figure_bounds(
    circles: list[Circle], segments: list[Segment], margin: float = 1.5
) -> tuple[float, float, float, float]:
    """``(x_min, x_max, y_min, y_max)`` around every circle centre and segment end."""
    points = [c[0] for c in circles] + [p for seg in segments for p in seg]
    if not points:
        return -margin, margin, -margin, margin
    arr = np.asarray(points)
    return (
        float(arr[:, 0].min()) - margin,
        float(arr[:, 0].max()) + margin,
        float(arr[:, 1].min()) - margin,
        float(arr[:, 1].max()) + margin,
    )


__all__ = [
    "BOND_COLOR",
    "UNKNOWN_COLOR",
    "atom_radius",
    "figure_bounds",
    "flat_figure",
    "projection",
]
