"""Views for long, large structures: axis frame, cut-aways, property colours.

Built for nanotubes and other elongated systems (roadmap: carbon nanotubes)
but defined for any structure: the **axis** is the principal axis of the
atoms (of the carbon atoms when there are enough of them, so a metal slab
under a tube does not tilt it), and the **radius** is the median distance
of those atoms from it.

* :func:`cutaway_keep` removes part of the structure to show what is
  inside: half of the wall (``half``), everything but a slice across the
  axis (``section``), or everything past a point on the axis (``end``).
  Atoms near the axis (an encapsulated species) are always kept.
* :func:`property_values` gives one number per atom for colouring by
  coordination, distance from the axis, partial charge or bond order to a
  metal; :func:`property_colors` maps them onto a colour ramp.
* :func:`axis_camera` returns the camera for an orthographic view down the
  axis or side-on, looking at the cut face of a ``half`` cut-away.

Everything runs on the server before the scene is built, so the browser,
headless capture and figure export draw the same atoms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from vibeview.elements import SYMBOL_TO_Z

if TYPE_CHECKING:
    from vibeview.bonds import Bond
    from vibeview.qvf import QVFReader, StructureData

CUTAWAYS: tuple[str, ...] = ("off", "half", "section", "end")
#: Atom colour modes computed from the structure or its analysis sections.
PROPERTY_COLOR_MODES: tuple[str, ...] = (
    "coordination", "axis_distance", "charge", "metal_bond_order", "local_strain", "carbon_sites",
)
PROPERTY_LABELS = {
    "coordination": "Coordination number",
    "axis_distance": "Distance from the axis (Å)",
    "charge": "Partial charge (e)",
    "local_strain": "C–C bond stretch (%)",
    "carbon_sites": "Open ends, coordination defects and adducts",
    "metal_bond_order": "Bond order to a metal",
}
#: Half-thickness of a ``section`` cut-away (Angstrom).
SECTION_HALF_WIDTH = 2.5
#: Atoms closer to the axis than this fraction of the radius are never cut.
CORE_FRACTION = 0.6

# Non-metals and metalloids; every other element counts as a metal for
# "bond order to a metal".
_NON_METALS = {1, 2, 5, 6, 7, 8, 9, 10, 14, 15, 16, 17, 18, 32, 33, 34, 35, 36, 51, 52, 53,
               54, 85, 86}


def is_metal(z: int) -> bool:
    return z > 0 and z not in _NON_METALS


@dataclass(frozen=True)
class AxisFrame:
    """Centre, unit axis, a unit normal perpendicular to it, and radius."""

    center: np.ndarray
    axis: np.ndarray
    normal: np.ndarray
    radius: float
    half_length: float


def axis_frame(structure: StructureData) -> AxisFrame:
    """The principal-axis frame of *structure* (see the module docstring)."""
    positions = np.array([a.position for a in structure.atoms], dtype=float).reshape(-1, 3)
    if len(positions) == 0:
        raise ValueError("the structure has no atoms")
    carbon = np.array([a.symbol == "C" for a in structure.atoms])
    pts = positions[carbon] if carbon.sum() >= 12 else positions
    center = pts.mean(axis=0)
    if len(pts) < 3:
        axis = np.array([0.0, 0.0, 1.0])
    else:
        _, _, vt = np.linalg.svd(pts - center, full_matrices=False)
        axis = vt[0]
    # A fixed sign, so a view does not flip between rebuilds.
    if axis[int(np.argmax(np.abs(axis)))] < 0:
        axis = -axis
    normal = np.cross(axis, [0.0, 0.0, 1.0])
    if np.linalg.norm(normal) < 1e-6:
        normal = np.cross(axis, [0.0, 1.0, 0.0])
    normal /= np.linalg.norm(normal)
    along = (pts - center) @ axis
    radial = np.linalg.norm((pts - center) - np.outer(along, axis), axis=1)
    half = float(np.abs((positions - center) @ axis).max())
    return AxisFrame(center, axis / np.linalg.norm(axis), normal,
                     float(np.median(radial)) if len(radial) else 0.0, half)


def cutaway_keep(structure: StructureData, mode: str, position: float = 0.5) -> set[int]:
    """Indices of the atoms a ``mode`` cut-away keeps.

    ``position`` (0 to 1) places a ``section`` or an ``end`` cut along the
    axis; ``half`` keeps the side away from :attr:`AxisFrame.normal`.
    """
    n = len(structure.atoms)
    if mode == "off" or n == 0:
        return set(range(n))
    if mode not in CUTAWAYS:
        raise ValueError(f"cut-away must be one of {', '.join(CUTAWAYS)}, got {mode!r}")
    frame = axis_frame(structure)
    rel = np.array([a.position for a in structure.atoms], dtype=float) - frame.center
    along = rel @ frame.axis
    radial = np.linalg.norm(rel - np.outer(along, frame.axis), axis=1)
    core = radial < CORE_FRACTION * frame.radius
    at = (min(1.0, max(0.0, float(position))) * 2.0 - 1.0) * frame.half_length
    if mode == "half":
        keep = rel @ frame.normal <= 0.0
    elif mode == "section":
        keep = np.abs(along - at) <= SECTION_HALF_WIDTH
    else:
        keep = along <= at
    if mode != "section":
        keep |= core
    return {int(i) for i in np.flatnonzero(keep)}


def axis_camera(structure: StructureData, view: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(position, focal point, view up)`` looking ``down`` the axis or ``side``-on.

    The side view looks from the +normal side, which is the side a ``half``
    cut-away removes, so it faces the exposed inner wall.
    """
    frame = axis_frame(structure)
    distance = 4.0 * max(frame.half_length, frame.radius, 5.0)
    if view == "down":
        return frame.center + frame.axis * distance, frame.center, frame.normal
    if view == "side":
        return frame.center + frame.normal * distance, frame.center, frame.axis
    raise ValueError(f"view must be 'down' or 'side', got {view!r}")


