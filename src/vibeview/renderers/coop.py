"""COOP/COHP renderer — bonding analysis charts."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

import numpy as np
import plotly.graph_objects as go

from vibeview.renderers import BaseRenderer
from vibeview.renderers._plotly import chart_layout, fermi_line, figure_to_html, zero_line
from vibeview.renderers.palette import series_color

if TYPE_CHECKING:
    from vibeview.qvf import DOSCOOPData, QVFReader, Section


class COOPRenderer(BaseRenderer):
    """COOP/COHP bonding analysis chart (dos.coop or dos.cohp).

    Renders energy-resolved overlap/Hamiltonian population between
    atom pairs as a Plotly chart.  Positive = bonding, negative =
    antibonding.  Integrated values (bond order) shown in legend.
    """

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: DOSCOOPData | None = None

    def load(self) -> DOSCOOPData:
        if self._data is None:
            self._data = self.reader.read_dos_coop(self.section_id)
        return self._data

    @property
    def kind_label(self) -> str:
        return "COOP" if self.section.kind == "dos.coop" else "COHP"

    def render_to_html(self, include_plotlyjs: str | bool = "cdn") -> str:
        data = self.load()
        energies = np.asarray(data.energies, dtype=float)
        projections = np.asarray(data.projections, dtype=float)
        integrated = np.asarray(data.integrated, dtype=float)
        meta = data.meta or {}
        raw_labels = meta.get("pair_labels", [])
        pair_labels = [str(p) for p in raw_labels] if isinstance(raw_labels, list) else []
        raw_fermi = meta.get("fermi_energy_ev")
        fermi = float(raw_fermi) if isinstance(raw_fermi, int | float) else None
        label = self.kind_label

        n_pairs = projections.shape[0]
        fig = go.Figure()

        for i in range(n_pairs):
            p_label = pair_labels[i] if i < len(pair_labels) else f"pair {i + 1}"
            i_val = float(integrated[i]) if i < len(integrated) else 0.0
            # Plotly renders legend and hover strings as HTML and expands
            # ``%{...}`` placeholders in a template, so a producer label goes
            # in as escaped per-point text, never into the template itself.
            safe_label = html.escape(p_label)
            fig.add_trace(
                go.Scatter(
                    x=energies,
                    y=projections[i],
                    mode="lines",
                    line={"color": series_color(i), "width": 1.5},
                    name=f"{safe_label} (I{label}={i_val:+.3f})",
                    text=[safe_label] * len(energies),
                    hovertemplate=(
                        f"%{{text}}<br>E: %{{x:.3f}} eV<br>{label}: %{{y:.4f}}<extra></extra>"
                    ),
                )
            )

        # Zero line (bonding/antibonding boundary).
        zero_line(fig, width=0.5)

        # Fermi line if present.
        if fermi is not None:
            fermi_line(fig, axis="x", at=fermi, position="top")

        chart_layout(
            fig,
            title=f"{label} Analysis",
            # QVF fixes dos.* energies in eV (E_F at 0); the axis unit does
            # not depend on whether the producer also wrote a Fermi tag.
            x_title="Energy (eV)",
            y_title=f"{label} (bonding → antibonding)",
            legend="bottom",
        )
        return figure_to_html(fig, include_plotlyjs=include_plotlyjs)
