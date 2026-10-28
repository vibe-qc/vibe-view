"""Metal partners for a nanotube: slabs, nanoparticles and molecular linkers.

Places one partner at an open end or on the sidewall of a tube at a chosen
distance and registry, the cluster models of a tube–metal contact:

* a **slab** of an fcc metal cut on (111), (100) or (110) by
  :func:`vibeview.crystal_builder.miller_slab`, a finite patch sized to the
  contact;
* a **nanoparticle**, the fcc atoms within a radius of a lattice site;
* a **linker**, the aminophenyl amide of diazonium grafting, tube–C(=O)–NH–
  C6H4–, on an open-end or a sidewall carbon, optionally with a slab on its
  free aryl carbon (the tube–linker–metal contacts of Nawarathne et al.,
  ACS Appl. Nano Mater. 2023, 6, 22001, doi:10.1021/acsanm.3c04137, and
  Nanoscale Adv., doi:10.1039/d3na00500c).

The tube is found in the given atoms, so a tube built earlier, read from a
file or edited stays usable: its axis is the principal axis of its carbon
atoms, its open ends the two-coordinated carbons at either extreme.

Distances are between the nearest partner atom and the tube atom it faces;
the default is the sum of their covalent radii (Cordero et al., Dalton
Trans. 2008, 2832), a bonded contact. Registry puts that tube atom over a
``top``, ``bridge`` or ``hollow`` site of the partner's facing layer.
Geometry is a starting point for an optimisation, as everywhere in the
builder.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from vibeview.crystal_builder import miller_slab
from vibeview.elements import SYMBOL_TO_Z, covalent_radius

#: fcc lattice constants (Angstrom) at room temperature, Kittel,
#: *Introduction to Solid State Physics*, 8th ed., Wiley 2005, ch. 1.
FCC_METALS: dict[str, float] = {
    "Al": 4.05, "Ni": 3.52, "Cu": 3.61, "Pd": 3.89, "Ag": 4.09, "Pt": 3.92, "Au": 4.08,
}
FACETS: tuple[str, ...] = ("111", "100", "110")
REGISTRIES: tuple[str, ...] = ("top", "bridge", "hollow")
SITES: tuple[str, ...] = ("end", "sidewall")
PARTNERS: tuple[str, ...] = ("slab", "particle", "linker")

_CC_SINGLE = 1.50
_CO_DOUBLE = 1.23
_CN_AMIDE = 1.34
_NH = 1.01
_N_ARYL = 1.41
_RING = 1.39
_CH = 1.08
#: Closest a partner atom may come to a tube atom other than the contact.
_CLASH = 1.5


@dataclass
class Assembly:
    """Atoms of the tube plus its partner, and what was placed."""

    symbols: list[str]
    positions: np.ndarray
    #: Indices of the partner's atoms in ``symbols``.
    partner: list[int] = field(default_factory=list)
    #: (tube atom, partner atom, distance) of the contact that was set.
    contact: tuple[int, int, float] | None = None
    note: str = ""


@dataclass(frozen=True)
class TubeFrame:
    """Where a tube is: centre, unit axis, radius and its two open-end rings."""

    center: np.ndarray
    axis: np.ndarray
    radius: float
    ends: tuple[np.ndarray, np.ndarray]


def _neighbour_counts(positions: np.ndarray, cutoff: float) -> np.ndarray:
    from vibeview.nanotube import _coordination

    return _coordination(positions, cutoff)


def tube_frame(symbols: list[str], positions: np.ndarray) -> TubeFrame:
    """The axis, radius and open-end atoms of the tube among *positions*.

    Uses the carbon atoms only. Each entry of ``ends`` holds indices into
    *positions* of two-coordinated carbons at the low and the high end of
    the axis; an end without any (capped or hydrogenated) is empty.
    """
    carbon = np.array([i for i, s in enumerate(symbols) if s == "C"], dtype=int)
    if len(carbon) < 12:
        raise ValueError("no nanotube found: the structure has fewer than 12 carbon atoms")
    pos = positions[carbon]
    center = pos.mean(axis=0)
    _, _, vt = np.linalg.svd(pos - center, full_matrices=False)
    axis = vt[0] / np.linalg.norm(vt[0])
    if axis @ np.array([0.0, 0.0, 1.0]) < 0:
        axis = -axis
    along = (pos - center) @ axis
    radial = np.linalg.norm((pos - center) - np.outer(along, axis), axis=1)
    radius = float(np.median(radial))
    coord = _neighbour_counts(pos, 1.7)
    edge = coord == 2
    lo = carbon[edge & (along < along.min() + 1.5)]
    hi = carbon[edge & (along > along.max() - 1.5)]
    return TubeFrame(center, axis, radius, (lo, hi))


def _rotation_to(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rotation matrix turning unit vector *a* onto unit vector *b*."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    s, c = float(np.linalg.norm(v)), float(a @ b)
    if s < 1e-10:
        if c > 0:
            return np.eye(3)
        perp = np.cross(a, [1.0, 0.0, 0.0])
        if np.linalg.norm(perp) < 1e-6:
            perp = np.cross(a, [0.0, 1.0, 0.0])
        perp /= np.linalg.norm(perp)
        return 2.0 * np.outer(perp, perp) - np.eye(3)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]]) / s
    return np.eye(3) + s * k + (1 - c) * (k @ k)


def _fcc_cell(metal: str) -> tuple[np.ndarray, list[dict[str, object]]]:
    if metal not in FCC_METALS:
        raise ValueError(f"metal must be one of {', '.join(FCC_METALS)}, got {metal!r}")
    a = FCC_METALS[metal]
    basis = [(0, 0, 0), (0, 0.5, 0.5), (0.5, 0, 0.5), (0.5, 0.5, 0)]
    return np.eye(3) * a, [{"symbol": metal, "position": [a * x for x in b]} for b in basis]


def fcc_slab(metal: str, facet: str, half_width: float, layers: int = 3) -> np.ndarray:
    """A square patch of an fcc (hkl) slab: top layer at z = 0, below it the rest.

    ``half_width`` is half the patch's side (Angstrom); the patch is
    centred on a top-layer atom at the origin.
    """
    if facet not in FACETS:
        raise ValueError(f"facet must be one of {', '.join(FACETS)}, got {facet!r}")
    if layers < 1:
        raise ValueError("a slab needs at least one layer")
    cell, atoms = _fcc_cell(metal)
    hkl = (int(facet[0]), int(facet[1]), int(facet[2]))
    # Enough repetitions of the surface cell to hold ``layers`` atomic planes.
    slab_cell, slab_atoms = miller_slab(cell, atoms, hkl, n_layers=layers + 2, vacuum=0.0)
    base = np.array([a["position"] for a in slab_atoms], dtype=float)
    normal = slab_cell[2] / np.linalg.norm(slab_cell[2])
    reps = int(math.ceil((2.0 * half_width + 8.0)
                         / min(np.linalg.norm(slab_cell[0]), np.linalg.norm(slab_cell[1])))) + 1
    shifts = np.array([i * slab_cell[0] + j * slab_cell[1]
                       for i in range(-reps, reps + 1) for j in range(-reps, reps + 1)])
    pts = (base[None, :, :] + shifts[:, None, :]).reshape(-1, 3)
    pts = pts @ _rotation_to(normal, np.array([0.0, 0.0, 1.0])).T
    planes = np.unique(np.round(pts[:, 2], 3))
    keep_planes = planes[-layers:]
    pts = pts[np.isin(np.round(pts[:, 2], 3), keep_planes)]
    top = pts[np.isclose(pts[:, 2], keep_planes[-1], atol=1e-3)]
    origin = top[np.argmin(np.linalg.norm(top[:, :2] - top[:, :2].mean(axis=0), axis=1))]
    pts = pts - origin
    inside = (np.abs(pts[:, 0]) <= half_width + 1e-6) & (np.abs(pts[:, 1]) <= half_width + 1e-6)
    patch: np.ndarray = pts[inside]
    return patch


def fcc_particle(metal: str, radius: float) -> np.ndarray:
    """fcc atoms within ``radius`` of a lattice site, centred at the origin."""
    if radius <= 0:
        raise ValueError("a nanoparticle needs a positive radius")
    cell, atoms = _fcc_cell(metal)
    a = cell[0, 0]
    n = int(math.ceil(radius / a)) + 1
    grid = np.array([(i, j, k) for i in range(-n, n + 1) for j in range(-n, n + 1)
                     for k in range(-n, n + 1)], dtype=float) * a
    basis = np.array([at["position"] for at in atoms], dtype=float)
    pts = (grid[:, None, :] + basis[None, :, :]).reshape(-1, 3)
    particle: np.ndarray = pts[np.linalg.norm(pts, axis=1) <= radius + 1e-6]
    return particle


def site_offset(layer: np.ndarray, registry: str) -> np.ndarray:
    """In-plane (x, y) of a ``top``, ``bridge`` or ``hollow`` site near the origin.

    *layer* is the facing layer's atoms (z ignored). ``hollow`` is the point
    within 3 A of the origin farthest from every atom, which is the
    three-fold hollow of (111) and the four-fold one of (100).
    """
    if registry not in REGISTRIES:
        raise ValueError(f"registry must be one of {', '.join(REGISTRIES)}, got {registry!r}")
    xy = layer[:, :2]
    first: np.ndarray = xy[np.argmin(np.linalg.norm(xy, axis=1))]
    if registry == "top":
        return first
    d = np.linalg.norm(xy - first, axis=1)
    d[d < 1e-6] = np.inf
    if registry == "bridge":
        bridge: np.ndarray = 0.5 * (first + xy[np.argmin(d)])
        return bridge
    g = np.linspace(-3.0, 3.0, 121)
    gx, gy = np.meshgrid(g, g)
    cand = np.column_stack((gx.ravel(), gy.ravel())) + first
    nearest = np.min(np.linalg.norm(cand[:, None, :] - xy[None, :, :], axis=-1), axis=1)
    rough = cand[np.argmax(nearest)]
    # Snap to the point equidistant from the three atoms around it.
    a, b, c = xy[np.argsort(np.linalg.norm(xy - rough, axis=1))[:3]]
    lhs = 2.0 * np.array([b - a, c - a])
    rhs = np.array([b @ b - a @ a, c @ c - a @ a])
    return np.linalg.solve(lhs, rhs)


def bond_distance(a: str, b: str) -> float:
    """Default contact distance: the sum of Cordero covalent radii."""
    return covalent_radius(SYMBOL_TO_Z[a], 1.5) + covalent_radius(SYMBOL_TO_Z[b], 1.5)


def _contact_atom(symbols: list[str], positions: np.ndarray, frame: TubeFrame,
                  site: str) -> tuple[int, np.ndarray]:
    """The tube carbon a partner faces, and the outward unit vector there."""
    if site not in SITES:
        raise ValueError(f"site must be one of {', '.join(SITES)}, got {site!r}")
    rel = positions - frame.center
    along = rel @ frame.axis
    if site == "end":
        ring = frame.ends[0]
        if len(ring) == 0:
            raise ValueError("the tube has no open end to attach to (capped or hydrogenated)")
        # The lowest open end faces down the axis.
        idx = int(ring[np.argmin(along[ring])])
        return idx, -frame.axis
    carbon = np.array([i for i, s in enumerate(symbols) if s == "C"])
    mid = carbon[np.argmin(np.abs(along[carbon]))]
    radial = rel[mid] - along[mid] * frame.axis
    # A sidewall atom on the tube's -x side (any fixed side will do), at mid length.
    side = np.cross(frame.axis, [0.0, 1.0, 0.0])
    if np.linalg.norm(side) < 1e-6:
        side = np.cross(frame.axis, [1.0, 0.0, 0.0])
    side /= np.linalg.norm(side)
    side = -side
    ring = carbon[np.abs(along[carbon] - along[mid]) < 1.5]
    radial = rel[ring] - np.outer(along[ring], frame.axis)
    idx = int(ring[np.argmax(radial @ side)])
    out = rel[idx] - along[idx] * frame.axis
    return idx, out / np.linalg.norm(out)


def _place_layer_partner(
    positions: np.ndarray, partner: np.ndarray, anchor: np.ndarray, outward: np.ndarray,
    distance: float, registry: str, facing_layer: np.ndarray,
) -> np.ndarray:
    """Turn a partner built facing +z so it faces *anchor* along *outward*.

    *partner* has its facing layer at z = 0 and extends to z < 0; the site of
    *registry* is put under *anchor*, with the anchor *distance* from the
    site's nearest atoms.
    """
    shift = site_offset(facing_layer, registry)
    # The anchor sits over the site; its distance to the nearest atoms of
    # the facing layer (one for top, two for bridge, three or four for
    # hollow) is *distance*, so the layer's height is set from the site's
    # in-plane distance to them.
    in_plane = float(np.min(np.linalg.norm(facing_layer[:, :2] - shift, axis=1)))
    if distance <= in_plane + 0.05:
        raise ValueError(
            f"a {registry} site is {in_plane:.2f} A from its nearest atom in plane; "
            f"the distance must be larger than that, got {distance:.2f} A")
    height = math.sqrt(distance * distance - in_plane * in_plane)
    local = partner - np.array([shift[0], shift[1], 0.0])
    # Local +z (towards the tube) must point back along -outward.
    rot = _rotation_to(np.array([0.0, 0.0, 1.0]), -outward)
    world = local @ rot.T
    placed: np.ndarray = world + anchor + outward * height
    return placed


def _tube_width(frame: TubeFrame, positions: np.ndarray, site: str) -> float:
    if site == "end":
        return frame.radius + 4.0
    length = float(np.ptp((positions - frame.center) @ frame.axis))
    return max(frame.radius, 0.5 * length) + 2.0


def _finish(symbols: list[str], positions: np.ndarray, extra_symbols: list[str],
            extra: np.ndarray, anchor_idx: int, note: str) -> Assembly:
    n = len(symbols)
    all_pos = np.vstack((positions, extra))
    d = np.linalg.norm(extra - positions[anchor_idx], axis=1)
    j = int(np.argmin(d))
    clash = np.linalg.norm(extra[:, None, :] - positions[None, :, :], axis=-1)
    clash[j, anchor_idx] = np.inf
    if clash.min() < _CLASH:
        raise ValueError(
            f"the partner comes within {clash.min():.2f} A of another tube atom; "
            "increase the distance or choose another registry")
    return Assembly(list(symbols) + list(extra_symbols), all_pos, list(range(n, n + len(extra))),
                    (anchor_idx, n + j, float(d[j])), note)


def add_slab(symbols: list[str], positions: np.ndarray, *, metal: str = "Cu",
             facet: str = "111", site: str = "end", distance: float | None = None,
             registry: str = "top", layers: int = 3) -> Assembly:
    """A slab facing the tube's lowest open end or its sidewall."""
    positions = np.asarray(positions, dtype=float)
    frame = tube_frame(symbols, positions)
    anchor_idx, outward = _contact_atom(symbols, positions, frame, site)
    if distance is None:
        distance = bond_distance("C", metal)
    slab = fcc_slab(metal, facet, _tube_width(frame, positions, site), layers)
    layer = slab[np.isclose(slab[:, 2], 0.0, atol=1e-3)]
    placed = _place_layer_partner(positions, slab, positions[anchor_idx], outward, distance,
                                  registry, layer)
    note = f"{metal}({facet}) slab, {layers} layers, {registry} site under the {site}"
    return _finish(symbols, positions, [metal] * len(placed), placed, anchor_idx, note)


