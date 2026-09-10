"""Bands renderer — 2D band structure plot with Fermi line.

Segments from kpath.json, eigenvalues from the binary .dat.
Interactive hover for band index and energy at a given k-point.

Provides both matplotlib PNG (render_to_bytes) and Plotly HTML
(render_to_html) outputs. The Plotly version has hover tooltips
showing band index, energy, and k-point index.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import plotly.graph_objects as go

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import BandsData, QVFReader, Section

matplotlib.use("Agg")


def _label_text(label: object) -> str:
    if isinstance(label, list | tuple) and len(label) >= 2:
        return str(label[1])
    return str(label)


def _band_axis_ticks(kpath: dict, n_kpts: int) -> tuple[list[int], list[str], list[int]]:
    """Return x tick positions, labels, and segment-boundary positions.

    QVF writers may either provide explicit ``start`` / ``end`` indices
    or the v1 writer's ``n_points`` per labeled segment. Both forms
    describe the same path; this helper normalizes them for plotting.
    """
    if n_kpts <= 0:
        return [], [], []

    segments = kpath.get("segments", []) or []
    tick_positions: list[int] = []
    tick_labels: list[str] = []
    boundaries: list[int] = []
    cursor = 0

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        start = int(seg.get("start", cursor))
        if "end" in seg:
            end = int(seg["end"])
        elif "n_points" in seg:
            end = start + max(int(seg["n_points"]) - 1, 0)
        else:
            end = n_kpts - 1
        start = max(0, min(start, n_kpts - 1))
        end = max(0, min(end, n_kpts - 1))

        tick_positions.append(start)
        tick_labels.append(str(seg.get("label_start", "")))
        if end != start:
            tick_positions.append(end)
            tick_labels.append(str(seg.get("label_end", "")))
        if start > 0:
            boundaries.append(start)
        cursor = end

    if not tick_positions:
        labels = kpath.get("labels", [])
        if labels:
            tick_positions = list(range(min(len(labels), n_kpts)))
            tick_labels = [_label_text(label) for label in labels[:n_kpts]]

    deduped_positions: list[int] = []
    deduped_labels: list[str] = []
    for pos, label in zip(tick_positions, tick_labels, strict=True):
        if deduped_positions and deduped_positions[-1] == pos:
            if label and label not in deduped_labels[-1].split("|"):
                deduped_labels[-1] = (
                    f"{deduped_labels[-1]}|{label}" if deduped_labels[-1] else label
                )
            continue
        deduped_positions.append(pos)
        deduped_labels.append(label)

    return deduped_positions, deduped_labels, boundaries


class BandsRenderer(BaseRenderer):
    """2D band structure plot."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: BandsData | None = None

    def load(self) -> BandsData:
        if self._data is None:
            self._data = self.reader.read_bands(self.section_id)
        return self._data

    def _load_projections(self) -> tuple[np.ndarray | None, list[str]]:
        """Load optional fat-band projections + channel labels.

        Returns ``(projections, channels)`` where ``projections`` has
        shape ``[n_kpoints, n_bands, n_channels]`` and ``channels`` is
        the list of channel labels.  Returns ``(None, [])`` when the
        section has no projections member.
        """
        section = self.reader.get_section(self.section_id)
        proj_member = section.members.get("projections")
        if proj_member is None:
            return None, []
        try:
            proj = self.reader._read_binary_member(self.section_id, "projections")
        except Exception:
            return None, []
        channels = list(
            (self.reader._read_json_member(self.section_id, "kpath") or {}).get("channels", [])
        )
        return proj, channels

    def render_to_bytes(self) -> bytes:
        """Render the band structure to a PNG byte string (matplotlib)."""
        data = self.load()
        kpath = data.kpath
        eigenvalues = data.eigenvalues
        fermi = data.fermi
        if fermi == 0.0:
            # The writer's no-Fermi-level sentinel (see dos.py
            # _bands_dos_figure) — don't draw a spurious E_F line or
            # relabel the axis for files that carry no Fermi energy.
            fermi = None

        n_spin, n_kpts, n_bands = eigenvalues.shape

        fig, ax = plt.subplots(figsize=(8, 5))
        x = np.arange(n_kpts)
        shift = fermi if fermi is not None else 0.0

        projections, channels = self._load_projections()
        has_fat = projections is not None and len(channels) > 0

        if has_fat:
            n_channels = len(channels)
            if projections.ndim == 4 and projections.shape[0] == n_spin:
                proj_spin = projections
            else:
                proj_spin = np.expand_dims(projections, 0)
            channel_palette = [
                "#3366CC",
                "#CC3333",
                "#66AA55",
                "#CC8833",
                "#AA66CC",
                "#33AAAA",
                "#CCCC33",
                "#CC6699",
                "#33CC66",
                "#9966CC",
            ]
            for spin in range(n_spin):
                eig = eigenvalues[spin]
                sp = min(spin, proj_spin.shape[0] - 1)
                spin_proj = proj_spin[sp]
                for b in range(n_bands):
                    band_e = eig[:, b]
                    band_p = spin_proj[:, b, :]
                    dom = np.argmax(band_p, axis=1)
                    seg_start = 0
                    for k in range(1, n_kpts + 1):
                        if k == n_kpts or dom[k] != dom[seg_start]:
                            ch = int(dom[seg_start])
                            colour = channel_palette[ch % len(channel_palette)]
                            ax.plot(
                                x[seg_start:k],
                                band_e[seg_start:k] - shift,
                                color=colour,
                                linewidth=1.0,
                                alpha=0.8,
                            )
                            seg_start = k
            # Channel legend.
            legend_patches = []
            legend_labels = []
            for ci in range(min(n_channels, len(channel_palette))):
                legend_patches.append(plt.Line2D([0], [0], color=channel_palette[ci], linewidth=2))
                legend_labels.append(channels[ci])
            ax.legend(
                legend_patches,
                legend_labels,
                fontsize=7,
                loc="upper right",
                ncol=max(1, n_channels // 8),
                framealpha=0.5,
            )
        else:
            colors = ["#3366CC", "#CC8833", "#66AA55", "#AA66CC"]
            for spin in range(n_spin):
                for b in range(n_bands):
                    ax.plot(
                        x,
                        eigenvalues[spin, :, b] - shift,
                        color=colors[spin % len(colors)],
                        linewidth=1.0,
                        alpha=0.8,
                    )

        if fermi is not None:
            ax.axhline(y=0.0, color="#CC3333", linestyle="--", linewidth=1.0, alpha=0.7)

        tick_positions, tick_labels, boundaries = _band_axis_ticks(kpath, n_kpts)
        for start in boundaries:
            ax.axvline(x=start, color="#999999", linestyle=":", linewidth=0.5)
        if tick_positions:
            ax.set_xticks(tick_positions)
            ax.set_xticklabels(tick_labels, fontsize=10)

        # Eigenvalues are ALWAYS stored in eV (writer multiplies by
        # _HARTREE_TO_EV), so never label them "a.u." — the old `if fermi`
        # truthiness test mislabeled a fermi==0.0 file as atomic units while
        # simultaneously drawing the E_F=0 line (audit finding A4-02).
        ax.set_ylabel("E − E_F (eV)" if fermi is not None else "Energy (eV)")
        ax.set_xlabel("k-point")
        ax.set_title(data.kpath.get("title", "Band Structure"))
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    def render_to_html(self, include_plotlyjs: str | bool = "cdn") -> str:
        """Render the band structure as an interactive Plotly HTML div.

        Features:
        - Hover tooltips: band index, energy, k-point index
        - Fermi level reference line
        - Segment boundary markers and labels
        - Pan and zoom
        """
        data = self.load()
        kpath = data.kpath
        eigenvalues = data.eigenvalues
        fermi = data.fermi
        if fermi == 0.0:
            # The writer's no-Fermi-level sentinel (see dos.py
            # _bands_dos_figure) — don't draw a spurious E_F line or
            # relabel the axis for files that carry no Fermi energy.
            fermi = None

        n_spin, n_kpts, n_bands = eigenvalues.shape
        shift = fermi if fermi is not None else 0.0

        fig = go.Figure()

        # ── Fat bands (projection-weighted) or plain bands ──────────
        projections, channels = self._load_projections()
        has_fat = projections is not None and len(channels) > 0

        if has_fat:
            n_channels = len(channels)
            # Handle spin dimension if present.
            if projections.ndim == 4 and projections.shape[0] == n_spin:
                proj_spin = projections  # [n_spin, n_kpts, n_bands, n_channels]
            else:
                proj_spin = np.expand_dims(projections, 0)  # [1, n_kpts, n_bands, n_channels]
            # Colour palette for up to 20 channels (distinct hues).
            channel_palette = [
                "#3366CC",
                "#CC3333",
                "#66AA55",
                "#CC8833",
                "#AA66CC",
                "#33AAAA",
                "#CCCC33",
                "#CC6699",
                "#33CC66",
                "#9966CC",
                "#CC6633",
                "#3399CC",
                "#99CC33",
                "#CC3399",
                "#66CCAA",
                "#CCAA33",
                "#336699",
                "#993366",
                "#669933",
                "#996633",
            ]
            for spin in range(n_spin):
                eig = eigenvalues[spin]  # [n_kpts, n_bands]
                sp = min(spin, proj_spin.shape[0] - 1)
                spin_proj = proj_spin[sp]  # [n_kpts, n_bands, n_channels]
                for b in range(n_bands):
                    band_e = eig[:, b]  # [n_kpts]
                    band_p = spin_proj[:, b, :]  # [n_kpts, n_channels]
                    # Dominant channel index per k-point.
                    dom = np.argmax(band_p, axis=1)  # [n_kpts]
                    # Split into contiguous same-dominant-channel segments.
                    seg_start = 0
                    for k in range(1, n_kpts + 1):
                        if k == n_kpts or dom[k] != dom[seg_start]:
                            ch = int(dom[seg_start])
                            colour = channel_palette[ch % len(channel_palette)]
                            ch_label = channels[ch] if ch < n_channels else f"ch{ch}"
                            name = f"{ch_label} (B{b + 1})"
                            x_seg = list(range(seg_start, k))
                            y_seg = band_e[seg_start:k] - shift
                            fig.add_trace(
                                go.Scatter(
                                    x=x_seg,
                                    y=y_seg,
                                    mode="lines",
                                    line={"color": colour, "width": 1.5},
                                    name=name,
                                    hovertemplate=(
                                        f"{ch_label}<br>B{b + 1}<br>E: %{{y:.4f}}<extra></extra>"
                                    ),
                                    showlegend=False,
                                )
                            )
                            seg_start = k
            # Channel legend — one marker trace per channel.
            for ci in range(min(n_channels, len(channel_palette))):
                fig.add_trace(
                    go.Scatter(
                        x=[None],
                        y=[None],
                        mode="markers",
                        marker=dict(size=10, color=channel_palette[ci]),
                        name=channels[ci],
                        showlegend=True,
                    )
                )
        else:
            # Plain monochromatic bands.
            colors = ["#3366CC", "#CC8833", "#66AA55", "#AA66CC"]
            for spin in range(n_spin):
                spin_label = f"spin {spin + 1}"
                for b in range(n_bands):
                    name = f"{spin_label} Band {b + 1}" if n_spin > 1 else f"Band {b + 1}"
                    fig.add_trace(
                        go.Scatter(
                            x=list(range(n_kpts)),
                            y=eigenvalues[spin, :, b] - shift,
                            mode="lines",
                            line={"color": colors[spin % len(colors)], "width": 1},
                            name=name,
                            hovertemplate=(
                                "%{fullData.name}<br>k-pt: %{x}<br>E: %{y:.4f}<extra></extra>"
                            ),
                            showlegend=False,
                        )
                    )

        # Fermi line
        if fermi is not None:
            fig.add_hline(
                y=0.0,
                line={"color": "#CC3333", "width": 1, "dash": "dash"},
                annotation_text="E<sub>F</sub>",
                annotation_position="top right",
            )

        # Segment boundaries and labels
        tick_vals, tick_text, boundaries = _band_axis_ticks(kpath, n_kpts)
        for start in boundaries:
            fig.add_vline(
                x=start,
                line={"color": "#999999", "width": 0.5, "dash": "dot"},
            )

        y_label = "E − E_F (eV)" if fermi is not None else "Energy (eV)"
        fig.update_layout(
            title=data.kpath.get("title", "Band Structure"),
            xaxis={"title": "k-point", "tickvals": tick_vals, "ticktext": tick_text}
            if tick_vals
            else {},
            yaxis={"title": y_label},
            template="plotly_dark",
            hovermode="closest",
            height=450,
            margin={"l": 60, "r": 20, "t": 50, "b": 50},
            showlegend=has_fat,
            legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5)
            if has_fat
            else {},
        )

        return fig.to_html(full_html=False, include_plotlyjs=include_plotlyjs)
