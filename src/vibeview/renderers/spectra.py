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

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import numpy as np
import plotly.graph_objects as go

from vibeview.renderers import BaseRenderer
from vibeview.renderers._mpl import agg_pyplot, figure_to_png
from vibeview.renderers._plotly import chart_layout, figure_to_html
from vibeview.renderers.palette import ACCENT_BLUE, ACCENT_RED, COMPARE
from vibeview.units import EV_NM as _EV_NM
from vibeview.units import EV_TO_CM1 as _CM1_PER_EV

if TYPE_CHECKING:
    from collections.abc import Iterable

    from vibeview.qvf import QVFReader, Section, SpectraData


@dataclass(frozen=True)
class SpectrumStyle:
    """Per-kind axis labels + broadening defaults."""
    title: str
    x_label: str
    y_label: str
    x_unit_short: str  # used in hover template (e.g. "cm⁻¹", "eV")
    x_buffer: float    # padding around the data range, in X units
    gamma: float       # Lorentzian half-width at half max, in X units


# Registry — keep keys in sync with the kinds dispatched in
# vibeview/renderers/__init__.py::_KIND_RENDERER.
_STYLES: dict[str, SpectrumStyle] = {
    "spectra.ir": SpectrumStyle(
        title="IR Spectrum",
        x_label="Frequency (cm⁻¹)",
        y_label="Intensity (km/mol)",
        x_unit_short="cm⁻¹",
        x_buffer=100.0,
        gamma=5.0,
    ),
    "spectra.raman": SpectrumStyle(
        title="Raman Spectrum",
        x_label="Raman shift (cm⁻¹)",
        y_label="Activity (Å⁴/amu)",
        x_unit_short="cm⁻¹",
        x_buffer=100.0,
        gamma=5.0,
    ),
    "spectra.uvvis": SpectrumStyle(
        title="UV-Vis Spectrum",
        x_label="Energy (eV)",
        y_label="Oscillator strength",
        x_unit_short="eV",
        x_buffer=0.5,
        gamma=0.1,
    ),
    "spectra.ecd": SpectrumStyle(
        title="ECD Spectrum",
        x_label="Energy (eV)",
        y_label="Δε (L·mol⁻¹·cm⁻¹)",
        x_unit_short="eV",
        x_buffer=0.5,
        gamma=0.1,
    ),
    "spectra.vcd": SpectrumStyle(
        title="VCD Spectrum",
        x_label="Frequency (cm⁻¹)",
        y_label="Δε (L·mol⁻¹·cm⁻¹)",
        x_unit_short="cm⁻¹",
        x_buffer=100.0,
        gamma=5.0,
    ),
}


def spectrum_style(section: Section, freqs: np.ndarray) -> SpectrumStyle:
    """Resolve the rendering style for a section. Falls back to a
    generic style for ``spectra.generic`` (or any unknown spectra.*
    kind) that reads the section label and adapts the broadening
    width to the data range."""
    style = _STYLES.get(section.kind)
    # spectra.generic + unknown — adapt buffer/gamma to the data range
    # so the broadened envelope is visible regardless of X scale.
    span = float(np.max(freqs) - np.min(freqs)) if len(freqs) > 0 else 1.0
    buf = max(span * 0.05, 0.01)
    gamma = max(span * 0.005, 0.001)
    label = getattr(section, "label", None) or "Spectrum"
    if style is None:
        style = SpectrumStyle(
            title=label, x_label="X", y_label="Intensity", x_unit_short="",
            x_buffer=buf, gamma=gamma,
        )
    # Imported spectra can store a different physical quantity from the
    # kind's conventional default, e.g. ORCA ECD rotatory strengths, not Δε.
    # Keep the numerical axis and unit conversions consistent in every UI.
    hints = getattr(section, "spectrum_style", None)
    if isinstance(hints, dict):
        def text_hint(key: str, default: str) -> str:
            value = hints.get(key)
            return value if isinstance(value, str) and value else default

        style = replace(
            style, title=label,
            x_label=text_hint("x_label", style.x_label),
            y_label=text_hint("y_label", style.y_label),
            x_unit_short=text_hint("x_unit_short", style.x_unit_short),
        )
    return style


