"""Structure renderer — v2.1 with LOD and batched large-system support.

Draws atoms as spheres (CPK or ball-and-stick), unit cell wireframe
when PBC flags are true, and bonds (explicit from bonds.json or
inferred from covalent radii).
"""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
import pyvista as pv
from vtkmodules.vtkCommonCore import vtkWeakReference

# Import covalent-radii data for periodic bond inference.
from vibeview.profiler import profiler
from vibeview.qvf import _BOND_TOLERANCE, _COVALENT_RADII, clamp_replication  # noqa: PLC2701
from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, Section, StructureData

# CPK colour table — Jmol colours by atomic number
_CPK_COLORS: dict[int, str] = {
    1: "#FFFFFF",
    2: "#D9FFFF",
    3: "#CC80FF",
    4: "#C2FF00",
    5: "#FFB5B5",
    6: "#909090",
    7: "#3050F8",
    8: "#FF0D0D",
    9: "#90E050",
    10: "#B3E3F5",
    11: "#AB5CF2",
    12: "#8AFF00",
    13: "#BFA6A6",
    14: "#F0C8A0",
    15: "#FF8000",
    16: "#FFFF30",
    17: "#1FF01F",
    18: "#80D1E3",
    19: "#8F40D4",
    20: "#3DFF00",
    21: "#E6E6E6",
    22: "#BFC2C7",
    23: "#A6A6AB",
    24: "#8A99C7",
    25: "#9C7AC7",
    26: "#E06633",
    27: "#F090A0",
    28: "#50D050",
    29: "#C88033",
    30: "#7D80B0",
    31: "#C28F8F",
    32: "#668F8F",
    33: "#BD80E3",
    34: "#FFA100",
    35: "#A62929",
    36: "#5CB8D1",
    37: "#702EB0",
    38: "#00FF00",
    39: "#94FFFF",
    40: "#94E0E0",
    41: "#73C2C9",
    42: "#54B5B5",
    43: "#3B9E9E",
    44: "#248F8F",
    45: "#0A7D8C",
    46: "#006985",
    47: "#C0C0C0",
    48: "#FFD98F",
    49: "#A67573",
    50: "#668080",
    51: "#9E63B5",
    52: "#D47A00",
    53: "#940094",
    54: "#429EB0",
    55: "#57178F",
    56: "#00C900",
    57: "#70D4FF",
    58: "#FFFFC7",
    59: "#D9FFC7",
    60: "#C7FFC7",
    61: "#A3FFC7",
    62: "#8FFFC7",
    63: "#61FFC7",
    64: "#45FFC7",
    65: "#30FFC7",
    66: "#1FFFC7",
    67: "#00FF9C",
    68: "#00E675",
    69: "#00D452",
    70: "#00BF38",
    71: "#00AB24",
    72: "#4DC2FF",
    73: "#4DA6FF",
    74: "#2194D6",
    75: "#267DAB",
    76: "#266696",
    77: "#175487",
    78: "#D0D0E0",
    79: "#FFD123",
    80: "#B8B8D0",
    81: "#A6544D",
    82: "#575961",
    83: "#9E4FB5",
    84: "#AB5C00",
    85: "#754F45",
    86: "#428296",
    87: "#420066",
    88: "#007D00",
    89: "#70ABFA",
    90: "#00BAFF",
    91: "#00A1FF",
    92: "#008FFF",
    93: "#0080FF",
    94: "#006BFF",
    95: "#545CF2",
    96: "#785CE3",
}

_DEFAULT_COLOR = "#FF1493"  # hot pink for genuinely unknown elements

# Level-of-detail thresholds: (max_atoms, theta_res, phi_res)
_LOD_THRESHOLDS: list[tuple[float, int, int]] = [
    (20, 32, 24),  # < 20 atoms: high quality
    (100, 16, 12),  # < 100 atoms: medium
    (500, 8, 6),  # < 500 atoms: low
    (float("inf"), 4, 3),  # >= 500 atoms: very low
]

# Threshold above which batched glyph rendering is used for unreplicated cells
_BATCH_ATOM_THRESHOLD = 100

# Threshold above which bonds are merged into a single mesh
_BATCH_BOND_THRESHOLD = 50


def _lod_resolution(n_atoms: int) -> tuple[int, int]:
    """Return (theta_res, phi_res) sphere resolution for *n_atoms*."""
    for threshold, theta, phi in _LOD_THRESHOLDS:
        if n_atoms < threshold:
            return theta, phi
    return 4, 3


# van der Waals display radii (Å). Sparse beyond Z=20; cpk_radius() fills the
# gaps from covalent radii so heavy atoms aren't all drawn the same size.
_VDW: dict[int, float] = {
    1: 1.20,
    2: 1.40,
    3: 1.82,
    4: 1.53,
    5: 1.92,
    6: 1.70,
    7: 1.55,
    8: 1.52,
    9: 1.47,
    10: 1.54,
    11: 2.27,
    12: 1.73,
    13: 1.84,
    14: 2.10,
    15: 1.80,
    16: 1.80,
    17: 1.75,
    18: 1.88,
    19: 2.75,
    20: 2.31,
}


# Per-element colour overrides, keyed by atomic number (design refresh 2026).
# Consulted inside cpk_color() rather than threaded through the ~8 render call
# sites, so every path — glyph batching, per-atom actors, replicated cells,
# compare mode — honours an override automatically and can't go half-wired.
_COLOR_OVERRIDES: dict[int, str] = {}


def set_color_overrides(overrides: dict[int, str] | None) -> None:
    """Replace the per-element colour overrides (atomic number → colour).

    Pass ``None``/empty to restore the stock CPK palette.
    """
    _COLOR_OVERRIDES.clear()
    if overrides:
        _COLOR_OVERRIDES.update({int(k): str(v) for k, v in overrides.items() if v})


def get_color_overrides() -> dict[int, str]:
    """Current per-element colour overrides (copy)."""
    return dict(_COLOR_OVERRIDES)


def cpk_color(atomic_number: int) -> str:
    """CPK/Jmol colour for an element by **atomic number** (1–96).

    A per-element override (``set_color_overrides``) wins over the stock
    table; otherwise the Jmol colour is used.

    Callers must pass the parsed ``Atom.atomic_number``, NOT a symbol routed
    through the truncated ``_symbol_to_num`` table (which stops at Ba, Z=56),
    which made every element heavier than Ba render as hot-pink despite the
    colour table reaching Z=96 (audit finding: heavy-element CPK).
    """
    z = int(atomic_number)
    override = _COLOR_OVERRIDES.get(z)
    if override:
        return override
    return _CPK_COLORS.get(z, _DEFAULT_COLOR)


def cpk_radius(atomic_number: int, scale: float = 1.0) -> float:
    """Approximate van der Waals display radius (Å) by atomic number.

    Falls back to ~1.6× the covalent radius for elements outside the small
    vdW table (Z>20) instead of a flat 1.7 Å, so e.g. Fe/Au/U don't all get
    the same size.
    """
    z = int(atomic_number)
    r = _VDW.get(z)
    if r is None:
        from vibeview.qvf import _COVALENT_RADII

        cov = _COVALENT_RADII.get(z)
        r = cov * 1.6 if cov is not None else 1.7
    return r * scale


# Label meshes are rebuilt, not rotated in place, when the camera moves.
# Keyed by actor name on the plotter and tied to that exact actor, so a
# re-orient can replace only geometry the registry still owns.
_LABEL_SPECS_ATTR = "_vibe_view_label_specs"


class _LabelSpec(NamedTuple):
    positions: list
    strings: list[str]
    scale: float
    lift: float
    actor_ref: vtkWeakReference


def _label_actor_ref(actor) -> vtkWeakReference:
    """A VTK-native weak reference stable across PyVista wrapper collection."""
    actor_ref = vtkWeakReference()
    actor_ref.Set(actor)
    return actor_ref


def _billboard_basis(anchor, camera):
    """Right / up / forward for a glyph at *anchor* facing *camera*.

    ``camera`` is ``(position, focal_point, view_up)``. Text3D lies in the
    XY plane reading along +X with its normal along +Z, so mapping local
    X to right, Y to up and Z to forward makes it readable head-on.

    Returns None when the basis is degenerate — an anchor sitting exactly
    at the camera, or a view-up parallel to the view direction. Callers
    fall back to the unoriented placement rather than emitting NaNs.
    """
    position, _focal, view_up = camera
    forward = np.asarray(position, dtype=float) - np.asarray(anchor, dtype=float)
    n = np.linalg.norm(forward)
    if not np.isfinite(n) or n < 1e-9:
        return None
    forward /= n
    up_hint = np.asarray(view_up, dtype=float)
    right = np.cross(up_hint, forward)
    rn = np.linalg.norm(right)
    if not np.isfinite(rn) or rn < 1e-9:
        return None
    right /= rn
    up = np.cross(forward, right)
    return right, up, forward


def build_label_mesh(
    positions,
    strings,
    *,
    scale: float = 0.5,
    lift: float = 0.55,
    camera=None,
):
    """Build ONE merged ``pv.PolyData`` of 3D polygonal text labels.

    Used for atom-index labels and atom-charge labels. We deliberately do
    NOT use ``plotter.add_point_labels``: that builds VTK 2D label actors
    (vtkLabelPlacementMapper), which the in-browser ``VtkLocalView``
    (vtk.js) does not render — so labels were invisible in the browser
    even though the toggle/handler worked. Real polygonal text
    (``vtkVectorText`` via ``pv.Text3D``) serialises and renders like any
    other mesh.

    ``positions`` are world-space anchor points (Å); each label is centred
    on its anchor and lifted ``lift`` Å clear of the atom sphere. Returns
    ``None`` if there is nothing to draw.

    Pass ``camera`` as ``(position, focal_point, view_up)`` to billboard
    the labels: each glyph is rotated to face that camera and lifted along
    the camera's up rather than world +z, so it reads head-on from the
    current viewpoint (roadmap D5). Without it the text lies in the XY
    plane and is edge-on or mirrored from most angles — the audit's
    deferred label-orientation finding.
    """
    glyphs = []
    for pos, text in zip(positions, strings):
        if not text:
            continue
        anchor = np.asarray(pos, dtype=float)
        glyph = pv.Text3D(str(text), depth=0.05)
        glyph.points *= scale
        glyph.points -= np.asarray(glyph.center, dtype=float)

        basis = _billboard_basis(anchor, camera) if camera is not None else None
        if basis is None:
            glyph.points += anchor + np.array([0.0, 0.0, lift])
        else:
            right, up, forward = basis
            # Columns are where local X, Y, Z go.
            glyph.points = glyph.points @ np.column_stack((right, up, forward)).T
            glyph.points += anchor + up * lift
        glyphs.append(glyph)
    if not glyphs:
        return None
    merged = glyphs[0]
    for extra in glyphs[1:]:
        merged = merged.merge(extra)
    return merged


