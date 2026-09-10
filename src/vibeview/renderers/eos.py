"""Equation-of-state renderer: V-E points + the fitted EOS curve.

The producer ships the sampled (volume, energy) points plus the parameters of a
fitted equation of state (`model`, `V0`, `E0`, `B0`, `B0_prime`). This panel
plots the points, overlays the smooth fitted curve, and marks the equilibrium
volume V0 so the user sees both the data and the fit.

The curve is reconstructed by evaluating the published EOS energy form at the
producer's fitted parameters; each formula is cited inline at its evaluation
site below. vibe-qc's core does not fit the EOS (a study script / external tool
does), so there is no producer-side references block for it; this panel is where
the model is named to the user, and the inline comments are the code-level
record of what equation is implemented and how to verify it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import plotly.graph_objects as go

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import EquationOfStateData, QVFReader, Section

# Spec §4.14 fixes EOS units to eV / Angstrom^3 / GPa. The Birch-Murnaghan and
# Murnaghan energy forms multiply a pressure (B0) by a volume; that product is in
# GPa·Å³ and must be converted to eV. 1 eV = 160.2176634 GPa·Å³
# (1.602176634e-19 J / 1e-21 J).
_GPA_A3_PER_EV = 160.2176634


def _f(x: object) -> float | None:
    """Float-or-None: tolerate a missing / non-numeric fit parameter."""
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _birch_murnaghan_energy(
    V: np.ndarray, V0: float, E0: float, B0_gpa: float, B0p: float
) -> np.ndarray:
    # Third-order Birch-Murnaghan EOS, energy form. Birch, Phys. Rev. 71, 809
    # (1947); standard energy formulation:
    #   E(V) = E0 + (9 V0 B0 / 16) { (η²−1)³ B0′ + (η²−1)² (6 − 4η²) },
    #   η = (V0/V)^(1/3).
    # B0·V0 is in GPa·Å³; divide by _GPA_A3_PER_EV to land E in eV. The bracket
    # vanishes at V=V0, so E(V0)=E0 exactly (used as a test anchor).
    eta2 = (V0 / V) ** (2.0 / 3.0)
    t = eta2 - 1.0
    pref = (9.0 / 16.0) * V0 * B0_gpa / _GPA_A3_PER_EV
    return E0 + pref * (t**3 * B0p + t**2 * (6.0 - 4.0 * eta2))


def _murnaghan_energy(
    V: np.ndarray, V0: float, E0: float, B0_gpa: float, B0p: float
) -> np.ndarray:
    # Murnaghan EOS, energy form. Murnaghan, Proc. Natl. Acad. Sci. USA 30, 244
    # (1944):
    #   E(V) = E0 + (B0 V / B0′) [ (V0/V)^B0′ / (B0′−1) + 1 ] − B0 V0 / (B0′−1).
    # B0·V is GPa·Å³ → eV via _GPA_A3_PER_EV. E(V0)=E0 for B0′≠1.
    B = B0_gpa / _GPA_A3_PER_EV
    return (
        E0
        + B * V / B0p * ((V0 / V) ** B0p / (B0p - 1.0) + 1.0)
        - B * V0 / (B0p - 1.0)
    )


class EquationOfStateRenderer(BaseRenderer):
    """Volume-energy curve with the producer's fitted EOS overlaid."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: EquationOfStateData | None = None

    def load(self) -> EquationOfStateData:
        if self._data is None:
            self._data = self.reader.read_equation_of_state(self.section_id)
        return self._data

    def render_to_html(self, include_plotlyjs: str | bool = "cdn") -> str:
        """Interactive Plotly E(V) plot: computed points + fitted EOS curve."""
        data = self.load()
        V = np.asarray(data.volumes, dtype=float)
        E = np.asarray(data.energies, dtype=float)
        fit = data.fit or {}

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=V, y=E, mode="markers", name="Computed",
                marker={"color": "#3366CC", "size": 7},
                hovertemplate="V: %{x:.3f} Å³<br>E: %{y:.4f} eV<extra></extra>",
            )
        )

        V0, E0 = _f(fit.get("V0")), _f(fit.get("E0"))
        B0, B0p = _f(fit.get("B0")), _f(fit.get("B0_prime"))
        model = str(fit.get("model", "")).lower()

        cite = ""
        # Overlay the fitted curve only when the parameters are complete and
        # sane (B0′ == 1 makes Murnaghan singular).
        if None not in (V0, E0, B0, B0p) and B0p != 1.0 and V.size:
            vmin, vmax = float(V.min()), float(V.max())
            pad = 0.05 * ((vmax - vmin) or 1.0)
            grid = np.linspace(vmin - pad, vmax + pad, 200)
            if "murnaghan" in model and "birch" not in model:
                curve = _murnaghan_energy(grid, V0, E0, B0, B0p)
                label, cite = "Murnaghan", "Murnaghan (1944)"
            else:
                curve = _birch_murnaghan_energy(grid, V0, E0, B0, B0p)
                label, cite = "Birch-Murnaghan", "Birch (1947)"
            fig.add_trace(
                go.Scatter(
                    x=grid, y=curve, mode="lines", name=f"{label} fit",
                    line={"color": "#66AA55", "width": 2},
                    hovertemplate="V: %{x:.3f} Å³<br>E: %{y:.4f} eV<extra></extra>",
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=[V0], y=[E0], mode="markers", name="V₀",
                    marker={"color": "#CC3333", "size": 11, "symbol": "x"},
                    hovertemplate="V₀: %{x:.3f} Å³<br>E₀: %{y:.4f} eV<extra></extra>",
                )
            )

        fig.update_layout(
            title="Equation of State",
            xaxis={"title": "Volume (Å³)"},
            yaxis={"title": "Energy (eV)"},
            template="plotly_dark",
            hovermode="closest",
            height=450,
            margin={"l": 70, "r": 20, "t": 50, "b": 50},
        )

        # Surface the fitted parameters + the model citation to the user (the
        # viewer's user-facing citation surface for the EOS model).
        lines = []
        if V0 is not None:
            lines.append(f"V₀ = {V0:.2f} Å³")
        if B0 is not None:
            lines.append(f"B₀ = {B0:.1f} GPa")
        if B0p is not None:
            lines.append(f"B₀′ = {B0p:.2f}")
        if cite:
            lines.append(cite)
        if lines:
            fig.add_annotation(
                text="<br>".join(lines), xref="paper", yref="paper",
                x=0.98, y=0.98, xanchor="right", yanchor="top",
                showarrow=False, align="left",
                bgcolor="rgba(20,20,30,0.7)", bordercolor="#555", borderwidth=1,
                font={"size": 12, "color": "#ccc"},
            )
        return fig.to_html(full_html=False, include_plotlyjs=include_plotlyjs)
