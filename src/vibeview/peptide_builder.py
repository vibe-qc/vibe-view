"""Sequence topology from RDKit; explicit backbone torsions after embedding.

Backbone presets are geometric alpha-helix (-57,-47) and extended beta-strand
(-135,135), in degrees. Internal proline uses constrained MMFF94 with
measured ring/amide geometry refusal; other chains use rigid torsion edits.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from vibeview.molecular_builder import rdkit

BACKBONES = {"helix": (-57.0, -47.0), "sheet": (-135.0, 135.0)}


def peptide(
    sequence: str, preset: str = "helix", phi: float = -57.0, psi: float = -47.0, seed: int = 42
) -> Any:
    sequence = "".join(sequence.split()).upper()
    if not sequence or len(sequence) > 100 or set(sequence) - set("ACDEFGHIKLMNPQRSTVWY"):
        raise ValueError("Use 1–100 standard one-letter amino acids")
    if preset in BACKBONES:
        phi, psi = BACKBONES[preset]
    elif preset != "custom":
        raise ValueError("Backbone must be helix, sheet or custom")
    if not np.isfinite([phi, psi]).all() or not -180 <= phi <= 180 or not -180 <= psi <= 180:
        raise ValueError("Backbone angles must lie between -180 and 180 degrees")
    Chem, AllChem = rdkit()
    from rdkit.Chem import rdMolTransforms

    mol = Chem.AddHs(Chem.MolFromFASTA(sequence))
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    params.numThreads = 1
    if AllChem.EmbedMolecule(mol, params) != 0:
        raise ValueError("Peptide embedding failed; try a shorter sequence or another seed")
    if AllChem.MMFFHasAllMoleculeParams(mol):
        AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
    residues: dict[int, dict[str, int]] = {}
    for atom in mol.GetAtoms():
        info = atom.GetPDBResidueInfo()
        if info is not None:
            residues.setdefault(info.GetResidueNumber(), {})[info.GetName().strip()] = atom.GetIdx()
    conf = mol.GetConformer()
    targets: list[tuple[tuple[int, int, int, int], float]] = []
    for i in range(1, len(sequence) + 1):
        row = residues[i]
        if i > 1:
            prev = residues[i - 1]
            targets.append(((prev["CA"], prev["C"], row["N"], row["CA"]), 180.0))
            targets.append(((prev["C"], row["N"], row["CA"], row["C"]), phi))
        if i < len(sequence):
            targets.append(((row["N"], row["CA"], row["C"], residues[i + 1]["N"]), psi))
    if "P" in sequence[1:]:
        from vibeview.builder_relaxation import constrain_torsions

        # Put all rotatable non-ring backbone bonds near their targets first.
        # This avoids forcing large rotations by distorting peptide bond angles.
        for ids, angle in targets:
            if not mol.GetBondBetweenAtoms(ids[1], ids[2]).IsInRing():
                rdMolTransforms.SetDihedralDeg(conf, *ids, angle)
        ring_angles: list[tuple[tuple[int, int, int], float]] = []
        for ring in mol.GetRingInfo().AtomRings():
            for j, center in enumerate(ring):
                angle_ids = (ring[j - 1], center, ring[(j + 1) % len(ring)])
                ring_angles.append((angle_ids, rdMolTransforms.GetAngleDeg(conf, *angle_ids)))
        mol = constrain_torsions(mol, targets)
        from itertools import combinations

        for i, letter in enumerate(sequence, 1):
            if letter != "P" or i == 1:
                continue
            atom = mol.GetAtomWithIdx(residues[i]["N"])
            neighbors = [a.GetIdx() for a in atom.GetNeighbors()]
            total = sum(
                rdMolTransforms.GetAngleDeg(mol.GetConformer(), a, atom.GetIdx(), b)
                for a, b in combinations(neighbors, 2)
            )
            if total < 350.0:
                raise ValueError(
                    "Requested proline phi requires nonplanar peptide nitrogen (>10 degrees)"
                )
        for angle_ids, angle in ring_angles:
            if abs(rdMolTransforms.GetAngleDeg(mol.GetConformer(), *angle_ids) - angle) > 10.0:
                raise ValueError("Requested proline phi requires ring-angle distortion >10 degrees")
    else:
        for ids, angle in targets:
            rdMolTransforms.SetDihedralDeg(conf, *ids, angle)
    mol.SetProp("builder_sequence", sequence)
    mol.SetDoubleProp("builder_phi", phi)
    mol.SetDoubleProp("builder_psi", psi)
    return mol