def register_label_actor(plotter, name, positions, strings, *, scale=0.5, lift=0.55):
    """Record what an actor's labels are, so a camera move can rebuild them."""
    specs = getattr(plotter, _LABEL_SPECS_ATTR, None)
    if specs is None:
        specs = {}
        setattr(plotter, _LABEL_SPECS_ATTR, specs)
    actor = plotter.actors.get(name)
    if actor is None:
        specs.pop(name, None)
        return
    specs[name] = _LabelSpec(
        list(positions), list(strings), scale, lift, _label_actor_ref(actor)
    )


def forget_label_actors(plotter) -> None:
    """Drop the registry — the scene that owned those actors is gone."""
    setattr(plotter, _LABEL_SPECS_ATTR, {})


def reorient_labels(plotter) -> int:
    """Rebuild every registered label actor to face the plotter's camera.

    Returns how many actors were rebuilt. Cheap enough to run on every
    interaction end: it re-meshes only the label glyphs, not the scene.
    Labels are polygonal text, so unlike a 2D annotation they genuinely
    have to be rebuilt when the viewpoint changes.
    """
    specs = getattr(plotter, _LABEL_SPECS_ATTR, None)
    if not specs:
        return 0
    try:
        camera = (
            tuple(plotter.camera.position),
            tuple(plotter.camera.focal_point),
            tuple(plotter.camera.up),
        )
    except Exception:  # noqa: BLE001 — no camera yet is not an error
        return 0
    rebuilt = 0
    for name, spec in list(specs.items()):
        # Actor ownership is authoritative. ``plotter.clear()`` and direct
        # ``remove_actor()`` calls discard scene geometry without touching
        # this lightweight cache; rebuilding a missing entry here would make
        # a deliberately hidden label reappear on the next camera move. Actor
        # identity also prevents a new, unrelated actor that reuses the name
        # from inheriting stale label behavior.
        actor = plotter.actors.get(name)
        if actor is None or spec.actor_ref.Get() is not actor:
            specs.pop(name, None)
            continue
        mesh = build_label_mesh(
            spec.positions,
            spec.strings,
            scale=spec.scale,
            lift=spec.lift,
            camera=camera,
        )
        if mesh is None:
            continue
        replacement = plotter.add_mesh(
            mesh,
            color="white",
            name=name,
            lighting=False,
            show_scalar_bar=False,
        )
        specs[name] = spec._replace(actor_ref=_label_actor_ref(replacement))
        rebuilt += 1
    return rebuilt


def _bond_far_endpoint(p1, p2, image, lattice, inv_lattice, pbc):
    """Where the far end of a bond actually sits.

    A nonzero ``image`` is authoritative: inference recorded exactly which
    periodic copy of atom j this bond reaches, and two bonds between the same
    pair can carry different images. Minimum image cannot express that -- it
    returns one nearest copy, so drawing by it collapses both bonds onto the
    same stick.

    A zero image means "not stated" rather than "in-cell": explicit bonds read
    from a file carry no offset, and some of those do cross a face. Falling
    back to the minimum image keeps those correct, and is a no-op for a bond
    that really is in-cell.
    """
    if lattice is not None and image is not None and any(image):
        return p2 + np.asarray(image, dtype=float) @ np.asarray(lattice, dtype=float)
    if inv_lattice is not None:
        return _minimum_image(p1, p2, lattice, inv_lattice, pbc)
    return p2


def _minimum_image(p1, p2, lattice, inv_lattice, pbc=(True, True, True)):
    """Return the periodic image of ``p2`` nearest to ``p1`` (Cartesian).

    Rounding the fractional offset alone only yields the nearest image for
    near-orthogonal cells; in skewed (hexagonal / triclinic) cells it can
    select a farther image (e.g. a hexagonal bond at 0.866 when the true
    nearest image is 0.5). So round to get close, then search the surrounding
    lattice images for the actual minimum. ``lattice`` rows are a, b, c
    (cart = frac @ lattice; frac = cart @ inv_lattice).

    Only axes flagged in ``pbc`` are wrapped or searched. A 2D slab has no
    images along its synthesized normal, so wrapping there would teleport an
    atom across a cell face that does not exist.
    """
    frac = (p2 - p1) @ inv_lattice
    shift = np.round(frac)
    for i in range(3):
        if not pbc[i]:
            shift[i] = 0.0
    frac = frac - shift
    base = p1 + frac @ lattice
    best, best_d2 = base, float(np.dot(base - p1, base - p1))
    spans = [(-1, 0, 1) if p else (0,) for p in pbc]
    for na in spans[0]:
        for nb in spans[1]:
            for nc in spans[2]:
                cand = base + na * lattice[0] + nb * lattice[1] + nc * lattice[2]
                d2 = float(np.dot(cand - p1, cand - p1))
                if d2 < best_d2:
                    best, best_d2 = cand, d2
    return best


