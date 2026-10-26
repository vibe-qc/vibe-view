"""``volume.series`` sections stepped like trajectory frames (#158)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from click.testing import CliRunner

from tests._qvf_corpus import qvf_corpus_dir, requires_qvf_corpus
from tests.conftest import build_qvf
from vibeview.cli import main
from vibeview.qvf import QVFError, QVFReader
from vibeview.renderers.palette import FRAME_VOLUME, ISO_NEGATIVE, ISO_POSITIVE
from vibeview.renderers.volume_series import (
    SERIES_PREFIX,
    describe,
    frame_atoms,
    is_signed,
    opening_isovalue,
    slab_label,
    slab_surfaces,
    trajectory_frame,
)

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

_N = 9
_STEP = 0.5  # bohr
_ORIGIN = [-2.0, -2.0, -2.0]
_HALF = [0.35, 0.45, 0.55]  # Å, half the H–H distance of each trajectory frame


def _gaussians(signed: bool = False) -> np.ndarray:
    axis = np.asarray(_ORIGIN[0]) + np.arange(_N) * _STEP
    x, y, z = np.meshgrid(axis, axis, axis, indexing="ij")
    slabs = [np.exp(-(x**2 + y**2 + z**2) / (0.5 + 0.2 * s)) for s in range(3)]
    data = np.asarray(slabs, dtype=np.float32)
    if signed:
        data[1] -= 0.5 * data[1].max()
    return data


def _archive(
    tmp_path: Path,
    *,
    axis: dict[str, Any] | None = None,
    data: np.ndarray | None = None,
) -> Path:
    slabs = _gaussians() if data is None else data
    structure = {
        "atoms": [
            {"symbol": "H", "position": [0.0, 0.0, -0.35], "atomic_number": 1},
            {"symbol": "H", "position": [0.0, 0.0, 0.35], "atomic_number": 1},
        ],
    }
    coords = np.asarray(
        [[[0.0, 0.0, -h], [0.0, 0.0, h]] for h in _HALF], dtype=np.float64
    )
    grid = {
        "origin": _ORIGIN,
        "voxel_vectors": [[_STEP, 0, 0], [0, _STEP, 0], [0, 0, _STEP]],
        "shape": [_N, _N, _N],
    }
    series_axis = axis if axis is not None else {
        "kind": "frame", "values": [0, 1, 2],
        "labels": ["r = 0.70 Å", "r = 0.90 Å", "r = 1.10 Å"],
        "trajectory_ref": "traj",
    }
    return build_qvf(tmp_path / "series.qvf", [
        ("structure", "structure",
         {"structure": ("json", "structure.json", json.dumps(structure).encode())}),
        ("traj", "trajectory", {
            "metadata": ("json", "traj/meta.json",
                         json.dumps({"symbols": ["H", "H"], "n_frames": 3}).encode()),
            "coords": ("binary", "traj/coords.bin", coords.tobytes(), "float64", [3, 2, 3]),
        }),
        ("rho", "volume.series", {
            "grid": ("json", "rho/grid.json", json.dumps(grid).encode()),
            "data": ("binary", "rho/data.bin", slabs.tobytes(), "float32", list(slabs.shape)),
            "axis": ("json", "rho/axis.json", json.dumps(series_axis).encode()),
        }),
    ])


def _series(tmp_path: Path, **kwargs: Any) -> Any:
    with QVFReader(_archive(tmp_path, **kwargs)) as reader:
        return reader.read_volume_series("rho")


# ── reader ──────────────────────────────────────────────────────────────────


def test_reader_returns_slabs_grid_and_axis(tmp_path: Path) -> None:
    series = _series(tmp_path)
    assert series.data.shape == (3, _N, _N, _N)
    assert series.grid.shape == (_N, _N, _N)
    assert series.axis.kind == "frame"
    assert series.axis.values == [0.0, 1.0, 2.0]
    assert series.axis.trajectory_ref == "traj"


@pytest.mark.parametrize(
    ("axis", "message"),
    [
        ({"kind": "frame", "values": [0, 1]}, "2 values for 3 slabs"),
        ({"kind": "time", "values": [0, 1, 2], "labels": ["a"]}, "1 labels for 3 slabs"),
        ({"kind": "colour", "values": [0, 1, 2]}, "kind"),
    ],
)
def test_reader_refuses_a_malformed_axis(
    tmp_path: Path, axis: dict[str, Any], message: str
) -> None:
    # The vendored schema refuses some axes at open; the reader the rest.
    with pytest.raises(QVFError, match=message), QVFReader(
        _archive(tmp_path, axis=axis)
    ) as reader:
        reader.read_volume_series("rho")


# ── labels, frames, colours, isovalue ───────────────────────────────────────


@pytest.mark.parametrize(
    ("axis", "label"),
    [
        ({"kind": "time", "values": [0, 5, 10], "units": "fs"}, "t = 5 fs"),
        ({"kind": "state", "values": [1, 2, 3]}, "state 2"),
        ({"kind": "custom", "values": [0.1, 0.2, 0.3]}, "0.2"),
        ({"kind": "frame", "values": [0, 1, 2], "labels": ["a", "b", "c"]}, "b"),
    ],
)
def test_slab_label(tmp_path: Path, axis: dict[str, Any], label: str) -> None:
    assert slab_label(_series(tmp_path, axis=axis), 1) == label


def test_slabs_map_to_trajectory_frames(tmp_path: Path) -> None:
    path = _archive(tmp_path)
    with QVFReader(path) as reader:
        series = reader.read_volume_series("rho")
        for slab, half in enumerate(_HALF):
            assert trajectory_frame(series, slab) == slab
            atoms = frame_atoms(reader, series, slab)
            assert atoms is not None
            np.testing.assert_allclose(atoms[1][1], [0.0, 0.0, half])
    unreferenced = _series(tmp_path, axis={"kind": "state", "values": [1, 2, 3]})
    assert trajectory_frame(unreferenced, 0) is None


def test_colours_follow_the_sign_of_the_whole_series(tmp_path: Path) -> None:
    density = _series(tmp_path)
    assert not is_signed(density)
    assert [(s, c) for s, c, _m in slab_surfaces(density, 0, 0.3)] == [("0", FRAME_VOLUME)]
    signed = _series(tmp_path, data=_gaussians(signed=True))
    assert is_signed(signed)
    # Slab 0 has no negative value, but the series does: ± colours throughout.
    assert {c for _s, c, _m in slab_surfaces(signed, 0, 0.3)} <= {ISO_POSITIVE, ISO_NEGATIVE}
    assert [s for s, _c, _m in slab_surfaces(signed, 1, 0.3)] == ["pos", "neg"]


def test_opening_isovalue_and_range_errors(tmp_path: Path) -> None:
    series = _series(tmp_path)
    assert opening_isovalue(series, 0.05) == 0.05
    assert opening_isovalue(series, 5.0) == pytest.approx(0.5)
    with pytest.raises(ValueError, match="slab 3 is out of range"):
        slab_surfaces(series, 3, 0.1)
    assert "lower the isovalue" in describe(series, 0, 5.0, 0)
    assert describe(series, 1, 0.05, 1).startswith("slab 2 of 3, r = 0.90 Å, at 0.05")


# ── browser ─────────────────────────────────────────────────────────────────


def _actors(plotter: Any, prefix: str) -> list[str]:
    return sorted(str(n) for n in plotter.actors if str(n).startswith(prefix))


def test_browser_steps_slabs_moves_atoms_and_clears(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path)))
    state, ctrl = app.state, app.controller
    viewer = ctrl.series_changed.func.__self__.viewer
    ctrl.activate_section("rho")
    assert state.series_section_id == "rho"
    assert (state.series_slab, state.series_n_slabs) == (0, 3)
    assert state.series_summary.startswith("slab 1 of 3, r = 0.70 Å")
    assert _actors(viewer.plotter, SERIES_PREFIX) == [f"{SERIES_PREFIX}surface_0"]
    assert state.structure_hidden  # the trajectory frame's atoms replace it
    assert _actors(viewer.plotter, "traj_atom_") == ["traj_atom_0", "traj_atom_1"]

    # Both sliders send both values with the trigger (#122).
    ctrl.series_changed(2, 0.1)
    assert state.series_slabs == {"rho": 2}
    assert state.series_summary.startswith("slab 3 of 3, r = 1.10 Å, at 0.1")
    centre = viewer.plotter.actors["traj_atom_1"].GetCenter()
    assert centre[2] == pytest.approx(_HALF[2], abs=1e-6)

    ctrl.activate_section("structure")
    assert state.series_section_id == ""
    assert not state.series_playing
    assert not _actors(viewer.plotter, SERIES_PREFIX)
    assert not _actors(viewer.plotter, "traj_atom_")
    assert not state.structure_hidden


def test_play_toggles_and_a_section_switch_stops_it(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path)))
    state, ctrl = app.state, app.controller
    ctrl.activate_section("rho")
    ctrl.series_play_toggle()
    assert state.series_playing
    ctrl.activate_section("structure")
    assert not state.series_playing


def test_bookmarks_keep_the_slab_and_isovalue(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path)))
    state, ctrl = app.state, app.controller
    ctrl.activate_section("rho")
    ctrl.series_changed(1, 0.2)
    ctrl.save_user_bookmark("slab-2")
    ctrl.series_changed(0, 0.4)
    ctrl.apply_user_bookmark("slab-2")
    assert state.series_slab == 1
    assert state.series_isovalue == pytest.approx(0.2)
    assert state.series_summary.startswith("slab 2 of 3")


# ── capture, batch, show, API ───────────────────────────────────────────────


def test_capture_cli_draws_a_chosen_slab(tmp_path: Path) -> None:
    out = tmp_path / "slab.png"
    result = CliRunner().invoke(
        main, ["capture", str(_archive(tmp_path)), "-s", "rho", "--frame", "2", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "slab 3 of 3, r = 1.10 Å" in result.output
    assert out.stat().st_size > 0


def test_capture_refuses_a_slab_out_of_range(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main, ["capture", str(_archive(tmp_path)), "-s", "rho", "--frame", "7",
               "-o", str(tmp_path / "x.png")],
    )
    assert result.exit_code == 1
    assert "slab 7 is out of range: the series has 3, numbered 0 to 2" in result.output


def test_batch_renders_the_first_slab(tmp_path: Path) -> None:
    from vibeview.batch import render_all_png

    (tmp_path / "gallery").mkdir()
    with QVFReader(_archive(tmp_path)) as reader:
        written = render_all_png(reader, tmp_path / "gallery", include_volumes=True)
    assert str(tmp_path / "gallery" / "rho.png") in written


def test_show_steps_slabs_with_their_geometry(tmp_path: Path) -> None:
    from vibeview.tui import scene as scenelib
    from vibeview.tui.show import render_section

    with QVFReader(_archive(tmp_path)) as reader:
        frames = scenelib.series_frames(reader, "rho")
        assert frames.shape == (3, 2, 3)
        np.testing.assert_allclose(frames[:, 1, 2], _HALF)
        assert scenelib.series_slab_meshes(reader, "rho", 2)
        assert render_section(reader, "rho", 40, 12, frame=2) is not None


def test_get_volume_reports_the_axis(tmp_path: Path) -> None:
    from vibeview.api import get_volume

    summary = get_volume(_archive(tmp_path), "rho")
    assert summary is not None
    assert summary["data_shape"] == [3, _N, _N, _N]
    assert summary["axis"]["kind"] == "frame"
    assert summary["axis"]["labels"][2] == "r = 1.10 Å"


@requires_qvf_corpus("volume_series.qvf")
def test_corpus_stretch_series_follows_its_trajectory() -> None:
    corpus = qvf_corpus_dir()
    assert corpus is not None
    with QVFReader(corpus / "volume_series.qvf") as reader:
        series = reader.read_volume_series("rho_frames")
        assert [trajectory_frame(series, s) for s in range(3)] == [0, 2, 4]
        atoms = frame_atoms(reader, series, 2)
    assert atoms is not None
    assert slab_label(series, 2) == "r = 1.10 Å"
    assert slab_surfaces(series, 2, opening_isovalue(series, 0.05))
