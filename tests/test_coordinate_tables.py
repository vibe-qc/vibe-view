"""Coordinate editor transactions, unit/refusal contracts and Gaussian inputs."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from vibeview.app.editing import EditingControllers
from vibeview.converters import gjf_to_qvf, xyz_to_qvf
from vibeview.coordinate_panel import (
    apply_rows,
    apply_zmatrix,
    generate_zmatrix,
    paste_xyz,
    refresh,
)
from vibeview.coordinate_tables import parse_rows, table_rows, xyz_rows
from vibeview.qvf import QVFReader
from vibeview.units import BOHR_TO_ANGSTROM


class State(SimpleNamespace):
    def dirty(self, *args):
        pass


@pytest.fixture
def viewer(monkeypatch):
    from vibeview.app import helpers

    reader = QVFReader(xyz_to_qvf(b"3\nwater\nO 0 0 0\nH 0 0 1\nH 1 0 0\n"))
    state = State(measure_unit="angstrom", coord_unit="angstrom", frozen_atoms=[0],
                  edit_history=[], edit_future=[], edit_selected=[], selected_atoms=[])
    result = SimpleNamespace(reader=reader, plotter=Mock(), server=SimpleNamespace(state=state))
    result.editing = EditingControllers(result)
    monkeypatch.setattr(result.editing, "_disable_live_opt", Mock())
    monkeypatch.setattr(result.editing, "_cancel_live_opt_intent", Mock())
    def rebuild(reader, plotter, positions, symbols=None, **kwargs):
        reader.set_edit_overlay(np.asarray(positions), symbols,
                                bonds=(reader.read_structure().bonds
                                       if kwargs.get("bonds") == "preserve"
                                else kwargs.get("bonds")))
    monkeypatch.setattr(helpers, "_rebuild_structure_from_positions", rebuild)
    for name in ("_push_view", "_update_edit_highlights", "_update_frozen_highlights"):
        monkeypatch.setattr(helpers, name, Mock())
    yield result
    reader.close()


def test_table_units_elements_freeze_and_undo(viewer):
    state = viewer.server.state
    state.measure_unit = "bohr"
    refresh(viewer)
    assert state.coord_rows[1]["z"] == pytest.approx(1/BOHR_TO_ANGSTROM)
    state.coord_rows[1]["z"] = 2
    state.coord_rows[1]["element"] = "F"
    state.coord_rows[1]["frozen"] = True
    apply_rows(viewer, state.coord_rows)
    assert len(state.edit_history) == 1
    assert viewer.reader.read_structure().atoms[1].symbol == "F"
    assert viewer.reader.read_structure().atoms[1].position[2] == pytest.approx(2*BOHR_TO_ANGSTROM)
    assert state.frozen_atoms == [0, 1]
    viewer.editing.edit_undo()
    assert viewer.reader.read_structure().atoms[1].symbol == "H"
    assert state.frozen_atoms == [0]
    viewer.editing.edit_redo()
    assert viewer.reader.read_structure().atoms[1].symbol == "F"
    assert state.frozen_atoms == [0, 1]


def test_xyz_replace_append_single_transaction(viewer):
    state = viewer.server.state
    state.coord_xyz = "1\nhelium\nHe 2 3 4\n"
    paste_xyz(viewer, True)
    assert len(viewer.reader.read_structure().atoms) == 4
    assert state.frozen_atoms == [0]
    paste_xyz(viewer, False)
    assert len(state.edit_history) == 2
    assert len(viewer.reader.read_structure().atoms) == 1
    assert state.frozen_atoms == []
    viewer.editing.edit_undo()
    assert len(viewer.reader.read_structure().atoms) == 4
    assert state.frozen_atoms == [0]


@pytest.mark.parametrize("bad", ["NaN", "inf", "garbage"])
def test_invalid_table_is_atomic(viewer, bad):
    rows = table_rows(viewer.reader.read_structure())
    rows[1]["x"] = bad
    with pytest.raises(ValueError):
        apply_rows(viewer, rows)
    assert viewer.server.state.edit_history == []
    assert not viewer.reader.edited_structure_ids


def test_zmatrix_edit_and_refusal(viewer):
    state = viewer.server.state
    generate_zmatrix(viewer)
    assert "R4=" in state.coord_ztext
    apply_zmatrix(viewer)
    assert len(state.edit_history) == 1
    original = np.array([a.position for a in viewer.reader.read_structure().atoms])
    np.testing.assert_allclose(original, [[0,0,0], [0,0,1], [1,0,0]], atol=1e-10)
    state.coord_ztext = "O\nH 1 R\nH 1 R 2 90\n\nR=1.5"
    with pytest.raises(ValueError, match="frozen"):
        apply_zmatrix(viewer)
    assert len(state.edit_history) == 1
    state.frozen_atoms = []
    apply_zmatrix(viewer)
    coords = np.array([a.position for a in viewer.reader.read_structure().atoms])
    assert np.linalg.norm(coords[1]-coords[0]) == pytest.approx(1.5)
    assert len(state.edit_history) == 2


@pytest.mark.parametrize("block", ["O\nH 1 R\nH 1 R 2 A\n\nR=0.96\nA=104.5",
                                      "O\nH 1 0.96\nH 1 0.96 2 104.5"])
def test_gaussian_zmatrix_opens(block):
    source = f"%mem=1GB\n# HF/STO-3G\n\nwater\n\n0 1\n{block}\n\n".encode()
    with QVFReader(gjf_to_qvf(source)) as reader:
        coords = np.array([a.position for a in reader.read_structure().atoms])
        assert len(coords) == 3
        assert np.linalg.norm(coords[1]-coords[0]) == pytest.approx(0.96)
        assert reader.provenance["charge"] == 0


def test_xyz_schema_and_table_schema():
    with pytest.raises(ValueError):
        xyz_rows("1\na\nH 0 0 0\n1\nb\nH 1 1 1")
    with pytest.raises(ValueError):
        parse_rows([{"element": "He", "x": 0, "y": 0, "z": 0, "frozen": "false"}])
    assert xyz_rows("1\n\nHe 0 0 0\n")[0]["element"] == "He"


def test_zmatrix_table_variables_and_style_exports(viewer):
    from vibeview.coordinate_panel import apply_ztable, export_zmatrix, load_ztable
    from vibeview.zmatrix import parse_zmatrix

    state = viewer.server.state
    state.frozen_atoms = []
    state.coord_zstyle = "gaussian"
    state.coord_ztext = "O\nH 1 R\nH 1 R 2 A\n\nR=0.96\nA=104.5"
    load_ztable(viewer)
    assert state.coord_zrows[1]["c2"] == "R"
    state.coord_zdefinitions = "R=1.1\nA=100"
    apply_ztable(viewer)
    assert len(state.edit_history) == 1
    for style in ("gaussian", "orca", "mopac"):
        state.coord_export_style = style
        export_zmatrix(viewer)
        symbols, coords = parse_zmatrix(state.coord_zexport, style).cartesian()
        assert symbols == ["O", "H", "H"]
        assert np.linalg.norm(coords[1]-coords[0]) == pytest.approx(1.1)


def test_coordinate_panel_templates():
    from trame.app import get_server
    from trame.widgets import html
    from trame.widgets import vuetify3 as v

    from vibeview.coordinate_panel import build_controls

    server = get_server("coordinate-template-test", client_type="vue3")
    with html.Div(trame_server=server) as root:
        build_controls(v, html, server.controller)
    markup = root.html
    assert 'v-model="row.x"' in markup
    assert 'v-model="row.frozen"' in markup
    assert 'v-model="row.c0"' in markup
    assert "coord_zdefinitions" in markup


@pytest.mark.parametrize("style", ["gaussian", "orca", "mopac"])
def test_standalone_zmatrix_import(style):
    from vibeview.converters import detect_format, zmatrix_to_qvf
    from vibeview.zmatrix import parse_zmatrix

    matrix = parse_zmatrix("O\nH 1 1\nH 1 1 2 90")
    assert detect_format("water.zmat") == "zmatrix"
    with QVFReader(zmatrix_to_qvf(matrix.text(style).encode())) as reader:
        assert len(reader.read_structure().atoms) == 3


def test_gaussian_variable_and_constant_sections():
    text = (b"# HF/Gen\n\nwater\n\n0 1\nO\nH 1 R\nH 1 R 2 A\n\nVariables:\nR=1\n"
            b"\nConstants:\nA=90\n\nO H 0\nSTO-3G\n****\n")
    with QVFReader(gjf_to_qvf(text)) as reader:
        assert len(reader.read_structure().atoms) == 3


def test_apply_ztable_uses_the_client_rows(viewer):
    import copy

    from vibeview.coordinate_panel import apply_ztable, load_ztable

    viewer.server.state.frozen_atoms = []
    viewer.server.state.coord_zstyle = "gaussian"
    viewer.server.state.coord_ztext = "O\nH 1 R\nH 1 R 2 A\n\nR=1.0\nA=90"
    load_ztable(viewer)
    rows = copy.deepcopy(viewer.server.state.coord_zrows)
    definitions = viewer.server.state.coord_zdefinitions
    # Client payload differs from the stale server's table. Numeric distance
    # replaces its symbolic reference while preserving ordered real atoms.
    rows[1]["c2"] = "1.4"
    apply_ztable(viewer, rows, definitions)
    positions = np.array([a.position for a in viewer.reader.read_structure().atoms])
    assert np.linalg.norm(positions[1] - positions[0]) == pytest.approx(1.4)


def test_cartesian_controller_uses_client_payload():
    import copy

    from vibeview.app import create_app

    with QVFReader(xyz_to_qvf(b"2\nHF\nH 0 0 0\nF 0 0 1\n")) as reader:
        app = create_app(reader)
        rows = copy.deepcopy(app.state.coord_rows)
        rows[0]["x"] = "0.25"
        assert app.state.coord_rows[0]["x"] == 0
        app.controller.coord_apply(rows)
        assert reader.read_structure().atoms[0].position[0] == pytest.approx(.25)
        assert len(app.state.edit_history) == 1


def test_invalid_client_ztable_preserves_text_and_geometry(viewer):
    from vibeview.coordinate_panel import apply_ztable

    viewer.server.state.coord_ztext = "unchanged"
    viewer.server.state.coord_zstyle = "gaussian"
    original = np.array([a.position for a in viewer.reader.read_structure().atoms])
    with pytest.raises(ValueError):
        apply_ztable(viewer, [{f"c{i}": "bad" for i in range(10)}], "")
    assert viewer.server.state.coord_ztext == "unchanged"
    np.testing.assert_array_equal(
        [a.position for a in viewer.reader.read_structure().atoms], original
    )
    assert viewer.server.state.edit_history == []
