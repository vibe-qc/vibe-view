"""Edit-overlay regression tests.

Before the overlay, every editor operation in app.py read
``reader.read_structure()`` — the pristine archive — and
``_rebuild_structure_from_positions`` never wrote the new geometry back
anywhere. Sequential edits therefore did not compound: adding two atoms
left only the second one, deleting after adding operated on the file's
original atom list, and export/submit used the un-edited structure.

The fix records the current edited geometry on the reader
(``QVFReader.set_edit_overlay``) at the single scene-rebuild write point,
and ``read_structure()`` returns the overlay when set — so every
consumer (edit handlers, renderers, export, input generation) sees the
same current structure.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

showcase_qvf = (
    Path(__file__).resolve().parents[2] / "examples" / "vibe_view" / "output-nacl-showcase.qvf"
)


# ── QVFReader overlay unit behaviour ─────────────────────────────────────


def test_overlay_set_read_clear_roundtrip(sample_qvf):
    from vibeview.qvf import QVFReader

    with QVFReader(sample_qvf) as reader:
        original = reader.read_structure()
        assert not reader.has_edit_overlay

        positions = [a.position.tolist() for a in original.atoms] + [[5.0, 5.0, 5.0]]
        symbols = [a.symbol for a in original.atoms] + ["C"]
        reader.set_edit_overlay(positions, symbols)

        assert reader.has_edit_overlay
        edited = reader.read_structure()
        assert len(edited.atoms) == len(original.atoms) + 1
        assert edited.atoms[-1].symbol == "C"
        assert edited.atoms[-1].atomic_number == 6  # resolved from the symbol
        np.testing.assert_allclose(edited.atoms[-1].position, [5.0, 5.0, 5.0])
        # File bond indices are stale after an edit — auto-bonding applies.
        assert edited.bonds is None
        # pbc / lattice still come from the file.
        assert edited.pbc == original.pbc

        reader.clear_edit_overlay()
        assert not reader.has_edit_overlay
        restored = reader.read_structure()
        assert len(restored.atoms) == len(original.atoms)
        assert [a.symbol for a in restored.atoms] == [a.symbol for a in original.atoms]


def test_fresh_reader_has_no_overlay(sample_qvf):
    """A fresh reader (file switch / hot reload) must re-read the file."""
    from vibeview.qvf import QVFReader

    with QVFReader(sample_qvf) as r1:
        n = len(r1.read_structure().atoms)
        r1.set_edit_overlay([[0.0, 0.0, 0.0]] * (n + 3), ["C"] * (n + 3))
        assert len(r1.read_structure().atoms) == n + 3
    with QVFReader(sample_qvf) as r2:
        assert not r2.has_edit_overlay
        assert len(r2.read_structure().atoms) == n


def test_overlay_lattice_is_atomic_and_persists_across_atom_edits(showcase_qvf):
    """A periodic edit overlay owns its expanded lattice with its atoms."""
    from vibeview.qvf import QVFReader

    with QVFReader(showcase_qvf) as reader:
        original = reader.read_structure()
        scaled = np.asarray(original.lattice_vectors, dtype=float).copy()
        scaled[0] *= 2
        positions = [atom.position.tolist() for atom in original.atoms]
        symbols = [atom.symbol for atom in original.atoms]

        reader.set_edit_overlay(
            positions,
            symbols,
            lattice_vectors=scaled,
        )
        np.testing.assert_allclose(reader.read_structure().lattice_vectors, scaled)

        # A later atoms-only edit must not silently fall back to the archive
        # cell; it is still part of the same edited periodic structure.
        positions[0] = [0.1, 0.0, 0.0]
        reader.set_edit_overlay(positions, symbols)
        edited = reader.read_structure()
        np.testing.assert_allclose(edited.lattice_vectors, scaled)
        np.testing.assert_allclose(edited.atoms[0].position, positions[0])

        # Invalid cell metadata cannot partially replace otherwise-valid
        # atoms or the current lattice.
        with pytest.raises(ValueError, match="finite 3x3"):
            reader.set_edit_overlay(
                [[9.0, 9.0, 9.0]],
                ["C"],
                lattice_vectors=[[1.0, 0.0], [0.0, 1.0]],
            )
        unchanged = reader.read_structure()
        assert len(unchanged.atoms) == len(original.atoms)
        np.testing.assert_allclose(unchanged.lattice_vectors, scaled)

        reader.clear_edit_overlay()
        np.testing.assert_allclose(
            reader.read_structure().lattice_vectors,
            original.lattice_vectors,
        )


# ── Driven-controller compounding regression ─────────────────────────────


def test_sequential_edits_compound_and_undo_restores(showcase_qvf):
    """Two add-atom clicks must yield n+2 atoms (pre-fix: the second click
    re-read the pristine file and dropped the first atom), and undo must
    step back to n+1."""
    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(showcase_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        ctrl = server.controller

        n0 = len(reader.read_structure().atoms)
        server.state.edit_mode = True
        server.state.edit_selected = []
        server.state.edit_history = []
        server.state.edit_future = []
        server.state.edit_new_element = "N"

        # Clicks in empty space, far (>1.5 A) from every showcase atom.
        ctrl.on_pick({"worldPosition": [50.0, 50.0, 50.0]})
        assert len(reader.read_structure().atoms) == n0 + 1

        ctrl.on_pick({"worldPosition": [55.0, 55.0, 55.0]})
        edited = reader.read_structure()
        assert len(edited.atoms) == n0 + 2, (
            "sequential edits must compound — the second add re-read the "
            "pristine file and dropped the first added atom"
        )
        assert edited.atoms[-1].symbol == "N"

        ctrl.edit_undo()
        assert len(reader.read_structure().atoms) == n0 + 1
        ctrl.edit_redo()
        assert len(reader.read_structure().atoms) == n0 + 2
    finally:
        reader.close()


def test_file_switch_isolates_supercell_history_from_incoming_reader(
    showcase_qvf,
    monkeypatch,
):
    """Undo/redo and atom-indexed UI state must not cross file identities."""
    import vibeview.app as appmod
    from vibeview.qvf import QVFReader

    h2_qvf = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    monkeypatch.setattr(appmod.pv, "Plotter", create_plotter)
    readers = [QVFReader(showcase_qvf), QVFReader(h2_qvf)]
    try:
        app = appmod.create_app(readers)
        state, ctrl = app.state, app.controller
        ctrl.view_update = lambda *_args, **_kwargs: None

        state.edit_history = []
        state.edit_future = []
        state.build_supercell_nx = 2
        state.build_supercell_ny = 1
        state.build_supercell_nz = 1
        ctrl.build_supercell()
        assert state.edit_history

        # Seed every atom-indexed interaction surface so the switch proves it
        # invalidates more than the currently reported Undo stack.
        state.edit_future = list(state.edit_history)
        state.edit_selected = [0]
        state.frozen_atoms = [0]
        state.element_picker_open = True
        state.element_picker_target = "change"
        state.context_menu_show = True
        state.context_menu_atom_idx = 0
        state.hover_tooltip = "Na"
        state.hover_tooltip_visible = True

        incoming = readers[1].read_structure()
        expected_symbols = [atom.symbol for atom in incoming.atoms]
        expected_positions = np.asarray([atom.position for atom in incoming.atoms])
        expected_pbc = incoming.pbc
        expected_dim = incoming.dim
        assert incoming.lattice_vectors is None

        marked: list[str] = []
        state_cls = type(state)
        real_dirty = state_cls.dirty
        state_cls.dirty = lambda self, *keys: (
            marked.extend(keys),
            real_dirty(self, *keys),
        )[1]
        try:
            ctrl.switch_file(1)
        finally:
            state_cls.dirty = real_dirty

        assert state.edit_history == []
        assert state.edit_future == []
        assert {"edit_history", "edit_future"} <= set(marked)
        assert state.edit_selected == []
        assert state.frozen_atoms == []
        assert state.element_picker_open is False
        assert state.element_picker_target == "new"
        assert state.context_menu_show is False
        assert state.context_menu_atom_idx == -1
        assert state.hover_tooltip == ""
        assert state.hover_tooltip_visible is False

        ctrl.edit_undo()
        assert state.status_message == "Nothing to undo"
        ctrl.edit_redo()
        assert state.status_message == "Nothing to redo"
        unchanged = readers[1].read_structure()
        assert [atom.symbol for atom in unchanged.atoms] == expected_symbols
        np.testing.assert_allclose(
            [atom.position for atom in unchanged.atoms], expected_positions
        )
        assert unchanged.lattice_vectors is None
        assert unchanged.pbc == expected_pbc
        assert unchanged.dim == expected_dim
    finally:
        for reader in readers:
            reader.close()
        for plotter in created:
            plotter.close()


def test_periodic_supercell_scales_reader_render_cif_input_and_history(
    showcase_qvf,
    monkeypatch,
):
    """Build, render, export, input generation, undo, and redo share one cell."""
    import vibeview.app as appmod
    from vibeview.qvf import QVFReader

    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    monkeypatch.setattr(appmod.pv, "Plotter", create_plotter)

    def close_deferred(coroutine):
        # Export controllers schedule only a short-lived UI-flag reset. This
        # synchronous controller regression consumes the payload immediately,
        # so close that coroutine instead of requiring an event loop.
        coroutine.close()

    monkeypatch.setattr(appmod.asyncio, "ensure_future", close_deferred)
    reader = QVFReader(showcase_qvf)
    comparison_reader = QVFReader(showcase_qvf)
    try:
        server = appmod.create_app([reader, comparison_reader])
        state, ctrl = server.state, server.controller
        ctrl.activate_section("structure")
        plotter = created[-1]

        original = reader.read_structure()
        original_lattice = np.asarray(original.lattice_vectors, dtype=float)
        expected_lattice = original_lattice.copy()
        expected_lattice[0] *= 2

        state.edit_history = []
        state.edit_future = []
        # A physical build supersedes display-only tiling; retaining this 2x
        # hint would duplicate the already-expanded overlay on any later
        # canonical scene rebuild.
        ctrl.update_replication(2, 1, 1)
        state.build_supercell_nx = 2
        state.build_supercell_ny = 1
        state.build_supercell_nz = 1
        ctrl.build_supercell()

        built = reader.read_structure()
        assert (
            state.replication_nx,
            state.replication_ny,
            state.replication_nz,
        ) == (1, 1, 1)
        assert len(built.atoms) == 2 * len(original.atoms)
        assert built.pbc == original.pbc
        assert built.dim == original.dim
        np.testing.assert_allclose(built.lattice_vectors, expected_lattice)
        fractional = np.asarray([atom.position for atom in built.atoms]) @ np.linalg.inv(
            built.lattice_vectors
        )
        assert np.all(fractional >= -1.0e-12)
        assert np.all(fractional < 1.0 + 1.0e-12)

        # The rebuilt scene must show the expanded a edge, not remove the
        # unit-cell wireframe or redraw the archive cell.
        cell_bounds = plotter.actors["cell_0_1"].GetBounds()
        assert cell_bounds[1] == pytest.approx(expected_lattice[0, 0])

        built_atom_actor_count = sum(
            str(name).startswith("structure_atom_") for name in plotter.actors
        )
        assert built_atom_actor_count == len(built.atoms)

        # Compare mode must hide the manual edit renderer, then rebuild from
        # the active overlay rather than resurrecting or double-drawing the
        # pristine archive geometry on exit.
        ctrl.toggle_compare_mode(True)
        assert not any(
            str(name).startswith(
                ("atom_", "bond_", "bonds_", "cartoon_", "cell_", "structure_")
            )
            for name in plotter.actors
        )
        ctrl.toggle_compare_mode(False)
        assert not any(
            str(name).startswith("structure_") for name in plotter.actors
        )
        restored_atom_actor_count = sum(
            str(name).startswith("atom_")
            and not str(name).endswith("_labels")
            for name in plotter.actors
        )
        assert restored_atom_actor_count == len(built.atoms)
        assert plotter.actors["cell_0_1"].GetBounds()[1] == pytest.approx(
            expected_lattice[0, 0]
        )
        restored_actor_names = set(plotter.actors)
        ctrl.toggle_compare_mode(True)
        ctrl.toggle_compare_mode(False)
        assert set(plotter.actors) == restored_actor_names

        ctrl.set_representation_style("space_filling")
        rebuilt_atom_actor_count = sum(
            str(name).startswith("atom_")
            and not str(name).endswith("_labels")
            for name in plotter.actors
        )
        assert rebuilt_atom_actor_count == len(built.atoms)
        assert plotter.actors["cell_0_1"].GetBounds()[1] == pytest.approx(
            expected_lattice[0, 0]
        )

        ctrl.export_geometry("cif")
        cif = base64.b64decode(state.export_data.split(",", 1)[1]).decode()
        assert "_cell_length_a 11.2800" in cif

        ctrl.export_py()
        script = base64.b64decode(state.export_data.split(",", 1)[1]).decode()
        expected_a_bohr = expected_lattice[0, 0] / 0.529177210903
        assert "PeriodicSystem(" in script
        assert f"{expected_a_bohr:.6f}" in script

        ctrl.edit_undo()
        undone = reader.read_structure()
        assert len(undone.atoms) == len(original.atoms)
        np.testing.assert_allclose(undone.lattice_vectors, original_lattice)
        assert plotter.actors["cell_0_1"].GetBounds()[1] == pytest.approx(
            original_lattice[0, 0]
        )

        ctrl.edit_redo()
        redone = reader.read_structure()
        assert len(redone.atoms) == len(built.atoms)
        np.testing.assert_allclose(redone.lattice_vectors, expected_lattice)
        assert plotter.actors["cell_0_1"].GetBounds()[1] == pytest.approx(
            expected_lattice[0, 0]
        )
    finally:
        reader.close()
        comparison_reader.close()
        for plotter in created:
            plotter.close()
