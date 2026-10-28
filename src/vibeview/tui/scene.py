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

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import numpy as np

from vibeview.kinds import VOLUME_KINDS
from vibeview.renderers import palette
from vibeview.renderers.cell import (
    bond_far_endpoints,
    cell_edges,
    reaction_lattice_rows_angstrom,
    supercell_offsets,
)
from vibeview.renderers.palette import CHAIN, SS
from vibeview.renderers.structure import cpk_color, cpk_radius
from vibeview.tui.raster import NEAR_PLANE, Camera, Canvas

if TYPE_CHECKING:
    import pyvista as pv

    from vibeview.bonds import Bond
    from vibeview.qvf import (
        Atom,
        GridData,
        QVFReader,
        ReactionPathData,
        StructureData,
        TrajectoryData,
    )


# Mirrors raster._NEAR: the fit must never place geometry behind the near
# plane, or the frame it solves for would clip what it was framing.

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
GEOMETRIC_KINDS = (
    frozenset({
        "structure", "trajectory", "reaction.path", "vibrations", "volume.vector",
        "volume.complex", "volume.series",
    })
    | VOLUME_KINDS
)

_CELL_COLOR = (90, 96, 120)
# One lobe convention everywhere (#38-19, maintainer decision): + blue, − red.
_ISO_POSITIVE = palette.rgb(palette.ISO_POSITIVE)
_ISO_NEGATIVE = palette.rgb(palette.ISO_NEGATIVE)
_ISO_NEUTRAL = (90, 200, 170)

# Distinct hues for chain colouring, in assignment order.
_CHAIN_COLORS = palette.rgb_palette(CHAIN)

# Secondary-structure palette: the cartoon renderer's, in RGB.
_SS_COLORS = {label: palette.rgb(color) for label, color in SS.items()}


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
        if z.min() <= NEAR_PLANE:
            return np.inf
        ex = (np.abs(local[:, 0]) + pad) * cam.fov / z / (lim_x * margin)
        ey = (np.abs(local[:, 1]) + pad) * cam.fov / z / (lim_y * margin)
        return float(max(ex.max(), ey.max())) - 1.0

    lo = max(0.5, NEAR_PLANE - float(z0.min()) + 1e-3)
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


def _chain_order(structure: StructureData | None) -> dict[str, int]:
    """Chain palette index by :meth:`StructureData.chains` order (#38-21).

    The browser colours chain ``order`` of ``enumerate(structure.chains())``;
    the terminal must agree, or a file whose supplied ``chains`` list differs
    from the atom order colours the same chain differently per surface.
    """
    if structure is None:
        return {}
    try:
        return {(str(c) or "-"): i for i, c in enumerate(structure.chains())}
    except (AttributeError, KeyError, ValueError):
        return {}


def _atom_colors(
    atoms: Sequence[Atom],
    color_mode: str,
    structure: StructureData | None = None,
    notes: list[str] | None = None,
) -> np.ndarray:
    """Per-atom RGB for the requested colouring scheme.

    ``notes`` collects human-readable reasons when the requested scheme
    cannot be honoured, so the viewer can say why it is showing something
    other than what was asked for rather than silently substituting.
    """
    notes = [] if notes is None else notes
    if color_mode == "chain":
        # Palette index by structure.chains() order, the order the browser
        # and _backbone_scene use (#38-21); chains it does not list (atoms
        # with no residue identity) follow in order of first appearance.
        chains = _chain_order(structure)
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
        else:
            # The browser's ramp (#38-22): cold → warm over the observed
            # range, an absent value grey rather than the coldest colour, and
            # a column with no variation at the midpoint. Report the flat
            # column so it does not read as a rendering fault.
            if float(finite.max() - finite.min()) <= 0.0:
                notes.append(
                    f"every b-factor is {finite[0]:.2f}, shown at the ramp midpoint"
                )
            return np.array(palette.sequential_ramp(vals.tolist()), dtype=np.float64)

    if color_mode == "secondary" and structure is not None:
        colors = np.array(
            [palette.rgb(cpk_color(a.atomic_number)) for a in atoms], dtype=np.float64
        )
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
                # strict=False: the two are meant to line up one-to-one, but
                # a residue carrying two alpha carbons (an altloc) puts one
                # entry in ``residues`` against two labels. Colour what lines
                # up rather than dropping the chain out of the TUI.
                for indices, label in zip(residues, labels, strict=False):
                    rgb = _SS_COLORS.get(label, _SS_COLORS["C"])
                    for idx in indices:
                        if 0 <= idx < len(colors):
                            colors[idx] = rgb
        except (AttributeError, KeyError, ValueError):
            pass
        return colors

    return np.array([palette.rgb(cpk_color(a.atomic_number)) for a in atoms], dtype=np.float64)


