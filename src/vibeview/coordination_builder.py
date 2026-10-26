"""Ideal coordination directions and rigid donor placement, not metal energetics."""

from __future__ import annotations

from itertools import permutations
from typing import Any

import numpy as np

from vibeview.bonds import structure_bonds
from vibeview.molecular_builder import rdkit
from vibeview.qvf import Atom, StructureData

TEMPLATES: dict[str, list[list[float]]] = {
    "linear": [[1, 0, 0], [-1, 0, 0]],
    "trigonal planar": [[1, 0, 0], [-0.5, 3**0.5 / 2, 0], [-0.5, -(3**0.5) / 2, 0]],
    "tetrahedral": [[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]],
    "square planar": [[1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]],
    "trigonal bipyramidal": [
        [1, 0, 0],
        [-0.5, 3**0.5 / 2, 0],
        [-0.5, -(3**0.5) / 2, 0],
        [0, 0, 1],
        [0, 0, -1],
    ],
    "square pyramidal": [[1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0], [0, 0, 1]],
    "octahedral": [[1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]],
}
# Donor indices are explicit in these small chemically meaningful templates.
LIGANDS: dict[str, tuple[str, tuple[int, ...]]] = {
    "water": ("O", (0,)),
    "ammonia": ("N", (0,)),
    "chloride": ("[Cl-]", (0,)),
    "carbon monoxide": ("[C-]#[O+]", (0,)),
    "pyridine": ("n1ccccc1", (0,)),
    "cyanide (C-bound)": ("[C-]#N", (0,)),
    "ethylenediamine": ("NCCN", (0, 3)),
    "oxalate": ("[O-]C(=O)C(=O)[O-]", (0, 5)),
}


def directions(template: str) -> np.ndarray:
    if template not in TEMPLATES:
        raise ValueError("Unknown coordination template")
    xyz = np.asarray(TEMPLATES[template], dtype=float)
    return np.asarray(xyz / np.linalg.norm(xyz, axis=1)[:, None])


