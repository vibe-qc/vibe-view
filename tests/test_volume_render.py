"""The direct-volume payload: range rules, quantisation, cap and dataset.

Step 1 of the direct volume rendering design: ``renderers.volume_render``
turns a stored grid into the one-array uint8 ``pv.ImageData`` a volume actor
draws, in ångström, capped by linear resampling that keeps the grid's box.
"""

from __future__ import annotations

import numpy as np
import pytest

from vibeview.kinds import SIGNED_VOLUME_KINDS
from vibeview.qvf import GridData
from vibeview.renderers.volume_render import (
    DEFAULT_MAX_DIM,
    SCALARS,
    VolumeRenderError,
    cap_dimensions,
    dequantise,
    quantise,
    volume_image,
    volume_range,
    zero_level,
)

BOHR = 0.529177210903  # ångström per bohr, as GridData.to_angstrom applies it


def _grid(shape, spacing=(0.5, 0.5, 0.5), origin=(-4.0, -4.0, -4.0), units="bohr"):
    return GridData(
        origin=np.array(origin, dtype=float),
        voxel_vectors=np.diag(spacing).astype(float),
        shape=tuple(shape),
        units=units,
    )


def _blob(shape, centre=(0.0, 0.0, 0.0)):
    axes = [np.linspace(-4.0, 4.0, n) for n in shape]
    x, y, z = np.meshgrid(*axes, indexing="ij")
    return np.exp(-((x - centre[0]) ** 2 + (y - centre[1]) ** 2 + (z - centre[2]) ** 2) / 2.0)


# ── Range rules ───────────────────────────────────────────────────────────


def test_density_range_ignores_the_cusp_and_starts_at_zero():
    field = _blob((20, 20, 20)).astype(np.float32)
    field[10, 10, 10] = 1000.0  # one nuclear cusp
    low, high = volume_range(field, "volume.density")
    assert low == 0.0
    assert 0.5 < high < 1.5, "the 99.5th percentile, not the cusp"


def test_elf_range_is_the_unit_interval_whatever_the_samples():
    assert volume_range(np.full((4, 4, 4), 0.3), "volume.elf") == (0.0, 1.0)


@pytest.mark.parametrize("kind", sorted(SIGNED_VOLUME_KINDS))
def test_signed_kinds_take_a_symmetric_range_and_put_zero_in_the_middle(kind):
    field = (_blob((20, 20, 20), (1.0, 0, 0)) - 3.0 * _blob((20, 20, 20), (-1.0, 0, 0)))
    low, high = volume_range(field, kind)
    assert low == -high
    assert 0.0 < high <= 3.0
    assert zero_level(low, high) == 128


def test_generic_range_is_the_finite_extremes_and_non_finite_is_ignored():
    field = np.linspace(-2.0, 5.0, 64).reshape(4, 4, 4)
    field[0, 0, 0] = np.nan
    field[1, 1, 1] = np.inf
    assert volume_range(field, "volume.generic") == pytest.approx(
        (float(np.nanmin(field[np.isfinite(field)])), 5.0)
    )


def test_fields_without_a_range_are_refused():
    with pytest.raises(VolumeRenderError, match="finite"):
        volume_range(np.full((2, 2, 2), np.nan), "volume.density")
    with pytest.raises(VolumeRenderError, match="constant"):
        volume_range(np.full((2, 2, 2), 1.5), "volume.generic")
    with pytest.raises(VolumeRenderError, match="zero everywhere"):
        volume_range(np.zeros((2, 2, 2)), "volume.spin")


# ── Quantisation ──────────────────────────────────────────────────────────


def test_quantise_round_trips_within_one_level_and_clips():
    rng = np.random.default_rng(7)
    low, high = -0.02, 0.02
    field = rng.uniform(low, high, size=(8, 8, 8))
    levels = quantise(field, low, high)
    assert levels.dtype == np.uint8
    assert np.abs(dequantise(levels, low, high) - field).max() <= (high - low) / 255 / 2 + 1e-12
    assert quantise(np.array([[[-1.0, 1.0]]]), low, high).tolist() == [[[0, 255]]]


def test_non_finite_samples_take_the_zero_level():
    field = np.array([[[np.nan, np.inf, 0.0]]])
    assert quantise(field, -1.0, 1.0).tolist() == [[[128, 128, 128]]]
    assert quantise(field, 0.0, 1.0).tolist() == [[[0, 0, 0]]]


# ── Cap ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        ((300, 200, 50), (128, 86, 22)),
        ((128, 128, 128), (128, 128, 128)),
        ((100, 40, 20), (100, 40, 20)),
        ((1, 300, 300), (1, 128, 128)),
        ((256, 1, 1), (128, 1, 1)),
    ],
)
def test_cap_dimensions_scale_uniformly_and_round_up(shape, expected):
    assert cap_dimensions(shape, DEFAULT_MAX_DIM) == expected


# ── The dataset ───────────────────────────────────────────────────────────


def test_volume_image_carries_one_uint8_array_in_angstrom():
    shape = (40, 30, 20)
    field = _blob(shape).astype(np.float32)
    vi = volume_image(field, _grid(shape), "volume.density")
    assert vi.sent_shape == shape and vi.reduced is False
    assert tuple(vi.image.dimensions) == shape
    assert vi.image.point_data.keys() == [SCALARS]
    assert len(vi.image.cell_data.keys()) == 0
    assert vi.image.point_data[SCALARS].dtype == np.uint8
    assert vi.payload_bytes == 40 * 30 * 20
    assert vi.image.spacing == pytest.approx((0.5 * BOHR,) * 3)
    assert vi.image.origin == pytest.approx((-4.0 * BOHR,) * 3)
    assert vi.range[0] == 0.0 and 0.5 < vi.range[1] < 1.5
    assert vi.zero == 0
    # The peak of the blob is the brightest level.
    levels = np.asarray(vi.image.point_data[SCALARS]).reshape(shape, order="F")
    assert levels[20, 15, 10] == 255


