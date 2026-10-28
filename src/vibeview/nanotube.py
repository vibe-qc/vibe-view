"""Carbon nanotube geometry: (n,m) tubes, multi-wall tubes, bundles, forests.

A single-wall tube is a graphene strip rolled along its chiral vector
``Ch = n a1 + m a2``. Its translational unit cell along the axis is
``T = t1 a1 + t2 a2`` with ``t1 = (2m + n) / dR`` and ``t2 = -(2n + m) / dR``,
``dR = gcd(2m + n, 2n + m)``, and holds ``4 (n^2 + nm + m^2) / dR`` atoms
(Saito, Dresselhaus and Dresselhaus, *Physical Properties of Carbon
Nanotubes*, Imperial College Press 1998, ch. 3). The diameter is
``|Ch| / pi`` and the chiral angle ``atan(sqrt(3) m / (2n + m))``, 0 deg for
zigzag (n,0) and 30 deg for armchair (n,n).

Everything here is numpy only and vectorised over unit cells, so a scene of
10^6 atoms builds in about a second. The geometry is a starting point for an
optimisation, not a relaxed structure: walls are ideal cylinders, and a cap
is a C60 hemisphere scaled onto the tube.

Each builder returns a :class:`Nanotube`: symbols, Cartesian positions in
Angstrom with the tube axis along z, the lattice when the tube is periodic
along its axis, and per-wall metrics for display.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

#: Graphene lattice constant (Angstrom), C-C 1.42 A times sqrt(3).
GRAPHENE_A = 2.46
#: C-C bond length used for caps and terminations (Angstrom).
CC_BOND = 1.42
#: C-H bond length for hydrogen-terminated open ends (Angstrom).
CH_BOND = 1.09
#: Interlayer spacing of turbostratic multi-wall tubes (Angstrom).
WALL_SPACING = 3.4
#: Closest wall-to-wall gap between tubes in a bundle (Angstrom), graphite's
#: interlayer spacing as in rope crystallography.
BUNDLE_GAP = 3.4

End = Literal["periodic", "open", "hydrogen", "cap"]
ENDS: tuple[str, ...] = ("periodic", "open", "hydrogen", "cap")


@dataclass(frozen=True)
class WallInfo:
    """Metrics of one wall, for the builder's readout."""

    n: int
    m: int
    diameter: float
    chiral_angle: float
    period: float
    atoms_per_cell: int

    @property
    def kind(self) -> str:
        """``armchair``, ``zigzag`` or ``chiral``."""
        if self.n == self.m:
            return "armchair"
        if self.m == 0 or self.n == 0:
            return "zigzag"
        return "chiral"

    @property
    def metallic(self) -> bool:
        """Zone-folding rule: metallic when ``(n - m)`` is a multiple of 3."""
        return (self.n - self.m) % 3 == 0


@dataclass
class Nanotube:
    """A built tube or tube assembly; the axis is along z."""

    symbols: list[str]
    positions: np.ndarray
    lattice: np.ndarray | None = None
    pbc: tuple[bool, bool, bool] = (False, False, False)
    walls: list[WallInfo] = field(default_factory=list)
    #: Index of the first atom of each tube in an assembly.
    tube_starts: list[int] = field(default_factory=list)

    @property
    def n_atoms(self) -> int:
        return len(self.symbols)

    @property
    def wall_spacings(self) -> list[float]:
        """Radial gaps between consecutive walls of the first tube."""
        radii = sorted(w.diameter / 2.0 for w in self.walls)
        return [b - a for a, b in zip(radii, radii[1:], strict=False)]

    def atoms(self) -> list[dict[str, object]]:
        """QVF ``structure`` atoms (``symbol``, ``atomic_number``, ``position``)."""
        z = {"H": 1, "C": 6}
        return [
            {"symbol": s, "atomic_number": z[s], "position": [float(v) for v in p]}
            for s, p in zip(self.symbols, self.positions, strict=True)
        ]


def _validate_indices(n: int, m: int) -> tuple[int, int]:
    n, m = int(n), int(m)
    if n < 0 or m < 0 or n + m == 0:
        raise ValueError(f"chiral indices must be non-negative and not both zero, got ({n},{m})")
    if m > n:
        # (n,m) and (m,n) are mirror images; keep the conventional n >= m.
        n, m = m, n
    return n, m


