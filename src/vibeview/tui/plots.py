"""Braille XY plots for the chart-shaped QVF kinds.

Bands, DOS, spectra, SCF traces, equations of state and COOP/COHP all
reduce to the same primitive: a framed data area with autoscaled axes, tick
labels in the margins, and one or more series drawn into it. The data area
is a braille pixel buffer (two dots wide, four tall per cell); the margins
are plain text in the same :class:`~vibeview.tui.braille.CellGrid`.

Axis conventions follow the Plotly renderers so a terminal plot and the
interactive one carry the same message: bands are Fermi-referenced whenever
the file carries a Fermi level (0.0 is the writer's "none" sentinel), DOS
grids are stored relative to E_F already, and SCF convergence is plotted as
|ΔE| on a log axis.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from vibeview.renderers._coerce import as_float
from vibeview.renderers.palette import SERIES, rgb_palette
from vibeview.tui.braille import CellGrid, pixel_size
from vibeview.units import HARTREE_TO_KCAL_MOL, band_axis_unit

_GAP = float("nan")  # a missing iteration field is a gap in the plot, not a zero

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader

RGB = tuple[int, int, int]
ArrayLike = np.ndarray | Sequence[float]

_AXIS = (120, 126, 140)
_LABEL = (196, 202, 214)
_TITLE = (236, 240, 248)
_GRID = (56, 60, 72)
_FERMI = (240, 120, 120)

# Series palette — the Okabe-Ito qualitative set, which stays distinguishable
# under the common colour-vision deficiencies and on a dark terminal.
SERIES_COLORS = rgb_palette(SERIES)


@dataclass
class Series:
    x: np.ndarray
    y: np.ndarray
    color: tuple[int, int, int]
    label: str = ""
    style: str = "line"  # "line" | "points" | "sticks"


@dataclass
class Plot:
    """An autoscaling XY plot rendered into a character grid."""

    cols: int
    rows: int
    title: str = ""
    xlabel: str = ""
    ylabel: str = ""
    series: list[Series] = field(default_factory=list)
    hlines: list[tuple[float, tuple[int, int, int], str]] = field(default_factory=list)
    vlines: list[tuple[float, tuple[int, int, int], str]] = field(default_factory=list)
    xticks: list[tuple[float, str]] | None = None
    ylog: bool = False
    xlim: tuple[float, float] | None = None
    ylim: tuple[float, float] | None = None
    margin_left: int = 10
    background: tuple[int, int, int] = (0, 0, 0)

    def add(
        self,
        x: ArrayLike,
        y: ArrayLike,
        color: RGB | None = None,
        label: str = "",
        style: str = "line",
    ) -> None:
        idx = len(self.series)
        self.series.append(
            Series(
                np.asarray(x, dtype=np.float64).ravel(),
                np.asarray(y, dtype=np.float64).ravel(),
                color or SERIES_COLORS[idx % len(SERIES_COLORS)],
                label,
                style,
            )
        )

    # ── scaling ───────────────────────────────────────────────────────────

    def _data_range(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """``((xlo, xhi), (ylo, yhi))`` with y already in *plotting* space.

        On a log axis the limits are derived after the transform, never
        before: padding a linear minimum of 4e-3 by 5% of the span pushes it
        negative, and ``log10(|negative|)`` then lands *above* the true
        minimum — the axis silently inverts at the bottom and clips the tail
        of a converging SCF, which is exactly the part worth looking at.
        """
        xs = [s.x for s in self.series if s.x.size]
        ys = [s.y for s in self.series if s.y.size]
        if not xs or not ys:
            return (0.0, 1.0), (0.0, 1.0)
        x_all = np.concatenate(xs)
        y_all = np.concatenate(ys)
        y_all = y_all[np.isfinite(y_all)]
        if y_all.size == 0:
            y_all = np.array([0.0, 1.0])

        xlo, xhi = float(np.nanmin(x_all)), float(np.nanmax(x_all))
        hline_values = [value for value, _c, _l in self.hlines]

        if self.ylog:
            transformed = self._to_log(y_all)
            if hline_values:
                transformed = np.concatenate([transformed, self._to_log(hline_values)])
            ylo, yhi = float(transformed.min()), float(transformed.max())
            limits = (
                (float(self._to_log(self.ylim[0])), float(self._to_log(self.ylim[1])))
                if self.ylim
                else None
            )
        else:
            ylo, yhi = float(y_all.min()), float(y_all.max())
            for value in hline_values:
                ylo, yhi = min(ylo, value), max(yhi, value)
            limits = self.ylim

        if self.xlim:
            xlo, xhi = self.xlim
        if limits:
            ylo, yhi = float(limits[0]), float(limits[1])
        if xhi - xlo < 1e-12:
            xlo, xhi = xlo - 0.5, xhi + 0.5
        if yhi - ylo < 1e-12:
            ylo, yhi = ylo - 0.5, yhi + 0.5
        if not limits:
            pad = 0.05 * (yhi - ylo)
            ylo, yhi = ylo - pad, yhi + pad
        return (xlo, xhi), (ylo, yhi)

    # ── rendering ─────────────────────────────────────────────────────────

    def render(self, mode: str = "braille") -> CellGrid:
        """Draw the plot; ``mode`` is the pixel mode (``braille`` or ``half``)."""
        grid = CellGrid(self.cols, self.rows, self.background)
        # Row 0 is always reserved for the title and the y-axis caption, even
        # when there is no title: sharing it with the plot area puts the
        # caption on top of the topmost y tick.
        top = 1
        # Below the data area: the axis row, the tick-label row, then the
        # optional x caption and legend. The tick-label row used to be left
        # out, so without a caption the labels fell off the grid and with one
        # the caption overwrote them (#38-1).
        bottom = 2 + (1 if self.xlabel else 0)
        legend_rows = (
            1
            if any(s.label for s in self.series) or any(lbl for _v, _c, lbl in self.hlines)
            else 0
        )
        bottom += legend_rows

        plot_cols = max(4, self.cols - self.margin_left - 1)
        plot_rows = max(2, self.rows - top - bottom)
        if self.ylabel:
            # The caption owns the start of row 0 in full; it was cut to the
            # margin width ("E − E_F (e", #38-2).
            grid.text(0, 0, self.ylabel[: self.cols], _LABEL)
        if self.title:
            start = max(0, (self.cols - len(self.title)) // 2)
            if self.ylabel:
                start = max(start, len(self.ylabel) + 2)
            grid.text(0, start, self.title, _TITLE)

        width, height = pixel_size(plot_cols, plot_rows, mode)
        pixels = np.zeros((height, width, 3), dtype=np.uint8)
        pixels[:] = self.background
        covered = np.zeros((height, width), dtype=bool)

        # _data_range already returns y in plotting space (log10 when ylog).
        (xlo, xhi), (ylo_t, yhi_t) = self._data_range()
        if yhi_t - ylo_t < 1e-12:
            yhi_t = ylo_t + 1.0

        def to_px(x: ArrayLike, y: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
            fx = (np.asarray(x, dtype=np.float64) - xlo) / (xhi - xlo)
            yy = self._to_log(np.asarray(y, dtype=np.float64)) if self.ylog else np.asarray(y)
            fy = (yy - ylo_t) / (yhi_t - ylo_t)
            return fx * (width - 1), (1.0 - fy) * (height - 1)

        # Guide lines first so data draws over them.
        for value, color, _label in self.hlines:
            _, py = to_px(np.array([xlo]), np.array([value]))
            self._dashed_row(pixels, covered, py[0], color)
        for value, color, _label in self.vlines:
            # x only — routing a dummy y through to_px would double-apply the
            # log transform on a log axis.
            fx = (float(value) - xlo) / (xhi - xlo) if xhi > xlo else 0.0
            self._dashed_col(pixels, covered, fx * (width - 1), color)

        baseline_px: float | None = None
        if self.ylog:
            baseline_px = float(height - 1)
        else:
            _, zero_y = to_px(np.array([xlo]), np.array([0.0]))
            baseline_px = float(np.clip(zero_y[0], 0, height - 1))

        for item in self.series:
            if item.x.size == 0:
                continue
            px, py = to_px(item.x, item.y)
            if item.style == "points":
                self._plot_points(pixels, covered, px, py, item.color)
            elif item.style == "sticks":
                base = np.full_like(py, baseline_px)
                self._plot_segments(pixels, covered, px, base, px, py, item.color)
            else:
                self._plot_segments(pixels, covered, px[:-1], py[:-1], px[1:], py[1:], item.color)

        grid.blit(pixels, covered, top, self.margin_left, mode)
        self._draw_frame(grid, top, plot_rows, plot_cols)
        self._draw_yticks(grid, top, plot_rows, ylo_t, yhi_t)
        self._draw_xticks(grid, top + plot_rows, plot_cols, xlo, xhi)

        row = top + plot_rows + 2
        if self.xlabel:
            grid.text(
                row,
                self.margin_left + max(0, (plot_cols - len(self.xlabel)) // 2),
                self.xlabel,
                _LABEL,
            )
            row += 1
        if legend_rows:
            self._draw_legend(grid, row)
        return grid

    @staticmethod
    def _to_log(values: ArrayLike | float) -> np.ndarray:
        """Log10 with a floor, so a converged |ΔE| of exactly 0 still plots."""
        arr = np.asarray(values, dtype=np.float64)
        logged: np.ndarray = np.log10(np.maximum(np.abs(arr), 1e-16))
        return logged

    @staticmethod
    def _plot_segments(
        pixels: np.ndarray,
        covered: np.ndarray,
        x0: np.ndarray,
        y0: np.ndarray,
        x1: np.ndarray,
        y1: np.ndarray,
        color: RGB,
    ) -> None:
        """Rasterize a batch of line segments in one vectorized pass.

        Each segment gets as many samples as its longer pixel span, produced
        with a repeat-and-offset trick rather than a Python loop per segment
        — a spin-polarized band structure is tens of thousands of segments
        and a loop there is visible as lag while panning.
        """
        x0 = np.asarray(x0, dtype=np.float64).ravel()
        y0 = np.asarray(y0, dtype=np.float64).ravel()
        x1 = np.asarray(x1, dtype=np.float64).ravel()
        y1 = np.asarray(y1, dtype=np.float64).ravel()
        if x0.size == 0:
            return
        good = np.isfinite(x0) & np.isfinite(y0) & np.isfinite(x1) & np.isfinite(y1)
        x0, y0, x1, y1 = x0[good], y0[good], x1[good], y1[good]
        if x0.size == 0:
            return

        steps = np.maximum(np.abs(x1 - x0), np.abs(y1 - y0)).astype(np.int64) + 1
        seg = np.repeat(np.arange(len(steps)), steps)
        offsets = np.concatenate([[0], np.cumsum(steps)[:-1]])
        t_index = np.arange(steps.sum()) - offsets[seg]
        t = t_index / np.maximum(steps[seg] - 1, 1)

        px = np.round(x0[seg] + (x1[seg] - x0[seg]) * t).astype(np.int64)
        py = np.round(y0[seg] + (y1[seg] - y0[seg]) * t).astype(np.int64)
        Plot._paint(pixels, covered, px, py, color)

    @staticmethod
    def _plot_points(
        pixels: np.ndarray, covered: np.ndarray, px: np.ndarray, py: np.ndarray, color: RGB
    ) -> None:
        Plot._paint(
            pixels, covered, np.round(px).astype(np.int64), np.round(py).astype(np.int64), color
        )

    @staticmethod
    def _paint(
        pixels: np.ndarray, covered: np.ndarray, px: np.ndarray, py: np.ndarray, color: RGB
    ) -> None:
        h, w = covered.shape
        keep = (px >= 0) & (px < w) & (py >= 0) & (py < h)
        if not keep.any():
            return
        px, py = px[keep], py[keep]
        pixels[py, px] = color
        covered[py, px] = True

    @staticmethod
    def _dashed_row(pixels: np.ndarray, covered: np.ndarray, py: float, color: RGB) -> None:
        h, w = covered.shape
        row = int(round(py))
        if 0 <= row < h:
            cols = np.arange(0, w, 3)
            pixels[row, cols] = color
            covered[row, cols] = True

    @staticmethod
    def _dashed_col(pixels: np.ndarray, covered: np.ndarray, px: float, color: RGB) -> None:
        h, w = covered.shape
        col = int(round(px))
        if 0 <= col < w:
            rows = np.arange(0, h, 3)
            pixels[rows, col] = color
            covered[rows, col] = True

    def _draw_frame(self, grid: CellGrid, top: int, plot_rows: int, plot_cols: int) -> None:
        axis_col = self.margin_left - 1
        for r in range(top, top + plot_rows):
            grid.text(r, axis_col, "│", _AXIS)
        grid.text(top + plot_rows, axis_col, "└", _AXIS)
        for c in range(self.margin_left, self.margin_left + plot_cols):
            grid.text(top + plot_rows, c, "─", _AXIS)

    def _draw_yticks(
        self, grid: CellGrid, top: int, plot_rows: int, ylo_t: float, yhi_t: float
    ) -> None:
        n_ticks = max(2, min(6, plot_rows // 2))
        for i in range(n_ticks):
            frac = i / (n_ticks - 1)
            row = top + int(round((1.0 - frac) * (plot_rows - 1)))
            value = ylo_t + frac * (yhi_t - ylo_t)
            # On a log axis the tick sits at an arbitrary decade fraction, so
            # label the value it represents rather than rounding the exponent
            # — `1e{-2.3:.0f}` prints "1e-2" for three ticks running.
            text = _fmt(10.0**value) if self.ylog else _fmt(_snap(value, yhi_t - ylo_t))
            grid.text_right(row, self.margin_left - 2, text[: self.margin_left - 1], _LABEL)

    def _draw_xticks(
        self, grid: CellGrid, row: int, plot_cols: int, xlo: float, xhi: float
    ) -> None:
        if self.xticks is not None:
            # Categorical ticks (band-structure high-symmetry points): place
            # each label at its own data position and drop any that would
            # overlap a label already placed.
            taken: list[tuple[int, int]] = []
            for value, label in self.xticks:
                if not label:
                    continue
                frac = (value - xlo) / (xhi - xlo) if xhi > xlo else 0.0
                center = self.margin_left + int(round(frac * (plot_cols - 1)))
                start = max(self.margin_left, center - len(label) // 2)
                end = start + len(label) - 1
                if any(not (end < a or start > b) for a, b in taken):
                    continue
                taken.append((start, end))
                grid.text(row + 1, start, label, _LABEL)
                grid.text(row, center, "┴", _AXIS)
            return

        for frac in (0.0, 0.5, 1.0):
            value = _snap(xlo + frac * (xhi - xlo), xhi - xlo)
            text = _fmt(value)
            center = self.margin_left + int(round(frac * (plot_cols - 1)))
            start = int(np.clip(center - len(text) // 2, self.margin_left, self.cols - len(text)))
            grid.text(row + 1, start, text, _LABEL)
            grid.text(row, center, "┴", _AXIS)

    def _draw_legend(self, grid: CellGrid, row: int) -> None:
        col = self.margin_left
        for item in self.series:
            if not item.label:
                continue
            entry = f"── {item.label}"
            if col + len(entry) + 2 > self.cols:
                break
            grid.text(row, col, entry, item.color)
            col += len(entry) + 2
        for _value, color, label in self.hlines:
            if not label:
                continue
            entry = f"-- {label}"
            if col + len(entry) + 2 > self.cols:
                break
            grid.text(row, col, entry, color)
            col += len(entry) + 2


def _snap(value: float, span: float) -> float:
    """Zero for a tick that is zero up to rounding of the axis span.

    A symmetric axis puts its middle tick at a float-noise value such as
    -8.7e-04 (#38-13); within 0.1 % of the span it is labelled 0.
    """
    return 0.0 if abs(value) <= 1e-3 * abs(span) else value


def _fmt(value: float) -> str:
    """Compact fixed-or-scientific tick text that fits a narrow margin."""
    if value == 0:
        return "0"
    magnitude = abs(value)
    if magnitude >= 1e5 or magnitude < 1e-3:
        return f"{value:.1e}"
    if magnitude >= 100:
        return f"{value:.0f}"
    if magnitude >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


# ── per-kind builders ─────────────────────────────────────────────────────


def bands_plot(
    reader: QVFReader, section_id: str, cols: int, rows: int, energy_unit: str = "ev"
) -> Plot:
    """Band structure along the k-path, Fermi-referenced when in range.

    ``energy_unit`` is the browser's ``band_energy_unit`` setting (#58); the
    stored eigenvalues are eV.
    """
    from vibeview.renderers.bands import band_axis_ticks

    data = reader.read_bands(section_id)
    eigenvalues = np.asarray(data.eigenvalues, dtype=np.float64)
    n_spin, n_k, _n_bands = eigenvalues.shape
    fermi = data.fermi

    # Same convention as the browser (BandsRenderer._fermi): fermi == 0.0 is
    # the writer's no-Fermi-level sentinel, and any other value references
    # the axis to E_F whether or not it falls inside the plotted bands. The
    # terminal used to treat 0.0 as a real level and to reference only when
    # E_F lay inside the data, so the two front ends labelled one file
    # differently (#38-3).
    if fermi is not None and float(fermi) == 0.0:
        fermi = None
    unit, factor = band_axis_unit(energy_unit)
    ylabel = f"E ({unit})"
    if fermi is not None:
        eigenvalues = eigenvalues - float(fermi)
        fermi = 0.0
        ylabel = f"E − E_F ({unit})"
    eigenvalues = eigenvalues * factor

    plot = Plot(cols, rows, title="Band structure", ylabel=ylabel)
    kx = np.arange(n_k, dtype=np.float64)
    for spin in range(n_spin):
        color = SERIES_COLORS[spin % len(SERIES_COLORS)]
        bands = eigenvalues[spin]
        # One Series per band, all in the spin channel's colour; only the
        # first band of a channel carries a legend label, since a per-band
        # entry would be meaningless.
        for band in range(bands.shape[1]):
            plot.add(
                kx,
                bands[:, band],
                color,
                label=(f"spin {spin + 1}" if band == 0 and n_spin > 1 else ""),
            )
    if fermi is not None:
        plot.hlines.append((float(fermi), _FERMI, "E_F"))

    positions, labels, _boundaries = band_axis_ticks(data.kpath, n_k)
    if positions:
        plot.xticks = [(float(p), lbl) for p, lbl in zip(positions, labels, strict=True)]
    else:
        plot.xlabel = "k-point index"
    return plot


def dos_plot(
    reader: QVFReader, section_id: str, cols: int, rows: int, energy_unit: str = "ev"
) -> Plot:
    """DOS energies are already relative to E_F (QVF specification §4.3).

    ``energy_unit`` sets the energy axis (#58). The DOS is rescaled with it,
    states per displayed unit, as in the browser.
    """
    from vibeview.renderers.dos import DOSRenderer

    section = reader.get_section(section_id)
    renderer = DOSRenderer(section, reader)
    raw_energies, raw_values = renderer.load()
    energies = np.asarray(raw_energies, dtype=np.float64).ravel()
    values = np.asarray(raw_values, dtype=np.float64)
    if renderer.is_projected:
        channels = renderer.projected_channel_labels()
    else:
        if values.ndim == 1:
            values = values[None, :]
        channels = (
            [f"spin {i + 1}" for i in range(values.shape[0])] if values.shape[0] > 1 else [""]
        )

    # The stored grid is referenced to E_F=0 even when absolute Fermi metadata
    # is missing, zero or outside the grid. Subtracting it shifts DOS twice.
    unit, factor = band_axis_unit(energy_unit)
    energies = energies * factor
    values = values / factor
    # Per eV unless said otherwise, as the terminal always labelled it.
    ylabel = "DOS" if unit == "eV" else f"DOS (/{unit})"
    plot = Plot(cols, rows, title=section.kind, xlabel=f"E − E_F ({unit})", ylabel=ylabel)
    for i in range(values.shape[0]):
        plot.add(energies, values[i], label=channels[i])
    plot.vlines.append((0.0, _FERMI, "E_F"))
    return plot


def spectra_plot(
    reader: QVFReader, section_id: str, cols: int, rows: int, broaden: bool = True
) -> Plot:
    """Stick spectrum, optionally with a Lorentzian envelope.

    The sticks are the computed transitions; the envelope is a display
    convenience only, so it is drawn *under* them and named in the legend
    rather than replacing the data.
    """
    from vibeview.renderers.spectra import broadened_envelope, spectrum_grid, spectrum_style

    section = reader.get_section(section_id)
    data = reader.read_spectra(section_id)
    freqs = np.asarray(data.frequencies, dtype=np.float64).ravel()
    intens = np.asarray(data.intensities, dtype=np.float64).ravel()

    # Axis identity comes from the per-kind registry shared with the
    # Plotly/matplotlib renderers: the QVF `frequencies` member is stored
    # in the kind's native unit (cm⁻¹ vibrational, eV electronic) and is
    # never converted here, so the label must be per-kind too. The old
    # magnitude guess (`uvvis: max < 2000 → "nm"`) labelled every
    # eV-valued spectrum "nm" while plotting eV.
    style = spectrum_style(section, freqs)
    plot = Plot(cols, rows, title=section.kind, xlabel=style.x_label, ylabel="Intensity")

    if broaden and freqs.size:
        width = style.gamma
        axis = spectrum_grid(freqs, style)
        envelope = broadened_envelope(freqs, intens, width, axis)
        unit = f" {style.x_unit_short}" if style.x_unit_short else ""
        plot.add(axis, envelope, (90, 100, 120), label=f"Lorentzian γ={width:.3g}{unit}")
    plot.add(freqs, intens, SERIES_COLORS[1], label="transitions", style="sticks")
    return plot


def scf_history_plot(reader: QVFReader, section_id: str, cols: int, rows: int) -> Plot:
    """SCF convergence: |ΔE| and the DIIS error on a shared log axis."""
    data = reader.read_scf_history(section_id)
    iters = data.iterations or []
    if not iters:
        return Plot(cols, rows, title="SCF history (empty)")

    steps = np.array([float(it.get("iter", i + 1)) for i, it in enumerate(iters)])
    plot = Plot(
        cols, rows, title="SCF convergence", xlabel="iteration", ylabel="residual", ylog=True
    )

    delta = np.array([as_float(it.get("delta_e"), _GAP) for it in iters])
    if np.isfinite(delta).any():
        plot.add(steps, np.abs(delta), SERIES_COLORS[0], label="|ΔE| (Ha)")
    diis = np.array([as_float(it.get("diis_error"), _GAP) for it in iters])
    if np.isfinite(diis).any():
        plot.add(steps, np.abs(diis), SERIES_COLORS[1], label="DIIS error")
    if not plot.series:
        energy = np.array([as_float(it.get("energy_eh"), _GAP) for it in iters])
        plot.ylog = False
        plot.ylabel = "E (Ha)"
        plot.add(steps, energy, SERIES_COLORS[2], label="E (Ha)")
    return plot


def eos_plot(reader: QVFReader, section_id: str, cols: int, rows: int) -> Plot:
    """Equation of state: computed points plus the fitted curve."""
    from vibeview.renderers.eos import sample_eos_fit

    data = reader.read_equation_of_state(section_id)
    volumes = np.asarray(data.volumes, dtype=np.float64)
    energies = np.asarray(data.energies, dtype=np.float64)
    fit = data.fit or {}
    plot = Plot(
        cols,
        rows,
        title=f"Equation of state ({fit.get('model', 'fit')})",
        xlabel="V (Å³)",
        ylabel="E (eV)",
    )
    plot.add(volumes, energies, SERIES_COLORS[1], label="Computed", style="points")
    sampled = sample_eos_fit(data)
    if sampled is not None:
        plot.add(sampled.volumes, sampled.energies, SERIES_COLORS[0], label=sampled.label)
        plot.vlines.append((sampled.volume0, _FERMI, f"V₀={sampled.volume0:.2f}"))
    return plot


def phonon_bands_plot(reader: QVFReader, section_id: str, cols: int, rows: int) -> Plot:
    """Phonon dispersion, with the ω=0 line marked."""
    from vibeview.renderers.bands import band_axis_ticks

    data = reader.read_phonon_bands(section_id)
    freqs = np.asarray(data.frequencies, dtype=np.float64)
    n_q = freqs.shape[0]
    plot = Plot(cols, rows, title="Phonon dispersion", ylabel="ω (cm⁻¹)")
    qx = np.arange(n_q, dtype=np.float64)
    for mode in range(freqs.shape[1]):
        plot.add(qx, freqs[:, mode], SERIES_COLORS[0])
    # Imaginary modes are conventionally written as negative frequencies, so
    # the zero line is the stability read-off — always draw it.
    plot.hlines.append((0.0, _FERMI, "ω=0"))
    positions, labels, _b = band_axis_ticks(data.qpath, n_q)
    if positions:
        plot.xticks = [(float(p), lbl) for p, lbl in zip(positions, labels, strict=True)]
    else:
        plot.xlabel = "q-point index"
    return plot


def phonon_dos_plot(reader: QVFReader, section_id: str, cols: int, rows: int) -> Plot:
    data = reader.read_phonon_dos(section_id)
    plot = Plot(cols, rows, title="Phonon DOS", xlabel="ω (cm⁻¹)", ylabel="g(ω)")
    plot.add(np.asarray(data.frequencies), np.asarray(data.dos), SERIES_COLORS[0])
    return plot


def coop_plot(
    reader: QVFReader, section_id: str, cols: int, rows: int, energy_unit: str = "ev"
) -> Plot:
    """COOP / COHP bonding analysis, one trace per atom pair.

    ``energy_unit`` is the energy-axis unit, the Band / DOS energy unit
    (#58); the stored grid stays in eV.
    """
    section = reader.get_section(section_id)
    data = reader.read_dos_coop(section_id)
    unit, factor = band_axis_unit(energy_unit)
    energies = np.asarray(data.energies, dtype=np.float64)
    projections = np.asarray(data.projections, dtype=np.float64)
    if projections.ndim == 1:
        projections = projections[None, :]
    raw_labels = data.meta.get("pair_labels", []) if isinstance(data.meta, dict) else []
    labels = list(raw_labels) if isinstance(raw_labels, list | tuple) else []

    xlabel = f"Energy ({unit})"
    raw_fermi = data.meta.get("fermi_energy_ev") if isinstance(data.meta, dict) else None
    fermi = float(raw_fermi) if isinstance(raw_fermi, int | float) else None
    if fermi is not None and energies.size and energies.min() <= fermi <= energies.max():
        energies = energies - fermi
        xlabel = f"E − E_F ({unit})"
    energies = energies * factor

    plot = Plot(cols, rows, title=section.kind, xlabel=xlabel, ylabel=section.kind.split(".")[-1])
    for i in range(projections.shape[0]):
        label = str(labels[i]) if i < len(labels) else f"pair {i + 1}"
        plot.add(energies, projections[i], label=label)
    plot.hlines.append((0.0, _GRID, ""))
    return plot


def reaction_plot(reader: QVFReader, section_id: str, cols: int, rows: int) -> Plot:
    """Energy profile along a reaction path, with waypoints marked."""
    data = reader.read_reaction_path(section_id)
    if not data.energies:
        return Plot(cols, rows, title="Reaction path (no energies)")
    energies = np.asarray(data.energies, dtype=np.float64)
    # Relative to the first frame in kcal/mol: an absolute Hartree total on
    # the y-axis hides the barrier in the noise of the leading digits.
    relative = (energies - energies[0]) * HARTREE_TO_KCAL_MOL
    coord = (
        np.asarray(data.reaction_coordinate, dtype=np.float64)
        if data.reaction_coordinate
        else np.arange(len(energies), dtype=np.float64)
    )
    label = data.reaction_coordinate_label or "frame"
    if data.reaction_coordinate_unit:
        label = f"{label} ({data.reaction_coordinate_unit})"

    plot = Plot(cols, rows, title="Reaction profile", xlabel=label, ylabel="ΔE (kcal/mol)")
    plot.add(coord, relative, SERIES_COLORS[0], label="ΔE")
    for waypoint in data.waypoints or []:
        idx = int(waypoint.frame_index)
        if 0 <= idx < len(coord):
            label = waypoint.label or waypoint.kind
            plot.vlines.append((float(coord[idx]), SERIES_COLORS[3], label))
    return plot


def trajectory_energy_plot(reader: QVFReader, section_id: str, cols: int, rows: int) -> Plot:
    """Per-frame energy of a trajectory.

    Returns an empty, titled plot rather than None when the archive carries
    no energies, so the caller never has to special-case the return before
    calling ``.render()``.
    """
    data = reader.read_trajectory(section_id)
    if not data.energies:
        return Plot(cols, rows, title="Trajectory carries no per-frame energies")
    energies = np.asarray(data.energies, dtype=np.float64)
    relative = (energies - energies.min()) * HARTREE_TO_KCAL_MOL
    plot = Plot(cols, rows, title="Trajectory energy", xlabel="frame", ylabel="ΔE (kcal/mol)")
    plot.add(np.arange(len(energies), dtype=np.float64), relative, SERIES_COLORS[0])
    return plot


def scan_surface_plot(
    reader: QVFReader, section_id: str, cols: int, rows: int, mode: str = "half"
) -> CellGrid:
    """2D relaxed scan as a colour heat map.

    Half-block mode by default: a surface carries its information in colour,
    and half-blocks give two true colours per cell where braille would
    average eight dots into one. An explicitly chosen pixel mode is honoured
    (#38-10).
    """
    data = reader.read_scan_surface(section_id)
    energies = np.asarray(data.energies, dtype=np.float64)
    grid = CellGrid(cols, rows)

    title = "Relaxed scan (kcal/mol vs minimum)"
    grid.text(0, max(0, (cols - len(title)) // 2), title, _TITLE)
    margin = 10
    plot_cols = max(4, cols - margin - 1)
    plot_rows = max(2, rows - 4)

    width, height = pixel_size(plot_cols, plot_rows, mode)
    finite = energies[np.isfinite(energies)]
    if finite.size == 0:
        grid.text(2, 2, "no finite energies in scan", _LABEL)
        return grid
    relative = (energies - finite.min()) * HARTREE_TO_KCAL_MOL

    # Nearest-neighbour resample onto the pixel grid; energies[a, b] maps to
    # (x=b, y=a) with y flipped so axis_a increases upward like a plot.
    ai = np.clip((np.arange(height) / max(height - 1, 1) * (relative.shape[0] - 1)), 0, None)
    bi = np.clip((np.arange(width) / max(width - 1, 1) * (relative.shape[1] - 1)), 0, None)
    sampled = relative[np.round(ai[::-1]).astype(int)[:, None], np.round(bi).astype(int)[None, :]]

    hi = float(np.nanmax(sampled)) or 1.0
    t = np.clip(np.nan_to_num(sampled) / hi, 0.0, 1.0)
    # The same viridis matplotlib draws the browser's heat maps with.
    from matplotlib import colormaps

    pixels = (np.asarray(colormaps["viridis"](t))[..., :3] * 255).astype(np.uint8)
    grid.blit(pixels, np.ones((height, width), dtype=bool), 1, margin, mode=mode)

    axis_a = np.asarray(data.axis_a, dtype=np.float64)
    axis_b = np.asarray(data.axis_b, dtype=np.float64)
    grid.text_right(1, margin - 2, _fmt(float(axis_a.max())), _LABEL)
    grid.text_right(plot_rows, margin - 2, _fmt(float(axis_a.min())), _LABEL)
    grid.text(plot_rows + 1, margin, _fmt(float(axis_b.min())), _LABEL)
    grid.text_right(plot_rows + 1, cols - 1, _fmt(float(axis_b.max())), _LABEL)
    label_a = data.coordinate_a_label or "axis a"
    label_b = data.coordinate_b_label or "axis b"
    grid.text(0, 0, label_a[: margin - 1], _LABEL)
    grid.text(plot_rows + 2, margin, f"{label_b}  ·  0 = minimum, {hi:.1f} = max", _LABEL)
    return grid


# Kinds that render as geometry but *also* have a chart form — the energy
# profile along a path. Without this the reaction/trajectory builders below
# would be unreachable, since the 3D dispatch claims those kinds first.
DUAL_KINDS = frozenset({"reaction.path", "trajectory"})


# Which kinds this module can chart, and with which builder.
PLOT_BUILDERS = {
    "trajectory": trajectory_energy_plot,
    "bands": bands_plot,
    "dos.total": dos_plot,
    "dos.projected": dos_plot,
    "dos.coop": coop_plot,
    "dos.cohp": coop_plot,
    "spectra.ir": spectra_plot,
    "spectra.raman": spectra_plot,
    "spectra.uvvis": spectra_plot,
    "spectra.ecd": spectra_plot,
    "spectra.vcd": spectra_plot,
    "spectra.generic": spectra_plot,
    "scf_history": scf_history_plot,
    "equation_of_state": eos_plot,
    "phonon_bands": phonon_bands_plot,
    "phonon_dos": phonon_dos_plot,
    "reaction.path": reaction_plot,
}

#: The builders whose energy axis follows the band/DOS energy unit (#58),
#: called with it as a fifth argument, as the browser's panels do.
ENERGY_AXIS_BUILDERS: dict[str, Callable[[QVFReader, str, int, int, str], Plot]] = {
    "bands": bands_plot,
    "dos.total": dos_plot,
    "dos.projected": dos_plot,
    "dos.coop": coop_plot,
    "dos.cohp": coop_plot,
}