class StructureRenderer(BaseRenderer):
    """3D structure drawer."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._structure: StructureData | None = None
        self._bonds: list[tuple[int, int]] | None = None
        self._bonds_loaded = False

    def load_structure(self) -> StructureData:
        """Load and cache structure geometry without inferring bonds."""
        if self._structure is None:
            self._structure = self.reader.read_structure()
        return self._structure

    def load(self) -> StructureData:
        """Load structure data and initialise its bond cache once."""
        s = self.load_structure()
        if not self._bonds_loaded:
            # QVFReader owns the one molecular/periodic inference path,
            # including minimum-image bonds and bond-order enrichment.
            self._bonds = self.reader.infer_bonds(s)
            self._bonds_loaded = True
        return s

    def add_to_plotter(
        self,
        plotter: pv.Plotter,
        replication: tuple[int, int, int] = (1, 1, 1),
        ball_scale: float = 0.35,
        show_labels: bool = False,
        representation: str = "ball_and_stick",
        cartoon_color_mode: str = "chain",
        residue_selection: str = "",
        atom_colors: dict[int, tuple[float, float, float]] | None = None,
    ) -> None:
        """Add atoms, bonds, and unit cell to a PyVista plotter.

        ``show_labels`` toggles per-atom labels (``Sym N``) next to each
        sphere — useful for orientation in larger structures.

        ``representation`` is one of:
        - ``"ball_and_stick"`` (default) — atom spheres + bond cylinders
        - ``"space_filling"`` — atoms at vdW radii, no bonds
        - ``"sticks_only"`` — bond cylinders only, no atom spheres
        - ``"wireframe"`` — thin bond lines only, no atom spheres
        - ``"cartoon"`` — biomolecular ribbon through the backbone
          (roadmap D3); needs residue identity, so it falls back to
          ball-and-stick on a structure that carries none

        ``cartoon_color_mode`` applies only to ``"cartoon"``: ``"chain"``
        for one hue per chain, ``"structure"`` for helix / sheet / loop.

        ``residue_selection`` is a selection string (see
        :func:`parse_residue_selection`) shared by every representation.
        Matching ribbon samples or atom spheres render white; bonds render
        white when both endpoints belong to the selection. Empty and
        non-matching selections leave the old render path unchanged.

        ``atom_colors`` optionally overrides CPK colour by original atom index.
        It is used for per-atom property overlays; residue-selection white
        remains the higher-priority highlight.
        """
        with profiler.measure("structure_render"):
            self._add_structure_inner(
                plotter,
                replication=replication,
                ball_scale=ball_scale,
                show_labels=show_labels,
                representation=representation,
                cartoon_color_mode=cartoon_color_mode,
                residue_selection=residue_selection,
                atom_colors=atom_colors,
            )

    def _add_structure_inner(
        self,
        plotter: pv.Plotter,
        replication: tuple[int, int, int] = (1, 1, 1),
        ball_scale: float = 0.35,
        show_labels: bool = False,
        representation: str = "ball_and_stick",
        cartoon_color_mode: str = "chain",
        residue_selection: str = "",
        atom_colors: dict[int, tuple[float, float, float]] | None = None,
    ) -> None:
        """Internal implementation of add_to_plotter (profiled)."""
        if representation == "cartoon":
            # Load geometry without initialising the bond cache: inference is
            # the dominant cost on a biomolecule and a ribbon never draws it.
            # Keeping the geometry on this renderer also lets callers inspect
            # metadata before rendering without parsing the section twice.
            structure = self.load_structure()
            # A ribbon needs a backbone. Anything without residue
            # identity (an XYZ, a computed molecule) has none, so fall
            # back rather than render an empty viewport.
            if structure.has_residues and len(structure.backbone_trace()):
                _add_cartoon(
                    plotter,
                    structure,
                    color_mode=cartoon_color_mode,
                    selection=residue_selection,
                )
                if structure.lattice_vectors is not None and any(structure.pbc):
                    _draw_unit_cell(
                        plotter, structure.lattice_vectors, structure.pbc
                    )
                return
            representation = "ball_and_stick"

        show_atoms = representation in ("ball_and_stick", "space_filling")
        show_bonds = representation in ("ball_and_stick", "sticks_only", "wireframe")
        wireframe_bonds = representation == "wireframe"
        structure = self.load() if show_bonds else self.load_structure()
        selection_terms, _rejected = parse_residue_selection(residue_selection)
        selected_atoms = _selected_atom_indices(structure, selection_terms)

        # Resolve per-atom radius based on representation style.
        def _atom_radius(z: int) -> float:
            if representation == "space_filling":
                # vdW fallback for elements missing from _VDW: ~1.75x the
                # covalent radius is a reasonable estimate. The old
                # fallback inherited the ball-and-stick scale (0.35x), so
                # heavy atoms rendered at a third of their vdW size.
                return _VDW.get(z, _COVALENT_RADII.get(z, 1.5) * 1.75)
            return cpk_radius(z, ball_scale)

        def _atom_color(
            atom_idx: int,
            z: int,
        ) -> tuple[float, float, float]:
            if atom_idx in selected_atoms:
                return _RESIDUE_SELECTION_COLOR
            if atom_colors is not None and atom_idx in atom_colors:
                return atom_colors[atom_idx]
            return cpk_color(z)

        # ── Build replicated atom positions ───────────────────────────
        # Track the original atom index so each sphere actor has a unique
        # name in the plotter — without this, two H atoms both get
        # name="atom_H" and pyvista keeps only the last, leaving the
        # earlier H invisible while its bond still renders ("dangling bond").
        all_atoms: list[tuple[int, str, int, np.ndarray]] = []
        lattice = structure.lattice_vectors
        # Never tile along a non-periodic axis: for a 2D slab lattice[2] is a
        # synthesized normal, not a cell edge, and replicating it would invent a
        # stack of sheets.
        rx, ry, rz = clamp_replication(replication, structure.pbc)

        for atom_idx, atom in enumerate(structure.atoms):
            pos = atom.position.copy()
            # Use the parsed atomic number (CPK colours reach Z=96); deriving
            # it from the symbol via _symbol_to_num would drop every element
            # heavier than Ba to hot-pink (heavy-element CPK bug).
            z = atom.atomic_number or _symbol_to_num(atom.symbol)
            for ix in range(rx):
                for iy in range(ry):
                    for iz in range(rz):
                        offset = np.zeros(3)
                        if lattice is not None:
                            offset = ix * lattice[0] + iy * lattice[1] + iz * lattice[2]
                        all_atoms.append((atom_idx, atom.symbol, z, pos + offset))

        # ── Atom spheres ──────────────────────────────────────────────
        # For replicated periodic cells (rx*ry*rz > 1) or large systems
        # (>100 atoms total) the naive one-sphere-per-atom loop issues
        # O(N) separate draw calls and VTK actors, which stalls the
        # renderer.  Glyph instancing collapses all atoms of the same
        # element type into a single merged PolyData and one draw call
        # per element — typically 2-10 calls regardless of system size
        # (A7-08).
        # Small molecular systems (<100 atoms, no replication) keep
        # per-atom actors so that _restore_cpk_atoms / charge-overlay
        # can target individual spheres by name without reconstructing
        # the full scene.
        n_replicated = rx * ry * rz
        n_total_atoms = len(all_atoms)
        use_glyphs = n_replicated > 1 or n_total_atoms > _BATCH_ATOM_THRESHOLD

        # Resolve sphere display resolution for this system size.
        lod_theta, lod_phi = _lod_resolution(n_total_atoms)

        label_points: list[np.ndarray] = []
        label_strings: list[str] = []

        if show_atoms and use_glyphs:
            # Group atoms by (color, radius) — one glyph call per unique
            # (display colour, radius) pair.
            from collections import defaultdict

            groups: dict[tuple, list[np.ndarray]] = defaultdict(list)
            for atom_idx, symbol, z, pos in all_atoms:
                color = _atom_color(atom_idx, z)
                key = (color, _atom_radius(z))
                groups[key].append(np.asarray(pos, dtype=float))
                if show_labels:
                    label_points.append(np.asarray(pos, dtype=float))
                    label_strings.append(f"{symbol}{atom_idx + 1}")

            for group_idx, ((color, radius), positions) in enumerate(groups.items()):
                centers = pv.PolyData(np.array(positions))
                prototype = pv.Sphere(
                    radius=radius, theta_resolution=lod_theta, phi_resolution=lod_phi
                )
                glyphed = centers.glyph(
                    geom=prototype, scale=False, orient=False, progress_bar=False
                )
                plotter.add_mesh(
                    glyphed,
                    color=color,
                    smooth_shading=True,
                    name=f"atom_group_{group_idx}",
                )
        elif show_atoms:
            # Molecular / no-replication: one actor per atom so that
            # _restore_cpk_atoms and the charge overlay can retarget
            # individual spheres by name.
            for slot, (atom_idx, symbol, z, pos) in enumerate(all_atoms):
                color = _atom_color(atom_idx, z)
                radius = _atom_radius(z)
                sphere = pv.Sphere(
                    radius=radius,
                    center=pos,
                    theta_resolution=lod_theta,
                    phi_resolution=lod_phi,
                )
                plotter.add_mesh(
                    sphere,
                    color=color,
                    smooth_shading=True,
                    name=f"atom_{atom_idx}_{slot}",
                )
                if show_labels:
                    label_points.append(np.asarray(pos, dtype=float))
                    label_strings.append(f"{symbol}{atom_idx + 1}")

        if show_labels and label_points:
            camera = None
            try:
                camera = (
                    tuple(plotter.camera.position),
                    tuple(plotter.camera.focal_point),
                    tuple(plotter.camera.up),
                )
            except Exception:  # noqa: BLE001 — unoriented is still readable head-on
                camera = None
            mesh = build_label_mesh(label_points, label_strings, camera=camera)
            if mesh is not None:
                plotter.add_mesh(
                    mesh,
                    color="white",
                    name="atom_index_labels",
                    lighting=False,
                    show_scalar_bar=False,
                )
                register_label_actor(
                    plotter, "atom_index_labels", label_points, label_strings
                )

        # ── Bonds ─────────────────────────────────────────────────────
        # Explicit bonds (from a `bonds` section) are drawn for molecular AND
        # periodic systems — the producer vouched for the connectivity. For a
        # periodic cell each bond is drawn to the *minimum image* of its second
        # endpoint, so a bond across a cell face renders to the nearest
        # neighbour instead of a long line across the cell.
        #
        # Inferred (covalent-radius) bonds now work for periodic systems too:
        # we use minimum-image distances to find bonded pairs within cutoff
        # across cell boundaries.
        if show_bonds:
            bonds = self._bonds
            if bonds:
                inv_lattice = np.linalg.inv(lattice) if lattice is not None else None
                n_atoms = len(structure.atoms)
                n_bonds_total = len(bonds)

                # Bonds replicate over the same cell offsets as the atom
                # spheres — without this, replication rendered bare atoms
                # in every cell but the base one.
                cell_offsets: list[np.ndarray] = []
                for ix in range(rx):
                    for iy in range(ry):
                        for iz in range(rz):
                            if lattice is not None:
                                cell_offsets.append(
                                    ix * lattice[0] + iy * lattice[1] + iz * lattice[2]
                                )
                            else:
                                cell_offsets.append(np.zeros(3))

                if n_bonds_total * len(cell_offsets) > _BATCH_BOND_THRESHOLD and (
                    not wireframe_bonds
                ):
                    # This outer decision is authoritative for the whole
                    # replicated scene. A single bond in many cells still
                    # needs one uniquely named batched actor per cell; letting
                    # the helper reconsider each replica would make those
                    # actors overwrite one another.
                    base_positions = np.array([a.position for a in structure.atoms])
                    for cell_idx, offset in enumerate(cell_offsets):
                        positions = base_positions + offset
                        valid_pairs: list[tuple[int, int]] = []
                        valid_orders: list[float] = []
                        segments: list[tuple[np.ndarray, np.ndarray]] = []
                        selected_pairs: list[tuple[int, int]] = []
                        selected_orders: list[float] = []
                        selected_segments: list[tuple[np.ndarray, np.ndarray]] = []
                        for bond in bonds:
                            i, j = int(bond[0]), int(bond[1])
                            order = float(bond[2]) if len(bond) > 2 else 1.0
                            if i < n_atoms and j < n_atoms:
                                p1 = positions[i]
                                p2 = _bond_far_endpoint(
                                    p1,
                                    positions[j],
                                    bond[3] if len(bond) > 3 else None,
                                    lattice,
                                    inv_lattice,
                                    structure.pbc,
                                )
                                if float(np.linalg.norm(p2 - p1)) >= 0.001:
                                    if i in selected_atoms and j in selected_atoms:
                                        selected_pairs.append((i, j))
                                        selected_orders.append(order)
                                        selected_segments.append((p1, p2))
                                    else:
                                        valid_pairs.append((i, j))
                                        valid_orders.append(order)
                                        segments.append((p1, p2))
                        if valid_pairs:
                            _add_batched_bonds(
                                plotter,
                                positions,
                                valid_pairs,
                                orders=valid_orders,
                                name=f"bonds_batched_{cell_idx}",
                                segments=segments,
                            )
                        if selected_pairs:
                            _add_batched_bonds(
                                plotter,
                                positions,
                                selected_pairs,
                                orders=selected_orders,
                                colors=[_RESIDUE_SELECTION_COLOR] * len(selected_pairs),
                                name=f"bonds_selected_{cell_idx}",
                                segments=selected_segments,
                                default_color=_RESIDUE_SELECTION_COLOR,
                            )
                else:
                    for cell_idx, offset in enumerate(cell_offsets):
                        for bond in bonds:
                            i, j = int(bond[0]), int(bond[1])
                            order = float(bond[2]) if len(bond) > 2 else 1.0
                            if not (i < n_atoms and j < n_atoms):
                                continue
                            p1 = structure.atoms[i].position + offset
                            p2 = _bond_far_endpoint(
                                p1,
                                structure.atoms[j].position + offset,
                                bond[3] if len(bond) > 3 else None,
                                lattice,
                                inv_lattice,
                                structure.pbc,
                            )
                            direction = p2 - p1
                            length = float(np.linalg.norm(direction))
                            if length < 0.001:
                                continue
                            direction = direction / length
                            center = (p1 + p2) / 2
                            # Keep the legacy name for the unreplicated case —
                            # actor-name consumers (and tests) pin bond_{i}_{j}.
                            name = (
                                f"bond_{i}_{j}"
                                if len(cell_offsets) == 1
                                else f"bond_{i}_{j}_c{cell_idx}"
                            )
                            color_override = (
                                _RESIDUE_SELECTION_COLOR
                                if i in selected_atoms and j in selected_atoms
                                else None
                            )
                            if wireframe_bonds:
                                _add_bond_wire(
                                    plotter,
                                    center,
                                    direction,
                                    length,
                                    order,
                                    name=name,
                                    color_override=color_override,
                                )
                            else:
                                _add_bond_cylinders(
                                    plotter,
                                    center,
                                    direction,
                                    length,
                                    order,
                                    name=name,
                                    color_override=color_override,
                                )

        # ── Unit cell wireframe ───────────────────────────────────────
        if lattice is not None and any(structure.pbc):
            _draw_unit_cell(plotter, lattice, structure.pbc)


def _symbol_to_num(symbol: str) -> int:
    """Quick atomic number lookup for common elements."""
    _SYMBOLS: dict[str, int] = {
        "H": 1,
        "He": 2,
        "Li": 3,
        "Be": 4,
        "B": 5,
        "C": 6,
        "N": 7,
        "O": 8,
        "F": 9,
        "Ne": 10,
        "Na": 11,
        "Mg": 12,
        "Al": 13,
        "Si": 14,
        "P": 15,
        "S": 16,
        "Cl": 17,
        "Ar": 18,
        "K": 19,
        "Ca": 20,
        "Sc": 21,
        "Ti": 22,
        "V": 23,
        "Cr": 24,
        "Mn": 25,
        "Fe": 26,
        "Co": 27,
        "Ni": 28,
        "Cu": 29,
        "Zn": 30,
        "Ga": 31,
        "Ge": 32,
        "As": 33,
        "Se": 34,
        "Br": 35,
        "Kr": 36,
        "Rb": 37,
        "Sr": 38,
        "Y": 39,
        "Zr": 40,
        "Nb": 41,
        "Mo": 42,
        "Tc": 43,
        "Ru": 44,
        "Rh": 45,
        "Pd": 46,
        "Ag": 47,
        "Cd": 48,
        "In": 49,
        "Sn": 50,
        "Sb": 51,
        "Te": 52,
        "I": 53,
        "Xe": 54,
        "Cs": 55,
        "Ba": 56,
    }
    return _SYMBOLS.get(symbol, 0)


def _vdw_radius(atomic_num: int) -> float:
    """Approximate van der Waals radius in angstroms for CPK scaling.

    Kept for the few call sites that still resolve via element symbol;
    prefer :func:`cpk_radius` when an atomic number is available.
    """
    return cpk_radius(atomic_num)


def _bond_order_color(order: float) -> str:
    """Map bond order to a display colour.

    single (≈1.0)  → grey
    aromatic (≈1.5) → purple
    double (≈2.0)  → blue
    triple (≈3.0)  → red
    Fractional orders interpolate between the nearest anchors.
    """
    anchors = [(1.0, "#888888"), (1.5, "#9933cc"), (2.0, "#3366cc"), (3.0, "#cc3333")]
    if order <= anchors[0][0]:
        return anchors[0][1]
    if order >= anchors[-1][0]:
        return anchors[-1][1]
    for i in range(len(anchors) - 1):
        o_lo, c_lo = anchors[i]
        o_hi, c_hi = anchors[i + 1]
        if o_lo <= order <= o_hi:
            # Linear interpolation in RGB space. Good enough for display colours.
            t = (order - o_lo) / (o_hi - o_lo)
            r_lo, g_lo, b_lo = int(c_lo[1:3], 16), int(c_lo[3:5], 16), int(c_lo[5:7], 16)
            r_hi, g_hi, b_hi = int(c_hi[1:3], 16), int(c_hi[3:5], 16), int(c_hi[5:7], 16)
            r = int(r_lo + t * (r_hi - r_lo))
            g = int(g_lo + t * (g_hi - g_lo))
            b = int(b_lo + t * (b_hi - b_lo))
            return f"#{r:02x}{g:02x}{b:02x}"
    return "#888888"


def _add_batched_bonds(
    plotter: pv.Plotter,
    positions: np.ndarray,
    bond_pairs: list[tuple[int, int]],
    colors: list[str] | None = None,
    orders: list[float] | None = None,
    radius: float = 0.08,
    name: str = "bonds_batched",
    segments: list[tuple[np.ndarray, np.ndarray]] | None = None,
    default_color: str = "#666666",
) -> None:
    """Merge one caller-selected bond partition into a single actor.

    The caller owns the batching decision across all periodic replicas. This
    helper therefore always returns at most one actor, even when the partition
    for one cell contains only one bond.

    ``segments`` gives the endpoints explicitly, parallel to ``bond_pairs``.
    Looking the second endpoint up from ``positions[b]`` cannot draw a bond
    that reaches a periodic image, and two bonds between the same pair via
    different images would render as one stick.

    ``orders`` preserves the small-system multiplicity contract inside the
    merged mesh. A homogeneous partition uses its bond-order colour directly
    on the actor, matching the small-system material/export contract. Mixed
    partitions use cell RGB scalars so every order (or explicit entry in
    ``colors``) remains distinguishable inside the one merged actor.
    """
    n_bonds = len(bond_pairs)
    if segments is not None and len(segments) != n_bonds:
        segments = None  # inconsistent input: fall back rather than mispair
    if orders is None or len(orders) != n_bonds:
        orders = [1.0] * n_bonds

    all_cylinders: list[pv.PolyData] = []
    cylinder_colors: list[np.ndarray] = []
    for i, (a, b) in enumerate(bond_pairs):
        start, end = (
            segments[i] if segments is not None else (positions[a], positions[b])
        )
        direction = end - start
        length = float(np.linalg.norm(direction))
        if length < 1e-6:
            continue
        direction = direction / length
        center = (start + end) / 2
        order = orders[i]
        n_cyl = max(1, min(3, int(round(order))))
        if n_cyl == 1:
            centers = [center]
            cylinder_radius = radius
        else:
            # Match _add_bond_cylinders: parallel thinner cylinders, offset
            # along a stable perpendicular to the bond axis.
            ref = np.array([0.0, 0.0, 1.0])
            if abs(float(np.dot(direction, ref))) > 0.9:
                ref = np.array([0.0, 1.0, 0.0])
            perp = np.cross(direction, ref)
            norm = float(np.linalg.norm(perp))
            perp = perp / norm if norm else np.array([1.0, 0.0, 0.0])
            offsets = (np.arange(n_cyl) - (n_cyl - 1) / 2.0) * 0.13
            centers = [center + off * perp for off in offsets]
            cylinder_radius = 0.05

        color = colors[i] if colors and i < len(colors) else _bond_order_color(order)
        rgb = np.asarray(pv.Color(color).int_rgb, dtype=np.uint8)
        for cylinder_center in centers:
            cyl = pv.Cylinder(
                center=cylinder_center,
                direction=direction,
                radius=cylinder_radius,
                height=length,
                resolution=6,
            )
            all_cylinders.append(cyl)
            cylinder_colors.append(rgb)

    if not all_cylinders:
        return

    homogeneous_color = all(
        np.array_equal(rgb, cylinder_colors[0]) for rgb in cylinder_colors[1:]
    )
    if homogeneous_color:
        combined = (
            all_cylinders[0].merge(all_cylinders[1:])
            if len(all_cylinders) > 1
            else all_cylinders[0]
        )
        plotter.add_mesh(
            combined,
            color=tuple(float(channel) / 255.0 for channel in cylinder_colors[0]),
            name=name,
            smooth_shading=True,
            show_scalar_bar=False,
        )
        return

    for cylinder, rgb in zip(all_cylinders, cylinder_colors, strict=True):
        cylinder.cell_data["bond_rgb"] = np.tile(rgb, (cylinder.n_cells, 1))
    combined = all_cylinders[0].merge(all_cylinders[1:])
    plotter.add_mesh(
        combined,
        scalars="bond_rgb",
        rgb=True,
        preference="cell",
        color=default_color,
        name=name,
        smooth_shading=True,
        show_scalar_bar=False,
    )


# Pre-toon (interpolation, edge visibility) is owned by the plotter whose
# actors it describes. A module-global actor-name map let a later app or a
# rebuilt scene consume snapshots from already-removed actors with the same
# names.
_TOON_SAVED_ATTR = "_vibeview_toon_saved"


def _toon_saved_properties(plotter: pv.Plotter) -> dict[str, tuple[int, int]]:
    saved = getattr(plotter, _TOON_SAVED_ATTR, None)
    if not isinstance(saved, dict):
        saved = {}
        setattr(plotter, _TOON_SAVED_ATTR, saved)
    return saved


def _restore_toon_properties(
    plotter: pv.Plotter, saved_properties: dict[str, tuple[int, int]]
) -> None:
    """Best-effort restoration for actors that toon actually changed."""
    for actor_name, actor in getattr(plotter, "actors", {}).items():
        saved = saved_properties.get(actor_name)
        if saved is None:
            # An actor without a snapshot was never changed by this toon pass.
            # In particular, replacement actors must retain their native
            # interpolation until a successful enable records it.
            continue
        try:
            prop = actor.GetProperty() if hasattr(actor, "GetProperty") else None
            if prop is None:
                continue
            interpolation, edge_visibility = saved
            prop.SetInterpolation(interpolation)
            prop.SetEdgeVisibility(edge_visibility)
        except Exception:
            continue


def enable_toon_rendering(plotter: pv.Plotter, outline: bool = True, levels: int = 4) -> bool:
    """Enable non-photorealistic toon/cel shading.

    Produces a cartoon-like render with discrete color bands
    and optional black outlines — ideal for textbook-style figures.

    Parameters
    ----------
    plotter : pv.Plotter
    outline : bool
        Whether to show black outlines on mesh edges.
    levels : int
        Number of discrete color levels (2-10).

    Returns
    -------
    bool
        True on success, False if toon shading could not be applied.
    """
    saved_properties = _toon_saved_properties(plotter)
    try:
        # Apply flat shading + edge outlines to all actors.
        # Uses VTK properties available on all builds — no
        # vtkToonShadingProperty dependency needed.
        for actor_name in plotter.actors:
            actor = plotter.actors[actor_name]
            prop = actor.GetProperty() if hasattr(actor, "GetProperty") else None
            if prop is None:
                continue
            # Remember what this actor looked like so disable can
            # restore it — bonds and axes are not Phong by default, so
            # blanket-resetting to Phong on disable changed the scene.
            saved_properties.setdefault(
                actor_name, (prop.GetInterpolation(), prop.GetEdgeVisibility())
            )
            prop.SetInterpolationToFlat()  # Flat shading for toon look
            if outline:
                prop.SetEdgeVisibility(True)
                prop.SetEdgeColor(0.1, 0.1, 0.1)
                prop.SetLineWidth(1.0)

        plotter.render()
        return True
    except Exception:
        # Enabling is transactional: a partial pass must neither leave actors
        # half-toon nor leave snapshots that a future same-name actor could
        # consume after the UI correctly reports toon as disabled.
        _restore_toon_properties(plotter, saved_properties)
        saved_properties.clear()
        with contextlib.suppress(Exception):
            plotter.render()
        return False


def disable_toon_rendering(plotter: pv.Plotter) -> None:
    """Restore each actor's pre-toon shading (saved by enable)."""
    saved_properties = _toon_saved_properties(plotter)
    try:
        _restore_toon_properties(plotter, saved_properties)
    finally:
        # A restoration failure must not leak actor-name snapshots into a
        # later scene. Individual actor failures are already isolated above.
        saved_properties.clear()
    with contextlib.suppress(Exception):
        plotter.render()


