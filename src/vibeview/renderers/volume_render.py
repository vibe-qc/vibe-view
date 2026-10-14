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
from typing import TYPE_CHECKING, Any

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


def resampled_box_image(
    data: np.ndarray, grid: GridData, max_dim: int = DEFAULT_MAX_DIM
) -> pv.ImageData:
    """A skewed grid interpolated onto the axis-aligned box that bounds it.

    vtk.js draws a volume on an axis-aligned image whatever direction
    matrix the image carries (checked in Chrome on 2026-10-06: a sheared
    ``vtkImageData`` drew its cloud axis-aligned beside the tilted contour),
    so a skewed grid has to be resampled before it can be sent. The box is
    the bounding box of the grid's eight corners; its spacing is the
    shortest voxel step, capped by :func:`cap_dimensions`; each box voxel
    takes the trilinear interpolation of the stored samples at its
    fractional grid index, and a voxel outside the cell takes zero. Pure
    numpy: eight gathers over the box, no SciPy. Periodic tiling is not
    applied, and the corners of a triclinic cell's box are empty.
    """
    vectors = np.asarray(grid.voxel_vectors, dtype=np.float64) * grid.to_angstrom
    origin = np.asarray(grid.origin, dtype=np.float64) * grid.to_angstrom
    shape = tuple(int(n) for n in grid.shape)
    source = np.asarray(data, dtype=np.float32)
    corners = np.array(
        [
            origin + i * (shape[0] - 1) * vectors[0] + j * (shape[1] - 1) * vectors[1]
            + k * (shape[2] - 1) * vectors[2]
            for i in (0, 1)
            for j in (0, 1)
            for k in (0, 1)
        ]
    )
    low, high = corners.min(axis=0), corners.max(axis=0)
    step = float(np.min(np.linalg.norm(vectors, axis=1)))
    if step <= 0.0:
        raise VolumeRenderError("the grid has a zero-length voxel vector")
    wanted = tuple(max(2, int(round((high[a] - low[a]) / step)) + 1) for a in range(3))
    dims = cap_dimensions(wanted, max_dim)
    spacing = tuple((high[a] - low[a]) / (dims[a] - 1) if dims[a] > 1 else 1.0 for a in range(3))
    axes = [low[a] + spacing[a] * np.arange(dims[a]) for a in range(3)]
    px, py, pz = np.meshgrid(*axes, indexing="ij")
    points = np.stack([px.ravel(), py.ravel(), pz.ravel()], axis=1)
    try:
        inverse = np.linalg.inv(vectors)
    except np.linalg.LinAlgError as exc:
        raise VolumeRenderError("the voxel vectors are singular") from exc
    fractional = (points - origin) @ inverse  # P = origin + f @ V
    inside = np.all(fractional >= 0.0, axis=1) & np.all(
        fractional <= np.array(shape, dtype=np.float64) - 1.0, axis=1
    )
    f = np.clip(fractional, 0.0, np.array(shape, dtype=np.float64) - 1.0)
    base = np.floor(f).astype(np.int64)
    base = np.minimum(base, np.array(shape) - 2)
    base = np.maximum(base, 0)
    t = (f - base).astype(np.float32)
    values = np.zeros(points.shape[0], dtype=np.float32)
    for di in (0, 1):
        wi = t[:, 0] if di else 1.0 - t[:, 0]
        for dj in (0, 1):
            wj = t[:, 1] if dj else 1.0 - t[:, 1]
            for dk in (0, 1):
                wk = t[:, 2] if dk else 1.0 - t[:, 2]
                values += (
                    wi * wj * wk
                    * source[base[:, 0] + di, base[:, 1] + dj, base[:, 2] + dk]
                )
    values[~inside] = 0.0
    image = pv.ImageData(dimensions=dims, spacing=spacing, origin=tuple(float(x) for x in low))
    image.point_data["v"] = values.reshape(dims, order="C").ravel(order="F")
    return image


