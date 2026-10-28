"""Nanotube and interface builder card (roadmap: carbon nanotubes).

**Build nanotube** opens a new structure: a single- or multi-wall (n,m) tube
of a given length, periodic along its axis, open, hydrogen-terminated or
capped. The card reads out each wall's diameter and chiral angle, whether
it is metallic, and the spacing between walls, as the indices are typed.

**Views** colour atoms by coordination, distance from the axis, partial
charge or bond order to a metal; cut the structure away to show the inner
wall, a cross-section or an encapsulated species; and point an orthographic
camera down the axis or at the cut face (:mod:`vibeview.structure_views`).

**Interface analysis** reports metal–C contacts, the PDOS of the interface
atoms and the axial charge-difference profile
(:mod:`vibeview.interface_analysis`).

**Add partner** puts a metal slab, a nanoparticle or an aminophenyl-amide
linker at the open end or on the sidewall of the tube that is shown, at a
chosen distance and registry. It is an edit of the shown structure, made
through the edit overlay like every other builder edit, so one Undo
removes it. Partners are finite cluster models: the shown structure must
not be periodic.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from vibeview import interface_builder as ib
from vibeview import nanotube as nt
from vibeview import structure_views as views
from vibeview.qvf import Atom, QVFReader, StructureData
from vibeview.qvf_writer import structure_archive

END_LABELS = {
    "hydrogen": "Open, H-terminated",
    "open": "Open, bare",
    "cap": "Capped (C60 half)",
    "periodic": "Periodic along the axis",
}


def wall_readout(n: Any, m: Any, walls: Any, spacing: Any) -> str:
    """One line per wall: (n,m), diameter, chiral angle, kind; then spacings."""
    try:
        series = nt.wall_series(int(n), int(m), max(1, int(walls)), float(spacing))
    except (TypeError, ValueError) as exc:
        return str(exc)
    lines = []
    for nn, mm in series:
        w = nt.wall_info(nn, mm)
        lines.append(
            f"({w.n},{w.m}) {w.kind}: d = {w.diameter:.2f} Å, θ = {w.chiral_angle:.1f}°, "
            f"{'metallic' if w.metallic else 'semiconducting'}"
        )
    radii = [nt.wall_info(*s).diameter / 2.0 for s in series]
    gaps = [b - a for a, b in zip(radii, radii[1:], strict=False)]
    if gaps:
        lines.append("Wall spacing: " + ", ".join(f"{g:.2f} Å" for g in gaps))
    return "\n".join(lines)


def build_tube(state: Any) -> nt.Nanotube:
    """The tube the card's fields describe."""
    n, m = int(state.nt_n), int(state.nt_m)
    walls = max(1, int(state.nt_walls))
    length = float(state.nt_length)
    if length <= 0:
        raise ValueError("The tube length must be positive")
    end = str(state.nt_end)
    if walls > 1:
        return nt.multi_wall(n, m, walls, length, spacing=float(state.nt_spacing), end=end)
    return nt.single_wall(n, m, length, end=end)


def tube_reader(tube: nt.Nanotube, label: str) -> QVFReader:
    builder = structure_archive(tube.atoms(), tube.pbc, tube.lattice,
                                calculation=f"nanotube builder: {label}")
    builder.set_provenance({"builder": "nanotube", "charge": 0, "multiplicity": 1,
                            "builder_details": {"walls": [[w.n, w.m] for w in tube.walls]}})
    return QVFReader(builder.to_bytesio())


def partner_options(state: Any) -> dict[str, Any]:
    """Keyword arguments of :func:`vibeview.interface_builder.build_partner`."""
    partner = str(state.nt_partner)
    distance = state.nt_distance
    options: dict[str, Any] = {
        "site": str(state.nt_site),
        "registry": str(state.nt_registry),
        "distance": None if distance in (None, "") else float(distance),
    }
    metal = str(state.nt_metal)
    if partner == "slab":
        options.update(metal=metal, facet=str(state.nt_facet), layers=int(state.nt_layers))
    elif partner == "particle":
        options.update(metal=metal, radius=float(state.nt_radius))
    else:
        options.update(metal=metal if state.nt_linker_metal else None,
                       facet=str(state.nt_facet), layers=int(state.nt_layers))
    return options


