"""Vector-field renderer for the ``volume.vector`` kind (#156).

A ``volume.vector`` section holds a Cartesian vector at every grid point, such
as a density gradient or an electric field (QVF spec § 5.2). It is drawn as
arrows on a subsampled lattice of its grid points:

* **Stride.** Every ``stride``-th point along each grid axis carries an arrow.
  Unset, the smallest stride that keeps the arrow count at or below
  ``max_arrows`` is used, so a small grid keeps every point.
* **Length.** An arrow's length is ``|v| / reference`` of the lattice spacing
  (times ``length_scale``), capped at 0.9 of it so neighbours do not touch.
  ``reference`` is the 95th percentile of the sampled nonzero magnitudes,
  so one huge vector near a nucleus does not shrink every other arrow.
* **Cutoff.** Arrows shorter than ``cutoff`` × ``reference`` are dropped.
* **Colour.** By magnitude, in the section's own unit.

The same glyphs serve the browser, ``vibe-view capture`` and ``batch``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    import pyvista as pv

    from vibeview.qvf import QVFReader, Section, VectorFieldData

#: Actor-name prefix of the arrows (``vibeview.app.scene._Actors.VECTOR``).
VECTOR_PREFIX = "vector_field_"
#: The default ceiling on drawn arrows.
MAX_ARROWS = 4000
#: The default cutoff, as a fraction of the reference magnitude.
DEFAULT_CUTOFF = 0.05
#: The longest arrow, as a fraction of the lattice spacing.
_MAX_FILL = 0.9


@dataclass
class VectorGlyphs:
    """The arrows one vector field draws, in the Å scene."""

    points: np.ndarray  # [n, 3] Å, arrow tails
    directions: np.ndarray  # [n, 3] unit vectors
    lengths: np.ndarray  # [n] Å
    magnitudes: np.ndarray  # [n] in the field's unit
    stride: int
    reference: float  # 95th-percentile sampled magnitude
    n_sampled: int  # lattice points considered, before the cutoff

    def __len__(self) -> int:
        return len(self.points)


def auto_stride(shape: tuple[int, int, int], max_arrows: int = MAX_ARROWS) -> int:
    """The smallest stride whose subsampled lattice has at most ``max_arrows`` points."""
    if max_arrows < 1:
        raise ValueError("max_arrows must be at least 1")
    stride = 1
    while math.prod(math.ceil(n / stride) for n in shape) > max_arrows:
        stride += 1
    return stride


def vector_glyphs(
    data: VectorFieldData,
    *,
    stride: int | None = None,
    max_arrows: int = MAX_ARROWS,
    length_scale: float = 1.0,
    cutoff: float = DEFAULT_CUTOFF,
) -> VectorGlyphs:
    """Arrows for ``data`` on every ``stride``-th grid point (see the module notes)."""
    if stride is not None and stride < 1:
        raise ValueError("stride must be at least 1")
    if not math.isfinite(length_scale) or length_scale <= 0:
        raise ValueError("length_scale must be finite and positive")
    if not math.isfinite(cutoff) or cutoff < 0:
        raise ValueError("cutoff must be finite and non-negative")
    grid = data.grid
    step = stride if stride is not None else auto_stride(grid.shape, max_arrows)
    sampled = np.asarray(data.vectors, dtype=float)[::step, ::step, ::step]
    nx, ny, nz = sampled.shape[:3]
    index = np.stack(
        np.meshgrid(
            np.arange(nx) * step, np.arange(ny) * step, np.arange(nz) * step, indexing="ij"
        ),
        axis=-1,
    ).reshape(-1, 3)
    vectors = sampled.reshape(-1, 3)
    points = (grid.origin + index @ grid.voxel_vectors) * grid.to_angstrom
    magnitudes = np.linalg.norm(vectors, axis=1)
    finite = np.isfinite(magnitudes) & np.isfinite(points).all(axis=1)
    nonzero = finite & (magnitudes > 0)
    reference = float(np.percentile(magnitudes[nonzero], 95)) if nonzero.any() else 0.0
    keep = nonzero & (magnitudes >= cutoff * reference) if reference > 0 else nonzero
    spacing = step * float(np.linalg.norm(grid.voxel_vectors, axis=1).min()) * grid.to_angstrom
    relative = np.minimum(magnitudes[keep] / reference, 1.0) if reference > 0 else magnitudes[keep]
    return VectorGlyphs(
        points=points[keep],
        directions=vectors[keep] / magnitudes[keep, None],
        lengths=_MAX_FILL * spacing * np.minimum(length_scale * relative, 1.0),
        magnitudes=magnitudes[keep],
        stride=step,
        reference=reference,
        n_sampled=len(points),
    )


def glyph_mesh(glyphs: VectorGlyphs) -> pv.PolyData | None:
    """One arrow mesh for all glyphs, carrying a ``magnitude`` point array."""
    import pyvista as pv

    if not len(glyphs):
        return None
    cloud = pv.PolyData(np.asarray(glyphs.points, dtype=float))
    cloud["vectors"] = glyphs.directions * glyphs.lengths[:, None]
    cloud["magnitude"] = glyphs.magnitudes
    arrow = pv.Arrow(tip_length=0.3, tip_radius=0.12, shaft_radius=0.04)
    mesh: pv.PolyData = cloud.glyph(orient="vectors", scale="vectors", factor=1.0, geom=arrow)
    return mesh


def add_vector_glyphs(
    plotter: pv.Plotter, glyphs: VectorGlyphs, *, colormap: str = "viridis",
    name: str = VECTOR_PREFIX + "arrows",
) -> bool:
    """Add the arrows to ``plotter``, coloured by magnitude; ``False`` when none."""
    from vibeview.plotter_compat import cmap_argument

    mesh = glyph_mesh(glyphs)
    if mesh is None or not mesh.n_points:
        return False
    low, high = float(glyphs.magnitudes.min()), float(glyphs.magnitudes.max())
    plotter.add_mesh(
        mesh,
        scalars="magnitude",
        cmap=cmap_argument(colormap),
        clim=(low, high if high > low else low + 1.0),
        name=name,
        show_scalar_bar=False,
    )
    return True


def describe(glyphs: VectorGlyphs, unit: str | None) -> str:
    """``120 arrows, every 2nd grid point, |v| 0.01 to 3.2 a.u.`` for a status line."""
    if not len(glyphs):
        return f"no arrows above the cutoff (of {glyphs.n_sampled} grid points)"
    spacing = (
        "every grid point" if glyphs.stride == 1
        else f"every {_ordinal(glyphs.stride)} grid point"
    )
    suffix = f" {unit}" if unit else ""
    return (
        f"{len(glyphs)} arrows, {spacing}, |v| {glyphs.magnitudes.min():.3g} "
        f"to {glyphs.magnitudes.max():.3g}{suffix}"
    )


def _ordinal(n: int) -> str:
    """``2nd``, ``3rd``, ``11th``."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class VectorFieldRenderer(BaseRenderer):
    """Arrow glyphs for a ``volume.vector`` section."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: VectorFieldData | None = None

    def load(self) -> VectorFieldData:
        if self._data is None:
            self._data = self.reader.read_volume_vector(self.section_id)
        return self._data

    def glyphs(
        self, *, stride: int | None = None, length_scale: float = 1.0,
        cutoff: float = DEFAULT_CUTOFF,
    ) -> VectorGlyphs:
        return vector_glyphs(
            self.load(), stride=stride, length_scale=length_scale, cutoff=cutoff
        )
