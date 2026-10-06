"""The payload of direct volume rendering: range, quantisation, cap, dataset.

Step 1 of the :doc:`direct volume rendering design </volume_rendering_design>`:
everything that turns a stored QVF grid into the ``pv.ImageData`` a volume
actor draws, with no viewer code involved, so headless capture and the tests
use it the same way the browser will.

Why the payload looks like this (measured 2026-10-02, ``docs/bench_volume_*``):
a volume sends the whole grid and grows with N³ where a contour grows with
N². A float32 128³ grid is about 8 MB on the wire against 2 MB as uint8, so
the field is quantised to 256 levels over a per-kind range, and the largest
dimension is capped at :data:`DEFAULT_MAX_DIM` by linear resampling, which
keeps the box the volume occupies. The range is kept in data units so the
transfer function is edited in the section's own units and a producer can
override it through a hint.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pyvista as pv

from vibeview.kinds import SIGNED_VOLUME_KINDS
from vibeview.renderers.volume import orthogonal_image

if TYPE_CHECKING:
    from vibeview.qvf import GridData

#: Largest grid dimension sent to the client. 128³ as uint8 is about 2 MB.
DEFAULT_MAX_DIM = 128

#: The percentile that bounds a density or a signed field, so a handful of
#: voxels at a nuclear cusp do not claim most of the 256 levels.
RANGE_PERCENTILE = 99.5

#: Name of the one point array the dataset carries. trame-vtk ships every
#: array on an image, so the dataset holds nothing else.
SCALARS = "q"


class VolumeRenderError(ValueError):
    """The grid cannot become a volume dataset; the message says why."""


def volume_range(data: np.ndarray, kind: str) -> tuple[float, float]:
    """The data-unit range the transfer function is defined over.

    Non-finite samples are ignored. ELF is bounded by construction; a signed
    kind takes a symmetric range so zero maps to the middle level; a density
    starts at zero and ends at the :data:`RANGE_PERCENTILE` of its samples;
    anything else takes its finite extremes.
    """
    values = np.asarray(data, dtype=np.float64)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise VolumeRenderError("the field has no finite samples")
    if kind == "volume.elf":
        return (0.0, 1.0)
    if kind in SIGNED_VOLUME_KINDS:
        magnitude = float(np.percentile(np.abs(finite), RANGE_PERCENTILE))
        if magnitude <= 0.0:
            magnitude = float(np.max(np.abs(finite)))
        if magnitude <= 0.0:
            raise VolumeRenderError("the field is zero everywhere")
        return (-magnitude, magnitude)
    if kind == "volume.density":
        high = float(np.percentile(finite, RANGE_PERCENTILE))
        if high <= 0.0:
            high = float(finite.max())
        if high <= 0.0:
            raise VolumeRenderError("the density is zero everywhere")
        return (0.0, high)
    low, high = float(finite.min()), float(finite.max())
    if high <= low:
        raise VolumeRenderError("the field is constant")
    return (low, high)


def _levels(dtype: type[np.integer]) -> int:
    return int(np.iinfo(dtype).max)


def zero_level(low: float, high: float, dtype: type[np.integer] = np.uint8) -> int:
    """The quantised level that stands for a value of zero, clipped into range."""
    levels = _levels(dtype)
    return int(np.clip(np.round((0.0 - low) / (high - low) * levels), 0, levels))


def quantise(
    data: np.ndarray, low: float, high: float, dtype: type[np.integer] = np.uint8
) -> np.ndarray:
    """``data`` mapped onto ``[0, levels]`` over ``[low, high]``, clipped.

    A non-finite sample takes :func:`zero_level`, which is 0 for an unsigned
    field and the middle level for a signed one, so it neither glows nor
    tears a hole.
    """
    if high <= low:
        raise VolumeRenderError("the range is empty")
    levels = _levels(dtype)
    scaled = (np.asarray(data, dtype=np.float64) - low) / (high - low) * levels
    scaled = np.where(np.isfinite(scaled), scaled, float(zero_level(low, high, dtype)))
    return np.clip(np.round(scaled), 0, levels).astype(dtype)


def dequantise(
    levels_array: np.ndarray, low: float, high: float, dtype: type[np.integer] = np.uint8
) -> np.ndarray:
    """The data-unit value each level stands for; the inverse of :func:`quantise`."""
    levels = _levels(dtype)
    return low + np.asarray(levels_array, dtype=np.float64) / levels * (high - low)


def cap_dimensions(shape: Sequence[int], max_dim: int = DEFAULT_MAX_DIM) -> tuple[int, int, int]:
    """``shape`` scaled uniformly so its largest dimension is at most ``max_dim``.

    Uniform, so the voxels keep their aspect; rounded up, so no axis loses a
    sample it could keep; a singleton axis stays a singleton.
    """
    if max_dim < 1:
        raise VolumeRenderError("max_dim must be at least 1")
    dims = tuple(int(n) for n in shape)
    largest = max(dims)
    if largest <= max_dim:
        return (dims[0], dims[1], dims[2])
    factor = max_dim / largest
    capped = tuple(max(1, math.ceil(n * factor)) if n > 1 else 1 for n in dims)
    return (capped[0], capped[1], capped[2])


@dataclass(frozen=True)
class VolumeImage:
    """A stored grid as the dataset a volume actor draws.

    ``image`` is a ``pv.ImageData`` in ångström carrying exactly one point
    array, :data:`SCALARS`, of ``dtype`` levels over ``range`` (data units).
    ``full_shape`` is the stored grid; ``sent_shape`` is what the client gets.
    """

    image: pv.ImageData
    kind: str
    range: tuple[float, float]
    full_shape: tuple[int, int, int]
    sent_shape: tuple[int, int, int]
    dtype: type[np.integer]

    @property
    def reduced(self) -> bool:
        """True when the cap resampled the grid."""
        return self.sent_shape != self.full_shape

    @property
    def payload_bytes(self) -> int:
        """Bytes of voxel data on the wire before compression."""
        return int(np.prod(self.sent_shape)) * int(np.dtype(self.dtype).itemsize)

    @property
    def zero(self) -> int:
        """The level that stands for a value of zero."""
        return zero_level(self.range[0], self.range[1], self.dtype)


def volume_image(
    data: np.ndarray,
    grid: GridData,
    kind: str,
    *,
    max_dim: int = DEFAULT_MAX_DIM,
    dtype: type[np.integer] = np.uint8,
    value_range: tuple[float, float] | None = None,
) -> VolumeImage:
    """Build the :class:`VolumeImage` for a stored field.

    Resampling happens on the float field before quantisation, so the levels
    are interpolated values, not interpolated levels. ``value_range``
    overrides :func:`volume_range`, for a producer hint. A skewed grid, a
    shape mismatch, an empty grid or a field without finite samples raise
    :class:`VolumeRenderError`; the caller keeps the isosurface and says why.
    """
    field = np.asarray(data)
    full_shape = tuple(int(n) for n in grid.shape)
    if len(full_shape) != 3 or field.shape != full_shape:
        raise VolumeRenderError(
            f"the field has shape {field.shape}, the grid {full_shape}"
        )
    if any(n < 1 for n in full_shape):
        raise VolumeRenderError("the grid is empty")
    low, high = value_range if value_range is not None else volume_range(field, kind)
    if high <= low:
        raise VolumeRenderError("the range is empty")

    image = orthogonal_image(grid, {"v": field.astype(np.float32, copy=False)})
    if image is None:
        raise VolumeRenderError("the grid is skewed; the isosurface draws it")

    sent_shape = cap_dimensions(full_shape, max_dim)
    if sent_shape != full_shape:
        # Linear, and without the half-voxel border extension, so the first
        # and last samples still sit on the stored grid's corners and the
        # box the volume occupies does not change.
        image = image.resample(
            dimensions=sent_shape,
            interpolation="linear",
            extend_border=False,
            scalars="v",
        )

    values = np.asarray(image.point_data["v"])
    image.point_data.clear()
    image.cell_data.clear()
    image.point_data[SCALARS] = quantise(values, low, high, dtype)
    sent = tuple(int(n) for n in image.dimensions)
    return VolumeImage(
        image=image,
        kind=kind,
        range=(float(low), float(high)),
        full_shape=(full_shape[0], full_shape[1], full_shape[2]),
        sent_shape=(sent[0], sent[1], sent[2]),
        dtype=dtype,
    )
