"""Scan-surface renderer (`scan.surface`).

A 2D relaxed-scan energy surface — two driven internal coordinates on
the axes, relaxed energy as a filled contour map. The minimum is marked
with a star; an optional current-node marker tracks the selected grid
point. Rendered with matplotlib (Agg) to a PNG, mirroring the
reaction-path energy-plot pattern so the app can show it in the 2D plot
panel without PyVista.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, ScanSurfaceData, Section

matplotlib.use("Agg")


class ScanSurfaceRenderer(BaseRenderer):
    """Filled-contour renderer for the `scan.surface` kind."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: ScanSurfaceData | None = None

    def load(self) -> ScanSurfaceData:
        if self._data is None:
            self._data = self.reader.read_scan_surface(self.section_id)
        return self._data

    @property
    def shape(self) -> tuple[int, int]:
        e = self.load().energies
        return (int(e.shape[0]), int(e.shape[1]))

    def _axis_label(self, label: str | None, unit: str | None, fallback: str) -> str:
        if label:
            return f"{label} ({unit})" if unit else label
        return fallback

    def min_node(self) -> tuple[int, int]:
        """(ia, ib) of the global energy minimum."""
        e = self.load().energies
        flat = int(np.argmin(e))
        return (flat // e.shape[1], flat % e.shape[1])

    def render_surface(self, current_node: tuple[int, int] | None = None) -> bytes:
        """Filled-contour energy map. ``current_node`` (ia, ib), if given,
        is marked with a crosshair."""
        data = self.load()
        e = np.asarray(data.energies, dtype=np.float64)
        if e.size == 0:
            return b""
        # ``contourf`` needs at least a 2×2 grid. A 1×N / N×1 scan is a valid
        # archive (the writer doesn't require ≥2 nodes per axis) but is really
        # a 1-D scan — plot it as a line instead of crashing in matplotlib.
        if min(e.shape) < 2:
            return self._render_line(data, e)
        a = np.asarray(data.axis_a, dtype=np.float64)
        b = np.asarray(data.axis_b, dtype=np.float64)

        # Energies relative to the minimum, in a readable unit. e is
        # [nA, nB] with axis A down rows, axis B across columns; for the
        # plot we put coordinate A on x and B on y, so transpose.
        rel = e - float(np.min(e))

        fig, ax = plt.subplots(figsize=(5.2, 4.2))
        cf = ax.contourf(
            a, b, rel.T, levels=24, cmap="viridis"
        )
        ax.contour(a, b, rel.T, levels=12, colors="white", linewidths=0.3, alpha=0.5)
        cbar = fig.colorbar(cf, ax=ax)
        cbar.set_label("E − E_min (a.u.)")

        ia_min, ib_min = self.min_node()
        ax.plot(
            a[ia_min], b[ib_min], marker="*", color="#FFD700",
            markersize=16, markeredgecolor="black", markeredgewidth=0.6,
            zorder=5, label="minimum",
        )
        if current_node is not None:
            ia, ib = current_node
            if 0 <= ia < len(a) and 0 <= ib < len(b):
                ax.plot(
                    a[ia], b[ib], marker="+", color="#FF3333",
                    markersize=14, markeredgewidth=2.0, zorder=6,
                )

        ax.set_xlabel(
            self._axis_label(
                data.coordinate_a_label, data.coordinate_a_unit, "Coordinate A"
            )
        )
        ax.set_ylabel(
            self._axis_label(
                data.coordinate_b_label, data.coordinate_b_unit, "Coordinate B"
            )
        )
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    def _render_line(self, data: ScanSurfaceData, e: np.ndarray) -> bytes:
        """1-D fallback when one scan axis has a single node (``contourf``
        needs a 2×2 grid). Plots relative energy along the axis that varies.
        """
        a = np.asarray(data.axis_a, dtype=np.float64)
        b = np.asarray(data.axis_b, dtype=np.float64)
        if e.shape[0] >= e.shape[1]:
            x, e_line = a, e[:, 0]
            xlabel = self._axis_label(
                data.coordinate_a_label, data.coordinate_a_unit, "Coordinate A"
            )
        else:
            x, e_line = b, e[0, :]
            xlabel = self._axis_label(
                data.coordinate_b_label, data.coordinate_b_unit, "Coordinate B"
            )
        rel = np.asarray(e_line, dtype=np.float64) - float(np.min(e))

        fig, ax = plt.subplots(figsize=(5.2, 4.2))
        ax.plot(x, rel, marker="o", color="#3366CC")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("E − E_min (a.u.)")
        ax.set_title("1-D scan (single node on the other axis)")
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        buf.seek(0)
        return buf.read()