def spectrum_grid(freqs: np.ndarray, style: SpectrumStyle) -> np.ndarray:
    """Sample an envelope in native units with the shared display padding."""
    if not len(freqs):
        return np.empty(0, dtype=float)
    lower = float(np.min(freqs)) - style.x_buffer
    if style.x_unit_short == "cm⁻¹":
        lower = max(0.0, lower)
    upper = float(np.max(freqs)) + style.x_buffer
    return np.linspace(lower, upper, 2000)


# X-axis unit conversion (design refresh 2026, spectra controls). The
# native X unit of a spectrum is cm⁻¹ (vibrational) or eV (electronic);
# eV is the common base. 1 eV = 8065.543937 cm⁻¹ (CODATA 2018); the
# energy/wavelength product E[eV]·λ[nm] = 1239.841984 (hc in eV·nm).
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


def broadened_envelope(
    freqs: np.ndarray, intens: np.ndarray, gamma: float, x_grid: np.ndarray
) -> np.ndarray:
    """Sum of per-mode Lorentzians at the given grid points.

    Signed strengths are preserved, including arbitrarily small nonzero
    values: ORCA MCD transitions routinely lie below 1e-10. A fixed absolute
    threshold would erase physically meaningful signals in their native units.
    """
    env = np.zeros_like(x_grid)
    for f, intensity in zip(freqs, intens, strict=True):
        if intensity != 0:
            env += intensity * (gamma**2) / ((x_grid - f) ** 2 + gamma**2)
    return env


# Preserve existing imports while real callers use the shared helpers.
_SpectrumStyle = SpectrumStyle
_style_for = spectrum_style
_broadened_envelope = broadened_envelope


