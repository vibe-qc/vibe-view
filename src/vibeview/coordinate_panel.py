"""Cartesian/internal coordinate editors sharing the geometry edit transaction."""
from __future__ import annotations

import copy
from typing import Any

import numpy as np

from vibeview.coordinate_tables import parse_rows, table_rows, xyz_rows
from vibeview.units import length_label
from vibeview.zmatrix import from_cartesian, parse_zmatrix


def refresh(viewer: Any, **_: Any) -> None:
    state = viewer.server.state
    try:
        state.coord_rows = table_rows(viewer.reader.read_structure(), state.measure_unit,
                                      list(state.frozen_atoms or []))
        state.coord_unit = state.measure_unit
        state.coord_unit_label = length_label(state.measure_unit)
    except Exception:
        state.coord_rows = []


def apply_rows(viewer: Any, rows: list[dict[str, Any]], *, replace: bool = False) -> None:
    from vibeview.app import helpers

    state = viewer.server.state
    symbols, positions, frozen = parse_rows(rows, str(state.coord_unit))
    structure = viewer.reader.read_structure()
    viewer.editing._disable_live_opt(clear_status=True)
    viewer.editing._record_undo(structure)
    helpers._rebuild_structure_from_positions(
        viewer.reader, viewer.plotter, positions.tolist(), symbols,
        bonds=None if replace else "preserve",
    )
    state.frozen_atoms = frozen
    state.edit_selected = []
    state.selected_atoms = []
    helpers._update_edit_highlights(viewer.reader, viewer.plotter, [])
    helpers._update_frozen_highlights(viewer.reader, viewer.plotter, frozen)
    refresh(viewer)
    state.status_message = "Coordinates applied as one undo step; auto-optimize stopped"
    helpers._push_view(viewer.plotter)


def paste_xyz(viewer: Any, append: bool) -> None:
    state = viewer.server.state
    rows = xyz_rows(str(state.coord_xyz), str(state.measure_unit))
    if append:
        rows = table_rows(viewer.reader.read_structure(), state.measure_unit,
                          list(state.frozen_atoms or [])) + rows
    state.coord_unit = state.measure_unit
    apply_rows(viewer, rows, replace=not append)


def generate_zmatrix(viewer: Any) -> None:
    from vibeview.bonds import structure_bonds

    structure = viewer.reader.read_structure()
    if any(structure.pbc):
        raise ValueError("Z-matrix editing requires a molecular structure")
    matrix = from_cartesian(
        [a.symbol for a in structure.atoms], np.array([a.position for a in structure.atoms]),
        [(b[0], b[1]) for b in structure_bonds(structure)],
    )
    state = viewer.server.state
    state.coord_zstyle = "gaussian"
    state.coord_ztext = matrix.text(variables=True)
    load_ztable(viewer)
    state.coord_znotice = "Distances are angstrom; angles are degrees. X rows are dummy anchors."


def load_ztable(viewer: Any) -> None:
    """Validate pasted coordinate block, then expose atom rows and variables separately."""
    state = viewer.server.state
    parse_zmatrix(str(state.coord_ztext), state.coord_zstyle)
    rows, definitions = [], []
    for line in str(state.coord_ztext).splitlines():
        stripped = line.split("!")[0].strip()
        if not stripped or stripped.lower() in ("variables:", "constants:"):
            continue
        import re

        if "=" in stripped or re.fullmatch(
            r"[A-Za-z]\w*\s+[+-]?[\d.]+(?:[eEdD][+-]?\d+)?", stripped
        ):
            definitions.append(stripped)
        else:
            fields = stripped.replace(",", " ").split()
            rows.append({f"c{i}": fields[i] if i < len(fields) else "" for i in range(10)})
    state.coord_zrows = rows
    state.coord_zdefinitions = "\n".join(definitions)


def apply_ztable(
    viewer: Any, rows: list[dict[str, Any]] | None = None, definitions: str | None = None
) -> None:
    state = viewer.server.state
    text = "\n".join(
        " ".join(str(row[f"c{i}"]).strip() for i in range(10)).rstrip()
        for row in (state.coord_zrows if rows is None else rows)
    ) + "\n\n" + str(state.coord_zdefinitions if definitions is None else definitions)
    apply_zmatrix(viewer, text)


def apply_zmatrix(viewer: Any, text: str | None = None) -> None:
    state = viewer.server.state
    structure = viewer.reader.read_structure()
    if any(structure.pbc):
        raise ValueError("Z-matrix editing requires a molecular structure")
    text = str(state.coord_ztext) if text is None else text
    symbols, positions = parse_zmatrix(text, state.coord_zstyle).cartesian()
    if symbols != [a.symbol for a in structure.atoms]:
        raise ValueError("Z-matrix edits must retain the ordered real atoms; use XYZ to replace")
    # Preserve the current molecular orientation as far as the changed geometry permits.
    from vibeview.align import kabsch

    original = np.array([a.position for a in structure.atoms])
    rotation, translation = kabsch(positions, original)
    positions = positions @ rotation.T + translation
    frozen = list(state.frozen_atoms or [])
    if frozen and not np.allclose(positions[frozen], original[frozen], atol=1e-8, rtol=0):
        raise ValueError("Z-matrix edit moves frozen atoms; unfreeze them explicitly first")
    rows = [{"element": s, "x": p[0], "y": p[1], "z": p[2], "frozen": i in frozen}
            for i, (s, p) in enumerate(zip(symbols, positions, strict=True))]
    state.coord_unit = "angstrom"
    apply_rows(viewer, rows)
    state.coord_ztext = text


