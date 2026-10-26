import pytest
from rdkit.Chem import rdMolTransforms

from vibeview.peptide_builder import peptide


@pytest.mark.parametrize(
    "preset,phi,psi", [("helix", -57, -47), ("sheet", -135, 135), ("custom", -80, 70)]
)
def test_backbone_angles_and_bond_lengths(preset, phi, psi):
    mol = peptide("AGSA", preset, phi, psi)
    conf = mol.GetConformer()
    res = {}
    for atom in mol.GetAtoms():
        info = atom.GetPDBResidueInfo()
        if info:
            res.setdefault(info.GetResidueNumber(), {})[info.GetName().strip()] = atom.GetIdx()
    for i in range(1, 5):
        row = res[i]
        if i > 1:
            assert rdMolTransforms.GetDihedralDeg(
                conf, res[i - 1]["C"], row["N"], row["CA"], row["C"]
            ) == pytest.approx(phi, abs=1e-8)
            assert abs(
                rdMolTransforms.GetDihedralDeg(
                    conf, res[i - 1]["CA"], res[i - 1]["C"], row["N"], row["CA"]
                )
            ) == pytest.approx(180, abs=1e-8)
            assert rdMolTransforms.GetBondLength(conf, res[i - 1]["C"], row["N"]) == pytest.approx(
                1.35, abs=0.06
            )
        if i < 4:
            assert rdMolTransforms.GetDihedralDeg(
                conf, row["N"], row["CA"], row["C"], res[i + 1]["N"]
            ) == pytest.approx(psi, abs=1e-8)
        assert rdMolTransforms.GetBondLength(conf, row["N"], row["CA"]) == pytest.approx(
            1.46, abs=0.06
        )


def test_internal_proline_constrained_phi_and_infeasible_refusal():
    mol = peptide("APA", "helix")
    atoms = {}
    for atom in mol.GetAtoms():
        info = atom.GetPDBResidueInfo()
        if info:
            atoms.setdefault(info.GetResidueNumber(), {})[info.GetName().strip()] = atom.GetIdx()
    assert rdMolTransforms.GetDihedralDeg(
        mol.GetConformer(), atoms[1]["C"], atoms[2]["N"], atoms[2]["CA"], atoms[2]["C"]
    ) == pytest.approx(-57, abs=0.1)
    with pytest.raises(ValueError, match="proline|torsion|clash|stereocentre"):
        peptide("APA", "custom", 60, -47)
