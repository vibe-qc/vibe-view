"""Normal modes become exportable geometry edits, independent of animation."""

import base64
import json

import numpy as np
import pytest

from tests import test_builder_displacement as builder
from tests._qvf_builders import build_qvf

editor = builder.editor
positions = builder.positions

ATOMS = [
    {"symbol": "O", "atomic_number": 8, "position": [0., 0., 0.]},
    {"symbol": "H", "atomic_number": 1, "position": [0.8, 0.6, 0.]},
    {"symbol": "H", "atomic_number": 1, "position": [-0.8, 0.6, 0.]},
]
DISPLACEMENTS = np.zeros((5, 3, 3), dtype="<f8")
DISPLACEMENTS[0, 1] = [0.15, 0.2, 0.]  # length .25, not component max .2
DISPLACEMENTS[0, 2] = [-0.1, 0., 0.]
DISPLACEMENTS[1] = -2 * DISPLACEMENTS[0]
DISPLACEMENTS[3, 0, 0] = np.nan
DISPLACEMENTS[4, 0, 2] = 0.4
DISPLACEMENTS[4, 1, 1] = 0.2


@pytest.fixture
def water_qvf(tmp_path):
    structure = json.dumps({"atoms": ATOMS, "pbc": [False] * 3}).encode()
    metadata = json.dumps({"atoms": ATOMS, "frequencies": [-100., 1000., 0., 50., 880.]}).encode()
    zeros = [{**a, "position": [0., 0., 0.]} for a in ATOMS]
    legacy = json.dumps({"atoms": zeros, "frequencies": [-100., 1000., 0., 50., 880.]}).encode()
    displaced = [{**a, "position": [x + 2 for x in a["position"]]} for a in ATOMS]
    other = json.dumps({"atoms": displaced, "pbc": [False] * 3}).encode()
    return build_qvf(tmp_path / "modes.qvf", [
        ("structure", "structure", {"structure": ("json", "s.json", structure)}),
        ("other", "structure", {"structure": ("json", "o.json", other)}),
        *[(name, "vibrations", {
            "metadata": ("json", f"{name}.json", meta),
            "displacements": ("binary", f"{name}.bin", DISPLACEMENTS.tobytes(),
                              "float64", list(DISPLACEMENTS.shape)),
        }) for name, meta in [("modes", metadata), ("legacy", legacy)]],
    ], extra={"modes": {"structure_ref": "structure"},
              "legacy": {"structure_ref": "structure"}})


@pytest.mark.parametrize("section", ["modes", "legacy"])
def test_signed_mode_export_repeat_undo_and_reference(editor, section):
    reader, state, ctrl = editor
    before = positions(reader)
    raw = reader.read_vibrations(section).displacements.copy()
    state.selected_section = section
    state.vibration_mode = 0  # imaginary frequencies are valid directions
    state.vibration_phase = 2.1
    state.vibration_amplitude = 2.7  # animation settings do not set the edit
    state.vibration_playing = True
    state.live_opt_enabled = True
    state.edit_selected = [0]  # whole mode, not a truncated selection
    state.vibration_geometry_displacement = 0.1
    ctrl.edit_displace_mode()
    expected = before + np.array([[0., 0., 0.], [.06, .08, 0.], [-.04, 0., 0.]])
    np.testing.assert_allclose(positions(reader), expected)
    assert not state.vibration_playing and not state.live_opt_enabled
    assert state.selected_section == "structure" and state.edit_mode
    assert not state.structure_hidden
    assert len(state.edit_history) == 1
    assert not state.edit_selected
    ctrl.export_geometry("xyz")
    xyz = base64.b64decode(state.export_data.split(",", 1)[1]).decode()
    coords = [[float(x) for x in row.split()[1:]] for row in xyz.splitlines()[2:]]
    np.testing.assert_allclose(coords, expected, atol=5.1e-7)
    ctrl.preview_orca_input()
    rows = state.calc_orca_preview.split("* xyz 0 1\n")[1].splitlines()[:-1]
    np.testing.assert_allclose([[float(x) for x in r.split()[1:]] for r in rows], expected)
    ctrl.edit_undo()
    np.testing.assert_allclose(positions(reader), before)
    ctrl.edit_redo()
    np.testing.assert_allclose(positions(reader), expected)
    state.selected_section = section
    state.vibration_geometry_displacement = -0.1
    ctrl.edit_displace_mode()
    np.testing.assert_allclose(positions(reader), 2 * before - expected)
    assert len(state.edit_history) == 2
    np.testing.assert_array_equal(reader.read_vibrations(section).displacements, raw)
    np.testing.assert_array_equal(
        [a.position for a in reader.read_structure(apply_edits=False).atoms], before,
    )
    ctrl.edit_undo()
    np.testing.assert_allclose(positions(reader), expected)


