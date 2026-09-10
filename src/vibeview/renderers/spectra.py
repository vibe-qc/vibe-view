"""Spectra renderer — 1D spectrum plot (IR / Raman / UV-Vis / ECD / VCD / generic).

Frequency vs. intensity. Provides both matplotlib PNG and interactive
Plotly HTML. The Plotly version has hover tooltips with frequency and
intensity values.

The axis labels, title, broadening width, and X-window buffer are
per-kind so that ``spectra.uvvis`` (eV X-axis) doesn't end up
mislabelled as cm⁻¹ when it shares the renderer with ``spectra.ir``.
``spectra.generic`` falls back to the section's ``label`` (if any)
plus a generic ``Intensity`` Y-axis; ``spectra.nmr`` is shape-different
(chemical shifts) and is intentionally not dispatched to this
renderer.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import plotly.graph_objects as go

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, Section, SpectraData

matplotlib.use("Agg")


@dataclass(frozen=True)
class _SpectrumStyle:
    """Per-kind axis labels + broadening defaults."""
    title: str
    x_label: str
    y_label: str
    x_unit_short: str  # used in hover template (e.g. "cm⁻¹", "eV")
    x_buffer: float    # padding around the data range, in X units
    gamma: float       # Lorentzian half-width at half max, in X units


# Registry — keep keys in sync with the kinds dispatched in
# vibeview/renderers/__init__.py::_KIND_RENDERER.
_STYLES: dict[str, _SpectrumStyle] = {
    "spectra.ir": _SpectrumStyle(
        title="IR Spectrum",
        x_label="Frequency (cm⁻¹)",
        y_label="Intensity (km/mol)",
        x_unit_short="cm⁻¹",
        x_buffer=100.0,
        gamma=5.0,
    ),
    "spectra.raman": _SpectrumStyle(
        title="Raman Spectrum",
        x_label="Raman shift (cm⁻¹)",
        y_label="Activity (Å⁴/amu)",
        x_unit_short="cm⁻¹",
        x_buffer=100.0,
        gamma=5.0,
    ),
    "spectra.uvvis": _SpectrumStyle(
        title="UV-Vis Spectrum",
        x_label="Energy (eV)",
        y_label="Oscillator strength",
        x_unit_short="eV",
        x_buffer=0.5,
        gamma=0.1,
    ),
    "spectra.ecd": _SpectrumStyle(
        title="ECD Spectrum",
        x_label="Energy (eV)",
        y_label="Δε (L·mol⁻¹·cm⁻¹)",
        x_unit_short="eV",
        x_buffer=0.5,
        gamma=0.1,
    ),
    "spectra.vcd": _SpectrumStyle(
        title="VCD Spectrum",
        x_label="Frequency (cm⁻¹)",
        y_label="Δε (L·mol⁻¹·cm⁻¹)",
        x_unit_short="cm⁻¹",
        x_buffer=100.0,
        gamma=5.0,
    ),
}


def _style_for(section: Section, freqs: np.ndarray) -> _SpectrumStyle:
    """Resolve the rendering style for a section. Falls back to a
    generic style for ``spectra.generic`` (or any unknown spectra.*
    kind) that reads the section label and adapts the broadening
    width to the data range."""
    style = _STYLES.get(section.kind)
    if style is not None:
        return style
    # spectra.generic + unknown — adapt buffer/gamma to the data range
    # so the broadened envelope is visible regardless of X scale.
    if len(freqs) > 0:
        span = float(np.max(freqs) - np.min(freqs))
    else:
        span = 1.0
    buf = max(span * 0.05, 0.01)
    gamma = max(span * 0.005, 0.001)
    label = getattr(section, "label", None) or "Spectrum"
    return _SpectrumStyle(
        title=label,
        x_label="X",
        y_label="Intensity",
        x_unit_short="",
        x_buffer=buf,
        gamma=gamma,
    )


# X-axis unit conversion (design refresh 2026, spectra controls). The
# native X unit of a spectrum is cm⁻¹ (vibrational) or eV (electronic);
# eV is the common base. 1 eV = 8065.543937 cm⁻¹ (CODATA 2018); the
# energy/wavelength product E[eV]·λ[nm] = 1239.841984 (hc in eV·nm).
_CM1_PER_EV = 8065.543937
_EV_NM = 1239.841984
_UNIT_LABELS = {
    "cm⁻¹": "Wavenumber (cm⁻¹)",
    "eV": "Energy (eV)",
    "nm": "Wavelength (nm)",
}


def _native_to_ev(x: np.ndarray, native_unit: str) -> np.ndarray | None:
    """Convert a native-unit X array to eV, or None if not convertible."""
    if native_unit == "eV":
        return x
    if native_unit == "cm⁻¹":
        return x / _CM1_PER_EV
    return None  # generic / unknown unit — no physical conversion


def _convert_x(
    x_native: np.ndarray, native_unit: str, target: str
) -> tuple[np.ndarray, str, str | None]:
    """Map a native-unit X array to a display unit.

    Returns ``(values, unit_short, axis_label)``; ``axis_label`` is None
    when the caller should keep the section's own label (native / no
    conversion). Non-convertible (generic) spectra pass through unchanged.
    """
    if target in ("native", "", None) or not native_unit:
        return x_native, native_unit, None
    ev = _native_to_ev(x_native, native_unit)
    if ev is None:
        return x_native, native_unit, None
    if target == "eV":
        return ev, "eV", _UNIT_LABELS["eV"]
    if target == "cm⁻¹":
        return ev * _CM1_PER_EV, "cm⁻¹", _UNIT_LABELS["cm⁻¹"]
    if target == "nm":
        # E·λ = hc; guard the E→0 grid endpoint (cm⁻¹ envelopes are
        # clamped to include 0) so it drops out instead of going to ∞.
        with np.errstate(divide="ignore", invalid="ignore"):
            lam = np.where(ev > 1e-9, _EV_NM / ev, np.nan)
        return lam, "nm", _UNIT_LABELS["nm"]
    return x_native, native_unit, None


def _broadened_envelope(
    freqs: np.ndarray, intens: np.ndarray, gamma: float, x_grid: np.ndarray
) -> np.ndarray:
    """Sum of per-mode Lorentzians at the given grid points.

    Gated on |intensity| (not intensity > 0): ECD/VCD intensities are
    *signed* rotatory strengths, so negative Cotton bands are physically
    essential. The old ``intensity > 1e-10`` gate silently dropped every
    negative band, rendering a positive-only caricature that loses the very
    sign pattern that distinguishes enantiomers (audit finding A4-03)."""
    env = np.zeros_like(x_grid)
    for f, intensity in zip(freqs, intens, strict=True):
        if abs(intensity) > 1e-10:
            env += intensity * (gamma**2) / ((x_grid - f) ** 2 + gamma**2)
    return env


class SpectraRenderer(BaseRenderer):
    """1D spectrum plot (IR / Raman / UV-Vis / ECD / VCD / generic)."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: SpectraData | None = None

    def load(self) -> SpectraData:
        if self._data is None:
            self._data = self.reader.read_spectra(self.section_id)
        return self._data

    def render_to_bytes(self) -> bytes:
        """Render the spectrum to a PNG byte string (matplotlib)."""
        data = self.load()
        freqs = data.frequencies
        intens = data.intensities
        style = _style_for(self.section, freqs)

        fig, ax = plt.subplots(figsize=(8, 4))

        if len(freqs) > 0:
            f_min = float(np.min(freqs)) - style.x_buffer
            # Frequencies are non-negative for IR/Raman/VCD; clamp at 0
            # so the broadened envelope doesn't extend below the
            # physical axis. UV-Vis/ECD can in principle have negative
            # excitation reports too, but the typical case is positive
            # — leave the data-driven minimum if it's already < 0.
            if style.x_unit_short == "cm⁻¹":
                f_min = max(0.0, f_min)
            f_max = float(np.max(freqs)) + style.x_buffer
            f_grid = np.linspace(f_min, f_max, 2000)
            broadened = _broadened_envelope(freqs, intens, style.gamma, f_grid)

            ax.plot(f_grid, broadened, color="#CC3333", linewidth=1.5)
            ax.vlines(freqs, 0, intens, colors="#3366CC", linewidths=1.0, alpha=0.6)
            ax.set_xlim(f_min, f_max)
        else:
            ax.set_xlim(0, 1)

        ax.set_xlabel(style.x_label)
        ax.set_ylabel(style.y_label)
        ax.set_title(style.title)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    def render_to_html(
        self,
        gamma_scale: float = 1.0,
        normalize: bool = False,
        x_unit: str = "native",
        display: str = "both",
    ) -> str:
        """Render the spectrum as an interactive Plotly HTML div.

        ``gamma_scale`` multiplies the per-kind default broadening width;
        ``normalize`` scales the envelope to a peak of 1; ``x_unit`` maps the
        X axis to ``"cm⁻¹"`` / ``"eV"`` / ``"nm"`` (``"native"`` keeps the
        section's own unit) (design refresh 2026, spectra controls).
        ``display`` selects what is drawn: ``"both"`` (envelope + stick
        spectrum, the historical rendering), ``"envelope"``, or
        ``"sticks"`` — publications want one or the other as often as the
        combination. An unknown value falls back to ``"both"``.
        """
        if display not in ("both", "envelope", "sticks"):
            display = "both"
        data = self.load()
        freqs = data.frequencies
        intens = data.intensities
        style = _style_for(self.section, freqs)
        gamma = style.gamma * max(0.05, float(gamma_scale))

        fig = go.Figure()

        if len(freqs) > 0:
            f_min = float(np.min(freqs)) - style.x_buffer
            if style.x_unit_short == "cm⁻¹":
                f_min = max(0.0, f_min)
            f_max = float(np.max(freqs)) + style.x_buffer
            f_grid = np.linspace(f_min, f_max, 2000)
            broadened = _broadened_envelope(freqs, intens, gamma, f_grid)
            if normalize and np.abs(broadened).max() > 0:
                broadened = broadened / np.abs(broadened).max()

            # Broaden in native units (correct physics), then map X for display.
            x_grid, unit_short, axis_label = _convert_x(f_grid, style.x_unit_short, x_unit)
            x_axis_title = axis_label or style.x_label
            unit = f" {unit_short}" if unit_short else ""
            if display in ("both", "envelope"):
                fig.add_trace(
                    go.Scatter(
                        x=x_grid,
                        y=broadened,
                        mode="lines",
                        line={"color": "#CC3333", "width": 1.5},
                        name="Spectrum",
                        hovertemplate="%{x:.3f}" + unit + "<br>I: %{y:.3f}<extra></extra>",
                    )
                )

            if display in ("both", "sticks"):
                stick_scale = 1.0
                if normalize:
                    m = float(np.abs(np.asarray(intens)).max())
                    stick_scale = 1.0 / m if m > 0 else 1.0
                # Sticks-only is the primary view, not an underlay: full
                # width and hover with values, since there is no envelope
                # to carry the hover.
                stick_width = 1 if display == "both" else 2
                stick_hover = (
                    {"hoverinfo": "skip"}
                    if display == "both"
                    else {
                        "hovertemplate": "%{x:.3f}" + unit
                        + "<br>I: %{y:.3f}<extra></extra>"
                    }
                )
                x_sticks, _, _ = _convert_x(
                    np.asarray(freqs, dtype=float), style.x_unit_short, x_unit
                )
                for xf, intensity in zip(x_sticks, intens, strict=True):
                    intensity = intensity * stick_scale
                    if abs(intensity) > 1e-10 and np.isfinite(xf):  # signed ECD/VCD — A4-03
                        fig.add_trace(
                            go.Scatter(
                                x=[xf, xf],
                                y=[0, intensity],
                                mode="lines",
                                line={"color": "#3366CC", "width": stick_width},
                                showlegend=False,
                                **stick_hover,
                            )
                        )
        else:
            x_axis_title = style.x_label

        fig.update_layout(
            title=style.title,
            xaxis={"title": x_axis_title},
            yaxis={"title": style.y_label},
            template="plotly_dark",
            hovermode="closest",
            height=350,
            margin={"l": 60, "r": 20, "t": 50, "b": 50},
            showlegend=False,
        )

        return fig.to_html(full_html=False, include_plotlyjs="cdn")

    def to_csv(
        self,
        gamma_scale: float = 1.0,
        normalize: bool = False,
        x_unit: str = "native",
    ) -> str:
        """Export the broadened envelope as CSV (grid + peak sticks).

        Two blocks: the sampled envelope, then the discrete peaks, both in
        the requested display unit — what a user drops into Origin / a paper.
        """
        data = self.load()
        freqs = np.asarray(data.frequencies, dtype=float)
        intens = np.asarray(data.intensities, dtype=float)
        style = _style_for(self.section, freqs)
        gamma = style.gamma * max(0.05, float(gamma_scale))

        lines: list[str] = []
        if len(freqs) > 0:
            f_min = float(np.min(freqs)) - style.x_buffer
            if style.x_unit_short == "cm⁻¹":
                f_min = max(0.0, f_min)
            f_max = float(np.max(freqs)) + style.x_buffer
            f_grid = np.linspace(f_min, f_max, 2000)
            env = _broadened_envelope(freqs, intens, gamma, f_grid)
            if normalize and np.abs(env).max() > 0:
                env = env / np.abs(env).max()
            x_grid, unit_short, _ = _convert_x(f_grid, style.x_unit_short, x_unit)
            x_sticks, _, _ = _convert_x(freqs, style.x_unit_short, x_unit)
            u = unit_short or "x"
            lines.append(f"# {style.title}")
            lines.append("# envelope")
            lines.append(f"{u},intensity")
            for xv, yv in zip(x_grid, env, strict=True):
                if np.isfinite(xv):
                    lines.append(f"{xv:.6g},{yv:.6g}")
            lines.append("# peaks")
            lines.append(f"{u},intensity")
            for xv, yv in zip(x_sticks, intens, strict=True):
                if np.isfinite(xv):
                    lines.append(f"{xv:.6g},{yv:.6g}")
        else:
            lines.append(f"# {style.title} (no data)")
        return "\n".join(lines) + "\n"


