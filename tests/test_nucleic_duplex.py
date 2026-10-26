"""Complementary topology and independent geometry checks for duplex seeds."""

import json
from importlib.resources import files

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import rdMolTransforms as t

from vibeview.builder_panel import molecule_archive
from vibeview.nucleic_builder import nucleic_acid
from vibeview.nucleic_duplex import complement, pairing_contacts
from vibeview.qvf import QVFReader


def residue_indices(mol):
    result = {}
    for atom in mol.GetAtoms():
        info = atom.GetPDBResidueInfo()
        result.setdefault((info.GetChainId(), info.GetResidueNumber()), {})[
            info.GetName().strip()
        ] = atom.GetIdx()
    return result


@pytest.mark.parametrize(
    "kind,sequence", [("DNA", "ACGT"), ("RNA", "ACGU"), ("DNA", "AAGC"), ("RNA", "AAGC")]
)
def test_antiparallel_duplex_pairing_backbone_stereochemistry_and_archive(kind, sequence):
    mol = nucleic_acid(sequence, kind, strands="duplex")
    assert len(Chem.GetMolFrags(mol)) == 2
    assert mol.GetProp("builder_complement") == complement(sequence, kind)
    rows = residue_indices(mol)
    assert set(rows) == {(c, i) for c in "AB" for i in range(1, len(sequence) + 1)}
    conf = mol.GetConformer()
    xyz = np.asarray(conf.GetPositions())
    assert np.isfinite(xyz).all()
    for chain, text in [("A", sequence), ("B", complement(sequence, kind))]:
        for i, code in enumerate(text, 1):
            row = rows[chain, i]
            assert (
                mol.GetAtomWithIdx(row["C1'"])
                .GetPDBResidueInfo()
                .GetResidueName()
                .strip()
                .endswith(code)
            )
            delta = t.GetDihedralDeg(conf, *[row[k] for k in ("C5'", "C4'", "C3'", "O3'")])
            assert (110 < delta < 145) if kind == "DNA" else (65 < delta < 95)
            assert ("O2'" in row) == (kind == "RNA")
            if i < len(text):
                nxt = rows[chain, i + 1]
                assert mol.GetBondBetweenAtoms(row["O3'"], nxt["P"]) is not None
                assert 1.4 < t.GetBondLength(conf, row["O3'"], nxt["P"]) < 1.8
    # Every intended WC contact pairs A_i with B_(n+1-i); no interstrand bond.
    contacts = pairing_contacts(mol, sequence)
    assert len(contacts) == sum(2 if base in "ATU" else 3 for base in sequence)
    for contact in contacts:
        first, second = contact["atoms"]
        ai, bi = (mol.GetAtomWithIdx(k).GetPDBResidueInfo() for k in (first, second))
        assert ai.GetChainId() != bi.GetChainId()
        assert ai.GetResidueNumber() + bi.GetResidueNumber() == len(sequence) + 1
        assert mol.GetBondBetweenAtoms(first, second) is None
        assert 2.4 <= contact["distance_angstrom"] <= 3.5
        assert contact["DHA_degrees"] >= 120
    heavy = np.array([a.GetAtomicNum() > 1 for a in mol.GetAtoms()])
    distances = np.linalg.norm(xyz[:, None] - xyz[None, :], axis=-1)
    assert (
        np.min(distances[(Chem.GetDistanceMatrix(mol) > 2) & heavy[:, None] & heavy[None, :]])
        >= 1.8
    )
    for bond in mol.GetBonds():
        a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if heavy[a] and heavy[b]:
            assert 1.1 <= distances[a, b] <= 1.85
    probe = Chem.Mol(mol)
    Chem.AssignAtomChiralTagsFromStructure(probe, replaceExistingTags=True)
    for a, b in zip(mol.GetAtoms(), probe.GetAtoms(), strict=True):
        if a.GetChiralTag() != Chem.ChiralType.CHI_UNSPECIFIED:
            assert a.GetChiralTag() == b.GetChiralTag()
    with QVFReader(molecule_archive(mol, "duplex fixture")) as reader:
        assert {a.chain_id for a in reader.read_structure().atoms} == {"A", "B"}
        assert len(reader.read_structure().atoms) == mol.GetNumAtoms()