def add_particle(symbols: list[str], positions: np.ndarray, *, metal: str = "Au",
                 radius: float = 6.0, site: str = "end", distance: float | None = None,
                 registry: str = "top") -> Assembly:
    """A nanoparticle touching the tube's lowest open end or its sidewall.

    The particle's facing layer is the plane of its atoms nearest the tube,
    found after turning it so its (111) direction faces the tube.
    """
    positions = np.asarray(positions, dtype=float)
    frame = tube_frame(symbols, positions)
    anchor_idx, outward = _contact_atom(symbols, positions, frame, site)
    if distance is None:
        distance = bond_distance("C", metal)
    particle = fcc_particle(metal, radius)
    particle = particle @ _rotation_to(np.array([1.0, 1.0, 1.0]), np.array([0.0, 0.0, 1.0])).T
    particle[:, 2] -= particle[:, 2].max()
    layer = particle[np.isclose(particle[:, 2], 0.0, atol=0.2)]
    placed = _place_layer_partner(positions, particle, positions[anchor_idx], outward, distance,
                                  registry, layer)
    note = f"{metal} nanoparticle, {len(placed)} atoms, {registry} site at the {site}"
    return _finish(symbols, positions, [metal] * len(placed), placed, anchor_idx, note)


def linker_fragment(outward: np.ndarray) -> tuple[list[str], np.ndarray, int]:
    """Aminophenyl amide, –C(=O)–NH–C6H4–, built from the origin along *outward*.

    The origin is the tube carbon it binds; returns symbols, positions and
    the index of the free para carbon, which binds a metal.
    """
    u = outward / np.linalg.norm(outward)
    p = np.cross(u, [0.0, 0.0, 1.0])
    if np.linalg.norm(p) < 1e-6:
        p = np.cross(u, [0.0, 1.0, 0.0])
    p /= np.linalg.norm(p)
    c1 = _CC_SINGLE * u
    d_o = 0.5 * u + math.sqrt(3) / 2 * p
    d_n = 0.5 * u - math.sqrt(3) / 2 * p
    o = c1 + _CO_DOUBLE * d_o
    n = c1 + _CN_AMIDE * d_n
    ipso = n + _N_ARYL * u
    h_dir = (d_n - u) / np.linalg.norm(d_n - u)
    hn = n + _NH * h_dir
    centre = ipso + _RING * u
    symbols = ["C", "O", "N", "H"]
    pos = [c1, o, n, hn]
    para = -1
    for theta in (180, 120, 60, 0, -60, -120):
        t = math.radians(theta)
        radial = math.cos(t) * u + math.sin(t) * p
        symbols.append("C")
        pos.append(centre + _RING * radial)
        if theta == 0:
            para = len(pos) - 1
        elif theta != 180:  # the ipso carbon carries the amide N
            symbols.append("H")
            pos.append(centre + (_RING + _CH) * radial)
    return symbols, np.array(pos), para


