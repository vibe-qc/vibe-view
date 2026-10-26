"""Molecular construction primitives; rigid edits never imply relaxation.

Conformers use RDKit ETKDGv3 (Riniker & Landrum, 2015,
doi:10.1021/acs.jcim.5b00654), followed by parameterised MMFF94 or UFF.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from vibeview.bonds import structure_bonds
from vibeview.qvf import StructureData
from vibeview.qvf_writer import QVFArchiveBuilder, structure_payload


def rdkit() -> tuple[Any, Any]:
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError as exc:
        from vibeview.install_hints import install_hint

        raise ValueError(install_hint("smiles")) from exc
    return Chem, AllChem


def rotate_bond(
    structure: StructureData, bond: Sequence[int], degrees: float, frozen: Sequence[int] = ()
) -> np.ndarray:
    """Rotate smaller cut component; ties move second endpoint (#124 rule)."""
    n = len(structure.atoms)
    if len(bond) != 2 or len(set(bond)) != 2 or min(bond) < 0 or max(bond) >= n:
        raise ValueError("Select two distinct valid bonded atoms")
    if not np.isfinite(degrees) or any(structure.pbc):
        raise ValueError("Bond rotation requires a finite angle and a molecular structure")
    i, j = bond
    graph: dict[int, set[int]] = {a: set() for a in range(n)}
    for a, b, _, image in structure_bonds(structure):
        if any(image):
            raise ValueError("Periodic image bonds cannot be rotated")
        graph[a].add(b)
        graph[b].add(a)
    if j not in graph[i]:
        raise ValueError("Selected atoms are not bonded")
    graph[i].remove(j)
    graph[j].remove(i)

    def component(start: int) -> set[int]:
        seen, todo = {start}, [start]
        while todo:
            for other in graph[todo.pop()] - seen:
                seen.add(other)
                todo.append(other)
        return seen

    left, right = component(i), component(j)
    if left & right:
        raise ValueError("Ring bond cannot rotate as a rigid fragment")
    moving = sorted(right if len(right) <= len(left) else left)
    positions = np.array([a.position for a in structure.atoms], dtype=float)
    axis = positions[j] - positions[i]
    norm = np.linalg.norm(axis)
    if norm < 1e-12:
        raise ValueError("Coincident bond endpoints have no rotation axis")
    axis /= norm
    points = positions[moving] - positions[i]
    angle = np.radians(degrees)
    result = positions.copy()
    result[moving] = (
        positions[i]
        + points * np.cos(angle)
        + np.cross(axis, points) * np.sin(angle)
        + np.outer(points @ axis, axis) * (1 - np.cos(angle))
    )
    if any(a < 0 or a >= n for a in frozen):
        raise ValueError("Invalid frozen atom index")
    if frozen and not np.allclose(
        result[list(frozen)], positions[list(frozen)], atol=1e-10, rtol=0
    ):
        raise ValueError("Bond rotation would move frozen atoms")
    return result


def structure_molecule(structure: StructureData) -> Any:
    """Use explicit connectivity; never assign charge from an electron count guess."""
    if any(structure.pbc):
        raise ValueError("Conformers require a nonperiodic molecule")
    Chem, _ = rdkit()
    rw = Chem.RWMol()
    for atom in structure.atoms:
        item = Chem.Atom(atom.symbol)
        item.SetNoImplicit(True)
        rw.AddAtom(item)
    orders = {
        1.0: Chem.BondType.SINGLE,
        1.5: Chem.BondType.AROMATIC,
        2.0: Chem.BondType.DOUBLE,
        3.0: Chem.BondType.TRIPLE,
    }
    for i, j, order, image in structure_bonds(structure):
        if any(image) or order not in orders:
            raise ValueError("Unsupported periodic image or bond order")
        rw.AddBond(i, j, orders[order])
    mol = rw.GetMol()
    try:
        Chem.SanitizeMol(mol)
    except Exception as exc:
        raise ValueError(
            "Connectivity/valence cannot be parameterised; correct bond orders first"
        ) from exc
    # The editor has no atom-local formal-charge model; refuse charged inputs
    # rather than silently generating a different molecule.
    if any(a.GetNumRadicalElectrons() for a in mol.GetAtoms()):
        raise ValueError("Complete hydrogens and explicit valences before conformer generation")
    return mol


@dataclass
class Conformers:
    molecule: Any
    ids: list[int]
    energies: list[float]
    converged: list[bool]
    force_field: str
    seed: int

    def archive(self) -> io.BytesIO:
        builder = QVFArchiveBuilder()
        atoms = [
            {
                "symbol": a.GetSymbol(),
                "atomic_number": a.GetAtomicNum(),
                "position": [0.0, 0.0, 0.0],
            }
            for a in self.molecule.GetAtoms()
        ]
        bonds = [
            {
                "i": b.GetBeginAtomIdx(),
                "j": b.GetEndAtomIdx(),
                "order": b.GetBondTypeAsDouble(),
                "image": [0, 0, 0],
            }
            for b in self.molecule.GetBonds()
        ]
        frames = []
        for rank, cid in enumerate(self.ids):
            coords = np.asarray(self.molecule.GetConformer(cid).GetPositions())
            frames.append(coords)
            records = [dict(a, position=p.tolist()) for a, p in zip(atoms, coords, strict=True)]
            builder.add_json(
                "structure" if rank == 0 else f"conformer_{rank}",
                "structure",
                "structure",
                structure_payload(records, [False] * 3),
                section_extra={
                    "label": f"Conformer {rank + 1}: {self.energies[rank]:.6f} kcal/mol"
                },
            )
            builder.add_json(
                "structure" if rank == 0 else f"conformer_{rank}",
                "structure",
                "bonds",
                {"pairs": bonds},
            )
        builder.add_json(
            "conformers", "trajectory", "metadata", {"atoms": atoms, "coordinate_unit": "angstrom"}
        )
        builder.add_binary("conformers", "trajectory", "coords", np.asarray(frames))
        Chem, _ = rdkit()
        builder.set_provenance(
            {
                "builder": "ETKDGv3",
                "rdkit_version": str(Chem.rdBase.rdkitVersion),
                "seed": self.seed,
                "force_field": self.force_field,
                "energies_kcal_mol": self.energies,
                "optimization_converged": self.converged,
                "warning": "Force-field starting geometries, not quantum energies",
            }
        )
        # Open on the lowest-energy conformer (#140).
        builder.set_root("viewer_defaults", {"auto_open": ["structure"]})
        return builder.to_bytesio()


def conformers(molecule: Any, count: int = 10, seed: int = 42) -> Conformers:
    """Deterministic one-thread ensemble, retaining minimizer convergence flags."""
    if not 1 <= count <= 100 or not 0 <= seed <= 2147483647:
        raise ValueError("Use 1–100 conformers and a nonnegative 32-bit seed")
    Chem, AllChem = rdkit()
    mol = Chem.Mol(molecule)
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    params.numThreads = 1
    params.pruneRmsThresh = -1
    ids = list(AllChem.EmbedMultipleConfs(mol, numConfs=count, params=params))
    if len(ids) != count:
        raise ValueError(f"Embedding generated {len(ids)} of {count} requested conformers")
    if AllChem.MMFFHasAllMoleculeParams(mol):
        kind = "MMFF94"
        results = AllChem.MMFFOptimizeMoleculeConfs(mol, numThreads=1, maxIters=1000)
    elif AllChem.UFFHasAllMoleculeParams(mol):
        kind = "UFF"
        results = AllChem.UFFOptimizeMoleculeConfs(mol, numThreads=1, maxIters=1000)
    else:
        raise ValueError(
            "Neither MMFF94 nor UFF parameterises this molecule; no energy ranking available"
        )
    rows = sorted(zip(ids, results, strict=True), key=lambda row: (row[1][1], row[0]))
    if not np.isfinite([r[1][1] for r in rows]).all() or any(r[1][0] < 0 for r in rows):
        raise ValueError("Force-field evaluation failed; no energy ranking available")
    return Conformers(
        mol,
        [r[0] for r in rows],
        [float(r[1][1]) for r in rows],
        [r[1][0] == 0 for r in rows],
        kind,
        seed,
    )