def export_zmatrix(viewer: Any) -> None:
    state = viewer.server.state
    matrix = parse_zmatrix(str(state.coord_ztext), state.coord_zstyle)
    state.coord_zexport = matrix.text(state.coord_export_style,
                                      variables=state.coord_export_style == "gaussian")


def bind(viewer: Any) -> None:
    from vibeview.app import helpers

    state, ctrl = viewer.server.state, viewer.ctrl
    state.setdefault("coord_rows", [])
    for key, value in {"coord_unit": "angstrom", "coord_unit_label": "Å", "coord_xyz": "",
                       "coord_ztext": "", "coord_zstyle": "gaussian", "coord_zexport": "",
                       "coord_export_style": "gaussian", "coord_znotice": "",
                       "coord_zrows": [], "coord_zdefinitions": ""}.items():
        state.setdefault(key, value)

    def guarded(fn: Any, *args: Any) -> None:
        try:
            fn(viewer, *args)
        except (ValueError, TypeError, KeyError) as exc:
            state.status_message = str(exc)

    ctrl.set("coord_refresh")(lambda: refresh(viewer))
    ctrl.set("coord_apply")(lambda rows=None: guarded(
        apply_rows, copy.deepcopy(state.coord_rows if rows is None else rows)))
    ctrl.set("coord_paste")(lambda append=False: guarded(paste_xyz, bool(append)))
    ctrl.set("coord_generate_z")(lambda: guarded(generate_zmatrix))
    ctrl.set("coord_load_ztable")(lambda: guarded(load_ztable))
    ctrl.set("coord_apply_ztable")(lambda rows=None, definitions=None: guarded(
        apply_ztable, rows, definitions))
    ctrl.set("coord_apply_z")(lambda: guarded(apply_zmatrix))
    ctrl.set("coord_export_z")(lambda: guarded(export_zmatrix))
    state.change("measure_unit", "selected_section", "active_file_index", "frozen_atoms")(
        lambda **kw: refresh(viewer))
    helpers._scene_hooks(viewer.plotter).geometry_changed = lambda: refresh(viewer)
    refresh(viewer)


def build_controls(v: Any, html: Any, ctrl: Any) -> None:
    with v.VCard(classes="pa-2 mb-2"):
        v.VCardTitle("Coordinate editor")
        html.Div("Cartesian lengths: {{ coord_unit_label }} (Length unit setting)",
                 classes="text-caption")
        v.VBtn("Reload geometry", click=ctrl.coord_refresh, size="small")
        with html.Div(style="max-height:300px;overflow:auto"), html.Table():
            with html.Tr():
                for label in ("Element", "x", "y", "z", "Frozen"):
                    html.Th(label)
            with html.Tr(v_for="(row, i) in coord_rows", key="i"):
                for field in ("element", "x", "y", "z"):
                    with html.Td():
                        html.Input(v_model=(f"row.{field}",), style="width:70px",
                                   aria_label=(f"'{field} atom ' + (i+1)",))
                with html.Td():
                    html.Input(type="checkbox", v_model=("row.frozen",),
                               aria_label=("'Freeze atom ' + (i+1)",))
        v.VBtn("Apply table", click=(ctrl.coord_apply, "[coord_rows]"), size="small")
        v.VTextarea(v_model=("coord_xyz",), label="Paste one XYZ frame (current length unit)",
                    rows=4)
        v.VBtn("Replace XYZ", click=(ctrl.coord_paste, "[false]"), size="small")
        v.VBtn("Append XYZ", click=(ctrl.coord_paste, "[true]"), size="small")
        v.VBtn("Generate Z-matrix", click=ctrl.coord_generate_z, size="small")
        v.VSelect(v_model=("coord_zstyle",),
                  items=("['gaussian', 'orca', 'mopac']",), label="Z-matrix input style")
        v.VTextarea(v_model=("coord_ztext",), label="Editable Z-matrix rows and variables (Å/°)",
                    rows=8)
        html.Div("{{ coord_znotice }}", classes="text-caption")
        v.VBtn("Load rows from block", click=ctrl.coord_load_ztable, size="small")
        v.VBtn("Apply pasted block", click=ctrl.coord_apply_z, size="small")
        html.Div("Gaussian: element, ref, R, ref, angle, ref, dihedral; "
                 "ORCA: element, 3 refs, R, angle, dihedral; "
                 "MOPAC: element, R, flag, angle, flag, dihedral, flag, 3 refs.",
                 classes="text-caption")
        with (html.Div(style="overflow:auto"), html.Table(),
              html.Tr(v_for="(row, i) in coord_zrows", key="i")):
            for column in range(10):
                with html.Td():
                    html.Input(v_model=(f"row.c{column}",), style="width:65px",
                               aria_label=(f"'Z column {column+1} row ' + (i+1)",))
        v.VTextarea(v_model=("coord_zdefinitions",), label="Numeric variable definitions", rows=3)
        v.VBtn("Apply edited Z-matrix table",
               click=(ctrl.coord_apply_ztable, "[coord_zrows, coord_zdefinitions]"), size="small")
        v.VSelect(v_model=("coord_export_style",),
                  items=("['gaussian', 'orca', 'mopac']",), label="Export style")
        v.VBtn("Export coordinate block", click=ctrl.coord_export_z, size="small")
        v.VTextarea(v_model=("coord_zexport",), label="Copy exported Z-matrix", readonly=True,
                    rows=6)