def _backbone_scene(structure: StructureData, color_mode: str) -> Scene | None:
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

    chain_order = _chain_order(structure)
    for chain in structure.chain_ids():
        trace = np.asarray(structure.backbone_trace(chain), dtype=np.float64)
        if len(trace) == 0:
            continue
        if color_mode == "chain":
            idx = chain_order.get(chain or "-", len(chain_order))
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


def structure_scene(
    reader: QVFReader,
    *,
    structure_id: str | None = None,
    representation: str = "ball_and_stick",
    color_mode: str = "element",
    show_bonds: bool = True,
    show_cell: bool = True,
    replication: tuple[int, int, int] = (1, 1, 1),
    positions_override: np.ndarray | None = None,
) -> Scene:
    """Build the structure scene, optionally at overridden coordinates.

    ``structure_id`` selects the structure whose atoms and cell are shown,
    by default the shown structure (the first one unless another was
    selected); this matters for an archive with several structures and for
    a ``wavefunction.gto`` section whose basis names another
    ``structure_ref``. ``positions_override`` retains the
    structure's identities and topology at supplied coordinates. Animated
    sections use :func:`frame_scene` to select their own atom identities.
    """
    structure = reader.read_structure(structure_id)
    return _scene_from_structure(
        structure,
        connectivity=lambda data: reader.connectivity(data, structure_id),
        representation=representation,
        color_mode=color_mode,
        show_bonds=show_bonds,
        show_cell=show_cell,
        replication=replication,
        positions_override=positions_override,
    )


def frame_scene(
    reader: QVFReader,
    section_id: str,
    positions: np.ndarray,
    *,
    frame_index: int = 0,
    representation: str = "ball_and_stick",
    color_mode: str = "element",
    show_bonds: bool = True,
    show_cell: bool = True,
    replication: tuple[int, int, int] = (1, 1, 1),
) -> Scene:
    """Draw a frame's own atoms with the section's applicable cell context.

    Reaction paths own their fixed/per-frame cell; a missing lattice means
    molecular. Trajectories and vibrations retain the terminal's legacy
    primary-cell inheritance when available. Their atom identities and
    inferred display bonds never come from that optional primary structure.
    """
    from vibeview.bonds import structure_bonds
    from vibeview.qvf import SectionNotFoundError, StructureData

    kind = reader.get_section(section_id).kind
    lattice = None
    pbc = (False, False, False)
    if kind == "reaction.path":
        path = reader.read_reaction_path(section_id)
        atoms = path.atoms
        lattice, pbc = _reaction_path_lattice(path, frame_index)
    elif kind == "trajectory":
        atoms = reader.read_trajectory(section_id).atoms
    elif kind == "vibrations":
        atoms = reader.read_vibrations(section_id).atoms
    else:
        raise ValueError(f"Section {section_id!r} has no animated atom geometry")

    if kind != "reaction.path":
        try:
            primary = reader.read_structure()
        except SectionNotFoundError:
            pass  # Self-contained atoms can render without primary context.
        else:
            lattice, pbc = primary.lattice_vectors, primary.pbc
    coords = np.asarray(positions, dtype=np.float64)
    if coords.shape != (len(atoms), 3):
        raise ValueError("Frame coordinates do not match the section's atom identities")
    # Keep cached reader payloads and edit overlays untouched. These kinds
    # supply no explicit bond list: infer display bonds on this frame only,
    # without borrowing archive-wide bond_orders by coincidental atom indices.
    snapshot = StructureData(
        atoms=[replace(atom, position=coords[i].copy()) for i, atom in enumerate(atoms)],
        pbc=pbc,
        lattice_vectors=None if lattice is None else lattice.copy(),
        bonds=None,  # dim is derived from pbc
    )
    return _scene_from_structure(
        snapshot,
        connectivity=structure_bonds,
        representation=representation,
        color_mode=color_mode,
        show_bonds=show_bonds,
        show_cell=show_cell,
        replication=replication,
    )