def wall_info(n: int, m: int) -> WallInfo:
    """Diameter, chiral angle, period and cell size of an (n,m) tube."""
    n, m = _validate_indices(n, m)
    a = GRAPHENE_A
    norm2 = n * n + n * m + m * m
    ch = a * math.sqrt(norm2)
    d_r = math.gcd(2 * m + n, 2 * n + m)
    period = math.sqrt(3.0) * ch / d_r
    return WallInfo(
        n=n,
        m=m,
        diameter=ch / math.pi,
        chiral_angle=math.degrees(math.atan2(math.sqrt(3.0) * m, 2 * n + m)),
        period=period,
        atoms_per_cell=4 * norm2 // d_r,
    )


def _unit_cell(n: int, m: int) -> tuple[np.ndarray, float, float]:
    """Cylindrical (theta, z) of one translational cell, its radius and period."""
    a = GRAPHENE_A
    a1 = np.array([a, 0.0])
    a2 = np.array([a / 2.0, a * math.sqrt(3.0) / 2.0])
    ch = n * a1 + m * a2
    d_r = math.gcd(2 * m + n, 2 * n + m)
    t = ((2 * m + n) // d_r) * a1 - ((2 * n + m) // d_r) * a2
    expected = 4 * (n * n + n * m + m * m) // d_r
    # Lattice points covering the Ch x T parallelogram: its corners in
    # (i, j) lattice coordinates bound the search.
    corners = np.array([[0.0, 0.0], ch, t, ch + t])
    inv = np.linalg.inv(np.column_stack((a1, a2)))
    ij = corners @ inv.T
    lo = np.floor(ij.min(axis=0)).astype(int) - 1
    hi = np.ceil(ij.max(axis=0)).astype(int) + 1
    ii, jj = np.meshgrid(np.arange(lo[0], hi[0] + 1), np.arange(lo[1], hi[1] + 1))
    lattice_pts = ii.reshape(-1, 1) * a1 + jj.reshape(-1, 1) * a2
    basis = (a1 + a2) / 3.0
    pts = np.vstack((lattice_pts, lattice_pts + basis))
    u = pts @ ch / (ch @ ch)
    v = pts @ t / (t @ t)
    eps = 1e-9
    keep = (u >= -eps) & (u < 1.0 - eps) & (v >= -eps) & (v < 1.0 - eps)
    u, v = u[keep], v[keep]
    if len(u) != expected:  # pragma: no cover — the construction guarantees this
        raise RuntimeError(f"({n},{m}) cell has {len(u)} atoms, expected {expected}")
    radius = float(np.linalg.norm(ch)) / (2.0 * math.pi)
    period = float(np.linalg.norm(t))
    return np.column_stack((2.0 * math.pi * u, v * period)), radius, period


def _roll(cyl: np.ndarray, radius: float) -> np.ndarray:
    theta, z = cyl[:, 0], cyl[:, 1]
    return np.column_stack((radius * np.cos(theta), radius * np.sin(theta), z))


def _coordination(positions: np.ndarray, cutoff: float = 1.7) -> np.ndarray:
    """Neighbour counts within *cutoff*, by a binned search (no scipy)."""
    n = len(positions)
    counts = np.zeros(n, dtype=int)
    if n == 0:
        return counts
    keys = np.floor(positions / cutoff).astype(np.int64)
    order = np.lexsort(keys.T[::-1])
    keys_sorted = keys[order]
    uniq, start = np.unique(keys_sorted, axis=0, return_index=True)
    bins = {tuple(k): order[s:e] for k, s, e in zip(
        uniq, start, list(start[1:]) + [n], strict=True)}
    c2 = cutoff * cutoff
    for key, members in bins.items():
        neigh = [bins[nb] for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
                 if (nb := (key[0] + dx, key[1] + dy, key[2] + dz)) in bins]
        others = np.concatenate(neigh)
        d2 = ((positions[members][:, None, :] - positions[others][None, :, :]) ** 2).sum(-1)
        counts[members] = ((d2 < c2) & (d2 > 1e-6)).sum(axis=1)
    return counts


def _strip_dangling(positions: np.ndarray) -> np.ndarray:
    """Drop atoms with fewer than two neighbours until none is left.

    Only the two ends of a straight tube along z can dangle, so only atoms
    within a few Angstrom of them are searched; a 10^6-atom tube stays cheap.
    """
    while True:
        z = positions[:, 2]
        window = (z < z.min() + 5.0) | (z > z.max() - 5.0)
        ends = (z < z.min() + 3.0) | (z > z.max() - 3.0)
        idx = np.flatnonzero(window)
        coord = _coordination(positions[idx])
        drop = idx[(coord < 2) & ends[idx]]
        if len(drop) == 0:
            return positions
        positions = np.delete(positions, drop, axis=0)


def _hydrogenate(carbon: np.ndarray) -> np.ndarray:
    """H on every two-coordinated C, along the bisector away from its bonds."""
    z = carbon[:, 2]
    window = np.flatnonzero((z < z.min() + 5.0) | (z > z.max() - 5.0))
    ends = (z[window] < z.min() + 3.0) | (z[window] > z.max() - 3.0)
    coord = _coordination(carbon[window])
    edge = window[(coord == 2) & ends]
    hydrogens = []
    for i in edge:
        d = carbon - carbon[i]
        r = np.linalg.norm(d, axis=1)
        nb = d[(r > 1e-6) & (r < 1.7)]
        out = -(nb / np.linalg.norm(nb, axis=1)[:, None]).sum(axis=0)
        length = float(np.linalg.norm(out))
        if length < 1e-6:
            continue
        hydrogens.append(carbon[i] + out / length * CH_BOND)
    return np.asarray(hydrogens, dtype=float).reshape(-1, 3)


def _c60() -> np.ndarray:
    """Truncated-icosahedron C60 with 1.42 A edges, centred at the origin."""
    phi = (1.0 + math.sqrt(5.0)) / 2.0
    seeds = [(0.0, 1.0, 3.0 * phi), (1.0, 2.0 + phi, 2.0 * phi), (phi, 2.0, 2.0 * phi + 1.0)]
    pts = set()
    for seed in seeds:
        for sx in (-1, 1):
            for sy in (-1, 1):
                for sz in (-1, 1):
                    v = (seed[0] * sx, seed[1] * sy, seed[2] * sz)
                    for k in range(3):  # even permutations of three = cyclic shifts
                        pts.add(tuple(round(c, 9) for c in v[k:] + v[:k]))
    arr = np.array(sorted(pts), dtype=float)
    return arr * (CC_BOND / 2.0)  # edge length is 2 in these coordinates


def _rotate_to_z(points: np.ndarray, axis: np.ndarray) -> np.ndarray:
    axis = axis / np.linalg.norm(axis)
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(axis, z)
    s, c = float(np.linalg.norm(v)), float(axis @ z)
    if s < 1e-12:
        return points.copy() if c > 0 else points * np.array([1.0, -1.0, -1.0])
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]]) / s
    rot = np.eye(3) + s * k + (1 - c) * (k @ k)
    return points @ rot.T


