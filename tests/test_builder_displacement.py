"""Builder displacement through the real edit overlay and export controllers."""

import base64

import numpy as np
import pytest


@pytest.fixture
def editor(water_qvf, monkeypatch):
    from trame.app import get_server

    import vibeview.app as appmod
    from vibeview.qvf import QVFReader

    plotters = []
    plotter_type = appmod.pv.Plotter

    def make_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        plotters.append(plotter)
        return plotter

    monkeypatch.setattr(appmod.pv, "Plotter", make_plotter)
    with QVFReader(water_qvf) as reader:
        appmod.create_app(reader)
        server = get_server(client_type="vue3")
        server.state.edit_mode = True
        try:
            yield reader, server.state, server.controller
        finally:
            for plotter in plotters:
                plotter.close()


def positions(reader):
    return np.array([a.position for a in reader.read_structure().atoms])


def test_displace_export_undo_redo_and_cancel(editor):
    reader, state, ctrl = editor
    before = positions(reader)
    state.live_opt_enabled = True
    state.edit_displacement = 0.01
    state.frozen_atoms = [0]
    ctrl.edit_displace()
    after = positions(reader)
    np.testing.assert_array_equal(after[0], before[0])
    lengths = np.linalg.norm(after[1:] - before[1:], axis=1)
    assert np.all((lengths > 0) & (lengths <= 0.01))
    assert not state.live_opt_enabled
    assert len(state.edit_history) == 1
    ctrl.export_geometry("xyz")
    xyz = base64.b64decode(state.export_data.split(",", 1)[1]).decode()
    coords = np.array([[float(x) for x in line.split()[1:]] for line in xyz.splitlines()[2:]])
    # XYZ output uses six decimal places.
    np.testing.assert_allclose(coords, after, atol=5.1e-7, rtol=0)
    ctrl.edit_undo()
    np.testing.assert_array_equal(positions(reader), before)
    ctrl.edit_redo()
    np.testing.assert_array_equal(positions(reader), after)


def test_only_selected_unfrozen_atoms_move(editor):
    reader, state, ctrl = editor
    before = positions(reader)
    state.edit_selected = [0, 1]
    state.frozen_atoms = [0]
    ctrl.edit_displace()
    after = positions(reader)
    np.testing.assert_array_equal(after[[0, 2]], before[[0, 2]])
    assert np.linalg.norm(after[1] - before[1]) > 0
    ctrl.edit_displace()
    assert len(state.edit_history) == 2


@pytest.mark.parametrize("value", [0, -0.01, 0.11, "bad", None, float("nan"), float("inf")])
def test_invalid_displacement_preserves_geometry_and_history(editor, value):
    reader, state, ctrl = editor
    before = positions(reader)
    state.edit_displacement = value
    ctrl.edit_displace()
    np.testing.assert_array_equal(positions(reader), before)
    assert not state.edit_history
    assert state.status_message.startswith("Displace:")


def test_all_frozen_is_noop(editor):
    reader, state, ctrl = editor
    before = positions(reader)
    state.frozen_atoms = list(range(len(before)))
    ctrl.edit_displace()
    np.testing.assert_array_equal(positions(reader), before)
    assert not state.edit_history
    assert "no unfrozen" in state.status_message
