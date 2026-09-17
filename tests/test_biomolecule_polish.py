"""Observable rendering and interaction contracts for biomolecule polish."""

from __future__ import annotations

import types
from contextlib import closing

import numpy as np
import pytest
import pyvista as pv

from tests.test_cartoon import _actor_dataset, _ideal_strand
from vibeview.qvf import Atom, StructureData
from vibeview.renderers.structure import (
    _add_cartoon,
    _biomolecule_atom_colors,
    _cartoon_ribbon_profile,
    _extrude_ribbon,
    hydrogen_bond_contacts,
)


def protein(n=12):
    atoms = [
        Atom(
            symbol="C",
            atomic_number=6,
            position=p,
            atom_name="CA",
            residue_name="ALA" if i % 2 else "ASP",
            chain_id="A",
            residue_seq=i + 1,
            b_factor=float(i),
        )
        for i, p in enumerate(_ideal_strand(n))
    ]
    return StructureData(atoms=atoms, pbc=(False, False, False), lattice_vectors=None, bonds=[])


def test_terminal_strand_is_not_truncated():
    assert protein().secondary_structure() == ["E"] * 12


@pytest.mark.parametrize("subtype,label", [("alpha", "H"), ("pi", "I"), ("3_10", "G")])
def test_supplied_subtypes_keep_coarse_api_and_change_profile(subtype, label):
    structure = protein()
    structure.supplied_secondary_structure = [
        {"type": "helix", "subtype": subtype, "chain": "A", "start_seq": 1, "end_seq": 12}
    ]
    assert structure.secondary_structure() == ["H"] * 12
    assert structure.secondary_structure(detailed=True) == [label] * 12
    width, _ = _cartoon_ribbon_profile(structure, "A", 12, 48)
    expected = {"H": 1.10, "I": 1.40, "G": 0.75}[label]
    np.testing.assert_allclose(width, expected)


def test_bridge_keeps_sheet_coarse_label_but_has_no_arrow():
    structure = protein()
    structure.supplied_secondary_structure = [
        {"type": "sheet", "subtype": "bridge", "chain": "A", "start_seq": 1, "end_seq": 12}
    ]
    assert structure.secondary_structure() == ["E"] * 12
    assert structure.secondary_structure(detailed=True) == ["B"] * 12
    width, _ = _cartoon_ribbon_profile(structure, "A", 12, 48)
    np.testing.assert_allclose(width, 0.55)


def test_rectangular_strands_and_independent_cap_normals():
    points = np.column_stack((np.zeros(5), np.zeros(5), np.arange(5)))
    side = np.tile([1.0, 0.0, 0.0], (5, 1))
    normal = np.tile([0.0, 1.0, 0.0], (5, 1))
    mesh = _extrude_ribbon(
        points, side, normal, np.ones(5), np.full(5, 0.2), rectangular=np.ones(5, dtype=bool)
    )
    ring = mesh.points[:40].reshape(5, 8, 3)
    np.testing.assert_allclose(np.abs(ring[:, :, 0]), 1)
    np.testing.assert_allclose(np.abs(ring[:, :, 1]), 0.2)
    normals = mesh.point_data["Normals"]
    np.testing.assert_allclose(normals[-16:-8], np.tile([0, 0, -1], (8, 1)))
    np.testing.assert_allclose(normals[-8:], np.tile([0, 0, 1], (8, 1)))
    assert np.dot(normals[1], normals[2]) == pytest.approx(0)


@pytest.mark.parametrize("mode", ["chain", "structure", "residue", "bfactor"])
def test_atom_palette_matches_ribbon_palette(mode):
    structure = protein()
    colors = _biomolecule_atom_colors(structure, mode)
    with closing(pv.Plotter(off_screen=True)) as plotter:
        _add_cartoon(plotter, structure, color_mode=mode)
        actor = plotter.actors["cartoon_chain_A"]
        mesh = _actor_dataset(actor)
        if mode == "chain":
            np.testing.assert_allclose(colors[0], actor.prop.color.float_rgb)
        else:
            for atom_index, color in zip(
                mesh["residue_atom_index"], mesh["cartoon_rgb"], strict=True
            ):
                np.testing.assert_allclose(np.array(colors[int(atom_index)]) * 255, color)