def _add_bond_cylinders(
    plotter: pv.Plotter,
    center: np.ndarray,
    direction: np.ndarray,
    length: float,
    order: float,
    *,
    name: str,
    color_override: str | None = None,
) -> None:
    """Draw a bond as one or more parallel cylinders sized to its order.

    Single (order≈1) → one cylinder; double (≈2) → two thinner parallel
    cylinders; triple (≈3) → three. Aromatic (1.5) rounds to a double. The
    duplicates are offset perpendicular to the bond axis (audit finding
    A6-02 — the producer's bond ``order`` was previously discarded so every
    bond rendered single).
    """
    color = color_override or _bond_order_color(order)
    n_cyl = max(1, min(3, int(round(order))))
    if n_cyl == 1:
        cyl = pv.Cylinder(
            center=center, direction=direction, radius=0.1, height=length, resolution=8
        )
        plotter.add_mesh(cyl, color=color, name=name)
        return
    # A stable perpendicular to offset the parallel cylinders along.
    ref = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(direction, ref))) > 0.9:
        ref = np.array([0.0, 1.0, 0.0])
    perp = np.cross(direction, ref)
    norm = float(np.linalg.norm(perp))
    perp = perp / norm if norm else np.array([1.0, 0.0, 0.0])
    offsets = (np.arange(n_cyl) - (n_cyl - 1) / 2.0) * 0.13
    for k, off in enumerate(offsets):
        cyl = pv.Cylinder(
            center=center + off * perp,
            direction=direction,
            radius=0.05,
            height=length,
            resolution=8,
        )
        plotter.add_mesh(cyl, color=color, name=f"{name}_{k}")


