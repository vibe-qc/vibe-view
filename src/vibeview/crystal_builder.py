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
_SPACE_GROUPS = [
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


def search_space_groups(query: str = "") -> list[dict]:
    """Return space groups matching ``query`` (case-insensitive substring).

    An empty query returns all groups.
    """
    q = query.lower().strip()
    if not q:
        return list(_SPACE_GROUPS)
    return [
        g
        for g in _SPACE_GROUPS
        if q in g["symbol"].lower() or q in str(g["number"]) or q in g["crystal_system"].lower()
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


def miller_slab(
    cell: np.ndarray,
    atoms: list[dict],
    hkl: tuple[int, int, int],
    n_layers: int = 3,
    vacuum: float = 15.0,
) -> tuple[np.ndarray, list[dict]]:
    """Cut a periodic slab along the (hkl) Miller plane.

    Parameters
    ----------
    cell : (3,3) array
        Lattice vectors (rows in Angstrom).
    atoms : list of dict
        Atoms with ``symbol`` and ``position`` keys.
    hkl : (h, k, l)
        Miller indices.
    n_layers : int
        Number of atomic layers in the slab.
    vacuum : float
        Vacuum gap above and below the slab (Angstrom).

    Returns
    -------
    (new_cell, new_atoms) : (3,3) array, list of dict
        The slab cell and atoms with vacuum padding.
    """
    h, k, l = hkl
    # Fractional coords (row convention: frac = cart @ inv(cell))
    frac_atoms = []
    for atom in atoms:
        pos = np.asarray(atom["position"], dtype=float)
        frac = cartesian_to_fractional(cell, pos)
        frac_atoms.append({**atom, "frac": frac})

    plane_norm = np.array([h, k, l], dtype=float)
    if not np.any(plane_norm):
        raise ValueError("Miller indices (0,0,0) do not define a plane")

    # True interplanar spacing needs the reciprocal lattice, not the bare
    # index vector. With cell rows a_i, the reciprocal vectors are the
    # columns of inv(cell), so G_hkl = inv(cell) @ (h,k,l) and d = 1/|G|.
    #
    # The previous expression, 1/|(h,k,l)|, is dimensionless: it reported
    # 1.0 A for (001) of a cubic 5 A cell whose real d-spacing is 5.0 A, and
    # 0.577 A for (111) against a true 2.887 A. Everything downstream --
    # layer selection and slab thickness -- inherited the error.
    g_vec = np.linalg.inv(cell) @ plane_norm
    g_norm = float(np.linalg.norm(g_vec))
    if g_norm <= 0.0:
        raise ValueError(f"degenerate cell for Miller plane {hkl}")
    d_spacing = 1.0 / g_norm
    normal_hat = g_vec / g_norm

    # This construction keeps a and b and stacks vacuum along c, which is
    # only a slab when a and b actually lie in the (hkl) plane. For any
    # other orientation the honest answer is to refuse: a general surface
    # cell means finding two lattice vectors spanning the plane, and
    # returning a mislabelled box would be worse than returning nothing.
    tol = 1e-6 * max(1.0, float(np.linalg.norm(cell)))
    if abs(float(cell[0] @ normal_hat)) > tol or abs(float(cell[1] @ normal_hat)) > tol:
        raise ValueError(
            f"miller_slab cannot build a {hkl} slab from this cell: the a and b "
            "vectors do not lie in the (hkl) plane, so vacuum along c would not "
            "be perpendicular to the surface. Only orientations whose normal is "
            "along c are supported."
        )

    # h*x + k*y + l*z on fractional coordinates is the plane index: atoms on
    # one (hkl) plane share it and consecutive planes differ by exactly 1.
    # So the selection window is n_layers wide in these units -- multiplying
    # it by a distance, as before, mixed plane counts with angstroms.
    proj = np.array([np.dot(a["frac"], plane_norm) for a in frac_atoms])
    indices = np.argsort(proj)
    min_proj = proj[indices[0]]
    max_proj = min_proj + float(n_layers)
    selected = [frac_atoms[i] for i in indices if proj[i] <= max_proj + 1e-9]

    # Thickness is a real length: n_layers planes at the true spacing.
    new_cell = cell.copy()
    slab_thickness = n_layers * d_spacing
    new_cell[2] = normal_hat * (slab_thickness + 2.0 * vacuum)

    new_atoms = []
    for a in selected:
        new_atoms.append(
            {"symbol": a["symbol"], "position": a["position"]}
            if "position" in a
            else {"symbol": a.get("symbol", "X"), "position": (cell @ a["frac"]).tolist()}
        )

    return new_cell, new_atoms


def fractional_to_cartesian(
    cell: np.ndarray,
    frac_coords: np.ndarray,
) -> np.ndarray:
    """Convert fractional coordinates to Cartesian (Angstrom)."""
    return frac_coords @ cell


def cartesian_to_fractional(
    cell: np.ndarray,
    cart_coords: np.ndarray,
) -> np.ndarray:
    """Convert Cartesian coordinates to fractional."""
    inv = np.linalg.inv(cell)
    return cart_coords @ inv


def cell_volume(cell: np.ndarray) -> float:
    """Volume of the unit cell (Angstrom^3)."""
    return abs(float(np.linalg.det(cell)))


def replicate_cell(
    cell: np.ndarray,
    atoms: list[dict],
    nx: int,
    ny: int,
    nz: int,
    wrap: bool = True,
) -> tuple[np.ndarray, list[dict]]:
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
