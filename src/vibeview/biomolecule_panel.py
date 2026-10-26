"""Explicit database actions, model/conformer chooser and linked sequence panel."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

from vibeview.biomolecule import residues
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder


def refresh(viewer: Any, **_: Any) -> None:
    state = viewer.server.state
    try:
        rows = residues(viewer.reader)
        selected = set(state.selected_atoms or []) | set(state.edit_selected or [])
        state.bio_rows = [
            {**row, "index": i, "selected": bool(selected.intersection(row["atom_indices"]))}
            for i, row in enumerate(rows)
        ]
        bio = viewer.reader.provenance.get("biomolecule", {})
        state.bio_variants = [
            {"title": value["label"], "value": key}
            for key, value in bio.get("variants", {}).items()
        ]
        state.bio_variant = viewer.reader.shown_structure_id
        variant = bio.get("variants", {}).get(viewer.reader.shown_structure_id, {})
        fallback = len(variant.get("fallback_record_indices", []))
        state.bio_notice = "; ".join(bio.get("warnings", []))
        if fallback:
            state.bio_notice += (
                f" {fallback} atoms lack this alt label; their first location is shown."
            )
        state.bio_reference_options = [
            {"title": f"Open structure {i + 1}", "value": i}
            for i in range(len(viewer._all_readers))
        ]
    except Exception as exc:
        state.bio_rows = []
        state.bio_notice = f"Sequence unavailable: {exc}"


def select_residue(viewer: Any, index: Any) -> None:
    from vibeview.app import helpers

    rows = residues(viewer.reader)
    idx = int(index)
    if idx < 0 or idx >= len(rows):
        raise ValueError("Residue index is no longer valid; refresh sequence")
    indices = rows[idx]["atom_indices"]
    state = viewer.server.state
    state.selected_atoms = indices
    state.edit_selected = indices
    helpers._update_edit_highlights(viewer.reader, viewer.plotter, indices)
    helpers._push_view(viewer.plotter)
    refresh(viewer)


def select_group(viewer: Any, kind: str) -> None:
    from vibeview.app import helpers

    if kind not in ("water", "ligand"):
        raise ValueError("Select water or ligand")
    indices = [
        i for row in residues(viewer.reader) if row["kind"] == kind for i in row["atom_indices"]
    ]
    viewer.server.state.selected_atoms = indices
    viewer.server.state.edit_selected = indices
    helpers._update_edit_highlights(viewer.reader, viewer.plotter, indices)
    helpers._push_view(viewer.plotter)
    refresh(viewer)


def align_copy(viewer: Any) -> None:
    """Open a transformed copy; the original structures and frames remain intact."""
    from vibeview.sequence_alignment import superpose

    state = viewer.server.state
    reference = viewer._all_readers[int(state.bio_reference_file)]
    reader = viewer.reader
    result = superpose(
        reader, reference, str(state.bio_mobile_chain), str(state.bio_reference_chain)
    )
    sid = reader.shown_structure_id
    if sid is None:
        raise ValueError("No structure to align")
    if reader.edited_structure_ids:
        raise ValueError(
            "Save/reopen edited geometry before sequence fitting; atom metadata may be stale"
        )
    payload = copy.deepcopy(reader.read_json_member(sid, "structure"))
    for atom, position in zip(payload["atoms"], result["transformed"], strict=True):
        atom["position"] = position
    # Cartesian fitting of crystallographic molecules is not a periodic-cell fit.
    payload["pbc"] = [False] * 3
    payload["dimensionality"] = 0
    payload.pop("lattice_vectors", None)
    builder = QVFArchiveBuilder(calculation="sequence-aligned molecular copy")
    builder.add_json(
        "structure",
        "structure",
        "structure",
        payload,
        section_extra=copy.deepcopy(reader.get_section(sid).model_extra or {}),
    )
    provenance = copy.deepcopy(reader.provenance)
    if "biomolecule" in provenance:
        bio = provenance["biomolecule"]
        bio["variants"] = {"structure": bio["variants"][sid]}
    provenance["sequence_superposition"] = {k: v for k, v in result.items() if k != "transformed"}
    builder.set_provenance(provenance)
    viewer.files._adopt_reader(QVFReader(builder.to_bytesio()))
    state.bio_alignment = json.dumps(provenance["sequence_superposition"], indent=2)
    state.status_message = (
        f"Sequence fit: {len(result['aligned_pairs'])} CA pairs, "
        f"RMSD {result['rmsd_angstrom']:.6f} Å; opened molecular copy"
    )
    refresh(viewer)


def bind(viewer: Any) -> None:
    state = viewer.server.state
    state.update(
        {
            "bio_provider": "RCSB",
            "bio_identifier": "",
            "bio_fetching": False,
            "bio_rows": [],
            "bio_variants": [],
            "bio_variant": "",
            "bio_notice": "",
            "bio_reference_file": 0,
            "bio_reference_options": [],
            "bio_mobile_chain": "A",
            "bio_reference_chain": "A",
            "bio_alignment": "",
        }
    )

    async def fetch() -> None:
        if state.bio_fetching:
            return
        from vibeview.database_fetch import fetch_structure

        provider, identifier = str(state.bio_provider), str(state.bio_identifier)
        state.bio_fetching = True
        try:
            stream = await asyncio.to_thread(fetch_structure, provider, identifier)
            viewer.files._adopt_reader(QVFReader(stream))
            state.status_message = f"Opened {provider} {identifier}"
            refresh(viewer)
        except Exception as exc:
            state.status_message = f"Cannot fetch structure: {exc}"
        finally:
            state.bio_fetching = False

    def safe_align() -> None:
        try:
            align_copy(viewer)
        except Exception as exc:
            state.status_message = f"Cannot align chains: {exc}"

    def choose(section_id: str) -> None:
        viewer.display.activate_section(section_id)
        refresh(viewer)

    viewer.ctrl.set("bio_fetch")(fetch)
    viewer.ctrl.set("bio_refresh")(lambda: refresh(viewer))
    viewer.ctrl.set("bio_select_residue")(lambda index: select_residue(viewer, index))
    viewer.ctrl.set("bio_choose_variant")(choose)
    viewer.ctrl.set("bio_align")(safe_align)
    viewer.ctrl.set("bio_select_group")(lambda kind: select_group(viewer, kind))
    viewer.lifecycle._watch_state(
        "selected_section", "active_file_idx", "selected_atoms", "edit_selected"
    )(lambda **kwargs: refresh(viewer, **kwargs))
    refresh(viewer)


def build_controls(v: Any, html: Any, ctrl: Any) -> None:
    with v.VCard(classes="pa-2 mb-2"):
        v.VCardTitle("Database & sequence")
        v.VSelect(
            v_model=("bio_provider",),
            items=("['RCSB', 'AlphaFold', 'PubChem CID', 'PubChem name']",),
            label="Public database",
            density="compact",
        )
        v.VTextField(
            v_model=("bio_identifier",),
            label="Public identifier or chemical name",
            density="compact",
        )
        html.Div(
            "Only Fetch sends this value to the selected public database. No local "
            "structure is uploaded.",
            classes="text-caption",
        )
        v.VBtn(
            "Fetch and open",
            click=ctrl.bio_fetch,
            loading=("bio_fetching",),
            disabled=("bio_fetching",),
            size="small",
        )
        v.VBtn("Refresh sequence", click=ctrl.bio_refresh, size="small")
        v.VBtn("Select waters", click=(ctrl.bio_select_group, "['water']"), size="small")
        v.VBtn("Select ligands", click=(ctrl.bio_select_group, "['ligand']"), size="small")
        v.VSelect(
            v_if=("bio_variants.length > 0",),
            v_model=("bio_variant",),
            items=("bio_variants",),
            item_title="title",
            item_value="value",
            label="Model / alternate location",
            update_modelValue=(ctrl.bio_choose_variant, "[$event]"),
            density="compact",
        )
        html.Div("{{ bio_notice }}", classes="text-caption")
        html.Div(
            "Observed residues: red helix, blue sheet, grey coil/unknown; outlined selection links "
            "to atoms.",
            classes="text-caption",
        )
        with html.Div(style="max-height: 220px; overflow: auto"):
            v.VChip(
                "{{ row.letter }} · {{ row.label }} ({{ row.kind }})",
                v_for="row in bio_rows",
                key=("row.index",),
                color=("row.secondary === 'H' ? 'red' : row.secondary === 'E' ? 'blue' : 'grey'",),
                variant=("row.selected ? 'outlined' : 'tonal'",),
                size="small",
                click=(ctrl.bio_select_residue, "[row.index]"),
            )
        v.VSelect(
            v_model=("bio_reference_file",),
            items=("bio_reference_options",),
            item_title="title",
            item_value="value",
            label="Reference structure",
            density="compact",
        )
        v.VTextField(
            v_model=("bio_mobile_chain",), label="Current chain to align", density="compact"
        )
        v.VTextField(v_model=("bio_reference_chain",), label="Reference chain", density="compact")
        v.VBtn("Align chains → molecular copy", click=ctrl.bio_align, size="small")
        v.VTextarea(
            v_if=("bio_alignment",),
            v_model=("bio_alignment",),
            readonly=True,
            label="Matched CA pairs and RMSD (Å)",
            rows=8,
        )
