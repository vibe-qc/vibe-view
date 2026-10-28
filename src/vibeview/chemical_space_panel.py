"""Asynchronous chemical-space display of producer-owned fingerprints."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from vibeview.chemical_space import ChemicalSpace


async def load(viewer: Any) -> None:
    state = viewer.server.state
    if state.chemspace_busy:
        return
    state.chemspace_busy = True
    state.chemspace_notice = "Reading producer fingerprints and projecting PCA..."
    state.flush()
    path, variant = str(state.chemspace_path), str(state.chemspace_variant)
    try:

        def prepare() -> tuple[ChemicalSpace, str]:
            source = Path(path).expanduser()
            if source.stat().st_size > 256 * 1024 * 1024:
                raise ValueError("Collection exceeds 256 MiB")
            space = ChemicalSpace.load(json.loads(source.read_text()), variant)
            return space, space.image()

        space, image = await asyncio.to_thread(prepare)
        viewer._chemical_space = space
        state.chemspace_image = image
        state.chemspace_count = len(space.records)
        state.chemspace_selected = 0
        state.chemspace_other = 1
        state.chemspace_colours = ["group"] + sorted(
            {k for r in space.records for k in r.get("properties", {})}
        )
        state.chemspace_colour = "group"
        state.chemspace_parameters = json.dumps(space.parameters, sort_keys=True)
        state.chemspace_notice = (
            f"{len(space.records)} structures; {space.variant}; PCA axes 1 and 2"
        )
        state.chemspace_legend = "Groups: " + ", ".join(
            sorted({str(r.get("group", "")) for r in space.records})
        )
        update_pair(viewer)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        state.chemspace_notice = f"Chemical space: {exc}"
    finally:
        state.chemspace_busy = False
        state.flush()


def update_pair(viewer: Any) -> None:
    state = viewer.server.state
    space = getattr(viewer, "_chemical_space", None)
    if space is None:
        return
    try:
        a, b = int(state.chemspace_selected), int(state.chemspace_other)
        state.chemspace_pair = [
            {"channel": k, "similarity": f"{v:.12g}"} for k, v in space.pair(a, b).items()
        ]
        state.chemspace_neighbours = [
            {"index": i, "id": space.records[i]["id"]}
            for i in space.neighbours(a, int(state.chemspace_k))
        ]
        state.chemspace_label = space.records[a]["id"]
    except (ValueError, IndexError, TypeError) as exc:
        state.chemspace_notice = str(exc)


async def recolour(viewer: Any) -> None:
    state = viewer.server.state
    space = getattr(viewer, "_chemical_space", None)
    if space is None or state.chemspace_busy:
        return
    state.chemspace_busy = True
    try:
        colour, selected = str(state.chemspace_colour), int(state.chemspace_selected)
        state.chemspace_image = await asyncio.to_thread(space.image, colour, selected)
        state.chemspace_legend = (
            "Groups: " + ", ".join(sorted({str(r.get("group", "")) for r in space.records}))
            if colour == "group"
            else f"{colour}: blue minimum to red maximum; missing values grey"
        )
    except (ValueError, TypeError) as exc:
        state.chemspace_notice = str(exc)
    finally:
        state.chemspace_busy = False
        state.flush()


def select(viewer: Any, index: Any) -> None:
    space = getattr(viewer, "_chemical_space", None)
    if space is None:
        return
    index = int(index)
    space._index(index)
    record = space.records[index]
    state = viewer.server.state
    state.chemspace_selected = index
    # Use the normal in-memory file import path: do not edit the previous model.
    rows = [str(len(record["symbols"])), record["id"]]
    rows.extend(
        s + " " + " ".join(f"{float(v):.17g}" for v in xyz)
        for s, xyz in zip(record["symbols"], record["coordinates_angstrom"], strict=True)
    )
    raw = ("\n".join(rows) + "\n").encode()
    viewer.files.load_file_from_bytes(base64.b64encode(raw).decode(), f"chemical-space-{index}.xyz")
    update_pair(viewer)


def pick(viewer: Any, x: Any, y: Any) -> None:
    space = getattr(viewer, "_chemical_space", None)
    if space is not None:
        select(viewer, space.nearest_point(float(x) * 640, float(y) * 400))


def bind(viewer: Any) -> None:
    state, ctrl = viewer.server.state, viewer.ctrl
    defaults = {
        "path": "",
        "variant": "quantile-scale",
        "busy": False,
        "image": "",
        "notice": "",
        "count": 0,
        "selected": 0,
        "other": 1,
        "k": 10,
        "pair": [],
        "neighbours": [],
        "label": "",
        "colour": "group",
        "colours": ["group"],
        "parameters": "",
        "legend": "",
    }
    for key, value in defaults.items():
        state.setdefault("chemspace_" + key, value)

    async def load_current(path: str, variant: str) -> None:
        state.chemspace_path, state.chemspace_variant = path, variant
        await load(viewer)

    async def colour_current(colour: str) -> None:
        state.chemspace_colour = colour
        await recolour(viewer)

    ctrl.set("chemspace_load")(load_current)
    ctrl.set("chemspace_recolour")(colour_current)
    ctrl.set("chemspace_select")(lambda index: select(viewer, index))
    ctrl.set("chemspace_pick")(lambda x, y: pick(viewer, x, y))

    def compare_current(first: int, second: int, k: int) -> None:
        state.chemspace_selected, state.chemspace_other, state.chemspace_k = first, second, k
        update_pair(viewer)

    ctrl.set("chemspace_compare")(compare_current)


def build_controls(v: Any, html: Any, ctrl: Any) -> None:
    with (
        v.VExpansionPanels(classes="mb-2"),
        v.VExpansionPanel(title="Chemical space"),
        v.VExpansionPanelText(),
    ):
        html.Div(
            "Open QC-produced fingerprint collections, including trajectory frames. "
            "The viewer never recomputes fingerprints.",
            classes="text-caption",
        )
        v.VTextField(v_model=("chemspace_path",), label="Collection JSON path")
        v.VSelect(
            v_model=("chemspace_variant",),
            items=("['quantile-scale', 'toney2026']",),
            label="Producer variant",
        )
        v.VBtn(
            "Open chemical space",
            click=(ctrl.chemspace_load, "[chemspace_path, chemspace_variant]"),
            disabled=("chemspace_busy",),
            loading=("chemspace_busy",),
        )
        html.Div("{{ chemspace_notice }}", role="status")
        with html.Div(v_if=("chemspace_image",)):
            html.Img(
                src=("chemspace_image",),
                alt="Chemical-space PCA map: click a point",
                style="width:100%;max-width:640px;cursor:crosshair",
                click=(
                    ctrl.chemspace_pick,
                    "[$event.offsetX/$event.target.clientWidth, "
                    "$event.offsetY/$event.target.clientHeight]",
                ),
            )
            html.Div("{{ chemspace_legend }}", classes="text-caption")
            v.VSelect(
                v_model=("chemspace_colour",),
                items=("chemspace_colours",),
                label="Colour by property or group",
            )
            v.VBtn(
                "Apply colours",
                click=(ctrl.chemspace_recolour, "[chemspace_colour]"),
                disabled=("chemspace_busy",),
            )
            html.Div("{{ chemspace_parameters }}", classes="text-caption")
            v.VTextField(
                v_model=("chemspace_selected",),
                type="number",
                min=0,
                max=("chemspace_count-1",),
                label="Structure index (zero based)",
            )
            v.VBtn(
                "Open selected structure",
                click=(ctrl.chemspace_select, "[chemspace_selected]"),
            )
            html.Div("{{ chemspace_label }}")
            v.VTextField(
                v_model=("chemspace_other",),
                type="number",
                min=0,
                max=("chemspace_count-1",),
                label="Compare with index",
            )
            v.VTextField(
                v_model=("chemspace_k",),
                type="number",
                min=1,
                max=100,
                label="Nearest neighbours",
            )
            v.VBtn(
                "Compare and find neighbours",
                click=(
                    ctrl.chemspace_compare,
                    "[chemspace_selected, chemspace_other, chemspace_k]",
                ),
            )
            with html.Div(v_for="row in chemspace_pair", key=("row.channel",)):
                html.Span("{{ row.channel }}: {{ row.similarity }}")
            with html.Div(v_for="row in chemspace_neighbours", key=("row.index",)):
                v.VBtn(
                    "{{ row.id }}",
                    click=(ctrl.chemspace_select, "[row.index]"),
                    size="small",
                )