def _add_bond_wire(
    plotter: pv.Plotter,
    center: np.ndarray,
    direction: np.ndarray,
    length: float,
    order: float,
    *,
    name: str,
    color_override: str | None = None,
) -> None:
    """Draw a bond as a thin line (wireframe representation).

    Single (order≈1) → one line; double (≈2) → two parallel lines;
    triple (≈3) → three.  Uses cylinders with very small radius (0.02)
    so they render as thin lines.
    """
    color = color_override or _bond_order_color(order)
    n_cyl = max(1, min(3, int(round(order))))
    radius = 0.02  # thin wireframe line
    if n_cyl == 1:
        cyl = pv.Cylinder(
            center=center, direction=direction, radius=radius, height=length, resolution=6
        )
        plotter.add_mesh(cyl, color=color, name=name)
        return
    ref = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(direction, ref))) > 0.9:
        ref = np.array([0.0, 1.0, 0.0])
    perp = np.cross(direction, ref)
    norm = float(np.linalg.norm(perp))
    perp = perp / norm if norm else np.array([1.0, 0.0, 0.0])
    offsets = (np.arange(n_cyl) - (n_cyl - 1) / 2.0) * 0.08
    for k, off in enumerate(offsets):
        cyl = pv.Cylinder(
            center=center + off * perp,
            direction=direction,
            radius=radius,
            height=length,
            resolution=6,
        )
        plotter.add_mesh(cyl, color=color, name=f"{name}_{k}")



# ── Cartoon / ribbon (roadmap D3) ────────────────────────────────────

# Spline samples per residue. The Ca trace is ~3.8 A between points, so
# 4 gives a visibly smooth ribbon without inflating the payload: an
# 885-residue chain lands near 1 MB, measured against ~15 MB for the
# same protein as 8,000-atom ball-and-stick
# (docs/bench_cartoon_payload.py).
_CARTOON_SAMPLES_PER_RESIDUE = 4
_CARTOON_RADIUS = 0.8          # angstroms — uniform round-cord fallback
_CARTOON_SIDES = 8

# Cross-section half-extents per secondary structure, angstroms. The
# profile is an ellipse spanned by the ribbon's own frame: ``width``
# along the in-plane side vector, ``thickness`` along the out-of-plane
# normal. A helix is wide and thin, so it reads as a flat band; a coil
# is equal in both, so it stays a round cord. These are display values,
# not measurements of anything.
_CARTOON_SS_WIDTH = {
    "H": 1.10,   # helix — 2.2 A across, the flat band the eye tracks
    "E": 0.90,   # strand — 1.8 A across, narrower than the helix
    "C": 0.35,   # coil / loop — round cord
}
_CARTOON_SS_THICKNESS = {
    "H": 0.20,   # 0.4 A edge-on: a band, not a cord
    "E": 0.18,
    "C": 0.35,   # equal to its width, so a loop is circular
}
# Residues over which a profile change is blended. A hard step at a helix
# terminus renders as a visible disc; a couple of residues of taper reads
# as the helix entering the loop.
_CARTOON_TAPER_RESIDUES = 1.5

# Sheet arrowhead, in residues at the C-terminal end of each strand run.
# The shoulder is a deliberate step (an arrow has a crisp barb), so the
# arrow is written over the smoothed profile rather than through it.
_CARTOON_ARROW_RESIDUES = 2.0
_CARTOON_ARROW_WIDTH = 1.60    # half-width at the shoulder
_CARTOON_ARROW_TIP = 0.12      # half-width at the point
# Below this many contiguous strand residues there is no room for an
# arrowhead; D2 calls a two-residue run a bridge, not a sheet.
_CARTOON_MIN_ARROW_RUN = 3

# Secondary-structure colours, following the convention the field already
# reads without a legend (PyMOL's default cartoon scheme): helices red,
# sheets yellow, loops grey.
_CARTOON_SS_COLOR = {
    "H": (0xE0, 0x36, 0x36),
    "E": (0xE8, 0xC4, 0x3A),
    "C": (0xB0, 0xB4, 0xBC),
}
# Per-residue-type colours, the RasMol "amino" scheme (Sayle &
# Milner-White, *RASMOL: biomolecular graphics for all*, Trends Biochem.
# Sci. 20, 374 (1995), doi:10.1016/S0968-0004(00)89080-5). Transcribed
# from that scheme rather than invented, because the whole value of
# colouring by residue type is that a reader already knows the mapping:
# acidic red, basic blue, aromatic indigo, and so on. Unknown and
# non-standard residues take the scheme's own fallback.
_CARTOON_RESIDUE_COLOR = {
    "ASP": (0xE6, 0x0A, 0x0A), "GLU": (0xE6, 0x0A, 0x0A),   # acidic
    "LYS": (0x14, 0x5A, 0xFF), "ARG": (0x14, 0x5A, 0xFF),   # basic
    "HIS": (0x82, 0x82, 0xD2),
    "PHE": (0x32, 0x32, 0xAA), "TYR": (0x32, 0x32, 0xAA),   # aromatic
    "TRP": (0xB4, 0x5A, 0xB4),
    "CYS": (0xE6, 0xE6, 0x00), "MET": (0xE6, 0xE6, 0x00),   # sulphur
    "SER": (0xFA, 0x96, 0x00), "THR": (0xFA, 0x96, 0x00),   # hydroxyl
    "ASN": (0x00, 0xDC, 0xDC), "GLN": (0x00, 0xDC, 0xDC),   # amide
    "LEU": (0x0F, 0x82, 0x0F), "VAL": (0x0F, 0x82, 0x0F),
    "ILE": (0x0F, 0x82, 0x0F),                              # aliphatic
    "ALA": (0xC8, 0xC8, 0xC8),
    "GLY": (0xEB, 0xEB, 0xEB),
    "PRO": (0xDC, 0x96, 0x82),
}
_CARTOON_RESIDUE_FALLBACK = (0xBE, 0xA0, 0x6E)

# Distinct, colour-blind-safe hues so adjacent chains stay tellable
# apart; cycled when a structure has more chains than colours.
_CHAIN_COLORS = (
    "#4363d8", "#e6194b", "#3cb44b", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
)

# Selected residues render white, preserving the cartoon selector's established
# accent in every representation. In atom views hydrogen may already be white,
# but the rest of a selected residue still changes from its element colours.
_RESIDUE_SELECTION_COLOR = "#ffffff"
_CARTOON_SELECTION_COLOR = (0xFF, 0xFF, 0xFF)


# ── Residue / chain selection (roadmap D4) ───────────────────────────


class SelectionTerm(NamedTuple):
    """One term of a residue selection: a chain, and optionally a range.

    ``chain`` is a chain id or ``"*"`` for every chain. ``start`` and
    ``end`` are inclusive residue sequence numbers, or both None for the
    whole chain.
    """

    chain: str
    start: int | None
    end: int | None

    def matches(self, chain: str, residue_seq: int) -> bool:
        if self.chain != "*" and self.chain != chain:
            return False
        if self.start is None:
            return True
        assert self.end is not None
        return self.start <= residue_seq <= self.end