def test_cartoon_isolation_and_hide_do_not_connect_hidden_spans():
    structure = protein()
    with closing(pv.Plotter(off_screen=True)) as plotter:
        _add_cartoon(plotter, structure, selection="A/2-3,A/8-9", visibility="isolate")
        actors = [a for n, a in plotter.actors.items() if n.startswith("cartoon")]
        assert len(actors) == 2
        for actor in actors:
            indices = set(_actor_dataset(actor)["residue_atom_index"])
            assert indices <= {1, 2} or indices <= {7, 8}
        plotter.clear()
        _add_cartoon(plotter, structure, selection="A/5-7", visibility="hide")
        actors = [a for n, a in plotter.actors.items() if n.startswith("cartoon")]
        assert len(actors) == 2
        for actor in actors:
            assert not set(_actor_dataset(actor)["residue_atom_index"]) & {4, 5, 6}


def test_viewport_pick_uses_visible_ribbon_membership():
    from vibeview.app import _pick_residue

    structure = protein()
    state = types.SimpleNamespace(representation_style="cartoon")
    with closing(pv.Plotter(off_screen=True)) as plotter:
        _add_cartoon(plotter, structure, selection="A/6", visibility="isolate")
        mesh = _actor_dataset(plotter.actors["cartoon_chain_A"])
        assert _pick_residue(structure, plotter, state, mesh.points[0]) == ("A", 6)
        assert _pick_residue(structure, plotter, state, structure.atoms[0].position) is None


def test_hydrogen_contacts_require_explicit_h_and_angle():
    structure = StructureData(
        atoms=[
            Atom(symbol=s, atomic_number=z, position=np.array(p, dtype=float))
            for s, z, p in [
                ("O", 8, [0, 0, 0]),
                ("H", 1, [1, 0, 0]),
                ("O", 8, [2.8, 0, 0]),
                ("N", 7, [1, 2, 0]),
            ]
        ],
        pbc=(False, False, False),
        lattice_vectors=None,
        bonds=[],
    )
    assert hydrogen_bond_contacts(structure) == [(0, 1, 2)]
    structure.atoms.pop(1)
    assert hydrogen_bond_contacts(structure) == []


@pytest.mark.parametrize("helix_class,label", [(" 1", "H"), (" 3", "I"), (" 5", "G"), ("  ", "H")])
def test_pdb_helix_class_survives_import(helix_class, label):
    from vibeview.converters import pdb_to_qvf
    from vibeview.qvf import QVFReader

    header = list(" " * 80)
    for start, text in [
        (0, "HELIX "),
        (19, "A"),
        (21, "   1"),
        (31, "A"),
        (33, "   8"),
        (38, helix_class),
    ]:
        header[start : start + len(text)] = text
    records = ["".join(header)]
    for i, atom in enumerate(protein(8).atoms, 1):
        x, y, z = atom.position
        records.append(
            f"ATOM  {i:5d}  CA  ALA A{i:4d}    {x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00           C"
        )
    with QVFReader(pdb_to_qvf("\n".join(records).encode())) as reader:
        assert reader.read_structure().secondary_structure(detailed=True) == [label] * 8


def test_atom_visibility_filters_spheres_bonds_and_labels():
    from vibeview.renderers.structure import StructureRenderer

    structure = protein(8)
    structure.bonds = [(i, i + 1, 1.0) for i in range(7)]
    reader = types.SimpleNamespace(
        read_structure=lambda: structure, infer_bonds=lambda _: structure.bonds
    )
    renderer = StructureRenderer(types.SimpleNamespace(id="structure"), reader)
    with closing(pv.Plotter(off_screen=True)) as plotter:
        renderer.add_to_plotter(
            plotter,
            residue_selection="A/3-4",
            residue_visibility="isolate",
            show_labels=True,
            atom_color_mode="residue",
        )
        names = list(plotter.actors)
        assert {n.split("_")[1] for n in names if n.startswith("atom_") and n[5].isdigit()} == {
            "2",
            "3",
        }
        assert "bond_2_3" in names
        assert "bond_1_2" not in names
        assert "bond_3_4" not in names


