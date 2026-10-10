"""One bond rule for the viewer, the terminal, the editor and every exporter.

Atoms *i* and *j* are bonded when ``|r_i - r_j| < R(i) + R(j) + BOND_TOLERANCE``
with the Cordero covalent radii from :mod:`vibeview.elements` (Cordero et al.,
Dalton Trans. 2008, 2832). Candidates come from a uniform cell list, so the
work is proportional to the atom count; a periodic structure is surrounded by
every lattice image that can reach into it -- one shell for an ordinary
cell, more for a cell thinner than the bond cutoff -- so bonds through
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
from typing import TYPE_CHECKING, Any, cast

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


#: Most lattice translations a periodic bond search enumerates. Real cells
#: need at most a few hundred (3 shells per axis, 342 translations, across
#: 2760 local periodic archives); atoms millions of cells apart would need
#: ~10**18, so the search would never finish.
MAX_IMAGE_SHIFTS = 100_000


class DegenerateLatticeError(ValueError):
    """A periodic cell whose geometry the bond search cannot enumerate."""


# Work bounds for the bond search (#62). The image cap above limits how many
# translations are tried, not how many atoms they produce: a supercell written
# with its unit-cell lattice (MgO 6x6x6, 1728 atoms, in the 4.2 A cell) lays
# hundreds of image copies on top of every atom and found 1.21 million
# "bonds" in 962 MB, and 4000 atoms inside 0.5 A exhausted 3 GB in seconds.
# Each bound is far above what a physical structure needs and is checked
# before the memory it guards is allocated.

#: Most atom-image placements (real atoms times translations) to try. A
#: 100,000-atom crystal with its 26 neighbour cells is 2.6 million.
MAX_ATOM_IMAGES = 50_000_000
#: Most image atoms kept around a periodic cell, per real atom, with a floor
#: for small cells: an ordinary cell keeps a fraction of one image per atom,
#: and a lone atom in a cell thinner than its bond keeps about a hundred.
MAX_IMAGES_PER_ATOM = 64
MIN_IMAGE_BUDGET = 100_000
#: Most candidate pairs the cell list may examine, per searched atom, plus a
#: floor. Physical densities give tens to a few hundred per atom; the most
#: is about 700, for a dense solid holding the largest-radius elements,
#: whose cutoff sets the bin size for every atom.
MAX_CANDIDATES_PER_ATOM = 2048
MIN_CANDIDATE_BUDGET = 1_000_000
#: Most bonds per real atom, on average. Real coordination tops out near 14
#: (bcc metals); 32 leaves room for dense covalent-radius overshoot.
MAX_BONDS_PER_ATOM = 32


class BondSearchLimitError(ValueError):
    """A structure too dense, or with a lattice too inconsistent, to search."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"structure too dense or lattice inconsistent for bond search: {detail}"
        )


def interplanar_spacings(lattice: np.ndarray) -> np.ndarray:
    """``d_i = 1 / |b_i|`` for the three cell axes, in the lattice's units.

    ``b_i`` is the reciprocal vector (``a_i . b_j = delta_ij``). Raises
    ``np.linalg.LinAlgError`` for a singular cell, and
    :class:`DegenerateLatticeError` when a cell vector length, the cell
    volume or a spacing is not finite, or a spacing is zero. Every component
    can be finite and the cell invertible while this still overflows or
    underflows in double precision: at 1e200 or -DBL_MAX the squared lengths
    overflow and the squared reciprocal lengths underflow to 0, at 1e-300
    they overflow to inf (#39-35). The bond search and the renderers' cell
    transforms both need these quantities.
    """
    cell = np.asarray(lattice, dtype=float).reshape(3, 3)
    reciprocal = np.linalg.inv(cell).T
    with np.errstate(over="ignore", under="ignore", divide="ignore", invalid="ignore"):
        lengths = np.linalg.norm(cell, axis=1)
        volume = float(abs(np.linalg.det(cell)))
        spacings = 1.0 / np.linalg.norm(reciprocal, axis=1)
    finite = (
        np.all(np.isfinite(lengths))
        and np.isfinite(volume)
        and np.all(np.isfinite(reciprocal))
        and np.all(np.isfinite(spacings))
    )
    if not finite or np.any(spacings <= 0.0):
        raise DegenerateLatticeError(
            "degenerate lattice: cell vector lengths "
            f"{', '.join(f'{v:.3g}' for v in lengths)}, volume {volume:.3g}, interplanar "
            f"spacings {', '.join(f'{d:.3g}' for d in spacings)}; each must be finite "
            "and the spacings positive"
        )
    return cast(np.ndarray, spacings)


