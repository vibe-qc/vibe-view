"""Animated terminal scenes keep section identities and frame cell context."""

from __future__ import annotations

import json
import sys
import zipfile
from dataclasses import replace

import numpy as np
import pytest

from tests.conftest import build_qvf
from vibeview.qvf import QVFReader, SHA256MismatchError
from vibeview.tui import scene as scenelib
from vibeview.tui.app import VibeViewTUI
from vibeview.tui.show import render_section


def _atoms(symbols):
    numbers = {"H": 1, "C": 6, "N": 7, "O": 8, "F": 9}
    return [
        {"symbol": symbol, "atomic_number": numbers[symbol], "position": [5 + 1.2 * i, 1, 0]}
        for i, symbol in enumerate(symbols)
    ]


def _binary(name, data):
    data = np.asarray(data, dtype="<f8")
    return "binary", name, data.tobytes(), "float64", list(data.shape)


def _archive(tmp_path, kind, primary=None, *, cell=False, lattice=None, two_sections=False):
    entries = []
    if primary is not None:
        structure = {"atoms": _atoms(primary)}
        if cell:
            structure.update(
                lattice_vectors=np.diag([10.0, 20.0, 30.0]).tolist(), pbc=[True, False, False]
            )
        entries.append(
            (
                "structure",
                "structure",
                {"structure": ("json", "structure.json", json.dumps(structure).encode())},
            )
        )
    for sid, symbols in [
        ("first", ["C", "O"]),
        *(([("second", ["N", "F"])]) if two_sections else []),
    ]:
        meta = {"atoms": _atoms(symbols)}
        coords = np.array([a["position"] for a in meta["atoms"]], dtype=float)
        if kind == "vibrations":
            meta["frequencies"] = [1000.0]
            members = {"displacements": _binary(f"{sid}-disp.bin", [[[0.1, 0, 0], [-0.1, 0, 0]]])}
        else:
            # Equal positions in both frames expose caches that omit the cell/frame.
            members = {"coords": _binary(f"{sid}-coords.bin", [coords, coords])}
        if lattice is not None:
            members["lattice"] = _binary(f"{sid}-lattice.bin", lattice)
            if np.asarray(lattice).ndim == 3:
                meta["dim_per_frame"] = [2, 1]
            else:
                meta["dim"] = 2
        members["metadata"] = "json", f"{sid}-meta.json", json.dumps(meta).encode()
        entries.append((sid, kind, members))
    return build_qvf(tmp_path / "frames.qvf", entries)


def _select(app, sid):
    app.selected = next(i for i, entry in enumerate(app.entries) if entry[0] == sid)


@pytest.mark.parametrize("kind", ["trajectory", "reaction.path", "vibrations"])
@pytest.mark.parametrize("primary", [None, ["H"], ["H", "H"]])
@pytest.mark.parametrize("frontend", ["show", "interactive"])
def test_actual_frame_callers_use_section_atoms(tmp_path, monkeypatch, kind, primary, frontend):
    monkeypatch.setitem(sys.modules, "vibeqc", None)
    path = _archive(tmp_path, kind, primary)
    captured = []
    render = scenelib.render

    def record(scene, canvas, camera):
        captured.append(scene)
        return render(scene, canvas, camera)

    monkeypatch.setattr(scenelib, "render", record)
    if frontend == "show":
        with QVFReader(path) as reader:
            grid = render_section(reader, "first", 80, 24, show_labels=True)
    else:
        app = VibeViewTUI(path)
        try:
            _select(app, "first")
            app.show_labels = True
            grid = app._build_3d(80, 24)
        finally:
            app.reader.close()
    assert grid is not None and np.any(grid.filled)
    assert "C1" in grid.to_plain() and "O2" in grid.to_plain()
    scene = captured[-1]
    assert scene.labels == ["C1", "O2"]
    np.testing.assert_allclose(scene.positions, [[5, 1, 0], [6.2, 1, 0]])
    np.testing.assert_allclose(scene.radii, [0.442, 0.3952])
    np.testing.assert_array_equal(scene.colors, [[144, 144, 144], [255, 13, 13]])
    assert len(scene.bond_starts) == 1


@pytest.mark.parametrize("kind", ["trajectory", "vibrations"])
def test_legacy_primary_cell_is_preserved_without_borrowing_atoms(tmp_path, kind):
    path = _archive(tmp_path, kind, ["H"], cell=True)
    app = VibeViewTUI(path)
    try:
        _select(app, "first")
        app.replication = [2, 4, 3]
        scene = app._current_scene()
        assert scene.labels == ["C1", "O2", "C1", "O2"]
        np.testing.assert_allclose(
            scene.positions, [[5, 1, 0], [6.2, 1, 0], [15, 1, 0], [16.2, 1, 0]]
        )
        np.testing.assert_array_equal(scene.line_starts, [[0, 0, 0], [10, 0, 0]])
        np.testing.assert_array_equal(scene.line_ends, [[10, 0, 0], [20, 0, 0]])
    finally:
        app.reader.close()


@pytest.mark.parametrize("kind", ["trajectory", "reaction.path", "vibrations"])
def test_equal_frame_coordinates_do_not_share_other_section_identities(tmp_path, kind):
    app = VibeViewTUI(_archive(tmp_path, kind, ["H", "H"], two_sections=True))
    try:
        _select(app, "first")
        first = app._current_scene()
        _select(app, "second")
        second = app._current_scene()
        assert first.labels == ["C1", "O2"]
        assert second.labels == ["N1", "F2"]
        assert second is not first
        np.testing.assert_array_equal(first.positions, second.positions)
    finally:
        app.reader.close()