def parse_residue_selection(spec: str) -> tuple[list[SelectionTerm], list[str]]:
    """Parse a residue selection into terms, plus the terms it rejected.

    The grammar is deliberately tiny — three forms, no operators, no
    nesting — because a selection *language* is a design commitment and
    this is the first slice of D4:

    * ``A`` — every residue of chain A.
    * ``A/24-38`` — residues 24 to 38 of chain A, inclusive.
    * ``A/24`` — one residue.
    * ``*`` in the chain position means every chain, so ``*/24-38``
      selects that range wherever it appears.

    Terms are separated by commas or whitespace, so ``A/24-38, B`` and
    ``A/24-38 B`` are the same. A reversed range is normalised.

    **A term without a slash is always a chain id, never a residue
    range.** ``24`` selects a chain literally named "24" (mmCIF chain ids
    can be numeric) and does *not* select residue 24; write ``*/24`` for
    that. Guessing from the shape of the token would make the meaning of
    a selection depend on the file it is applied to.

    Chain ids are matched **case-sensitively**: in a PDB, chain ``a`` and
    chain ``A`` are two different chains, and folding them would silently
    select twice what the user asked for. A chainless structure groups
    under the empty chain id, which cannot be typed — use ``*``.

    Rejected terms are returned rather than raised: a half-typed
    selection should narrow nothing and say so, not blank the viewport.
    """
    terms: list[SelectionTerm] = []
    rejected: list[str] = []
    for raw in re.split(r"[,\s]+", (spec or "").strip()):
        if not raw:
            continue
        chain, _, residues = raw.partition("/")
        if not chain:
            rejected.append(raw)
            continue
        if not residues:
            terms.append(SelectionTerm(chain, None, None))
            continue
        bounds = residues.split("-", 1)
        try:
            start = int(bounds[0])
            end = int(bounds[1]) if len(bounds) > 1 else start
        except ValueError:
            rejected.append(raw)
            continue
        terms.append(SelectionTerm(chain, min(start, end), max(start, end)))
    return terms, rejected


def _selected_atom_indices(
    structure: StructureData,
    terms: list[SelectionTerm],
) -> set[int]:
    """Atom indices in selected CA-bearing residues.

    Walk :meth:`StructureData.chains` rather than reading the optional fields
    from each atom. The chain map is the reader's precedence boundary: it uses
    producer-supplied residue membership when present and the per-atom PDB
    fields otherwise. Re-deriving here would make selection disagree with the
    cartoon and with files carrying canonical QVF biomolecule metadata.

    The eligible residue keys come from :func:`_ca_residue_keys`, preserving
    the selection domain and summary that the cartoon feature already shipped.
    Atom representations expand those same keys to every atom in the residue;
    they do not quietly make ligands or solvent without a CA selectable under
    grammar that still reports only ribbon residues.
    """
    if not terms:
        return set()
    selected_keys = {
        (chain, seq)
        for chain, seq in _ca_residue_keys(structure)
        if any(term.matches(chain, seq) for term in terms)
    }
    if not selected_keys:
        return set()
    selected: set[int] = set()
    for chain, residues in structure.chains().items():
        for seq, atom_indices in residues:
            if (chain, seq) in selected_keys:
                selected.update(atom_indices)
    return selected


def _ca_residue_keys(
    structure: StructureData,
    chain_id: str | None = None,
) -> list[tuple[str, int]]:
    """``(chain, residue_seq)`` per alpha-carbon, aligned with the trace.

    Must agree element-for-element with
    :meth:`StructureData.backbone_trace`, or a selection mask would
    highlight the wrong residues. ``StructureData.ca_residues()`` is the
    one ordering ``backbone_trace()`` and the supplied-assignment branch of
    ``secondary_structure()`` share, so defer to it rather than keeping a
    second copy of the walk that could drift.
    """
    return [(chain, seq) for chain, seq, _index in structure.ca_residues(chain_id)]


def _ca_residue_names(
    structure: StructureData,
    chain_id: str | None = None,
) -> list[str] | None:
    """Residue name per alpha-carbon, aligned with the trace.

    Same ordering as :func:`_ca_residue_keys`, which carries the CA's own
    atom index, so the name comes off that atom rather than off a second
    lookup that could pick a different residue.
    """
    return [
        (structure.atoms[index].residue_name or "")
        for _chain, _seq, index in structure.ca_residues(chain_id)
    ]


# Colour-by-b-factor endpoints. A continuous scale, unlike the categorical
# schemes above: cool blue for the rigid low-B end, warm red for the mobile
# high-B end, which is what `spectrum b` conditions people to expect.
_CARTOON_BFACTOR_COLD = (0x30, 0x54, 0xC8)
_CARTOON_BFACTOR_WARM = (0xE0, 0x36, 0x36)
# Atoms with no b-factor at all. Deliberately off-scale: `None` means
# unmeasured, not cold, and painting it the coldest colour would assert a
# rigidity nobody recorded. Distinct from a genuine 0.00, which is data.
_CARTOON_BFACTOR_ABSENT = (0x6E, 0x74, 0x80)


def _cartoon_bfactor_colors(
    structure: StructureData,
    chain_id: str,
    n_residues: int,
    n_samples: int,
) -> np.ndarray | None:
    """Per-spline-sample RGB from the per-atom b-factor (D4 colour-by).

    ``[n_samples, 3]`` uint8, or None when no alpha-carbon in the chain
    carries a b-factor — the caller then falls back to chain colour rather
    than painting the whole ribbon one meaningless hue.

    **Normalised over this structure, not an absolute scale.** B-factors
    from different refinements are not comparable, and a fixed 0-100 ramp
    flattens a well-ordered structure into one colour. The consequence is
    that the same residue can differ in colour between two files, so the
    caller states the observed range alongside the render.

    Unlike the categorical schemes this one *is* interpolated: b-factor is
    a continuous quantity, so a gradient between two residues is a real
    intermediate value rather than an invented category.
    """
    per_ca = [
        structure.atoms[index].b_factor
        for _chain, _seq, index in structure.ca_residues(chain_id)
    ]
    if len(per_ca) != n_residues:
        return None
    measured = [b for b in per_ca if b is not None]
    if not measured:
        return None

    low, high = min(measured), max(measured)
    span = high - low
    cold = np.array(_CARTOON_BFACTOR_COLD, dtype=float)
    warm = np.array(_CARTOON_BFACTOR_WARM, dtype=float)

    per_residue = np.empty((n_residues, 3), dtype=np.uint8)
    for i, b in enumerate(per_ca):
        if b is None:
            per_residue[i] = _CARTOON_BFACTOR_ABSENT
            continue
        # A structure refined to one uniform B has no gradient to show;
        # dividing by a zero span would be a NaN, so sit at the midpoint.
        frac = 0.5 if span <= 0 else (b - low) / span
        per_residue[i] = np.rint(cold + (warm - cold) * frac).astype(np.uint8)

    at = np.rint(np.linspace(0.0, n_residues - 1, n_samples)).astype(int)
    return per_residue[at]


def _cartoon_residue_colors(
    structure: StructureData,
    chain_id: str,
    n_residues: int,
    n_samples: int,
) -> np.ndarray | None:
    """Per-spline-sample RGB from the residue type (D4 colour-by).

    ``[n_samples, 3]`` uint8, or None when the chain carries no residue
    names at all — a file with residue *numbers* but no names would
    otherwise render one flat fallback colour, which says nothing and
    looks like a bug rather than like missing data.

    Not smoothed, for the reason the secondary-structure colours are not:
    a blend between two residues' colours is a residue that does not
    exist.
    """
    names = _ca_residue_names(structure, chain_id)
    if names is None or len(names) != n_residues:
        return None
    if not any(names):
        return None
    at = np.rint(np.linspace(0.0, n_residues - 1, n_samples)).astype(int)
    return np.array(
        [
            _CARTOON_RESIDUE_COLOR.get(
                names[i].upper(), _CARTOON_RESIDUE_FALLBACK
            )
            for i in at
        ],
        dtype=np.uint8,
    )


def summarize_selection(
    structure: StructureData,
    spec: str,
) -> tuple[int, list[str], list[str]]:
    """``(n_residues_matched, chains_touched, complaints)`` for a selection.

    The controller surfaces this so a selection that matches nothing says
    so, instead of looking like a render bug. ``complaints`` carries
    rejected terms and chain ids the structure does not have — naming an
    absent chain is the most common mistake and is worth calling out
    separately from a syntax error.
    """
    terms, rejected = parse_residue_selection(spec)
    complaints = [f"could not read {t!r}" for t in rejected]
    if not terms:
        return 0, [], complaints

    present = set(structure.chains())
    unknown = sorted(
        {t.chain for t in terms if t.chain != "*" and t.chain not in present}
    )
    if unknown:
        complaints.append("no such chain: " + ", ".join(unknown))
        if present and not any(present):
            # Every chain here is the empty chain id, which no selection
            # can name. A single-chain PDB with blank cols 22 is common —
            # DHFR from the RCSB is one — and without this the user is
            # told their chain does not exist with no way to find the one
            # that does.
            complaints.append("this file has no chain ids — select with *, e.g. */24-38")

    matched = 0
    touched: set[str] = set()
    for chain, seq in _ca_residue_keys(structure):
        if any(t.matches(chain, seq) for t in terms):
            matched += 1
            touched.add(chain)
    return matched, sorted(touched), complaints


def _cartoon_selection_mask(
    structure: StructureData,
    chain_id: str,
    terms: list[SelectionTerm],
    n_residues: int,
    n_samples: int,
) -> np.ndarray | None:
    """Per-spline-sample bool: is this sample inside the selection?

    None when nothing in this chain is selected, so the caller leaves the
    chain's colouring untouched.

    Resampled nearest-residue and **not** smoothed, for the same reason
    the colour profile is not: a half-selected residue does not exist.
    """
    if not terms:
        return None
    keys = _ca_residue_keys(structure, chain_id)
    if len(keys) != n_residues:
        # Should not happen — both come from the same walk — but a
        # misaligned mask would highlight the wrong residues silently.
        return None
    per_residue = np.array(
        [any(t.matches(chain, seq) for t in terms) for chain, seq in keys],
        dtype=bool,
    )
    if not per_residue.any():
        return None
    at = np.linspace(0.0, n_residues - 1, n_samples)
    return per_residue[np.rint(at).astype(int)]


