"""What the frame-sequence renderers (trajectory, reaction path) share.

Both kinds hold ``coords [n_frames, n_atoms, 3]`` with optional per-frame
energies and draw an energy profile with a moving frame marker. The mixin
owns the frame accessors and the energy-plot cache; a subclass supplies
:meth:`_draw_energy_figure` for its own axes and annotations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from vibeview.renderers._mpl import agg_pyplot, figure_to_png

if TYPE_CHECKING:
    from matplotlib.axes import Axes


class _FrameData(Protocol):
    atoms: list[Any]
    coords: np.ndarray
    energies: Any


class FrameSequenceMixin:
    """Frame accessors and a lazily filled per-frame energy-plot cache.

    The plot for a frame is rendered the first time that frame is asked
    for and kept, so stepping through playback costs one matplotlib
    render per distinct frame and nothing on a repeat; the first call no
    longer renders every frame of a long trajectory up front.
    """

    _frame_pngs: dict[int, bytes]

    def load(self) -> _FrameData:  # pragma: no cover - supplied by the renderer
        raise NotImplementedError

    def _draw_energy_figure(self, ax: Axes, energies: np.ndarray, frame: int) -> None:
        """Draw the profile for ``energies`` with ``frame`` marked, on ``ax``."""
        raise NotImplementedError

    @property
    def n_frames(self) -> int:
        return int(self.load().coords.shape[0])

    @property
    def n_atoms(self) -> int:
        return len(self.load().atoms)

    def frame_positions(self, index: int) -> np.ndarray:
        """Array view of one frame, without allocating one tuple per atom."""
        return np.asarray(self.load().coords[index])

    def get_frame(self, index: int) -> list[tuple[str, np.ndarray]]:
        """Atom symbols and positions for frame ``index``."""
        data = self.load()
        coords = self.frame_positions(index)  # [n_atoms, 3]
        return [(data.atoms[i].symbol, coords[i].copy()) for i in range(len(data.atoms))]

    def has_energies(self) -> bool:
        energies = self.load().energies
        return energies is not None and len(energies) > 0

    def get_energy(self, index: int) -> float | None:
        energies = self.load().energies
        if energies is not None and index < len(energies):
            return float(energies[index])
        return None

    def render_energy_plot(self, current_frame: int) -> bytes:
        """The energy profile PNG with ``current_frame`` marked; cached per frame."""
        energies = self.load().energies
        if energies is None or len(energies) == 0:
            return b""
        values = np.asarray(energies, dtype=np.float64)
        frame = max(0, min(int(current_frame), len(values) - 1))
        cached = self._frame_pngs.get(frame)
        if cached is None:
            fig, ax = agg_pyplot().subplots(figsize=self._energy_figsize)
            self._draw_energy_figure(ax, values, frame)
            fig.tight_layout()
            # Where the axes sit in the image and what they span, so a click
            # on the image can be mapped back to a frame; tight_layout has
            # settled the position by now, and it is the same for every frame.
            box = ax.get_position()
            lo, hi = ax.get_xlim()
            self._energy_axis = (float(box.x0), float(box.x1), float(lo), float(hi))
            cached = figure_to_png(fig)
            self._frame_pngs[frame] = cached
        return cached

    _energy_figsize: tuple[float, float] = (6.0, 2.5)
    #: ``(x0, x1, xmin, xmax)``: the energy axes' left and right edges as
    #: fractions of the image width, and the data range they span.
    _energy_axis: tuple[float, float, float, float] | None = None

    def _frame_x_values(self, n_frames: int) -> np.ndarray:
        """The x value each frame is plotted at: the frame index by default."""
        return np.arange(n_frames, dtype=np.float64)

    def frame_at_fraction(self, fraction: float) -> int | None:
        """The frame nearest a click at ``fraction`` of the energy image's width.

        ``fraction`` runs from 0 at the left edge to 1 at the right; a click
        left of the axes lands on the first frame, right of them on the
        last. None before a plot was rendered or without energies.
        """
        axis = self._energy_axis
        energies = self.load().energies
        if axis is None or energies is None or len(energies) == 0:
            return None
        x0, x1, lo, hi = axis
        if x1 <= x0:
            return None
        x = lo + (float(fraction) - x0) / (x1 - x0) * (hi - lo)
        xs = self._frame_x_values(len(energies))
        return int(np.argmin(np.abs(xs - x)))