@dataclass(frozen=True)
class VolumeImage:
    """A stored grid as the dataset a volume actor draws.

    ``image`` is a ``pv.ImageData`` in ångström carrying exactly one point
    array, :data:`SCALARS`, of ``dtype`` levels over ``range`` (data units).
    ``full_shape`` is the stored grid; ``sent_shape`` is what the client gets.
    ``resampled`` says the grid was skewed and was interpolated onto an
    axis-aligned box (:func:`resampled_box_image`).
    """

    image: pv.ImageData
    kind: str
    range: tuple[float, float]
    full_shape: tuple[int, int, int]
    sent_shape: tuple[int, int, int]
    dtype: type[np.integer]
    resampled: bool = False

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
    overrides :func:`volume_range`, for a producer hint. A skewed grid is
    interpolated onto its bounding box (:func:`resampled_box_image`). A
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

    resampled = False
    image = orthogonal_image(grid, {"v": field.astype(np.float32, copy=False)})
    if image is None:
        # A skewed grid: the client cannot place a sheared image, so it is
        # interpolated onto the box that bounds it, already at the cap.
        image = resampled_box_image(field, grid, max_dim)
        resampled = True

    sent_shape = tuple(int(n) for n in image.dimensions) if resampled else cap_dimensions(
        full_shape, max_dim
    )
    if not resampled and sent_shape != full_shape:
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
        resampled=resampled,
    )


# ── The transfer function and the actor ───────────────────────────────────

#: Opacity ramps per look, as (position in [0, 1] across the range, opacity
#: with a peak of 1.0). The Opacity control scales the peak.
_OPACITY_RAMPS: dict[str, tuple[tuple[float, float], ...]] = {
    # A diffuse shell: nothing near zero, rising towards the dense end.
    "density-shell": ((0.0, 0.0), (0.15, 0.0), (0.4, 0.4), (1.0, 1.0)),
    # ELF basins: nothing below 0.5, the basins from 0.8 up.
    "elf-basins": ((0.0, 0.0), (0.5, 0.0), (0.8, 1.0), (1.0, 1.0)),
    # A signed field: both lobes, nothing in the middle where zero sits. The
    # gap spans 40% to 60% so it lands on the editor's slider positions and
    # survives the six-point sampling.
    "signed-lobes": ((0.0, 1.0), (0.25, 0.5), (0.4, 0.0), (0.6, 0.0), (0.75, 0.5), (1.0, 1.0)),
    # A potential: the same shape with gentler slopes.
    "potential": ((0.0, 1.0), (0.3, 0.3), (0.4, 0.0), (0.6, 0.0), (0.7, 0.3), (1.0, 1.0)),
    # Anything else: a plain ramp.
    "ramp": ((0.0, 0.0), (1.0, 1.0)),
}

#: How many colour nodes a colormap is sampled into.
_COLOR_NODES = 8


def preset_for_kind(kind: str) -> str:
    """The opacity-ramp name a volume kind starts in."""
    if kind == "volume.density":
        return "density-shell"
    if kind == "volume.elf":
        return "elf-basins"
    if kind == "volume.potential":
        return "potential"
    if kind in SIGNED_VOLUME_KINDS:
        return "signed-lobes"
    return "ramp"


def default_transfer_function(
    kind: str,
    value_range: tuple[float, float],
    *,
    colormap: str = "viridis",
    opacity: float = 0.6,
    preset: str | None = None,
) -> dict[str, object]:
    """The transfer function a section starts volume mode with.

    Node positions are in data units over ``value_range``. The colour nodes
    sample the matplotlib ``colormap``, the opacity nodes follow the kind's
    ramp with its peak at ``opacity``. The result is JSON (lists and
    floats), so it can live in trame state, a session or a hint.
    """
    import matplotlib

    low, high = float(value_range[0]), float(value_range[1])
    if high <= low:
        raise VolumeRenderError("the range is empty")
    name = preset or preset_for_kind(kind)
    ramp = _OPACITY_RAMPS.get(name, _OPACITY_RAMPS["ramp"])
    try:
        cmap = matplotlib.colormaps[colormap]
    except KeyError:
        cmap = matplotlib.colormaps["viridis"]
    peak = float(np.clip(opacity, 0.0, 1.0))
    positions = np.linspace(0.0, 1.0, _COLOR_NODES)
    color = [
        [float(low + t * (high - low)), float(r), float(g), float(b)]
        for t, (r, g, b, _a) in zip(positions, cmap(positions), strict=True)
    ]
    alpha = [[float(low + t * (high - low)), float(a * peak)] for t, a in ramp]
    return {
        "preset": name,
        "colormap": colormap,
        "range": [low, high],
        "color": color,
        "opacity": alpha,
        "ramp": [v * peak for v in ramp_for_preset(name)],
    }


