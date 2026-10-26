"""Constrained force-field cleanup with measured geometry acceptance/refusal."""

from __future__ import annotations

from typing import Any

import numpy as np

from vibeview.molecular_builder import rdkit


def constrain_torsions(
    molecule: Any,
    targets: list[tuple[tuple[int, int, int, int], float]],
    *,
    tolerance: float = 0.1,
    max_steps: int = 4000,
) -> Any:
    """Impose torsions without accepting broken bonds or unresolved heavy clashes.

    Constraints have 0.001-degree windows. Acceptance is measured independently
    (0.1 degree, heavy bonds 1.1–1.85 Å, nonbonded heavy separation >=1.8 Å).
    A failed optimization returns no geometry; raw template remains untouched.
    """
    Chem, AllChem = rdkit()
    from rdkit.Chem import rdMolTransforms

    mol = Chem.Mol(molecule)
    if not AllChem.MMFFHasAllMoleculeParams(mol):
        raise ValueError("Constrained builder requires complete MMFF94 parameters")
    props = AllChem.MMFFGetMoleculeProperties(mol)
    code = 1
    for strength in (10.0, 100.0, 1000.0):
        ff = AllChem.MMFFGetMoleculeForceField(mol, props)
        for ids, angle in targets:
            angle = (angle + 180) % 360 - 180
            lo, hi = max(-180.0, angle - 0.001), min(180.0, angle + 0.001)
            ff.MMFFAddTorsionConstraint(*ids, False, lo, hi, strength)
        ff.Initialize()
        code = ff.Minimize(maxIts=max_steps, forceTol=1e-4, energyTol=1e-7)
    conf = mol.GetConformer()
    if code != 0:
        raise ValueError(
            "Constrained geometry did not converge; requested backbone is not accepted"
        )
    for ids, angle in targets:
        actual = rdMolTransforms.GetDihedralDeg(conf, *ids)
        if abs((actual - angle + 180) % 360 - 180) > tolerance:
            raise ValueError("Requested backbone torsion could not be achieved within tolerance")
    validate_geometry(mol, molecule)
    mol.SetProp("builder_relaxation", "MMFF94 constrained; torsions within 0.1 degree")
    return mol


def validate_geometry(mol: Any, molecule: Any) -> None:
    """Shared heavy-bond, clash and specified stereochemistry acceptance."""
    Chem, _ = rdkit()
    conf = mol.GetConformer()
    positions = np.asarray(conf.GetPositions())
    if not np.isfinite(positions).all():
        raise ValueError("Constrained geometry contains nonfinite coordinates")
    heavy = np.array([a.GetAtomicNum() > 1 for a in mol.GetAtoms()])
    for bond in mol.GetBonds():
        i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if heavy[i] and heavy[j] and not 1.1 <= np.linalg.norm(positions[i] - positions[j]) <= 1.85:
            raise ValueError("Requested backbone requires a distorted heavy-atom bond")
    graph = Chem.GetDistanceMatrix(mol)
    distances = np.linalg.norm(positions[:, None] - positions[None, :], axis=-1)
    mask = (graph > 2) & heavy[:, None] & heavy[None, :]
    if np.any(distances[mask] < 1.8):
        raise ValueError("Requested backbone retains a heavy-atom clash; no geometry accepted")
    probe = Chem.Mol(mol)
    Chem.AssignAtomChiralTagsFromStructure(probe, replaceExistingTags=True)
    expected = [a for a in molecule.GetAtoms() if a.GetAtomicNum() > 1]
    observed = [a for a in probe.GetAtoms() if a.GetAtomicNum() > 1]
    for before, after in zip(expected, observed, strict=True):
        if before.GetAtomicNum() != after.GetAtomicNum():
            raise ValueError("Constrained geometry changed heavy-atom identity")
        tag = before.GetChiralTag()
        if tag != Chem.ChiralType.CHI_UNSPECIFIED and after.GetChiralTag() != tag:
            raise ValueError("Constrained geometry changed a specified stereocentre")