def bind(viewer: Any) -> None:
    from vibeview.builder_panel import apply_structure

    state, ctrl = viewer.server.state, viewer.ctrl
    state.update(
        {
            "nt_n": 10,
            "nt_m": 10,
            "nt_length": 30.0,
            "nt_end": "hydrogen",
            "nt_walls": 1,
            "nt_spacing": nt.WALL_SPACING,
            "nt_readout": wall_readout(10, 10, 1, nt.WALL_SPACING),
            "nt_partner": "slab",
            "nt_metal": "Cu",
            "nt_facet": "111",
            "nt_site": "end",
            "nt_registry": "top",
            # Blank: the sum of the covalent radii, a bonded contact.
            "nt_distance": "",
            "nt_layers": 3,
            "nt_radius": 6.0,
            "nt_linker_metal": True,
            "nt_color_mode": "element",
            "cutaway": "off",
            "cutaway_position": 0.5,
            "nt_interface_html": "",
        }
    )

    def guarded(action: Any) -> None:
        try:
            action()
        except (ValueError, TypeError, KeyError, RuntimeError) as exc:
            state.status_message = str(exc)

    def build() -> None:
        tube = build_tube(state)
        label = ", ".join(f"({w.n},{w.m})" for w in tube.walls)
        viewer.files._adopt_reader(tube_reader(tube, label))
        state.status_message = (
            f"Built a {label} tube, {tube.n_atoms} atoms (ideal cylinder; relax before use)"
        )

    def add_partner() -> None:
        current = viewer.reader.read_structure()
        if any(current.pbc):
            raise ValueError(
                "Partners are built as finite cluster models; build the tube with open, "
                "hydrogen-terminated or capped ends")
        symbols = [a.symbol for a in current.atoms]
        positions = np.array([a.position for a in current.atoms], dtype=float)
        result = ib.build_partner(symbols, positions, str(state.nt_partner),
                                  **partner_options(state))
        atoms = [Atom(s, p.copy(), 0) for s, p in zip(result.symbols, result.positions,
                                                      strict=True)]
        apply_structure(viewer, StructureData(atoms, current.pbc, current.lattice_vectors, None),
                        topology=True)
        tube_atom, partner_atom, distance = result.contact or (0, 0, 0.0)
        state.status_message = (
            f"Added {result.note}: {result.symbols[partner_atom]}{partner_atom + 1}–"
            f"{result.symbols[tube_atom]}{tube_atom + 1} {distance:.2f} Å (one undo step)"
        )

    def rebuild() -> None:
        from vibeview.app import helpers

        helpers._rebuild_scene(viewer.reader, viewer.plotter, viewer.viewer_state, state)

    def set_color(mode: Any) -> None:
        from vibeview.app import helpers

        mode = str(helpers._first(mode) or "element")
        if mode != "element":
            structure = viewer.reader.read_structure()
            values = views.property_values(structure, viewer.reader.connectivity(structure), mode,
                                           viewer.reader)
            _, (low, high) = views.property_colors(values, mode)
        state.atom_color_mode = mode
        state.nt_color_mode = mode
        rebuild()
        state.status_message = (views.describe(mode, low, high) if mode != "element"
                                else "Atoms coloured by element")

    def set_cutaway(mode: Any = None, position: Any = None) -> None:
        from vibeview.app import helpers

        if mode is not None:
            mode = str(helpers._first(mode))
            if mode not in views.CUTAWAYS:
                raise ValueError(f"Unknown cut-away {mode!r}")
            state.cutaway = mode
        if position is not None:
            state.cutaway_position = min(1.0, max(0.0, float(helpers._first(position))))
        rebuild()
        state.status_message = ("Cut-away off" if state.cutaway == "off" else
                                f"Cut-away: {state.cutaway} (inner atoms are always kept)")

    def view_axis(view: Any) -> None:
        from vibeview.app import helpers

        view = str(helpers._first(view))
        position, focal, up = views.axis_camera(viewer.reader.read_structure(), view)
        camera = viewer.plotter.camera
        camera.position, camera.focal_point, camera.up = (
            tuple(position), tuple(focal), tuple(up))
        camera.parallel_projection = True
        state.orthographic_projection = True
        viewer.plotter.reset_camera()
        helpers._push_camera(viewer.plotter)
        state.status_message = ("Orthographic view down the axis" if view == "down"
                                else "Orthographic side view onto the cut face")

    def analyse() -> None:
        from vibeview.interface_analysis import metal_carbon_contacts, report_html

        structure = viewer.reader.read_structure()
        state.nt_interface_html = report_html(viewer.reader, structure)
        n = len(metal_carbon_contacts(structure, viewer.reader))
        state.status_message = f"Interface analysis: {n} metal–C contacts"

    def refresh(**_: Any) -> None:
        state.nt_readout = wall_readout(state.nt_n, state.nt_m, state.nt_walls, state.nt_spacing)

    ctrl.set("nt_color")(lambda mode=None, *_: guarded(lambda: set_color(mode)))
    ctrl.set("nt_cutaway")(
        lambda mode=None, position=None, *_: guarded(lambda: set_cutaway(mode, position)))
    ctrl.set("nt_view")(lambda view="down", *_: guarded(lambda: view_axis(view)))
    ctrl.set("nt_analyse")(lambda: guarded(analyse))
    ctrl.set("nt_build")(lambda: guarded(build))
    ctrl.set("nt_add_partner")(lambda: guarded(add_partner))
    # Through the lifecycle, so a second create_app does not stack watchers.
    viewer.lifecycle._watch_state("nt_n", "nt_m", "nt_walls", "nt_spacing")(refresh)