def test_the_cap_resamples_on_the_float_field_and_keeps_the_box():
    shape = (300, 20, 20)
    field = _blob(shape).astype(np.float32)
    grid = _grid(shape, spacing=(0.05, 0.5, 0.5))
    full = volume_image(field, grid, "volume.density", max_dim=1000)
    vi = volume_image(field, grid, "volume.density")
    assert vi.reduced is True
    assert vi.sent_shape == (128, 9, 9)
    assert tuple(vi.image.dimensions) == (128, 9, 9)
    assert vi.image.bounds == pytest.approx(full.image.bounds, abs=1e-6)
    assert vi.image.point_data.keys() == [SCALARS]
    assert vi.payload_bytes == 128 * 9 * 9
    # Interpolated values, not interpolated levels: the centre stays bright.
    levels = np.asarray(vi.image.point_data[SCALARS]).reshape((128, 9, 9), order="F")
    assert levels[64, 4, 4] >= 250


def test_a_flipped_axis_is_placed_like_the_isosurface():
    from vibeview.renderers.volume import orthogonal_image

    shape = (4, 4, 6)
    field = np.broadcast_to(np.arange(6, dtype=np.float32), shape).copy()  # value = k index
    grid = _grid(shape, spacing=(0.5, 0.5, -0.5), origin=(0.0, 0.0, 2.5), units="angstrom")
    vi = volume_image(field, grid, "volume.generic")
    reference = orthogonal_image(grid, {"values": field})
    assert reference is not None
    assert vi.image.origin == pytest.approx(reference.origin)
    assert vi.image.spacing == pytest.approx((0.5, 0.5, 0.5))
    assert vi.image.origin == pytest.approx((0.0, 0.0, 0.0)), "the origin moved to the low corner"
    levels = np.asarray(vi.image.point_data[SCALARS]).reshape(shape, order="F")
    assert levels[0, 0, 0] == 255 and levels[0, 0, 5] == 0, "the data flipped with the axis"


def test_a_skewed_grid_is_resampled_onto_its_bounding_box():
    from vibeview.renderers.volume_render import resampled_box_image

    n = 24
    vectors = np.array([[0.5, 0.0, 0.0], [0.25, 0.4330127, 0.0], [0.0, 0.0, 0.5]])
    grid = GridData(
        origin=np.array([-6.0, -6.0, -6.0]),
        voxel_vectors=vectors,
        shape=(n, n, n),
        units="angstrom",
    )
    # A blob at the cell's centre, in fractional index coordinates.
    i, j, k = np.meshgrid(np.arange(n), np.arange(n), np.arange(n), indexing="ij")
    c = (n - 1) / 2
    field = np.exp(-(((i - c) ** 2 + (j - c) ** 2 + (k - c) ** 2) / 18.0)).astype(np.float32)

    box = resampled_box_image(field, grid, max_dim=64)
    assert all(s > 0 for s in box.spacing) and box.point_data.keys() == ["v"]
    corners = np.array(
        [grid.origin + (n - 1) * (a * vectors[0] + b * vectors[1] + d * vectors[2])
         for a in (0, 1) for b in (0, 1) for d in (0, 1)]
    )
    assert np.allclose(box.bounds[::2], corners.min(axis=0))
    assert np.allclose(box.bounds[1::2], corners.max(axis=0))
    values = np.asarray(box.point_data["v"]).reshape(box.dimensions, order="F")
    centre = grid.origin + c * vectors.sum(axis=0)
    index = tuple(int(round((centre[a] - box.origin[a]) / box.spacing[a])) for a in range(3))
    assert values[index] > 0.9, "the blob's centre lands where the affine grid puts it"
    # The box's (max x, min y) corner lies outside the sheared cell, and a
    # 60-degree shear leaves a third of the box empty.
    assert values[-1, 0, 0] == 0.0
    assert np.mean(values == 0.0) > 0.25

    vi = volume_image(field, grid, "volume.density", max_dim=64)
    assert vi.resampled is True and vi.full_shape == (n, n, n)
    assert max(vi.sent_shape) <= 64 and vi.image.point_data.keys() == [SCALARS]


def test_refusals_say_why():
    shape = (4, 4, 4)
    field = _blob(shape)
    with pytest.raises(VolumeRenderError, match="shape"):
        volume_image(field, _grid((4, 4, 5)), "volume.density")
    with pytest.raises(VolumeRenderError, match="finite"):
        volume_image(np.full(shape, np.nan), _grid(shape), "volume.density")
    with pytest.raises(VolumeRenderError, match="range"):
        volume_image(field, _grid(shape), "volume.density", value_range=(1.0, 1.0))


def test_a_producer_range_overrides_the_rule():
    shape = (7, 7, 7)  # odd, so the blob's centre is a sample and its peak is 1.0
    vi = volume_image(_blob(shape), _grid(shape), "volume.density", value_range=(0.0, 2.0))
    assert vi.range == (0.0, 2.0)
    levels = np.asarray(vi.image.point_data[SCALARS])
    assert levels.max() == 128, "the blob's peak of 1.0 sits halfway up a [0, 2] range"
