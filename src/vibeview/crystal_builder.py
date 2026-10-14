"""Crystal building tools for periodic systems (v1.5).

Provides cell parameter editing, space group symmetry expansion,
fractional coordinate tools, Miller plane cutting, and slab
construction for surface calculations.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

# ── Space group data (subset of the 230 groups, enough for common work) ──
# Each entry: {"number": int, "symbol": str, "crystal_system": str}
_SPACE_GROUPS: list[dict[str, Any]] = [
    {"number": 1, "symbol": "P1", "crystal_system": "triclinic"},
    {"number": 2, "symbol": "P-1", "crystal_system": "triclinic"},
    {"number": 3, "symbol": "P2", "crystal_system": "monoclinic"},
    {"number": 4, "symbol": "P2_1", "crystal_system": "monoclinic"},
    {"number": 5, "symbol": "C2", "crystal_system": "monoclinic"},
    {"number": 14, "symbol": "P2_1/c", "crystal_system": "monoclinic"},
    {"number": 15, "symbol": "C2/c", "crystal_system": "monoclinic"},
    {"number": 16, "symbol": "P222", "crystal_system": "orthorhombic"},
    {"number": 19, "symbol": "P2_12_12_1", "crystal_system": "orthorhombic"},
    {"number": 33, "symbol": "Pna2_1", "crystal_system": "orthorhombic"},
    {"number": 62, "symbol": "Pnma", "crystal_system": "orthorhombic"},
    {"number": 75, "symbol": "P4", "crystal_system": "tetragonal"},
    {"number": 96, "symbol": "P4_32_12", "crystal_system": "tetragonal"},
    {"number": 143, "symbol": "P3", "crystal_system": "trigonal"},
    {"number": 166, "symbol": "R-3m", "crystal_system": "trigonal"},
    {"number": 167, "symbol": "R-3c", "crystal_system": "trigonal"},
    {"number": 186, "symbol": "P6_3mc", "crystal_system": "hexagonal"},
    {"number": 194, "symbol": "P6_3/mmc", "crystal_system": "hexagonal"},
    {"number": 216, "symbol": "F-43m", "crystal_system": "cubic"},
    {"number": 221, "symbol": "Pm-3m", "crystal_system": "cubic"},
    {"number": 225, "symbol": "Fm-3m", "crystal_system": "cubic"},
    {"number": 227, "symbol": "Fd-3m", "crystal_system": "cubic"},
    {"number": 229, "symbol": "Im-3m", "crystal_system": "cubic"},
]


def search_space_groups(query: str = "") -> list[dict[str, Any]]:
    """Return space groups matching ``query`` (case-insensitive substring).

    An empty query returns all groups.
    """
    q = query.lower().strip()
    if not q:
        return list(_SPACE_GROUPS)
    return [
        g
        for g in _SPACE_GROUPS
        if q in str(g["symbol"]).lower()
        or q in str(g["number"])
        or q in str(g["crystal_system"]).lower()
    ]


def cell_from_abc(
    a: float, b: float, c: float, alpha: float, beta: float, gamma: float
) -> np.ndarray:
    """Build a 3x3 lattice matrix from cell parameters (Angstrom / degrees).

    Standard convention: a along x, b in xy plane, c tilted.
    """
    alpha_r = math.radians(alpha)
    beta_r = math.radians(beta)
    gamma_r = math.radians(gamma)

    cell = np.zeros((3, 3), dtype=float)
    cell[0, 0] = a
    cell[1, 0] = b * math.cos(gamma_r)
    cell[1, 1] = b * math.sin(gamma_r)
    cell[2, 0] = c * math.cos(beta_r)
    cell[2, 1] = c * (math.cos(alpha_r) - math.cos(beta_r) * math.cos(gamma_r)) / math.sin(gamma_r)
    cell[2, 2] = (
        c
        * math.sqrt(
            max(
                0,
                1
                - math.cos(alpha_r) ** 2
                - math.cos(beta_r) ** 2
                - math.cos(gamma_r) ** 2
                + 2 * math.cos(alpha_r) * math.cos(beta_r) * math.cos(gamma_r),
            )
        )
        / math.sin(gamma_r)
    )
    return cell


def abc_from_cell(cell: np.ndarray) -> tuple[float, float, float, float, float, float]:
    """Extract (a, b, c, alpha, beta, gamma) from a 3x3 lattice matrix."""
    a = float(np.linalg.norm(cell[0]))
    b = float(np.linalg.norm(cell[1]))
    c = float(np.linalg.norm(cell[2]))
    alpha = math.degrees(math.acos(max(-1, min(1, np.dot(cell[1], cell[2]) / (b * c)))))
    beta = math.degrees(math.acos(max(-1, min(1, np.dot(cell[0], cell[2]) / (a * c)))))
    gamma = math.degrees(math.acos(max(-1, min(1, np.dot(cell[0], cell[1]) / (a * b)))))
    return (a, b, c, alpha, beta, gamma)


def _ext_gcd(a: int, b: int) -> tuple[int, int]:
    """Bezout coefficients ``(p, q)`` with ``p*a + q*b == gcd(a, b)``."""
    if b == 0:
        return 1, 0
    if a % b == 0:
        return 0, 1
    x, y = _ext_gcd(b, a % b)
    return y, x - y * (a // b)


def surface_cell_basis(cell: np.ndarray, hkl: tuple[int, int, int]) -> np.ndarray:
    """Integer basis of the (hkl) surface cell, as rows of lattice-vector counts.

    Rows 0 and 1 are lattice vectors lying in the (hkl) plane; row 2 is a
    lattice vector that advances one plane along the surface normal. Indices
    with a common factor name the same plane family as their reduced form,
    (0,0,2) the same as (0,0,1), and are reduced first, so one plane here is
    ``gcd(h, k, l) * d_hkl``. The matrix is unimodular, so the surface cell
    has the parent cell's signed volume and holds exactly one image of every
    parent atom. The determinant-one transformation preserves the parent's
    handedness; a left-handed input remains left-handed.

    The construction is the extended-Euclid one ASE uses in
    ``ase.build.surface`` (``general_surface``): two in-plane vectors follow
    from Bezout coefficients of the indices, the shorter of the candidate
    first vectors is chosen with the metric, and the third vector completes
    a determinant of one. The general slab-generation strategy is described
    by W. Sun and G. Ceder, Surf. Sci. 617, 53 (2013),
    doi:10.1016/j.susc.2013.05.016.
    """
    h, k, l = (int(v) for v in hkl)  # noqa: E741 — h, k, l are the Miller indices
    if h == 0 and k == 0 and l == 0:
        raise ValueError("Miller indices (0,0,0) do not define a plane")
    common = math.gcd(h, k, l)
    h, k, l = h // common, k // common, l // common  # noqa: E741

    zeros = [v == 0 for v in (h, k, l)]
    if sum(zeros) == 2:
        # A basal plane: the surface cell is a cyclic permutation of the
        # parent's own vectors, which keeps the handedness.
        if not zeros[0]:
            basis = np.array([(0, 1, 0), (0, 0, 1), (1, 0, 0)])
        elif not zeros[1]:
            basis = np.array([(0, 0, 1), (1, 0, 0), (0, 1, 0)])
        else:
            basis = np.array([(1, 0, 0), (0, 1, 0), (0, 0, 1)])
    else:
        p, q = _ext_gcd(k, l)
        a1, a2, a3 = (np.asarray(cell[i], dtype=float) for i in range(3))
        # The first in-plane vector is p*(k a1 - h a2) + q*(l a1 - h a3) up to
        # multiples of the second, (l a2 - k a3); k1 and k2 are the dot
        # products that pick the multiple giving the shortest one.
        k1 = float(np.dot(p * (k * a1 - h * a2) + q * (l * a1 - h * a3), l * a2 - k * a3))
        k2 = float(np.dot(l * (k * a1 - h * a2) - k * (l * a1 - h * a3), l * a2 - k * a3))
        if abs(k2) > 1e-10 * max(1.0, float(np.linalg.norm(cell)) ** 2):
            i = -int(round(k1 / k2))
            p, q = p + i * l, q - i * k
        a, b = _ext_gcd(p * k + q * l, h)
        c1 = (p * k + q * l, -p * h, -q * h)
        c2 = tuple(int(v) for v in np.array((0, l, -k)) // abs(math.gcd(l, k)))
        c3 = (b, a * p, a * q)
        basis = np.array([c1, c2, c3])

    # Point the third vector along +G, so a repetition advances towards the
    # surface rather than away from it (a negative index can flip it), and
    # then fix the handedness with an in-plane vector, which leaves the
    # normal alone.
    if int(basis[2] @ np.array((h, k, l))) < 0:
        basis[2] = -basis[2]
    if int(round(float(np.linalg.det(basis)))) < 0:
        basis[1] = -basis[1]
    det = int(round(float(np.linalg.det(basis))))
    if det != 1:  # pragma: no cover — the construction guarantees this
        raise ValueError(f"surface cell basis for {hkl} is not unimodular (det {det})")
    return basis


def miller_slab(
    cell: np.ndarray,
    atoms: list[dict[str, Any]],
    hkl: tuple[int, int, int],
    n_layers: int = 3,
    vacuum: float = 15.0,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Cut a slab whose surface is the (hkl) Miller plane.

    Works for any orientation of a nonsingular cell. The slab is built in the (hkl)
    surface cell from :func:`surface_cell_basis`: two lattice vectors spanning
    the plane and one stepping along the normal. The parent cell's atoms are
    mapped into that cell, repeated ``n_layers`` times along the normal, and
    the third vector is then replaced by the surface normal scaled to the slab
    thickness plus ``2 * vacuum``. Atoms are not translated to center the
    slab in this padding. The in-plane vectors and every atom stay in the
    parent crystal's Cartesian frame, so the slab overlays the bulk it was cut
    from. The surface basis preserves the parent cell's handedness.

    Parameters
    ----------
    cell : (3,3) array
        Lattice vectors (rows in Angstrom).
    atoms : list of dict
        The content of one parent cell, with ``symbol`` and ``position`` keys.
        Positions are wrapped into the cell; an atom given outside it is
        treated as the periodic image inside.
    hkl : (h, k, l)
        Miller indices.
    n_layers : int
        Repetitions of the surface cell along the normal. Each repetition is
        one interplanar spacing ``d_hkl`` (for indices with a common factor,
        one spacing of the reduced family, so (0,0,2) stacks like (0,0,1));
        how many atomic planes that holds depends on the cell (a conventional
        fcc cell puts two (001) planes in one repetition, one (111) plane).
    vacuum : float
        Half the extra cell length along the normal (Angstrom). Atom positions
        are unchanged by this padding; equal gaps on both sides are not implied.

    Returns
    -------
    (new_cell, new_atoms) : (3,3) array, list of dict
        The slab cell and its atoms. Only the first two vectors are periodic.
    """
    if n_layers < 1:
        raise ValueError("a slab needs at least one layer")
    cell = np.asarray(cell, dtype=float)
    indices = tuple(int(v) for v in hkl)
    basis = surface_cell_basis(cell, (indices[0], indices[1], indices[2]))

    # True interplanar spacing needs the reciprocal lattice, not the bare
    # index vector. With cell rows a_i, the reciprocal vectors are the
    # columns of inv(cell), so G_hkl = inv(cell) @ (h,k,l) and d = 1/|G|.
    g_vec = np.linalg.inv(cell) @ np.asarray(indices, dtype=float)
    g_norm = float(np.linalg.norm(g_vec))
    if g_norm <= 0.0:
        raise ValueError(f"degenerate cell for Miller plane {hkl}")
    normal_hat = g_vec / g_norm

    surf_cell = basis.astype(float) @ cell
    # One repetition advances one plane of the reduced family: d_hkl for
    # indices with no common factor, gcd(h,k,l) * d_hkl otherwise.
    layer_step = float(surf_cell[2] @ normal_hat)
    thickness = n_layers * layer_step

    # Map the parent atoms into the surface cell (row convention:
    # frac = cart @ inv(cell)) and wrap them into it. The basis is unimodular,
    # so this is a bijection and no atom is lost or doubled.
    new_atoms: list[dict[str, Any]] = []
    if atoms:
        cart = np.asarray([atom["position"] for atom in atoms], dtype=float).reshape(-1, 3)
        frac = cart @ np.linalg.inv(surf_cell)
        frac -= np.floor(frac + 1e-8)
        frac[frac < 0.0] = 0.0
        for layer in range(n_layers):
            offset = np.array([0.0, 0.0, float(layer)])
            for atom, f in zip(atoms, frac, strict=True):
                position = (f + offset) @ surf_cell
                new_atoms.append({**atom, "position": position.tolist()})

    new_cell = surf_cell.copy()
    new_cell[2] = normal_hat * (thickness + 2.0 * vacuum)
    return new_cell, new_atoms