def align_vectors(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Proper row-vector Kabsch rotation; one vector uses Rodrigues."""
    if len(source) == 1:
        a, b = source[0] / np.linalg.norm(source[0]), target[0] / np.linalg.norm(target[0])
        v = np.cross(a, b)
        c = float(a @ b)
        if c < -1 + 1e-10:
            axis = np.cross(a, np.eye(3)[np.argmin(np.abs(a))])
            axis /= np.linalg.norm(axis)
            return np.asarray(2 * np.outer(axis, axis) - np.eye(3))
        k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
        return np.asarray((np.eye(3) + k + k @ k / (1 + c)).T)
    u, _, vt = np.linalg.svd(source.T @ target)
    return u @ np.diag([1.0, 1.0, np.linalg.det(u @ vt)]) @ vt


def ligand_geometry(name: str) -> tuple[Any, np.ndarray, tuple[int, ...]]:
    if name not in LIGANDS:
        raise ValueError("Unknown ligand")
    Chem, AllChem = rdkit()
    smiles, donors = LIGANDS[name]
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    if AllChem.EmbedMolecule(mol, randomSeed=42) != 0:
        raise ValueError("Ligand embedding failed")
    if AllChem.MMFFHasAllMoleculeParams(mol):
        AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
    elif AllChem.UFFHasAllMoleculeParams(mol):
        AllChem.UFFOptimizeMolecule(mol, maxIters=500)
    return mol, np.asarray(mol.GetConformer().GetPositions()), donors


def coordinate(
    structure: StructureData,
    metal: int,
    template: str,
    ligand: str,
    distance: float = 2.1,
    replace_hydrogens: bool = False,
    donor_tolerance: float = 0.25,
) -> StructureData:
    """Fill open sites while preserving every existing non-H ligand.

    Existing neighbour directions orient the template (max 15° deviation).
    Bidentate ligands occupy nearest compatible free-site pairs; placement is
    rigid and refuses donor-distance mismatch > donor_tolerance angstrom.
    This explicitly avoids distorting the chelate to force a nominal template.
    """
    if any(structure.pbc) or not 0 <= metal < len(structure.atoms):
        raise ValueError("Choose one metal in a molecular structure")
    z = structure.atoms[metal].atomic_number
    if z is None or not (
        z in (3, 4, 11, 12, 13, 19, 20, 31, 37, 38, 49, 50, 55, 56, 81, 82, 83, 87, 88)
        or 21 <= z <= 30
        or 39 <= z <= 48
        or 57 <= z <= 80
        or 89 <= z <= 112
    ):
        raise ValueError("Selected atom is not a supported metal")
    if not np.isfinite(distance) or distance <= 0 or not 0 <= donor_tolerance <= 0.5:
        raise ValueError("Use a positive metal–donor distance and tolerance between 0 and 0.5 Å")
    bonds = structure_bonds(structure)
    neighbors = {j if i == metal else i for i, j, _, _ in bonds if metal in (i, j)}
    removed = {i for i in neighbors if replace_hydrogens and structure.atoms[i].symbol == "H"}
    retained = sorted(neighbors - removed)
    center = structure.atoms[metal].position
    dirs = directions(template)
    if len(retained) >= len(dirs):
        raise ValueError("No open coordination sites; choose hydrogen replacement if appropriate")
    used: tuple[int, ...] = ()
    if retained:
        current = np.array([structure.atoms[i].position - center for i in retained])
        if np.any(np.linalg.norm(current, axis=1) < 1e-10):
            raise ValueError("Existing ligand overlaps metal")
        current /= np.linalg.norm(current, axis=1)[:, None]
        candidates = []
        for choice in permutations(range(len(dirs)), len(retained)):
            rotation = align_vectors(dirs[list(choice)], current)
            error = float(np.max(np.linalg.norm(dirs[list(choice)] @ rotation - current, axis=1)))
            candidates.append((error, choice, rotation))
        error, used, rotation = min(candidates, key=lambda x: x[0])
        if error > 2 * np.sin(np.radians(15) / 2):
            raise ValueError("Existing ligands differ from template by more than 15 degrees")
        dirs = dirs @ rotation
    open_sites = [i for i in range(len(dirs)) if i not in used]
    mol, xyz, donors = ligand_geometry(ligand)
    if len(open_sites) % len(donors):
        raise ValueError("Open-site count is incompatible with ligand denticity")
    mapping = {
        old: new
        for new, old in enumerate(i for i in range(len(structure.atoms)) if i not in removed)
    }
    atoms = [
        Atom(a.symbol, a.position.copy(), a.atomic_number)
        for i, a in enumerate(structure.atoms)
        if i not in removed
    ]
    result_bonds = [
        (mapping[i], mapping[j], order, image)
        for i, j, order, image in bonds
        if i not in removed and j not in removed
    ]
    while open_sites:
        first = open_sites.pop(0)
        sites = [first]
        if len(donors) == 2:
            sep = float(np.linalg.norm(xyz[donors[0]] - xyz[donors[1]]))
            second = min(
                open_sites,
                key=lambda j: abs(distance * np.linalg.norm(dirs[first] - dirs[j]) - sep),
            )
            if (
                abs(distance * np.linalg.norm(dirs[first] - dirs[second]) - sep)
                > 2 * donor_tolerance
            ):
                raise ValueError(
                    "Chelate donor separation does not fit the requested sites/distance"
                )
            open_sites.remove(second)
            sites.append(second)
        target = center + distance * dirs[sites]
        if len(donors) == 1:
            # Put ligand mass beyond its donor, away from the metal.
            origin = xyz[donors[0]]
            outward = xyz.mean(axis=0) - origin
            if np.linalg.norm(outward) < 1e-8:
                outward = np.array([1.0, 0, 0])
            rotation = align_vectors(outward[None, :], dirs[sites])
        else:
            origin = xyz[list(donors)].mean(axis=0)
            rotation = align_vectors(
                (xyz[donors[1]] - xyz[donors[0]])[None, :], (target[1] - target[0])[None, :]
            )
        placed = (xyz - origin) @ rotation + target.mean(axis=0)
        if np.max(np.linalg.norm(placed[list(donors)] - target, axis=1)) > donor_tolerance:
            raise ValueError("Rigid ligand exceeds donor-placement tolerance")
        start = len(atoms)
        atoms.extend(
            Atom(a.GetSymbol(), p, a.GetAtomicNum())
            for a, p in zip(mol.GetAtoms(), placed, strict=True)
        )
        result_bonds.extend(
            (
                start + b.GetBeginAtomIdx(),
                start + b.GetEndAtomIdx(),
                b.GetBondTypeAsDouble(),
                (0, 0, 0),
            )
            for b in mol.GetBonds()
        )
        result_bonds.extend((mapping[metal], start + d, 1.0, (0, 0, 0)) for d in donors)
    return StructureData(atoms, (False, False, False), None, result_bonds)
