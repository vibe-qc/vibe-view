"""Fragment library and build tools for molecular construction (v1.3).

Provides common molecular fragments, hydrogen addition, and supercell
replication for periodic systems.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np

from vibeview.bonds import infer_bonds, neighbour_counts
from vibeview.elements import COVALENT_RADII

# ── Fragment library ────────────────────────────────────────────────────
# Common fragments as (symbol, relative_position) tuples in Angstrom.
# Positions are relative to the attachment point (first atom).

FRAGMENTS: dict[str, list[tuple[str, list[float]]]] = {
    "CH3": [
        ("C", [0.0, 0.0, 0.0]),
        ("H", [0.0, 1.09, 0.0]),
        ("H", [1.03, -0.54, 0.0]),
        ("H", [-1.03, -0.54, 0.0]),
    ],
    "NH2": [
        ("N", [0.0, 0.0, 0.0]),
        ("H", [0.0, 1.01, 0.0]),
        ("H", [0.95, -0.33, 0.0]),
    ],
    "OH": [
        ("O", [0.0, 0.0, 0.0]),
        ("H", [0.0, 0.96, 0.0]),
    ],
    "COOH": [
        ("C", [0.0, 0.0, 0.0]),
        ("O", [0.0, 1.21, 0.0]),
        ("O", [1.18, -0.57, 0.0]),
        ("H", [1.48, -1.29, 0.50]),
    ],
    "Ph": [  # phenyl ring
        ("C", [0.000, 1.396, 0.0]),
        ("C", [1.209, 0.698, 0.0]),
        ("C", [1.209, -0.698, 0.0]),
        ("C", [0.000, -1.396, 0.0]),
        ("C", [-1.209, -0.698, 0.0]),
        ("C", [-1.209, 0.698, 0.0]),
        ("H", [0.000, 2.479, 0.0]),
        ("H", [2.147, 1.240, 0.0]),
        ("H", [2.147, -1.240, 0.0]),
        ("H", [0.000, -2.479, 0.0]),
        ("H", [-2.147, -1.240, 0.0]),
        ("H", [-2.147, 1.240, 0.0]),
    ],
    "CHO": [
        ("C", [0.0, 0.0, 0.0]),
        ("O", [0.0, 1.21, 0.0]),
        ("H", [1.10, 0.0, 0.0]),
    ],
    "NO2": [
        ("N", [0.0, 0.0, 0.0]),
        ("O", [1.22, 0.0, 0.0]),
        ("O", [-1.22, 0.0, 0.0]),
    ],
    "CN": [
        ("C", [0.0, 0.0, 0.0]),
        ("N", [1.16, 0.0, 0.0]),
    ],
    "CF3": [
        ("C", [0.0, 0.0, 0.0]),
        ("F", [1.33, 0.0, 0.0]),
        ("F", [-0.67, 1.15, 0.0]),
        ("F", [-0.67, -1.15, 0.0]),
    ],
    "SO3H": [
        ("S", [0.0, 0.0, 0.0]),
        ("O", [1.45, 0.0, 0.0]),
        ("O", [-0.73, 1.26, 0.0]),
        ("O", [-0.73, -1.26, 0.0]),
        ("H", [1.95, -0.80, 0.0]),
    ],
}


# ── Element data ────────────────────────────────────────────────────────


# Standard valences (number of bonds typically formed)
VALENCES: dict[int, int] = {
    1: 1,
    2: 0,
    3: 1,
    4: 2,
    5: 3,
    6: 4,
    7: 3,
    8: 2,
    9: 1,
    10: 0,
    11: 1,
    12: 2,
    13: 3,
    14: 4,
    15: 3,
    16: 2,
    17: 1,
    18: 0,
    19: 1,
    20: 2,
    35: 1,
    53: 1,
}

# Covalent radii for H placement: the viewer's table (vibeview.elements).


def add_hydrogens(
    atoms: list[dict[str, Any]],
    charge: int = 0,
    only: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    """Add hydrogen atoms to saturate open valences.

    Parameters
    ----------
    atoms : list of dict
        Each dict has ``symbol``, ``atomic_number``, ``position`` [x,y,z].
    charge : int
        Net charge (subtracts from total valence electron count).
    only : iterable of int, optional
        Saturate only these atom indices (the editor's selection). ``None``
        saturates every atom.

    Returns
    -------
    list of dict
        New atom list with hydrogens appended.
    """
    positions = np.array([a["position"] for a in atoms])
    nums = [a["atomic_number"] for a in atoms]
    new_atoms = list(atoms)

    # Current bonds per atom, by the same rule the viewer draws with.
    n = len(atoms)
    bonds = infer_bonds(positions, nums) if n else []
    bonds_per_atom = neighbour_counts(n, bonds)
    bonded: dict[int, list[int]] = {i: [] for i in range(n)}
    for i, j, _order, _image in bonds:
        bonded[i].append(j)
        bonded[j].append(i)

    selected = None if only is None else {int(i) for i in only}
    # Add hydrogens for each unsaturated atom
    for i in range(n):
        if selected is not None and i not in selected:
            continue
        z = nums[i]
        target = VALENCES.get(z, 0)
        missing = target - bonds_per_atom[i]
        if missing <= 0:
            continue
        r_h = COVALENT_RADII.get(1, 0.31)
        r_a = COVALENT_RADII.get(z, 0.7)
        bond_len = r_a + r_h

        # Place hydrogens tetrahedrally around the atom. Each new H
        # consumes its direction: the old code only checked candidate
        # directions against *pre-existing* atoms, so a redirected first
        # hydrogen and a later one could land on the same tetrahedral
        # vertex — two H at the identical position, which downstream
        # produced a zero-length bond and a NaN render transform.
        dirs = [
            np.array([1.0, 0.0, 0.0]),
            np.array([-0.33, 0.94, 0.0]),
            np.array([-0.33, -0.47, 0.82]),
            np.array([-0.33, -0.47, -0.82]),
        ]
        used: list[np.ndarray] = []
        # Only bonded neighbours block a direction; a far atom that merely
        # lies that way does not. A coincident atom has no direction.
        for j in bonded[i]:
            vec = positions[j] - positions[i]
            dist = float(np.linalg.norm(vec))
            if dist > 1e-6:
                used.append(vec / dist)

        placed = 0
        for direction in dirs:
            if placed >= missing:
                break
            if any(float(np.dot(direction, u)) > 0.7 for u in used):
                continue
            used.append(direction)
            placed += 1
            h_pos = positions[i] + direction * bond_len
            new_atoms.append(
                {
                    "symbol": "H",
                    "atomic_number": 1,
                    "position": [float(h_pos[0]), float(h_pos[1]), float(h_pos[2])],
                }
            )
        # If geometry blocks every remaining vertex, placing fewer
        # hydrogens beats placing coincident ones.

    return new_atoms


def build_supercell(
    atoms: list[dict[str, Any]],
    lattice_vectors: list[list[float]],
    replication: tuple[int, int, int],
) -> list[dict[str, Any]]:
    """Replicate atoms for a supercell.

    Parameters
    ----------
    atoms : list of dict
        Unit cell atoms with ``position`` [x,y,z].
    lattice_vectors : list of list
        3×3 matrix of lattice vectors in Angstrom.
    replication : tuple
        (nx, ny, nz) replication factors.

    Returns
    -------
    list of dict
        Replicated atoms.
    """
    lat = np.array(lattice_vectors)
    nx, ny, nz = replication
    result = []
    for ix in range(nx):
        for iy in range(ny):
            for iz in range(nz):
                offset = ix * lat[0] + iy * lat[1] + iz * lat[2]
                for a in atoms:
                    pos = np.array(a["position"]) + offset
                    result.append(
                        {
                            "symbol": a["symbol"],
                            "atomic_number": a["atomic_number"],
                            "position": [float(pos[0]), float(pos[1]), float(pos[2])],
                        }
                    )
    return result