def _cap_templates() -> list[np.ndarray]:
    """C60 oriented along its 5-fold and 3-fold axes, upper half first."""
    c60 = _c60()
    phi = (1.0 + math.sqrt(5.0)) / 2.0
    return [_rotate_to_z(c60, axis) for axis in (np.array([0.0, 1.0, phi]),
                                                   np.array([1.0, 1.0, 1.0]))]


def _attach_cap(tube: np.ndarray, radius: float, top: bool) -> np.ndarray | None:
    """Close one open end with a piece of C60 scaled onto the tube.

    Every template is cut at each of its atomic layers; a cut whose lowest,
    under-coordinated atoms (the rim) number as many as the tube's
    two-coordinated end atoms is scaled radially to the tube, rotated about
    the axis and shifted along it until the rim lies on the end ring. The rim
    is then dropped, so the tube's own end atoms take its place, and the cap
    is accepted when every atom near the joined end is three-coordinated.
    Returns the new positions, or None when no template fits.
    """
    flip = np.array([1.0, 1.0, 1.0 if top else -1.0])
    work = tube * flip  # the end being capped is at the top of ``work``
    z_top = work[:, 2].max()
    near = work[:, 2] > z_top - 4.0
    local = work[near]
    coord = _coordination(local)
    ring = local[(coord == 2) & (local[:, 2] > z_top - 1.5)]
    candidates = []
    for template in _cap_templates():
        layers = np.unique(np.round(template[:, 2], 2))
        for z_cut in layers[layers > -0.5]:
            cap = template[template[:, 2] >= z_cut - 1e-3]
            rim = _coordination(cap) < 3
            if rim.sum() != len(ring) or rim.all():
                continue
            rim_r = float(np.hypot(cap[rim, 0], cap[rim, 1]).mean())
            if abs(rim_r - radius) <= 0.6:
                candidates.append((abs(rim_r - radius), cap, rim, rim_r))
    # The closest rim radius distorts the cap least.
    for _, cap, rim, rim_r in sorted(candidates, key=lambda c: c[0]):
        scaled = cap.copy()
        scaled[:, :2] *= radius / rim_r
        scaled[:, 2] += ring[:, 2].mean() - scaled[rim, 2].mean()
        for step in range(720):
            angle = 2.0 * math.pi * step / 720.0
            ca, sa = math.cos(angle), math.sin(angle)
            rot = scaled @ np.array([[ca, sa, 0.0], [-sa, ca, 0.0], [0.0, 0.0, 1.0]])
            d = np.linalg.norm(rot[rim][:, None, :] - ring[None, :, :], axis=-1)
            if d.min(axis=1).max() > 0.45 or len(set(d.argmin(axis=1))) != len(ring):
                continue
            added = rot[~rim]
            joined = np.vstack((local, added))
            jc = _coordination(joined, cutoff=1.7)
            check = joined[:, 2] > z_top - 1.0
            gaps = np.linalg.norm(added[:, None, :] - local[None, :, :], axis=-1)
            if np.all(jc[check] == 3) and gaps.min() > 1.25:
                closed: np.ndarray = np.vstack((work, added)) * flip
                return closed
    return None


