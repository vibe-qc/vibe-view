"""Publication figure controls, persistent state and shared capture decoration."""

from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from vibeview.figure_scene import FigureScene, draw_measurements, mapped_attribute, overlay


def scene(viewer: Any) -> FigureScene:
    raw = viewer.server.state.figure_scene
    return FigureScene.from_dict(raw) if isinstance(raw, dict) else FigureScene()


def composite(viewer: Any, image: Any) -> Any:
    from PIL import Image

    state = viewer.server.state
    return Image.alpha_composite(
        image.convert("RGBA"),
        overlay(
            scene(viewer), image.size, reference_size=tuple(state.figure_viewport or image.size)
        ),
    )


def deliver_figure(viewer: Any, image: Any) -> bool:
    fmt = viewer.server.state.figure_export_format
    if fmt not in ("pdf", "svg"):
        return False
    viewer.server.state.figure_export_format = ""
    data = io.BytesIO()
    if fmt == "pdf":
        image.convert("RGB").save(data, format="PDF", resolution=150.0)
        payload = data.getvalue()
        mime = "application/pdf"
    else:
        image.save(data, format="PNG")
        encoded = base64.b64encode(data.getvalue()).decode()
        payload = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{image.width}" '
            f'height="{image.height}" '
            f'viewBox="0 0 {image.width} {image.height}"><image width="100%" height="100%" '
            f'href="data:image/png;base64,{encoded}"/></svg>'
        ).encode()
        mime = "image/svg+xml"
    viewer.exports._offer_download(payload, mime, f"figure.{fmt}", label="Figure", fmt=fmt.upper())
    viewer.server.state.status_message = f"Figure {fmt.upper()} exported (embedded viewport image)"
    return True


def _structures(viewer: Any) -> dict[str, Any]:
    from vibeview.qvf import SectionNotFoundError

    try:
        result = {"current": viewer.reader.read_structure()}
    except SectionNotFoundError:
        # Chart-only archives still use figure controls, without geometry.
        result = {}
    commands = getattr(viewer, "_model_scene_commands", None)
    if commands is not None:
        result.update({name: model.structure for name, model in commands.scene.models.items()})
    return result


def render(viewer: Any, *, push: bool = False) -> None:
    if getattr(viewer, "_figure_busy", False):
        return
    viewer._figure_busy = True
    try:
        from vibeview.app import helpers

        state = viewer.server.state
        spec = scene(viewer)
        draw_measurements(
            viewer.plotter,
            spec,
            _structures(viewer),
            str(state.representation_style or "ball_and_stick"),
        )
        hidden = getattr(viewer, "_figure_hidden", {})
        for name in list(viewer.plotter.actors):
            if name.startswith("figure_attr_"):
                viewer.plotter.remove_actor(name, render=False)
        for name, (actor, visible) in hidden.items():
            if viewer.plotter.actors.get(name) is actor:
                actor.visibility = visible and not bool(state.model_scene_enabled)
                if state.model_scene_enabled:
                    viewer._model_scene_hidden[name] = (actor, visible)
        viewer._figure_hidden = {}
        if spec.attribute is not None:
            if state.model_scene_enabled:
                raise ValueError(
                    "Attribute mapping targets the current structure; disable working models"
                )
            from vibeview.model_scene_panel import _NamespacedPlotter
            from vibeview.qvf import Section
            from vibeview.renderers.structure import StructureRenderer

            structure = viewer.reader.read_structure()
            colors, radii = mapped_attribute(spec.attribute, len(structure.atoms))
            renderer = StructureRenderer(
                Section(id="figure-attribute", kind="structure"), viewer.reader
            )
            renderer._structure = structure
            renderer._bonds = structure.bonds
            renderer._bonds_loaded = structure.bonds is not None
            for name, actor in list(viewer.plotter.actors.items()):
                if name.startswith(("atom_", "bond_", "bonds_", "cartoon_", "cell_")):
                    viewer._figure_hidden[name] = (actor, actor.visibility)
                    actor.visibility = False
            wrapper = _NamespacedPlotter(viewer.plotter, "figure_attr_", np.zeros(3))
            renderer.add_to_plotter(
                wrapper,  # type: ignore[arg-type]
                replication=tuple(viewer.viewer_state.replication),
                representation=str(state.representation_style or "ball_and_stick"),
                atom_colors=colors,
                atom_radii=radii,
            )
        raw_size = state.figure_viewport or [900, 600]
        size = (int(raw_size[0]), int(raw_size[1]))
        layer = overlay(spec, size)
        data = io.BytesIO()
        layer.save(data, format="PNG")
        state.figure_overlay_data = (
            "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()
        )
        state.figure_saved_json = json.dumps(spec.to_dict(), indent=2)
        if push:
            helpers._push_view(viewer.plotter)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        viewer.server.state.figure_status = str(exc)
    finally:
        viewer._figure_busy = False


