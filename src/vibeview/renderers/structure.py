"""Structure renderer — v2.1 with LOD and batched large-system support.

Draws atoms as spheres (CPK or ball-and-stick), unit cell wireframe
when PBC flags are true, and bonds (explicit from bonds.json or
inferred from covalent radii).
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np
import pyvista as pv
from vtkmodules.vtkCommonCore import vtkWeakReference

from vibeview.elements import (
    UNKNOWN_COLOR,
    VDW_RADII,
    covalent_radius,
    cpk_hex,
    symbol_to_z,
    vdw_radius,
)
from vibeview.profiler import profiler
from vibeview.qvf import clamp_replication
from vibeview.renderers import BaseRenderer, palette
from vibeview.renderers.cell import bond_far_endpoints, cell_wireframe, supercell_offsets
from vibeview.renderers.palette import (
    BFACTOR_ABSENT,
    BFACTOR_COLD,
    BFACTOR_WARM,
    BOND_ORDER_ANCHORS,
    GUIDE_GREY,
    SS,
)

if TYPE_CHECKING:
    from vibeview.bonds import Bond
    from vibeview.qvf import QVFReader, Section, StructureData

Color = str | tuple[float, float, float]


_DEFAULT_COLOR = UNKNOWN_COLOR

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

    Prefer the parsed ``Atom.atomic_number``; the shared symbol lookup is
    available for structures that provide only a symbol.
    """
    z = int(atomic_number)
    override = _COLOR_OVERRIDES.get(z)
    if override:
        return override
    return cpk_hex(z)


def cpk_radius(atomic_number: int, scale: float = 1.0) -> float:
    """Approximate van der Waals display radius (Å) by atomic number.

    Falls back to ~1.6× the covalent radius for elements outside the small
    vdW table (Z>20) instead of a flat 1.7 Å, so e.g. Fe/Au/U don't all get
    the same size.
    """
    return vdw_radius(int(atomic_number)) * scale


# Label meshes are rebuilt, not rotated in place, when the camera moves.
# Keyed by actor name on the plotter and tied to that exact actor, so a
# re-orient can replace only geometry the registry still owns.
_LABEL_SPECS_ATTR = "_vibe_view_label_specs"


class _LabelSpec(NamedTuple):
    positions: list[Any]
    strings: list[str]
    scale: float
    lift: float
    actor_ref: vtkWeakReference


def _label_actor_ref(actor: Any) -> vtkWeakReference:
    """A VTK-native weak reference stable across PyVista wrapper collection."""
    actor_ref = vtkWeakReference()
    actor_ref.Set(actor)
    return actor_ref


def _billboard_basis(
    anchor: np.ndarray, camera: Any
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
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
    positions: Sequence[Any] | np.ndarray,
    strings: Sequence[str],
    *,
    scale: float = 0.5,
    lift: float = 0.55,
    camera: Any = None,
) -> pv.PolyData | None:
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
    for pos, text in zip(positions, strings, strict=True):
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


def register_label_actor(
    plotter: Any,
    name: str,
    positions: Sequence[Any] | np.ndarray,
    strings: Sequence[str],
    *,
    scale: float = 0.5,
    lift: float = 0.55,
) -> None:
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


def forget_label_actors(plotter: Any) -> None:
    """Drop the registry — the scene that owned those actors is gone."""
    setattr(plotter, _LABEL_SPECS_ATTR, {})


def reorient_labels(plotter: Any) -> int:
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


