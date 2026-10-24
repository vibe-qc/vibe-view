"""Rendering-free spectrum broadening and reproducible ASCII export.

The normalized Gaussian and Cauchy (Lorentzian) densities follow:
https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.norm.html
https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.cauchy.html
Their scale parameters are FWHM / sqrt(8 ln 2) and FWHM / 2.
No SciPy or rendering dependency is required.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import numpy as np

from vibeview.units import EV_NM as _EV_NM
from vibeview.units import EV_TO_CM1 as _CM1_PER_EV

if TYPE_CHECKING:
    from vibeview.qvf import Section

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



def broaden_lines(
    positions: np.ndarray, strengths: np.ndarray, grid: np.ndarray,
    *, fwhm: float, lineshape: str = "lorentzian",
) -> np.ndarray:
    """Area-conserving signed line sum; all positions/widths share one unit.

    Each kernel integrates to one on the infinite axis. A finite export window
    truncates tails; it is deliberately not renormalized to hide that loss.
    """
    positions, strengths, grid = (np.asarray(v, dtype=float) for v in
                                  (positions, strengths, grid))
    if positions.ndim != 1 or strengths.shape != positions.shape or grid.ndim != 1:
        raise ValueError("positions, strengths and grid must be one-dimensional")
    if not all(np.isfinite(v).all() for v in (positions, strengths, grid)):
        raise ValueError("spectrum inputs must be finite")
    if not np.isfinite(fwhm) or fwhm <= 0:
        raise ValueError("FWHM must be finite and positive")
    if lineshape not in ("gaussian", "lorentzian"):
        raise ValueError("line shape must be gaussian or lorentzian")
    result = np.zeros_like(grid)
    for position, strength in zip(positions, strengths, strict=True):
        delta = (grid - position) / fwhm
        if lineshape == "gaussian":
            kernel = np.sqrt(4 * np.log(2) / np.pi) / fwhm * np.exp(-4 * np.log(2) * delta**2)
        else:
            kernel = 2 / (np.pi * fwhm) / (1 + 4 * delta**2)
        result += strength * kernel
    return result


def native_fwhm(style: SpectrumStyle, fwhm: float | None, unit: str = "native") -> float:
    """Resolve a typed width in energy units, never in wavelength."""
    unit = "cm⁻¹" if unit == "cm-1" else unit
    if fwhm is None:
        return 2 * style.gamma
    width = float(fwhm)
    if not np.isfinite(width) or width <= 0:
        raise ValueError("FWHM must be finite and positive")
    if unit in ("native", style.x_unit_short):
        return width
    if unit == "eV" and style.x_unit_short == "cm⁻¹":
        return width * _CM1_PER_EV
    if unit == "cm⁻¹" and style.x_unit_short == "eV":
        return width / _CM1_PER_EV
    raise ValueError("FWHM unit must match the spectrum, or be eV/cm-1 for energy spectra")


def sampling_grid(
    freqs: np.ndarray, style: SpectrumStyle, grid_min: float | None = None,
    grid_max: float | None = None, grid_step: float | None = None,
) -> np.ndarray:
    """Uniform grid in the spectrum's native energy unit; at most 1M points."""
    default = spectrum_grid(freqs, style)
    if not len(default):
        return default
    lower = float(default[0]) if grid_min is None else float(grid_min)
    upper = float(default[-1]) if grid_max is None else float(grid_max)
    if not np.isfinite([lower, upper]).all() or upper <= lower:
        raise ValueError("grid maximum must exceed its finite minimum")
    if grid_step is None:
        return np.linspace(lower, upper, 2000)
    if not np.isfinite(grid_step) or grid_step <= 0:
        raise ValueError("grid step must be finite and positive")
    intervals = (upper - lower) / grid_step
    if not np.isfinite(intervals) or intervals > 999999:
        raise ValueError("spectrum grid exceeds 1000000 points")
    return lower + np.arange(int(np.floor(intervals + 1e-10)) + 1) * grid_step


def spectrum_ascii(
    section: Section, freqs: np.ndarray, intens: np.ndarray, *, source: str,
    lineshape: str = "lorentzian", fwhm: float | None = None, unit: str = "native",
    normalize: bool = False, x_unit: str = "native", grid_min: float | None = None,
    grid_max: float | None = None, grid_step: float | None = None,
) -> str:
    """One deterministic whitespace format shared by every front end."""
    freqs, intens = np.asarray(freqs, dtype=float), np.asarray(intens, dtype=float)
    style = spectrum_style(section, freqs)
    width = native_fwhm(style, fwhm, unit)
    grid = sampling_grid(freqs, style, grid_min, grid_max, grid_step)
    envelope = broaden_lines(freqs, intens, grid, fwhm=width, lineshape=lineshape)
    if normalize and envelope.size and np.max(np.abs(envelope)) > 0:
        envelope = envelope / np.max(np.abs(envelope))
    x, display_unit, _ = _convert_x(grid, style.x_unit_short, x_unit)
    sticks, _, _ = _convert_x(freqs, style.x_unit_short, x_unit)
    source = source.replace("\n", " ").replace("\r", " ")
    lines = [f"# source: {source}; section: {section.id}",
             f"# quantity: {style.y_label}; axis: {display_unit or 'native'}",
             f"# display broadening: {lineshape}; FWHM: {width:.12g} {style.x_unit_short}",
             f"# normalization: {'unit absolute peak' if normalize else 'area-preserving kernels'}",
             f"# grid native unit: {style.x_unit_short}; points: {len(grid)}"]
    if grid.size:
        step = grid[1] - grid[0] if len(grid) > 1 else 0.0
        lines.append(f"# grid: {grid[0]:.12g} {grid[-1]:.12g} step {step:.12g}")
    lines.append("# envelope: position intensity")
    lines.extend(f"{a:.12g} {b:.12g}" for a, b in zip(x, envelope, strict=True) if np.isfinite(a))
    lines.append("# sticks: position stored_strength (not normalized)")
    lines.extend(f"{a:.12g} {b:.12g}" for a, b in zip(sticks, intens, strict=True)
                 if np.isfinite(a))
    return "\n".join(lines) + "\n"