def _image_shells(
    lattice: np.ndarray, pbc: Sequence[bool], positions: np.ndarray, reach: float
) -> list[int]:
    """How many image shells each periodic axis needs to find every bond.

    An image translated by ``k`` cells along axis *i* moves every atom by
    ``k`` in fractional coordinate *i*. The distance between two points is at
    least their separation along the plane normal, ``|delta f_i| * d_i``,
    where ``d_i = 1 / |b_i|`` is the interplanar spacing and ``b_i`` the
    reciprocal vector (``a_i . b_j = delta_ij``). An image can therefore lie
    within ``reach`` of a real atom only when ``|k| <= s_i + reach / d_i``,
    with ``s_i`` the fractional span of the atoms along *i*. One shell was
    assumed before (#35-9), which is enough only when the cell is thicker
    than the cutoff; a lone carbon in a 0.9 A cell has bonded images at 1.8 A.

    Raises :class:`DegenerateLatticeError` for a spacing that is zero or not
    finite, for atoms whose fractional span is not finite, and for more than
    :data:`MAX_IMAGE_SHIFTS` translations.
    """
    shells = [0, 0, 0]
    try:
        spacings = interplanar_spacings(lattice)
    except np.linalg.LinAlgError:
        # A singular cell has no finite interplanar spacing; keep the single
        # shell rather than enumerate an unbounded number of images.
        return [1 if (axis < len(pbc) and pbc[axis]) else 0 for axis in range(3)]
    reciprocal = np.linalg.inv(lattice).T
    with np.errstate(over="ignore", invalid="ignore"):
        frac = positions @ reciprocal.T
    for axis in range(3):
        if not (axis < len(pbc) and pbc[axis]):
            continue
        with np.errstate(over="ignore", invalid="ignore"):
            span = float(np.ptp(frac[:, axis])) if len(frac) else 0.0
            need = span + reach / float(spacings[axis])
        if not np.isfinite(need):
            raise DegenerateLatticeError(
                "degenerate lattice: the atoms' fractional coordinates along "
                f"axis {axis} are not finite"
            )
        # Capped before int() so a huge span cannot allocate a huge integer.
        shells[axis] = max(1, int(np.ceil(min(need, float(MAX_IMAGE_SHIFTS)))))
    n_shifts = 1
    for n in shells:
        n_shifts *= 2 * n + 1
    if n_shifts - 1 > MAX_IMAGE_SHIFTS:
        raise DegenerateLatticeError(
            f"degenerate lattice: finding bonds would need {n_shifts - 1:,} cell images "
            f"(shells {shells}); the atoms lie too many cells apart"
        )
    return shells


def periodic_image_shells(
    positions: Sequence[Sequence[float]] | np.ndarray,
    atomic_numbers: Sequence[int],
    lattice: Sequence[Sequence[float]] | np.ndarray,
    pbc: Sequence[bool],
    *,
    tolerance: float = BOND_TOLERANCE,
) -> list[int]:
    """The image shells :func:`infer_bonds` searches for this periodic structure.

    The reader calls it to refuse a cell the bond search cannot handle
    (:class:`DegenerateLatticeError`) when the structure is read, rather than
    when something first draws it.
    """
    numbers = np.asarray(atomic_numbers, dtype=np.int64).reshape(-1)
    coords = np.asarray(positions, dtype=float).reshape(-1, 3)
    radii = covalent_radii(numbers)
    reach = float(2.0 * radii.max() + tolerance) if len(radii) else tolerance
    return _image_shells(
        np.asarray(lattice, dtype=float).reshape(3, 3), tuple(bool(v) for v in pbc), coords, reach
    )


def _periodic_shifts(
    lattice: np.ndarray, shells: Sequence[int]
) -> list[tuple[Image, np.ndarray]]:
    """Every non-zero lattice translation within ``shells`` cells per axis."""
    ranges = [range(-n, n + 1) for n in shells]
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