def stick_segments(
    positions: np.ndarray, intensities: np.ndarray
) -> tuple[list[float | None], list[float | None]]:
    """One polyline of vertical sticks, ``None``-separated, for a single Plotly trace.

    A trace per stick made a 3000-mode spectrum a 3000-trace figure; Plotly
    breaks a line at a ``None`` vertex, so every stick fits in one. Sticks
    with a zero or non-finite value are left out, and a negative
    intensity (ECD, VCD) keeps its sign.
    """
    xs: list[float | None] = []
    ys: list[float | None] = []
    for x, y in zip(positions, intensities, strict=True):
        if y == 0 or not np.isfinite(x) or not np.isfinite(y):
            continue
        xs.extend((float(x), float(x), None))
        ys.extend((0.0, float(y), None))
    return xs, ys


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
        style = spectrum_style(self.section, freqs)

        fig, ax = agg_pyplot().subplots(figsize=(8, 4))

        if len(freqs) > 0:
            f_grid = spectrum_grid(freqs, style)
            broadened = broadened_envelope(freqs, intens, style.gamma, f_grid)

            ax.plot(f_grid, broadened, color=ACCENT_RED, linewidth=1.5)
            ax.vlines(freqs, 0, intens, colors=ACCENT_BLUE, linewidths=1.0, alpha=0.6)
            ax.set_xlim(float(f_grid[0]), float(f_grid[-1]))
        else:
            ax.set_xlim(0, 1)

        ax.set_xlabel(style.x_label)
        ax.set_ylabel(style.y_label)
        ax.set_title(style.title)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        return figure_to_png(fig)

    def render_to_html(
        self,
        gamma_scale: float = 1.0,
        normalize: bool = False,
        x_unit: str = "native",
        display: str = "both",
        include_plotlyjs: str | bool = "cdn",
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
        style = spectrum_style(self.section, freqs)
        gamma = style.gamma * max(0.05, float(gamma_scale))

        fig = go.Figure()

        if len(freqs) > 0:
            f_grid = spectrum_grid(freqs, style)
            broadened = broadened_envelope(freqs, intens, gamma, f_grid)
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
                        line={"color": ACCENT_RED, "width": 1.5},
                        # Named for what it is, so the legend says the curve is
                        # a broadening convenience and not the data.
                        name=f"Lorentzian envelope, γ = {gamma:g}{unit}",
                        hovertemplate="%{x:.3f}" + unit + "<br>I: %{y:.4g}<extra></extra>",
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
                        + "<br>I: %{y:.4g}<extra></extra>"
                    }
                )
                x_sticks, _, _ = _convert_x(
                    np.asarray(freqs, dtype=float), style.x_unit_short, x_unit
                )
                xs, ys = stick_segments(x_sticks, np.asarray(intens, dtype=float) * stick_scale)
                text = None
                if data.labels is not None:
                    text = [
                        label for x, y, label in zip(x_sticks, intens, data.labels, strict=True)
                        if y != 0 and np.isfinite(x)
                        for _ in range(3)
                    ]
                    stick_hover = {
                        "hovertemplate": "%{text}<br>%{x:.3f}" + unit
                        + "<br>I: %{y:.4g}<extra></extra>"
                    }
                if xs:
                    fig.add_trace(
                        go.Scatter(
                            x=xs,
                            y=ys,
                            mode="lines",
                            line={"color": ACCENT_BLUE, "width": stick_width},
                            name="Sticks",
                            showlegend=False,
                            text=text,
                            **stick_hover,
                        )
                    )
        else:
            x_axis_title = style.x_label

        chart_layout(fig, title=style.title, x_title=x_axis_title, y_title=style.y_label)
        return figure_to_html(fig, include_plotlyjs=include_plotlyjs, fill_height=True)

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
        style = spectrum_style(self.section, freqs)
        gamma = style.gamma * max(0.05, float(gamma_scale))

        lines: list[str] = []
        if len(freqs) > 0:
            f_grid = spectrum_grid(freqs, style)
            env = broadened_envelope(freqs, intens, gamma, f_grid)
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
def render_comparison_html(
    entries: Iterable[tuple[str, Section, QVFReader]],
    gamma_scale: float = 1.0,
    normalize: bool = False,
    x_unit: str = "native",
    display: str = "envelope",
    include_plotlyjs: str | bool = "cdn",
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
        current_style = spectrum_style(section, freqs)
        if style is None:
            style = current_style
        elif (style.x_unit_short, style.y_label) != (
            current_style.x_unit_short, current_style.y_label
        ):
            return '<div>Cannot overlay spectra with different units or intensity quantities.</div>'
        if len(freqs):
            series.append((label, freqs, intens))
    if not series or style is None:
        return '<div style="padding: 16px;">No spectra to compare.</div>'

    bounds = np.array([
        min(float(np.min(f)) for _, f, _ in series),
        max(float(np.max(f)) for _, f, _ in series),
    ])
    f_grid = spectrum_grid(bounds, style)

    fig = go.Figure()
    x_grid, unit_short, axis_label = _convert_x(f_grid, style.x_unit_short, x_unit)
    x_axis_title = axis_label or style.x_label
    unit = f" {unit_short}" if unit_short else ""
    gamma = style.gamma * max(0.05, float(gamma_scale))
    for i, (label, freqs, intens) in enumerate(series):
        color = COMPARE[i % len(COMPARE)]
        if display in ("both", "envelope"):
            broadened = broadened_envelope(freqs, intens, gamma, f_grid)
            if normalize and np.abs(broadened).max() > 0:
                broadened = broadened / np.abs(broadened).max()
            fig.add_trace(
                go.Scatter(
                    x=x_grid,
                    y=broadened,
                    mode="lines",
                    line={"color": color, "width": 1.5},
                    name=label,
                    hovertemplate="%{x:.3f}" + unit + "<br>I: %{y:.4g}<extra>" + label + "</extra>",
                )
            )
        if display in ("both", "sticks"):
            stick_scale = 1.0
            if normalize:
                m = float(np.abs(intens).max())
                stick_scale = 1.0 / m if m > 0 else 1.0
            x_sticks, _, _ = _convert_x(freqs, style.x_unit_short, x_unit)
            xs, ys = stick_segments(x_sticks, np.asarray(intens, dtype=float) * stick_scale)
            if xs:
                fig.add_trace(
                    go.Scatter(
                        x=xs,
                        y=ys,
                        mode="lines",
                        line={"color": color, "width": 2 if display == "sticks" else 1},
                        name=label,
                        # sticks-only: the sticks are the legend entry per file
                        showlegend=display == "sticks",
                        legendgroup=label,
                        hovertemplate="%{x:.3f}" + unit
                        + "<br>I: %{y:.4g}<extra>" + label + "</extra>",
                    )
                )
    chart_layout(
        fig,
        title=f"{style.title} — {len(series)} files",
        x_title=x_axis_title,
        y_title=style.y_label,
        legend="bottom",
    )
    return figure_to_html(fig, include_plotlyjs=include_plotlyjs, fill_height=True)
