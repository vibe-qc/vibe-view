"""Trajectory renderer — frame-by-frame animation of geometry optimisation or IRC.

Play/pause/step controls. Energy plot below the structure view that
updates with the current frame.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, Section, TrajectoryData

matplotlib.use("Agg")


class TrajectoryRenderer(BaseRenderer):
    """Frame-by-frame geometry animation."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: TrajectoryData | None = None
        self._frame_pngs: list[bytes] | None = None  # pre-rendered (A7-04)

    def load(self) -> TrajectoryData:
        if self._data is None:
            self._data = self.reader.read_trajectory(self.section_id)
        return self._data

    @property
    def n_frames(self) -> int:
        data = self.load()
        return data.coords.shape[0]

    @property
    def n_atoms(self) -> int:
        data = self.load()
        return len(data.atoms)

    def get_frame(self, index: int) -> list[tuple[str, np.ndarray]]:
        """Return atom symbols + positions for frame `index`."""
        data = self.load()
        coords = data.coords[index]  # [n_atoms, 3]
        return [(data.atoms[i].symbol, coords[i].copy()) for i in range(self.n_atoms)]

    def has_energies(self) -> bool:
        data = self.load()
        return data.energies is not None and len(data.energies) > 0

    def get_energy(self, index: int) -> float | None:
        data = self.load()
        if data.energies is not None and index < len(data.energies):
            return data.energies[index]
        return None

    def render_energy_plot(self, current_frame: int) -> bytes:
        """Return a pre-rendered PNG of the energy-vs-frame plot for
        ``current_frame``.

        All N frame variants are rendered once and cached the first time
        this method is called (A7-04). Subsequent calls are O(1) cache
        lookups; frame-stepping during playback no longer blocks on a
        matplotlib render per step.
        """
        data = self.load()
        if not data.energies:
            return b""
        if self._frame_pngs is None:
            self._frame_pngs = self._prerender_all_frames(data)
        n = len(self._frame_pngs)
        if n == 0:
            return b""
        idx = max(0, min(current_frame, n - 1))
        return self._frame_pngs[idx]

    def _prerender_all_frames(self, data) -> list[bytes]:
        """Render one PNG per trajectory frame (frame indicator varies;
        everything else is identical). Called once at section load."""
        energies = np.array(data.energies)
        n = len(energies)
        if n == 0:
            return []

        pngs: list[bytes] = []
        for frame in range(n):
            fig, ax = plt.subplots(figsize=(6, 2.5))
            ax.plot(
                range(n), energies, "o-",
                color="#3366CC", markersize=3, linewidth=1.0,
            )
            ax.plot(
                frame, energies[frame], "o",
                color="#CC3333", markersize=8,
            )
            ax.set_xlabel("Frame")
            ax.set_ylabel("Energy (a.u.)")
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=100)
            plt.close(fig)
            buf.seek(0)
            pngs.append(buf.read())
        return pngs