def single_wall(
    n: int,
    m: int,
    length: float | None = None,
    *,
    cells: int | None = None,
    end: str = "open",
) -> Nanotube:
    """An (n,m) tube along z, ``length`` Angstrom or ``cells`` unit cells long.

    ``end`` is ``periodic`` (a whole number of cells, periodic along z),
    ``open`` (dangling atoms removed), ``hydrogen`` (open ends passivated
    with H) or ``cap`` (both ends closed with C60 hemispheres; only tubes
    whose rim matches a C60 half, such as (5,5) and (9,0), can be capped).
    """
    if end not in ENDS:
        raise ValueError(f"end must be one of {', '.join(ENDS)}, got {end!r}")
    info = wall_info(n, m)
    cyl, radius, period = _unit_cell(info.n, info.m)
    if cells is None:
        if length is None:
            raise ValueError("give a length or a number of cells")
        cells = max(1, math.ceil(float(length) / period - 1e-9))
    if cells < 1:
        raise ValueError("a tube needs at least one unit cell")
    shifts = np.arange(cells, dtype=float)[:, None] * period
    tiled = (cyl[None, :, :] + np.column_stack((np.zeros(cells), shifts[:, 0]))[:, None, :])
    positions = _roll(tiled.reshape(-1, 2), radius)
    lattice = None
    pbc = (False, False, False)
    symbols: list[str]
    if end == "periodic":
        box = 2.0 * radius + 15.0
        lattice = np.diag([box, box, cells * period])
        pbc = (False, False, True)
        positions[:, :2] += box / 2.0
    else:
        positions = _strip_dangling(positions)
        if end == "cap":
            for top in (True, False):
                capped = _attach_cap(positions, radius, top)
                if capped is None:
                    raise ValueError(
                        f"no C60 cap fits the ({info.n},{info.m}) rim; "
                        "use an open or hydrogen-terminated end")
                positions = capped
    symbols = ["C"] * len(positions)
    if end == "hydrogen":
        h = _hydrogenate(positions)
        positions = np.vstack((positions, h))
        symbols += ["H"] * len(h)
    return Nanotube(symbols, positions, lattice, pbc, [info], [0])


def wall_series(
    n: int, m: int, walls: int, spacing: float = WALL_SPACING
) -> list[tuple[int, int]]:
    """(n,m) of each wall outward from (n,m), about ``spacing`` apart.

    Each outer wall is the (n',m') whose radius is closest to the previous
    one plus ``spacing``, ties broken by the chiral angle nearest the inner
    wall's, so an armchair core grows armchair walls 3.39 A apart.
    """
    if walls < 1:
        raise ValueError("a tube needs at least one wall")
    series = [_validate_indices(n, m)]
    theta0 = wall_info(*series[0]).chiral_angle
    for _ in range(walls - 1):
        r_prev = wall_info(*series[-1]).diameter / 2.0
        target = r_prev + spacing
        best: tuple[float, tuple[int, int]] | None = None
        n_max = int(2.0 * math.pi * (target + 1.0) / GRAPHENE_A) + 2
        for nn in range(1, n_max + 1):
            for mm in range(0, nn + 1):
                w = wall_info(nn, mm)
                if w.diameter / 2.0 <= r_prev + 0.5 * spacing:
                    continue
                cost = abs(w.diameter / 2.0 - target) + 0.02 * abs(w.chiral_angle - theta0)
                if best is None or cost < best[0]:
                    best = (cost, (nn, mm))
        assert best is not None
        series.append(best[1])
    return series