def decoration_colour(viewer: Any) -> str:
    """The colour a new label, overlay or key is stored with.

    ``auto`` (the default) contrasts with the current background: the old
    default black was nearly invisible on the viewer's dark background
    (#138). The concrete colour is stored, so a saved scene keeps it.
    """
    import pyvista as pv

    chosen = str(viewer.server.state.figure_colour or "").strip()
    if chosen.lower() not in ("", "auto"):
        return chosen
    red, green, blue = pv.Color(viewer.plotter.background_color).float_rgb
    return "white" if 0.2126 * red + 0.7152 * green + 0.0722 * blue < 0.5 else "black"


def property_values(viewer: Any, name: str) -> np.ndarray:
    structure = viewer.reader.read_structure()
    if name in ("b_factor", "residue_seq", "atomic_number"):
        values = [getattr(atom, name) for atom in structure.atoms]
        if any(v is None for v in values):
            raise ValueError("Attribute is missing on one or more atoms")
        return np.asarray(values, dtype=float)
    if name == "custom":
        values = json.loads(str(viewer.server.state.figure_custom_values))
        return np.asarray(values, dtype=float)
    sid, member = name.split("/", 1)
    return np.asarray(viewer.reader._read_binary_member(sid, member), dtype=float)


def bind(viewer: Any) -> None:
    from vibeview.app import helpers

    state, ctrl = viewer.server.state, viewer.ctrl
    for key, value in {
        "figure_scene": FigureScene().to_dict(),
        "figure_overlay_data": "",
        "figure_viewport": [900, 600],
        "figure_export_format": "",
        "figure_status": "",
        "figure_saved_json": "",
        "figure_precision": 2,
        "figure_colour": "auto",
        "figure_label_scale": 0.3,
        "figure_annotation_kind": "text",
        "figure_text": "Label",
        "figure_x": 0.1,
        "figure_y": 0.1,
        "figure_x2": 0.5,
        "figure_y2": 0.5,
        "figure_font_size": 20,
        "figure_key_title": "Property",
        "figure_palette": "viridis",
        "figure_min": 0.0,
        "figure_max": 1.0,
        "figure_attribute": "b_factor",
        "figure_attribute_options": [],
        "figure_custom_values": "[]",
        "figure_radius_enabled": False,
        "figure_radius_min": 0.15,
        "figure_radius_max": 0.65,
        "figure_morph_running": False,
        "figure_morph_target": 0,
        "figure_morph_frames": 60,
        "figure_morph_fps": 20,
        "figure_morph_mapping": "",
        "figure_morph_mode": "linear",
        "figure_morph_format": "gif",
        "figure_morph_targets": [],
    }.items():
        state.setdefault(key, value)

    def guarded(fn: Any, *args: Any) -> None:
        try:
            fn(*args)
            render(viewer, push=True)
        except (ValueError, TypeError, KeyError, IndexError, RuntimeError) as exc:
            state.figure_status = str(exc)

    def save(spec: FigureScene) -> None:
        state.figure_scene = FigureScene.from_dict(spec.to_dict()).to_dict()
        state.session_dirty = True

    def measurement() -> None:
        spec = scene(viewer)
        if state.model_scene_enabled:
            refs = sorted(viewer._model_scene_commands.scene.selected)
        else:
            refs = [
                ("current", int(i)) for i in (state.edit_selected or state.selected_atoms or [])
            ]
        spec.measurements.append(
            {
                "refs": [list(r) for r in refs],
                "precision": int(state.figure_precision),
                "colour": decoration_colour(viewer),
                "scale": float(state.figure_label_scale),
            }
        )
        save(spec)

    def annotation() -> None:
        spec = scene(viewer)
        spec.annotations.append(
            {
                "kind": str(state.figure_annotation_kind),
                "start": [float(state.figure_x), float(state.figure_y)],
                "end": [float(state.figure_x2), float(state.figure_y2)],
                "text": str(state.figure_text),
                "colour": decoration_colour(viewer),
                "font_size": float(state.figure_font_size),
            }
        )
        save(spec)

    def add_key() -> None:
        spec = scene(viewer)
        spec.keys.append(
            {
                "title": str(state.figure_key_title),
                "range": [float(state.figure_min), float(state.figure_max)],
                "palette": str(state.figure_palette),
                "start": [float(state.figure_x), float(state.figure_y)],
                "end": [float(state.figure_x2), float(state.figure_y2)],
                "colour": decoration_colour(viewer),
            }
        )
        save(spec)

    def attribute() -> None:
        spec = scene(viewer)
        spec.attribute = {
            "name": str(state.figure_attribute),
            "values": property_values(viewer, str(state.figure_attribute)).tolist(),
            "range": [float(state.figure_min), float(state.figure_max)],
            "palette": str(state.figure_palette),
            "radius_enabled": bool(state.figure_radius_enabled),
            "radii": [float(state.figure_radius_min), float(state.figure_radius_max)],
        }
        mapped_attribute(spec.attribute, len(viewer.reader.read_structure().atoms))
        save(spec)

    def restore() -> None:
        save(FigureScene.from_dict(json.loads(str(state.figure_saved_json))))

    def clear() -> None:
        save(FigureScene())
        viewer.display.activate_section(state.selected_section)

    def viewport(width: Any, height: Any) -> None:
        values = [int(width), int(height)]
        if min(values) > 0 and max(values) <= 8192:
            state.figure_viewport = values
            render(viewer)

    def refresh_options(**_: Any) -> None:
        options = ["b_factor", "residue_seq", "atomic_number", "custom"]
        options += [
            f"{s.id}/{member}"
            for s in viewer.reader.sections
            if s.kind == "atom_properties"
            for member in s.members
            if member != "metadata"
        ]
        state.figure_attribute_options = options
        state.figure_morph_targets = [
            {"title": f"Open structure {i + 1}", "value": i}
            for i in range(len(viewer._all_readers))
        ]

    def export(fmt: Any) -> None:
        state.figure_export_format = str(fmt)
        viewer.exports.save_screenshot()

    def morph() -> None:
        from vibeview.morph_movie import render_morph

        end = viewer._all_readers[int(state.figure_morph_target)].read_structure()
        mapping = [
            int(v) - 1 for v in str(state.figure_morph_mapping).replace(",", " ").split()
        ] or None
        path = None
        if state.figure_morph_mode == "path":
            section = next((s for s in viewer.reader.sections if s.kind == "trajectory"), None)
            if section is None:
                raise ValueError("Path interpolation needs a trajectory in the current file")
            path = viewer.reader.read_trajectory(section.id).coords
        if state.figure_morph_running:
            raise ValueError("A morph export is already running")
        from vibeview.geometry_edit import geometry_archive
        from vibeview.qvf import QVFReader

        # Freeze input and view state before dispatch, so switching files while
        # encoding cannot change the movie or close its input archive.
        source = copy.deepcopy(viewer.reader.read_structure())
        source.bonds = viewer.reader.connectivity(source)
        archive = geometry_archive(source, provenance=viewer.reader.provenance)
        end = copy.deepcopy(end)
        path = None if path is None else np.array(path, copy=True)
        fmt = str(state.figure_morph_format)
        count, fps = int(state.figure_morph_frames), float(state.figure_morph_fps)
        width, height = state.figure_viewport
        decorations, camera = scene(viewer), copy.deepcopy(viewer.plotter.camera_position)

        def worker() -> tuple[bytes, str]:
            with (
                tempfile.TemporaryDirectory() as temp,
                QVFReader(io.BytesIO(archive)) as source_reader,
            ):
                output = render_morph(
                    source_reader,
                    end,
                    Path(temp) / ("morph." + fmt),
                    count=count,
                    mapping=mapping,
                    path=path,
                    fps=fps,
                    size=(int(width), int(height)),
                    format=fmt,
                    scene=decorations,
                    camera=camera,
                )
                return output.read_bytes(), output.suffix

        async def finish() -> None:
            try:
                payload, suffix = await asyncio.to_thread(worker)
                viewer.exports._offer_download(
                    payload,
                    "image/gif" if suffix == ".gif" else "video/mp4",
                    "morph" + suffix,
                    label="Morph movie",
                    fmt=suffix[1:].upper(),
                )
                state.figure_status = (
                    "Morph exported; geometric interpolation, not a calculated reaction path"
                )
            except Exception as exc:
                state.figure_status = f"Morph export failed: {exc}"
            finally:
                state.figure_morph_running = False
                state.flush()

        state.figure_morph_running = True
        helpers._spawn_task(finish())

    for name, fn in [
        ("measurement", measurement),
        ("annotation", annotation),
        ("key", add_key),
        ("attribute", attribute),
        ("restore", restore),
        ("clear", clear),
        ("morph", morph),
    ]:
        ctrl.set("figure_" + name)(lambda fn=fn: guarded(fn))
    ctrl.set("figure_export")(lambda fmt="png": guarded(export, fmt))
    ctrl.trigger("figure_viewport_size")(viewport)
    previous = helpers._scene_hooks(viewer.plotter).geometry_changed

    def refresh_after_rebuild() -> None:
        if previous is not None:
            previous()
        render(viewer)

    helpers._scene_hooks(viewer.plotter).geometry_changed = refresh_after_rebuild
    state.change("active_file_index", "selected_section")(refresh_options)
    refresh_options()
    render(viewer)