def _add_cartoon(
    plotter: pv.Plotter,
    structure: StructureData,
    color_mode: str = "chain",
    selection: str = "",
) -> None:
    """Draw one ribbon per chain through the alpha-carbon trace.

    Each chain is splined and tubed **separately**: a single spline over
    a concatenated trace would draw a spurious ribbon leaping from the
    C-terminus of one chain to the N-terminus of the next.

    A chain of fewer than 4 alpha-carbons cannot be splined (and is not
    a meaningful ribbon), so it is skipped rather than approximated —
    ions and single-residue ligands land here.

    The cross-section is an ellipse carried along an oriented frame, not
    a circle: helices and strands render **flat and wide** and loops
    round and thin, from the per-residue assignment in
    :meth:`StructureData.secondary_structure` (D2), and each strand run
    ends in an arrowhead. When that assignment is unavailable the ribbon
    falls back to one uniform round cord, which still shows the fold.

    ``color_mode`` is ``"chain"`` (one hue per chain, so subunits are
    tellable apart), ``"structure"`` (helix / sheet / loop, so the
    architecture is readable within one chain), or ``"residue"`` (residue
    type, in the RasMol amino scheme). Structure mode needs the D2
    assignment and residue mode needs residue names; either falls back to
    chain colour without them.

    ``selection`` is a residue selection (see
    :func:`parse_residue_selection`). Matching residues render white, on
    top of whichever colour mode is active — a selection has to be
    visible in both, so it overrides rather than composing.
    """
    terms, _rejected = parse_residue_selection(selection)
    chains = structure.chains()
    for order, chain_id in enumerate(chains):
        trace = structure.backbone_trace(chain_id)
        if len(trace) < 4:
            continue
        n_samples = max(len(trace) * _CARTOON_SAMPLES_PER_RESIDUE, 16)
        points = np.asarray(pv.Spline(trace, n_samples).points, dtype=float)

        profile = _cartoon_ribbon_profile(
            structure, chain_id, len(trace), n_samples
        )
        if profile is None:
            # No usable assignment: one round cord of _CARTOON_RADIUS,
            # which is what D3 shipped before the profile existed.
            half_width = np.full(n_samples, _CARTOON_RADIUS)
            half_thickness = half_width
        else:
            half_width, half_thickness = profile

        side, normal = _cartoon_ribbon_frames(trace, points)
        mesh = _extrude_ribbon(points, side, normal, half_width, half_thickness)

        if color_mode == "structure":
            rgb = _cartoon_ss_colors(structure, chain_id, len(trace), n_samples)
        elif color_mode == "residue":
            rgb = _cartoon_residue_colors(
                structure, chain_id, len(trace), n_samples
            )
        elif color_mode == "bfactor":
            rgb = _cartoon_bfactor_colors(
                structure, chain_id, len(trace), n_samples
            )
        else:
            rgb = None
        chain_color = _CHAIN_COLORS[order % len(_CHAIN_COLORS)]
        selected = _cartoon_selection_mask(
            structure, chain_id, terms, len(trace), n_samples
        )
        if selected is not None:
            # Chain mode has no per-sample array of its own, so a
            # selection is the one thing that gives it one: fill with the
            # chain's flat colour, then overwrite the selected samples.
            flat = np.tile(_hex_to_rgb(chain_color), (n_samples, 1))
            rgb = flat if rgb is None else rgb.copy()
            rgb[selected] = _CARTOON_SELECTION_COLOR

        name = f"cartoon_chain_{chain_id or order}"
        if rgb is not None:
            # One colour per spline sample, held across that sample's
            # whole cross-section ring, so a colour boundary is a clean
            # cut across the ribbon rather than a diagonal smear.
            mesh.point_data["cartoon_rgb"] = np.repeat(
                rgb, _CARTOON_SIDES, axis=0
            )
            # rgb=True takes the array as literal colours rather than
            # running it through a colour map.
            plotter.add_mesh(
                mesh,
                scalars="cartoon_rgb",
                rgb=True,
                smooth_shading=True,
                name=name,
            )
        else:
            plotter.add_mesh(
                mesh,
                color=chain_color,
                smooth_shading=True,
                name=name,
            )


