"""One bond rule for the viewer, the terminal, the editor and every exporter.

Atoms *i* and *j* are bonded when ``|r_i - r_j| < R(i) + R(j) + BOND_TOLERANCE``
with the Cordero covalent radii from :mod:`vibeview.elements` (Cordero et al.,
Dalton Trans. 2008, 2832). Candidates come from a uniform cell list, so the
work is proportional to the atom count; a periodic structure is surrounded by
the thin shell of lattice images that can reach into it, so bonds through
cell faces are found and carry the image they reach. In a structure that
mixes metals with non-metals, metal-metal contacts are not bonds: neutral
radii overshoot cation separations and would draw a sublattice that is not
there, whereas an all-metal structure keeps them because there they are the
bonding.

Every surface that draws or writes connectivity calls :func:`structure_bonds`,
so a bond present in the browser is the same bond in the terminal, the
edit overlay, the CML, SVG, PDF, POV-Ray, Blender and HTML exports, and the
hydrogen-adding tool. Producers that ship explicit bonds override inference
everywhere, with their orders.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

from vibeview.elements import COVALENT_RADII

if TYPE_CHECKING:
    from vibeview.qvf import StructureData

Image = tuple[int, int, int]
#: ``(i, j, order, image)``: the bond runs from atom *i* to atom *j* displaced
#: by ``image @ lattice``. In-cell bonds carry ``(0, 0, 0)``.
Bond = tuple[int, int, float, Image]

ZERO_IMAGE: Image = (0, 0, 0)
BOND_TOLERANCE = 0.45  # Å added to the sum of covalent radii
UNKNOWN_RADIUS = 1.5  # Å for an atomic number outside the radius table

# Elements that form ordinary covalent bonds. Everything else is treated as
# a metal. The metalloids (B, Si, Ge, As, Sb, Te) sit here deliberately:
# they form real directional bonds and belong with the non-metals for this
# purpose, whatever their formal classification.
NON_METALS = frozenset(
    {1, 2, 5, 6, 7, 8, 9, 10, 14, 15, 16, 17, 18, 32, 33, 34, 35, 36, 51, 52, 53, 54, 85, 86}
)


def covalent_radii(atomic_numbers: Sequence[int] | np.ndarray) -> np.ndarray:
    """The bonding radius of each atom, with one fallback for unknown elements."""
    return np.array(
        [COVALENT_RADII.get(int(z), UNKNOWN_RADIUS) for z in atomic_numbers], dtype=float
    )


def metal_metal_pairs_are_bonds(atomic_numbers: Sequence[int] | np.ndarray) -> bool:
    """Should two metal atoms be joined by a stick in this structure?

    Only in a structure that is entirely metal. The radii here are
    neutral-atom radii, and a cation is far smaller than its neutral atom --
    Mg is 1.41 Å against 0.72 for Mg(2+) -- so in an ionic solid the
    metal-metal separation falls inside a cutoff built from neutral radii and
    a bonded metal sublattice appears that does not exist. Measured before
    this rule: MgO gave 24 spurious Mg-Mg bonds and read 18-coordinate
    instead of 6, and CsCl gave 3 spurious Cs-Cs and read 14 instead of 8.

    In an actual metal those contacts are the bonding, so fcc copper keeps
    all twelve neighbours. This is a display heuristic, not chemistry: a
    producer that supplies explicit bonds overrides it entirely.
    """
    seen_metal = False
    for z in atomic_numbers:
        if int(z) in NON_METALS:
            return False
        seen_metal = True
    return seen_metal


def _periodic_shifts(lattice: np.ndarray, pbc: Sequence[bool]) -> list[tuple[Image, np.ndarray]]:
    """Lattice translations whose image shell can reach into the cell.

    Only axes flagged periodic are translated, and only by one cell: a
    covalent cutoff is far shorter than any cell vibe-view can render, so a
    second shell could never contribute a bond.
    """
    ranges = []
    for axis in range(3):
        periodic = axis < len(pbc) and pbc[axis]
        ranges.append((-1, 0, 1) if periodic else (0,))
    shifts: list[tuple[Image, np.ndarray]] = []
    for ia in ranges[0]:
        for ib in ranges[1]:
            for ic in ranges[2]:
                if ia == 0 and ib == 0 and ic == 0:
                    continue
                shifts.append(((ia, ib, ic), ia * lattice[0] + ib * lattice[1] + ic * lattice[2]))
    return shifts


def _dedupe_bonds(
    bonds: Sequence[tuple[int, int, float]],
    ghost_of: np.ndarray | None,
    ghost_shift: np.ndarray | None = None,
) -> list[Bond]:
    """Map image indices back to real atoms and drop true repeats.

    The image is part of a bond's identity, not decoration. Two atoms in a
    dense crystal are bonded through several different images at once -- the
    neighbour to the left and the neighbour to the right can be the same atom
    reached the other way round -- and keying only on ``(i, j)`` collapsed
    them, halving the coordination: rocksalt NaCl showed 3 neighbours per ion
    instead of 6, fcc copper 3 instead of 12.

    A bond and its reverse are the same bond, so ``(i, j, +image)`` and
    ``(j, i, -image)`` are folded onto one canonical orientation. An atom
    paired with its own image at ``(0,0,0)`` would be a bond to itself and is
    dropped; through a nonzero image it is a real bond in a cell narrower than
    the bond, so it is kept.
    """
    seen: set[tuple[int, int, Image]] = set()
    out: list[Bond] = []
    for i, j, order in bonds:
        image: Image
        if ghost_of is None:
            a, b, image = int(i), int(j), ZERO_IMAGE
        else:
            a, b = int(ghost_of[i]), int(ghost_of[j])
            shift_i = ghost_shift[i] if ghost_shift is not None else (0, 0, 0)
            shift_j = ghost_shift[j] if ghost_shift is not None else (0, 0, 0)
            delta = np.asarray(shift_j) - np.asarray(shift_i)
            image = (int(delta[0]), int(delta[1]), int(delta[2]))
        if a == b and not any(image):
            continue
        # Canonical orientation: lower index first, and for a self-pair the
        # lexicographically larger image, so +n and -n are one bond.
        if a > b or (a == b and image < (-image[0], -image[1], -image[2])):
            a, b = b, a
            image = (-image[0], -image[1], -image[2])
        key = (a, b, image)
        if key in seen:
            continue
        seen.add(key)
        out.append((a, b, order, image))
    out.sort(key=lambda bond: (bond[0], bond[1], bond[3]))
    return out


def infer_bonds(
    positions: Sequence[Sequence[float]] | np.ndarray,
    atomic_numbers: Sequence[int],
    *,
    lattice: Sequence[Sequence[float]] | np.ndarray | None = None,
    pbc: Sequence[bool] = (False, False, False),
    tolerance: float = BOND_TOLERANCE,
) -> list[Bond]:
    """Covalent-radius bond inference over a uniform cell list.

    ``positions`` are Cartesian Å, ``lattice`` rows are the cell vectors
    (``cart = frac @ lattice``). Inferred bonds carry order 1.0: radii cannot
    recover multiplicity.

    The all-pairs form this replaces was an O(N^2) Python loop, which is
    fine for a molecule and quadratically not fine for a biomolecule:
    13,772 atoms is 94.8 million iterations, measured at 158.7 s. Binning at
    the largest possible cutoff keeps the result identical while making the
    work proportional to the number of atoms.
    """
    numbers = np.asarray(atomic_numbers, dtype=np.int64).reshape(-1)
    n = int(numbers.size)
    if n < 2:
        return []
    positions = np.asarray(positions, dtype=float).reshape(n, 3)
    radii = covalent_radii(numbers)
    is_metal = np.array([int(z) not in NON_METALS for z in numbers], dtype=bool)
    drop_metal_pairs = not metal_metal_pairs_are_bonds(numbers)

    # A periodic structure bonds through its own cell walls. Searching raw
    # Cartesian space misses every one of them: two chlorines 1.0 Å apart
    # across a 10 Å boundary sit 9.0 Å apart in these coordinates. Rather
    # than teach the neighbour search about wrapping, surround the cell with
    # the thin shell of periodic images that could reach into it and search
    # the union. The shell is a few angstroms against a cell edge of tens,
    # so this costs a small multiple of the atom count.
    ghost_of: np.ndarray | None = None
    ghost_shift: np.ndarray | None = None
    flags = tuple(bool(v) for v in pbc)
    if lattice is not None and any(flags):
        reach = float(2.0 * radii.max() + tolerance)
        cell_vectors = np.asarray(lattice, dtype=float).reshape(3, 3)
        shifts = _periodic_shifts(cell_vectors, flags)
        if shifts:
            lo = positions.min(axis=0) - reach
            hi = positions.max(axis=0) + reach
            ghost_pos = [positions]
            ghost_rad = [radii]
            ghost_src = [np.arange(n)]
            ghost_img = [np.zeros((n, 3), dtype=np.int64)]
            for cellshift, shift in shifts:
                moved = positions + shift
                # Keep only images that can actually reach a real atom.
                # Appending all 26 translations wholesale searches 27x the
                # atoms: measured 8.05 s on a 20,000-atom periodic cell,
                # against 0.23 s for 13,772 atoms without images.
                keep = np.all((moved >= lo) & (moved <= hi), axis=1)
                if not keep.any():
                    continue
                ghost_pos.append(moved[keep])
                ghost_rad.append(radii[keep])
                ghost_src.append(np.arange(n)[keep])
                ghost_img.append(
                    np.repeat(
                        np.asarray(cellshift, dtype=np.int64)[None, :], int(keep.sum()), axis=0
                    )
                )
            positions = np.vstack(ghost_pos)
            radii = np.concatenate(ghost_rad)
            ghost_of = np.concatenate(ghost_src)
            ghost_shift = np.vstack(ghost_img)
            n = len(positions)

    # One bin edge = the largest cutoff any pair can have, so every bond
    # partner is in this bin or one of the 26 around it.
    cell = float(2.0 * radii.max() + tolerance)
    if not np.isfinite(cell) or cell <= 0.0:
        return []

    origin = positions.min(axis=0)
    idx = np.floor((positions - origin) / cell).astype(np.int64)

    # Small structures: the binning overhead dominates, and the vectorized
    # all-pairs form is both simpler and faster.
    if n <= 512:
        d2 = np.sum((positions[:, None, :] - positions[None, :, :]) ** 2, axis=-1)
        cutoff = radii[:, None] + radii[None, :] + tolerance
        hit = np.triu(d2 < cutoff**2, k=1)
        if drop_metal_pairs:
            src = is_metal[ghost_of] if ghost_of is not None else is_metal
            hit &= ~(src[:, None] & src[None, :])
        i_arr, j_arr = np.nonzero(hit)
        return _dedupe_bonds(
            [(int(i), int(j), 1.0) for i, j in zip(i_arr, j_arr, strict=True)],
            ghost_of,
            ghost_shift,
        )

    buckets: dict[tuple[int, int, int], list[int]] = {}
    for a, key in enumerate(map(tuple, idx)):
        buckets.setdefault(key, []).append(a)

    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    bonds: list[tuple[int, int, float]] = []
    for (bx, by, bz), members in buckets.items():
        neighbours: list[int] = []
        for dx, dy, dz in offsets:
            neighbours.extend(buckets.get((bx + dx, by + dy, bz + dz), ()))
        if not neighbours:
            continue
        cand = np.asarray(neighbours, dtype=np.int64)
        for i in members:
            # i < j only, so each pair is emitted exactly once even though
            # the bin pair is visited from both sides.
            higher = cand[cand > i]
            if higher.size == 0:
                continue
            d2 = np.sum((positions[higher] - positions[i]) ** 2, axis=1)
            cutoff = radii[i] + radii[higher] + tolerance
            for j in higher[d2 < cutoff**2]:
                if drop_metal_pairs:
                    a = int(ghost_of[i]) if ghost_of is not None else i
                    b = int(ghost_of[j]) if ghost_of is not None else int(j)
                    if is_metal[a] and is_metal[b]:
                        continue
                bonds.append((i, int(j), 1.0))

    return _dedupe_bonds(bonds, ghost_of, ghost_shift)


def structure_bonds(structure: StructureData | Any) -> list[Bond]:
    """The connectivity every surface shows for ``structure``.

    Explicit bonds from the producer win, with their orders; otherwise the
    covalent-radius inference runs over the structure's atoms, lattice and
    periodic flags. Any object with ``atoms`` (each with ``atomic_number``
    and ``position``) is accepted; ``bonds``, ``lattice_vectors`` and ``pbc``
    are optional.
    """
    explicit = getattr(structure, "bonds", None)
    if explicit is not None:
        return [normalize_bond(bond) for bond in explicit]
    atoms = structure.atoms
    if not atoms:
        return []
    return infer_bonds(
        np.asarray([atom.position for atom in atoms], dtype=float),
        [atom.atomic_number for atom in atoms],
        lattice=getattr(structure, "lattice_vectors", None),
        pbc=tuple(bool(v) for v in (getattr(structure, "pbc", None) or ())),
    )


def normalize_bond(bond: Sequence[Any]) -> Bond:
    """A full ``(i, j, order, image)`` from a pair, a triple or a bond.

    Producers and callers that only know the pair get order 1.0 and the zero
    image, so every consumer can unpack four fields.
    """
    i, j = int(bond[0]), int(bond[1])
    order = float(bond[2]) if len(bond) > 2 else 1.0
    image: Image = ZERO_IMAGE
    if len(bond) > 3 and bond[3] is not None:
        raw = bond[3]
        image = (int(raw[0]), int(raw[1]), int(raw[2]))
    return (i, j, order, image)


def in_cell(bonds: Sequence[Bond]) -> list[Bond]:
    """Only the bonds that do not cross a periodic cell face.

    For outputs that address atoms by index and cannot place an image (CML,
    a Blender script, a 2-D figure), a bond to a periodic copy would be
    drawn to the wrong atom, so it is left out.
    """
    return [bond for bond in bonds if not any(bond[3])]


def bond_endpoints(
    positions: np.ndarray,
    bond: Bond,
    lattice: Sequence[Sequence[float]] | np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Cartesian start and end of ``bond``, the end displaced by its image."""
    i, j, _order, image = bond
    start = np.asarray(positions[i], dtype=float)
    end = np.asarray(positions[j], dtype=float)
    if lattice is not None and any(image):
        end = end + np.asarray(image, dtype=float) @ np.asarray(lattice, dtype=float)
    return start, end


def bond_lengths(
    positions: np.ndarray,
    bonds: Sequence[Bond],
    lattice: Sequence[Sequence[float]] | np.ndarray | None = None,
) -> list[float]:
    """Length of each bond in Å, through its periodic image when it has one."""
    lengths: list[float] = []
    for bond in bonds:
        start, end = bond_endpoints(positions, bond, lattice)
        lengths.append(float(np.linalg.norm(end - start)))
    return lengths


def neighbour_counts(n_atoms: int, bonds: Sequence[Bond]) -> list[int]:
    """How many bonds touch each atom; a bond to an own image counts twice."""
    counts = [0] * n_atoms
    for i, j, _order, _image in bonds:
        counts[i] += 1
        counts[j] += 1
    return counts
