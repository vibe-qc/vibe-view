"""Use existing sequence builders only after the sketch's graph identity matches."""
from __future__ import annotations

import json
import re
from typing import Any

import numpy as np

from vibeview.qvf import QVFReader
from vibeview.sketch_return import origin_bytes


def sequence_hint(reader: QVFReader) -> dict[str, Any] | None:
    payload = origin_bytes(reader)
    if payload is None:
        return None
    value = json.loads(payload)
    return value if isinstance(value, dict) and "helm" in value else None


def _plain_sequence(helm: str) -> tuple[str, str]:
    peptide = re.fullmatch(
        r"PEPTIDE1\{([ACDEFGHIKLMNPQRSTVWY](?:\.[ACDEFGHIKLMNPQRSTVWY])*)\}\$\$\$\$", helm
    )
    if peptide:
        return "peptide", peptide[1].replace(".", "")
    rna = re.fullmatch(r"RNA1\{([^{}]+)\}\$\$\$\$", helm)
    if rna:
        tokens = rna[1].split(".")
        matches = [re.fullmatch(r"(\[dR\]|R)\(([ACGTU])\)(P?)", token) for token in tokens]
        if all(matches):
            residues = [m for m in matches if m is not None]
            sugar = residues[0][1]
            kind = "DNA" if sugar == "[dR]" else "RNA"
            if (all(m[1] == sugar for m in residues)
                    and all(m[3] == "P" for m in residues[:-1])
                    and residues[-1][3] == ""
                    and set(m[2] for m in residues) <= set("ACGT" if kind == "DNA" else "ACGU")):
                return kind, "".join(m[2] for m in residues)
    raise ValueError("This modified or linked HELM is not supported by the sequence builder; "
                     "the original atom graph is unchanged")


def build_sequence_positions(reader: QVFReader) -> tuple[np.ndarray, dict[str, Any]]:
    """Return builder coordinates in the *original* atom order, or refuse unchanged."""
    from vibeview.molecular_builder import rdkit

    hint = sequence_hint(reader)
    if hint is None or not isinstance(hint.get("helm"), str):
        raise ValueError("This structure has no sequence hand-off")
    kind, sequence = _plain_sequence(hint["helm"])
    structures = [s for s in reader.sections if s.kind == "structure"]
    if len(structures) != 1:
        raise ValueError("A sequence hand-off needs exactly one structure")
    extra = structures[0].model_extra or {}
    Chem, _ = rdkit()
    graph = Chem.MolFromSmiles(extra.get("smiles", ""))
    if graph is None or not graph.GetNumAtoms():
        raise ValueError("The original structure has no usable graph identity")
    expected = hint.get("helm_inchikey")
    if not isinstance(expected, str) or Chem.MolToInchiKey(graph) != expected:
        raise ValueError("HELM InChIKey differs from the original graph; geometry unchanged")
    # Verify the source order before mapping the independent builder topology.
    template = Chem.AddHs(graph)
    structure = reader.read_structure()
    if [a.GetSymbol() for a in template.GetAtoms()] != [a.symbol for a in structure.atoms]:
        raise ValueError("Cannot prove the original atom order; geometry unchanged")
    def bonds(mol: Any) -> set[tuple[int, int, float]]:
        return {(min(b.GetBeginAtomIdx(), b.GetEndAtomIdx()),
                 max(b.GetBeginAtomIdx(), b.GetEndAtomIdx()), float(b.GetBondTypeAsDouble()))
                for b in mol.GetBonds()}
    if structure.bonds is None or bonds(template) != {
        (min(i, j), max(i, j), float(order)) for i, j, order, _ in structure.bonds
    }:
        raise ValueError("Explicit bonds differ from sequence identity; geometry unchanged")
    if kind == "peptide":
        from vibeview.peptide_builder import peptide

        built = peptide(sequence)
    else:
        from vibeview.nucleic_builder import nucleic_acid

        built = nucleic_acid(sequence, kind)
    # MMFF setup may clear aromatic flags; restore RDKit graph perception
    # before exact matching without altering the builder coordinates.
    Chem.SanitizeMol(built)
    if Chem.MolToInchiKey(Chem.RemoveHs(built)) != expected:
        raise ValueError("Built sequence InChIKey differs; the original geometry is unchanged")
    indices = built.GetSubstructMatch(template, useChirality=True)
    if len(indices) != len(structure.atoms) or len(set(indices)) != len(indices):
        raise ValueError("The builder has no exact atom mapping; geometry unchanged")
    positions = np.array([list(built.GetConformer().GetAtomPosition(i)) for i in indices])
    if not np.isfinite(positions).all():
        raise ValueError("Sequence builder returned non-finite coordinates")
    return positions, {"builder": kind, "sequence": sequence, "inchikey": expected,
                       "geometry": "starting model, not an equilibrium prediction"}
