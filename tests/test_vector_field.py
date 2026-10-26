"""``volume.vector`` sections drawn as arrows (#156)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from click.testing import CliRunner

from tests._qvf_corpus import qvf_corpus_dir, requires_qvf_corpus
from tests.conftest import _water_structure_bytes, build_qvf
from vibeview.cli import main
from vibeview.qvf import BOHR_TO_ANGSTROM, QVFError, QVFReader
from vibeview.renderers.vector_field import (
    VECTOR_PREFIX,
    auto_stride,
    describe,
    glyph_mesh,
    vector_glyphs,
)

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

_ORIGIN = [-1.0, -1.0, -1.0]
_STEP = 0.5  # bohr


def _radial(shape: tuple[int, int, int] = (5, 5, 5)) -> np.ndarray:
    """Vectors pointing at the grid centre, |v| growing with distance."""
    index = np.stack(np.meshgrid(*[np.arange(n) for n in shape], indexing="ij"), axis=-1)
    position = np.asarray(_ORIGIN) + index * _STEP
    return (-position).astype(np.float32)


def _archive(
    tmp_path: Path, vectors: np.ndarray | None = None, *, units: dict[str, str] | None = None,
    shape: list[int] | None = None,
) -> Path:
    data = _radial() if vectors is None else vectors
    grid = {
        "origin": _ORIGIN,
        "voxel_vectors": [[_STEP, 0, 0], [0, _STEP, 0], [0, 0, _STEP]],
        "shape": shape or list(data.shape[:3]),
    }
    if units:
        grid["units"] = units
    return build_qvf(tmp_path / "field.qvf", [
        ("structure", "structure",
         {"structure": ("json", "sections/structure.json", _water_structure_bytes())}),
        ("grad", "volume.vector", {
            "grid": ("json", "grad/grid.json", json.dumps(grid).encode()),
            "data": ("binary", "grad/data.bin", data.tobytes(), "float32", list(data.shape)),
        }),
    ])


# ── reader ──────────────────────────────────────────────────────────────────


def test_reader_returns_grid_vectors_and_unit(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path, units={"data": "e/bohr^4"})) as reader:
        field = reader.read_volume_vector("grad")
    assert field.grid.shape == (5, 5, 5)
    assert field.vectors.shape == (5, 5, 5, 3)
    assert field.unit == "e/bohr^4"
    np.testing.assert_allclose(field.vectors[0, 0, 0], [1.0, 1.0, 1.0])


@pytest.mark.parametrize(
    ("vectors", "shape", "message"),
    [
        (np.zeros((5, 5, 5, 2), dtype=np.float32), None, "3 was expected"),
        (np.zeros((5, 5, 5), dtype=np.float32), None, "expected"),
        (np.zeros((5, 5, 5, 3), dtype=np.float32), [5, 5, 4], "does not match"),
    ],
)
def test_reader_refuses_a_malformed_field(
    tmp_path: Path, vectors: np.ndarray, shape: list[int] | None, message: str
) -> None:
    # The vendored schema refuses some shapes at open; the reader the rest.
    with pytest.raises(QVFError, match=message), QVFReader(
        _archive(tmp_path, vectors, shape=shape)
    ) as reader:
        reader.read_volume_vector("grad")


# ── glyphs ──────────────────────────────────────────────────────────────────


def test_auto_stride_keeps_small_grids_whole_and_caps_large_ones() -> None:
    assert auto_stride((5, 5, 5)) == 1
    assert auto_stride((100, 100, 100)) == 7  # 15³ = 3375 ≤ 4000 < 14³·…
    assert auto_stride((10, 10, 10), max_arrows=8) == 5
    with pytest.raises(ValueError):
        auto_stride((5, 5, 5), max_arrows=0)


def test_glyphs_follow_the_stored_vectors(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path)) as reader:
        field = reader.read_volume_vector("grad")
    glyphs = vector_glyphs(field, cutoff=0.0)
    # The centre point is the one zero vector; every other point has an arrow.
    assert glyphs.stride == 1 and glyphs.n_sampled == 125 and len(glyphs) == 124
    # Tails sit on the grid points in Å; directions are the stored unit vectors.
    corner = np.argmin(glyphs.points.sum(axis=1))
    np.testing.assert_allclose(glyphs.points[corner], np.asarray(_ORIGIN) * BOHR_TO_ANGSTROM)
    np.testing.assert_allclose(glyphs.directions[corner], np.ones(3) / np.sqrt(3))
    np.testing.assert_allclose(glyphs.magnitudes[corner], np.sqrt(3), rtol=1e-6)
    # No arrow is longer than 0.9 of the lattice spacing.
    assert glyphs.lengths.max() <= 0.9 * _STEP * BOHR_TO_ANGSTROM + 1e-12


def test_stride_cutoff_and_length_scale(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path)) as reader:
        field = reader.read_volume_vector("grad")
    assert vector_glyphs(field, stride=2, cutoff=0.0).n_sampled == 27
    full = vector_glyphs(field, cutoff=0.0)
    trimmed = vector_glyphs(field, cutoff=0.6)
    assert len(trimmed) < len(full)
    assert trimmed.magnitudes.min() >= 0.6 * full.reference
    short = vector_glyphs(field, cutoff=0.0, length_scale=0.5)
    assert short.lengths.max() < full.lengths.max()
    with pytest.raises(ValueError):
        vector_glyphs(field, stride=0)


def test_a_zero_field_draws_nothing(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path, np.zeros((3, 3, 3, 3), dtype=np.float32))) as reader:
        glyphs = vector_glyphs(reader.read_volume_vector("grad"))
    assert len(glyphs) == 0 and glyph_mesh(glyphs) is None
    assert describe(glyphs, None) == "no arrows above the cutoff (of 27 grid points)"


def test_glyph_mesh_carries_the_magnitude(tmp_path: Path) -> None:
    with QVFReader(_archive(tmp_path)) as reader:
        glyphs = vector_glyphs(reader.read_volume_vector("grad"), stride=2)
    mesh = glyph_mesh(glyphs)
    assert mesh is not None and "magnitude" in mesh.array_names
    assert describe(glyphs, "a.u.").startswith(f"{len(glyphs)} arrows, every 2nd grid point")


# ── browser ─────────────────────────────────────────────────────────────────


def _vector_actors(plotter: Any) -> list[str]:
    return [name for name in plotter.actors if str(name).startswith(VECTOR_PREFIX)]


def test_browser_draws_redraws_and_clears_the_arrows(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path)))
    state, ctrl = app.state, app.controller
    viewer = ctrl.vector_glyphs_changed.func.__self__.viewer
    ctrl.activate_section("grad")
    assert state.vector_section_id == "grad"
    assert state.vector_summary.startswith("124 arrows, every grid point")
    assert _vector_actors(viewer.plotter)

    # The card sends all four settings with the trigger (#122).
    ctrl.vector_glyphs_changed(2, 1.0, 0.0, "plasma")
    assert (state.vector_stride, state.vector_colormap) == (2, "plasma")
    assert "every 2nd grid point" in state.vector_summary

    ctrl.activate_section("structure")
    assert state.vector_section_id == ""
    assert not _vector_actors(viewer.plotter)


def test_bookmarks_keep_the_arrow_settings(tmp_path: Path) -> None:
    from vibeview.app import create_app

    app = create_app(QVFReader(_archive(tmp_path)))
    state, ctrl = app.state, app.controller
    ctrl.activate_section("grad")
    ctrl.vector_glyphs_changed(3, 0.5, 0.2, "magma")
    ctrl.save_user_bookmark("arrows")
    ctrl.vector_glyphs_changed(0, 1.0, 0.05, "viridis")
    ctrl.apply_user_bookmark("arrows")
    assert (state.vector_stride, state.vector_length_scale, state.vector_cutoff,
            state.vector_colormap) == (3, 0.5, 0.2, "magma")
    assert "every 3rd grid point" in state.vector_summary


# ── capture, batch, show, API ───────────────────────────────────────────────


def test_capture_cli_draws_the_arrows(tmp_path: Path) -> None:
    out = tmp_path / "grad.png"
    result = CliRunner().invoke(
        main, ["capture", str(_archive(tmp_path)), "-s", "grad", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "124 arrows" in result.output
    assert out.stat().st_size > 0


def test_capture_refuses_a_field_with_no_arrows(tmp_path: Path) -> None:
    from vibeview.capture import CaptureError, render_vector_field

    path = _archive(tmp_path, np.zeros((3, 3, 3, 3), dtype=np.float32))
    with QVFReader(path) as reader, pytest.raises(CaptureError, match="no vector"):
        render_vector_field(reader, "grad", tmp_path / "x.png")


def test_batch_renders_the_vector_section(tmp_path: Path) -> None:
    from vibeview.batch import render_all_png

    (tmp_path / "gallery").mkdir()
    with QVFReader(_archive(tmp_path)) as reader:
        written = render_all_png(reader, tmp_path / "gallery", include_volumes=True)
    assert str(tmp_path / "gallery" / "grad.png") in written


def test_show_draws_a_magnitude_surface(tmp_path: Path) -> None:
    from vibeview.tui.show import render_section

    with QVFReader(_archive(tmp_path)) as reader:
        grid = render_section(reader, "grad", 40, 12)
        from vibeview.tui import scene as scenelib

        meshes = scenelib.vector_magnitude_meshes(reader, "grad")
    assert grid is not None
    assert meshes


def test_get_volume_summarises_the_magnitude(tmp_path: Path) -> None:
    from vibeview.api import get_volume

    summary = get_volume(_archive(tmp_path, units={"data": "a.u."}), "grad")
    assert summary is not None
    assert summary["data_shape"] == [5, 5, 5, 3]
    assert summary["data_min"] == 0.0
    assert summary["data_max"] == pytest.approx(np.sqrt(3), rel=1e-6)
    assert summary["unit"] == "a.u."


@requires_qvf_corpus("volume_vector.qvf")
def test_corpus_density_gradient_draws() -> None:
    corpus = qvf_corpus_dir()
    assert corpus is not None
    with QVFReader(corpus / "volume_vector.qvf") as reader:
        glyphs = vector_glyphs(reader.read_volume_vector("grad_rho"))
    assert glyphs.n_sampled == 120 and len(glyphs) > 100