def test_controller_selection_visibility_and_palette_survive_representation(tmp_path):
    from tests.test_cartoon import TestSelectionController

    app = TestSelectionController()._app(tmp_path)
    app.controller.set_residue_selection("A/3-6")
    app.controller.set_residue_visibility("isolate")
    app.controller.set_cartoon_color_mode("residue")
    app.controller.set_representation_style("space_filling")
    assert app.state.residue_visibility == "isolate"
    assert app.state.structure_color_mode == "residue"
    assert app.state.atom_color_mode == "residue"
    app.controller.set_residue_selection("")
    assert app.state.residue_visibility == "all"
    app.controller.set_residue_visibility("hide")
    assert app.state.residue_visibility == "all"
    assert "Select residues" in app.state.status_message


@pytest.mark.parametrize("batched", [False, True])
def test_material_replay_preserves_residue_bond_colours(batched, monkeypatch):
    from vibeview.app import _apply_scene_appearance
    from vibeview.renderers.structure import StructureRenderer

    structure = protein(8)
    structure.atoms[0].residue_name = "GLY"  # keep the batch's bond colours nonuniform
    structure.bonds = [(i, i + 1, 1.0) for i in range(7)]
    reader = types.SimpleNamespace(
        read_structure=lambda: structure, infer_bonds=lambda _: structure.bonds
    )
    renderer = StructureRenderer(types.SimpleNamespace(id="structure"), reader)
    monkeypatch.setattr("vibeview.renderers.structure._BATCH_BOND_THRESHOLD", 0 if batched else 999)
    state = types.SimpleNamespace(atom_color_mode="residue", material_preset="scientific")
    with closing(pv.Plotter(off_screen=True)) as plotter:
        renderer.add_to_plotter(plotter, atom_color_mode="residue")
        bonds = [actor for name, actor in plotter.actors.items() if name.startswith("bond")]
        before = [
            (actor.GetProperty().GetColor(), actor.GetMapper().GetScalarVisibility())
            for actor in bonds
        ]
        assert before
        _apply_scene_appearance(plotter, state)
        after = [
            (actor.GetProperty().GetColor(), actor.GetMapper().GetScalarVisibility())
            for actor in bonds
        ]
        for (colour_before, visible_before), (colour_after, visible_after) in zip(
            before, after, strict=True
        ):
            np.testing.assert_allclose(colour_after, colour_before, rtol=0, atol=1e-12)
            assert visible_after == visible_before
        if batched:
            assert all(visible for _, visible in after)


def test_pick_controller_selects_ribbon_residue(tmp_path):
    from tests.test_cartoon import TestSelectionController

    app = TestSelectionController()._app(tmp_path)
    app.controller.set_residue_pick_mode(True)
    # A known CA position in the fixture's first helical chain.
    from tests.test_cartoon import _ideal_helix

    app.controller.on_pick({"worldPosition": _ideal_helix(16)[5].tolist()})
    assert app.state.residue_selection == "A/6"


@pytest.mark.parametrize("dark", [True, False])
def test_scene_rebuild_keeps_background(dark):
    from vibeview.app import _rebuild_scene

    class Plotter:
        def clear(self):
            pass

        def set_background(self, color):
            self.background = color

        def view_isometric(self):
            pass

        def show_grid(self):
            pass

        def render(self):
            pass

    plotter = Plotter()
    state = types.SimpleNamespace(
        dark_background=dark,
        active_volume_id=None,
        background_override="#1a1a2e" if dark else "#f0f0f0",
        status_message="",
        raytrace_enabled=False,
    )
    _rebuild_scene(
        types.SimpleNamespace(sections=[]),
        plotter,
        types.SimpleNamespace(replication=(1, 1, 1)),
        state,
    )
    assert plotter.background == ("#1a1a2e" if dark else "#f0f0f0")


def test_export_without_running_loop_does_not_schedule_unawaited_coroutine(tmp_path, monkeypatch):
    import asyncio

    from tests.test_cartoon import TestSelectionController

    app = TestSelectionController()._app(tmp_path)
    monkeypatch.setattr("vibeview.app._save_export_disk", lambda *args: None)

    def no_running_loop():
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(asyncio, "get_running_loop", no_running_loop)
    calls = []
    monkeypatch.setattr(asyncio, "ensure_future", lambda coro: calls.append(coro))
    app.controller.export_geometry("xyz")
    assert app.state.export_ready
    assert app.state.export_filename.endswith(".xyz")
    assert not calls