def _scene_from_structure(
    structure: StructureData,
    *,
    connectivity: Callable[[StructureData], list[Bond]],
    representation: str,
    color_mode: str,
    show_bonds: bool,
    show_cell: bool,
    replication: tuple[int, int, int],
    positions_override: np.ndarray | None = None,
) -> Scene:
    """Shared atom/bond/cell assembly; the caller owns the topology context."""
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
        supercell_offsets(lattice, structure.pbc, replication) if periodic else np.zeros((1, 3))
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

    bonds: Sequence[tuple[Any, ...]] | None = structure.bonds
    if bonds is None:
        bonds = connectivity(structure) if wants_bonds and show_bonds else []
    if show_bonds and wants_bonds and bonds:
        pairs = np.array([(b[0], b[1]) for b in bonds], dtype=np.int64)
        keep = (pairs[:, 0] < len(atoms)) & (pairs[:, 1] < len(atoms))
        pairs = pairs[keep]
        if len(pairs):
            # A periodic bond list stores the in-cell index pair, so a bond
            # that crosses a cell face has a *direct* separation of a whole
            # lattice vector (graphene: 1.42 Å bonds listed at 2.84 / 3.76 /
            # 5.68 Å). Drawing those endpoints as-is stretches sticks across
            # the box; a length cutoff would instead delete real bonds. The
            # shared helper draws each bond to its stated image, or to the
            # nearest one, exactly as the browser renderer does.
            kept_bonds = [b for b, flag in zip(bonds, keep, strict=True) if flag]
            starts, ends = bond_far_endpoints(
                base_positions, kept_bonds, lattice if periodic else None, structure.pbc
            )
            scene.bond_starts = (starts[None] + offsets[:, None, :]).reshape(-1, 3)
            scene.bond_ends = (ends[None] + offsets[:, None, :]).reshape(-1, 3)
            scene.bond_colors_a = np.tile(colors[pairs[:, 0]], (n_images, 1))
            scene.bond_colors_b = np.tile(colors[pairs[:, 1]], (n_images, 1))
    scene.bond_radius = bond_radius
    # extend, not assign: the backbone fallback above may already have
    # left a note on the scene and assignment would discard it.
    scene.color_notes.extend(color_notes)

    if show_cell and periodic and lattice is not None:
        starts, ends = cell_edges(lattice, structure.pbc)
        if len(starts):
            scene.line_starts = (starts[None] + offsets[:, None, :]).reshape(-1, 3)
            scene.line_ends = (ends[None] + offsets[:, None, :]).reshape(-1, 3)

    return scene


# ── volumes ───────────────────────────────────────────────────────────────


