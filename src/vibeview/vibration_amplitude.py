"""One vibration-amplitude convention for every viewer.

A normal mode's displacement vectors come from the producer in whatever
normalisation it uses (mass-weighted, unit-norm, Cartesian), so drawing
them raw makes a stiff X-H stretch and a soft torsion look very different
in size, and made the browser and the terminal disagree (audit #38-28). All
front ends instead scale each mode so its largest *atomic* excursion (the
longest per-atom displacement vector) equals
``DEFAULT_PEAK_EXCURSION_ANGSTROM`` times a user amplitude scale (1.0 by
default). This changes rendering only; the archive's displacement data is
never modified.
"""

from __future__ import annotations

import numpy as np

# Peak atomic excursion, in Å, at amplitude scale 1.0.
DEFAULT_PEAK_EXCURSION_ANGSTROM = 0.6


def peak_excursion(scale: float = 1.0) -> float:
    """Largest atomic excursion (Å) drawn at user amplitude ``scale``."""
    return DEFAULT_PEAK_EXCURSION_ANGSTROM * float(scale)


def normalized_displacement(displacement: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """``displacement`` ([n_atoms, 3]) rescaled to the shared convention.

    The longest per-atom vector becomes ``peak_excursion(scale)`` Å. A mode
    with no displacement (all zeros, or non-finite) returns zeros instead of
    dividing by zero.
    """
    disp = np.asarray(displacement, dtype=np.float64).reshape(-1, 3)
    norms = np.linalg.norm(disp, axis=1) if disp.size else np.zeros(0)
    peak = float(np.max(norms)) if norms.size else 0.0
    if not np.isfinite(peak) or peak <= 1e-12:
        return np.zeros_like(disp)
    out: np.ndarray = disp * (peak_excursion(scale) / peak)
    return out