def build_controls(v: Any, html: Any, ctrl: Any) -> None:
    with v.VCard(classes="pa-2 mb-2"):
        v.VCardTitle("Nanotube builder and views")
        html.Div(
            "Ideal starting geometries; relax them before a calculation.",
            classes="text-caption",
        )
        with v.VRow(dense=True):
            with v.VCol(cols=6):
                v.VTextField(v_model=("nt_n",), type="number", min=0, label="n",
                             density="compact", hide_details=True)
            with v.VCol(cols=6):
                v.VTextField(v_model=("nt_m",), type="number", min=0, label="m",
                             density="compact", hide_details=True)
        with v.VRow(dense=True):
            with v.VCol(cols=6):
                v.VTextField(v_model=("nt_walls",), type="number", min=1, label="Walls",
                             density="compact", hide_details=True)
            with v.VCol(cols=6):
                v.VTextField(v_model=("nt_spacing",), type="number", step=0.05,
                             label="Wall spacing (Å)", density="compact", hide_details=True,
                             disabled=("Number(nt_walls) < 2",))
        v.VTextField(v_model=("nt_length",), type="number", label="Length (Å)",
                     density="compact", hide_details=True, classes="mt-2")
        v.VSelect(
            v_model=("nt_end",),
            items=("nt_end_options", [{"title": t, "value": k} for k, t in END_LABELS.items()]),
            label="Ends",
            density="compact",
            hide_details=True,
            classes="mt-2",
        )
        html.Pre("{{ nt_readout }}", classes="text-caption",
                 style="white-space: pre-wrap; margin: 6px 0")
        v.VBtn("Build nanotube", click=ctrl.nt_build, size="small", variant="tonal",
               block=True)
        v.VDivider(classes="my-2")
        v.VSelect(
            v_model=("nt_partner",),
            items=("nt_partner_options", [
                {"title": "Metal slab", "value": "slab"},
                {"title": "Nanoparticle", "value": "particle"},
                {"title": "Aminophenyl amide linker", "value": "linker"},
            ]),
            label="Partner",
            density="compact",
            hide_details=True,
        )
        with v.VRow(dense=True, classes="mt-1"):
            with v.VCol(cols=6):
                v.VSelect(v_model=("nt_metal",), items=("nt_metal_options", list(ib.FCC_METALS)),
                          label="Metal", density="compact", hide_details=True)
            with v.VCol(cols=6):
                v.VSelect(v_model=("nt_facet",), items=("nt_facet_options", list(ib.FACETS)),
                          label="Facet", density="compact", hide_details=True,
                          disabled=("nt_partner === 'particle'",))
        with v.VRow(dense=True):
            with v.VCol(cols=6):
                v.VSelect(v_model=("nt_site",), items=("nt_site_options", [
                    {"title": "Open end", "value": "end"},
                    {"title": "Sidewall", "value": "sidewall"},
                ]), label="Site", density="compact", hide_details=True)
            with v.VCol(cols=6):
                v.VSelect(v_model=("nt_registry",),
                          items=("nt_registry_options", list(ib.REGISTRIES)),
                          label="Registry", density="compact", hide_details=True)
        with v.VRow(dense=True):
            with v.VCol(cols=6):
                v.VTextField(v_model=("nt_distance",), type="number", step=0.05,
                             label="M–C distance (Å)", placeholder="covalent radii",
                             density="compact", hide_details=True)
            with v.VCol(cols=6):
                v.VTextField(v_model=("nt_layers",), type="number", min=1, label="Layers",
                             density="compact", hide_details=True,
                             v_if=("nt_partner !== 'particle'",))
                v.VTextField(v_model=("nt_radius",), type="number", label="Radius (Å)",
                             density="compact", hide_details=True,
                             v_if=("nt_partner === 'particle'",))
        v.VCheckbox(v_model=("nt_linker_metal",), label="Bond a metal slab to the linker",
                    density="compact", hide_details=True,
                    v_if=("nt_partner === 'linker'",))
        v.VBtn("Add partner to shown tube", click=ctrl.nt_add_partner, size="small",
               variant="tonal", block=True, classes="mt-2")
        v.VDivider(classes="my-2")
        v.VSelect(
            v_model=("nt_color_mode",),
            items=("nt_color_options", [{"title": "Element", "value": "element"}] + [
                {"title": t, "value": k} for k, t in views.PROPERTY_LABELS.items()]),
            label="Colour atoms by",
            density="compact",
            hide_details=True,
            update_modelValue=(ctrl.nt_color, "[$event]"),
        )
        v.VSelect(
            v_model=("cutaway",),
            items=("cutaway_options", [
                {"title": "No cut-away", "value": "off"},
                {"title": "Half wall (see inside)", "value": "half"},
                {"title": "Cross-section slice", "value": "section"},
                {"title": "Cut at a point on the axis", "value": "end"},
            ]),
            label="Cut-away",
            density="compact",
            hide_details=True,
            classes="mt-2",
            update_modelValue=(ctrl.nt_cutaway, "[$event]"),
        )
        v.VSlider(
            v_model=("cutaway_position",),
            min=0, max=1, step=0.01,
            label="Cut position",
            aria_label="Cut-away position",
            __properties=[("aria_label", "aria-label")],
            density="compact",
            hide_details=True,
            disabled=("cutaway === 'off' || cutaway === 'half'",),
            end=(ctrl.nt_cutaway, "[null, $event]"),
        )
        with v.VRow(dense=True):
            with v.VCol(cols=6):
                v.VBtn("Down the axis", click=(ctrl.nt_view, "['down']"), size="small",
                       variant="tonal", block=True)
            with v.VCol(cols=6):
                v.VBtn("Side view", click=(ctrl.nt_view, "['side']"), size="small",
                       variant="tonal", block=True)
        v.VCheckbox(v_model=("nt_depth_cue", False), label="Depth cue (browser figure)",
                    density="compact", hide_details=True,
                    update_modelValue="window.vvDepthCue && window.vvDepthCue.set($event)")
        v.VDivider(classes="my-2")
        v.VBtn("Interface analysis", click=ctrl.nt_analyse, size="small", variant="tonal",
               block=True)
        html.Div(
            "Metal–C contacts with lengths and bond orders, the PDOS of the interface "
            "atoms and the charge difference along the axis, from the archive's sections.",
            classes="text-caption",
        )
        html.Iframe(
            v_if=("nt_interface_html",),
            srcdoc=("nt_interface_html",),
            style="width: 100%; height: 640px; border: none;",
            sandbox="allow-scripts",
        )