def _search_atoms(
    positions: np.ndarray,
    radii: np.ndarray,
    lattice: Sequence[Sequence[float]] | np.ndarray | None,
    flags: tuple[bool, ...],
    tolerance: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None]:
    """The real atoms plus every periodic image that can reach one of them.

    Returns ``(positions, radii, ghost_of, ghost_shift)``; the last two are
    ``None`` for a structure with nothing to add. Raises
    :class:`BondSearchLimitError` before the image arrays outgrow their
    budget (#62).

    A periodic structure bonds through its own cell walls. Searching raw
    Cartesian space misses every one of them: two chlorines 1.0 Å apart
    across a 10 Å boundary sit 9.0 Å apart in these coordinates. Rather
    than teach the neighbour search about wrapping, surround the cell with
    the periodic images that could reach into it and search the union.
    For an ordinary cell that is one shell a few angstroms thick against a
    cell edge of tens, a small multiple of the atom count; a cell thinner
    than the cutoff gets as many shells as the cutoff spans.
    """
    n = len(positions)
    if lattice is None or not any(flags):
        return positions, radii, None, None
    reach = float(2.0 * radii.max() + tolerance)
    cell_vectors = np.asarray(lattice, dtype=float).reshape(3, 3)
    shells = _image_shells(cell_vectors, flags, positions, reach)
    n_shifts = 1
    for count in shells:
        n_shifts *= 2 * count + 1
    if n * (n_shifts - 1) > MAX_ATOM_IMAGES:
        raise BondSearchLimitError(
            f"{n:,} atoms in {n_shifts - 1:,} cell images is more than "
            f"{MAX_ATOM_IMAGES:,} placements to try"
        )
    shifts = _periodic_shifts(cell_vectors, shells)
    if not shifts:
        return positions, radii, None, None
    image_budget = max(MIN_IMAGE_BUDGET, MAX_IMAGES_PER_ATOM * n)
    n_images = 0
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
        n_images += int(keep.sum())
        if n_images > image_budget:
            # Only a lattice far smaller than the structure it holds puts
            # this many copies within bonding reach.
            raise BondSearchLimitError(
                f"more than {image_budget:,} periodic image atoms lie within "
                f"bonding reach of {n:,} atoms; the lattice is likely smaller "
                "than the structure (a supercell written with its unit cell?)"
            )
        ghost_pos.append(moved[keep])
        ghost_rad.append(radii[keep])
        ghost_src.append(np.arange(n)[keep])
        ghost_img.append(
            np.repeat(np.asarray(cellshift, dtype=np.int64)[None, :], int(keep.sum()), axis=0)
        )
    return (
        np.vstack(ghost_pos),
        np.concatenate(ghost_rad),
        np.concatenate(ghost_src),
        np.vstack(ghost_img),
    )


_OFFSETS = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]


def _bin_keys(positions: np.ndarray, cell: float) -> np.ndarray:
    """Integer cell-list bin of every atom, ``cell`` Å on a side."""
    origin = positions.min(axis=0)
    return cast(np.ndarray, np.floor((positions - origin) / cell).astype(np.int64))


