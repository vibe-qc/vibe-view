"""Searchable named SMILES with generated RDKit thumbnails and explicit anchors."""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Any

import numpy as np

from vibeview.bonds import structure_bonds
from vibeview.coordination_builder import align_vectors
from vibeview.elements import COVALENT_RADII
from vibeview.molecular_builder import rdkit
from vibeview.qvf import Atom, StructureData

FRAGMENTS: dict[str, str] = {
    "methane / methyl": "C",
    "ethane / ethyl": "CC",
    "propane / propyl": "CCC",
    "isobutane / tert-butyl": "CC(C)C",
    "ethene / vinyl": "C=C",
    "ethyne / ethynyl": "C#C",
    "benzene / phenyl": "c1ccccc1",
    "pyridine": "c1ccncc1",
    "pyrimidine": "c1cncnc1",
    "imidazole": "c1ncc[nH]1",
    "pyrazole": "c1cn[nH]c1",
    "pyrrole": "c1cc[nH]c1",
    "furan": "c1ccoc1",
    "thiophene": "c1ccsc1",
    "cyclopropane": "C1CC1",
    "cyclobutane": "C1CCC1",
    "cyclopentane": "C1CCCC1",
    "cyclohexane": "C1CCCCC1",
    "naphthalene": "c1ccc2ccccc2c1",
    "indole": "c1ccc2[nH]ccc2c1",
    "water / hydroxyl": "O",
    "ammonia / amino": "N",
    "methanethiol": "CS",
    "methanol": "CO",
    "ethanol": "CCO",
    "acetaldehyde": "CC=O",
    "acetone": "CC(=O)C",
    "acetic acid": "CC(=O)O",
    "acetamide": "CC(=O)N",
    "acetonitrile": "CC#N",
    "dimethyl ether": "COC",
    "dimethyl sulfoxide": "CS(C)=O",
    "nitromethane": "C[N+](=O)[O-]",
    "trifluoromethane": "C(F)(F)F",
    "fluoromethane": "CF",
    "chloromethane": "CCl",
    "bromomethane": "CBr",
    "iodomethane": "CI",
    "morpholine": "C1COCCN1",
    "piperidine": "C1CCNCC1",
    "piperazine": "C1CNCCN1",
    "ethylene glycol": "OCCO",
}


def search(query: str) -> list[str]:
    words = query.lower().split()
    return [
        name
        for name, smiles in FRAGMENTS.items()
        if all(w in (name + " " + smiles).lower() for w in words)
    ]


@lru_cache(maxsize=128)
def thumbnail(name: str) -> str:
    Chem, _ = rdkit()
    from rdkit.Chem.Draw import rdMolDraw2D

    if name not in FRAGMENTS:
        raise ValueError("Unknown fragment")
    drawer = rdMolDraw2D.MolDraw2DSVG(180, 100)
    drawer.DrawMolecule(Chem.MolFromSmiles(FRAGMENTS[name]))
    drawer.FinishDrawing()
    return (
        "data:image/svg+xml;base64," + base64.b64encode(drawer.GetDrawingText().encode()).decode()
    )


def fragment(name: str) -> Any:
    if name not in FRAGMENTS:
        raise ValueError("Unknown fragment")
    Chem, AllChem = rdkit()
    mol = Chem.AddHs(Chem.MolFromSmiles(FRAGMENTS[name]))
    if AllChem.EmbedMolecule(mol, randomSeed=42) != 0:
        raise ValueError("Fragment embedding failed")
    if AllChem.MMFFHasAllMoleculeParams(mol):
        AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
    return mol


def attach(structure: StructureData, selected: int, name: str) -> StructureData:
    """Replace one explicit H at selected atom with fragment atom 0 minus one H."""
    if any(structure.pbc) or not 0 <= selected < len(structure.atoms):
        raise ValueError("Choose an atom in a molecular structure")
    bonds = structure_bonds(structure)
    neighbors = [j if i == selected else i for i, j, _, _ in bonds if selected in (i, j)]
    hydrogens = [i for i in neighbors if structure.atoms[i].symbol == "H"]
    if not hydrogens:
        raise ValueError("Selected atom needs an explicit hydrogen to replace")
    removed = hydrogens[0]
    mol = fragment(name)
    coords = np.asarray(mol.GetConformer().GetPositions())
    hs = [a.GetIdx() for a in mol.GetAtomWithIdx(0).GetNeighbors() if a.GetSymbol() == "H"]
    if not hs:
        raise ValueError("This fragment has no replaceable hydrogen at its anchor")
    direction = structure.atoms[removed].position - structure.atoms[selected].position
    if np.linalg.norm(direction) < 1e-10:
        raise ValueError("Overlapping hydrogen has no attachment direction")
    direction /= np.linalg.norm(direction)
    rotation = align_vectors((coords[hs[0]] - coords[0])[None, :], -direction[None, :])
    z1, z2 = structure.atoms[selected].atomic_number, mol.GetAtomWithIdx(0).GetAtomicNum()
    distance = COVALENT_RADII.get(z1, 0.77) + COVALENT_RADII.get(z2, 0.77)
    placed = (
        (coords - coords[0]) @ rotation + structure.atoms[selected].position + distance * direction
    )
    oldmap = {
        old: new for new, old in enumerate(i for i in range(len(structure.atoms)) if i != removed)
    }
    atoms = [
        Atom(a.symbol, a.position.copy(), a.atomic_number)
        for i, a in enumerate(structure.atoms)
        if i != removed
    ]
    mapping = {
        old: len(atoms) + new
        for new, old in enumerate(i for i in range(mol.GetNumAtoms()) if i != hs[0])
    }
    atoms.extend(
        Atom(a.GetSymbol(), placed[a.GetIdx()], a.GetAtomicNum())
        for a in mol.GetAtoms()
        if a.GetIdx() != hs[0]
    )
    result = [
        (oldmap[i], oldmap[j], order, image)
        for i, j, order, image in bonds
        if removed not in (i, j)
    ]
    result.extend(
        (
            mapping[b.GetBeginAtomIdx()],
            mapping[b.GetEndAtomIdx()],
            b.GetBondTypeAsDouble(),
            (0, 0, 0),
        )
        for b in mol.GetBonds()
        if hs[0] not in (b.GetBeginAtomIdx(), b.GetEndAtomIdx())
    )
    result.append((oldmap[selected], mapping[0], 1.0, (0, 0, 0)))
    return StructureData(atoms, (False, False, False), None, result)