@pytest.mark.parametrize("field,value,message", [
    ("vibration_geometry_displacement", 0, "signed peak"),
    ("vibration_geometry_displacement", 1.01, "signed peak"),
    ("vibration_geometry_displacement", float("nan"), "signed peak"),
    ("vibration_geometry_displacement", None, "Mode displacement:"),
    ("vibration_geometry_displacement", "bad", "Mode displacement:"),
    ("vibration_mode", -1, "valid normal mode"),
    ("vibration_mode", 5, "valid normal mode"),
    ("vibration_mode", 0.5, "valid normal mode"),
    ("vibration_mode", 2, "no nonzero"),
    ("vibration_mode", 3, "must be finite"),
    ("frozen_atoms", [1], "unfreeze"),
    ("selected_section", "structure", "select a Vibrations"),
])
def test_invalid_input_does_not_mutate(editor, field, value, message):
    reader, state, ctrl = editor
    before = positions(reader)
    state.selected_section = "modes"
    setattr(state, field, value)
    ctrl.edit_displace_mode()
    assert message in state.status_message
    np.testing.assert_array_equal(positions(reader), before)
    assert not state.edit_history


def test_other_structure_is_not_displaced(editor):
    reader, state, ctrl = editor
    reader.show_structure("other")
    before = positions(reader)
    state.selected_section = "modes"
    ctrl.edit_displace_mode()
    assert "associated" in state.status_message
    np.testing.assert_array_equal(positions(reader), before)
    assert not state.edit_history


def test_changed_atom_list_is_not_replaced(editor):
    reader, state, ctrl = editor
    before = positions(reader)
    reader.set_edit_overlay(before, ["C", "H", "H"])
    state.selected_section = "modes"
    ctrl.edit_displace_mode()
    assert "atom lists differ" in state.status_message
    assert reader.read_structure().atoms[0].symbol == "C"
    assert not state.edit_history


def test_mismatched_mode_equilibrium_is_rejected(editor, monkeypatch):
    reader, state, ctrl = editor
    data = reader.read_vibrations("modes")
    data.atoms[1].position[0] += 0.5
    monkeypatch.setattr(reader, "read_vibrations", lambda _: data)
    state.selected_section = "modes"
    ctrl.edit_displace_mode()
    assert "equilibrium does not match" in state.status_message
    assert not state.edit_history


def test_replaces_prior_coordinate_edit_with_undo(editor):
    reader, state, ctrl = editor
    before = positions(reader)
    reader.set_edit_overlay(before + 3, [a.symbol for a in reader.read_structure().atoms])
    state.selected_section = "modes"
    state.vibration_mode = 1
    ctrl.edit_displace_mode()
    np.testing.assert_allclose(
        positions(reader), before + [[0, 0, 0], [-.06, -.08, 0], [.04, 0, 0]],
    )
    ctrl.edit_undo()
    np.testing.assert_allclose(positions(reader), before + 3)


