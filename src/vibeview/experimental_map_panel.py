"""Experimental map levels, sampled histogram and selection-following zones."""
from __future__ import annotations

from typing import Any

import numpy as np

from vibeview.experimental_map import ExperimentalMap, map_surface


def refresh(viewer: Any, **_: Any) -> None:
    state = viewer.server.state
    choices = []
    for i, reader in enumerate(viewer._all_readers):
        if "experimental_map" in reader.provenance:
            for section in reader.sections:
                if section.kind == "volume.generic":
                    choices.append({"title": f"Map in file {i+1}: {section.id}",
                                    "value": f"{i}:{section.id}"})
    state.expmap_options = choices
    if state.expmap_source not in [c["value"] for c in choices]:
        state.expmap_source = choices[0]["value"] if choices else ""
    if state.expmap_enabled:
        render(viewer)


def render(viewer: Any, **_: Any) -> None:
    from vibeview.app import helpers

    state = viewer.server.state
    if getattr(viewer, "_expmap_busy", False):
        return
    viewer._expmap_busy = True
    try:
        if not state.expmap_enabled:
            viewer.plotter.remove_actor("experimental-map", render=False)
            helpers._push_view(viewer.plotter)
            return
        index, section_id = str(state.expmap_source).split(":", 1)
        reader = viewer._all_readers[int(index)]
        meta = reader.provenance["experimental_map"]
        grid = reader.read_volume_grid(section_id)
        stats = meta["header_statistics"]
        density = ExperimentalMap(
            reader.read_volume_data(section_id), grid.origin*grid.to_angstrom,
            grid.voxel_vectors*grid.to_angstrom,
            float(stats["mean"]) if stats["mean"] is not None else float("nan"),
            float(stats["rms"]) if stats["rms"] is not None else float("nan"),
            float(stats["minimum"]) if stats["minimum"] is not None else float("nan"),
            float(stats["maximum"]) if stats["maximum"] is not None else float("nan"),
            tuple(meta["axis_order"]), meta["origin_convention"],
        )
        selection = None
        if state.expmap_zone:
            structure = viewer.reader.read_structure()
            ids = sorted(set(state.selected_atoms or []) | set(state.edit_selected or []))
            if any(i < 0 or i >= len(structure.atoms) for i in ids):
                raise ValueError("Selection is stale; select atoms in the current model again")
            selection = np.array([structure.atoms[i].position for i in ids]).reshape(-1, 3)
        level = float(state.expmap_level)
        sigma = state.expmap_units == "sigma"
        mesh = map_surface(density, level, sigma=sigma, selection=selection,
                           radius=float(state.expmap_radius))
        counts, edges = density.histogram(40)
        maximum = max(int(counts.max()), 1)
        state.expmap_histogram = [
            {"index": i, "count": int(n), "level": float((edges[i]+edges[i+1])/2),
             "height": max(2, int(70*n/maximum))}
            for i, n in enumerate(counts)
        ]
        absolute = density.sigma_level(level) if sigma else level
        state.expmap_notice = (
            f"Absolute level {absolute:.7g}; original mean {density.mean:.7g}, "
            f"RMS {density.rms:.7g}; import stride {meta['stride']}. "
            "Histogram is the interaction sample; no symmetry expansion or fitting."
        )
        # This explicit map control replaces the generic active-volume display,
        # avoiding simultaneous contours with different levels.
        helpers._remove_actors_by_prefix(viewer.plotter, helpers._Actors.VOLUME)
        viewer.plotter.remove_actor("experimental-map", render=False)
        if mesh.n_points:
            viewer.plotter.add_mesh(mesh, name="experimental-map", color="steelblue",
                                    opacity=float(state.expmap_opacity),
                                    style=("wireframe" if state.expmap_style == "mesh"
                                           else "surface"),
                                    )
        helpers._push_view(viewer.plotter)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        state.status_message = f"Map: {exc}"
    finally:
        viewer._expmap_busy = False


def choose_bin(viewer: Any, index: Any) -> None:
    state = viewer.server.state
    level = state.expmap_histogram[int(index)]["level"]
    state.expmap_units = "absolute"
    state.expmap_level = level
    render(viewer)


def bind(viewer: Any) -> None:
    state, ctrl = viewer.server.state, viewer.ctrl
    for key, value in {"expmap_options": [], "expmap_source": "", "expmap_enabled": False,
                       "expmap_units": "sigma", "expmap_level": 1., "expmap_style": "surface",
                       "expmap_zone": False, "expmap_radius": 3., "expmap_opacity": .45,
                       "expmap_notice": "", "expmap_histogram": []}.items():
        state.setdefault(key, value)
    ctrl.set("expmap_refresh")(lambda: refresh(viewer))
    ctrl.set("expmap_apply")(lambda: render(viewer))
    ctrl.set("expmap_bin")(lambda index: choose_bin(viewer, index))
    state.change("active_file_index", "selected_section")(lambda **kw: refresh(viewer))
    state.change("selected_atoms", "edit_selected", "coord_rows")(lambda **kw: render(viewer)
                                                  if state.expmap_enabled else None)
    state.change("expmap_enabled")(lambda **kw: render(viewer))
    refresh(viewer)


def build_controls(v: Any, html: Any, ctrl: Any) -> None:
    with v.VCard(classes="pa-2 mb-2"):
        v.VCardTitle("Experimental map", classes="text-subtitle-1")
        html.Div("Open a map and model as separate files. Select model atoms to define a zone.",
                 classes="text-caption")
        v.VBtn("Refresh open maps", click=ctrl.expmap_refresh, size="small")
        v.VSelect(v_model=("expmap_source",), items=("expmap_options",), label="Map")
        v.VSwitch(v_model=("expmap_enabled",), label="Show controlled map")
        with html.Div(v_if=("expmap_enabled",)):
            v.VSelect(v_model=("expmap_units",), items=("['sigma', 'absolute']",),
                      label="Level units")
            v.VTextField(v_model=("expmap_level",), type="number", label="Level")
            v.VSelect(v_model=("expmap_style",), items=("['surface', 'mesh']",), label="Display")
            v.VSlider(
                v_model=("expmap_opacity",), min=0, max=1, step=.05, label="Opacity",
                aria_label="Experimental map opacity",
                __properties=[("aria_label", "aria-label")],
            )
            v.VSwitch(v_model=("expmap_zone",), label="Zone around selected atoms")
            v.VTextField(v_model=("expmap_radius",), type="number", label="Zone radius (Å)")
            v.VBtn("Apply map controls", click=ctrl.expmap_apply, size="small")
            html.Div("Sample histogram: click a bar to choose an absolute level",
                     classes="text-caption")
            with html.Div(style="display:flex;align-items:end;height:76px"):
                html.Button(v_for="bin in expmap_histogram", key=("bin.index",),
                            title=("bin.level + ': ' + bin.count + ' voxels'",),
                            style=("{height:bin.height+'px',width:'2.5%',background:'steelblue'}",),
                            click=(ctrl.expmap_bin, "[bin.index]"))
            html.Div("{{ expmap_notice }}", classes="text-caption")
