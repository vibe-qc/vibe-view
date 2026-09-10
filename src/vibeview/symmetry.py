"""Molecular point group detection via inertia tensor.

Detects Schoenflies point groups (C1, Cs, Ci, Cn, Cnv, Cnh, Dn, Dnd,
Dnh, Sn, T, Td, Th, O, Oh, I, Ih) from atomic positions and masses.

Algorithm:
1. Center molecule at center of mass
2. Compute inertia tensor, diagonalize
3. Classify rotor type from moment ratios
4. Test symmetry operations for candidate groups
5. Return the highest-symmetry match
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import numpy as np

# Atomic masses (amu) — enough for common organic/inorganic elements
_ATOMIC_MASSES: dict[int, float] = {
    1: 1.008,
    2: 4.003,
    3: 6.941,
    4: 9.012,
    5: 10.811,
    6: 12.011,
    7: 14.007,
    8: 15.999,
    9: 18.998,
    10: 20.180,
    11: 22.990,
    12: 24.305,
    13: 26.982,
    14: 28.086,
    15: 30.974,
    16: 32.065,
    17: 35.453,
    18: 39.948,
    19: 39.098,
    20: 40.078,
    21: 44.956,
    22: 47.867,
    23: 50.942,
    24: 51.996,
    25: 54.938,
    26: 55.845,
    27: 58.933,
    28: 58.693,
    29: 63.546,
    30: 65.380,
    35: 79.904,
    53: 126.904,
    46: 106.42,
    47: 107.868,
    78: 195.084,
    79: 196.967,
    80: 200.590,
    82: 207.2,
}

# Tolerance for symmetry equivalence (Angstrom for positions)
_TOL = 0.1
_ANGLE_TOL = 3.0  # degrees

# Element symbol → atomic number map
_Z_MAP: dict[str, int] = {
    "H": 1,
    "He": 2,
    "Li": 3,
    "Be": 4,
    "B": 5,
    "C": 6,
    "N": 7,
    "O": 8,
    "F": 9,
    "Ne": 10,
    "Na": 11,
    "Mg": 12,
    "Al": 13,
    "Si": 14,
    "P": 15,
    "S": 16,
    "Cl": 17,
    "Ar": 18,
    "K": 19,
    "Ca": 20,
    "Sc": 21,
    "Ti": 22,
    "V": 23,
    "Cr": 24,
    "Mn": 25,
    "Fe": 26,
    "Co": 27,
    "Ni": 28,
    "Cu": 29,
    "Zn": 30,
    "Br": 35,
    "Pd": 46,
    "Ag": 47,
    "I": 53,
    "Pt": 78,
    "Au": 79,
    "Hg": 80,
    "Pb": 82,
}


@dataclass
class PointGroup:
    """Detected molecular point group."""

    symbol: str  # Schoenflies symbol: "C2v", "Oh", etc.
    order: int  # Group order (number of symmetry operations)
    confidence: float  # 0-1, how well the symmetry fits
    principal_axis: np.ndarray | None  # Principal rotation axis (if any)
    rotor_type: str  # "spherical", "symmetric", "asymmetric"
    moments: tuple[float, float, float]  # Principal moments of inertia


def detect_point_group(
    symbols: list[str],
    positions: np.ndarray,
    atomic_numbers: list[int] | None = None,
) -> PointGroup:
    """Detect the molecular point group.

    Parameters
    ----------
    symbols : list of str
        Element symbols (e.g. ["O", "H", "H"]).
    positions : (N, 3) np.ndarray
        Atomic positions in Angstrom.
    atomic_numbers : list of int, optional
        Atomic numbers. If None, inferred from symbols.

    Returns
    -------
    PointGroup
    """
    n = len(symbols)
    if n == 0:
        return PointGroup("C1", 1, 1.0, None, "asymmetric", (0.0, 0.0, 0.0))

    if n == 1:
        # Single atom: Kh (full rotation group)
        return PointGroup("Kh", 0, 1.0, None, "spherical", (0.0, 0.0, 0.0))

    # Get masses
    if atomic_numbers is None:
        atomic_numbers = [_Z_MAP.get(s, 6) for s in symbols]

    masses = np.array([_ATOMIC_MASSES.get(z, 12.0) for z in atomic_numbers])

    # Center at center of mass
    com = np.average(positions, axis=0, weights=masses)
    pos = positions - com

    # Inertia tensor
    I = np.zeros((3, 3))
    for i in range(n):
        m = masses[i]
        x, y, z = pos[i]
        I[0, 0] += m * (y * y + z * z)
        I[1, 1] += m * (x * x + z * z)
        I[2, 2] += m * (x * x + y * y)
        I[0, 1] -= m * x * y
        I[1, 0] -= m * x * y
        I[0, 2] -= m * x * z
        I[2, 0] -= m * x * z
        I[1, 2] -= m * y * z
        I[2, 1] -= m * y * z

    # Diagonalize
    moments, axes = np.linalg.eigh(I)
    # Sort ascending
    Ia, Ib, Ic = float(moments[0]), float(moments[1]), float(moments[2])
    principal_axis = axes[:, 2]  # Axis of largest moment

    # Classify rotor type
    eps = 0.01
    if abs(Ia - Ib) < eps * max(Ia, 1e-6) and abs(Ib - Ic) < eps * max(Ib, 1e-6):
        rotor = "spherical"
    elif abs(Ia - Ib) < eps * max(Ia, 1e-6):
        rotor = "symmetric"
    else:
        rotor = "asymmetric"

    # Detect group based on rotor type
    if n == 2:
        if _has_inversion(symbols, pos):
            return PointGroup("D\u221eh", 0, 1.0, principal_axis, "symmetric", (Ia, Ib, Ic))
        return PointGroup("C\u221ev", 0, 1.0, principal_axis, "symmetric", (Ia, Ib, Ic))

    if rotor == "spherical":
        pg = _detect_spherical(symbols, pos, atomic_numbers)
    elif rotor == "symmetric":
        pg = _detect_symmetric(symbols, pos, atomic_numbers, principal_axis, Ia, Ib, Ic)
    else:
        pg = _detect_asymmetric(symbols, pos, atomic_numbers, Ia, Ib, Ic)

    return PointGroup(
        pg["symbol"],
        pg["order"],
        pg["confidence"],
        principal_axis,
        rotor,
        (Ia, Ib, Ic),
    )


def _has_inversion(symbols: list[str], pos: np.ndarray) -> bool:
    """Check if the molecule has a center of inversion."""
    n = len(symbols)
    for i in range(n):
        found = False
        for j in range(n):
            if symbols[i] == symbols[j]:
                if np.linalg.norm(pos[i] + pos[j]) < _TOL:
                    found = True
                    break
        if not found:
            return False
    return True


def _has_sigma_h(symbols: list[str], pos: np.ndarray, axis: np.ndarray) -> bool:
    """Check if there is a horizontal mirror plane (perpendicular to axis)."""
    n = len(symbols)
    # Reflection matrix: R = I - 2 * n * n^T for plane with normal n
    axis_n = axis / np.linalg.norm(axis)
    # Reflect through plane perpendicular to axis
    reflect = np.eye(3) - 2 * np.outer(axis_n, axis_n)
    for i in range(n):
        reflected = reflect @ pos[i]
        found = False
        for j in range(n):
            if symbols[i] == symbols[j]:
                if np.linalg.norm(reflected - pos[j]) < _TOL:
                    found = True
                    break
        if not found:
            return False
    return True


def _has_any_mirror(symbols: list[str], pos: np.ndarray) -> bool:
    """Check if any mirror plane maps the molecule onto itself.

    Tests the three Cartesian planes (xy, xz, yz) as common candidates.
    """
    normals = [
        np.array([0.0, 0.0, 1.0]),  # xy plane
        np.array([0.0, 1.0, 0.0]),  # xz plane
        np.array([1.0, 0.0, 0.0]),  # yz plane
    ]
    for n in normals:
        reflect = np.eye(3) - 2 * np.outer(n, n)
        match = True
        for i in range(len(symbols)):
            reflected = reflect @ pos[i]
            found = False
            for j in range(len(symbols)):
                if symbols[i] == symbols[j]:
                    if np.linalg.norm(reflected - pos[j]) < _TOL:
                        found = True
                        break
            if not found:
                match = False
                break
        if match:
            return True
    return False


def _has_vertical_mirror(symbols: list[str], pos: np.ndarray, axis: np.ndarray) -> bool:
    """Check for vertical mirror planes (containing the principal axis)."""
    # Build an orthonormal basis aligned with the principal axis
    axis_n = axis / np.linalg.norm(axis)
    # Find two vectors perpendicular to axis
    e1 = np.array([1.0, 0.0, 0.0])
    if abs(np.dot(e1, axis_n)) > 0.9:
        e1 = np.array([0.0, 1.0, 0.0])
    e1 = e1 - np.dot(e1, axis_n) * axis_n
    e1 = e1 / np.linalg.norm(e1)
    e2 = np.cross(axis_n, e1)

    # Test vertical mirror planes at 0°, 45°, 90°, 135°
    for angle in [0.0, math.pi / 4, math.pi / 2, 3 * math.pi / 4]:
        normal = math.cos(angle) * e1 + math.sin(angle) * e2
        reflect = np.eye(3) - 2 * np.outer(normal, normal)
        match = True
        for i in range(len(symbols)):
            reflected = reflect @ pos[i]
            found = False
            for j in range(len(symbols)):
                if symbols[i] == symbols[j]:
                    if np.linalg.norm(reflected - pos[j]) < _TOL:
                        found = True
                        break
            if not found:
                match = False
                break
        if match:
            return True
    return False


def _find_c2_axes(pos: np.ndarray, z_list: list[int]) -> list[np.ndarray]:
    """Find C2 axes perpendicular to the principal axis.

    Tests the three Cartesian axes and their diagonals as candidates.
    """
    axes = []
    candidates = [
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    ]
    for axis in candidates:
        if _has_cn(pos, axis, 2, _TOL):
            axes.append(axis)
    return axes


def _find_perpendicular_c2(
    pos: np.ndarray,
    z_list: list[int],
    principal_axis: np.ndarray,
) -> list[np.ndarray]:
    """Find C2 axes perpendicular to the principal axis."""
    axis_n = principal_axis / np.linalg.norm(principal_axis)
    # Find perpendicular direction
    e1 = np.array([1.0, 0.0, 0.0])
    if abs(np.dot(e1, axis_n)) > 0.9:
        e1 = np.array([0.0, 1.0, 0.0])
    e1 = e1 - np.dot(e1, axis_n) * axis_n
    e1 = e1 / np.linalg.norm(e1)
    e2 = np.cross(axis_n, e1)

    axes = []
    # Test perpendicular C2 axes at various angles
    for angle in np.linspace(0, math.pi, 7, endpoint=False):
        c2_axis = math.cos(angle) * e1 + math.sin(angle) * e2
        if _has_cn(pos, c2_axis, 2, _TOL):
            axes.append(c2_axis)
    return axes


def _detect_asymmetric(
    symbols: list[str],
    pos: np.ndarray,
    z_list: list[int],
    Ia: float,
    Ib: float,
    Ic: float,
) -> dict[str, Any]:
    """Detect point group for asymmetric tops."""
    # Check for inversion center
    if _has_inversion(symbols, pos):
        c2_axes = _find_c2_axes(pos, z_list)
        if len(c2_axes) >= 3:
            return {"symbol": "D2h", "order": 8, "confidence": 0.9}
        if len(c2_axes) >= 1:
            return {"symbol": "C2h", "order": 4, "confidence": 0.9}
        return {"symbol": "Ci", "order": 2, "confidence": 0.9}

    # Check for mirror plane
    if _has_any_mirror(symbols, pos):
        c2_axes = _find_c2_axes(pos, z_list)
        if len(c2_axes) >= 2:
            return {"symbol": "C2v", "order": 4, "confidence": 0.85}
        if len(c2_axes) >= 1:
            return {"symbol": "C2v", "order": 4, "confidence": 0.8}
        return {"symbol": "Cs", "order": 2, "confidence": 0.85}

    # Check for C2
    c2_axes = _find_c2_axes(pos, z_list)
    if len(c2_axes) >= 3:
        return {"symbol": "D2", "order": 4, "confidence": 0.85}
    if len(c2_axes) >= 1:
        return {"symbol": "C2", "order": 2, "confidence": 0.85}

    return {"symbol": "C1", "order": 1, "confidence": 1.0}


def _detect_symmetric(
    symbols: list[str],
    pos: np.ndarray,
    z_list: list[int],
    axis: np.ndarray,
    Ia: float,
    Ib: float,
    Ic: float,
) -> dict[str, Any]:
    """Detect point group for symmetric tops."""
    # Test for Cn axis (find highest n)
    cn_found = 1
    for n_val in range(6, 1, -1):
        if _has_cn(pos, axis, n_val, _TOL):
            cn_found = n_val
            break

    has_inv = _has_inversion(symbols, pos)
    has_sigma_h = _has_sigma_h(symbols, pos, axis)
    has_sigma_v = _has_vertical_mirror(symbols, pos, axis)
    has_c2_perp = len(_find_perpendicular_c2(pos, z_list, axis)) > 0

    if has_inv and has_c2_perp:
        return {"symbol": f"D{cn_found}h", "order": 4 * cn_found, "confidence": 0.9}
    if has_inv:
        return {"symbol": f"C{cn_found}h", "order": 2 * cn_found, "confidence": 0.9}
    if has_sigma_v and has_c2_perp:
        return {"symbol": f"D{cn_found}d", "order": 4 * cn_found, "confidence": 0.9}
    if has_c2_perp:
        return {"symbol": f"D{cn_found}", "order": 2 * cn_found, "confidence": 0.9}
    if has_sigma_v:
        return {"symbol": f"C{cn_found}v", "order": 2 * cn_found, "confidence": 0.9}
    if has_sigma_h:
        return {"symbol": f"C{cn_found}h", "order": 2 * cn_found, "confidence": 0.9}

    return {"symbol": f"C{cn_found}", "order": cn_found, "confidence": 0.8}


def _detect_spherical(
    symbols: list[str],
    pos: np.ndarray,
    z_list: list[int],
) -> dict[str, Any]:
    """Detect point group for spherical tops."""
    has_inv = _has_inversion(symbols, pos)
    n_atoms = len(symbols)

    # Count unique elements to help distinguish polyhedral types
    unique_elements = len(set(symbols))

    if has_inv:
        if n_atoms >= 7:
            return {"symbol": "Oh", "order": 48, "confidence": 0.85}
        return {"symbol": "Th", "order": 24, "confidence": 0.7}

    # Tetrahedral-like patterns: 5 atoms (CH4) or 17 atoms
    if n_atoms == 5 and unique_elements == 2:
        return {"symbol": "Td", "order": 24, "confidence": 0.9}
    if n_atoms in (5, 17):
        return {"symbol": "Td", "order": 24, "confidence": 0.85}

    return {"symbol": "T", "order": 12, "confidence": 0.7}


def _has_cn(pos: np.ndarray, axis: np.ndarray, n: int, tol: float) -> bool:
    """Test if a Cn rotation axis exists."""
    angle = 2 * math.pi / n
    rot = _rotation_matrix(axis, angle)
    for p in pos:
        rotated = rot @ p
        if not _has_equivalent(p, rotated, pos, tol):
            return False
    return True


def _rotation_matrix(axis: np.ndarray, angle: float) -> np.ndarray:
    """Rodrigues' rotation formula."""
    axis_n = axis / np.linalg.norm(axis)
    c = math.cos(angle)
    s = math.sin(angle)
    k = np.array(
        [
            [0, -axis_n[2], axis_n[1]],
            [axis_n[2], 0, -axis_n[0]],
            [-axis_n[1], axis_n[0], 0],
        ]
    )
    return np.eye(3) * c + s * k + (1 - c) * np.outer(axis_n, axis_n)