def multi_wall(
    n: int,
    m: int,
    walls: int,
    length: float,
    *,
    spacing: float = WALL_SPACING,
    end: str = "open",
    series: list[tuple[int, int]] | None = None,
) -> Nanotube:
    """Concentric walls from :func:`wall_series` (or an explicit ``series``).

    ``end`` is ``open`` or ``hydrogen``; caps and periodic multi-wall tubes
    are not built, because walls of different chirality have
    incommensurate periods.
    """
    if end not in ("open", "hydrogen"):
        raise ValueError("a multi-wall tube is built with open or hydrogen ends")
    chosen = series if series is not None else wall_series(n, m, walls, spacing)
    parts = [single_wall(nn, mm, length, end=end) for nn, mm in chosen]
    return _combine(parts, [np.zeros(3)] * len(parts), walls_from=parts)


def _combine(
    parts: list[Nanotube], offsets: list[np.ndarray], walls_from: list[Nanotube] | None = None
) -> Nanotube:
    symbols: list[str] = []
    chunks = []
    starts = []
    for part, offset in zip(parts, offsets, strict=True):
        starts.append(len(symbols))
        symbols += part.symbols
        chunks.append(part.positions + offset)
    walls = [w for p in (walls_from or parts[:1]) for w in p.walls]
    return Nanotube(symbols, np.vstack(chunks), None, (False, False, False), walls, starts)


def hexagonal_sites(count: int, spacing: float) -> np.ndarray:
    """``count`` points of a triangular lattice, filled shell by shell."""
    if count < 1:
        raise ValueError("an assembly needs at least one tube")
    rings = 0
    while 3 * rings * (rings + 1) + 1 < count:
        rings += 1
    pts = []
    for i in range(-rings, rings + 1):
        for j in range(-rings, rings + 1):
            if abs(i + j) <= rings:
                pts.append((spacing * (i + 0.5 * j), spacing * math.sqrt(3.0) / 2.0 * j))
    arr = np.array(pts)
    arr = arr[np.argsort(np.hypot(arr[:, 0], arr[:, 1]), kind="stable")][:count]
    return np.column_stack((arr, np.zeros(count)))


def bundle(tube: Nanotube, count: int, gap: float = BUNDLE_GAP) -> Nanotube:
    """``count`` copies of ``tube`` packed hexagonally, walls ``gap`` apart."""
    outer = max(w.diameter for w in tube.walls)
    sites = hexagonal_sites(count, outer + gap)
    return _combine([tube] * count, list(sites), walls_from=[tube])


def forest(tube: Nanotube, nx: int, ny: int, pitch: float) -> Nanotube:
    """An aligned forest: ``nx`` x ``ny`` copies of ``tube`` on a square grid."""
    if nx < 1 or ny < 1:
        raise ValueError("a forest needs at least one tube per row")
    outer = max(w.diameter for w in tube.walls)
    if pitch < outer:
        raise ValueError(f"pitch {pitch:.1f} A is smaller than the tube diameter {outer:.1f} A")
    sites = [np.array([i * pitch, j * pitch, 0.0]) for j in range(ny) for i in range(nx)]
    return _combine([tube] * (nx * ny), sites, walls_from=[tube])


#: The test scenes of the client performance page.
SCENES: tuple[str, ...] = ("single", "multi", "bundle", "forest")


def scene(kind: str, target_atoms: int) -> Nanotube:
    """A test scene of about ``target_atoms`` atoms.

    ``single`` is an open (10,10) tube, ``multi`` a five-wall tube on a
    (5,5) core, ``bundle`` a 19-tube (10,10) rope and ``forest`` a forest of
    three-wall tubes 5 nm apart, up to 10 x 10 and at least 20 A long, the
    shapes of an aligned array grown from a catalyst film. The length is
    chosen to reach the target.
    """
    if kind not in SCENES:
        raise ValueError(f"scene must be one of {', '.join(SCENES)}")
    per_length = {
        # atoms per Angstrom of tube
        "single": 40 / wall_info(10, 10).period,
        "multi": sum(wall_info(*w).atoms_per_cell / wall_info(*w).period
                     for w in wall_series(5, 5, 5)),
        "bundle": 19 * 40 / wall_info(10, 10).period,
        "forest": 100 * sum(wall_info(*w).atoms_per_cell / wall_info(*w).period
                            for w in wall_series(5, 5, 3)),
    }[kind]
    length = max(5.0, target_atoms / per_length)
    if kind == "single":
        return single_wall(10, 10, length)
    if kind == "multi":
        return multi_wall(5, 5, 5, length)
    if kind == "bundle":
        return bundle(single_wall(10, 10, length), 19)
    side = 10
    while side > 2 and target_atoms / (side * side) < 20.0 * per_length / 100.0:
        side -= 1
    length = max(5.0, target_atoms / (per_length * side * side / 100.0))
    return forest(multi_wall(5, 5, 3, length), side, side, 50.0)