def transfer_functions(
    tf: dict[str, object], dtype: type[np.integer] = np.uint8
) -> tuple[object, object]:
    """The VTK colour and opacity functions for ``tf`` in quantised level space.

    The nodes stay in data units in ``tf``; only here are they mapped onto
    the levels the dataset carries, so editing a node never touches the grid.
    """
    import vtk

    rng = tf.get("range")
    if not isinstance(rng, (list, tuple)) or len(rng) != 2:
        raise VolumeRenderError("the transfer function has no range")
    low, high = float(rng[0]), float(rng[1])
    if high <= low:
        raise VolumeRenderError("the range is empty")
    levels = _levels(dtype)

    def level(x: Any) -> float:
        return float(np.clip((float(x) - low) / (high - low) * levels, 0.0, levels))

    ctf = vtk.vtkColorTransferFunction()
    colors = tf.get("color")
    if not isinstance(colors, (list, tuple)) or not colors:
        raise VolumeRenderError("the transfer function has no colour nodes")
    for node in colors:
        x, r, g, b = node
        ctf.AddRGBPoint(level(x), float(r), float(g), float(b))
    pwf = vtk.vtkPiecewiseFunction()
    alphas = tf.get("opacity")
    if not isinstance(alphas, (list, tuple)) or not alphas:
        raise VolumeRenderError("the transfer function has no opacity nodes")
    for node in alphas:
        x, a = node
        pwf.AddPoint(level(x), float(np.clip(float(a), 0.0, 1.0)))
    return ctf, pwf


#: Actor-name prefix of a rendered volume: ``volume_render_<section id>``.
#: Deliberately a member of the ``volume_`` family, so every path that
#: removes a section's volume actors removes this one too; the material
#: replay tells it apart by type, not by name.
VOLUME_ACTOR_PREFIX = "volume_render_"


def volume_actor_name(section_id: str) -> str:
    """The actor name of ``section_id`` rendered as a volume."""
    return f"{VOLUME_ACTOR_PREFIX}{section_id}"


def is_volume_actor(actor: object) -> bool:
    """True for a ``vtkVolume``, whose property is not a surface property."""
    is_a = getattr(actor, "IsA", None)
    return bool(is_a is not None and is_a("vtkVolume"))


def add_volume_actor(
    plotter: Any,
    volume: VolumeImage,
    tf: dict[str, object],
    *,
    name: str,
    shade: bool = False,
) -> Any:
    """Add ``volume`` to ``plotter`` as a volume actor drawn through ``tf``.

    The smart mapper ray-casts server-side for screenshots; trame-vtk
    serialises it as the vtk.js volume mapper, and the property, the
    colour function and the opacity function go with it. Composite
    blending, linear interpolation and, by default, no shading, since the
    slow GPUs are the unmeasured case. The actor is not pickable, so a
    hover or a click still reaches the atoms behind it.
    """
    levels = _levels(volume.dtype)
    actor = plotter.add_volume(
        volume.image,
        scalars=SCALARS,
        clim=[0, levels],
        mapper="smart",
        blending="composite",
        name=name,
        show_scalar_bar=False,
        reset_camera=False,
        pickable=False,
        shade=shade,
        render=False,
    )
    ctf, pwf = transfer_functions(tf, volume.dtype)
    prop = actor.GetProperty()
    prop.SetColor(0, ctf)
    prop.SetScalarOpacity(0, pwf)
    prop.SetInterpolationTypeToLinear()
    prop.SetShade(1 if shade else 0)
    return actor


# ── The editor's model: preset, window, ramp ──────────────────────────────

#: Where the editor's six opacity sliders sit across the window.
RAMP_POSITIONS: tuple[float, ...] = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