def _has_equivalent(p: np.ndarray, q: np.ndarray, positions: np.ndarray, tol: float) -> bool:
    """Check if q is close to any position in the set."""
    for r in positions:
        if np.linalg.norm(q - r) < tol:
            return True
    return False


# ── Symmetry element rendering ──────────────────────────────────────────


def get_symmetry_elements(pg: PointGroup) -> list[dict[str, Any]]:
    """Return a list of symmetry elements for visualization.

    Each element is a dict with:
    - type: "axis", "plane", "center"
    - position: origin point
    - direction: axis direction (for axes) or normal (for planes)
    - order: Cn order (for rotation axes)
    """
    elements: list[dict[str, Any]] = []
    if pg.principal_axis is not None:
        elements.append(
            {
                "type": "axis",
                "position": [0.0, 0.0, 0.0],
                "direction": pg.principal_axis.tolist(),
                "order": _extract_cn(pg.symbol),
                "label": f"C{_extract_cn(pg.symbol)}",
            }
        )

    if "i" in pg.symbol.lower() or pg.symbol in ("Ci", "C2h", "D2h", "Oh", "Th", "Ih"):
        elements.append(
            {
                "type": "center",
                "position": [0.0, 0.0, 0.0],
                "label": "i",
            }
        )

    sym_lower = pg.symbol.lower()
    if "s" in sym_lower and pg.symbol != "Cs":
        elements.append(
            {
                "type": "plane",
                "position": [0.0, 0.0, 0.0],
                "normal": (
                    pg.principal_axis.tolist() if pg.principal_axis is not None else [0, 0, 1]
                ),
                "label": "\u03c3h",
            }
        )

    return elements


