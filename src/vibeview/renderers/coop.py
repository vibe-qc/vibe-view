"""COOP/COHP renderer — bonding analysis charts."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import plotly.graph_objects as go

from vibeview.renderers import BaseRenderer

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

    def render_to_html(self) -> str:
        data = self.load()
        energies = np.asarray(data.energies, dtype=float)
        projections = np.asarray(data.projections, dtype=float)
        integrated = np.asarray(data.integrated, dtype=float)
        meta = data.meta or {}
        pair_labels = meta.get("pair_labels", [])
        fermi = meta.get("fermi_energy_ev")
        label = self.kind_label

        n_pairs = projections.shape[0]
        fig = go.Figure()

        colours = [
            "#3366CC",
            "#CC3333",
            "#66AA55",
            "#CC8833",
            "#AA66CC",
            "#33AAAA",
            "#CCCC33",
            "#CC6699",
        ]

        for i in range(n_pairs):
            p_label = pair_labels[i] if i < len(pair_labels) else f"pair {i + 1}"
            i_val = float(integrated[i]) if i < len(integrated) else 0.0
            name = f"{p_label} (I{label}={i_val:+.3f})"
            fig.add_trace(
                go.Scatter(
                    x=energies,
                    y=projections[i],
                    mode="lines",
                    line={"color": colours[i % len(colours)], "width": 1.5},
                    name=name,
                    hovertemplate=f"{p_label}<br>E: %{{x:.3f}}<br>{label}: %{{y:.4f}}<extra></extra>",
                )
            )

        # Zero line (bonding/antibonding boundary).
        fig.add_hline(y=0.0, line={"color": "#888888", "width": 0.5, "dash": "dot"})

        # Fermi line if present.
        if fermi is not None:
            fig.add_vline(
                x=float(fermi),
                line={"color": "#CC3333", "width": 1, "dash": "dash"},
                annotation_text="E<sub>F</sub>",
                annotation_position="top",
            )

        y_title = f"{label} (bonding → antibonding)"
        fig.update_layout(
            title=f"{label} Analysis",
            xaxis_title="Energy (eV)" if fermi is not None else "Energy",
            yaxis_title=y_title,
            template="plotly_dark",
            hovermode="closest",
            height=400,
            margin={"l": 60, "r": 20, "t": 50, "b": 50},
            showlegend=True,
            legend=dict(orientation="h", yanchor="top", y=-0.2, xanchor="center", x=0.5),
        )

        return fig.to_html(full_html=False, include_plotlyjs="cdn")
