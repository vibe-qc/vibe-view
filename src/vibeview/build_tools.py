"""Fragment library and build tools for molecular construction (v1.3).

Provides common molecular fragments, hydrogen addition, and supercell
replication for periodic systems.
"""

from __future__ import annotations

import numpy as np

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

_SYMBOLS = [
    "",
    "H",
    "He",
    "Li",
    "Be",
    "B",
    "C",
    "N",
    "O",
    "F",
    "Ne",
    "Na",
    "Mg",
    "Al",
    "Si",
    "P",
    "S",
    "Cl",
    "Ar",
    "K",
    "Ca",
    "Sc",
    "Ti",
    "V",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
    "Cu",
    "Zn",
    "Ga",
    "Ge",
    "As",
    "Se",
    "Br",
    "Kr",
]

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

# Covalent radii (Angstrom) for bond detection and H placement
COVALENT_RADII: dict[int, float] = {
    1: 0.31,
    2: 0.28,
    3: 1.28,
    4: 0.96,
    5: 0.84,
    6: 0.76,
    7: 0.71,
    8: 0.66,
    9: 0.57,
    10: 0.58,
    11: 1.66,
    12: 1.41,
    13: 1.21,
    14: 1.11,
    15: 1.07,
    16: 1.05,
    17: 1.02,
    18: 1.06,
    19: 2.03,
    20: 1.76,
    21: 1.70,
    22: 1.60,
    23: 1.53,
    24: 1.39,
    25: 1.39,
    26: 1.32,
    27: 1.26,
    28: 1.24,
    29: 1.32,
    30: 1.22,
    31: 1.22,
    32: 1.20,
    33: 1.19,
    34: 1.20,
    35: 1.20,
    36: 1.16,
    46: 1.39,
    47: 1.45,
    48: 1.44,
    53: 1.39,
    78: 1.36,
    79: 1.36,
    80: 1.32,
    82: 1.46,
}


def add_hydrogens(
    atoms: list[dict],
    charge: int = 0,
) -> list[dict]:
    """Add hydrogen atoms to saturate all open valences.

    Parameters
    ----------
    atoms : list of dict
        Each dict has ``symbol``, ``atomic_number``, ``position`` [x,y,z].
    charge : int
        Net charge (subtracts from total valence electron count).

    Returns
    -------
    list of dict
        New atom list with hydrogens appended.
    """
    positions = np.array([a["position"] for a in atoms])
    nums = [a["atomic_number"] for a in atoms]
    new_atoms = list(atoms)

    # Count current bonds per atom (rough estimate from distances)
    n = len(atoms)
    bonds_per_atom = [0] * n
    for i in range(n):
        for j in range(i + 1, n):
            ri = COVALENT_RADII.get(nums[i], 0.7)
            rj = COVALENT_RADII.get(nums[j], 0.7)
            cutoff = (ri + rj) * 1.2
            dist = float(np.linalg.norm(positions[i] - positions[j]))
            if dist < cutoff:
                bonds_per_atom[i] += 1
                bonds_per_atom[j] += 1

    # Add hydrogens for each unsaturated atom
    for i in range(n):
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
        for j in range(n):
            if i == j:
                continue
            vec = positions[j] - positions[i]
            dist = float(np.linalg.norm(vec))
            # Only bonded neighbours block a direction; a far atom that
            # merely lies that way does not.
            r_i = COVALENT_RADII.get(nums[i], 0.7)
            r_j = COVALENT_RADII.get(nums[j], 0.7)
            if 1e-6 < dist < (r_i + r_j) * 1.2:
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
    atoms: list[dict],
    lattice_vectors: list[list[float]],
    replication: tuple[int, int, int],
) -> list[dict]:
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