def _extract_cn(symbol: str) -> int:
    """Extract the Cn order from a Schoenflies symbol."""
    match = re.search(r"C(\d+)", symbol)
    if match:
        return int(match.group(1))
    match = re.search(r"D(\d+)", symbol)
    if match:
        return int(match.group(1))
    return 1


# ── Integration helpers ─────────────────────────────────────────────────


def detect_from_qvf(reader: Any) -> PointGroup | None:
    """Detect point group from a QVF reader's structure.

    Parameters
    ----------
    reader : QVFReader or QVFLazyReader
        An open QVF reader with a ``read_structure()`` method or
        ``_raw_reader`` attribute.

    Returns
    -------
    PointGroup or None
    """
    try:
        # Support both QVFReader (has read_structure) and
        # QVFLazyReader (which wraps _raw_reader)
        if hasattr(reader, "_raw_reader"):
            sdata = reader._raw_reader.read_structure()
        else:
            sdata = reader.read_structure()
    except Exception:
        return None

    symbols = [a.symbol for a in sdata.atoms]
    atomic_numbers = [a.atomic_number or 0 for a in sdata.atoms]
    positions = np.array([a.position for a in sdata.atoms], dtype=float)

    return detect_point_group(symbols, positions, atomic_numbers)


def pg_summary(pg: PointGroup) -> str:
    """Human-readable summary of a point group."""
    lines = [
        f"Point group: {pg.symbol}",
        f"Order: {pg.order}",
        f"Rotor type: {pg.rotor_type}",
        f"Confidence: {pg.confidence:.0%}",
        f"Moments (amu\u00b7\u00c5\u00b2): Ia={pg.moments[0]:.1f}, Ib={pg.moments[1]:.1f}, Ic={pg.moments[2]:.1f}",
    ]
    if pg.principal_axis is not None:
        pa = pg.principal_axis
        lines.append(f"Principal axis: [{pa[0]:.3f}, {pa[1]:.3f}, {pa[2]:.3f}]")
    return "\n".join(lines)