#: The presets a user can pick, in the order the picker lists them.
PRESET_OPTIONS: tuple[tuple[str, str], ...] = (
    ("density-shell", "Density shell"),
    ("elf-basins", "ELF basins"),
    ("signed-lobes", "Signed lobes"),
    ("potential", "Potential"),
    ("ramp", "Plain ramp"),
    ("custom", "Custom curve"),
)

#: The preset name of a function whose opacity nodes were placed one by one.
CUSTOM_PRESET = "custom"


def opacity_nodes_in_window(
    nodes: Sequence[Sequence[float]], window: tuple[float, float]
) -> list[list[float]]:
    """``nodes`` as ``[x, a]`` pairs sorted by position, clamped into the window
    and into [0, 1]; a malformed node is dropped."""
    low, high = float(window[0]), float(window[1])
    clean: list[list[float]] = []
    for node in nodes:
        if not isinstance(node, (list, tuple)) or len(node) != 2:
            continue
        try:
            x, a = float(node[0]), float(node[1])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(x) and math.isfinite(a)):
            continue
        clean.append([min(high, max(low, x)), min(1.0, max(0.0, a))])
    clean.sort(key=lambda node: node[0])
    return clean


def _interpolate(nodes: Sequence[Sequence[float]], positions: Sequence[float]) -> list[float]:
    xs = [float(node[0]) for node in nodes]
    ys = [float(node[1]) for node in nodes]
    return [float(v) for v in np.interp(positions, xs, ys)]


def ramp_for_preset(name: str) -> list[float]:
    """The preset's opacity ramp sampled at :data:`RAMP_POSITIONS`, peak 1.0."""
    nodes = _OPACITY_RAMPS.get(name, _OPACITY_RAMPS["ramp"])
    return _interpolate(nodes, RAMP_POSITIONS)


def ramp_from_transfer_function(tf: dict[str, object]) -> list[float]:
    """The six slider values that stand for ``tf``'s opacity nodes.

    A producer may give nodes at any positions; the sliders show them
    sampled at :data:`RAMP_POSITIONS`, and a touched slider replaces the
    nodes with the six-point form.
    """
    rng = tf.get("range")
    nodes = tf.get("opacity")
    if not isinstance(rng, (list, tuple)) or not isinstance(nodes, (list, tuple)) or not nodes:
        return ramp_for_preset("ramp")
    low, high = float(rng[0]), float(rng[1])
    if high <= low:
        return ramp_for_preset("ramp")
    positions = [low + t * (high - low) for t in RAMP_POSITIONS]
    return [float(np.clip(v, 0.0, 1.0)) for v in _interpolate(nodes, positions)]


def window_bounds(data: np.ndarray, kind: str) -> tuple[float, float]:
    """The slider bounds of the window: the field's finite extremes widened
    to the kind's default range, symmetric for a signed kind so the middle
    of the sliders is zero.

    The widening matters for a density, whose window starts at zero while
    the field's smallest sample is a hair above it, and for ELF, whose
    window is the unit interval whatever the samples span.
    """
    values = np.asarray(data, dtype=np.float64)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return (0.0, 1.0)
    try:
        rule_low, rule_high = volume_range(finite, kind)
    except VolumeRenderError:
        rule_low, rule_high = float(finite.min()), float(finite.max())
    if kind in SIGNED_VOLUME_KINDS:
        magnitude = max(float(np.max(np.abs(finite))), abs(rule_low), abs(rule_high))
        return (-magnitude, magnitude) if magnitude > 0 else (-1.0, 1.0)
    low = min(float(finite.min()), rule_low)
    high = max(float(finite.max()), rule_high)
    return (low, high) if high > low else (low, low + 1.0)


def _color_nodes(colormap: str, low: float, high: float) -> list[list[float]]:
    import matplotlib

    try:
        cmap = matplotlib.colormaps[colormap]
    except KeyError:
        cmap = matplotlib.colormaps["viridis"]
    positions = np.linspace(0.0, 1.0, _COLOR_NODES)
    return [
        [float(low + t * (high - low)), float(r), float(g), float(b)]
        for t, (r, g, b, _a) in zip(positions, cmap(positions), strict=True)
    ]