def test_reaction_cell_tracks_current_frame_and_dim_without_changing_reader_data(tmp_path):
    lattice = np.array(
        [
            [[4.0, 1.0, 0.0], [0.0, 6.0, 0.0], [0.0, 0.0, 8.0]],
            [[8.0, 2.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 10.0]],
        ]
    )
    app = VibeViewTUI(_archive(tmp_path, "reaction.path", ["H", "H"], cell=True, lattice=lattice))
    try:
        _select(app, "first")
        data = app.reader.read_reaction_path("first")
        coords = data.coords.copy()
        first = app._current_scene()
        app.frame = 1
        second = app._current_scene()
        assert second is not first
        assert len(first.line_starts) == 4 and len(second.line_starts) == 1
        np.testing.assert_allclose(
            np.max(first.line_ends, axis=0), [2.645886054515, 3.175063265418, 0]
        )
        np.testing.assert_allclose(second.line_starts, [[0, 0, 0]])
        np.testing.assert_allclose(second.line_ends, [[4.233417687224, 0, 0]])
        np.testing.assert_array_equal(first.positions, second.positions)
        np.testing.assert_array_equal(app.reader.read_reaction_path("first").coords, coords)
        np.testing.assert_array_equal(app.reader.read_reaction_path("first").lattice, lattice)
        np.testing.assert_array_equal(
            app.reader.read_structure().lattice_vectors, np.diag([10, 20, 30])
        )
    finally:
        app.reader.close()


def test_molecular_reaction_never_inherits_primary_periodicity(tmp_path):
    app = VibeViewTUI(_archive(tmp_path, "reaction.path", ["H", "H"], cell=True))
    try:
        _select(app, "first")
        app.replication = [2, 3, 4]
        scene = app._current_scene()
        assert scene.labels == ["C1", "O2"]
        assert len(scene.line_starts) == 0
    finally:
        app.reader.close()


def test_frame_bonds_use_frame_positions_without_mutating_atoms(tmp_path, monkeypatch):
    with QVFReader(_archive(tmp_path, "trajectory", ["H", "H"], cell=True)) as reader:
        data = reader.read_trajectory("first")
        original_positions = np.array([atom.position.copy() for atom in data.atoms])
        monkeypatch.setattr(reader, "read_trajectory", lambda sid: data)
        scene = scenelib.frame_scene(reader, "first", np.array([[0.2, 0.4, 0], [9.2, 0.4, 0]]))
        np.testing.assert_allclose(scene.bond_starts, [[0.2, 0.4, 0]])
        np.testing.assert_allclose(scene.bond_ends, [[-0.8, 0.4, 0]], atol=1e-12)
        np.testing.assert_array_equal([atom.position for atom in data.atoms], original_positions)


def test_unrelated_explicit_bonds_remain_only_on_the_static_structure(tmp_path, monkeypatch):
    with QVFReader(_archive(tmp_path, "trajectory", ["H", "H"])) as reader:
        primary = replace(reader.read_structure(), bonds=[(0, 1, 3.0, (1, 0, 0))])
        monkeypatch.setattr(reader, "read_structure", lambda: primary)
        before = primary.bonds.copy()
        static = scenelib.structure_scene(reader)
        dynamic = scenelib.frame_scene(
            reader, "first", np.array([[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]])
        )
        assert len(static.bond_starts) == 1
        assert len(dynamic.bond_starts) == 0
        assert primary.bonds == before


@pytest.mark.parametrize("variable", [False, True])
def test_show_reaction_uses_selected_cell_and_masks_replication(tmp_path, monkeypatch, variable):
    first = [[4.0, 1.0, 0.0], [0.0, 6.0, 0.0], [0.0, 0.0, 8.0]]
    second = [[8.0, 2.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 10.0]]
    path = _archive(tmp_path, "reaction.path", lattice=[first, second] if variable else first)
    captured = []
    original = scenelib.render

    def record(scene, canvas, camera):
        captured.append(scene)
        return original(scene, canvas, camera)

    monkeypatch.setattr(scenelib, "render", record)
    with QVFReader(path) as reader:
        grid = render_section(reader, "first", 80, 24, frame=1, replication=(2, 3, 4))
        data = reader.read_reaction_path("first")
        edges = scenelib.reaction_path_cell(data, 1)
    assert grid is not None and np.any(grid.filled)
    scene = captured[-1]
    assert len(scene.positions) == (4 if variable else 12)
    assert len(scene.line_starts) == (2 if variable else 24)
    assert edges is not None and len(edges[0]) == (1 if variable else 4)
    np.testing.assert_allclose(
        scene.positions[2] - scene.positions[0],
        [4.233417687224, 0, 0] if variable else [0.529177210903, 3.175063265418, 0],
    )


@pytest.mark.parametrize("kind", ["trajectory", "vibrations"])
@pytest.mark.parametrize("frontend", ["show", "interactive"])
def test_corrupt_primary_context_is_not_silently_treated_as_absent(tmp_path, kind, frontend):
    path = _archive(tmp_path, kind, ["H", "H"], cell=True)
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    # Change valid JSON bytes while retaining the recorded digest.
    members["structure.json"] += b" "
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    if frontend == "show":
        with QVFReader(path) as reader, pytest.raises(SHA256MismatchError):
            render_section(reader, "first", 80, 24)
    else:
        app = VibeViewTUI(path)
        try:
            _select(app, "first")
            with pytest.raises(SHA256MismatchError):
                app._build_3d(80, 24)
        finally:
            app.reader.close()