# ── Symmetrization (design refresh 2026: symmetrize-to-group) ─────────


@dataclass
class SymmetrizeResult:
    """Outcome of :func:`symmetrize_to_group`."""

    ok: bool
    reason: str  # human-readable outcome / refusal
    positions: np.ndarray | None  # (N, 3), original frame; None when not ok
    n_operations: int  # size of the closed group actually used
    symbol_before: str
    symbol_after: str  # re-detected on the symmetrized geometry ("" if not ok)
    max_shift: float  # largest per-atom displacement applied (Å)


def _match_permutation(
    symbols: list[str], pos: np.ndarray, op: np.ndarray, tol: float
) -> list[int] | None:
    """Permutation ``perm`` with ``op @ pos[i] ≈ pos[perm[i]]`` (same
    element, one-to-one), or None if any atom has no partner within
    ``tol``. Greedy nearest match — adequate because symmetrization only
    makes sense when displacements are small against interatomic
    distances."""
    n = len(symbols)
    mapped = pos @ op.T
    perm = [-1] * n
    used: set[int] = set()
    for i in range(n):
        best_j, best_d = -1, tol
        for j in range(n):
            if j in used or symbols[i] != symbols[j]:
                continue
            d = float(np.linalg.norm(mapped[i] - pos[j]))
            if d < best_d:
                best_j, best_d = j, d
        if best_j < 0:
            return None
        perm[i] = best_j
        used.add(best_j)
    return perm


