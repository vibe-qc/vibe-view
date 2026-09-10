"""QVF archive to renderable scene.

The rasterizer knows about spheres, cylinders, meshes and lines; it knows
nothing about chemistry. This module is the bridge: it reads a
:class:`~vibeview.qvf.QVFReader` and produces a :class:`Scene` the canvas
can draw.

Colours and radii come from :mod:`vibeview.renderers.structure` — the same
``cpk_color`` / ``cpk_radius`` the interactive viewer and every PNG capture
use — so a terminal frame and a GUI screenshot of the same archive agree on
what carbon looks like, including any per-element override the user set.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from vibeview.renderers.structure import cpk_color, cpk_radius
from vibeview.tui.raster import Camera, Canvas

_BOHR_TO_ANGSTROM = 0.529177210903

# Mirrors raster._NEAR: the fit must never place geometry behind the near
# plane, or the frame it solves for would clip what it was framing.
_NEAR_FIT = 0.05

# Representation presets: (atom radius scale, explicit radius override,
# bond radius Å, draw bonds). A scale of 1.0 against cpk_radius is full
# van der Waals; ball-and-stick shrinks it so bonds stay visible.
REPRESENTATIONS: dict[str, tuple[float, float | None, float, bool]] = {
    "ball_and_stick": (0.26, None, 0.10, True),
    "licorice": (0.0, 0.16, 0.16, True),
    "spacefill": (1.0, None, 0.0, False),
    "wireframe": (0.0, 0.05, 0.045, True),
    "points": (0.0, 0.09, 0.0, False),
    # Biomolecular: the alpha-carbon trace rather than every atom. Radii and
    # bond radius are the ribbon's thickness, not an atomic size.
    "backbone": (0.0, 0.55, 0.35, True),
}

#: Representations that draw the backbone trace instead of the atom list.
TRACE_REPRESENTATIONS = frozenset({"backbone"})

# Kinds the 3D viewport draws; everything else is a chart or a table. These
# live here rather than in `app` so the Textual-free paths (`show`, tests)
# can classify a section without importing the TUI.
GEOMETRIC_KINDS = frozenset(
    {
        "structure",
        "trajectory",
        "reaction.path",
        "vibrations",
        "volume.density",
        "volume.orbital",
        "volume.spin",
        "volume.elf",
        "volume.difference",
        "volume.generic",
        "volume.potential",
        "volume.rdg",
        "basis.ao",
    }
)

VOLUME_KINDS = frozenset(k for k in GEOMETRIC_KINDS if k.startswith("volume.")) | {"basis.ao"}

_CELL_COLOR = (90, 96, 120)
_ISO_POSITIVE = (255, 138, 30)
_ISO_NEGATIVE = (40, 110, 255)
_ISO_NEUTRAL = (90, 200, 170)

# Distinct hues for chain colouring, in assignment order.
_CHAIN_COLORS = [
    (86, 180, 233),
    (230, 159, 0),
    (0, 158, 115),
    (204, 121, 167),
    (240, 228, 66),
    (213, 94, 0),
    (150, 150, 255),
    (170, 220, 120),
]

# Secondary-structure palette, matching the cartoon renderer's convention.
_SS_COLORS = {"H": (235, 80, 90), "E": (240, 200, 60), "C": (150, 155, 165)}


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """``"#rrggbb"`` to an (r, g, b) int triple."""
    text = value.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except (ValueError, IndexError):
        return (200, 200, 200)


@dataclass
class Mesh:
    """A triangle mesh in world (Å) coordinates."""

    vertices: np.ndarray
    faces: np.ndarray
    normals: np.ndarray
    color: tuple[int, int, int]
    opacity: float = 1.0
    label: str = ""


@dataclass
class Scene:
    """Everything the viewport draws for one frame, in world coordinates."""

    positions: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    radii: np.ndarray = field(default_factory=lambda: np.zeros(0))
    colors: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    labels: list[str] = field(default_factory=list)
    bond_starts: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    bond_ends: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    bond_colors_a: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    bond_colors_b: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    bond_radius: float = 0.10
    line_starts: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    line_ends: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    line_color: tuple[int, int, int] = _CELL_COLOR
    meshes: list[Mesh] = field(default_factory=list)
    #: Why the requested colour scheme was not honoured, if it was not.
    color_notes: list[str] = field(default_factory=list)

    def bounds(self) -> tuple[np.ndarray, float]:
        """Scene centre and bounding radius (Å), padded by atom radii."""
        chunks = []
        if len(self.positions):
            chunks.append(self.positions)
        if len(self.line_starts):
            chunks.append(self.line_starts)
            chunks.append(self.line_ends)
        for mesh in self.meshes:
            if len(mesh.vertices):
                chunks.append(mesh.vertices)
        if not chunks:
            return np.zeros(3), 1.0
        allpts = np.vstack(chunks)
        lo, hi = allpts.min(axis=0), allpts.max(axis=0)
        center = 0.5 * (lo + hi)
        # True bounding-sphere radius, not half the box diagonal: for a flat
        # sheet or a long chain the diagonal over-estimates badly and the
        # structure ends up framed at half the viewport it could use.
        pad = float(self.radii.max()) if len(self.radii) else 0.0
        radius = float(np.linalg.norm(allpts - center, axis=1).max()) + pad
        return center, max(radius, 0.8)


def _extent_points(scene: Scene) -> tuple[np.ndarray, np.ndarray]:
    """Every point that must stay on screen, with its world-space padding."""
    pts: list[np.ndarray] = []
    pad: list[np.ndarray] = []
    if len(scene.positions):
        pts.append(scene.positions)
        pad.append(scene.radii)
    if len(scene.line_starts):
        pts.append(scene.line_starts)
        pad.append(np.zeros(len(scene.line_starts)))
        pts.append(scene.line_ends)
        pad.append(np.zeros(len(scene.line_ends)))
    for mesh in scene.meshes:
        if len(mesh.vertices):
            pts.append(mesh.vertices)
            pad.append(np.zeros(len(mesh.vertices)))
    if not pts:
        return np.zeros((1, 3)), np.zeros(1)
    return np.vstack(pts), np.concatenate(pad)


def fit_camera(
    scene: Scene,
    canvas: Canvas | None = None,
    camera: Camera | None = None,
    margin: float = 0.92,
) -> Camera:
    """Frame ``scene`` tightly at the current orientation.

    A bounding-sphere fit is simple but wastes viewport: the sphere's radius
    includes the depth extent, which contributes nothing to how large the
    structure projects. A flat slab or a long polymer then renders at half
    the size it could. Instead this solves the projection directly for the
    distance at which the widest projected point lands on ``margin`` of the
    viewport edge.

    The screen half-extent of a point is ``x * fov / z`` and ``z`` is
    ``z0 + distance``, so the projected extent decreases monotonically with
    distance — a bisection converges in a handful of iterations and needs no
    assumption about the shape of the system.
    """
    cam = camera or Camera()
    pts, pad = _extent_points(scene)
    center = 0.5 * (pts.min(axis=0) + pts.max(axis=0))
    cam.center = center
    cam.pan = (0.0, 0.0)

    local = (cam.rotation @ (pts - center).T).T
    z0 = local[:, 2]
    # Half-viewport in projected units. The canvas ties both axes to the
    # smaller dimension, so the wider axis gets the slack it deserves.
    if canvas is not None:
        lim_x = (canvas.width / 2.0) / canvas.scale
        lim_y = (canvas.height / 2.0) / canvas.scale
    else:
        lim_x = lim_y = 1.0

    def overflow(distance: float) -> float:
        """Worst-case fraction of the allowed half-extent used, minus one."""
        z = z0 + distance
        if z.min() <= _NEAR_FIT:
            return np.inf
        ex = (np.abs(local[:, 0]) + pad) * cam.fov / z / (lim_x * margin)
        ey = (np.abs(local[:, 1]) + pad) * cam.fov / z / (lim_y * margin)
        return float(max(ex.max(), ey.max())) - 1.0

    lo = max(0.5, _NEAR_FIT - float(z0.min()) + 1e-3)
    hi = max(lo * 2.0, 4.0)
    guard = 0
    while overflow(hi) > 0.0 and guard < 60:
        hi *= 1.6
        guard += 1
    for _ in range(48):
        mid = 0.5 * (lo + hi)
        if overflow(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    cam.distance = hi
    return cam


# ── structure ─────────────────────────────────────────────────────────────


def _atom_colors(atoms, color_mode: str, structure=None, notes=None) -> np.ndarray:
    """Per-atom RGB for the requested colouring scheme.

    ``notes`` collects human-readable reasons when the requested scheme
    cannot be honoured, so the viewer can say why it is showing something
    other than what was asked for rather than silently substituting.
    """
    notes = [] if notes is None else notes
    if color_mode == "chain":
        chains: dict[str, int] = {}
        out = []
        for atom in atoms:
            key = atom.chain_id or "-"
            if key not in chains:
                chains[key] = len(chains)
            out.append(_CHAIN_COLORS[chains[key] % len(_CHAIN_COLORS)])
        return np.array(out, dtype=np.float64)

    if color_mode == "bfactor":
        vals = np.array(
            [a.b_factor if a.b_factor is not None else np.nan for a in atoms], dtype=np.float64
        )
        # A column with no variation carries no information, and a great many
        # files write a constant 0.00 rather than omitting the field. Scaling
        # it anyway paints every atom the single colour at one end of the
        # ramp, which says nothing and reads as a rendering fault rather than
        # as absent data. Same call the cartoon renderer makes when residue
        # names are missing: fall back to something meaningful and say so.
        finite = vals[np.isfinite(vals)]
        if finite.size == 0:
            color_mode = "element"
            notes.append("no b-factors in this archive, coloured by element")
        elif float(finite.max() - finite.min()) <= 0.0:
            color_mode = "element"
            notes.append(
                f"every b-factor is {finite[0]:.2f}, coloured by element instead"
            )
        else:
            lo = float(finite.min())
            hi = float(finite.max())
            span = hi - lo
            t = np.clip((np.nan_to_num(vals, nan=lo) - lo) / span, 0.0, 1.0)
            # blue (low) → white → red (high)
            out = np.empty((len(atoms), 3))
            out[:, 0] = 60 + 195 * t
            out[:, 1] = 90 + 110 * (1.0 - np.abs(2 * t - 1))
            out[:, 2] = 255 - 195 * t
            return out

    if color_mode == "secondary" and structure is not None:
        colors = np.array([hex_to_rgb(cpk_color(a.atomic_number)) for a in atoms], dtype=np.float64)
        try:
            all_chains = structure.chains()
            for chain in structure.chain_ids():
                labels = structure.secondary_structure(chain)
                # secondary_structure() is one label per *alpha carbon*, not
                # per residue: a residue with no CA contributes no label, so
                # zipping it against the raw residue list mislabels a whole
                # chain once a ligand or a water is interleaved. ca_residues()
                # is the single ordering both it and backbone_trace() use, so
                # consuming it is what keeps the three from drifting.
                # Matched on the CA *atom index*, never on residue_seq: PDB
                # numbering wraps at 9999, so a seq is not unique even within
                # one chain, which is why chains() groups by contiguous run in
                # the first place. Keying a lookup by seq would merge two
                # unrelated residues back together.
                ca_indices = {i for _chain, _seq, i in structure.ca_residues(chain)}
                residues = [
                    indices
                    for _seq, indices in all_chains.get(chain, [])
                    if any(i in ca_indices for i in indices)
                ]
                for indices, label in zip(residues, labels):
                    rgb = _SS_COLORS.get(label, _SS_COLORS["C"])
                    for idx in indices:
                        if 0 <= idx < len(colors):
                            colors[idx] = rgb
        except (AttributeError, KeyError, ValueError):
            pass
        return colors

    return np.array([hex_to_rgb(cpk_color(a.atomic_number)) for a in atoms], dtype=np.float64)


def _backbone_scene(structure, color_mode: str) -> Scene | None:
    """The alpha-carbon trace, chain by chain, or None if there is none.

    An all-atom render of a solvated protein is not a picture of a protein:
    a 167k-atom system is overwhelmingly water, and even the solute alone is
    thousands of overlapping spheres that resolve to a solid mass at
    terminal resolution. The trace is what makes the fold legible.

    Built per chain rather than over one flat list, because consecutive
    points are joined: a single trace across a chain boundary would draw a
    bond between one chain's C-terminus and the next chain's N-terminus,
    inventing a covalent link across a gap that may be the width of the box.

    Coloured by secondary structure unless another scheme was asked for,
    since that is the information a fold view exists to show.
    """
    if not structure.has_residues:
        return None

    scene = Scene()
    positions, colors = [], []
    starts, ends, bond_a, bond_b = [], [], [], []
    labels: list[str] = []

    for chain in structure.chain_ids():
        trace = np.asarray(structure.backbone_trace(chain), dtype=np.float64)
        if len(trace) == 0:
            continue
        if color_mode == "chain":
            idx = structure.chain_ids().index(chain)
            rgb = np.tile(_CHAIN_COLORS[idx % len(_CHAIN_COLORS)], (len(trace), 1))
        else:
            ss = structure.secondary_structure(chain)
            rgb = np.array(
                [
                    _SS_COLORS.get(ss[i], _SS_COLORS["C"]) if i < len(ss) else _SS_COLORS["C"]
                    for i in range(len(trace))
                ],
                dtype=np.float64,
            )
        positions.append(trace)
        colors.append(rgb)
        labels.extend(f"{chain or '-'}{i + 1}" for i in range(len(trace)))
        if len(trace) > 1:
            starts.append(trace[:-1])
            ends.append(trace[1:])
            bond_a.append(rgb[:-1])
            bond_b.append(rgb[1:])

    if not positions:
        return None

    scale, fixed, bond_radius, _bonds = REPRESENTATIONS["backbone"]
    scene.positions = np.vstack(positions)
    scene.colors = np.vstack(colors)
    scene.radii = np.full(len(scene.positions), fixed)
    scene.labels = labels
    if starts:
        scene.bond_starts = np.vstack(starts)
        scene.bond_ends = np.vstack(ends)
        scene.bond_colors_a = np.vstack(bond_a)
        scene.bond_colors_b = np.vstack(bond_b)
    scene.bond_radius = bond_radius
    if color_mode not in ("secondary", "chain"):
        scene.color_notes.append(f"backbone is coloured by secondary structure, not {color_mode}")
    return scene


def _cell_edges(lattice: np.ndarray, pbc) -> tuple[np.ndarray, np.ndarray]:
    """Wireframe edges of the cell spanned by the *periodic* axes only.

    LATTICE CONVENTION: the ``structure`` section stores vectors as **rows**
    (``lattice[0]`` = a). ``reaction.path`` stores them as **columns** and in
    bohr — transpose and convert before calling this, or non-orthogonal cells
    come out silently sheared. Mirrors
    ``renderers.structure._draw_unit_cell``; axes outside ``pbc`` are
    synthesized bookkeeping for the AO integrals and are never drawn.
    """
    vecs = [np.asarray(lattice[i], dtype=float) for i, flag in enumerate(pbc) if flag]
    n = len(vecs)
    if n == 0:
        return np.zeros((0, 3)), np.zeros((0, 3))

    corners = [
        sum((vecs[k] for k in range(n) if mask & (1 << k)), np.zeros(3)) for mask in range(1 << n)
    ]
    starts, ends = [], []
    for mask in range(1 << n):
        for k in range(n):
            if mask & (1 << k):
                continue
            starts.append(corners[mask])
            ends.append(corners[mask | (1 << k)])
    return np.array(starts), np.array(ends)


def _replication_offsets(lattice, pbc, replication) -> np.ndarray:
    """Translation vectors for a supercell view, periodic axes only."""
    if lattice is None:
        return np.zeros((1, 3))
    counts = [max(1, int(r)) if flag else 1 for r, flag in zip(replication, pbc)]
    offsets = []
    for i in range(counts[0]):
        for j in range(counts[1]):
            for k in range(counts[2]):
                offsets.append(i * lattice[0] + j * lattice[1] + k * lattice[2])
    return np.array(offsets, dtype=np.float64)


def structure_scene(
    reader,
    *,
    structure_id: str = "structure",
    representation: str = "ball_and_stick",
    color_mode: str = "element",
    show_bonds: bool = True,
    show_cell: bool = True,
    replication: tuple[int, int, int] = (1, 1, 1),
    positions_override: np.ndarray | None = None,
) -> Scene:
    """Build the structure scene, optionally at overridden coordinates.

    ``structure_id`` selects the structure whose atoms and cell are shown;
    this matters for a ``wavefunction.gto`` section whose basis names a
    non-primary ``structure_ref``. ``positions_override`` lets trajectory
    and vibration modes reuse the whole element/bond/cell pipeline for a
    frame's coordinates without re-reading the archive per frame.
    """
    structure = (
        reader.read_structure()
        if structure_id == "structure"
        else reader.read_structure(structure_id)
    )
    atoms = structure.atoms
    scene = Scene()
    if not atoms:
        return scene

    if representation in TRACE_REPRESENTATIONS:
        trace = _backbone_scene(structure, color_mode)
        if trace is not None:
            return trace
        # Nothing to trace: an all-atom fallback beats an empty viewport, but
        # silently showing a different representation than the one asked for
        # is its own defect, so say which and why.
        scene.color_notes.append(
            "no backbone to trace (no alpha carbons), showing all atoms"
        )
        representation = "ball_and_stick"

    base_positions = (
        np.asarray(positions_override, dtype=np.float64)
        if positions_override is not None
        else np.array([a.position for a in atoms], dtype=np.float64)
    )

    scale, fixed, bond_radius, wants_bonds = REPRESENTATIONS.get(
        representation, REPRESENTATIONS["ball_and_stick"]
    )
    radii = (
        np.full(len(atoms), fixed, dtype=np.float64)
        if fixed is not None
        else np.array([cpk_radius(a.atomic_number, scale) for a in atoms], dtype=np.float64)
    )
    color_notes: list[str] = []
    colors = _atom_colors(atoms, color_mode, structure, color_notes)

    lattice = structure.lattice_vectors
    periodic = lattice is not None and any(structure.pbc)
    offsets = (
        _replication_offsets(lattice, structure.pbc, replication)
        if periodic
        else np.zeros((1, 3))
    )
    n_images = len(offsets)

    scene.positions = (base_positions[None, :, :] + offsets[:, None, :]).reshape(-1, 3)
    scene.radii = np.tile(radii, n_images)
    scene.colors = np.tile(colors, (n_images, 1))
    # Replicated images inherit their parent's label, so an index printed in
    # the supercell still names the atom the geometry table lists.
    scene.labels = [
        f"{atoms[i].symbol}{i + 1}" for _ in range(n_images) for i in range(len(atoms))
    ]

    bonds = structure.bonds
    if bonds is None:
        bonds = reader.infer_bonds(structure) if wants_bonds and show_bonds else []
    if show_bonds and wants_bonds and bonds:
        pairs = np.array([(b[0], b[1]) for b in bonds], dtype=np.int64)
        keep = (pairs[:, 0] < len(atoms)) & (pairs[:, 1] < len(atoms))
        pairs = pairs[keep]
        if len(pairs):
            starts = base_positions[pairs[:, 0]]
            ends = base_positions[pairs[:, 1]]
            # A periodic bond list stores the in-cell index pair, so a bond
            # that crosses a cell face has a *direct* separation of a whole
            # lattice vector (graphene: 1.42 Å bonds listed at 2.84 / 3.76 /
            # 5.68 Å). Drawing those endpoints as-is stretches sticks across
            # the box; a length cutoff would instead delete real bonds. Both
            # are fixed by drawing to the nearest periodic image, which is
            # what the interactive renderer does — reuse its helper rather
            # than re-deriving it, since the naive round-the-fraction version
            # picks the wrong image in a hexagonal cell.
            if periodic:
                from vibeview.renderers.structure import _minimum_image

                inv_lattice = np.linalg.inv(lattice)
                ends = np.array(
                    [
                        _minimum_image(s, e, lattice, inv_lattice, structure.pbc)
                        for s, e in zip(starts, ends)
                    ]
                )
            scene.bond_starts = (starts[None] + offsets[:, None, :]).reshape(-1, 3)
            scene.bond_ends = (ends[None] + offsets[:, None, :]).reshape(-1, 3)
            scene.bond_colors_a = np.tile(colors[pairs[:, 0]], (n_images, 1))
            scene.bond_colors_b = np.tile(colors[pairs[:, 1]], (n_images, 1))
    scene.bond_radius = bond_radius
    # extend, not assign: the backbone fallback above may already have
    # left a note on the scene and assignment would discard it.
    scene.color_notes.extend(color_notes)

    if show_cell and periodic:
        starts, ends = _cell_edges(lattice, structure.pbc)
        if len(starts):
            scene.line_starts = (starts[None] + offsets[:, None, :]).reshape(-1, 3)
            scene.line_ends = (ends[None] + offsets[:, None, :]).reshape(-1, 3)

    return scene


# ── volumes ───────────────────────────────────────────────────────────────


def _polydata_to_mesh(poly, color, opacity, label) -> Mesh | None:
    """Convert PyVista PolyData to a plain (verts, faces, normals) Mesh.

    VTK's contour and normals filters are pure computation — no render
    window, no GL context — which is what lets the terminal viewer draw
    isosurfaces on a machine that could never open a PyVista plotter.
    """
    if poly is None or poly.n_points == 0 or poly.n_cells == 0:
        return None
    surface = poly.triangulate()
    # split_vertices=False keeps the point count equal to the input's, so
    # `faces` indices stay valid against `points`; splitting would duplicate
    # vertices along sharp edges and silently renumber them. The kwarg was
    # named `splitting` before PyVista 0.45 — passing the old name raises
    # TypeError rather than being ignored, so this is version-sensitive.
    with_normals = surface.compute_normals(
        cell_normals=False,
        point_normals=True,
        auto_orient_normals=False,
        split_vertices=False,
    )

    vertices = np.asarray(with_normals.points, dtype=np.float64)
    faces_flat = np.asarray(with_normals.faces, dtype=np.int64)
    if faces_flat.size == 0:
        return None
    # A triangulated PolyData stores faces as [3, i, j, k] runs.
    faces = faces_flat.reshape(-1, 4)[:, 1:]
    normals = np.asarray(
        with_normals.point_data.get("Normals", np.zeros_like(vertices)), dtype=np.float64
    )
    return Mesh(vertices, faces, normals, color, opacity, label)


def sampled_field_meshes(
    grid,
    data: np.ndarray,
    *,
    isovalue: float = 0.05,
    signed: bool | None = None,
    grid_units: str = "angstrom",
    replication: tuple[int, int, int] = (1, 1, 1),
    lattice_vectors: np.ndarray | None = None,
    pbc=None,
    opacity: float = 1.0,
) -> list[Mesh]:
    """Contour an in-memory scalar field into terminal-renderable meshes.

    This is the unit-aware path for fields evaluated from an embedded GTO
    wavefunction. Those grids are already in angstroms, unlike stored QVF
    ``volume.*`` grids, whose coordinates are in bohr. Signed fields request
    positive and negative lobes independently; a legitimate one-sign orbital
    simply returns the lobe that exists.
    """
    from vibeview.renderers.volume import build_isosurface_mesh

    values = np.asarray(data)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return []
    lo = float(finite.min())
    hi = float(finite.max())
    if signed is None:
        signed = lo < 0.0
    magnitude = abs(float(isovalue))
    levels = (
        [(magnitude, _ISO_POSITIVE), (-magnitude, _ISO_NEGATIVE)]
        if signed
        else [(magnitude, _ISO_NEUTRAL)]
    )

    meshes: list[Mesh] = []
    for level, color in levels:
        if hi < level or lo > level:
            continue
        poly = build_isosurface_mesh(
            values,
            grid,
            level,
            grid_units=grid_units,
            replication=replication,
            lattice_vectors=lattice_vectors,
            pbc=pbc,
        )
        mesh = _polydata_to_mesh(poly, color, opacity, f"{level:+.4g}")
        if mesh is not None:
            meshes.append(mesh)
    return meshes


def volume_meshes(
    reader,
    section_id: str,
    *,
    isovalue: float = 0.05,
    replication: tuple[int, int, int] = (1, 1, 1),
    opacity: float = 1.0,
) -> list[Mesh]:
    """Isosurface meshes for a ``volume.*`` / ``basis.ao`` section.

    Signed fields (orbitals, spin, difference densities) get both lobes;
    strictly positive fields (density, ELF, potential) get one surface.
    """
    from vibeview.renderers.volume import build_isosurface_mesh

    grid = reader.read_volume_grid(section_id)
    data = reader.read_volume_data(section_id)
    if data is None:
        return []

    section = reader.get_section(section_id)
    structure = None
    try:
        structure = reader.read_structure()
    except Exception:  # noqa: BLE001 — a volume without a structure still draws
        structure = None
    lattice = structure.lattice_vectors if structure is not None else None
    pbc = structure.pbc if structure is not None else None

    signed = bool(np.nanmin(data) < 0.0) and getattr(section, "kind", "") != "volume.density"
    levels = (
        [(abs(isovalue), _ISO_POSITIVE), (-abs(isovalue), _ISO_NEGATIVE)]
        if signed
        else [(abs(isovalue), _ISO_NEUTRAL)]
    )

    meshes = []
    for level, color in levels:
        if data.max() < level or data.min() > level:
            continue
        poly = build_isosurface_mesh(
            data,
            grid,
            level,
            replication=replication,
            lattice_vectors=lattice,
            pbc=pbc,
        )
        mesh = _polydata_to_mesh(poly, color, opacity, f"{level:+.4g}")
        if mesh is not None:
            meshes.append(mesh)
    return meshes


# ── frame sequences ───────────────────────────────────────────────────────


def trajectory_frames(reader, section_id: str) -> tuple[np.ndarray, list[float] | None]:
    """``(coords [n_frames, n_atoms, 3] Å, per-frame energies or None)``."""
    kind = reader.get_section(section_id).kind
    if kind == "reaction.path":
        data = reader.read_reaction_path(section_id)
    else:
        data = reader.read_trajectory(section_id)
    return np.asarray(data.coords, dtype=np.float64), data.energies


def vibration_frames(
    reader,
    section_id: str,
    mode_index: int,
    *,
    n_frames: int = 16,
    amplitude: float = 0.6,
) -> tuple[np.ndarray, float]:
    """Animate one normal mode as a closed displacement cycle.

    Returns ``(coords [n_frames, n_atoms, 3], frequency_cm1)``. The
    displacement is scaled so the largest atomic excursion is ``amplitude``
    Å, which keeps a stiff X-H stretch and a soft torsion equally legible.
    """
    data = reader.read_vibrations(section_id)
    base = np.array([a.position for a in data.atoms], dtype=np.float64)
    n_modes = len(data.frequencies)
    if n_modes == 0:
        return base[None, :, :], 0.0
    idx = int(np.clip(mode_index, 0, n_modes - 1))
    disp = np.asarray(data.displacements[idx], dtype=np.float64)
    peak = float(np.abs(disp).max())
    if peak > 1e-9:
        disp = disp / peak * amplitude
    phases = np.sin(np.linspace(0.0, 2.0 * np.pi, n_frames, endpoint=False))
    coords = base[None, :, :] + phases[:, None, None] * disp[None, :, :]
    return coords, float(data.frequencies[idx])


def reaction_path_cell(data) -> tuple[np.ndarray, np.ndarray] | None:
    """Cell edges for a periodic reaction path, or None when molecular.

    ``reaction.path`` stores lattice vectors as **columns in bohr** — the
    opposite convention from the ``structure`` section (rows, Å). Transposed
    and converted here so :func:`_cell_edges` sees its documented input.
    """
    lattice = getattr(data, "lattice", None)
    if lattice is None:
        return None
    lat = np.asarray(lattice, dtype=np.float64)
    if lat.ndim == 3:
        lat = lat[0]
    rows = lat.T * _BOHR_TO_ANGSTROM
    dim = getattr(data, "dim", None) or 3
    pbc = tuple(i < dim for i in range(3))
    return _cell_edges(rows, pbc)


# ── drawing ───────────────────────────────────────────────────────────────


def render(scene: Scene, canvas: Canvas, camera: Camera) -> None:
    """Draw a scene into a canvas. Bonds first, then atoms, then meshes.

    Order is a performance hint only — the depth buffer, not the call
    sequence, decides what is visible.
    """
    if len(scene.line_starts):
        canvas.draw_lines(
            camera.to_camera(scene.line_starts),
            camera.to_camera(scene.line_ends),
            scene.line_color,
            camera.fov,
        )
    if len(scene.bond_starts) and scene.bond_radius > 0:
        canvas.draw_cylinders(
            camera.to_camera(scene.bond_starts),
            camera.to_camera(scene.bond_ends),
            scene.bond_radius,
            scene.bond_colors_a,
            scene.bond_colors_b,
            camera.fov,
        )
    if len(scene.positions):
        canvas.draw_spheres(
            camera.to_camera(scene.positions), scene.radii, scene.colors, camera.fov
        )
    for mesh in scene.meshes:
        if len(mesh.vertices) == 0:
            continue
        canvas.draw_mesh(
            camera.to_camera(mesh.vertices),
            mesh.faces,
            camera.directions(mesh.normals),
            mesh.color,
            camera.fov,
            mesh.opacity,
        )


def atom_screen_positions(
    scene: Scene, canvas: Canvas, camera: Camera
) -> tuple[np.ndarray, np.ndarray]:
    """Screen-space ``(xy, z)`` of every atom — used to place index labels."""
    if not len(scene.positions):
        return np.zeros((0, 2)), np.zeros(0)
    return canvas.project(camera.to_camera(scene.positions), camera.fov)
