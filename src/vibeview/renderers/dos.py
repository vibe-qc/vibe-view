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

    def render_to_html(self, include_plotlyjs: str | bool = "cdn") -> str:
        """Render the DOS as an interactive Plotly chart.

        The Fermi level (``fermi_energy_ev``, section-level metadata) is
        honoured so the user can locate E_F — previously it was ignored, so
        a producer that ships absolute energies (e.g. the committed NaCl
        showcase, spanning thousands of eV) gave an unreadable plot with no
        Fermi marker (audit finding A4-01). When E_F falls inside the energy
        window we Fermi-reference (subtract it, mark x=0), matching the
        native vibe-qc plotter; when it falls outside (suspect / molecular)
        we keep absolute energies but surface E_F in the axis label rather
        than shifting all the data off-screen.
        """
        energies, dos = self.load()
        energies = np.asarray(energies, dtype=float)

        fermi = self._section_meta("fermi_energy_ev", None)
        x_title = "Energy (eV)"
        fermi_referenced = False
        if fermi is not None and energies.size:
            fermi = float(fermi)
            if float(energies.min()) <= fermi <= float(energies.max()):
                energies = energies - fermi
                x_title = "E − E_F (eV)"
                fermi_referenced = True
            else:
                x_title = f"Energy (eV)  ·  E_F = {fermi:.2f} eV (outside range)"

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
            xaxis={"title": x_title},
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


def render_bands_dos_combined(
    bands_renderer,
    dos_renderer,
    *,
    title: str = "Band Structure & DOS",
    include_plotlyjs: str | bool = "cdn",
) -> str:
    """Serialize the combined bands+DOS figure to a standalone HTML fragment.

    See :func:`_bands_dos_figure` for the shared-energy-axis layout and the
    single-Fermi-reference contract (audit A4-04 + follow-up).
    """
    fig = _bands_dos_figure(bands_renderer, dos_renderer, title=title)
    return fig.to_html(full_html=False, include_plotlyjs=include_plotlyjs)


def _bands_dos_figure(
    bands_renderer,
    dos_renderer,
    *,
    title: str = "Band Structure & DOS",
):
    """Bands + DOS in ONE figure with a **shared energy axis** (the standard
    solid-state figure): band structure on the left (E vs k), DOS rotated on
    the right (DOS vs E), the two energy axes aligned, and a single Fermi
    line spanning both (audit finding A4-04).

    Both panels are referenced to the Fermi level so E_F sits at 0 in each:
    the bands use ``kpath.fermi``; the DOS uses its ``fermi_energy_ev`` when
    that level lies within the DOS energy window (matching
    :meth:`DOSRenderer.render_to_html`). When neither carries a Fermi level
    the raw energies are shown and no reference line is drawn.
    """
    from plotly.subplots import make_subplots

    from vibeview.renderers.bands import _band_axis_ticks

    bd = bands_renderer.load()
    eig = bd.eigenvalues  # [n_spin, n_k, n_bands]
    n_spin, n_kpts, n_bands = eig.shape
    b_fermi = bd.fermi

    energies, dos = dos_renderer.load()
    energies = np.asarray(energies, dtype=float)
    d_fermi = dos_renderer._section_meta("fermi_energy_ev", None)
    d_referenced = (
        d_fermi is not None
        and energies.size > 0
        and float(energies.min()) <= float(d_fermi) <= float(energies.max())
    )
    # Reference BOTH panels to a SINGLE Fermi zero, or the shared energy axis
    # is meaningless. Previously the bands were shifted by their own fermi and
    # the DOS by its own, so when the bands carried fermi=0.0 (the writer's
    # "no Fermi level" sentinel) while the DOS had a real in-window E_F, the
    # two panels sat on different zeros under one shared y-axis — the bands
    # offset from the DOS, the single E_F line correct only for the DOS, and
    # the axis mislabeled "E − E_F". Prefer the DOS in-window Fermi (a concrete
    # value on the energy grid); fall back to the bands Fermi, treating 0.0 as
    # "no reference" (matching the always-eV bands axis, A4-02). Draw the E_F
    # line + "E − E_F" label only when both panels share that zero. (A4-04 follow-up)
    if d_referenced:
        e_ref: float | None = float(d_fermi)
    elif b_fermi is not None and abs(float(b_fermi)) > 1e-12:
        e_ref = float(b_fermi)
    else:
        e_ref = None
    b_shift = e_ref if e_ref is not None else 0.0
    e_dos = energies - e_ref if e_ref is not None else energies
    referenced = e_ref is not None

    fig = make_subplots(
        rows=1, cols=2, shared_yaxes=True,
        column_widths=[0.72, 0.28], horizontal_spacing=0.02,
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

    if referenced:
        fig.add_hline(
            y=0.0, line={"color": "#CC3333", "width": 1, "dash": "dash"},
            annotation_text="E<sub>F</sub>", annotation_position="top left",
        )

    tick_vals, tick_text, boundaries = _band_axis_ticks(bd.kpath, n_kpts)
    for start in boundaries:
        fig.add_vline(x=start, line={"color": "#999999", "width": 0.5, "dash": "dot"}, row=1, col=1)

    y_title = "E − E_F (eV)" if referenced else "Energy (eV)"
    fig.update_yaxes(title_text=y_title, row=1, col=1)
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