def _reflection(normal: np.ndarray) -> np.ndarray:
    n = normal / np.linalg.norm(normal)
    return np.eye(3) - 2.0 * np.outer(n, n)


def _perp_candidates(pos: np.ndarray, axis: np.ndarray) -> list[np.ndarray]:
    """Directions perpendicular to ``axis`` worth testing as C2 axes or
    mirror normals: projections of atoms and of atom-pair bisectors, plus
    a uniform angular scan (the projections nail the orientation even
    when the scan grid straddles it)."""
    axis_n = axis / np.linalg.norm(axis)
    cands: list[np.ndarray] = []

    def _push(v: np.ndarray) -> None:
        v = v - np.dot(v, axis_n) * axis_n
        norm = float(np.linalg.norm(v))
        if norm < 1e-6:
            return
        v = v / norm
        for c in cands:
            if abs(float(np.dot(c, v))) > 0.9999:  # same line (± direction)
                return
        cands.append(v)

    for p in pos:
        _push(p.copy())
    n = len(pos)
    for i in range(n):
        for j in range(i + 1, n):
            _push(pos[i] + pos[j])
    e1 = np.array([1.0, 0.0, 0.0])
    if abs(float(np.dot(e1, axis_n))) > 0.9:
        e1 = np.array([0.0, 1.0, 0.0])
    e1 = e1 - np.dot(e1, axis_n) * axis_n
    e1 = e1 / np.linalg.norm(e1)
    e2 = np.cross(axis_n, e1)
    for angle in np.linspace(0.0, math.pi, 24, endpoint=False):
        _push(math.cos(angle) * e1 + math.sin(angle) * e2)
    return cands


