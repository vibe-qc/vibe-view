import re
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from vibeview.builder_panel import apply_structure, molecule_archive
from vibeview.fragment_catalog import FRAGMENTS, attach, fragment, search, thumbnail
from vibeview.qvf import QVFReader


def test_fragment_catalog_search_thumbnails_and_valid_geometry():
    assert len(FRAGMENTS) > 30
    assert search("cyclo") == [n for n in FRAGMENTS if "cyclo" in n]
    for name in FRAGMENTS:
        mol = fragment(name)
        assert np.isfinite(mol.GetConformer().GetPositions()).all()
        assert thumbnail(name).startswith("data:image/svg+xml;base64,")
    assert not search("nonexistent fragment")


def test_attachment_replaces_two_hydrogens_preserves_host_and_connectivity():
    with QVFReader(molecule_archive(fragment("methane / methyl"), "fixture")) as reader:
        s = reader.read_structure()
        result = attach(s, 0, "methane / methyl")
        assert [a.symbol for a in result.atoms].count("C") == 2
        assert [a.symbol for a in result.atoms].count("H") == 6
        np.testing.assert_array_equal(result.atoms[0].position, s.atoms[0].position)
        assert len(result.bonds) == 7
        assert len(s.atoms) == 5


def test_geometry_transaction_and_frozen_refusal(monkeypatch):
    from vibeview.app import helpers

    with QVFReader(molecule_archive(fragment("methane / methyl"), "fixture")) as reader:
        state = SimpleNamespace(frozen_atoms=[], edit_selected=[0], selected_atoms=[0])
        viewer = SimpleNamespace(
            server=SimpleNamespace(state=state), reader=reader, plotter=Mock(), editing=Mock()
        )
        rebuild = Mock()
        monkeypatch.setattr(helpers, "_rebuild_structure_from_positions", rebuild)
        monkeypatch.setattr(helpers, "_update_edit_highlights", Mock())
        monkeypatch.setattr(helpers, "_push_view", Mock())
        result = attach(reader.read_structure(), 0, "methane / methyl")
        apply_structure(viewer, result, topology=True)
        viewer.editing._record_undo.assert_called_once()
        assert rebuild.call_args.kwargs["bonds"] == result.bonds
        state.frozen_atoms = [0]
        with pytest.raises(ValueError, match="Unfreeze"):
            apply_structure(viewer, result, topology=True)
        viewer.editing._record_undo.assert_called_once()


def test_bond_rotation_keeps_the_bond_selected_and_uses_the_released_angle(monkeypatch):
    """#131 acceptance: a rotation cleared the selection, so each further
    typed or dragged step needed a reselect; the slider now sends its value."""
    from vibeview.app import helpers
    from vibeview.builder_panel import rotate

    with QVFReader(molecule_archive(fragment("ethane / ethyl"), "fixture")) as reader:
        s = reader.read_structure()
        carbons = [i for i, a in enumerate(s.atoms) if a.symbol == "C"]
        h_on = {c: next(j for j, a in enumerate(s.atoms) if a.symbol == "H"
                        and np.linalg.norm(a.position - s.atoms[c].position) < 1.2)
                for c in carbons}
        state = SimpleNamespace(frozen_atoms=[], edit_selected=list(carbons),
                                selected_atoms=[], mb_angle=0)
        viewer = SimpleNamespace(
            server=SimpleNamespace(state=state), reader=reader, plotter=Mock(), editing=Mock()
        )
        positions = [s]

        def rebuild(reader, plotter, xyz, symbols, bonds=None):
            positions.append(np.asarray(xyz))

        def torsion(xyz):
            a, b, c, d = (np.asarray(xyz[i]) for i in
                          (h_on[carbons[0]], carbons[0], carbons[1], h_on[carbons[1]]))
            b1, b2, b3 = b - a, c - b, d - c
            n1, n2 = np.cross(b1, b2), np.cross(b2, b3)
            m = np.cross(n1, b2 / np.linalg.norm(b2))
            return np.degrees(np.arctan2(m @ n2, n1 @ n2))

        monkeypatch.setattr(helpers, "_rebuild_structure_from_positions", rebuild)
        highlights = Mock()
        monkeypatch.setattr(helpers, "_update_edit_highlights", highlights)
        monkeypatch.setattr(helpers, "_push_view", Mock())
        start = torsion([a.position for a in s.atoms])

        rotate(viewer, 30)        # slider release: its own value, mb_angle still 0
        assert state.edit_selected == carbons
        assert highlights.call_args.args[2] == carbons
        viewer.reader = SimpleNamespace(read_structure=lambda: _moved(s, positions[-1]))
        state.mb_angle = 45       # typed, then the button
        rotate(viewer)
        assert state.edit_selected == carbons
        turned = (torsion(positions[-1]) - start + 180) % 360 - 180
        assert abs(turned) == pytest.approx(75, abs=1e-6)


def _moved(structure, xyz):
    from vibeview.qvf import Atom, StructureData

    atoms = [Atom(a.symbol, np.asarray(p), a.atomic_number)
             for a, p in zip(structure.atoms, xyz, strict=True)]
    return StructureData(atoms, structure.pbc, structure.lattice_vectors, structure.bonds)


def test_builder_controls_emit_all_operations():
    from trame.app import get_server
    from trame.widgets import html
    from trame.widgets import vuetify3 as v

    from vibeview.builder_panel import build_controls

    server = get_server("builder-panel-test", client_type="vue3")
    with html.Div(trame_server=server) as root:
        build_controls(v, html, server.controller)
    markup = root.html
    for label in [
        "Molecular builder",
        "Rotate selected bond",
        "Fill open sites",
        "Build sequence",
        "Generate and rank conformers",
        "Attach at selected atom",
    ]:
        assert label in markup
    assert "mb_strands" in markup
    assert "mb_fragments" in markup
    assert "@end=" in markup
    # The slider sends the released angle with the trigger (#131).
    assert re.search(r"@end=\"trigger\('[^']+', \[\$event\]\)\"", markup)


def test_sequence_box_starts_empty_strips_and_refuses_an_empty_sequence(monkeypatch):
    """#139: the box started as "AGSA", so typed input was appended to it."""
    from trame.app import get_server
    from trame.widgets import html
    from trame.widgets import vuetify3 as v

    from vibeview.builder_panel import bind, build_controls

    server = get_server("builder-sequence-" + __import__("uuid").uuid4().hex, client_type="vue3")
    adopted = []
    viewer = SimpleNamespace(
        server=server, ctrl=server.controller, reader=Mock(), plotter=Mock(),
        editing=Mock(), files=SimpleNamespace(_adopt_reader=adopted.append),
    )
    bind(viewer)
    state = server.state
    assert state.mb_sequence == ""
    with html.Div(trame_server=server) as root:
        build_controls(v, html, server.controller)
    assert 'placeholder="AGSA (peptide) or ACGT (DNA/RNA)"' in root.html

    state.mb_sequence = "  \n"
    server.controller.mb_sequence_build()
    assert state.status_message.startswith("Enter a one-letter sequence")
    assert not adopted

    state.mb_sequence_kind, state.mb_backbone = "peptide", "helix"
    state.mb_sequence = " AG\n"
    server.controller.mb_sequence_build()
    assert len(adopted) == 1
    assert state.status_message.startswith("Sequence built")
