"""Trajectory renderer — frame-by-frame animation of geometry optimisation or IRC.

Play/pause/step controls. Energy plot below the structure view that
updates with the current frame.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from vibeview.renderers import BaseRenderer
from vibeview.renderers._frames import FrameSequenceMixin
from vibeview.renderers.palette import ACCENT_BLUE, ACCENT_RED

if TYPE_CHECKING:
    from matplotlib.axes import Axes

    from vibeview.qvf import QVFReader, Section, TrajectoryData


class TrajectoryRenderer(FrameSequenceMixin, BaseRenderer):
    """Frame-by-frame geometry animation."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: TrajectoryData | None = None
        self._frame_pngs = {}

    def load(self) -> TrajectoryData:
        if self._data is None:
            self._data = self.reader.read_trajectory(self.section_id)
        return self._data

    def _draw_energy_figure(self, ax: Axes, energies: np.ndarray, frame: int) -> None:
        n = len(energies)
        ax.plot(range(n), energies, "o-", color=ACCENT_BLUE, markersize=3, linewidth=1.0)
        ax.plot(frame, energies[frame], "o", color=ACCENT_RED, markersize=8)
        ax.set_xlabel("Frame")
        ax.set_ylabel("Energy (a.u.)")
        ax.grid(True, alpha=0.3)
