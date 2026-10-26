"""Volume-series renderer for the ``volume.series`` kind (#158).

A ``volume.series`` section holds ``n`` scalar fields ("slabs") on one grid,
such as the density along a trajectory, a time-dependent density or the
densities of several states; its ``axis`` says what indexes them (QVF spec
§ 5.2). One slab is drawn at a time, and the viewer steps through them as it
steps trajectory frames.

* **Colour.** As a reaction path colours its per-frame fields: one surface
  for a non-negative series, the ± lobes in the shared phase colours for a
  series with negative values. Signedness is decided once for the whole
  series, so the colours do not flip from slab to slab.
* **Isovalue.** The section's hint (0.05 by default); when no slab reaches
  it, half the largest |value| of the series, so a small field does not
  open empty.
* **Geometry.** With ``kind: "frame"`` and a ``trajectory_ref``, slab ``s``
  belongs to frame ``values[s]`` of that trajectory or reaction path, and is
  drawn on that frame's atoms.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from vibeview.renderers import BaseRenderer
from vibeview.renderers.palette import FRAME_VOLUME, ISO_NEGATIVE, ISO_POSITIVE

if TYPE_CHECKING:
    import pyvista as pv

    from vibeview.qvf import QVFReader, Section, VolumeSeriesData

#: Actor-name prefix of the surfaces (``vibeview.app.scene._Actors.SERIES``).
SERIES_PREFIX = "volume_series_"
_AXIS_NAMES = {"time": "t", "frame": "frame", "state": "state"}


def _check_slab(data: VolumeSeriesData, slab: int) -> int:
    n = len(data.data)
    if not 0 <= slab < n:
        raise ValueError(f"slab {slab} is out of range: the series has {n}, numbered 0 to {n - 1}")
    return slab


def slab_label(data: VolumeSeriesData, slab: int) -> str:
    """The slab's own label, else its axis value: ``r = 0.90 Å``, ``t = 10 fs``."""
    axis = data.axis
    _check_slab(data, slab)
    if axis.labels is not None:
        return axis.labels[slab]
    value = f"{axis.values[slab]:g}" + (f" {axis.units}" if axis.units else "")
    name = _AXIS_NAMES.get(axis.kind)
    if name is None:
        return value
    return f"{name} = {value}" if axis.kind == "time" else f"{name} {value}"


def is_signed(data: VolumeSeriesData) -> bool:
    """Whether any slab has a negative value, which draws ± lobes."""
    return bool(np.any(np.asarray(data.data) < 0))


def max_abs(data: VolumeSeriesData) -> float:
    """The largest finite |value| over every slab, 0.0 when there is none."""
    values = np.abs(np.asarray(data.data, dtype=float))
    finite = values[np.isfinite(values)]
    return float(finite.max()) if finite.size else 0.0


def opening_isovalue(data: VolumeSeriesData, hinted: float) -> float:
    """``hinted``, or half the largest |value| when no slab reaches it."""
    peak = max_abs(data)
    if peak > 0 and hinted >= peak:
        return 0.5 * peak
    return hinted


def trajectory_frame(data: VolumeSeriesData, slab: int) -> int | None:
    """The trajectory frame slab ``slab`` belongs to, or ``None`` when it names none."""
    _check_slab(data, slab)
    if data.axis.kind != "frame" or data.axis.trajectory_ref is None:
        return None
    return int(data.axis.values[slab])


def slab_surfaces(
    data: VolumeSeriesData, slab: int, isovalue: float, *, signed: bool | None = None,
) -> list[tuple[str, str, pv.PolyData]]:
    """``(suffix, colour, mesh)`` for each non-empty surface of ``slab``."""
    from vibeview.renderers.volume import build_isosurface_mesh

    _check_slab(data, slab)
    if not math.isfinite(isovalue) or isovalue <= 0:
        raise ValueError("isovalue must be finite and positive")
    field = np.asarray(data.data[slab], dtype=float)
    two_signs = is_signed(data) if signed is None else signed
    levels = (
        [("pos", ISO_POSITIVE, field), ("neg", ISO_NEGATIVE, -field)]
        if two_signs else [("0", FRAME_VOLUME, field)]
    )
    surfaces = []
    for suffix, colour, values in levels:
        mesh = build_isosurface_mesh(values, data.grid, isovalue)
        if mesh is not None and mesh.n_points:
            surfaces.append((suffix, colour, mesh))
    return surfaces


def add_series_slab(
    plotter: pv.Plotter,
    data: VolumeSeriesData,
    slab: int,
    isovalue: float,
    *,
    opacity: float = 0.6,
    name: str = SERIES_PREFIX + "surface",
) -> int:
    """Draw ``slab`` at ``isovalue`` into ``plotter``; return the surfaces drawn."""
    surfaces = slab_surfaces(data, slab, isovalue)
    for suffix, colour, mesh in surfaces:
        plotter.add_mesh(
            mesh, color=colour, opacity=opacity, name=f"{name}_{suffix}", show_scalar_bar=False
        )
    return len(surfaces)


def describe(data: VolumeSeriesData, slab: int, isovalue: float, drawn: int) -> str:
    """``slab 2 of 3, r = 0.90 Å, at 0.05 (largest 0.27)`` for a status line."""
    where = f"slab {slab + 1} of {len(data.data)}, {slab_label(data, slab)}"
    peak = f"largest {max_abs(data):.3g}"
    if not drawn:
        return f"{where}: no surface at {isovalue:.3g}; {peak}, lower the isovalue"
    return f"{where}, at {isovalue:.3g} ({peak})"


def frame_atoms(
    reader: QVFReader, data: VolumeSeriesData, slab: int
) -> list[tuple[str, np.ndarray]] | None:
    """The atoms of the trajectory frame ``slab`` belongs to, in Å, or ``None``.

    ``None`` when the axis names no trajectory frame, or the frame is not
    in the referenced section; the slab is then drawn on the shown structure.
    """
    from vibeview.renderers.reaction import ReactionPathRenderer
    from vibeview.renderers.trajectory import TrajectoryRenderer

    frame = trajectory_frame(data, slab)
    reference = data.axis.trajectory_ref
    if frame is None or reference is None or not reader.has_section(reference):
        return None
    section = reader.get_section(reference)
    renderer: TrajectoryRenderer | ReactionPathRenderer
    if section.kind == "reaction.path":
        renderer = ReactionPathRenderer(section, reader)
    elif section.kind == "trajectory":
        renderer = TrajectoryRenderer(section, reader)
    else:
        return None
    renderer.load()
    if not 0 <= frame < renderer.n_frames:
        return None
    return renderer.get_frame(frame)


class VolumeSeriesRenderer(BaseRenderer):
    """One slab at a time of a ``volume.series`` section."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: VolumeSeriesData | None = None

    def load(self) -> VolumeSeriesData:
        if self._data is None:
            self._data = self.reader.read_volume_series(self.section_id)
        return self._data