def test_selected_modes_sum_with_independent_signed_amplitudes(editor):
    reader, state, ctrl = editor
    before = positions(reader)
    state.selected_section = "modes"
    state.vibration_mode = 3  # invalid preview mode is not part of the selection
    state.vibration_displacement_modes = [0, 4]
    ctrl.edit_mode_amplitude(0, "0.10")
    ctrl.edit_mode_amplitude(4, "-0.20")
    ctrl.edit_mode_amplitude(3, "invalid unselected value")
    ctrl.edit_displace_mode()
    expected = before + [[0, 0, -.2], [.06, -.02, 0], [-.04, 0, 0]]
    np.testing.assert_allclose(positions(reader), expected, atol=1e-15)
    assert len(state.edit_history) == 1
    ctrl.export_geometry("xyz")
    xyz = base64.b64decode(state.export_data.split(",", 1)[1]).decode()
    coords = [[float(x) for x in row.split()[1:]] for row in xyz.splitlines()[2:]]
    np.testing.assert_allclose(coords, expected, atol=5.1e-7)
    ctrl.preview_orca_input()
    rows = state.calc_orca_preview.split("* xyz 0 1\n")[1].splitlines()[:-1]
    np.testing.assert_allclose([[float(x) for x in r.split()[1:]] for r in rows], expected)
    ctrl.edit_undo()
    np.testing.assert_allclose(positions(reader), before)
    ctrl.edit_redo()
    np.testing.assert_allclose(positions(reader), expected)
    # Applying again replaces the previous edit, rather than accumulating it.
    state.selected_section = "modes"
    state.vibration_displacement_modes = [0, 4]
    state.vibration_displacement_amplitudes = {"0": .1, "4": -.2}
    ctrl.edit_displace_mode()
    np.testing.assert_allclose(positions(reader), expected)


def test_mode_sum_is_not_renormalized(editor):
    reader, state, ctrl = editor
    before = positions(reader)
    state.selected_section = "modes"
    state.vibration_displacement_modes = [0, 1]
    state.vibration_displacement_amplitudes = {"0": .1, "1": -.2}
    ctrl.edit_displace_mode()
    np.testing.assert_allclose(
        positions(reader), before + [[0, 0, 0], [.18, .24, 0], [-.12, 0, 0]],
    )


@pytest.mark.parametrize("modes,amplitudes,message", [
    ([0, 4], {"0": .1, "4": float("nan")}, "signed peak"),
    ([0, 4], {"0": .1, "4": 1.1}, "signed peak"),
    ([0, 4], {"0": .1, "4": 0}, "signed peak"),
    ([0, 3], {}, "finite"),
    ([0, 2], {}, "nonzero"),
    ([0, 99], {}, "valid normal mode"),
    ([0, 0], {}, "only once"),
])
def test_invalid_combination_is_atomic(editor, modes, amplitudes, message):
    reader, state, ctrl = editor
    before = positions(reader)
    state.selected_section = "modes"
    state.vibration_displacement_modes = modes
    state.vibration_displacement_amplitudes = amplitudes
    ctrl.edit_displace_mode()
    assert message in state.status_message
    np.testing.assert_array_equal(positions(reader), before)
    assert not state.edit_history


def test_mode_selection_resets_with_section_state(editor):
    from vibeview.app.helpers import _clear_output_panels

    _, state, ctrl = editor
    state.vibration_displacement_modes = [0, 4]
    ctrl.edit_mode_amplitude(4, -.2)
    _clear_output_panels(state)
    assert state.vibration_displacement_modes == []
    assert state.vibration_displacement_amplitudes == {}


def test_served_controls_offer_selection_and_per_mode_amplitudes(editor):
    from trame.app import get_server

    from tests._served import served_document

    document = served_document(get_server(client_type="vue3"))
    assert 'label="Modes to displace"' in document
    assert 'v-for="mode in vibration_displacement_modes"' in document
    assert 'vibration_displacement_amplitudes[mode]' in document