class StructureRenderer(BaseRenderer):
    """3D structure drawer."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._structure: StructureData | None = None
        self._bonds: list[Bond] | None = None
        self._bonds_loaded = False

    def load_structure(self) -> StructureData:
        """Load and cache structure geometry without inferring bonds.

        The renderer's own structure section, edit overlay included; a
        renderer built on a section of another kind draws the shown one.
        """
        if self._structure is None:
            self._structure = self.reader.read_structure(self._structure_id)
        return self._structure

    @property
    def _structure_id(self) -> str | None:
        return self.section_id if self.kind == "structure" else None

    def load(self) -> StructureData:
        """Load structure data and initialise its bond cache once."""
        s = self.load_structure()
        if not self._bonds_loaded:
            # QVFReader owns the one molecular/periodic inference path,
            # including minimum-image bonds and bond-order enrichment.
            self._bonds = self.reader.connectivity(s, self._structure_id)
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
        atom_color_mode: str = "element",
        residue_visibility: str = "all",
        show_hydrogen_bonds: bool = False,
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

        ``cartoon_color_mode`` colours ribbons by chain, structure, residue
        or bfactor. ``atom_color_mode`` offers the same palettes for atoms and
        bonds, defaulting to element colours. ``residue_visibility`` is all,
        isolate or hide; ``show_hydrogen_bonds`` adds dashed geometric contacts
        using explicit H in the input cell.

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
                atom_color_mode=atom_color_mode,
                residue_visibility=residue_visibility,
                show_hydrogen_bonds=show_hydrogen_bonds,
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
        atom_color_mode: str = "element",
        residue_visibility: str = "all",
        show_hydrogen_bonds: bool = False,
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
                    visibility=residue_visibility,
                )
                if show_hydrogen_bonds:
                    _add_hydrogen_bonds(plotter, structure, residue_selection, residue_visibility)
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
        visible_atoms = _visible_atom_indices(structure, selected_atoms, residue_visibility)
        residue_colors = _biomolecule_atom_colors(structure, atom_color_mode)

        # Resolve per-atom radius based on representation style.
        def _atom_radius(z: int) -> float:
            if representation == "space_filling":
                # vdW fallback for elements missing from VDW_RADII: ~1.75x the
                # covalent radius is a reasonable estimate. The old
                # fallback inherited the ball-and-stick scale (0.35x), so
                # heavy atoms rendered at a third of their vdW size.
                return VDW_RADII.get(z, covalent_radius(z, 1.5) * 1.75)
            return cpk_radius(z, ball_scale)

        def _atom_color(
            atom_idx: int,
            z: int,
        ) -> Color:
            if atom_idx in selected_atoms:
                return _RESIDUE_SELECTION_COLOR
            if atom_colors is not None and atom_idx in atom_colors:
                return atom_colors[atom_idx]
            return residue_colors.get(atom_idx, cpk_color(z))

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
        image_offsets = supercell_offsets(lattice, structure.pbc, (rx, ry, rz))

        for atom_idx, atom in enumerate(structure.atoms):
            if atom_idx not in visible_atoms:
                continue
            pos = atom.position.copy()
            # Prefer the parsed atomic number; symbol-only atoms use the
            # same H–Cm identity table as the importers.
            z = atom.atomic_number or symbol_to_z(atom.symbol)
            for offset in image_offsets:
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

            groups: dict[tuple[Any, ...], list[np.ndarray]] = defaultdict(list)
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
            bonds = [
                b for b in (self._bonds or []) if b[0] in visible_atoms and b[1] in visible_atoms
            ]
            if bonds:
                n_atoms = len(structure.atoms)
                # A bond past the atom list is a producer error; skip it here
                # once rather than in every replica.
                bonds = [b for b in bonds if int(b[0]) < n_atoms and int(b[1]) < n_atoms]
                n_bonds_total = len(bonds)

                # Bonds replicate over the same cell offsets as the atom
                # spheres — without this, replication rendered bare atoms
                # in every cell but the base one.
                cell_offsets = list(image_offsets)

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
                        cell_positions = base_positions + offset
                        valid_pairs: list[tuple[int, int]] = []
                        valid_orders: list[float] = []
                        segments: list[tuple[np.ndarray, np.ndarray]] = []
                        selected_pairs: list[tuple[int, int]] = []
                        selected_orders: list[float] = []
                        selected_segments: list[tuple[np.ndarray, np.ndarray]] = []
                        bond_starts, bond_ends = bond_far_endpoints(
                            cell_positions, bonds, lattice, structure.pbc
                        )
                        for k, bond in enumerate(bonds):
                            i, j = int(bond[0]), int(bond[1])
                            order = float(bond[2]) if len(bond) > 2 else 1.0
                            p1, p2 = bond_starts[k], bond_ends[k]
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
                                cell_positions,
                                valid_pairs,
                                orders=valid_orders,
                                colors=[_residue_bond_color(residue_colors, i, j)
                                        for i, j in valid_pairs] if residue_colors else None,
                                name=f"bonds_batched_{cell_idx}",
                                segments=segments,
                            )
                        if selected_pairs:
                            _add_batched_bonds(
                                plotter,
                                cell_positions,
                                selected_pairs,
                                orders=selected_orders,
                                colors=[_RESIDUE_SELECTION_COLOR] * len(selected_pairs),
                                name=f"bonds_selected_{cell_idx}",
                                segments=selected_segments,
                                default_color=_RESIDUE_SELECTION_COLOR,
                            )
                else:
                    base_positions = np.array([a.position for a in structure.atoms])
                    for cell_idx, offset in enumerate(cell_offsets):
                        bond_starts, bond_ends = bond_far_endpoints(
                            base_positions + offset, bonds, lattice, structure.pbc
                        )
                        for k, bond in enumerate(bonds):
                            i, j = int(bond[0]), int(bond[1])
                            order = float(bond[2]) if len(bond) > 2 else 1.0
                            p1, p2 = bond_starts[k], bond_ends[k]
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
                                else _residue_bond_color(residue_colors, i, j)
                                if residue_colors else None
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

        if show_hydrogen_bonds:
            _add_hydrogen_bonds(plotter, structure, residue_selection, residue_visibility)

        # ── Unit cell wireframe ───────────────────────────────────────
        if lattice is not None and any(structure.pbc):
            _draw_unit_cell(plotter, lattice, structure.pbc)


def _bond_order_color(order: float) -> str:
    """Map bond order to a display colour.

    single (≈1.0)  → grey
    aromatic (≈1.5) → purple
    double (≈2.0)  → blue
    triple (≈3.0)  → red
    Fractional orders interpolate between the nearest anchors.
    """
    anchors = BOND_ORDER_ANCHORS
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
    return GUIDE_GREY


def _add_batched_bonds(
    plotter: pv.Plotter,
    positions: np.ndarray,
    bond_pairs: list[tuple[int, int]],
    colors: Sequence[Color] | None = None,
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
_TOON_SAVED_ATTR = "_vibe_view_toon_saved"


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


def enable_toon_rendering(plotter: pv.Plotter, outline: bool = True) -> bool:
    """Enable non-photorealistic toon/cel shading.

    Switches every actor to flat shading and optionally draws dark edge
    outlines, for textbook-style figures. Flat shading is what the vtk.js
    client can reproduce; a banded (quantised) lighting ramp would need a
    server-side shader pass that never reaches the client.

    Parameters
    ----------
    plotter : pv.Plotter
    outline : bool
        Whether to show black outlines on mesh edges.

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
            # A rendered volume carries a vtkVolumeProperty, which has no
            # interpolation mode or edges to flatten; toon leaves it alone.
            if prop is None or not hasattr(prop, "GetInterpolation"):
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
    color_override: Color | None = None,
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
    color_override: Color | None = None,
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
    "G": 0.75, "I": 1.40, "B": 0.55,
    "H": 1.10,   # helix — 2.2 A across, the flat band the eye tracks
    "E": 0.90,   # strand — 1.8 A across, narrower than the helix
    "C": 0.35,   # coil / loop — round cord
}
_CARTOON_SS_THICKNESS = {
    "G": 0.16, "I": 0.24, "B": 0.18,
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
_CARTOON_SS_COLOR = {label: palette.rgb(color) for label, color in SS.items()}
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


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Half-open contiguous spans, without joining across hidden residues."""
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False].astype(int)))
    return list(zip(edges[::2], edges[1::2], strict=True))


def _visible_atom_indices(
    structure: StructureData, selected: set[int], visibility: str
) -> set[int]:
    all_atoms = set(range(len(structure.atoms)))
    if visibility == "isolate":
        return selected
    if visibility == "hide":
        return all_atoms - selected
    return all_atoms


def _biomolecule_atom_colors(
    structure: StructureData, mode: str
) -> dict[int, tuple[float, float, float]]:
    """Apply the ribbon palette to every atom in each CA-bearing residue."""
    if mode not in ("chain", "structure", "residue", "bfactor"):
        return {}
    colors = {}
    for order, (chain, residues) in enumerate(structure.chains().items()):
        keys = structure.ca_residues(chain)
        n = len(keys)
        if not n:
            continue
        helper = {"structure": _cartoon_ss_colors, "residue": _cartoon_residue_colors,
                  "bfactor": _cartoon_bfactor_colors}.get(mode)
        rgb = helper(structure, chain, n, n) if helper else None
        if rgb is None:
            rgb = np.tile(_hex_to_rgb(palette.chain_color(order)), (n, 1))
        by_seq = {
            seq: (float(rgb[i][0]) / 255, float(rgb[i][1]) / 255, float(rgb[i][2]) / 255)
            for i, (_, seq, _) in enumerate(keys)
        }
        for seq, indices in residues:
            if seq in by_seq:
                colors.update(dict.fromkeys(indices, by_seq[seq]))
    return colors


def _residue_bond_color(
    colors: dict[int, tuple[float, float, float]], i: int, j: int
) -> tuple[float, float, float]:
    left = colors.get(i, (0.6, 0.6, 0.6))
    right = colors.get(j, (0.6, 0.6, 0.6))
    return ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2, (left[2] + right[2]) / 2)


def hydrogen_bond_contacts(structure: StructureData) -> list[tuple[int, int, int]]:
    """Geometric (donor, H, acceptor) candidates in the supplied coordinates.

    Explicit H and N/O donors/acceptors only; no inferred protonation or
    periodic images. Criteria: D-H <= 1.2 Å, D-A <= 3.0 Å, D-H-A >= 150°,
    as in MDAnalysis HydrogenBondAnalysis (Smith, 2019 documentation). This
    is a display heuristic, not an electronic/chemical bond assignment.
    A cell list keeps the search local without requiring scipy.
    """
    from collections import defaultdict
    from itertools import product

    positions = np.array([atom.position for atom in structure.atoms])
    cells = defaultdict(list)
    for index, atom in enumerate(structure.atoms):
        if atom.symbol in ("N", "O"):
            cells[tuple(np.floor(positions[index] / 3.0).astype(int))].append(index)

    def neighbors(point: np.ndarray) -> list[int]:
        cell = np.floor(point / 3.0).astype(int)
        return [index for shift in product((-1, 0, 1), repeat=3)
                for index in cells.get(tuple(cell + shift), ())]

    contacts = []
    for h, atom in enumerate(structure.atoms):
        if atom.symbol != "H":
            continue
        nearby = neighbors(positions[h])
        donors = [(float(np.linalg.norm(positions[d] - positions[h])), d) for d in nearby]
        donors = [(distance, d) for distance, d in donors if 0.1 < distance <= 1.2]
        if not donors:
            continue
        _, donor = min(donors)
        dh = positions[donor] - positions[h]
        for acceptor in nearby:
            if acceptor == donor:
                continue
            da = np.linalg.norm(positions[acceptor] - positions[donor])
            ha = positions[acceptor] - positions[h]
            length = np.linalg.norm(ha)
            if not (1.5 < da <= 3.0 and length > 1.2):
                continue
            cosine = float(np.dot(dh, ha) / (np.linalg.norm(dh) * length))
            if cosine <= np.cos(np.deg2rad(150.0)):
                contacts.append((donor, h, acceptor))
    return contacts


def _add_hydrogen_bonds(
    plotter: Any, structure: StructureData, selection: str = "", visibility: str = "all"
) -> None:
    terms, _ = parse_residue_selection(selection)
    visible = _visible_atom_indices(structure, _selected_atom_indices(structure, terms), visibility)
    points: list[np.ndarray] = []
    for donor, hydrogen, acceptor in hydrogen_bond_contacts(structure):
        if not {donor, hydrogen, acceptor} <= visible:
            continue
        start, end = structure.atoms[hydrogen].position, structure.atoms[acceptor].position
        count = max(2, int(np.ceil(np.linalg.norm(end - start) / 0.3)))
        for t in np.arange(count) / count:
            points.extend((start + t * (end - start), start + (t + 0.55 / count) * (end - start)))
    if points:
        mesh = pv.PolyData(np.asarray(points, dtype=np.float32))
        mesh.lines = np.column_stack((np.full(len(points) // 2, 2),
                                      np.arange(len(points)).reshape(-1, 2))).ravel()
        plotter.add_mesh(mesh, color="#45b8c8", line_width=2, lighting=False,
                         name="hydrogen_bond_contacts", pickable=False)


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
_CARTOON_BFACTOR_COLD = palette.rgb(BFACTOR_COLD)
_CARTOON_BFACTOR_WARM = palette.rgb(BFACTOR_WARM)
# Atoms with no b-factor at all. Deliberately off-scale: `None` means
# unmeasured, not cold, and painting it the coldest colour would assert a
# rigidity nobody recorded. Distinct from a genuine 0.00, which is data.
_CARTOON_BFACTOR_ABSENT = palette.rgb(BFACTOR_ABSENT)


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

    **Normalised over this structure, not an absolute scale**: one min-max
    over ALL atoms that carry a b-factor, never per chain, so an equal B is
    an equal colour in every chain and matches the terminal ramp
    (maintainer decision, #49). B-factors
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
    if all(b is None for b in per_ca):
        return None

    # The whole structure's range, not this chain's (#49).
    structure_b = [
        float(b) for atom in structure.atoms
        if (b := getattr(atom, "b_factor", None)) is not None
    ]
    # palette.sequential_ramp is the terminal's ramp too: absent -> grey,
    # a structure refined to one uniform B -> the midpoint.
    ramp = palette.sequential_ramp(
        structure_b + [float("nan") if b is None else float(b) for b in per_ca]
    )[len(structure_b):]
    per_residue = np.array(ramp, dtype=np.uint8).reshape(n_residues, 3)

    at = np.rint(np.linspace(0.0, n_residues - 1, n_samples)).astype(int)
    sampled: np.ndarray = per_residue[at]
    return sampled


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
    mask: np.ndarray = per_residue[np.rint(at).astype(int)]
    return mask


def _add_cartoon(
    plotter: pv.Plotter,
    structure: StructureData,
    color_mode: str = "chain",
    selection: str = "",
    visibility: str = "all",
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
        chain_color = palette.chain_color(order)
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

        keep = np.ones(n_samples, dtype=bool)
        mask = selected if selected is not None else np.zeros(n_samples, dtype=bool)
        if visibility == "isolate":
            keep = mask
        elif visibility == "hide":
            keep = ~mask
        labels = structure.secondary_structure(chain_id, detailed=True)
        at = np.rint(np.linspace(0, len(trace) - 1, n_samples)).astype(int)
        rectangular = np.array([labels[i] in ("E", "B") for i in at])
        ca_indices = np.array([index for _, _, index in structure.ca_residues(chain_id)],
                              dtype=np.int32)
        runs = _true_runs(keep)
        for part, (start, stop) in enumerate(runs):
            if stop - start < 2:
                continue
            span = slice(start, stop)
            mesh = _extrude_ribbon(points[span], side[span], normal[span],
                                   half_width[span], half_thickness[span],
                                   rectangular=rectangular[span])
            samples = np.concatenate((np.repeat(np.arange(start, stop), _CARTOON_SIDES),
                                      np.repeat([start, stop - 1], _CARTOON_SIDES)))
            mesh.point_data["residue_atom_index"] = ca_indices[at[samples]]
            name = f"cartoon_chain_{chain_id or order}"
            if len(runs) > 1:
                name += f"_part_{part}"
            if rgb is not None:
                mesh.point_data["cartoon_rgb"] = rgb[samples]
                plotter.add_mesh(mesh, scalars="cartoon_rgb", rgb=True,
                                 smooth_shading=True, name=name)
            else:
                plotter.add_mesh(mesh, color=chain_color, smooth_shading=True, name=name)


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
    rectangular: np.ndarray | None = None,
) -> pv.PolyData:
    """Sweep elliptical or crisp rectangular sections along an oriented path.

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
    if rectangular is not None and np.any(rectangular):
        if n_sides != 8:
            raise ValueError("Rectangular ribbons require eight corner vertices")
        # Duplicate each corner so adjoining flat faces have independent normals.
        square_x = np.array([1, 1, 1, -1, -1, -1, -1, 1])
        square_y = np.array([-1, 1, 1, 1, 1, -1, -1, -1])
        square = (points[:, None, :]
                  + half_width[:, None, None] * square_x[None, :, None] * side[:, None, :]
                  + half_thickness[:, None, None] * square_y[None, :, None] * normal[:, None, :])
        ring[rectangular] = square[rectangular]
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
    if rectangular is not None and np.any(rectangular):
        dx = np.array([0, 0, -1, -1, 0, 0, 1, 1])
        dy = np.array([1, 1, 0, 0, -1, -1, 0, 0])
        square_tangent = (half_width[:, None, None] * dx[None, :, None] * side[:, None, :]
                          + half_thickness[:, None, None] * dy[None, :, None] * normal[:, None, :])
        d_theta[rectangular] = square_tangent[rectangular]
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

    # Caps need separate vertices: sharing radial normals makes their faces
    # shade like the side wall. Use the outward tangent at each endpoint.
    cap_start = len(verts)
    caps = np.concatenate(([n_sides], cap_start + np.arange(n_sides)[::-1],
                           [n_sides], cap_start + n_sides + np.arange(n_sides))).astype(np.int64)
    verts = np.concatenate((verts, ring[0], ring[-1]))
    tangents = np.array([points[0] - points[1], points[-1] - points[-2]])
    tangents /= np.maximum(np.linalg.norm(tangents, axis=1, keepdims=True), 1e-12)
    all_normals = np.concatenate((normals.reshape(-1, 3),
                                  np.repeat(tangents, n_sides, axis=0)))
    mesh = pv.PolyData(verts.astype(np.float32), faces=caps, strips=strips)
    mesh.point_data["Normals"] = all_normals.astype(np.float32)
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
        labels = structure.secondary_structure(chain_id, detailed=True)
    except Exception:  # noqa: BLE001 — chain colour is a fine fallback
        return None
    if len(labels) != n_residues:
        return None
    if not any(label in ("H", "G", "I", "E", "B") for label in labels):
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
        labels = structure.secondary_structure(chain_id, detailed=True)
    except Exception:  # noqa: BLE001 — a ribbon is still better than nothing
        return None
    if len(labels) != n_residues:
        # Should not happen (both derive from the same trace), but a
        # mismatched profile would silently mis-shape the whole chain.
        return None
    if not any(label in ("H", "G", "I", "E", "B") for label in labels):
        return None  # all coil — a uniform round cord says the same thing

    width = _cartoon_ss_profile(labels, _CARTOON_SS_WIDTH, n_samples)
    thickness = _cartoon_ss_profile(labels, _CARTOON_SS_THICKNESS, n_samples)
    _apply_sheet_arrows(width, labels, n_samples)
    return width, thickness


def _cartoon_ss_profile(
    labels: Sequence[str],
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
    out: np.ndarray = per_residue[np.rint(at).astype(int)]

    taper = max(int(round(_CARTOON_TAPER_RESIDUES * _CARTOON_SAMPLES_PER_RESIDUE)), 1)
    if taper > 1 and len(out) > taper:
        kernel = np.ones(taper, dtype=float) / taper
        # Pad by edge value so the chain's own ends are not thinned.
        padded = np.pad(out, (taper, taper), mode="edge")
        out = np.convolve(padded, kernel, mode="same")[taper : taper + n_samples]
    return out


def _strand_runs(labels: Sequence[str]) -> list[tuple[int, int]]:
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


def _apply_sheet_arrows(width: np.ndarray, labels: Sequence[str], n_samples: int) -> None:
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
    mesh = cell_wireframe(lattice, pbc)
    if mesh is None:
        return
    # One actor for the whole cell; the "cell_" prefix is what the edit
    # rebuild removes.
    plotter.add_mesh(mesh, color="#444444", line_width=2, name="cell_wireframe")
