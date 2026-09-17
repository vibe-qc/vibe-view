"""DOS renderer — total and projected density of states charts.

Interactive Plotly charts with hover tooltips:
  - ``dos.total`` — line chart of total DOS
  - ``dos.projected`` — stacked area chart of per-channel PDOS
Can be combined with a bands plot for side-by-side display.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import plotly.graph_objects as go

from vibeview.renderers import BaseRenderer
from vibeview.renderers.energy_window import (
    EnergyWindow,
    apply_axis_range,
    auto_energy_window,
    dos_support,
    resolve_window,
)

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, Section


class DOSRenderer(BaseRenderer):
    """Density of states chart (total or projected).

    For ``dos.total``: renders a single line chart.
    For ``dos.projected``: renders a stacked area plot with one
    trace per channel, labeled from the section metadata ``channels``
    list.
    """

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._energies: np.ndarray | None = None
        self._dos: np.ndarray | None = None
        self._channels: list[dict] | None = None
        self._loaded: bool = False

    @property
    def is_projected(self) -> bool:
        return self.section.kind == "dos.projected"

    def _section_meta(self, key: str, default=None):
        """Read a section-level metadata key.

        The QVF writer stores DOS metadata (``channels``, ``n_spin``,
        ``fermi_energy_ev`` …) as section-level JSON keys, which Pydantic's
        ``extra="allow"`` lands in ``model_extra`` — *not* as direct
        attributes. ``getattr(section, key)`` would silently miss them.
        """
        extra = getattr(self.section, "model_extra", None) or {}
        return extra.get(key, default)

    def load(self) -> tuple[np.ndarray, np.ndarray]:
        """Load energies and DOS/projections arrays from the archive.

        Member layout (QVF spec §4.8/§4.9):
          * ``dos.total``     — members ``energies`` [n_pts],
            ``dos`` [n_pts] or [2, n_pts] (spin axis leading).
          * ``dos.projected`` — members ``energies`` [n_pts],
            ``projections`` [n_ch, n_pts] or [2, n_ch, n_pts].

        ``_read_binary_member`` already reshapes to the member's declared
        ``shape``, so no manual reshape is needed.
        """
        if not self._loaded:
            self._energies = self.reader._read_binary_member(self.section.id, "energies")

            if self.is_projected:
                proj = self.reader._read_binary_member(self.section.id, "projections")
                # Spin-projected [2, n_ch, n_pts] → collapse spin (sum) so the
                # stacked-area renderer sees one trace per channel.
                if proj.ndim == 3:
                    proj = proj.sum(axis=0)
                self._dos = proj
                self._channels = self._section_meta("channels", []) or []
            else:
                # dos is already [n_pts] or [2, n_pts] from the member shape.
                self._dos = self.reader._read_binary_member(self.section.id, "dos")
            self._loaded = True
        return self._energies, self._dos

    def default_energy_window(self) -> EnergyWindow | None:
        """The valence window this DOS would open at (#26).

        QVF §4.3 stores the grid in eV relative to E_F, so the energies
        need no shift. The window is computed from the grid points that
        actually carry weight, but measured against the whole grid —
        that is the range the axis would otherwise autoscale to.
        """
        energies, dos = self.load()
        e = np.asarray(energies, dtype=float)
        if e.size == 0:
            return None
        return auto_energy_window(
            dos_support(e, dos), full_range=(float(e.min()), float(e.max()))
        )

    def render_to_html(
        self,
        include_plotlyjs: str | bool = "cdn",
        *,
        energy_window: EnergyWindow | None = None,
        auto_window: bool = True,
    ) -> str:
        """Render the DOS as an interactive Plotly chart.

        QVF §4.3 stores DOS energies in eV relative to E_F = 0.
        The optional absolute Fermi metadata never shifts that grid.

        ``energy_window`` limits the energy axis to ``(min, max)`` in eV.
        Left unset, the chart opens on :meth:`default_energy_window`; pass
        ``auto_window=False`` for the full autoscale (#26).
        """
        energies, dos = self.load()
        window = resolve_window(
            energy_window, auto=auto_window, compute=self.default_energy_window
        )
        energies = np.asarray(energies, dtype=float)
        x_title = "E − E_F (eV)"
        fermi_referenced = True

        fig = go.Figure()

        if self.is_projected:
            # dos.projected: stacked area plot, one trace per channel.
            channels = self._channels or []
            n_channels = dos.shape[0]
            for i in range(n_channels):
                ch = channels[i] if i < len(channels) else None
                if isinstance(ch, dict):
                    label = ch.get("label", f"ch{i}")
                elif ch is not None:
                    label = str(ch)
                else:
                    label = f"ch{i}"
                fig.add_trace(
                    go.Scatter(
                        x=energies,
                        y=dos[i],
                        mode="lines",
                        fill="tonexty" if i > 0 else "none",
                        stackgroup="one",
                        name=label,
                        hovertemplate=("E: %{x:.3f} eV<br>" + label + ": %{y:.4f}<extra></extra>"),
                    )
                )
            y_title = "PDOS (states / eV / cell)"
        elif dos.ndim == 2 and dos.shape[0] == 2:
            # Spin-polarized
            fig.add_trace(
                go.Scatter(
                    x=energies,
                    y=dos[0],
                    mode="lines",
                    fill="tozeroy",
                    name="DOS α (↑)",
                    line={"color": "#3366CC", "width": 1.5},
                    hovertemplate="E: %{x:.3f} eV<br>DOS: %{y:.4f}<extra></extra>",
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=energies,
                    y=-dos[1],
                    mode="lines",
                    fill="tozeroy",
                    name="DOS β (↓)",
                    line={"color": "#CC3333", "width": 1.5},
                    hovertemplate="E: %{x:.3f} eV<br>DOS: %{y:.4f}<extra></extra>",
                )
            )
            y_title = "DOS (states / eV / spin)"
        else:
            dos_1d = dos if dos.ndim == 1 else dos[0]
            fig.add_trace(
                go.Scatter(
                    x=energies,
                    y=dos_1d,
                    mode="lines",
                    fill="tozeroy",
                    name="Total DOS",
                    line={"color": "#3366CC", "width": 1.5},
                    hovertemplate="E: %{x:.3f} eV<br>DOS: %{y:.4f}<extra></extra>",
                )
            )
            y_title = "DOS (states / eV)"

        if fermi_referenced:
            fig.add_vline(
                x=0.0,
                line={"color": "#CC3333", "width": 1, "dash": "dash"},
                annotation_text="E<sub>F</sub>",
                annotation_position="top right",
            )

        fig.update_layout(
            title=("Density of States" if not self.is_projected else "Projected DOS"),
            xaxis=apply_axis_range({"title": x_title}, window),
            yaxis={"title": y_title},
            template="plotly_dark",
            height=450,
            margin={"l": 60, "r": 20, "t": 50, "b": 50},
        )
        return fig.to_html(full_html=False, include_plotlyjs=include_plotlyjs)


def make_bands_dos_html(
    bands_fragment: str,
    dos_fragment: str,
    title: str = "Band Structure & DOS",
) -> str:
    """Combine bands and DOS Plotly figures into a side-by-side layout.

    ``bands_fragment`` and ``dos_fragment`` must each be a Plotly HTML
    fragment produced with ``include_plotlyjs=False`` — i.e. a
    ``<div>…</div>`` *plus* its inline ``Plotly.newPlot(...)`` draw
    script, but without the Plotly library itself. We load Plotly once
    in the wrapper ``<head>`` (a render-blocking ``<script src>`` that
    executes before the body's inline draw scripts), so both figures
    actually render. The previous implementation regex-extracted only
    the ``<div>`` and dropped the draw script, leaving two empty boxes.
    """
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
    <style>
        body {{ margin: 0; background: #111; color: #ccc; }}
        .container {{ display: flex; width: 100%; height: 100vh; }}
        .panel {{ flex: 1; min-width: 0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="panel">{bands_fragment}</div>
        <div class="panel">{dos_fragment}</div>
    </div>
</body>
</html>"""


def bands_dos_energy_window(bands_renderer, dos_renderer) -> EnergyWindow | None:
    """The valence window the combined panel would open at (#26).

    The two panels share one energy axis, so the window has to frame both:
    it is computed from the band eigenvalues and the weighted part of the
    DOS grid together, against the full range of both. Returns ``None``
    when the bands carry no Fermi reference, because then the two axes are
    on different references and nothing can be windowed jointly.
    """
    bd = bands_renderer.load()
    if bd.fermi is None or bd.fermi == 0.0:
        return None
    energies, dos = dos_renderer.load()
    e_dos = np.asarray(energies, dtype=float)
    e_bands = np.asarray(bd.eigenvalues, dtype=float).ravel() - float(bd.fermi)
    states = np.concatenate([e_bands, dos_support(e_dos, dos)])
    # The axis would otherwise autoscale over whichever panel reaches further.
    spans = [e_bands] + ([e_dos] if e_dos.size else [])
    full_lo = min(float(a.min()) for a in spans)
    full_hi = max(float(a.max()) for a in spans)
    return auto_energy_window(states, full_range=(full_lo, full_hi))


def render_bands_dos_combined(
    bands_renderer,
    dos_renderer,
    *,
    title: str = "Band Structure & DOS",
    include_plotlyjs: str | bool = "cdn",
    energy_window: EnergyWindow | None = None,
    auto_window: bool = True,
) -> str:
    """Serialize the combined bands+DOS figure to a standalone HTML fragment.

    See :func:`_bands_dos_figure` for the energy-reference conventions and
    :func:`bands_dos_energy_window` for the default window.
    """
    fig = _bands_dos_figure(
        bands_renderer,
        dos_renderer,
        title=title,
        energy_window=energy_window,
        auto_window=auto_window,
    )
    return fig.to_html(full_html=False, include_plotlyjs=include_plotlyjs)


def _bands_dos_figure(
    bands_renderer,
    dos_renderer,
    *,
    title: str = "Band Structure & DOS",
    energy_window: EnergyWindow | None = None,
    auto_window: bool = True,
):
    """Bands (left) and rotated DOS (right), aligned when E_F is known.

    QVF §4.3 stores DOS energies relative to E_F already; bands carry
    absolute eV eigenvalues and their own kpath.fermi reference. Without
    that reference, keep separately labelled axes rather than imply alignment.

    ``energy_window`` limits the shared energy axis to ``(min, max)`` in eV
    relative to E_F; unset, it opens on :func:`bands_dos_energy_window`
    (#26). ``auto_window=False`` restores the full autoscale.
    """
    from plotly.subplots import make_subplots

    from vibeview.renderers.bands import _band_axis_ticks

    window = resolve_window(
        energy_window,
        auto=auto_window,
        compute=lambda: bands_dos_energy_window(bands_renderer, dos_renderer),
    )

    bd = bands_renderer.load()
    eig = bd.eigenvalues  # [n_spin, n_k, n_bands]
    n_spin, n_kpts, n_bands = eig.shape
    b_fermi = bd.fermi

    energies, dos = dos_renderer.load()
    energies = np.asarray(energies, dtype=float)
    # Each section has its own energy convention (QVF §4.3). Subtracting
    # an absolute reference from the DOS grid a second time corrupts it.
    b_shift = float(b_fermi) if b_fermi is not None else 0.0
    e_dos = energies
    referenced = b_fermi is not None

    fig = make_subplots(
        rows=1, cols=2, shared_yaxes=referenced,
        column_widths=[0.72, 0.28], horizontal_spacing=0.02 if referenced else 0.10,
        subplot_titles=("Band structure", "DOS"),
    )

    colors = ["#3366CC", "#CC8833", "#66AA55", "#AA66CC"]
    for spin in range(n_spin):
        for b in range(n_bands):
            fig.add_trace(
                go.Scatter(
                    x=list(range(n_kpts)),
                    y=eig[spin, :, b] - b_shift,
                    mode="lines",
                    line={"color": colors[spin % len(colors)], "width": 1},
                    name=f"spin {spin + 1} band {b + 1}",
                    showlegend=False,
                    hovertemplate="k-pt: %{x}<br>E: %{y:.4f}<extra></extra>",
                ),
                row=1, col=1,
            )

    # DOS rotated: x = DOS magnitude, y = energy. Spin-polarized DOS mirrors
    # the β channel to negative x (the conventional up/down DOS layout).
    if dos.ndim == 2 and dos.shape[0] == 2:
        fig.add_trace(
            go.Scatter(x=dos[0], y=e_dos, mode="lines", name="DOS ↑",
                       line={"color": "#3366CC", "width": 1.2}, showlegend=False),
            row=1, col=2,
        )
        fig.add_trace(
            go.Scatter(x=-dos[1], y=e_dos, mode="lines", name="DOS ↓",
                       line={"color": "#CC3333", "width": 1.2}, showlegend=False),
            row=1, col=2,
        )
    else:
        dos_1d = dos if dos.ndim == 1 else dos[0]
        fig.add_trace(
            go.Scatter(x=dos_1d, y=e_dos, mode="lines", name="DOS", fill="tozerox",
                       line={"color": "#3366CC", "width": 1.2}, showlegend=False),
            row=1, col=2,
        )

    for col in ([1, 2] if referenced else [2]):
        fig.add_hline(
            y=0.0, line={"color": "#CC3333", "width": 1, "dash": "dash"},
            annotation_text="E<sub>F</sub>", annotation_position="top left",
            row=1, col=col,
        )

    tick_vals, tick_text, boundaries = _band_axis_ticks(bd.kpath, n_kpts)
    for start in boundaries:
        fig.add_vline(x=start, line={"color": "#999999", "width": 0.5, "dash": "dot"}, row=1, col=1)

    y_title = "E − E_F (eV)" if referenced else "Energy (eV)"
    fig.update_yaxes(title_text=y_title, row=1, col=1)
    if not referenced:
        fig.update_yaxes(title_text="E − E_F (eV)", side="right", row=1, col=2)
    if window is not None:
        # The window is in eV relative to E_F. The DOS axis is always on that
        # reference (QVF §4.3); the bands axis only when the file carries a
        # Fermi energy — without one it shows absolute eV, and stamping the
        # same numbers on it would window the wrong scale. Set both axes
        # explicitly rather than leaning on shared_yaxes, which links them
        # for interaction but leaves the right panel its own initial range.
        if referenced:
            fig.update_yaxes(range=[window[0], window[1]], row=1, col=1)
        fig.update_yaxes(range=[window[0], window[1]], row=1, col=2)
    x1 = {"title_text": "k-point"}
    if tick_vals:
        x1.update({"tickvals": tick_vals, "ticktext": tick_text})
    fig.update_xaxes(row=1, col=1, **x1)
    fig.update_xaxes(title_text="DOS (states/eV)", row=1, col=2)
    fig.update_layout(
        title=title, template="plotly_dark", hovermode="closest",
        height=500, margin={"l": 60, "r": 20, "t": 60, "b": 50},
    )
    return fig
