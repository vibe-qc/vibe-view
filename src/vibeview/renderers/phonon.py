"""Phonon renderers — phonon band structure + phonon DOS (2D Plotly panels).

Phonon bands mirror the electronic :class:`~vibeview.renderers.bands.BandsRenderer`
(a q-path through the Brillouin zone + frequencies in cm^-1) but carry no spin
channel and no Fermi level; the zero-frequency line marks the acoustic floor.
Phonon DOS mirrors the electronic DOS renderer (a frequency grid + g(omega)).

The viewer plots the producer-computed frequencies faithfully — no lattice
dynamics is solved here, so there is no scientific dependency to cite beyond the
producer's own phonon method (which carries its citation on the producer side).

Soft (imaginary) modes are stored by the writer as *negative* frequencies; both
renderers draw them as-is below the omega = 0 line rather than clipping, because
a negative branch is a physically meaningful red flag (dynamical instability)
the user must be able to see.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import plotly.graph_objects as go

from vibeview.renderers import BaseRenderer
from vibeview.renderers._plotly import chart_layout, figure_to_html, segment_lines, zero_line
from vibeview.renderers.bands import band_axis_ticks
from vibeview.renderers.palette import ACCENT_BLUE, ACCENT_GREEN

if TYPE_CHECKING:
    from vibeview.qvf import PhononBandsData, PhononDOSData, QVFReader, Section


class PhononBandsRenderer(BaseRenderer):
    """Phonon dispersion omega(q): frequencies (cm^-1) along a BZ q-path."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: PhononBandsData | None = None

    def load(self) -> PhononBandsData:
        if self._data is None:
            self._data = self.reader.read_phonon_bands(self.section_id)
        return self._data

    def render_to_html(self, include_plotlyjs: str | bool = "cdn") -> str:
        """Interactive Plotly dispersion plot (hover: q-index + frequency)."""
        data = self.load()
        qpath = data.qpath
        freq = np.asarray(data.frequencies, dtype=float)  # [n_q, n_modes]
        n_q, n_modes = freq.shape

        fig = go.Figure()
        for m in range(n_modes):
            fig.add_trace(
                go.Scatter(
                    x=list(range(n_q)),
                    y=freq[:, m],
                    mode="lines",
                    line={"color": ACCENT_BLUE, "width": 1},
                    name=f"Mode {m + 1}",
                    showlegend=False,
                    hovertemplate="q-pt: %{x}<br>ν: %{y:.2f} cm⁻¹<extra></extra>",
                )
            )

        # Acoustic floor at omega = 0. A negative (soft) branch dipping below
        # this line is an imaginary mode the user should notice, so mark the
        # zero line rather than clamping the y-range.
        zero_line(fig)

        # q-path segments reuse the electronic k-path tick helper (segments
        # carry `n_points`, which `band_axis_ticks` already normalizes).
        tick_vals, tick_text, boundaries = band_axis_ticks(qpath, n_q)
        segment_lines(fig, boundaries)

        chart_layout(
            fig,
            title=qpath.get("title", "Phonon Band Structure"),
            x_title="q-point",
            xaxis={"tickvals": tick_vals, "ticktext": tick_text} if tick_vals else None,
            y_title="Frequency (cm⁻¹)",
            legend="default",
        )
        return figure_to_html(fig, include_plotlyjs=include_plotlyjs)


class PhononDOSRenderer(BaseRenderer):
    """Phonon density of states g(omega) vs frequency (cm^-1)."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: PhononDOSData | None = None

    def load(self) -> PhononDOSData:
        if self._data is None:
            self._data = self.reader.read_phonon_dos(self.section_id)
        return self._data

    def render_to_html(self, include_plotlyjs: str | bool = "cdn") -> str:
        """Interactive Plotly DOS plot (filled line, hover: frequency + g)."""
        data = self.load()
        freq = np.asarray(data.frequencies, dtype=float)
        dos = np.asarray(data.dos, dtype=float)

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=freq,
                y=dos,
                mode="lines",
                fill="tozeroy",
                name="Phonon DOS",
                line={"color": ACCENT_GREEN, "width": 1.5},
                hovertemplate="ν: %{x:.2f} cm⁻¹<br>g: %{y:.4f}<extra></extra>",
            )
        )
        # Mark omega = 0 so a DOS with weight at negative frequency (soft modes)
        # is read correctly rather than mistaken for the low-frequency edge.
        zero_line(fig, axis="x")

        chart_layout(
            fig,
            title="Phonon DOS",
            x_title="Frequency (cm⁻¹)",
            y_title="DOS (states / cm⁻¹)",
            legend="default",
        )
        return figure_to_html(fig, include_plotlyjs=include_plotlyjs)
