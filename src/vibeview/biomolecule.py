"""PDB/mmCIF identity-preserving models, conformers and residue selection.

Unrepresented PDB fields stay in open QVF provenance, never invented schema
fields. See docs/biomolecules.md for the display and ensemble contract.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np

from vibeview.elements import SYMBOL_TO_Z
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder, structure_payload

AMINO = dict(
    zip(
        [
            "ALA",
            "ARG",
            "ASN",
            "ASP",
            "CYS",
            "GLN",
            "GLU",
            "GLY",
            "HIS",
            "ILE",
            "LEU",
            "LYS",
            "MET",
            "PHE",
            "PRO",
            "SER",
            "THR",
            "TRP",
            "TYR",
            "VAL",
        ],
        "ARNDCQEGHILKMFPSTWYV",
        strict=True,
    )
)
AMINO.update(MSE="M", SEC="U", PYL="O", ASX="B", GLX="Z", UNK="X")
WATERS = {"HOH", "WAT", "DOD", "H2O"}


def _identity(atom: dict[str, Any]) -> tuple[Any, ...]:
    return (
        atom["chain_id"],
        atom["residue_seq"],
        atom["insertion_code"],
        atom["residue_name"],
        atom["atom_name"],
        atom["hetero"],
    )


def _archive(
    models: list[list[dict[str, Any]]],
    labels: list[str],
    *,
    lattice: Any,
    secondary: list[dict[str, Any]],
    source: str,
    source_format: str,
    warnings: list[str] | None = None,
) -> io.BytesIO:
    if not models or any(not model for model in models):
        raise ValueError("Every biomolecular model must contain atoms")
    builder = QVFArchiveBuilder(calculation=f"{source_format}:biomolecule")
    variants: dict[str, Any] = {}
    frames: dict[str, list[tuple[list[tuple[Any, ...]], list[dict[str, Any]], str]]] = {}
    warnings = list(warnings or [])
    for model_index, records in enumerate(models):
        alternatives = list(dict.fromkeys(r["altloc"] for r in records if r["altloc"]))
        choices = ["first", *alternatives] if alternatives else ["first"]
        for choice_index, choice in enumerate(choices):
            groups: dict[tuple[Any, ...], list[int]] = {}
            for i, record in enumerate(records):
                groups.setdefault(_identity(record), []).append(i)
            selected = []
            fallback = []
            for indices in groups.values():
                matches = [i for i in indices if records[i]["altloc"] == choice]
                common = [i for i in indices if not records[i]["altloc"]]
                selected.append((matches or common or indices)[0])
                if choice != "first" and not matches and not common:
                    fallback.append(indices[0])
            atoms: list[dict[str, Any]] = [
                {
                    k: r[k]
                    for k in (
                        "symbol",
                        "atomic_number",
                        "position",
                        "atom_name",
                        "residue_name",
                        "chain_id",
                        "residue_seq",
                        "b_factor",
                    )
                    if k in r
                }
                for r in (records[i] for i in selected)
            ]
            section = (
                "structure"
                if model_index == choice_index == 0
                else f"bio_{model_index}_{choice_index}"
            )
            label = f"Model {labels[model_index]} · alt {choice}"
            payload = structure_payload(atoms, [lattice is not None] * 3, lattice)
            builder.add_json(
                section,
                "structure",
                "structure",
                payload,
                section_extra={"label": label, "secondary_structure": secondary},
            )
            variants[section] = {
                "model_index": model_index,
                "model_label": labels[model_index],
                "altloc": choice,
                "record_indices": selected,
                "fallback_record_indices": fallback,
                "label": label,
            }
            identities = [_identity(records[i]) for i in selected]
            frames.setdefault(choice, []).append((identities, atoms, section))
    for index, (choice, entries) in enumerate(frames.items()):
        if len(entries) < 2:
            continue
        if any(item[0] != entries[0][0] for item in entries[1:]):
            warnings.append(
                f"Alt {choice}: model atom identities differ; use structure chooser, not trajectory"
            )
            continue
        metadata = {"atoms": entries[0][1], "coordinate_unit": "angstrom"}
        sid = f"bio_ensemble_{index}"
        builder.add_json(
            sid,
            "trajectory",
            "metadata",
            metadata,
            section_extra={"label": f"NMR models · alt {choice}", "structure_ref": entries[0][2]},
        )
        builder.add_binary(
            sid,
            "trajectory",
            "coords",
            np.asarray([[atom["position"] for atom in item[1]] for item in entries]),
        )
    builder.set_provenance(
        {
            "biomolecule": {
                "models": models,
                "variants": variants,
                "source_format": source_format,
                "source_text": source,
                "warnings": warnings,
            }
        }
    )
    return builder.to_bytesio()


def pdb_models(text: str) -> io.BytesIO:
    """Built-in PDB reading with retained models, alternate records and author IDs."""
    from vibeview.converters import _pdb_element, _pdb_first_to_qvf

    with QVFReader(_pdb_first_to_qvf(text.encode())) as reader:
        structure = reader.read_structure()
        lattice = structure.lattice_vectors
        secondary = structure.supplied_secondary_structure or []
    models: list[list[dict[str, Any]]] = []
    labels: list[str] = []
    current: list[dict[str, Any]] = []
    label = "1"
    warnings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if line.startswith("MODEL "):
            if current:
                models.append(current)
                labels.append(label)
                current = []
            label = line[10:14].strip() or str(len(models) + 1)
        elif line.startswith("ENDMDL"):
            if current:
                models.append(current)
                labels.append(label)
                current = []
        elif line.startswith(("ATOM  ", "HETATM")):
            try:
                symbol = _pdb_element(line)
                if not symbol:
                    raise ValueError("unknown element")
                position = [float(line[i : i + 8]) for i in (30, 38, 46)]
                if not np.isfinite(position).all():
                    raise ValueError("non-finite coordinates")
                record = {
                    "symbol": symbol,
                    "atomic_number": SYMBOL_TO_Z.get(symbol, 0),
                    "position": position,
                    "atom_name": line[12:16].strip(),
                    "residue_name": line[17:20].strip(),
                    "chain_id": line[21:22].strip(),
                    "residue_seq": int(line[22:26]),
                    "insertion_code": line[26:27].strip(),
                    "altloc": line[16:17].strip(),
                    "occupancy": float(line[54:60].strip() or "1"),
                    "hetero": line.startswith("HETATM"),
                }
                # Missing or malformed experimental metadata must not invent a
                # measurement or discard an otherwise valid atom.
                try:
                    b_factor = float(line[60:66])
                    if np.isfinite(b_factor):
                        record["b_factor"] = b_factor
                except ValueError:
                    pass
                current.append(record)
            except ValueError as exc:
                warnings.append(
                    f"PDB line {line_number}: retained in source but not displayed ({exc})"
                )
    if current:
        models.append(current)
        labels.append(label)
    return _archive(
        models,
        labels,
        lattice=lattice,
        secondary=secondary,
        source=text,
        source_format="pdb",
        warnings=warnings,
    )


def mmcif_models(text: str) -> io.BytesIO:
    """Use MPL-2.0 Gemmi, retaining author residue IDs and every alt/model."""
    try:
        import gemmi
    except ImportError as exc:
        from vibeview.install_hints import install_hint

        raise ImportError("mmCIF requires Gemmi. " + install_hint("biomolecule")) from exc
    structure = gemmi.make_structure_from_block(gemmi.cif.read_string(text).sole_block())
    models = []
    labels = []
    for model in structure:
        records = []
        for chain in model:
            for residue in chain:
                for atom in residue:
                    position = [atom.pos.x, atom.pos.y, atom.pos.z]
                    if not np.isfinite(position).all() or atom.element.atomic_number < 1:
                        raise ValueError("mmCIF contains invalid coordinates/elements")
                    records.append(
                        {
                            "symbol": atom.element.name,
                            "atomic_number": atom.element.atomic_number,
                            "position": position,
                            "atom_name": atom.name,
                            "residue_name": residue.name,
                            "chain_id": chain.name,
                            "residue_seq": residue.seqid.num,
                            "insertion_code": residue.seqid.icode.strip(),
                            "altloc": atom.altloc.replace("\x00", "").strip(),
                            "occupancy": float(atom.occ),
                            "b_factor": float(atom.b_iso),
                            "hetero": residue.het_flag != "A",
                        }
                    )
        models.append(records)
        labels.append(str(model.num))
    secondary = []
    ranges = [(h.start, h.end, "helix") for h in structure.helices]
    ranges += [(r.start, r.end, "sheet") for sheet in structure.sheets for r in sheet.strands]
    for start, end, kind in ranges:
        if (
            start.chain_name == end.chain_name
            and not start.res_id.seqid.icode.strip()
            and not end.res_id.seqid.icode.strip()
        ):
            secondary.append(
                {
                    "type": kind,
                    "chain": start.chain_name,
                    "start_seq": start.res_id.seqid.num,
                    "end_seq": end.res_id.seqid.num,
                }
            )
    lattice = None
    if structure.cell.is_crystal():
        lattice = np.asarray(structure.cell.orth.mat).T.tolist()
    return _archive(
        models, labels, lattice=lattice, secondary=secondary, source=text, source_format="mmcif"
    )


def residues(reader: QVFReader, section_id: str | None = None) -> list[dict[str, Any]]:
    """Observed residues, including insertion codes, linked to displayed atom indices."""
    sid = section_id or reader.shown_structure_id
    if sid in reader.edited_structure_ids:
        raise ValueError(
            "Residue metadata is stale after editing; save/reopen a labelled structure"
        )
    structure = reader.read_structure(sid)
    bio = reader.provenance.get("biomolecule", {})
    variant = bio.get("variants", {}).get(sid)
    if variant:
        original = bio["models"][variant["model_index"]]
        records = [original[i] for i in variant["record_indices"]]
    else:
        records = [
            {
                "chain_id": a.chain_id or "",
                "residue_seq": a.residue_seq,
                "residue_name": a.residue_name or "UNK",
                "atom_name": a.atom_name,
                "insertion_code": "",
                "hetero": False,
            }
            for a in structure.atoms
        ]
    result: dict[tuple[Any, ...], dict[str, Any]] = {}
    secondary = dict(
        zip(
            (i for _, _, i in structure.ca_residues()), structure.secondary_structure(), strict=True
        )
    )
    for index, record in enumerate(records):
        if record["residue_seq"] is None:
            continue
        key = (
            record["chain_id"],
            record["residue_seq"],
            record["insertion_code"],
            record["residue_name"],
        )
        name = record["residue_name"]
        kind = (
            "water"
            if name in WATERS
            else "polymer"
            if name in AMINO or not record["hetero"]
            else "ligand"
        )
        entry = result.setdefault(
            key,
            {
                "key": list(key),
                "label": f"{key[0]}/{key[1]}{key[2]} {name}",
                "chain": key[0],
                "letter": AMINO.get(name, "X"),
                "kind": kind,
                "atom_indices": [],
                "ca_index": None,
                "secondary": "coil",
            },
        )
        entry["atom_indices"].append(index)
        if record["atom_name"] == "CA" and kind == "polymer":
            entry["ca_index"] = index
            entry["secondary"] = (
                secondary.get(index, "unknown") if not record["insertion_code"] else "unknown"
            )
    return list(result.values())
