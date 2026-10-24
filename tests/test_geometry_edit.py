"""Rigid fragment parameter edits: chain, alternate side, frozen and ring cases."""

import numpy as np
import pytest

from vibeview.geometry_edit import parameter_value, set_parameter
from vibeview.qvf import Atom, StructureData


def chain():
    positions = [[0, 1, 0], [0, 0, 0], [1.5, 0, 0], [1.5, 1, 1], [2.5, 1, 1]]
    return StructureData(
        [Atom("C", np.array(p, dtype=float), 6) for p in positions],
        (False, False, False),
        None,
        [(i, i + 1, 1.0, (0, 0, 0)) for i in range(4)],
    )


@pytest.mark.parametrize(
    "ids,value", [([1, 2], 2.0), ([0, 1, 2], 120.0), ([0, 1, 2, 3], -80.0), ([0, 1, 2, 3], 179.0)]
)
@pytest.mark.parametrize("side", ["smaller", "other", "last"])
def test_exact_value_and_input_immutable(ids, value, side):
    s = chain()
    original = np.array([a.position for a in s.atoms])
    result = set_parameter(s, ids, value, side=side)
    assert parameter_value(result, ids) == pytest.approx(value, abs=1e-10)
    np.testing.assert_array_equal([a.position for a in s.atoms], original)


def test_smaller_side_and_other_preserve_internal_distances():
    s = chain()
    before = np.array([a.position for a in s.atoms])
    smaller = set_parameter(s, [1, 2], 2.0)
    np.testing.assert_array_equal(smaller[2:], before[2:])
    np.testing.assert_allclose(smaller[0] - smaller[1], before[0] - before[1])
    other = set_parameter(s, [1, 2], 2.0, side="other")
    np.testing.assert_array_equal(other[:2], before[:2])
    np.testing.assert_allclose(other[2] - other[4], before[2] - before[4])


def test_frozen_refuses_whole_transaction_and_can_choose_other_side():
    s = chain()
    with pytest.raises(ValueError, match="frozen"):
        set_parameter(s, [1, 2], 2.0, frozen=[0])
    result = set_parameter(s, [1, 2], 2.0, side="other", frozen=[0])
    np.testing.assert_array_equal(result[0], s.atoms[0].position)


@pytest.mark.parametrize("side", ["smaller", "other", "last"])
def test_ring_bond_refused_even_for_last_atom(side):
    s = chain()
    s.bonds.append((0, 4, 1.0, (0, 0, 0)))
    with pytest.raises(ValueError, match="Ring"):
        set_parameter(s, [1, 2], 2.0, side=side)


def test_frozen_rotation_axis_atoms_need_not_move():
    s = chain()
    set_parameter(s, [0, 1, 2, 3], 80.0, side="other", frozen=[1, 2])


@pytest.mark.parametrize("ids,value", [([0, 0], 1), ([0, 1], -1), ([0, 1, 2], 180), ([0, 9], 1)])
def test_invalid_parameters(ids, value):
    with pytest.raises(ValueError):
        set_parameter(chain(), ids, value)


def test_explicit_bond_orders_and_empty_bonds_roundtrip():
    import io

    from vibeview.geometry_edit import edited_bonds, geometry_archive
    from vibeview.qvf import QVFReader

    structure = chain()
    structure.bonds = edited_bonds(structure, 0, 1, 2)
    with QVFReader(io.BytesIO(geometry_archive(structure))) as reader:
        assert (0, 1, 2.0, (0, 0, 0)) in reader.read_structure().bonds
    structure.bonds = []
    with QVFReader(io.BytesIO(geometry_archive(structure))) as reader:
        assert reader.read_structure().bonds == []


def test_remove_bond_is_explicit_not_reinferred():
    from vibeview.bonds import structure_bonds
    from vibeview.geometry_edit import edited_bonds

    structure = chain()
    structure.bonds = edited_bonds(structure, 1, 2, 0)
    assert not any({i, j} == {1, 2} for i, j, _, _ in structure_bonds(structure))


