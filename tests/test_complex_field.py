"""``volume.complex`` sections drawn as a real, imaginary or phase-coloured part (#157)."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from click.testing import CliRunner

from tests._qvf_corpus import qvf_corpus_dir, requires_qvf_corpus
from tests.conftest import _water_structure_bytes, build_qvf
from vibeview.cli import main
from vibeview.qvf import QVFError, QVFReader
from vibeview.renderers.complex_field import (
    COMPLEX_PREFIX,
    complex_meshes,
    describe,
    max_modulus,
    opening_isovalue,
    part_field,
)

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

_ORIGIN = [-2.0, -2.0, -2.0]
_STEP = 0.4  # bohr
_N = 11


def _p_plus_one() -> np.ndarray:
    """(x + iy)·exp(−r²): a 2p m = +1 shape, |ψ| a ring around z."""
    axis = np.asarray(_ORIGIN[0]) + np.arange(_N) * _STEP
    x, y, z = np.meshgrid(axis, axis, axis, indexing="ij")
    envelope = np.exp(-(x**2 + y**2 + z**2))
    return (x + 1j * y) * envelope


def _archive(tmp_path: Path, values: np.ndarray | None = None, *, shape: list[int] | None = None,
             pairs: np.ndarray | None = None) -> Path:
    field = _p_plus_one() if values is None else values
    data = pairs if pairs is not None else np.stack([field.real, field.imag], axis=-1)
    data = np.ascontiguousarray(data, dtype=np.float64)
    grid = {
        "origin": _ORIGIN,
        "voxel_vectors": [[_STEP, 0, 0], [0, _STEP, 0], [0, 0, _STEP]],
        "shape": shape or list(data.shape[:3]),
    }
    return build_qvf(tmp_path / "psi.qvf", [
        ("structure", "structure",
         {"structure": ("json", "sections/structure.json", _water_structure_bytes())}),
        ("psi", "volume.complex", {
            "grid": ("json", "psi/grid.json", json.dumps(grid).encode()),
            "data": ("binary", "psi/data.bin", data.tobytes(), "float64", list(data.shape)),
        }),
    ])


# ── reader ──────────────────────────────────────────────────────────────────


def test_reader_returns_the_complex_field(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path)) as reader:
        field = reader.read_volume_complex("psi")
    assert field.grid.shape == (_N, _N, _N)
    assert field.values.dtype == np.complex128
    np.testing.assert_allclose(field.values, _p_plus_one())
    assert field.unit is None


@pytest.mark.parametrize(
    ("pairs", "shape", "message"),
    [
        (np.zeros((3, 3, 3, 3)), None, "expected"),
        (np.zeros((3, 3, 3, 2)), [3, 3, 2], "does not match"),
    ],
)
def test_reader_refuses_a_malformed_field(
    tmp_path: Path, pairs: np.ndarray, shape: list[int] | None, message: str
) -> None:
    # The vendored schema refuses some shapes at open; the reader the rest.
    with pytest.raises(QVFError, match=message), QVFReader(
        _archive(tmp_path, pairs=pairs, shape=shape)
    ) as reader:
        reader.read_volume_complex("psi")


# ── parts, isovalue, phase ──────────────────────────────────────────────────


def test_parts_are_the_stored_values() -> None:
    values = _p_plus_one()
    np.testing.assert_allclose(part_field(values, "real"), values.real)
    np.testing.assert_allclose(part_field(values, "imag"), values.imag)
    np.testing.assert_allclose(part_field(values, "modulus"), np.abs(values))
    with pytest.raises(ValueError, match="part must be one of"):
        part_field(values, "phase")


def test_opening_isovalue_fits_a_small_field(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path)) as reader:
        field = reader.read_volume_complex("psi")
    peak = max_modulus(field)
    assert opening_isovalue(field, 0.05) == 0.05  # the field reaches the hint
    assert opening_isovalue(field, 10.0) == pytest.approx(0.5 * peak)


def test_signed_parts_draw_both_lobes(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path)) as reader:
        field = reader.read_volume_complex("psi")
    for part in ("real", "imag"):
        meshes = complex_meshes(field, part, 0.1)
        assert set(meshes) == {"pos", "neg"}
    assert complex_meshes(field, "real", 10.0) == {}
    with pytest.raises(ValueError):
        complex_meshes(field, "real", 0.0)


def test_modulus_vertex_phase_is_arg_psi(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path)) as reader:
        field = reader.read_volume_complex("psi")
    mesh = complex_meshes(field, "modulus", 0.1)["modulus"]
    phase = np.asarray(mesh["phase"])
    assert np.all((phase >= -math.pi) & (phase <= math.pi))
    # For (x + iy)·f(r), arg ψ is the azimuth of each vertex, wherever it sits
    # on the ring: the phase winds once around z.
    points = np.asarray(mesh.points)
    azimuth = np.arctan2(points[:, 1], points[:, 0])
    difference = np.angle(np.exp(1j * (phase - azimuth)))
    assert np.abs(difference).max() < 0.05
    assert phase.max() - phase.min() > 1.9 * math.pi


def test_describe_names_the_part_and_an_empty_contour(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path)) as reader:
        field = reader.read_volume_complex("psi")
    assert describe(field, "modulus", 0.1, 1).startswith("|ψ| coloured by phase at 0.1")
    assert "lower the isovalue" in describe(field, "real", 9.0, 0)


# ── browser ─────────────────────────────────────────────────────────────────


def _complex_actors(plotter: Any) -> list[str]:
    return sorted(str(n) for n in plotter.actors if str(n).startswith(COMPLEX_PREFIX))


def test_browser_draws_switches_parts_and_clears(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path)))
    state, ctrl = app.state, app.controller
    viewer = ctrl.complex_field_changed.func.__self__.viewer
    ctrl.activate_section("psi")
    assert state.complex_section_id == "psi"
    assert state.complex_part == "real"
    assert state.complex_summary.startswith("real part at 0.05")
    assert _complex_actors(viewer.plotter) == [
        f"{COMPLEX_PREFIX}surface_neg", f"{COMPLEX_PREFIX}surface_pos",
    ]

    # Both controls send both values with the trigger (#122).
    ctrl.complex_field_changed("modulus", 0.1)
    assert state.complex_isovalues == {"psi": 0.1}
    assert state.complex_summary.startswith("|ψ| coloured by phase at 0.1")
    assert _complex_actors(viewer.plotter) == [f"{COMPLEX_PREFIX}surface_modulus"]

    ctrl.activate_section("structure")
    assert state.complex_section_id == ""
    assert not _complex_actors(viewer.plotter)


def test_bookmarks_keep_the_part_and_isovalue(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path)))
    state, ctrl = app.state, app.controller
    ctrl.activate_section("psi")
    ctrl.complex_field_changed("imag", 0.12)
    ctrl.save_user_bookmark("imag")
    ctrl.complex_field_changed("real", 0.2)
    ctrl.apply_user_bookmark("imag")
    assert state.complex_part == "imag"
    assert state.complex_isovalues["psi"] == pytest.approx(0.12)
    assert state.complex_summary.startswith("imaginary part at 0.12")


# ── capture, batch, show, API ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("part", "said"),
    [("real", "real part"), ("imag", "imaginary part"), ("modulus", "|ψ| coloured by phase")],
)
def test_capture_cli_draws_each_part(tmp_path: Path, part: str, said: str) -> None:
    out = tmp_path / f"{part}.png"
    result = CliRunner().invoke(
        main, ["capture", str(_archive(tmp_path)), "-s", "psi", "--part", part, "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert said in result.output
    assert out.stat().st_size > 0


def test_capture_reports_an_isovalue_above_the_field(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main, ["capture", str(_archive(tmp_path)), "-s", "psi", "--isovalue", "5",
               "-o", str(tmp_path / "x.png")],
    )
    assert result.exit_code == 1
    assert "lower the isovalue" in result.output
    assert not (tmp_path / "x.png").exists()


def test_batch_renders_the_complex_section(tmp_path: Path) -> None:
    from vibeview.batch import render_all_png

    (tmp_path / "gallery").mkdir()
    with QVFReader(_archive(tmp_path)) as reader:
        written = render_all_png(reader, tmp_path / "gallery", include_volumes=True)
    assert str(tmp_path / "gallery" / "psi.png") in written


def test_show_draws_the_real_part(tmp_path: Path) -> None:
    from vibeview.tui import scene as scenelib
    from vibeview.tui.show import render_section

    with QVFReader(_archive(tmp_path)) as reader:
        assert render_section(reader, "psi", 40, 12) is not None
        assert scenelib.complex_real_meshes(reader, "psi")


def test_get_volume_summarises_the_modulus(tmp_path: Path) -> None:
    from vibeview.api import get_volume

    summary = get_volume(_archive(tmp_path), "psi")
    assert summary is not None
    assert summary["data_shape"] == [_N, _N, _N, 2]
    assert summary["data_max"] == pytest.approx(np.abs(_p_plus_one()).max())


@requires_qvf_corpus("volume_complex.qvf")
def test_corpus_2p_plus_one_opens_with_a_surface() -> None:
    corpus = qvf_corpus_dir()
    assert corpus is not None
    with QVFReader(corpus / "volume_complex.qvf") as reader:
        field = reader.read_volume_complex("psi_2p1")
    # Its largest |ψ| (0.048) is below the 0.05 default, so the view opens lower.
    level = opening_isovalue(field, 0.05)
    assert level < 0.05
    assert complex_meshes(field, "modulus", level)
