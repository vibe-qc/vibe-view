"""Sequence builders preserve sketch graph identity and atom ordering (#98)."""
from __future__ import annotations

import json

import numpy as np
import pytest

from vibeview.molecular_builder import rdkit
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import structure_archive
from vibeview.sequence_handoff import _plain_sequence, build_sequence_positions


def source(helm: str, *, identity: str | None = None) -> QVFReader:
    Chem, _ = rdkit()
    molecule = Chem.MolFromHELM(helm)
    smiles = Chem.MolToSmiles(molecule)
    ordered = Chem.AddHs(Chem.MolFromSmiles(smiles))
    builder = structure_archive(
        [{"symbol": a.GetSymbol(), "atomic_number": a.GetAtomicNum(), "position": [0, 0, 0]}
         for a in ordered.GetAtoms()], [False] * 3,
        bonds=[{"i": b.GetBeginAtomIdx(), "j": b.GetEndAtomIdx(),
                "order": b.GetBondTypeAsDouble()} for b in ordered.GetBonds()],
    )
    builder.manifest["sections"][0]["smiles"] = smiles
    builder.add_json_bytes("origin", "x_vibesketch.origin", "origin", json.dumps({
        "helm": helm, "helm_inchikey": identity or Chem.MolToInchiKey(molecule),
    }).encode())
    return QVFReader(builder.to_bytesio())


@pytest.mark.parametrize("helm", ["PEPTIDE1{A.G}$$$$", "RNA1{[dR](A)P.[dR](T)}$$$$"])
def test_existing_builder_coordinates_have_exact_source_mapping(helm):
    with source(helm) as reader:
        positions, provenance = build_sequence_positions(reader)
        assert positions.shape == (len(reader.read_structure().atoms), 3)
        assert np.isfinite(positions).all() and np.ptp(positions) > 1
        assert "starting model" in provenance["geometry"]
        # Computing a proposal does not overwrite the source or apply a partial edit.
        assert not reader.has_edit_overlay


def test_wrong_identity_refuses_without_editing():
    with source("PEPTIDE1{A.G}$$$$", identity="wrong") as reader:
        with pytest.raises(ValueError, match="InChIKey differs"):
            build_sequence_positions(reader)
        assert not reader.has_edit_overlay


@pytest.mark.parametrize("helm", [
    "PEPTIDE1{[dA].G}$$$$", "PEPTIDE1{C.C}$PEPTIDE1,PEPTIDE1,1:R3-2:R3$$$",
    "RNA1{R(A)P.[dR](T)}$$$$",
])
def test_modified_linked_or_mixed_sugars_keep_the_graph(helm):
    with pytest.raises(ValueError, match="original atom graph is unchanged"):
        _plain_sequence(helm)


def test_real_browser_controller_builds_and_keeps_undo():
    from vibeview.app import create_app

    with source("PEPTIDE1{A.G}$$$$") as reader:
        app = create_app(reader)
        assert app.state.sketch_sequence_available
        app.controller.build_sketch_sequence()
        assert reader.has_edit_overlay
        assert len(app.state.edit_history) == 1
        assert "graph checked" in app.state.status_message