def test_python_edits_preserve_bonds_and_export(tmp_path):
    import io

    from vibeview.api import export_geometry_qvf, set_bond, set_geometry_parameter
    from vibeview.export_cml import export_cml
    from vibeview.geometry_edit import geometry_archive
    from vibeview.qvf import QVFReader

    with QVFReader(io.BytesIO(geometry_archive(chain()))) as reader:
        set_bond(reader, 0, 1, 2)
        set_geometry_parameter(reader, [1, 2], 2.0)
        assert (0, 1, 2.0, (0, 0, 0)) in reader.read_structure().bonds
        set_bond(reader, 0, 1, 0)
        set_geometry_parameter(reader, [1, 2], 1.8)
        assert not any({b[0], b[1]} == {0, 1} for b in reader.read_structure().bonds)
        export_geometry_qvf(reader, tmp_path / "edited.qvf")
        export_cml(reader, str(tmp_path / "edited.cml"))
    with QVFReader(tmp_path / "edited.qvf") as saved:
        assert parameter_value(
            np.array([a.position for a in saved.read_structure().atoms]), [1, 2]
        ) == pytest.approx(1.8)
        assert len(saved.read_structure().bonds) == 3
    assert 'atomRefs2="a1 a2"' not in (tmp_path / "edited.cml").read_text()


def test_controller_parameter_and_bond_are_single_undo_transactions(monkeypatch):
    import io
    from types import SimpleNamespace
    from unittest.mock import Mock

    from vibeview.app import helpers
    from vibeview.app.editing import EditingControllers
    from vibeview.geometry_edit import geometry_archive
    from vibeview.qvf import QVFReader

    with QVFReader(io.BytesIO(geometry_archive(chain()))) as reader:
        state = SimpleNamespace(
            selected_atoms=[1, 2],
            geometry_parameter_value=2.0,
            geometry_parameter_side="smaller",
            frozen_atoms=[],
            measure_unit="angstrom",
            edit_mode=True,
            edit_selected=[0, 1],
            edit_bond_order=2.0,
            edit_history=[],
            edit_future=[],
            dirty=Mock(),
        )
        viewer = SimpleNamespace(reader=reader, server=SimpleNamespace(state=state), plotter=Mock())
        controller = EditingControllers(viewer)
        viewer.editing = controller
        stopped = Mock()
        monkeypatch.setattr(controller, "_disable_live_opt", stopped)
        monkeypatch.setattr(helpers, "_push_view", Mock())
        monkeypatch.setattr(helpers, "_update_pick_markers", Mock())
        monkeypatch.setattr(helpers, "_update_edit_highlights", Mock())
        monkeypatch.setattr(helpers, "_update_frozen_highlights", Mock())
        monkeypatch.setattr(helpers, "_reset_frozen", Mock())
        monkeypatch.setattr(controller, "_cancel_live_opt_intent", Mock())

        def rebuild(reader, plotter, positions, symbols=None, *, bonds="preserve", **kwargs):
            previous = reader.read_structure()
            reader.set_edit_overlay(
                positions,
                symbols or [a.symbol for a in previous.atoms],
                bonds=previous.bonds if bonds == "preserve" else bonds,
            )

        monkeypatch.setattr(helpers, "_rebuild_structure_from_positions", rebuild)
        controller.edit_set_parameter()
        assert len(state.edit_history) == 1
        controller.edit_set_bond()
        assert len(state.edit_history) == 2
        assert stopped.call_count == 2
        assert (0, 1, 2.0, (0, 0, 0)) in reader.read_structure().bonds
        controller.edit_undo()
        assert (0, 1, 1.0, (0, 0, 0)) in reader.read_structure().bonds
        controller.edit_redo()
        assert (0, 1, 2.0, (0, 0, 0)) in reader.read_structure().bonds
        before = len(state.edit_history)
        state.selected_atoms = [0, 1]
        state.frozen_atoms = [0, 1]
        state.geometry_parameter_value = 8
        controller.edit_set_parameter()
        assert len(state.edit_history) == before
        assert "frozen" in state.status_message


@pytest.mark.parametrize(
    "bond",
    [
        (0, 99, 1.0, (0, 0, 0)),
        (0, 0, 1.0, (0, 0, 0)),
        (0, 1, float("nan"), (0, 0, 0)),
        (0, 1, 1.0, (0.5, 0, 0)),
    ],
)
def test_invalid_overlay_bond_is_atomic(bond):
    import io

    from vibeview.geometry_edit import geometry_archive
    from vibeview.qvf import QVFReader

    with QVFReader(io.BytesIO(geometry_archive(chain()))) as reader:
        previous = reader.read_structure()
        revision = reader.geometry_revision
        with pytest.raises(ValueError, match="bond"):
            reader.set_edit_overlay(
                [a.position.tolist() for a in previous.atoms],
                [a.symbol for a in previous.atoms],
                bonds=[bond],
            )
        assert reader.geometry_revision == revision
        assert not reader.has_edit_overlay