def check_bond_search(
    positions: Sequence[Sequence[float]] | np.ndarray,
    atomic_numbers: Sequence[int],
    *,
    lattice: Sequence[Sequence[float]] | np.ndarray | None = None,
    pbc: Sequence[bool] = (False, False, False),
    tolerance: float = BOND_TOLERANCE,
) -> None:
    """Raise :class:`BondSearchLimitError` if :func:`infer_bonds` would refuse.

    Runs the image and candidate-pair bounds without computing a distance,
    so the reader can report a structure the bond search cannot handle
    when it is read, rather than hang whatever first draws it (#62). The
    per-atom bond cap still applies inside :func:`infer_bonds`.
    """
    numbers = np.asarray(atomic_numbers, dtype=np.int64).reshape(-1)
    n = int(numbers.size)
    if n < 2:
        return
    coords = np.asarray(positions, dtype=float).reshape(n, 3)
    radii = covalent_radii(numbers)
    flags = tuple(bool(v) for v in pbc)
    search, search_radii, _, _ = _search_atoms(coords, radii, lattice, flags, tolerance)
    cell = float(2.0 * search_radii.max() + tolerance)
    if len(search) <= 512 or not np.isfinite(cell) or cell <= 0.0:
        return
    keys, counts = np.unique(_bin_keys(search, cell), axis=0, return_counts=True)
    _check_candidate_pairs(
        {
            (int(key[0]), int(key[1]), int(key[2])): int(c)
            for key, c in zip(keys, counts, strict=True)
        },
        _OFFSETS,
        len(search),
    )


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

    The work is bounded (#62): too many image atoms, candidate pairs or bonds
    raise :class:`BondSearchLimitError` instead of allocating without limit.
    """
    numbers = np.asarray(atomic_numbers, dtype=np.int64).reshape(-1)
    n = int(numbers.size)
    flags = tuple(bool(v) for v in pbc)
    periodic = lattice is not None and any(flags)
    # A lone atom has no partner in a molecule, but in a periodic cell it
    # can bond to its own images.
    if n == 0 or (n < 2 and not periodic):
        return []
    positions = np.asarray(positions, dtype=float).reshape(n, 3)
    radii = covalent_radii(numbers)
    is_metal = np.array([int(z) not in NON_METALS for z in numbers], dtype=bool)
    drop_metal_pairs = not metal_metal_pairs_are_bonds(numbers)

    positions, radii, ghost_of, ghost_shift = _search_atoms(
        positions, radii, lattice, flags, tolerance
    )
    n = len(positions)
    # Real atoms come first; a bond needs at least one of them. Since pairs
    # are taken i < j, restricting i to the real atoms keeps every bond
    # (a ghost-ghost pair is a translated copy of a real-ghost pair the
    # search also finds) and skips the ghost-ghost work entirely.
    n_real = int(numbers.size)

    # One bin edge = the largest cutoff any pair can have, so every bond
    # partner is in this bin or one of the 26 around it.
    cell = float(2.0 * radii.max() + tolerance)
    if not np.isfinite(cell) or cell <= 0.0:
        return []

    # Small structures: the binning overhead dominates, and the vectorized
    # all-pairs form is both simpler and faster.
    if n <= 512:
        d2 = np.sum((positions[:, None, :] - positions[None, :, :]) ** 2, axis=-1)
        cutoff = radii[:, None] + radii[None, :] + tolerance
        hit = np.triu(d2 < cutoff**2, k=1)
        hit[n_real:, :] = False
        if drop_metal_pairs:
            src = is_metal[ghost_of] if ghost_of is not None else is_metal
            hit &= ~(src[:, None] & src[None, :])
        i_arr, j_arr = np.nonzero(hit)
        return _dedupe_bonds(
            [(int(i), int(j), 1.0) for i, j in zip(i_arr, j_arr, strict=True)],
            ghost_of,
            ghost_shift,
        )

    idx = _bin_keys(positions, cell)
    buckets: dict[tuple[int, int, int], list[int]] = {}
    for a, key in enumerate(map(tuple, idx)):
        buckets.setdefault(key, []).append(a)

    _check_candidate_pairs(
        {key: len(members) for key, members in buckets.items()}, _OFFSETS, n
    )
    bond_budget = MAX_BONDS_PER_ATOM * n_real + MAX_BONDS_PER_ATOM
    bonds: list[tuple[int, int, float]] = []
    for (bx, by, bz), members in buckets.items():
        neighbours: list[int] = []
        for dx, dy, dz in _OFFSETS:
            neighbours.extend(buckets.get((bx + dx, by + dy, bz + dz), ()))
        if not neighbours:
            continue
        cand = np.asarray(neighbours, dtype=np.int64)
        for i in members:
            if i >= n_real:
                continue
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
            if len(bonds) > bond_budget:
                raise BondSearchLimitError(
                    f"more than {bond_budget:,} bonds among {n_real:,} atoms "
                    f"(over {MAX_BONDS_PER_ATOM} per atom); atoms overlap"
                )

    return _dedupe_bonds(bonds, ghost_of, ghost_shift)


def _check_candidate_pairs(
    counts: dict[tuple[int, int, int], int],
    offsets: Sequence[tuple[int, int, int]],
    n_search: int,
) -> None:
    """Refuse a cell list whose neighbourhoods hold too many candidate pairs.

    Each atom is compared with every atom in its own bin and the 26 around
    it, so the work is the sum over bins of ``count * neighbourhood count``.
    That is known from the bin counts alone, before any distance is
    computed or any bond stored.
    """
    budget = max(MIN_CANDIDATE_BUDGET, MAX_CANDIDATES_PER_ATOM * n_search)
    total = 0
    for (bx, by, bz), count in counts.items():
        around = sum(counts.get((bx + dx, by + dy, bz + dz), 0) for dx, dy, dz in offsets)
        total += count * around
        if total > budget:
            raise BondSearchLimitError(
                f"more than {budget:,} candidate atom pairs for {n_search:,} atoms "
                "within bonding reach of each other"
            )


def structure_bonds(structure: StructureData | Any) -> list[Bond]:
    """The connectivity every surface shows for ``structure``.

    Explicit bonds from the producer win, with their orders; otherwise the
    covalent-radius inference runs over the structure's atoms, lattice and
    periodic flags. The parameter is a protocol, not only
    :class:`~vibeview.qvf.StructureData`: any object with ``atoms`` (each
    with ``atomic_number`` and ``position``) is accepted, and ``bonds``,
    ``lattice_vectors`` and ``pbc`` are optional on it, which is why they
    are read with ``getattr`` (exporters and tests pass light structures).
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
