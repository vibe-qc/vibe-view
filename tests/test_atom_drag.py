"""Atom selection, camera-plane translations and editor transaction boundaries."""

import base64

import numpy as np
import pytest

from tests.test_builder_displacement import editor as editor
from tests.test_builder_displacement import positions
from vibeview.app.atom_drag import plane_point, ray_atom


def event(phase, point=None, *, token=1, shift=False):
    ray = None if point is None else [[10, point[1], point[2]], [-10, point[1], point[2]]]
    return {"phase": phase, "token": token, "ray": ray, "normal": [-1, 0, 0], "shift": shift}


def test_frontmost_ray_hit_and_plane_projection():
    ray = np.array([[0., 0, 10], [0, 0, -10]])
    assert ray_atom(np.array([[0., 0, 0], [0, 0, 3]]), np.array([1., 1]), ray) == 1
    assert ray_atom(np.array([[3., 0, 0]]), np.array([1.]), ray) is None
    np.testing.assert_allclose(
        plane_point(ray, np.array([0, 0, 2]), np.array([0, 0, 1])), [0, 0, 2]
    )
    assert plane_point(ray, np.zeros(3), np.array([1, 0, 0])) is None


def test_single_drag_multiple_updates_one_undo_and_xyz(editor):
    reader, state, ctrl = editor
    state.edit_drag_mode = True
    before = positions(reader)
    ctrl.edit_atom_drag(event("begin", before[1]))
    assert state.edit_selected == [1]
    for displacement in (0.1, 0.2, 0.3):
        ctrl.edit_atom_drag(event("move", before[1] + [0, displacement, 0]))
    ctrl.edit_atom_drag(event("end", before[1] + [0, 0.4, 0.2]))
    expected = before.copy()
    expected[1] += [0, 0.4, 0.2]
    np.testing.assert_allclose(positions(reader), expected)
    assert len(state.edit_history) == 1
    ctrl.export_geometry("xyz")
    xyz = base64.b64decode(state.export_data.split(",", 1)[1]).decode()
    coordinates = [[float(v) for v in line.split()[1:]] for line in xyz.splitlines()[2:]]
    np.testing.assert_allclose(coordinates, expected, atol=5.1e-7)
    ctrl.edit_undo()
    np.testing.assert_allclose(positions(reader), before)
    viewer = ctrl.edit_atom_drag.func.__self__.viewer
    assert not any(str(name).startswith("edit_highlight_") for name in viewer.plotter.actors)
    ctrl.edit_redo()
    np.testing.assert_allclose(positions(reader), expected)


def test_shift_selection_group_translation_and_frozen_atoms(editor):
    reader, state, ctrl = editor
    state.edit_drag_mode = True
    before = positions(reader)
    for i in (0, 1):
        ctrl.edit_atom_drag(event("begin", before[i], token=i+1, shift=True))
    assert state.edit_selected == [0, 1]
    assert not state.edit_history
    ctrl.edit_atom_drag(event("begin", before[1], token=3))
    ctrl.edit_atom_drag(event("end", before[1] + [0, 0.3, 0.1], token=3))
    after = positions(reader)
    np.testing.assert_allclose(after[:2] - before[:2], [[0, .3, .1]] * 2)
    np.testing.assert_array_equal(after[2], before[2])
    assert np.linalg.norm(after[0] - after[1]) == pytest.approx(np.linalg.norm(before[0]-before[1]))
    state.frozen_atoms = [0]
    ctrl.edit_atom_drag(event("begin", after[1], token=4))
    ctrl.edit_atom_drag(event("end", after[1] + [0, 0.2, 0], token=4))
    np.testing.assert_array_equal(positions(reader)[0], after[0])
    np.testing.assert_allclose(positions(reader)[1], after[1] + [0, 0.2, 0])


def test_select_connected_all_clear(editor):
    reader, state, ctrl = editor
    state.edit_selected = [1]
    ctrl.edit_select_atoms("connected")
    assert state.edit_selected == [0, 1, 2]
    ctrl.edit_select_atoms("clear")
    assert state.edit_selected == []
    ctrl.edit_select_atoms("all")
    assert state.edit_selected == list(range(len(positions(reader))))
    assert not state.edit_history


@pytest.mark.parametrize("boundary", ["undo", "exit", "different_edit", "new_reader"])
def test_late_motion_cannot_overwrite_other_operations(editor, boundary):
    from vibeview.qvf import QVFReader

    reader, state, ctrl = editor
    viewer = ctrl.edit_atom_drag.func.__self__.viewer
    state.edit_drag_mode = True
    before = positions(reader)
    ctrl.edit_atom_drag(event("begin", before[1]))
    ctrl.edit_atom_drag(event("move", before[1] + [0, .1, 0]))
    replacement = None
    if boundary == "undo":
        ctrl.edit_undo()
    elif boundary == "exit":
        ctrl.toggle_edit_mode()
    elif boundary == "different_edit":
        ctrl.edit_displace()
    else:
        replacement = QVFReader(reader.path)
        viewer.reader = replacement
    expected = positions(viewer.reader).copy()
    try:
        ctrl.edit_atom_drag(event("end", before[1] + [0, 5, 0]))
        np.testing.assert_array_equal(positions(viewer.reader), expected)
    finally:
        if replacement:
            viewer.reader = reader
            replacement.close()


def test_click_empty_space_frozen_and_bad_rays_do_not_edit(editor):
    reader, state, ctrl = editor
    state.edit_drag_mode = True
    before = positions(reader)
    ctrl.edit_atom_drag(event("begin", [0, 50, 50]))
    ctrl.edit_atom_drag(event("end", [0, 60, 60]))
    state.frozen_atoms = [1]
    ctrl.edit_atom_drag(event("begin", before[1]))
    ctrl.edit_atom_drag(event("end", before[1] + [0, 2, 0]))
    state.frozen_atoms = []
    ctrl.edit_atom_drag(event("begin", before[1]))
    ctrl.edit_atom_drag(event("move", [0, float("nan"), 0]))
    ctrl.edit_atom_drag(event("end", before[1]))
    # A vtk.js click after releasing must not add/toggle atoms in drag mode.
    ctrl.on_pick({"worldPosition": [50, 50, 50]})
    np.testing.assert_array_equal(positions(reader), before)
    assert not state.edit_history


def test_auto_opt_pauses_until_release(editor, monkeypatch):
    reader, state, ctrl = editor
    viewer = ctrl.edit_atom_drag.func.__self__.viewer
    cancelled, scheduled = [], []
    monkeypatch.setattr(viewer.live_opt, "cancel", lambda: cancelled.append(True))
    monkeypatch.setattr(viewer.live_opt, "schedule", lambda *a, **k: scheduled.append(a[0]))
    state.edit_drag_mode = True
    state.live_opt_enabled = True
    before = positions(reader)
    ctrl.edit_atom_drag(event("begin", before[1]))
    assert cancelled
    ctrl.edit_atom_drag(event("move", before[1] + [0, .1, 0]))
    assert not scheduled
    ctrl.edit_atom_drag(event("end"))
    assert len(scheduled) == 1
    np.testing.assert_allclose(scheduled[0]["positions"], positions(reader))
