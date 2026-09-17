"""Shared energy-window logic for the band-structure and DOS charts.

All-electron periodic archives carry core eigenvalues hundreds or
thousands of eV below E_F. Autoscaling the energy axis to the full data
range then squeezes the valence and conduction states — the part a
reader actually looks at — into a few pixels around the E_F line, so the
chart is unreadable without a manual drag-zoom (#26).

This module supplies the two pieces the bands, DOS and combined panels
share: a default *valence window* for core-dominated archives, and a
parser for the explicit windows that arrive from a ``viewer_defaults``
hint or from the viewer's own minimum/maximum controls.

The default window is a display convention, not a physical claim. It
rests on one observation: in an all-electron spectrum the core shells sit
below the valence manifold behind energy gaps of hundreds of eV, whereas
gaps *inside* the valence and conduction region are at most a few tens of
eV (the widest band gaps of any solid are around 20 eV). Grouping the
states by those gaps and keeping only the groups near E_F therefore
separates core from valence without needing to know the element, the
basis or the method. Every threshold below is deliberately loose, and the
window is only applied when it is a large improvement over autoscale — a
file with no core states keeps the axis it has today.
"""

from __future__ import annotations

import numpy as np

# An empty stretch wider than this (eV) starts a new group of states.
# Comfortably above the widest band gap of any real solid (~20 eV for
# solid neon), so a genuine valence/conduction gap never splits a group;
# far below a core/valence separation, which runs to hundreds of eV.
GROUP_GAP_EV = 30.0

# Groups whose nearest edge is further than this (eV) from E_F are read as
# core or semicore and left out of the default window. Chosen to clear the
# widest minimal-basis valence manifolds — the silicon STO-3G case in #26
# spans 106 eV — while excluding even shallow core levels, which for
# every element past lithium bind at more than 50 eV.
VALENCE_PROBE_EV = 150.0

# Apply the default window only when the full range is at least this many
# times wider than it. Without a factor this large, a file whose spectrum
# is entirely valence would get a window that changes nothing but removes
# the reader's familiar autoscale.
MIN_NARROWING = 3.0

# Padding added to each side of a computed window, as a fraction of its
# width, so the outermost states are not drawn on the axis line.
PAD_FRACTION = 0.04

EnergyWindow = tuple[float, float]


def parse_energy_window(value: object) -> EnergyWindow | None:
    """Normalize an energy window from a manifest hint or a UI control.

    Accepts ``[min, max]`` / ``(min, max)`` and the mapping forms
    ``{"min": …, "max": …}`` and ``{"emin": …, "emax": …}``. Returns
    ``None`` for anything else, for non-finite numbers and for an empty
    or inverted range — a producer's malformed hint must not be able to
    blank the chart.
    """
    lo: object
    hi: object
    if isinstance(value, dict):
        if "min" in value or "max" in value:
            lo, hi = value.get("min"), value.get("max")
        else:
            lo, hi = value.get("emin"), value.get("emax")
    elif isinstance(value, list | tuple) and len(value) == 2:
        lo, hi = value
    else:
        return None

    if isinstance(lo, bool) or isinstance(hi, bool):
        return None
    if not isinstance(lo, int | float) or not isinstance(hi, int | float):
        return None
    lo_f, hi_f = float(lo), float(hi)
    if not (np.isfinite(lo_f) and np.isfinite(hi_f)):
        return None
    if hi_f <= lo_f:
        return None
    return lo_f, hi_f


def _groups(sorted_energies: np.ndarray, gap: float) -> list[tuple[float, float]]:
    """Split ascending energies into (low, high) runs separated by ``gap``."""
    breaks = np.nonzero(np.diff(sorted_energies) > gap)[0]
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [len(sorted_energies) - 1]))
    return [
        (float(sorted_energies[s]), float(sorted_energies[e]))
        for s, e in zip(starts, ends, strict=True)
    ]


def auto_energy_window(
    energies: np.ndarray,
    *,
    full_range: tuple[float, float] | None = None,
    group_gap: float = GROUP_GAP_EV,
    probe: float = VALENCE_PROBE_EV,
    min_narrowing: float = MIN_NARROWING,
    pad_fraction: float = PAD_FRACTION,
) -> EnergyWindow | None:
    """Return a default valence window, or ``None`` to keep autoscale.

    ``energies`` are the occupied and virtual state energies **relative to
    E_F**, in eV: band eigenvalues for a band structure, the grid points
    carrying weight for a DOS (see :func:`dos_support`). ``full_range`` is
    the span the axis would otherwise autoscale to, which for a DOS is the
    whole energy grid rather than just its support; it defaults to the
    range of ``energies``.

    ``None`` means "nothing to do": too few states, no states near E_F, or
    a window that would not be meaningfully narrower than the full range.
    """
    e = np.asarray(energies, dtype=float).ravel()
    e = e[np.isfinite(e)]
    if e.size < 2:
        return None
    e.sort()

    if full_range is None:
        full_lo, full_hi = float(e[0]), float(e[-1])
    else:
        full_lo, full_hi = float(full_range[0]), float(full_range[1])
    full_span = full_hi - full_lo
    if not np.isfinite(full_span) or full_span <= 0.0:
        return None

    # Keep the groups that reach within `probe` of E_F; a group straddling
    # E_F is at distance zero.
    kept = [
        (lo, hi)
        for lo, hi in _groups(e, group_gap)
        if (0.0 if lo <= 0.0 <= hi else min(abs(lo), abs(hi))) <= probe
    ]
    if not kept:
        return None

    lo = min(g[0] for g in kept)
    hi = max(g[1] for g in kept)
    span = hi - lo
    if span <= 0.0:
        return None
    if span * min_narrowing > full_span:
        return None

    # Round the bounds: the viewer shows this window in its min/max fields,
    # and a field reading -55.837 while the axis sits at -55.83702237 would
    # move the chart the moment the reader commits the value it was shown.
    pad = span * pad_fraction
    window = (round(lo - pad, 3), round(hi + pad, 3))
    if window[1] <= window[0]:
        # States packed into less than a millielectronvolt: rounding has
        # collapsed the window, and a zero-width range is not an axis.
        return None
    return window


def dos_support(energies: np.ndarray, dos: np.ndarray, *, tol: float = 1e-3) -> np.ndarray:
    """Grid energies carrying non-negligible DOS weight.

    Spin and channel axes are summed in magnitude first, so a spin-down
    channel stored as a positive array — the renderers mirror it to
    negative x only at draw time — still counts as weight.
    """
    e = np.asarray(energies, dtype=float).ravel()
    d = np.abs(np.asarray(dos, dtype=float))
    while d.ndim > 1:
        d = d.sum(axis=0)
    if d.shape != e.shape:
        return e
    peak = float(d.max()) if d.size else 0.0
    if not np.isfinite(peak) or peak <= 0.0:
        return e
    return e[d > tol * peak]


def apply_axis_range(axis: dict, window: EnergyWindow | None) -> dict:
    """Add a Plotly ``range`` to an axis spec when a window is set."""
    if window is not None:
        axis["range"] = [window[0], window[1]]
    return axis


def resolve_window(
    explicit: EnergyWindow | None,
    *,
    auto: bool,
    compute,
) -> EnergyWindow | None:
    """Pick the window a renderer should draw with.

    An explicit window always wins. Otherwise ``compute()`` supplies the
    default when ``auto`` is set, and ``None`` (full autoscale) when it is
    not. ``compute`` is a callable so the default is not computed for a
    caller that has already chosen a window.
    """
    if explicit is not None:
        return explicit
    if not auto:
        return None
    return compute()