def build_controls(v: Any, html: Any, ctrl: Any) -> None:
    with v.VCard(classes="pa-2 mb-2"):
        v.VCardTitle("Publication figure")
        html.Div("{{ figure_status }}", classes="text-caption")
        v.VTextField(
            v_model=("figure_colour",),
            label="Annotation / measurement colour (auto: contrast with background)",
        )
        v.VTextField(
            v_model=("figure_precision",), type="number", label="Measurement decimals (0–8)"
        )
        v.VTextField(v_model=("figure_label_scale",), type="number", label="3D label scale (Å)")
        v.VBtn("Label selected 2 / 3 / 4 atoms", click=ctrl.figure_measurement, size="small")
        v.VSelect(
            v_model=("figure_annotation_kind",),
            items=("figure_annotation_kind_options", ["text", "arrow", "box"]),
            label="Overlay",
        )
        v.VTextField(v_model=("figure_text",), label="Overlay text")
        v.VTextField(
            v_model=("figure_font_size",), type="number", label="Font size (viewport pixels)"
        )
        html.Div(
            "Overlay/key coordinates: fractions of image, top-left origin.", classes="text-caption"
        )
        for field in ("x", "y", "x2", "y2"):
            v.VTextField(v_model=("figure_" + field,), type="number", label=field + " (0–1)")
        v.VBtn("Add overlay", click=ctrl.figure_annotation, size="small")
        v.VTextField(v_model=("figure_palette",), label="Matplotlib palette")
        v.VTextField(v_model=("figure_min",), type="number", label="Property minimum")
        v.VTextField(v_model=("figure_max",), type="number", label="Property maximum")
        v.VTextField(v_model=("figure_key_title",), label="Colour key title / units")
        v.VBtn("Add colour key", click=ctrl.figure_key, size="small")
        v.VSelect(
            v_model=("figure_attribute",),
            items=("figure_attribute_options",),
            label="Per-atom property",
        )
        v.VTextarea(
            v_model=("figure_custom_values",), label="Custom per-atom values (JSON)", rows=2
        )
        v.VCheckbox(v_model=("figure_radius_enabled",), label="Map display radius too")
        for field in ("min", "max"):
            v.VTextField(
                v_model=("figure_radius_" + field,), type="number", label="Radius " + field + " (Å)"
            )
        v.VBtn("Apply attribute", click=ctrl.figure_attribute, size="small")
        for fmt in ("png", "pdf", "svg"):
            v.VBtn(fmt.upper(), click=(ctrl.figure_export, "['" + fmt + "']"), size="small")
        v.VTextarea(v_model=("figure_saved_json",), label="Editable figure scene JSON", rows=3)
        v.VBtn("Apply scene JSON", click=ctrl.figure_restore, size="small")
        v.VBtn("Clear decorations", click=ctrl.figure_clear, size="small")
        v.VSelect(
            v_model=("figure_morph_target",), items=("figure_morph_targets",), label="Morph target"
        )
        v.VTextField(
            v_model=("figure_morph_mapping",),
            label="Target atom order (1-based; blank = same order)",
        )
        v.VSelect(
            v_model=("figure_morph_mode",),
            items=("figure_morph_mode_options", ["linear", "path"]),
            label="Interpolation",
        )
        v.VTextField(
            v_model=("figure_morph_frames",), type="number", label="Exact frame count (2–2000)"
        )
        v.VTextField(v_model=("figure_morph_fps",), type="number", label="Frames / second")
        v.VSelect(
            v_model=("figure_morph_format",),
            items=("figure_morph_format_options", ["gif", "mp4"]),
            label="Movie format",
        )
        html.Div("Geometry interpolation; not a calculated reaction path.", classes="text-caption")
        v.VBtn(
            "Export morph",
            click=ctrl.figure_morph,
            size="small",
            disabled=("figure_morph_running",),
        )
