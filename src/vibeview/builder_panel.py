"""Molecular builder UI: single-transaction geometry edits and retained ensembles."""

from __future__ import annotations

import asyncio
import io
from typing import Any

import numpy as np

from vibeview.coordination_builder import LIGANDS, TEMPLATES, coordinate
from vibeview.fragment_catalog import attach, fragment, search, thumbnail
from vibeview.molecular_builder import conformers, rotate_bond, structure_molecule
from vibeview.nucleic_builder import nucleic_acid
from vibeview.peptide_builder import peptide
from vibeview.qvf import QVFReader, StructureData
from vibeview.qvf_writer import QVFArchiveBuilder, structure_payload


def molecule_archive(mol: Any, label: str) -> io.BytesIO:
    coords = np.asarray(mol.GetConformer().GetPositions())
    atoms = []
    for atom, point in zip(mol.GetAtoms(), coords, strict=True):
        row = {
            "symbol": atom.GetSymbol(),
            "atomic_number": atom.GetAtomicNum(),
            "position": point.tolist(),
        }
        info = atom.GetPDBResidueInfo()
        if info is not None:
            row.update(
                atom_name=info.GetName().strip(),
                residue_name=info.GetResidueName().strip(),
                residue_seq=info.GetResidueNumber(),
                chain_id=info.GetChainId().strip() or "A",
            )
        atoms.append(row)
    builder = QVFArchiveBuilder(calculation=label)
    builder.add_json("structure", "structure", "structure", structure_payload(atoms, [False] * 3))
    builder.add_json(
        "structure",
        "structure",
        "bonds",
        {
            "pairs": [
                {
                    "i": b.GetBeginAtomIdx(),
                    "j": b.GetEndAtomIdx(),
                    "order": b.GetBondTypeAsDouble(),
                    "image": [0, 0, 0],
                }
                for b in mol.GetBonds()
            ]
        },
    )
    builder.set_provenance(
        {
            "builder": label,
            "charge": sum(a.GetFormalCharge() for a in mol.GetAtoms()),
            "builder_details": {
                key: mol.GetProp(key) for key in mol.GetPropNames() if key.startswith("builder_")
            },
            "atom_formal_charges": [a.GetFormalCharge() for a in mol.GetAtoms()],
            "warning": "Constructed starting geometry; not an optimized quantum result",
        }
    )
    # Open on the built structure, so the builder card stays in view (#140).
    builder.set_root("viewer_defaults", {"auto_open": ["structure"]})
    return builder.to_bytesio()


def selected(viewer: Any, count: int) -> list[int]:
    state = viewer.server.state
    ids = list(state.edit_selected or state.selected_atoms or [])
    if len(ids) != count:
        raise ValueError(f"Select exactly {count} atom(s)")
    return ids


def apply_structure(viewer: Any, structure: StructureData, *, topology: bool) -> None:
    from vibeview.app import helpers

    state = viewer.server.state
    if topology and state.frozen_atoms:
        raise ValueError("Unfreeze atoms before a topology-changing builder operation")
    viewer.editing._disable_live_opt(clear_status=True)
    viewer.editing._record_undo(viewer.reader.read_structure())
    helpers._rebuild_structure_from_positions(
        viewer.reader,
        viewer.plotter,
        [a.position.tolist() for a in structure.atoms],
        [a.symbol for a in structure.atoms],
        bonds=structure.bonds,
    )
    if topology:
        # New or removed atoms make the selected indices stale.
        state.edit_selected = []
        state.selected_atoms = []
    # A rotation keeps every atom, so the bond stays selected for the next
    # typed or dragged step (#131 acceptance: each step needed a reselect).
    helpers._update_edit_highlights(viewer.reader, viewer.plotter, list(state.edit_selected or []))
    helpers._push_view(viewer.plotter)


def rotate(viewer: Any, angle: Any = None) -> None:
    """Rotate the selected bond by ``angle`` (the slider sends its released
    value; the button leaves it to ``mb_angle``, typed well before the click)."""
    s = viewer.server.state
    original = viewer.reader.read_structure()
    result = rotate_bond(
        original,
        selected(viewer, 2),
        float(s.mb_angle if angle is None else angle),
        list(s.frozen_atoms or []),
    )
    from vibeview.qvf import Atom

    edited = StructureData(
        [Atom(a.symbol, p, a.atomic_number) for a, p in zip(original.atoms, result, strict=True)],
        original.pbc,
        original.lattice_vectors,
        original.bonds,
    )
    apply_structure(viewer, edited, topology=False)
    s.mb_angle = 0
    s.status_message = "Bond rotated as one undo step"


