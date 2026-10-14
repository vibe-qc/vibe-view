"""Signed surfaces keep physical coordinates across capture and browser paths."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.conftest import build_qvf
from vibeview.app import _build_signed_contour
from vibeview.capture import _build_contour, capture_volume
from vibeview.qvf import QVFReader
from vibeview.renderers.volume import VolumeRenderer


def _signed_archive(path: Path, *, flipped_axes: tuple[int, ...] = (), skew: bool = False) -> Path:
    shape = (5, 5, 5)
    voxel = np.array([[1.0, 0.0, 0.0], [0.5 if skew else 0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    origin = np.zeros(3)
    for axis in flipped_axes:
        origin += 4 * voxel[axis]
        voxel[axis] *= -1
    indices = np.stack(np.indices(shape), axis=-1)
    xyz = origin + indices @ voxel
    # An independent linear field makes each lobe an exact Cartesian plane.
    data = (xyz[..., 0] + 0.25 * xyz[..., 1] - 0.5 * xyz[..., 2] - 1).astype("<f8")
    grid = json.dumps(
        {
            "origin": origin.tolist(),
            "voxel_vectors": voxel.tolist(),
            "shape": list(shape),
        }
    ).encode()
    return build_qvf(
        path,
        [
            (
                "orbital",
                "volume.orbital",
                {
                    "grid": ("json", "grid.json", grid),
                    "data": ("binary", "data.bin", data.tobytes(), "float64", list(shape)),
                },
            )
        ],
    )


@pytest.mark.parametrize(
    "builder", [_build_signed_contour, _build_contour], ids=["browser", "capture"]
)
@pytest.mark.parametrize("flipped_axes", [(), (0,), (1,), (2,), (0, 1, 2)])
@pytest.mark.parametrize("skew", [False, True], ids=["cartesian", "skew"])
def test_signed_contours_follow_physical_planes(tmp_path, builder, flipped_axes, skew):
    path = _signed_archive(tmp_path / "orbital.qvf", flipped_axes=flipped_axes, skew=skew)
    with QVFReader(path) as reader:
        renderer = VolumeRenderer(reader.get_section("orbital"), reader)
        data = renderer.load_data()
        for sign, level in [(1, 2.5), (-1, -0.5)]:
            mesh = builder(renderer, np.maximum(sign * data, 0), 1.5, (1, 1, 1), None)
            assert mesh is not None and mesh.n_points > 0
            # QVF coordinates are bohr; viewer world coordinates are angstrom.
            points = mesh.points / 0.529177210903
            np.testing.assert_allclose(
                points[:, 0] + 0.25 * points[:, 1] - 0.5 * points[:, 2],
                level,
                atol=1e-6,
            )


@pytest.mark.parametrize(
    "builder", [_build_signed_contour, _build_contour], ids=["browser", "capture"]
)
def test_signed_contours_keep_empty_and_replication_contracts(tmp_path, builder):
    with QVFReader(_signed_archive(tmp_path / "orbital.qvf", flipped_axes=(2,))) as reader:
        renderer = VolumeRenderer(reader.get_section("orbital"), reader)
        data = np.maximum(renderer.load_data(), 0)
        assert builder(renderer, data, 100, (1, 1, 1), None) is None
        single = builder(renderer, data, 0.5, (1, 1, 1), None)
        repeated = builder(renderer, data, 0.5, (2, 1, 1), np.diag([6.0, 7.0, 8.0]))
        assert single is not None and repeated is not None
        assert repeated.n_points == 2 * single.n_points
        np.testing.assert_allclose(
            repeated.bounds,
            [single.bounds[0], single.bounds[1] + 6, *single.bounds[2:]],
            atol=1e-6,
        )


def test_capture_flipped_grid_matches_visible_signed_reference(tmp_path):
    images = []
    for label, flipped_axes in [("positive", ()), ("negative", (0, 1, 2))]:
        path = _signed_archive(tmp_path / f"{label}.qvf", flipped_axes=flipped_axes)
        output = tmp_path / f"{label}.png"
        with QVFReader(path) as reader:
            assert capture_volume(reader, "orbital", output, isovalue=0.5, size=(320, 240))
        with Image.open(output) as image:
            pixels = np.array(image.convert("RGB"), dtype=float)
        red, blue = pixels[..., 0], pixels[..., 2]
        assert np.count_nonzero((blue > 1.4 * red) & (blue > 50)) > 100
        assert np.count_nonzero((red > 1.4 * blue) & (red > 50)) > 100
        images.append(pixels)
    np.testing.assert_array_equal(images[0], images[1])