@pytest.mark.parametrize("kind,sequence", [("DNA", "AAAA"), ("RNA", "GGGG")])
def test_rigid_step_and_base_internal_geometry_preserved(kind, sequence):
    mol = nucleic_acid(sequence, kind, strands="duplex")
    rows = residue_indices(mol)
    xyz = mol.GetConformer().GetPositions()
    names = ("N9", "C4", "C8", "N1", "C2", "N3", "C5", "C6", "N7")
    a = np.array([xyz[rows["A", 1][k]] for k in names])
    b = np.array([xyz[rows["A", 2][k]] for k in names])
    # Independent rigid fit of output base atoms, not the construction helper.
    u, _, vt = np.linalg.svd((a - a.mean(0)).T @ (b - b.mean(0)))
    rotation = u @ vt
    shift = b.mean(0) - a.mean(0) @ rotation
    angle = np.degrees(np.arccos((np.trace(rotation) - 1) / 2))
    values, vectors = np.linalg.eig(rotation.T)
    axis = vectors[:, np.argmin(abs(values - 1))].real
    rise = abs(axis @ shift)
    expected = (
        (3.129152642490139, 37.444813065373374)
        if kind == "DNA"
        else (3.0054360807126237, 32.77842139202005)
    )
    assert (rise, angle) == pytest.approx(expected, abs=1e-7)
    data = json.loads(files("vibeview").joinpath("duplex_templates.json").read_text())[kind]
    ref = data["base_pairs"]["AT" if kind == "DNA" else "GC"]["pair"][0]
    reference = np.array([ref[k] for k in names])
    np.testing.assert_allclose(
        np.linalg.norm(a[:, None] - a[None, :], axis=-1),
        np.linalg.norm(reference[:, None] - reference[None, :], axis=-1),
        atol=1e-7,
    )


def test_explicit_choice_validation_and_contact_failure():
    assert complement("AAGC", "DNA") == "GCTT"
    assert complement("AAGC", "RNA") == "GCUU"
    for sequence, kind in [("AX", "DNA"), ("AT", "RNA"), ("", "DNA"), ("A", "protein")]:
        with pytest.raises(ValueError):
            nucleic_acid(sequence, kind, strands="duplex")
    with pytest.raises(ValueError, match="single or duplex"):
        nucleic_acid("AC", "DNA", strands="automatic")
    mol = nucleic_acid("A", "DNA", strands="duplex")
    conf = mol.GetConformer()
    for atom in mol.GetAtoms():
        if atom.GetPDBResidueInfo().GetChainId() == "B":
            p = conf.GetAtomPosition(atom.GetIdx())
            conf.SetAtomPosition(atom.GetIdx(), (p.x + 10, p.y, p.z))
    with pytest.raises(ValueError, match="Watson"):
        pairing_contacts(mol, "A")


@pytest.mark.parametrize("fails", [False, True])
def test_panel_duplex_background_snapshots_inputs_and_finishes(monkeypatch, fails):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock
    from uuid import uuid4

    from trame.app import get_server

    from vibeview import builder_panel
    from vibeview.app import helpers

    molecule = nucleic_acid("A", "DNA", strands="duplex")
    construct = (
        Mock(side_effect=ValueError("geometry refusal")) if fails else Mock(return_value=molecule)
    )
    monkeypatch.setattr(builder_panel, "nucleic_acid", construct)
    monkeypatch.setattr(builder_panel, "refresh_catalog", lambda viewer: None)
    pending = []
    monkeypatch.setattr(helpers, "_spawn_task", pending.append)
    server = get_server("duplex-" + uuid4().hex, client_type="vue3")
    viewer = SimpleNamespace(
        server=server, ctrl=server.controller, files=SimpleNamespace(_adopt_reader=Mock())
    )
    builder_panel.bind(viewer)
    server.state.mb_sequence_kind = "DNA"
    server.state.mb_strands = "duplex"
    server.state.mb_sequence = "A"
    server.controller.mb_sequence_build()
    assert server.state.mb_sequence_running
    server.state.mb_sequence = "C"
    asyncio.run(pending.pop())
    construct.assert_called_once_with("A", "DNA", strands="duplex")
    assert not server.state.mb_sequence_running
    if fails:
        viewer.files._adopt_reader.assert_not_called()
        assert "geometry refusal" in server.state.status_message
    else:
        result = viewer.files._adopt_reader.call_args.args[0]
        assert {a.chain_id for a in result.read_structure().atoms} == {"A", "B"}
        result.close()
