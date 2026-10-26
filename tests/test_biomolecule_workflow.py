"""Recorded public responses and exact/reference biomolecular geometry contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import URLError

import numpy as np
import pytest

from vibeview.biomolecule import mmcif_models, pdb_models, residues
from vibeview.converters import cif_to_qvf, detect_format, pdb_to_qvf
from vibeview.database_fetch import fetch_structure
from vibeview.qvf import QVFReader
from vibeview.sequence_alignment import aligned_pairs, superpose

FIXTURES = Path(__file__).parent / "fixtures"
BIO = FIXTURES / "biomolecule"


def atom(serial, name, residue, seq, pos, *, alt="", insertion="", hetero=False):
    return (
        f"{'HETATM' if hetero else 'ATOM':6s}{serial:5d} {name:^4s}{alt:1s}{residue:3s} A"
        f"{seq:4d}{insertion:1s}   {pos[0]:8.3f}{pos[1]:8.3f}{pos[2]:8.3f}"
        f"{1.0:6.2f}{20.0:6.2f}          {'O' if name == 'O' else 'C':>2s}"
    )


def recorded(url):
    if url == "https://files.rcsb.org/download/1CRN.cif":
        return (BIO / "1crn.cif").read_bytes()
    if url == "https://alphafold.ebi.ac.uk/api/prediction/P69905":
        return (BIO / "alphafold-P69905.json").read_bytes()
    if url == json.loads((BIO / "alphafold-P69905.json").read_text())[0]["cifUrl"]:
        return (BIO / "AF-P69905-F1.cif").read_bytes()
    if url in (
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/ethanol/SDF?record_type=3d",
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/702/SDF?record_type=3d",
    ):
        return (BIO / "ethanol.sdf").read_bytes()
    raise AssertionError(f"Unexpected network endpoint: {url}")


@pytest.mark.parametrize(
    "provider,identifier,n",
    [
        ("RCSB", "1crn", 327),
        ("AlphaFold", "P69905", 1077),
        ("PubChem name", "ethanol", 9),
        ("PubChem CID", "702", 9),
    ],
)
def test_explicit_recorded_fetch_and_open(provider, identifier, n):
    calls = []

    def transport(url):
        calls.append(url)
        return recorded(url)

    with QVFReader(fetch_structure(provider, identifier, transport=transport)) as reader:
        assert len(reader.read_structure().atoms) == n
        assert calls
        assert reader.provenance["database_fetch"]["responses"][0]["url"] == calls[0]


def test_mmcif_pdb_parity_and_identity_fit():
    with (
        QVFReader(cif_to_qvf(BIO / "1crn.cif")) as cif,
        QVFReader(pdb_to_qvf(FIXTURES / "inspection" / "1crn.pdb")) as pdb,
    ):
        a, b = cif.read_structure(), pdb.read_structure()
        assert [x.symbol for x in a.atoms] == [x.symbol for x in b.atoms]
        np.testing.assert_allclose(
            [x.position for x in a.atoms], [x.position for x in b.atoms], atol=1e-12
        )
        np.testing.assert_allclose(a.lattice_vectors, b.lattice_vectors, atol=1e-12)
        assert a.secondary_structure() == b.secondary_structure()
        assert a.supplied_secondary_structure and b.supplied_secondary_structure
        assert [r["key"] for r in residues(cif)] == [r["key"] for r in residues(pdb)]
        fit = superpose(cif, pdb, "A", "A")
        assert len(fit["aligned_pairs"]) == 46
        assert fit["rmsd_angstrom"] < 1e-10


def test_models_alternates_insertion_codes_and_groups():
    lines = []
    for model in (1, 2):
        lines += [
            f"MODEL     {model:4d}",
            atom(1, "CA", "ALA", 1, [model, 0, 0], alt="A"),
            atom(2, "CA", "ALA", 1, [model + 4, 0, 0], alt="B"),
            atom(3, "CA", "GLY", 1, [0, 1, 0], insertion="A"),
            atom(4, "C1", "LIG", 2, [0, 0, 2], hetero=True),
            atom(5, "O", "HOH", 3, [0, 0, 3], hetero=True),
            "ENDMDL",
        ]
    text = "\n".join(lines) + "\nEND\n"
    with QVFReader(pdb_models(text)) as reader:
        bio = reader.provenance["biomolecule"]
        assert len(bio["models"]) == 2
        assert all(len(m) == 5 for m in bio["models"])
        assert len(bio["variants"]) == 6
        assert len([s for s in reader.sections if s.kind == "trajectory"]) == 3
        trajectory = reader.read_trajectory("bio_ensemble_0")
        assert trajectory.coords.shape == (2, 4, 3)
        assert trajectory.coords[:, 0, 0].tolist() == [1, 2]
        assert reader.read_structure().atoms[0].position[0] == 1
        sid = next(
            k for k, v in bio["variants"].items() if v["model_index"] == 1 and v["altloc"] == "B"
        )
        assert reader.read_structure(sid).atoms[0].position[0] == 6
        rr = residues(reader)
        assert [r["label"] for r in rr][:2] == ["A/1 ALA", "A/1A GLY"]
        assert [r["kind"] for r in rr] == ["polymer", "polymer", "ligand", "water"]
        assert bio["source_text"] == text


def test_changed_model_identity_is_preserved_not_forced_into_frames():
    text = "MODEL        1\n" + atom(1, "CA", "ALA", 1, [0, 0, 0]) + "\nENDMDL\nMODEL        2\n"
    text += atom(1, "CA", "GLY", 1, [1, 0, 0]) + "\nENDMDL\n"
    with QVFReader(pdb_models(text)) as reader:
        assert len([s for s in reader.sections if s.kind == "structure"]) == 2
        assert not any(s.kind == "trajectory" for s in reader.sections)
        assert reader.provenance["biomolecule"]["warnings"]


def test_nonzero_analytical_reference_rmsd_and_pairs():
    points = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]], float)
    names = ["ALA", "GLY", "SER", "THR"]

    def archive(scale, shift):
        return pdb_models(
            "\n".join(
                atom(i + 1, "CA", name, i + 1, p * scale + shift)
                for i, (name, p) in enumerate(zip(names, points, strict=True))
            )
        )

    with QVFReader(archive(1.1, 4)) as mobile, QVFReader(archive(1.0, 0)) as reference:
        fit = superpose(mobile, reference, "A", "A")
        # Unit tetrahedron, isotropic 10% expansion: each residual norm sqrt(3)*0.1.
        assert fit["rmsd_angstrom"] == pytest.approx(np.sqrt(3) * 0.1, abs=1e-12)
        assert len(fit["aligned_pairs"]) == 4
        assert np.linalg.det(fit["rotation"]) == pytest.approx(1.0)
    assert aligned_pairs("ACDE", "ACXDE") == [(0, 0), (1, 1), (2, 3), (3, 4)]
    with pytest.raises(ValueError, match="four million"):
        aligned_pairs("A" * 3000, "A" * 3000)


def test_missing_gemmi_offline_and_endpoint_refusals(monkeypatch):
    monkeypatch.setitem(sys.modules, "gemmi", None)
    with pytest.raises(ImportError, match="biomolecule"):
        mmcif_models((BIO / "1crn.cif").read_text())
    from vibeview import database_fetch

    monkeypatch.setattr(database_fetch, "urlopen", Mock(side_effect=URLError("offline")))
    with pytest.raises(ValueError, match="offline"):
        database_fetch._download("https://files.rcsb.org/download/1CRN.cif")
    for provider, value in [
        ("RCSB", "file:///x"),
        ("PubChem name", "/private/file"),
        ("PubChem name", "a@b.example"),
    ]:
        with pytest.raises(ValueError):
            fetch_structure(provider, value, transport=lambda _: pytest.fail("unexpected request"))
    with pytest.raises(ValueError, match="approved"):
        fetch_structure(
            "AlphaFold", "P69905", transport=lambda _: b'[{"cifUrl":"https://example.org/x"}]'
        )
    assert detect_format("molecule.mmcif") == "mmcif"


def test_linked_selection_and_aligned_copy(monkeypatch):
    from vibeview.app import helpers
    from vibeview.biomolecule_panel import align_copy, select_residue

    with QVFReader(pdb_to_qvf(FIXTURES / "inspection" / "1crn.pdb")) as reader:
        state = SimpleNamespace(
            selected_atoms=[],
            edit_selected=[],
            bio_reference_file=0,
            bio_mobile_chain="A",
            bio_reference_chain="A",
        )
        viewer = SimpleNamespace(
            reader=reader,
            _all_readers=[reader],
            server=SimpleNamespace(state=state),
            plotter=Mock(),
            files=Mock(),
        )
        highlight = Mock()
        monkeypatch.setattr(helpers, "_update_edit_highlights", highlight)
        monkeypatch.setattr(helpers, "_push_view", Mock())
        select_residue(viewer, 0)
        assert state.selected_atoms == list(range(7))
        assert state.bio_rows[0]["selected"]
        highlight.assert_called_once()
        align_copy(viewer)
        new = viewer.files._adopt_reader.call_args.args[0]
        assert new.provenance["sequence_superposition"]["rmsd_angstrom"] < 1e-10
        assert not any(new.read_structure().pbc)
        assert len(residues(new)) == 46
        new.close()


def test_native_panel_template_arrays_are_vue_expressions():
    from trame.app import get_server
    from trame.widgets import html
    from trame.widgets import vuetify3 as v

    from vibeview.biomolecule_panel import build_controls
    from vibeview.input_writer_panel import build_controls as input_controls

    server = get_server("biomolecule-template-test", client_type="vue3")
    with html.Div(trame_server=server) as root:
        build_controls(v, html, server.controller)
        input_controls(v, html, server.controller)
    markup = root.html
    assert ":items=\"['RCSB'" in markup
    assert ":items=\"['single_point'" in markup
    assert (
        "row.index" in markup and "bio_select_residue" not in markup
    )  # controller becomes trigger