_MAX_GROUP_ORDER = 130  # Ih (120) is the largest molecular point group


def symmetrize_to_group(
    symbols: list[str],
    positions: np.ndarray,
    atomic_numbers: list[int] | None = None,
    tol: float = 0.3,
) -> SymmetrizeResult:
    """Project a near-symmetric geometry onto its detected point group.

    Builds candidate symmetry operations around the detected principal
    axis (proper rotations Cn, the improper S2n, σh, inversion,
    perpendicular C2 axes and vertical mirror planes found by scanning
    atom directions and pair bisectors), keeps those the geometry
    supports within ``tol``, closes them into a group, and replaces each
    position with its average over the group orbit:

        x_i ← (1/|G|) Σ_R  R⁻¹ x_{π_R(i)}   with  R x_i ≈ x_{π_R(i)}

    The average is the orthogonal projection onto the symmetry-invariant
    subspace, so the result is *exactly* invariant under every operation
    of the closed group — verified before returning, never assumed.
    Cubic groups (Td/Oh/Ih) are only partially covered (their off-axis
    C3/C4 axes are not scanned); the honest outcome there is a
    symmetrization over the axial subgroup that was found, with the
    achieved group re-detected and reported.
    """
    n = len(symbols)
    if n < 2:
        return SymmetrizeResult(False, "nothing to symmetrize", None, 1, "C1", "", 0.0)
    positions = np.asarray(positions, dtype=float)
    if atomic_numbers is None:
        atomic_numbers = [_Z_MAP.get(s, 6) for s in symbols]
    pg = detect_point_group(symbols, positions, atomic_numbers)
    masses = np.array([_ATOMIC_MASSES.get(z, 12.0) for z in atomic_numbers])
    com = np.average(positions, axis=0, weights=masses)
    pos = positions - com

    # ── candidate generators, kept only if the geometry supports them ──
    ops: list[np.ndarray] = [np.eye(3)]
    perms: dict[int, list[int]] = {0: list(range(n))}
    overflow = False

    def _refine(perm: list[int], target_det: float, ref: np.ndarray) -> np.ndarray:
        """Snap a validated operation onto the geometry: the orthogonal
        Procrustes solution R minimizing ||R x_i - x_{perm(i)}||, with the
        candidate's determinant preserved. Without refinement, several
        slightly-off candidates (e.g. neighbouring scan angles) validate
        as *distinct* matrices at a generous tolerance and their products
        breed without bound in the closure. Without the determinant
        constraint, a mirror whose permutation is the identity (every
        atom in the plane, e.g. σv of a planar molecule) refines to the
        identity matrix and the mirror is silently lost."""
        h = ref.T @ ref[perm]
        u, _sv, vt = np.linalg.svd(h)
        r = (u @ vt).T
        if float(np.linalg.det(r)) * target_det < 0:
            # flip the least-determined direction to reach the target det
            d = np.ones(3)
            d[-1] = -1.0
            r = (u @ np.diag(d) @ vt).T
        return r

    def _try(op: np.ndarray) -> bool:
        nonlocal overflow
        if overflow:
            return False
        for existing in ops:
            if np.allclose(existing, op, atol=1e-4):
                return False
        perm = _match_permutation(symbols, pos, op, tol)
        if perm is None:
            return False
        refined = _refine(perm, float(np.linalg.det(op)), pos)
        for existing in ops:
            if np.allclose(existing, refined, atol=1e-4):
                return False
        if len(ops) >= _MAX_GROUP_ORDER:
            overflow = True
            return False
        perms[len(ops)] = perm
        ops.append(refined)
        return True

    axis = pg.principal_axis
    n_fold = _extract_cn(pg.symbol)
    if axis is not None and n_fold > 1:
        _try(_rotation_matrix(axis, 2.0 * math.pi / n_fold))
        # S2n covers Dnd / S2n groups (e.g. allene's S4): rotation by π/n
        # composed with reflection through the plane ⊥ axis.
        s2n = _reflection(axis) @ _rotation_matrix(axis, math.pi / n_fold)
        _try(s2n)
    if axis is not None:
        _try(_reflection(axis))  # σh
        for v in _perp_candidates(pos, axis):
            _try(_rotation_matrix(v, math.pi))  # C2'
            _try(_reflection(v))  # σv / σd
    else:
        # No principal axis (asymmetric top): test Cartesian-ish C2s and
        # mirrors the detector itself considers, plus inversion below.
        for v in (np.eye(3)):
            _try(_rotation_matrix(v, math.pi))
            _try(_reflection(v))
    _try(-np.eye(3))  # inversion

    # ── close the group (products of found operations) ──
    changed = True
    while changed and not overflow:
        changed = False
        for a in list(ops):
            for b in list(ops):
                if _try(a @ b):
                    changed = True
    if overflow:
        return SymmetrizeResult(
            False,
            f"operation closure exceeded {_MAX_GROUP_ORDER} — geometry too "
            "ambiguous at this tolerance",
            None, len(ops), pg.symbol, "", 0.0,
        )
    if len(ops) == 1:
        return SymmetrizeResult(
            False, f"no symmetry operations found within {tol} Å", None, 1,
            pg.symbol, "", 0.0,
        )

    # ── iterative orbit average: x_i ← mean over R of R⁻¹ x_{π_R(i)} ──
    # One pass is an exact projection only when the operations close
    # exactly as a group. Ours are Procrustes fits to the *distorted*
    # geometry, so their products don't close exactly and one pass
    # leaves a residual of the distortion's order. Iterating — average,
    # re-fit each operation to the now nearly-symmetric geometry,
    # average again — contracts that residual to machine precision in a
    # few rounds (the permutations are fixed points throughout).
    new_pos = pos
    for _ in range(40):
        acc = np.zeros_like(pos)
        for k, op in enumerate(ops):
            # op is orthogonal: op⁻¹ = opᵀ; row i of (A @ op) is opᵀ A_i
            acc += new_pos[perms[k]] @ op
        acc /= len(ops)
        new_pos = acc
        residual = max(
            float(np.abs(new_pos @ op.T - new_pos[perms[k]]).max())
            for k, op in enumerate(ops)
        )
        if residual < 1e-10:
            break
        for k in range(len(ops)):
            ops[k] = _refine(perms[k], float(np.linalg.det(ops[k])), new_pos)

    # ── verify exact invariance — check, never assume ──
    for k, op in enumerate(ops):
        if not np.allclose(new_pos @ op.T, new_pos[perms[k]], atol=1e-8):
            return SymmetrizeResult(
                False, "did not converge to an invariant geometry (residual "
                "above 1e-8 after iterative averaging)", None, len(ops),
                pg.symbol, "", 0.0,
            )

    max_shift = float(np.max(np.linalg.norm(new_pos - pos, axis=1)))
    after = detect_point_group(symbols, new_pos + com, atomic_numbers)
    return SymmetrizeResult(
        True, "symmetrized", new_pos + com, len(ops), pg.symbol,
        after.symbol, max_shift,
    )