def test_rebuild_preserves_explicit_removal_and_infers_only_new_atoms(monkeypatch):
    import io
    from types import SimpleNamespace
    from unittest.mock import Mock

    from vibeview.app import helpers
    from vibeview.geometry_edit import geometry_archive
    from vibeview.qvf import QVFReader

    structure = chain()
    structure.bonds = []
    with QVFReader(io.BytesIO(geometry_archive(structure))) as reader:
        monkeypatch.setattr(
            helpers,
            "_scene_hooks",
            lambda _: SimpleNamespace(
                invalidate_relocalization=None, refresh_relocalization=None, state=Mock()
            ),
        )
        monkeypatch.setattr(helpers, "_remove_static_structure", Mock())
        monkeypatch.setattr(helpers, "_draw_structure", Mock())
        positions = [a.position.tolist() for a in structure.atoms]
        helpers._rebuild_structure_from_positions(reader, Mock(), positions)
        assert reader.read_structure().bonds == []
        helpers._rebuild_structure_from_positions(
            reader, Mock(), positions + [[0, 2, 0]], ["C"] * 5 + ["H"]
        )
        assert all(max(b[0], b[1]) == 5 for b in reader.read_structure().bonds)
        assert reader.read_structure().bonds


def _hydroxyl_radical_cation():
    """OH, written with charge +1 and multiplicity 3 plus a stale method."""
    import io

    from vibeview.qvf_writer import structure_archive

    builder = structure_archive(
        [
            {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.0]},
            {"symbol": "H", "atomic_number": 1, "position": [0.97, 0.0, 0.0]},
        ],
        [False, False, False],
        bonds=[{"i": 0, "j": 1, "order": 1}],
    )
    builder.set_provenance({"charge": 1, "multiplicity": 3, "method": "B3LYP"})
    return io.BytesIO(builder.to_bytesio().getvalue())


def test_saved_geometry_keeps_charge_and_multiplicity(tmp_path):
    """#137: Save geometry (QVF) rebuilt the archive without the species' charge
    and spin, so an edited ion or radical came back unspecified."""
    import io

    from vibeview.api import export_geometry_qvf
    from vibeview.geometry_edit import geometry_archive
    from vibeview.qvf import QVFReader

    with QVFReader(_hydroxyl_radical_cation()) as source:
        saved = geometry_archive(source.read_structure(), provenance=source.provenance)
        exported = tmp_path / "edited.qvf"
        export_geometry_qvf(source, exported)
    for archive in (io.BytesIO(saved), exported):
        with QVFReader(archive) as reader:
            # The species' state stays; a stale method result does not.
            assert reader.provenance == {"charge": 1, "multiplicity": 3}
            assert len(reader.read_structure().atoms) == 2


@pytest.mark.parametrize(
    "provenance,expected",
    [
        (None, {}),
        ({}, {}),
        ({"charge": 0}, {"charge": 0}),
        ({"charge": -1.0, "multiplicity": 2.0}, {"charge": -1, "multiplicity": 2}),
        ({"charge": True, "multiplicity": 1.5}, {}),
        ({"charge": "n/a", "multiplicity": None}, {}),
    ],
)
def test_unstated_or_invalid_spin_state_is_not_invented(provenance, expected):
    import io

    from vibeview.geometry_edit import geometry_archive
    from vibeview.qvf import QVFReader

    with QVFReader(io.BytesIO(geometry_archive(chain(), provenance=provenance))) as reader:
        assert reader.provenance == expected


def test_save_geometry_button_keeps_charge_and_multiplicity(tmp_path):
    """The browser's Save geometry (QVF) download carries the source's state."""
    import base64
    import io

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    path = tmp_path / "oh.qvf"
    path.write_bytes(_hydroxyl_radical_cation().getvalue())
    reader = QVFReader(path)
    try:
        app = create_app([reader])
        # A file opened from disk also gets a copy beside it; that copy's
        # extension lookup had no "qvf" entry and raised KeyError (#137).
        app.controller.export_geometry("qvf")
        data = base64.b64decode(app.state.export_data.split(",", 1)[1])
        assert app.state.status_message == "Exported as QVF"
    finally:
        reader.close()
    beside = list(tmp_path.glob("oh_export_*.qvf"))
    assert len(beside) == 1
    for archive in (io.BytesIO(data), beside[0]):
        with QVFReader(archive) as saved:
            assert saved.provenance == {"charge": 1, "multiplicity": 3}
