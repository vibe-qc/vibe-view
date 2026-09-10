"""Wannier-centre overlay.

Cyclic-cluster (and molecular) runs can carry per-orbital Wannier-function
centroids and spreads as an ``x_ccm.wannier_centers`` vendor section. The
QVF spec lists vendor (``x_*``) sections but leaves rendering to consumers
that understand them; vibe-view understands this one and draws a marker at
each centroid, sized by the function's spread.

Section payload (one JSON member, either a bare list or ``{"centers": [...]}``):

    { "center": [x, y, z],   # ANGSTROM, same frame as structure atoms
      "spread": 1.83,        # Å² (state units); scales the marker
      "orbital_ref": "co_06",# optional: id of a linked volume.orbital
      "label": "C1-C2 sigma" # optional
    }
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader

WANNIER_KIND = "x_ccm.wannier_centers"


@dataclass
class WannierCentre:
    center: np.ndarray  # [3] Å
    spread: float  # Å² (marker scale); 1.0 when unspecified
    label: str | None = None
    orbital_ref: str | None = None


def find_wannier_section(reader: "QVFReader"):
    """Return the wannier-centres section, or None."""
    return next((s for s in reader.sections if s.kind == WANNIER_KIND), None)


def read_wannier_centres(reader: "QVFReader") -> list[WannierCentre]:
    """Parse the ``x_ccm.wannier_centers`` section into centres.

    Returns ``[]`` when the section is absent or empty. Malformed entries
    (missing ``center``) are skipped rather than raising, so one bad row
    never blanks the overlay.
    """
    section = find_wannier_section(reader)
    if section is None or not section.members:
        return []
    # Prefer a member named "centers"/"wannier_centers", else the first.
    member = None
    for name in ("centers", "wannier_centers"):
        if name in section.members:
            member = name
            break
    if member is None:
        member = next(iter(section.members))

    raw = reader._read_json_member(section.id, member)
    items = raw.get("centers", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []

    out: list[WannierCentre] = []
    for it in items:
        if not isinstance(it, dict) or "center" not in it:
            continue
        pos = np.asarray(it["center"], dtype=float)
        if pos.shape != (3,):
            continue
        spread = it.get("spread", 1.0)
        try:
            spread = float(spread)
        except (TypeError, ValueError):
            spread = 1.0
        out.append(
            WannierCentre(
                center=pos,
                spread=spread if spread > 0 else 1.0,
                label=it.get("label"),
                orbital_ref=it.get("orbital_ref"),
            )
        )
    return out


def build_wannier_glyphs(centres: list[WannierCentre], *, base_radius: float = 0.35):
    """Sphere markers at each centre, radius scaled by sqrt(spread).

    Returns a single glyph-instanced ``pv.PolyData`` (one draw call), or
    None when there are no centres. Radius uses ``sqrt(spread)`` (spread is
    a variance-like Å²), normalised so the median-spread marker has
    ``base_radius`` and outliers stay legible.
    """
    import pyvista as pv

    if not centres:
        return None
    pts = np.array([c.center for c in centres], dtype=float)
    spreads = np.array([c.spread for c in centres], dtype=float)
    positive = spreads[spreads > 0]
    median = float(np.median(positive)) if positive.size else 1.0
    radii = base_radius * np.sqrt(np.clip(spreads, 1e-6, None) / median)

    poly = pv.PolyData(pts)
    poly["radius"] = radii
    return poly.glyph(
        geom=pv.Sphere(radius=1.0, theta_resolution=16, phi_resolution=16),
        scale="radius",
        orient=False,
    )
