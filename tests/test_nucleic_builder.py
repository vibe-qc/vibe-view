import numpy as np
import pytest
from rdkit.Chem import rdMolTransforms as t

from vibeview.nucleic_builder import nucleic_acid


@pytest.mark.parametrize(
    "kind,sequence,epsilon,zeta", [("DNA", "ACGT", -178.3, -96.8), ("RNA", "ACGU", -150, -73)]
)
def test_templates_topology_and_interresidue_geometry(kind, sequence, epsilon, zeta):
    m = nucleic_acid(sequence, kind)
    c = m.GetConformer()
    res = {}
    for a in m.GetAtoms():
        info = a.GetPDBResidueInfo()
        res.setdefault(info.GetResidueNumber(), {})[info.GetName().strip()] = a.GetIdx()
    assert np.isfinite(c.GetPositions()).all()
    for i in range(1, 4):
        a, b = res[i], res[i + 1]
        assert t.GetBondLength(c, a["O3'"], b["P"]) == pytest.approx(1.607, abs=0.08)
        assert t.GetAngleDeg(c, a["C3'"], a["O3'"], b["P"]) == pytest.approx(120, abs=10)
        assert t.GetDihedralDeg(c, a["C4'"], a["C3'"], a["O3'"], b["P"]) == pytest.approx(
            epsilon, abs=0.1
        )
        assert t.GetDihedralDeg(c, a["C3'"], a["O3'"], b["P"], b["O5'"]) == pytest.approx(
            zeta, abs=0.1
        )
    for b in m.GetBonds():
        left, right = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
        if m.GetAtomWithIdx(left).GetSymbol() == "H" or m.GetAtomWithIdx(right).GetSymbol() == "H":
            continue
        assert 1.15 < t.GetBondLength(c, left, right) < 1.7
    from rdkit import Chem

    xyz = np.asarray(c.GetPositions())
    distance = np.linalg.norm(xyz[:, None] - xyz[None, :], axis=-1)
    heavy = np.array([a.GetAtomicNum() > 1 for a in m.GetAtoms()])
    assert (
        np.min(distance[(Chem.GetDistanceMatrix(m) > 2) & heavy[:, None] & heavy[None, :]]) >= 1.8
    )
    for row in res.values():
        delta = t.GetDihedralDeg(c, row["C5'"], row["C4'"], row["C3'"], row["O3'"])
        assert (110 < delta < 145) if kind == "DNA" else (65 < delta < 95)
    assert all(("O2'" in row) == (kind == "RNA") for row in res.values())


def test_invalid_base_refuses():
    with pytest.raises(ValueError):
        nucleic_acid("ACGU", "DNA")
    with pytest.raises(ValueError):
        nucleic_acid("", "RNA")
