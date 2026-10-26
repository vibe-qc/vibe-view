"""Cartesian mode arrows shared by the live viewport and image/video export."""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from vibeview.vibration_amplitude import normalized_displacement

if TYPE_CHECKING:
    import pyvista as pv

ARROW_PREFIX = "vib_arrow_"


def arrow_vectors(
    positions: np.ndarray, displacement: np.ndarray, amplitude: float = 1.0,
    cutoff: float = 0.01,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Indices, atom origins and positive-phase vectors, with lengths in Å.

    Origins follow the current animated atom positions. Directions always show
    the selected eigenvector's positive phase, including when playback pauses
    at equilibrium; they do not flip or disappear as the oscillation crosses zero.
    """
    if not np.isfinite(amplitude) or amplitude < 0 or not np.isfinite(cutoff) or cutoff < 0:
        raise ValueError("Arrow amplitude and cutoff must be finite and non-negative")
    origins = np.asarray(positions, dtype=float)
    vectors = normalized_displacement(displacement, amplitude)
    if origins.shape != vectors.shape or not np.isfinite(origins).all():
        raise ValueError("Arrow origins must match the mode's finite atom positions")
    lengths = np.linalg.norm(vectors, axis=1)
    indices = np.flatnonzero((lengths >= cutoff) & (lengths > 1e-12))
    return indices, origins[indices].copy(), vectors[indices]


def add_mode_arrows(
    plotter: pv.Plotter, positions: np.ndarray, displacement: np.ndarray, *,
    amplitude: float = 1.0, cutoff: float = 0.01, color: str = "#ffb300",
    thickness: float = 0.025, atom_radii: np.ndarray | None = None,
    prefix: str = ARROW_PREFIX,
) -> list[str]:
    """Add polygonal annotations, retaining the mode vectors and their lengths.

    ``prefix`` names the actors; force arrows use their own (#159), so
    clearing one group never removes the other.

    When displayed atom radii are supplied, tails start just outside their
    own spheres along the positive-phase direction. The offset is annotation
    placement only: no vector is enlarged and no atom position is changed.
    """
    import pyvista as pv

    if not np.isfinite(thickness) or thickness <= 0:
        raise ValueError("Arrow thickness must be finite and positive (angstrom)")
    indices, origins, vectors = arrow_vectors(positions, displacement, amplitude, cutoff)
    if atom_radii is not None:
        radii = np.asarray(atom_radii, dtype=float)
        if (radii.shape != (len(positions),) or not np.isfinite(radii).all()
                or np.any(radii < 0)):
            raise ValueError("Displayed atom radii must be finite non-negative per-atom values")
        lengths = np.linalg.norm(vectors, axis=1)
        # Keep a small gap so the finite shaft does not intersect its own sphere.
        offsets = np.where(radii[indices] > 0, radii[indices] + thickness, 0)
        origins += vectors / lengths[:, None] * offsets[:, None]
    names = []
    for index, origin, vector in zip(indices, origins, vectors, strict=True):
        length = float(np.linalg.norm(vector))
        radius = min(thickness / length, 0.15)
        mesh = pv.Arrow(
            start=origin, direction=vector, scale=length,
            shaft_radius=radius, tip_radius=2 * radius, tip_length=0.25,
            shaft_resolution=12, tip_resolution=12,
        )
        name = f"{prefix}{index}"
        plotter.add_mesh(mesh, color=color, name=name, smooth_shading=True,
                         show_scalar_bar=False, pickable=False)
        names.append(name)
    return names