# Distinguishable per-file colors for the multi-file comparison overlay.
_COMPARE_COLORS = ("#CC3333", "#3366CC", "#33AA55", "#CC8800", "#8844CC", "#00AABB")


def render_comparison_html(
    entries,
    gamma_scale: float = 1.0,
    normalize: bool = False,
    x_unit: str = "native",
    display: str = "envelope",
) -> str:
    """Overlay same-kind spectra from several QVF files.

    ``entries`` is ``[(label, section, reader), ...]``; the first entry
    defines the axis style and broadening. ``display`` follows the single
    -view selector ("both" / "envelope" / "sticks"), with per-file colours
    on the sticks; the parameterless default stays the historical
    envelopes-only overlay — several files' sticks read as a picket fence
    unless the user explicitly asks for them (design refresh 2026, item 8).
    """
    if display not in ("both", "envelope", "sticks"):
        display = "envelope"
    series = []
    style = None
    for label, section, reader in entries:
        data = reader.read_spectra(section.id)
        freqs = np.asarray(data.frequencies, dtype=float)
        intens = np.asarray(data.intensities, dtype=float)
        if style is None:
            style = _style_for(section, freqs)
        if len(freqs):
            series.append((label, freqs, intens))
    if not series or style is None:
        return '<div style="padding: 16px;">No spectra to compare.</div>'

    f_min = min(float(np.min(f)) for _, f, _ in series) - style.x_buffer
    if style.x_unit_short == "cm⁻¹":
        f_min = max(0.0, f_min)
    f_max = max(float(np.max(f)) for _, f, _ in series) + style.x_buffer
    f_grid = np.linspace(f_min, f_max, 2000)

    fig = go.Figure()
    x_grid, unit_short, axis_label = _convert_x(f_grid, style.x_unit_short, x_unit)
    x_axis_title = axis_label or style.x_label
    unit = f" {unit_short}" if unit_short else ""
    gamma = style.gamma * max(0.05, float(gamma_scale))
    for i, (label, freqs, intens) in enumerate(series):
        color = _COMPARE_COLORS[i % len(_COMPARE_COLORS)]
        if display in ("both", "envelope"):
            broadened = _broadened_envelope(freqs, intens, gamma, f_grid)
            if normalize and np.abs(broadened).max() > 0:
                broadened = broadened / np.abs(broadened).max()
            fig.add_trace(
                go.Scatter(
                    x=x_grid,
                    y=broadened,
                    mode="lines",
                    line={"color": color, "width": 1.5},
                    name=label,
                    hovertemplate="%{x:.3f}" + unit + "<br>I: %{y:.3f}<extra>" + label + "</extra>",
                )
            )
        if display in ("both", "sticks"):
            stick_scale = 1.0
            if normalize:
                m = float(np.abs(intens).max())
                stick_scale = 1.0 / m if m > 0 else 1.0
            x_sticks, _, _ = _convert_x(freqs, style.x_unit_short, x_unit)
            first = display == "sticks"  # sticks-only: one legend entry per file
            for xf, intensity in zip(x_sticks, intens, strict=True):
                intensity = intensity * stick_scale
                if abs(intensity) > 1e-10 and np.isfinite(xf):  # signed ECD/VCD
                    fig.add_trace(
                        go.Scatter(
                            x=[xf, xf],
                            y=[0, intensity],
                            mode="lines",
                            line={"color": color, "width": 2 if display == "sticks" else 1},
                            name=label,
                            showlegend=first,
                            legendgroup=label,
                            hovertemplate="%{x:.3f}" + unit
                            + "<br>I: %{y:.3f}<extra>" + label + "</extra>",
                        )
                    )
                    first = False
    fig.update_layout(
        title=f"{style.title} — {len(series)} files",
        xaxis={"title": x_axis_title},
        yaxis={"title": style.y_label},
        template="plotly_dark",
        hovermode="closest",
        height=350,
        margin={"l": 60, "r": 20, "t": 50, "b": 80},
        legend={"orientation": "h", "y": -0.3},
        showlegend=True,
    )
    return fig.to_html(full_html=False, include_plotlyjs="cdn")