def add_linker(symbols: list[str], positions: np.ndarray, *, site: str = "end",
               metal: str | None = None, facet: str = "111", distance: float | None = None,
               registry: str = "top", layers: int = 3) -> Assembly:
    """An aminophenyl amide on an open-end or sidewall carbon; a slab on it if ``metal``.

    A sidewall attachment makes that carbon sp3; the tube is not relaxed.
    """
    positions = np.asarray(positions, dtype=float)
    frame = tube_frame(symbols, positions)
    anchor_idx, outward = _contact_atom(symbols, positions, frame, site)
    l_sym, l_pos, para = linker_fragment(outward)
    l_pos = l_pos + positions[anchor_idx]
    note = f"aminophenyl amide linker on the {site}"
    if metal is None:
        return _finish(symbols, positions, l_sym, l_pos, anchor_idx, note)
    if distance is None:
        distance = bond_distance("C", metal)
    width = 6.0
    slab = fcc_slab(metal, facet, width, layers)
    layer = slab[np.isclose(slab[:, 2], 0.0, atol=1e-3)]
    placed = _place_layer_partner(positions, slab, l_pos[para], outward, distance, registry, layer)
    extra_sym = l_sym + [metal] * len(placed)
    extra = np.vstack((l_pos, placed))
    result = _finish(symbols, positions, extra_sym, extra, anchor_idx,
                     f"{note}, {metal}({facet}) slab on its aryl carbon")
    n0 = len(symbols)
    result.contact = (n0 + para, n0 + len(l_sym) + int(np.argmin(
        np.linalg.norm(placed - l_pos[para], axis=1))), float(distance))
    return result


def build_partner(symbols: list[str], positions: np.ndarray, partner: str,
                  **options: object) -> Assembly:
    """Dispatch to :func:`add_slab`, :func:`add_particle` or :func:`add_linker`."""
    if partner == "slab":
        return add_slab(symbols, positions, **options)  # type: ignore[arg-type]
    if partner == "particle":
        return add_particle(symbols, positions, **options)  # type: ignore[arg-type]
    if partner == "linker":
        return add_linker(symbols, positions, **options)  # type: ignore[arg-type]
    raise ValueError(f"partner must be one of {', '.join(PARTNERS)}, got {partner!r}")