def refresh_catalog(viewer: Any, **_: Any) -> None:
    state = viewer.server.state
    try:
        state.mb_fragments = [
            {"name": name, "thumbnail": thumbnail(name)} for name in search(str(state.mb_search))
        ]
    except ValueError as exc:
        state.mb_fragments = []
        state.mb_notice = str(exc)


def bind(viewer: Any) -> None:
    state, ctrl = viewer.server.state, viewer.ctrl
    state.update(
        {
            "mb_angle": 0.0,
            "mb_template": "octahedral",
            "mb_ligand": "ammonia",
            "mb_distance": 2.1,
            "mb_replace_h": False,
            # Empty, not a sample: typed input appended to "AGSA" (#139).
            "mb_sequence": "",
            "mb_sequence_kind": "peptide",
            "mb_strands": "single",
            "mb_sequence_running": False,
            "mb_backbone": "helix",
            "mb_phi": -57.0,
            "mb_psi": -47.0,
            "mb_count": 10,
            "mb_seed": 42,
            "mb_search": "",
            "mb_fragments": [],
            "mb_fragment": "benzene / phenyl",
            "mb_notice": "",
        }
    )

    def guarded(action: Any, *args: Any) -> None:
        try:
            action(*args)
        except (ValueError, TypeError, KeyError, RuntimeError) as exc:
            state.status_message = str(exc)

    def build_sequence() -> None:
        if state.mb_sequence_running:
            raise ValueError("Sequence construction is already running")
        sequence = str(state.mb_sequence or "").strip()
        if not sequence:
            raise ValueError("Enter a one-letter sequence, for example AGSA or ACGT")
        if state.mb_sequence_kind in ("DNA", "RNA") and state.mb_strands == "duplex":
            from vibeview.app import helpers

            kind = str(state.mb_sequence_kind)
            state.mb_sequence_running = True
            state.status_message = "Constructing checked duplex geometry…"

            async def finish() -> None:
                try:
                    molecule = await asyncio.to_thread(
                        nucleic_acid, sequence, kind, strands="duplex"
                    )
                    viewer.files._adopt_reader(
                        QVFReader(molecule_archive(molecule, "duplex builder"))
                    )
                    state.status_message = "Duplex starting geometry opened as a new structure"
                except Exception as exc:
                    state.status_message = f"Duplex construction refused: {exc}"
                finally:
                    state.mb_sequence_running = False
                    state.flush()

            helpers._spawn_task(finish())
            return
        if state.mb_sequence_kind == "peptide":
            mol = peptide(
                sequence,
                str(state.mb_backbone),
                float(state.mb_phi),
                float(state.mb_psi),
                int(state.mb_seed),
            )
        else:
            mol = nucleic_acid(sequence, str(state.mb_sequence_kind), strands=str(state.mb_strands))
        viewer.files._adopt_reader(QVFReader(molecule_archive(mol, "sequence builder")))
        state.status_message = "Sequence built as a starting geometry; no auto-optimization started"

    def build_coordination() -> None:
        result = coordinate(
            viewer.reader.read_structure(),
            selected(viewer, 1)[0],
            str(state.mb_template),
            str(state.mb_ligand),
            float(state.mb_distance),
            bool(state.mb_replace_h),
        )
        apply_structure(viewer, result, topology=True)
        state.status_message = "Coordination template applied; check charge/spin before calculation"

    def generate_conformers() -> None:
        if viewer.reader.provenance.get("charge", 0) != 0:
            raise ValueError(
                "Current-structure conformers require neutral explicit valences; "
                "charged atoms are not yet typed"
            )
        result = conformers(
            structure_molecule(viewer.reader.read_structure()),
            int(state.mb_count),
            int(state.mb_seed),
        )
        viewer.files._adopt_reader(QVFReader(result.archive()))
        state.status_message = (
            f"{len(result.ids)} energy-ranked conformers; {sum(result.converged)} converged "
            f"({result.force_field}, kcal/mol). Select a structure or trajectory frame."
        )

    def use_fragment(name: str, append: bool) -> None:
        state.mb_fragment = name
        if append:
            apply_structure(
                viewer,
                attach(viewer.reader.read_structure(), selected(viewer, 1)[0], name),
                topology=True,
            )
        else:
            viewer.files._adopt_reader(
                QVFReader(molecule_archive(fragment(name), f"fragment: {name}"))
            )
        state.status_message = (
            "Fragment built; attachment replaces one explicit hydrogen on each anchor"
        )

    ctrl.set("mb_rotate")(lambda angle=None, *_: guarded(rotate, viewer, angle))
    ctrl.set("mb_coordinate")(lambda: guarded(build_coordination))
    ctrl.set("mb_sequence_build")(lambda: guarded(build_sequence))
    ctrl.set("mb_conformers")(lambda: guarded(generate_conformers))
    ctrl.set("mb_fragment_use")(
        lambda name, append=False: guarded(use_fragment, str(name), bool(append))
    )
    state.change("mb_search")(lambda **kw: refresh_catalog(viewer))
    refresh_catalog(viewer)