def fractional_to_cartesian(
    cell: np.ndarray,
    frac_coords: np.ndarray,
) -> np.ndarray:
    """Convert fractional coordinates to Cartesian (Angstrom)."""
    cart: np.ndarray = frac_coords @ cell
    return cart


def cartesian_to_fractional(
    cell: np.ndarray,
    cart_coords: np.ndarray,
) -> np.ndarray:
    """Convert Cartesian coordinates to fractional."""
    frac: np.ndarray = cart_coords @ np.linalg.inv(cell)
    return frac


def cell_volume(cell: np.ndarray) -> float:
    """Volume of the unit cell (Angstrom^3)."""
    return abs(float(np.linalg.det(cell)))


def replicate_cell(
    cell: np.ndarray,
    atoms: list[dict[str, Any]],
    nx: int,
    ny: int,
    nz: int,
    wrap: bool = True,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Create a supercell by replicating the unit cell.

    Parameters
    ----------
    cell : (3,3) array
    atoms : list of dict with ``symbol`` and ``position``
    nx, ny, nz : int
        Replication factors.
    wrap : bool
        If True, wrap fractional coords to [0, 1).

    Returns
    -------
    (supercell, super_atoms)
    """
    new_cell = cell.copy()
    new_cell[0] *= nx
    new_cell[1] *= ny
    new_cell[2] *= nz

    new_atoms = []
    for atom in atoms:
        # Row convention throughout this module: frac = cart @ inv(cell).
        # (The old inv @ pos was the column convention — wrong for any
        # non-orthogonal cell — and converting the offset fractionals
        # with the *scaled* supercell stretched every replica by
        # (nx,ny,nz); wrapping after the offset collapsed all replicas
        # onto the base image.)
        frac = cartesian_to_fractional(cell, np.asarray(atom["position"], dtype=float))
        if wrap:
            frac = frac % 1.0
        for ix in range(nx):
            for iy in range(ny):
                for iz in range(nz):
                    f = frac + np.array([ix, iy, iz], dtype=float)
                    # Offsets are integer multiples of the ORIGINAL cell
                    # vectors; the supercell only redefines periodicity.
                    cart = fractional_to_cartesian(cell, f)
                    new_atoms.append({"symbol": atom["symbol"], "position": cart.tolist()})

    return new_cell, new_atoms