def _charges(reader: QVFReader) -> np.ndarray:
    for section in reader.sections:
        if section.kind != "atom_properties":
            continue
        data = reader.read_atom_properties(section.id)
        # IAO first: it is the basis-set-stable scheme.
        for values in (data.iao_charges, data.hirshfeld_charges, data.mulliken_charges,
                       data.loewdin_charges):
            if values is not None:
                return np.asarray(values, dtype=float)
    raise ValueError("Colour by charge needs an atom_properties section with charges")


def _metal_bond_orders(structure: StructureData, reader: QVFReader) -> np.ndarray:
    z = [a.atomic_number or SYMBOL_TO_Z.get(a.symbol, 0) for a in structure.atoms]
    out = np.zeros(len(z))
    found = False
    for section in reader.sections:
        if section.kind != "bond_orders":
            continue
        found = True
        for pair in reader.read_bond_orders(section.id).pairs:
            i, j, order = pair.get("i"), pair.get("j"), pair.get("order")
            if not (isinstance(i, int) and isinstance(j, int)
                    and isinstance(order, (int, float))):
                continue
            if not (0 <= i < len(z) and 0 <= j < len(z)):
                continue
            if is_metal(z[j]) and not is_metal(z[i]):
                out[i] = max(out[i], order)
            if is_metal(z[i]) and not is_metal(z[j]):
                out[j] = max(out[j], order)
    if not found:
        raise ValueError("Colour by bond order to a metal needs a bond_orders section")
    return out