def _polydata_to_mesh(
    poly: pv.PolyData | None, color: tuple[int, int, int], opacity: float, label: str
) -> Mesh | None:
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
    grid: GridData,
    data: np.ndarray,
    *,
    isovalue: float = 0.05,
    signed: bool | None = None,
    replication: tuple[int, int, int] = (1, 1, 1),
    lattice_vectors: np.ndarray | None = None,
    pbc: Sequence[bool] | None = None,
    opacity: float = 1.0,
) -> list[Mesh]:
    """Contour an in-memory scalar field into terminal-renderable meshes.

    This is the path for fields evaluated from an embedded GTO wavefunction.
    Those grids are already in angstroms, unlike stored QVF ``volume.*``
    grids, whose coordinates are in bohr; ``grid.units`` records which, and
    the contour helper honours it. Signed fields request
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
            replication=replication,
            lattice_vectors=lattice_vectors,
            pbc=pbc,
        )
        mesh = _polydata_to_mesh(poly, color, opacity, f"{level:+.4g}")
        if mesh is not None:
            meshes.append(mesh)
    return meshes


def volume_meshes(
    reader: QVFReader,
    section_id: str,
    *,
    isovalue: float = 0.05,
    replication: tuple[int, int, int] = (1, 1, 1),
    opacity: float = 1.0,
) -> list[Mesh]:
    """Isosurface meshes for a ``volume.*`` / ``basis.ao`` section.

    Which kinds are signed (a +/- lobe pair) comes from
    :data:`vibeview.kinds.SIGNED_VOLUME_KINDS`, the browser's list; the
    terminal used to guess from whether any value was negative, so an
    all-positive orbital drew one neutral surface and an ELF with negative
    noise drew lobes (#38-18). The contour itself goes through
    :func:`sampled_field_meshes`, whose NaN-aware range test replaces a
    second copy that compared against NaN and contoured a level no finite
    value reached (#38-17). The grid carries its own units (bohr for a
    stored QVF volume).
    """
    from vibeview.kinds import SIGNED_VOLUME_KINDS

    grid = reader.read_volume_grid(section_id)
    data = reader.read_volume_data(section_id)
    if data is None:
        return []

    section = reader.get_section(section_id)
    # The stored array as-is, as in the browser: ``component`` is a label,
    # never a transform (#49, #38-19).
    from vibeview.renderers.volume import stored_volume_field

    data = stored_volume_field(data, getattr(section, "component", None))
    structure = None
    try:
        structure = reader.read_structure()
    except Exception:  # noqa: BLE001 — a volume without a structure still draws
        structure = None
    lattice = structure.lattice_vectors if structure is not None else None
    pbc = structure.pbc if structure is not None else None

    return sampled_field_meshes(
        grid,
        data,
        isovalue=isovalue,
        signed=getattr(section, "kind", "") in SIGNED_VOLUME_KINDS,
        replication=replication,
        lattice_vectors=lattice,
        pbc=pbc,
        opacity=opacity,
    )


def vector_magnitude_meshes(reader: QVFReader, section_id: str) -> list[Mesh]:
    """A ``volume.vector`` section in braille: a surface of its magnitude (#156).

    Arrows do not read at terminal resolution, so the terminal draws |v| at
    half its 95th-percentile value, the browser's reference magnitude.
    """
    from vibeview.renderers.vector_field import vector_glyphs

    field = reader.read_volume_vector(section_id)
    magnitude = np.linalg.norm(np.asarray(field.vectors, dtype=float), axis=-1)
    reference = vector_glyphs(field, stride=1, cutoff=0.0).reference
    if reference <= 0:
        return []
    return sampled_field_meshes(field.grid, magnitude, isovalue=0.5 * reference, signed=False)


def series_frames(reader: QVFReader, section_id: str) -> np.ndarray:
    """One geometry per slab of a ``volume.series`` section, in Å (#158).

    A slab that belongs to a trajectory frame takes that frame's atoms when
    it has as many atoms as the shown structure; any other slab takes the
    shown structure's. The TUI's frame keys and playback step through these.
    """
    from vibeview.renderers.volume_series import frame_atoms

    series = reader.read_volume_series(section_id)
    base = np.asarray([a.position for a in reader.read_structure().atoms], dtype=float)
    frames = []
    for slab in range(len(series.data)):
        atoms = frame_atoms(reader, series, slab)
        if atoms is not None and len(atoms) == len(base):
            frames.append(np.asarray([position for _symbol, position in atoms], dtype=float))
        else:
            frames.append(base)
    return np.asarray(frames)


def series_slab_meshes(reader: QVFReader, section_id: str, slab: int) -> list[Mesh]:
    """Slab ``slab`` of a ``volume.series`` section at the browser's opening isovalue (#158).

    ± lobes when any slab of the series is negative, one surface otherwise.
    """
    from vibeview.renderers.volume_series import is_signed, opening_isovalue
    from vibeview.viewer_defaults import section_volume_hints

    series = reader.read_volume_series(section_id)
    level = opening_isovalue(series, section_volume_hints(reader, section_id).isovalue)
    index = slab % len(series.data)
    return sampled_field_meshes(
        series.grid, np.asarray(series.data[index], dtype=float), isovalue=level,
        signed=is_signed(series),
    )


def complex_real_meshes(reader: QVFReader, section_id: str) -> list[Mesh]:
    """A ``volume.complex`` section in braille: its real part as ± lobes (#157).

    The browser's default part; the phase colouring of the modulus does not
    read in braille. The isovalue is the one the browser opens at.
    """
    from vibeview.renderers.complex_field import opening_isovalue, part_field
    from vibeview.viewer_defaults import section_volume_hints

    field = reader.read_volume_complex(section_id)
    level = opening_isovalue(field, section_volume_hints(reader, section_id).isovalue)
    return sampled_field_meshes(
        field.grid, part_field(field.values, "real"), isovalue=level, signed=True
    )


# ── frame sequences ───────────────────────────────────────────────────────


def trajectory_frames(
    reader: QVFReader, section_id: str
) -> tuple[np.ndarray, list[float] | None]:
    """``(coords [n_frames, n_atoms, 3] Å, per-frame energies or None)``."""
    kind = reader.get_section(section_id).kind
    data: TrajectoryData | ReactionPathData
    if kind == "reaction.path":
        data = reader.read_reaction_path(section_id)
    else:
        data = reader.read_trajectory(section_id)
    return np.asarray(data.coords, dtype=np.float64), data.energies


def vibration_frames(
    reader: QVFReader,
    section_id: str,
    mode_index: int,
    *,
    n_frames: int = 16,
    scale: float = 1.0,
) -> tuple[np.ndarray, float]:
    """Animate one normal mode as a closed displacement cycle.

    Returns ``(coords [n_frames, n_atoms, 3], frequency_cm1)``. The mode is
    scaled by the convention the browser shares
    (:mod:`vibeview.vibration_amplitude`): the largest atomic excursion is
    ``DEFAULT_PEAK_EXCURSION_ANGSTROM`` × ``scale`` Å, which keeps a stiff
    X-H stretch and a soft torsion equally legible.
    """
    from vibeview.vibration_amplitude import normalized_displacement

    data = reader.read_vibrations(section_id)
    base = np.array([a.position for a in data.atoms], dtype=np.float64)
    n_modes = len(data.frequencies)
    if n_modes == 0:
        return base[None, :, :], 0.0
    idx = int(np.clip(mode_index, 0, n_modes - 1))
    disp = normalized_displacement(data.displacements[idx], scale)
    phases = np.sin(np.linspace(0.0, 2.0 * np.pi, n_frames, endpoint=False))
    coords = base[None, :, :] + phases[:, None, None] * disp[None, :, :]
    return coords, float(data.frequencies[idx])


def _reaction_path_lattice(
    data: ReactionPathData, frame_index: int
) -> tuple[np.ndarray | None, tuple[bool, bool, bool]]:
    """Selected reaction cell in Å rows, plus its physical periodic axes."""
    if data.lattice is None:
        return None, (False, False, False)
    lattice = np.asarray(data.lattice, dtype=np.float64)
    if lattice.ndim == 3:
        lattice = lattice[frame_index]
    dim = data.dim_per_frame[frame_index] if data.dim_per_frame is not None else data.dim
    dim = 3 if dim is None else dim
    rows = reaction_lattice_rows_angstrom(lattice)
    assert rows is not None
    return rows, (dim > 0, dim > 1, dim > 2)


def reaction_path_cell(
    data: ReactionPathData, frame_index: int = 0
) -> tuple[np.ndarray, np.ndarray] | None:
    """Selected reaction-frame cell edges, or None when molecular.

    Reaction lattice columns are stored in bohr; the shared conversion
    supplies Å rows and the selected frame's dimensionality.
    """
    lattice, pbc = _reaction_path_lattice(data, frame_index)
    return None if lattice is None else cell_edges(lattice, pbc)


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
            endpoint_sprites=len(scene.positions) >= 10_000,
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


def label_atoms(
    grid: Any,
    scene: Scene,
    canvas: Any,
    camera: Any,
    cols: int,
    rows: int,
    mode: str,
    color: tuple[int, int, int] = (245, 245, 245),
) -> None:
    """Print each atom's label above its projected centre, nearest last.

    Placed in *character* cells, not pixels: a braille dot cannot carry a
    digit. Nearer atoms are drawn last so their label wins the cell. Shared
    by the interactive viewer and ``vibe-view show``.
    """
    xy, z = atom_screen_positions(scene, canvas, camera)
    px_col, px_row = (2, 4) if mode == "braille" else (1, 2)
    for idx in np.argsort(-z):
        if not np.isfinite(xy[idx]).all() or z[idx] <= 0:
            continue
        col = int(xy[idx, 0] // px_col)
        row = int(xy[idx, 1] // px_row) - 1
        if 0 <= row < rows and 0 <= col < cols:
            label = scene.labels[idx] if idx < len(scene.labels) else str(idx + 1)
            grid.text(row, col, label, color)


def atom_screen_positions(
    scene: Scene, canvas: Canvas, camera: Camera
) -> tuple[np.ndarray, np.ndarray]:
    """Screen-space ``(xy, z)`` of every atom — used to place index labels."""
    if not len(scene.positions):
        return np.zeros((0, 2)), np.zeros(0)
    return canvas.project(camera.to_camera(scene.positions), camera.fov)