def build_controls(v: Any, html: Any, ctrl: Any) -> None:
    with v.VCard(classes="pa-2 mb-2"):
        v.VCardTitle("Molecular builder")
        html.Div(
            "Starting geometries; check strain, charge and spin before calculation.",
            classes="text-caption",
        )
        v.VTextField(v_model=("mb_angle",), type="number", label="Bond rotation Δ angle (degrees)")
        v.VSlider(
            v_model=("mb_angle",),
            min=-180,
            max=180,
            step=1,
            label="Drag bond rotation",
            aria_label="Bond rotation angle",
            __properties=[("aria_label", "aria-label")],
            # The released value, not mb_angle: its v-model update can
            # reach the server after this trigger (as in #122).
            end=(ctrl.mb_rotate, "[$event]"),
        )
        v.VBtn("Rotate selected bond", click=ctrl.mb_rotate, size="small")
        v.VSelect(
            v_model=("mb_template",),
            items=("mb_template_options", list(TEMPLATES)),
            label="Coordination geometry",
        )
        v.VSelect(
            v_model=("mb_ligand",), items=("mb_ligand_options", list(LIGANDS)), label="Ligand"
        )
        v.VTextField(v_model=("mb_distance",), type="number", label="Metal–donor distance (Å)")
        v.VCheckbox(v_model=("mb_replace_h",), label="Replace hydrogens bonded to selected metal")
        v.VBtn("Fill open sites", click=ctrl.mb_coordinate, size="small")
        v.VSelect(
            v_model=("mb_sequence_kind",),
            items=("mb_sequence_kind_options", ["peptide", "DNA", "RNA"]),
            label="Sequence type",
        )
        v.VTextarea(
            v_model=("mb_sequence",),
            label="One-letter sequence",
            placeholder="AGSA (peptide) or ACGT (DNA/RNA)",
            rows=2,
        )
        v.VSelect(
            v_model=("mb_strands",),
            items=("mb_strands_options", ["single", "duplex"]),
            label="DNA/RNA strand construction",
        )
        html.Div(
            "Duplex: antiparallel reverse complement, B-family DNA or A-family RNA. "
            "Starting geometry; no secondary-structure prediction.",
            classes="text-caption",
        )
        v.VSelect(
            v_model=("mb_backbone",),
            items=("mb_backbone_options", ["helix", "sheet", "custom"]),
            label="Peptide backbone",
        )
        v.VTextField(v_model=("mb_phi",), type="number", label="Custom phi (degrees)")
        v.VTextField(v_model=("mb_psi",), type="number", label="Custom psi (degrees)")
        v.VBtn(
            "Build sequence",
            click=ctrl.mb_sequence_build,
            size="small",
            disabled=("mb_sequence_running",),
        )
        v.VTextField(v_model=("mb_count",), type="number", label="Conformers (1–100)")
        v.VTextField(v_model=("mb_seed",), type="number", label="Fixed random seed")
        v.VBtn("Generate and rank conformers", click=ctrl.mb_conformers, size="small")
        v.VTextField(v_model=("mb_search",), label="Search fragments by name / SMILES")
        html.Div("{{ mb_notice }}", classes="text-caption")
        with (
            html.Div(style="max-height:300px;overflow:auto"),
            v.VCard(v_for="item in mb_fragments", key="item.name", classes="pa-1 mb-1"),
        ):
            html.Img(src=("item.thumbnail",), alt=("item.name",), width=180, height=100)
            html.Div("{{ item.name }}")
            v.VBtn("Open", click=(ctrl.mb_fragment_use, "[item.name, false]"), size="small")
            v.VBtn(
                "Attach at selected atom",
                click=(ctrl.mb_fragment_use, "[item.name, true]"),
                size="small",
            )