def property_values(structure: StructureData, bonds: list[Bond] | None, mode: str,
                    reader: QVFReader | None = None) -> np.ndarray:
    """One value per atom for colour ``mode`` (see :data:`PROPERTY_COLOR_MODES`).

    Raises ``ValueError`` naming what is missing when the archive has no data
    for the mode, or when its table does not match the atom count.
    """
    n = len(structure.atoms)
    if mode == "coordination":
        counts = np.zeros(n)
        for bond in bonds or []:
            i, j = int(bond[0]), int(bond[1])
            if i < n and j < n:
                counts[i] += 1
                counts[j] += 1
        return counts
    if mode in ("local_strain", "carbon_sites"):
        from vibeview.nanotube import CC_BOND
        from vibeview.renderers.cell import bond_far_endpoints

        numbers = np.asarray([a.atomic_number or SYMBOL_TO_Z.get(a.symbol, 0)
                              for a in structure.atoms])
        carbon = numbers == 6
        pairs = np.asarray([(b[0], b[1]) for b in bonds or []], dtype=int).reshape(-1, 2)
        valid = np.all((pairs >= 0) & (pairs < n), axis=1)
        pairs = pairs[valid]
        if mode == "local_strain":
            positions = np.asarray([a.position for a in structure.atoms], dtype=float)
            starts, ends = bond_far_endpoints(
                positions, [b for b, ok in zip(bonds or [], valid, strict=True) if ok],
                structure.lattice_vectors, structure.pbc)
            cc = carbon[pairs[:, 0]] & carbon[pairs[:, 1]]
            # Engineering bond stretch relative to the builder's ideal graphene
            # bond: mean (length / reference - 1), NOT a fitted strain tensor.
            stretch = 100 * (np.linalg.norm(ends[cc] - starts[cc], axis=1) / CC_BOND - 1)
            totals = np.zeros(n)
            counts = np.zeros(n)
            for side in (0, 1):
                np.add.at(totals, pairs[cc, side], stretch)
                np.add.at(counts, pairs[cc, side], 1)
            return totals / np.maximum(counts, 1)
        degree = np.bincount(pairs.ravel(), minlength=n)
        values = np.zeros(n)
        values[carbon & (degree != 3)] = 2  # coordination anomaly
        if carbon.any():
            frame = axis_frame(structure)
            positions = np.asarray([a.position for a in structure.atoms])
            axial = (positions - frame.center) @ frame.axis
            ends = ((axial <= axial[carbon].min() + 2.0)
                    | (axial >= axial[carbon].max() - 2.0))
            if not any(structure.pbc):
                values[carbon & (degree == 2) & ends] = 1
        for side in (0, 1):
            c, other = pairs[:, side], pairs[:, 1 - side]
            attached = carbon[c] & ~np.isin(numbers[other], [1, 6])
            values[c[attached]] = 3
            values[other[attached]] = 3
        return values
    if mode == "axis_distance":
        frame = axis_frame(structure)
        rel = np.array([a.position for a in structure.atoms], dtype=float) - frame.center
        along = rel @ frame.axis
        distance: np.ndarray = np.linalg.norm(rel - np.outer(along, frame.axis), axis=1)
        return distance
    if mode not in PROPERTY_COLOR_MODES:
        raise ValueError(f"unknown atom colour mode {mode!r}")
    if reader is None:
        raise ValueError(f"Colour by {PROPERTY_LABELS[mode].lower()} needs the archive")
    values = _charges(reader) if mode == "charge" else _metal_bond_orders(structure, reader)
    if len(values) != n:
        raise ValueError(
            f"{PROPERTY_LABELS[mode]}: the table has {len(values)} atoms, the structure {n}")
    return values


# Sequential ramp (dark blue to yellow, perceptually ordered) and a diverging
# blue-white-red one for signed values such as charges.
_SEQUENTIAL = np.array([[0.267, 0.005, 0.329], [0.229, 0.322, 0.546], [0.128, 0.567, 0.551],
                        [0.369, 0.789, 0.383], [0.993, 0.906, 0.144]])
_DIVERGING = np.array([[0.230, 0.299, 0.754], [0.865, 0.865, 0.865], [0.706, 0.016, 0.150]])


def _ramp(stops: np.ndarray, t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0.0, 1.0) * (len(stops) - 1)
    lo = np.minimum(np.floor(t).astype(int), len(stops) - 2)
    frac = (t - lo)[:, None]
    out: np.ndarray = stops[lo] * (1.0 - frac) + stops[lo + 1] * frac
    return out


def property_colors(values: np.ndarray, mode: str) -> tuple[dict[int, tuple[float, float, float]],
                                                             tuple[float, float]]:
    """Per-atom RGB (0-1) for *values*, and the (low, high) range of the ramp.

    Charges use a diverging ramp symmetric about zero; the other modes a
    sequential one over their range.
    """
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return {}, (0.0, 0.0)
    if mode == "carbon_sites":
        palette = np.array([[0.55, 0.55, 0.55], [0.1, 0.7, 1.0],
                            [1.0, 0.35, 0.1], [0.85, 0.2, 0.8]])
        rgb = palette[np.clip(values.astype(int), 0, 3)]
        colors = {i: (float(row[0]), float(row[1]), float(row[2]))
                  for i, row in enumerate(rgb)}
        return colors, (0.0, 3.0)
    if mode in ("charge", "local_strain"):
        bound = float(np.max(np.abs(values))) or 1.0
        lo, hi = -bound, bound
        rgb = _ramp(_DIVERGING, (values - lo) / (hi - lo))
    else:
        lo, hi = float(values.min()), float(values.max())
        span = (hi - lo) or 1.0
        rgb = _ramp(_SEQUENTIAL, (values - lo) / span)
    return {i: (float(r[0]), float(r[1]), float(r[2])) for i, r in enumerate(rgb)}, (lo, hi)


def describe(mode: str, low: float, high: float) -> str:
    """Status line naming the colour ramp's range."""
    if mode == "carbon_sites":
        return "Sites: grey ordinary; blue open-end; orange coordination defect; magenta adduct"
    return f"Atoms coloured by {PROPERTY_LABELS[mode].lower()}: {low:.3g} to {high:.3g}"