def _hex_to_rgb(color: str) -> np.ndarray:
    """``"#4363d8"`` to ``uint8[3]``."""
    h = color.lstrip("#")
    return np.array(
        [int(h[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.uint8
    )


def _cartoon_ribbon_frames(
    trace: np.ndarray,
    points: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-sample orientation frame for the ribbon cross-section.

    Returns ``(side, normal)``, each ``[n_samples, 3]`` unit vectors,
    both perpendicular to the local spline tangent and to each other.
    ``side`` is the direction the ribbon is *wide* in; ``normal`` is the
    thin direction, so a helix's flat face points away from its axis.

    Built from the alpha-carbon trace alone, the way ribbon models have
    been oriented since Carson & Bugg, *Algorithm for ribbon models of
    proteins*, J. Mol. Graphics 4, 121-122 (1986): at residue i take

        A = CA(i+1) - CA(i-1)          (local chain direction)
        B = CA(i-1) - 2 CA(i) + CA(i+1) (curvature; points at the axis)
        side = normalise(A x B)

    ``side`` is then perpendicular to the curvature, so the ribbon's thin
    direction lies along it and a helix presents its face outward.

    **The sign has to be propagated.** A beta-strand's alpha-carbons
    zig-zag, so B reverses at every residue and the raw ``A x B`` flips
    180 degrees each step; swept as-is the strand renders as a ribbon
    twisting a half-turn per residue instead of a flat plank. Each frame
    is therefore flipped to agree with its predecessor.
    """
    ca = np.asarray(trace, dtype=float)
    n = len(ca)

    a = np.empty_like(ca)
    b = np.empty_like(ca)
    a[1:-1] = ca[2:] - ca[:-2]
    b[1:-1] = ca[:-2] - 2.0 * ca[1:-1] + ca[2:]
    # The termini have no i-1 / i+1 pair; reuse the neighbouring frame
    # rather than inventing curvature from a one-sided difference.
    a[0], b[0] = a[1], b[1]
    a[-1], b[-1] = a[-2], b[-2]

    per_residue = np.cross(a, b)
    lengths = np.linalg.norm(per_residue, axis=1)
    # Three collinear alpha-carbons give no curvature and hence no
    # preferred side; carry the previous frame forward instead of
    # normalising a zero vector.
    good = lengths > 1e-9
    if not good.any():
        # A perfectly straight trace (only synthetic geometry does this):
        # any consistent perpendicular will do.
        per_residue = np.tile(_perpendicular_to(a[0]), (n, 1))
    else:
        first = int(np.argmax(good))
        per_residue[:first] = per_residue[first]
        for i in range(first + 1, n):
            if not good[i]:
                per_residue[i] = per_residue[i - 1]
        per_residue /= np.linalg.norm(per_residue, axis=1, keepdims=True)
        # Sign propagation — see the docstring; without this a strand
        # twists half a turn per residue.
        for i in range(1, n):
            if float(per_residue[i] @ per_residue[i - 1]) < 0.0:
                per_residue[i] = -per_residue[i]

    n_samples = len(points)
    # Resample the residue frames onto the spline's samples. Linear in
    # the vector then renormalised: the frames are already sign-aligned,
    # so neighbours are close and the interpolant cannot collapse.
    at = np.linspace(0.0, n - 1, n_samples)
    lo = np.clip(np.floor(at).astype(int), 0, n - 1)
    hi = np.clip(lo + 1, 0, n - 1)
    frac = (at - lo)[:, None]
    side = per_residue[lo] * (1.0 - frac) + per_residue[hi] * frac

    tangent = np.gradient(points, axis=0)
    t_len = np.linalg.norm(tangent, axis=1, keepdims=True)
    tangent = np.divide(tangent, t_len, out=np.zeros_like(tangent), where=t_len > 0)

    # Gram-Schmidt the side vector against the tangent so the ellipse is
    # swept in the plane perpendicular to the path, then complete the
    # right-handed frame.
    side = side - (np.sum(side * tangent, axis=1, keepdims=True)) * tangent
    s_len = np.linalg.norm(side, axis=1, keepdims=True)
    degenerate = (s_len <= 1e-9).ravel()
    if degenerate.any():
        side[degenerate] = np.array(
            [_perpendicular_to(t) for t in tangent[degenerate]]
        )
        s_len = np.linalg.norm(side, axis=1, keepdims=True)
    side = side / s_len
    normal = np.cross(tangent, side)
    n_len = np.linalg.norm(normal, axis=1, keepdims=True)
    normal = np.divide(normal, n_len, out=np.zeros_like(normal), where=n_len > 0)
    return side, normal


def _perpendicular_to(v: np.ndarray) -> np.ndarray:
    """Any unit vector perpendicular to ``v`` (``v`` need not be unit)."""
    v = np.asarray(v, dtype=float)
    # Cross with whichever axis v is least aligned to, so the cross
    # product is never near-zero.
    axis = np.zeros(3)
    axis[int(np.argmin(np.abs(v)))] = 1.0
    out = np.cross(v, axis)
    length = float(np.linalg.norm(out))
    return out / length if length > 1e-12 else np.array([1.0, 0.0, 0.0])


def _extrude_ribbon(
    points: np.ndarray,
    side: np.ndarray,
    normal: np.ndarray,
    half_width: np.ndarray,
    half_thickness: np.ndarray,
    n_sides: int = _CARTOON_SIDES,
) -> pv.PolyData:
    """Sweep an elliptical cross-section along an oriented path.

    This is what replaces ``spline.tube()``: a tube can only vary one
    radius, so a strand came out a thick cord. Here each sample carries
    its own half-width (along ``side``) and half-thickness (along
    ``normal``), which is what makes a helix a flat band and lets a
    strand taper to an arrow point.

    Topology matches an ``n_sides`` tube exactly — ``n_sides`` points per
    sample, and the surface emitted as ``n_sides`` **triangle strips**
    running the length of the chain. Three details keep the wire payload
    at the tube's own 0.60 MB on the 885-residue ClC benchmark; each was
    measured against that number rather than assumed, and each was worth
    a 1.7x to 2.5x payload on its own:

    * Strips, not polygons. The identical surface written as independent
      quads serialises to 1.28 MB.
    * Point normals computed here. ``add_mesh(smooth_shading=True)``
      leaves a mesh that already carries normals alone, and otherwise
      derives them itself, which triangulates the strips away — 1.51 MB.
      ``tube()`` emits normals, so this has to as well.
    * float32 vertices and normals, which is what ``tube()`` emits. Left
      at numpy's float64 the same mesh is 1.06 MB.
    """
    n_samples = len(points)
    ang = np.linspace(0.0, 2.0 * np.pi, n_sides, endpoint=False)
    cos, sin = np.cos(ang), np.sin(ang)

    # [n_samples, n_sides, 3]
    ring = (
        points[:, None, :]
        + (half_width[:, None] * cos[None, :])[:, :, None] * side[:, None, :]
        + (half_thickness[:, None] * sin[None, :])[:, :, None] * normal[:, None, :]
    )
    verts = ring.reshape(-1, 3)

    # Surface normal as the cross product of the two parametric
    # derivatives, outward by construction (see the sign note below).
    # d/dtheta of the ellipse is analytic; d/ds is the swept direction,
    # differenced so a taper tilts the normal the way the surface
    # actually tilts.
    d_theta = (
        (half_width[:, None] * -sin[None, :])[:, :, None] * side[:, None, :]
        + (half_thickness[:, None] * cos[None, :])[:, :, None] * normal[:, None, :]
    )
    d_s = np.gradient(ring, axis=0)
    # cross(d_theta, d_s), not cross(d_s, d_theta): for a circular
    # cross-section the latter evaluates to -(outward radial).
    normals = np.cross(d_theta, d_s)
    lengths = np.linalg.norm(normals, axis=2, keepdims=True)
    normals = np.divide(
        normals, lengths, out=np.zeros_like(normals), where=lengths > 1e-12
    )

    # One strip per longitudinal face, zig-zagging between the k-th and
    # (k+1)-th point of each successive ring.
    i = np.arange(n_samples)[None, :]
    k = np.arange(n_sides)[:, None]
    k_next = (k + 1) % n_sides
    zigzag = np.stack(
        [
            np.broadcast_to(i * n_sides + k_next, (n_sides, n_samples)),
            np.broadcast_to(i * n_sides + k, (n_sides, n_samples)),
        ],
        axis=-1,
    ).reshape(n_sides, 2 * n_samples)
    strips = np.hstack(
        [
            np.full((n_sides, 1), 2 * n_samples, dtype=np.int64),
            zigzag.astype(np.int64),
        ]
    ).ravel()

    # Cap both ends. The first is wound backwards so its face points out
    # of the chain rather than into it.
    caps = np.concatenate(
        [
            [n_sides],
            np.arange(n_sides)[::-1],
            [n_sides],
            (n_samples - 1) * n_sides + np.arange(n_sides),
        ]
    ).astype(np.int64)
    mesh = pv.PolyData(verts.astype(np.float32), faces=caps, strips=strips)
    mesh.point_data["Normals"] = normals.reshape(-1, 3).astype(np.float32)
    mesh.point_data.active_normals_name = "Normals"
    return mesh


def _cartoon_ss_colors(
    structure: StructureData,
    chain_id: str,
    n_residues: int,
    n_samples: int,
) -> np.ndarray | None:
    """Per-spline-sample RGB from the secondary-structure assignment.

    Returns ``[n_samples, 3]`` uint8, or None when there is no assignment
    to colour by (the caller then falls back to one colour per chain).

    Unlike the radius profile this is **not** smoothed: a helix should end
    where it ends, and blending its colour into the loop would invent a
    gradient that means nothing.
    """
    try:
        labels = structure.secondary_structure(chain_id)
    except Exception:  # noqa: BLE001 — chain colour is a fine fallback
        return None
    if len(labels) != n_residues:
        return None
    if not any(label in ("H", "E") for label in labels):
        return None  # all coil — one flat grey says the same thing

    at = np.rint(np.linspace(0.0, n_residues - 1, n_samples)).astype(int)
    return np.array(
        [_CARTOON_SS_COLOR.get(labels[i], _CARTOON_SS_COLOR["C"]) for i in at],
        dtype=np.uint8,
    )


def _cartoon_ribbon_profile(
    structure: StructureData,
    chain_id: str,
    n_residues: int,
    n_samples: int,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Per-spline-sample ``(half_width, half_thickness)`` in angstroms.

    Returns None to fall back to one uniform round cord: no assignment,
    an assignment that does not line up with the trace, or a chain that
    is all coil and so has no architecture to draw.

    Both profiles are resampled onto the spline's points and smoothed
    over ``_CARTOON_TAPER_RESIDUES``, so a helix does not end in a
    visible step where it meets its loop.

    The **arrowheads are written over** the smoothed width rather than
    through it: a sheet arrow's shoulder is a deliberate discontinuity,
    and smoothing it would round the barb off into a bulge.
    """
    try:
        labels = structure.secondary_structure(chain_id)
    except Exception:  # noqa: BLE001 — a ribbon is still better than nothing
        return None
    if len(labels) != n_residues:
        # Should not happen (both derive from the same trace), but a
        # mismatched profile would silently mis-shape the whole chain.
        return None
    if not any(label in ("H", "E") for label in labels):
        return None  # all coil — a uniform round cord says the same thing

    width = _cartoon_ss_profile(labels, _CARTOON_SS_WIDTH, n_samples)
    thickness = _cartoon_ss_profile(labels, _CARTOON_SS_THICKNESS, n_samples)
    _apply_sheet_arrows(width, labels, n_samples)
    return width, thickness


def _cartoon_ss_profile(
    labels: str,
    values: dict[str, float],
    n_samples: int,
) -> np.ndarray:
    """Resample a per-secondary-structure scalar onto the spline, smoothed."""
    n_residues = len(labels)
    per_residue = np.array(
        [values.get(label, values["C"]) for label in labels], dtype=float
    )
    # Nearest-residue resampling: the spline samples run along the trace,
    # so sample j sits at residue j * (n_residues - 1) / (n_samples - 1).
    at = np.linspace(0.0, n_residues - 1, n_samples)
    out = per_residue[np.rint(at).astype(int)]

    taper = max(int(round(_CARTOON_TAPER_RESIDUES * _CARTOON_SAMPLES_PER_RESIDUE)), 1)
    if taper > 1 and len(out) > taper:
        kernel = np.ones(taper, dtype=float) / taper
        # Pad by edge value so the chain's own ends are not thinned.
        padded = np.pad(out, (taper, taper), mode="edge")
        out = np.convolve(padded, kernel, mode="same")[taper : taper + n_samples]
    return out


def _strand_runs(labels: str) -> list[tuple[int, int]]:
    """Inclusive ``(first, last)`` residue index of each contiguous ``E`` run."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, label in enumerate(labels):
        if label == "E":
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(labels) - 1))
    return runs


def _apply_sheet_arrows(width: np.ndarray, labels: str, n_samples: int) -> None:
    """Widen then taper the last residues of each strand run, in place.

    A beta-strand is drawn pointing at its C-terminus: the width steps
    out to ``_CARTOON_ARROW_WIDTH`` at the shoulder and falls linearly to
    ``_CARTOON_ARROW_TIP`` at the point. Without this the strand is a
    plank with no direction, which is the whole reason sheets are drawn
    as arrows.

    Runs shorter than ``_CARTOON_MIN_ARROW_RUN`` are left as plain planks
    — there is no room for a barb, and D2 reads a two-residue run as a
    bridge rather than a sheet.
    """
    n_residues = len(labels)
    if n_residues < 2 or n_samples < 2:
        return
    # Residue index -> sample index, the same mapping _cartoon_ss_profile
    # resamples with.
    per_residue = (n_samples - 1) / (n_residues - 1)
    for first, last in _strand_runs(labels):
        if last - first + 1 < _CARTOON_MIN_ARROW_RUN:
            continue
        shoulder = max(float(last) - _CARTOON_ARROW_RESIDUES, float(first))
        j0 = int(round(shoulder * per_residue))
        j1 = int(round(last * per_residue))
        if j1 <= j0:
            continue
        span = np.linspace(0.0, 1.0, j1 - j0 + 1)
        width[j0 : j1 + 1] = (
            _CARTOON_ARROW_WIDTH
            + (_CARTOON_ARROW_TIP - _CARTOON_ARROW_WIDTH) * span
        )


def _draw_unit_cell(
    plotter: pv.Plotter,
    lattice: np.ndarray,
    pbc: tuple[bool, bool, bool] = (True, True, True),
) -> None:
    """Draw the wireframe cell spanned by the *periodic* lattice vectors only.

    Only the axes flagged in ``pbc`` are real cell edges. A 3D crystal gives the
    familiar 8-corner parallelepiped; a 2D slab gives the in-plane ``a,b``
    parallelogram (4 edges); a 1D polymer gives a single ``a`` edge. Columns of
    a lower-dimensional lattice beyond ``dim`` are synthesized bookkeeping for
    the AO integrals and are never drawn — see ``StructureData``.

    LATTICE CONVENTION (structure section): the ``structure`` JSON stores
    lattice vectors as **ROWS** — ``lattice[0]`` is a, ``[1]`` is b, ``[2]``
    is c (the writer transposes ``PeriodicSystem.lattice`` with ``.T`` before
    emitting; see python/vibeqc/output/formats/qvf.py::_write_structure_section).
    This is the OPPOSITE of ``reaction.path``, whose binary ``lattice`` member
    stores vectors as COLUMNS (see renderers/reaction.py::_cell_edges). Both
    are correct for their own source; do not cross-wire them without
    transposing, or non-orthogonal cells will be silently sheared.
    """
    vecs = [np.asarray(lattice[i], dtype=float) for i, p in enumerate(pbc) if p]
    n = len(vecs)
    if n == 0:
        return

    # Corners are every subset-sum of the periodic vectors; an edge joins two
    # corners differing by exactly one vector. n=3 -> 8 corners / 12 edges,
    # n=2 -> 4 / 4, n=1 -> 2 / 1.
    corners = [
        sum((vecs[k] for k in range(n) if mask & (1 << k)), np.zeros(3))
        for mask in range(1 << n)
    ]

    for mask in range(1 << n):
        for k in range(n):
            if mask & (1 << k):
                continue
            i, j = mask, mask | (1 << k)
            line = pv.Line(corners[i], corners[j])
            plotter.add_mesh(line, color="#444444", line_width=2, name=f"cell_{i}_{j}")