def transfer_function_from_editor(
    *,
    window: tuple[float, float],
    colormap: str,
    ramp: Sequence[float],
    preset: str,
    color: Sequence[Sequence[float]] | None = None,
    opacity_nodes: Sequence[Sequence[float]] | None = None,
) -> dict[str, object]:
    """The transfer function the editor's state describes.

    Opacity nodes sit at :data:`RAMP_POSITIONS` across ``window`` with the
    ramp's values, unless ``opacity_nodes`` places them one by one (the
    curve editor), in which case ``ramp`` is the six-point sampling of the
    curve. Colour nodes sample ``colormap`` unless explicit ``color`` nodes
    (a producer's palette, ``colormap == "custom"``) are kept. The result
    is JSON and carries ``ramp`` so the sliders round-trip.
    """
    low, high = float(window[0]), float(window[1])
    if high <= low:
        raise VolumeRenderError("the window is empty")
    if color is not None and colormap == "custom":
        nodes = [[float(c) for c in node] for node in color]
    else:
        nodes = _color_nodes(colormap, low, high)
    if opacity_nodes is not None:
        placed = opacity_nodes_in_window(opacity_nodes, (low, high))
        if len(placed) < 2:
            raise VolumeRenderError("an opacity curve needs at least two nodes")
        tf: dict[str, object] = {
            "preset": preset,
            "colormap": colormap,
            "range": [low, high],
            "color": nodes,
            "opacity": placed,
        }
        tf["ramp"] = ramp_from_transfer_function(tf)
        return tf
    values = [float(np.clip(v, 0.0, 1.0)) for v in ramp]
    if len(values) != len(RAMP_POSITIONS):
        values = ramp_for_preset(preset)
    return {
        "preset": preset,
        "colormap": colormap,
        "range": [low, high],
        "color": nodes,
        "opacity": [
            [low + t * (high - low), a] for t, a in zip(RAMP_POSITIONS, values, strict=True)
        ],
        "ramp": values,
    }


def resolve_transfer_function(
    *,
    kind: str,
    window: tuple[float, float],
    colormap: str,
    opacity: float,
    existing: dict[str, object] | None,
) -> dict[str, object]:
    """The transfer function to draw with: ``existing`` carried over the
    current window, or the kind's default with ``opacity`` as its peak.

    ``existing`` is the section's saved or hinted function. Its preset,
    opacity shape and, for a custom palette, colour nodes are kept; the
    colour nodes otherwise follow ``colormap``, so the colormap picker
    works in volume mode without an editor round trip.
    """
    if existing is None:
        preset = preset_for_kind(kind)
        peak = float(np.clip(opacity, 0.0, 1.0))
        ramp = [v * peak for v in ramp_for_preset(preset)]
        return transfer_function_from_editor(
            window=window, colormap=colormap, ramp=ramp, preset=preset
        )
    preset = str(existing.get("preset") or preset_for_kind(kind))
    custom = existing.get("colormap") == "custom"
    color = existing.get("color") if custom else None
    rescaled = dict(existing)
    rescaled["range"] = list(window)
    old = existing.get("range")
    nodes = existing.get("opacity")
    if (
        isinstance(old, (list, tuple))
        and isinstance(nodes, (list, tuple))
        and float(old[1]) > float(old[0])
    ):
        # Keep the shape across a window change: positions move with it.
        low0, high0 = float(old[0]), float(old[1])
        rescaled["opacity"] = [
            [window[0] + (float(x) - low0) / (high0 - low0) * (window[1] - window[0]), float(a)]
            for x, a in nodes
        ]
    placed = rescaled.get("opacity")
    if isinstance(placed, (list, tuple)) and len(placed) >= 2:
        # Keep the nodes exactly: a producer's curve, or one placed node by
        # node in the editor, must not collapse to the six-point sampling
        # on the next render. The ramp is derived from them for the sliders.
        return transfer_function_from_editor(
            window=window,
            colormap="custom" if custom else colormap,
            ramp=[],
            preset=preset,
            color=color if isinstance(color, (list, tuple)) else None,
            opacity_nodes=placed,
        )
    return transfer_function_from_editor(
        window=window,
        colormap="custom" if custom else colormap,
        ramp=ramp_from_transfer_function(rescaled),
        preset=preset,
        color=color if isinstance(color, (list, tuple)) else None,
    )
