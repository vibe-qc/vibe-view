"""Braille XY plots for the chart-shaped QVF kinds.

Bands, DOS, spectra, SCF traces, equations of state and COOP/COHP all
reduce to the same primitive: a framed data area with autoscaled axes, tick
labels in the margins, and one or more series drawn into it. The data area
is a braille pixel buffer (two dots wide, four tall per cell); the margins
are plain text in the same :class:`~vibeview.tui.braille.CellGrid`.

Axis conventions follow the Plotly renderers so a terminal plot and the
interactive one carry the same message: bands and DOS are Fermi-referenced
when E_F falls inside the data window and absolute otherwise (with E_F
named in the axis label), and SCF convergence is plotted as |ΔE| on a log
axis.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from vibeview.tui.braille import CellGrid, pixel_size

_AXIS = (120, 126, 140)
_LABEL = (196, 202, 214)
_TITLE = (236, 240, 248)
_GRID = (56, 60, 72)
_FERMI = (240, 120, 120)

# Series palette — the Okabe-Ito qualitative set, which stays distinguishable
# under the common colour-vision deficiencies and on a dark terminal.
SERIES_COLORS = [
    (86, 180, 233),
    (230, 159, 0),
    (0, 158, 115),
    (204, 121, 167),
    (240, 228, 66),
    (0, 114, 178),
    (213, 94, 0),
    (150, 200, 150),
]


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

    def add(self, x, y, color=None, label="", style="line") -> None:
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

    def _data_range(self):
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
            limits = (self._to_log(self.ylim[0]), self._to_log(self.ylim[1])) if self.ylim else None
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

    def render(self) -> CellGrid:
        grid = CellGrid(self.cols, self.rows, self.background)
        # Row 0 is always reserved for the title and the y-axis caption, even
        # when there is no title: sharing it with the plot area puts the
        # caption on top of the topmost y tick.
        top = 1
        bottom = 1 + (1 if self.xlabel else 0)
        legend_rows = 1 if any(s.label for s in self.series) else 0
        bottom += legend_rows

        plot_cols = max(4, self.cols - self.margin_left - 1)
        plot_rows = max(2, self.rows - top - bottom)
        if self.title:
            grid.text(0, max(0, (self.cols - len(self.title)) // 2), self.title, _TITLE)

        width, height = pixel_size(plot_cols, plot_rows)
        pixels = np.zeros((height, width, 3), dtype=np.uint8)
        pixels[:] = self.background
        covered = np.zeros((height, width), dtype=bool)

        # _data_range already returns y in plotting space (log10 when ylog).
        (xlo, xhi), (ylo_t, yhi_t) = self._data_range()
        if yhi_t - ylo_t < 1e-12:
            yhi_t = ylo_t + 1.0

        def to_px(x, y):
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

        baseline_px = None
        if self.ylog:
            baseline_px = height - 1
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

        grid.blit(pixels, covered, top, self.margin_left)
        self._draw_frame(grid, top, plot_rows, plot_cols)
        self._draw_yticks(grid, top, plot_rows, ylo_t, yhi_t)
        self._draw_xticks(grid, top + plot_rows, plot_cols, xlo, xhi)

        row = top + plot_rows + 1
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
        if self.ylabel:
            grid.text(0, 0, self.ylabel[: self.margin_left], _LABEL)
        return grid

    @staticmethod
    def _to_log(values):
        """Log10 with a floor, so a converged |ΔE| of exactly 0 still plots."""
        arr = np.asarray(values, dtype=np.float64)
        return np.log10(np.maximum(np.abs(arr), 1e-16))

    @staticmethod
    def _plot_segments(pixels, covered, x0, y0, x1, y1, color):
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
    def _plot_points(pixels, covered, px, py, color):
        Plot._paint(
            pixels, covered, np.round(px).astype(np.int64), np.round(py).astype(np.int64), color
        )

    @staticmethod
    def _paint(pixels, covered, px, py, color):
        h, w = covered.shape
        keep = (px >= 0) & (px < w) & (py >= 0) & (py < h)
        if not keep.any():
            return
        px, py = px[keep], py[keep]
        pixels[py, px] = color
        covered[py, px] = True

    @staticmethod
    def _dashed_row(pixels, covered, py, color):
        h, w = covered.shape
        row = int(round(py))
        if 0 <= row < h:
            cols = np.arange(0, w, 3)
            pixels[row, cols] = color
            covered[row, cols] = True

    @staticmethod
    def _dashed_col(pixels, covered, px, color):
        h, w = covered.shape
        col = int(round(px))
        if 0 <= col < w:
            rows = np.arange(0, h, 3)
            pixels[rows, col] = color
            covered[rows, col] = True

    def _draw_frame(self, grid, top, plot_rows, plot_cols):
        axis_col = self.margin_left - 1
        for r in range(top, top + plot_rows):
            grid.text(r, axis_col, "│", _AXIS)
        grid.text(top + plot_rows, axis_col, "└", _AXIS)
        for c in range(self.margin_left, self.margin_left + plot_cols):
            grid.text(top + plot_rows, c, "─", _AXIS)

    def _draw_yticks(self, grid, top, plot_rows, ylo_t, yhi_t):
        n_ticks = max(2, min(6, plot_rows // 2))
        for i in range(n_ticks):
            frac = i / (n_ticks - 1)
            row = top + int(round((1.0 - frac) * (plot_rows - 1)))
            value = ylo_t + frac * (yhi_t - ylo_t)
            # On a log axis the tick sits at an arbitrary decade fraction, so
            # label the value it represents rather than rounding the exponent
            # — `1e{-2.3:.0f}` prints "1e-2" for three ticks running.
            text = _fmt(10.0**value) if self.ylog else _fmt(value)
            grid.text_right(row, self.margin_left - 2, text[: self.margin_left - 1], _LABEL)

    def _draw_xticks(self, grid, row, plot_cols, xlo, xhi):
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
            value = xlo + frac * (xhi - xlo)
            text = _fmt(value)
            center = self.margin_left + int(round(frac * (plot_cols - 1)))
            start = int(np.clip(center - len(text) // 2, self.margin_left, self.cols - len(text)))
            grid.text(row + 1, start, text, _LABEL)
            grid.text(row, center, "┴", _AXIS)

    def _draw_legend(self, grid, row):
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


def _section_meta(section, key, default=None):
    """Section-level JSON keys land in Pydantic's ``model_extra``.

    ``getattr(section, key)`` silently misses them — the same trap the DOS
    renderer documents.
    """
    extra = getattr(section, "model_extra", None) or {}
    return extra.get(key, default)


# ── per-kind builders ─────────────────────────────────────────────────────


def bands_plot(reader, section_id: str, cols: int, rows: int) -> Plot:
    """Band structure along the k-path, Fermi-referenced when in range."""
    from vibeview.renderers.bands import _band_axis_ticks

    data = reader.read_bands(section_id)
    eigenvalues = np.asarray(data.eigenvalues, dtype=np.float64)
    n_spin, n_k, _n_bands = eigenvalues.shape
    fermi = data.fermi

    ylabel = "E (eV)"
    if fermi is not None:
        lo, hi = float(eigenvalues.min()), float(eigenvalues.max())
        if lo <= float(fermi) <= hi:
            eigenvalues = eigenvalues - float(fermi)
            fermi = 0.0
            ylabel = "E − E_F (eV)"

    plot = Plot(cols, rows, title="Band structure", ylabel=ylabel)
    kx = np.arange(n_k, dtype=np.float64)
    for spin in range(n_spin):
        color = SERIES_COLORS[spin % len(SERIES_COLORS)]
        bands = eigenvalues[spin]
        # One Series per spin channel, not per band: the segment rasterizer
        # takes the whole (n_bands, n_k) block at once, and a per-band legend
        # entry would be meaningless anyway.
        for band in range(bands.shape[1]):
            plot.add(
                kx,
                bands[:, band],
                color,
                label=(f"spin {spin + 1}" if band == 0 and n_spin > 1 else ""),
            )
    if fermi is not None:
        plot.hlines.append((float(fermi), _FERMI, "E_F"))

    positions, labels, _boundaries = _band_axis_ticks(data.kpath, n_k)
    if positions:
        plot.xticks = [(float(p), lbl) for p, lbl in zip(positions, labels)]
    else:
        plot.xlabel = "k-point index"
    return plot


def dos_plot(reader, section_id: str, cols: int, rows: int) -> Plot:
    """Total or projected density of states."""
    section = reader.get_section(section_id)
    energies = np.asarray(
        reader._read_binary_member(section_id, "energies"), dtype=np.float64
    ).ravel()

    projected = section.kind == "dos.projected"
    if projected:
        values = np.asarray(
            reader._read_binary_member(section_id, "projections"), dtype=np.float64
        )
        if values.ndim == 3:
            values = values.sum(axis=0)
        channels = _section_meta(section, "channels", []) or []
    else:
        values = np.asarray(reader._read_binary_member(section_id, "dos"), dtype=np.float64)
        if values.ndim == 1:
            values = values[None, :]
        channels = (
            [f"spin {i + 1}" for i in range(values.shape[0])] if values.shape[0] > 1 else [""]
        )

    xlabel = "Energy (eV)"
    fermi = _section_meta(section, "fermi_energy_ev", None)
    if fermi is not None and energies.size:
        fermi = float(fermi)
        if float(energies.min()) <= fermi <= float(energies.max()):
            energies = energies - fermi
            xlabel = "E − E_F (eV)"
        else:
            xlabel = f"Energy (eV)  E_F={fermi:.2f} outside range"

    plot = Plot(cols, rows, title=section.kind, xlabel=xlabel, ylabel="DOS")
    for i in range(values.shape[0]):
        label = str(channels[i]) if i < len(channels) else f"ch {i + 1}"
        plot.add(energies, values[i], label=label)
    if xlabel.startswith("E −"):
        plot.vlines.append((0.0, _FERMI, "E_F"))
    return plot


def spectra_plot(reader, section_id: str, cols: int, rows: int, broaden: bool = True) -> Plot:
    """Stick spectrum, optionally with a Lorentzian envelope.

    The sticks are the computed transitions; the envelope is a display
    convenience only, so it is drawn *under* them and named in the legend
    rather than replacing the data.
    """
    from vibeview.renderers.spectra import _style_for

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
    style = _style_for(section, freqs)
    plot = Plot(cols, rows, title=section.kind, xlabel=style.x_label, ylabel="Intensity")

    if broaden and freqs.size:
        span = float(freqs.max() - freqs.min()) or 1.0
        # Span-scaled for terminal legibility (~2·cols braille samples
        # across the axis), floored at the kind's native-unit gamma —
        # never at a cm⁻¹-scale constant, which on an eV axis broadened
        # a 4–10 eV spectrum with γ=4 eV.
        width = max(span * 0.01, style.gamma)
        axis = np.linspace(freqs.min() - 3 * width, freqs.max() + 3 * width, 600)
        envelope = (
            intens[:, None] * width**2 / ((axis[None, :] - freqs[:, None]) ** 2 + width**2)
        ).sum(axis=0)
        unit = f" {style.x_unit_short}" if style.x_unit_short else ""
        plot.add(axis, envelope, (90, 100, 120), label=f"Lorentzian γ={width:.3g}{unit}")
    plot.add(freqs, intens, SERIES_COLORS[1], label="transitions", style="sticks")
    return plot


def scf_history_plot(reader, section_id: str, cols: int, rows: int) -> Plot:
    """SCF convergence: |ΔE| and the DIIS error on a shared log axis."""
    data = reader.read_scf_history(section_id)
    iters = data.iterations or []
    if not iters:
        return Plot(cols, rows, title="SCF history (empty)")

    steps = np.array([float(it.get("iter", i + 1)) for i, it in enumerate(iters)])
    plot = Plot(
        cols, rows, title="SCF convergence", xlabel="iteration", ylabel="residual", ylog=True
    )

    delta = np.array([_num(it.get("delta_e")) for it in iters])
    if np.isfinite(delta).any():
        plot.add(steps, np.abs(delta), SERIES_COLORS[0], label="|ΔE| (Ha)")
    diis = np.array([_num(it.get("diis_error")) for it in iters])
    if np.isfinite(diis).any():
        plot.add(steps, np.abs(diis), SERIES_COLORS[1], label="DIIS error")
    if not plot.series:
        energy = np.array([_num(it.get("energy_eh")) for it in iters])
        plot.ylog = False
        plot.ylabel = "E (Ha)"
        plot.add(steps, energy, SERIES_COLORS[2], label="E (Ha)")
    return plot


def eos_plot(reader, section_id: str, cols: int, rows: int) -> Plot:
    """Equation of state: computed points plus the fitted curve."""
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
    order = np.argsort(volumes)
    plot.add(volumes[order], energies[order], SERIES_COLORS[0], label="E(V)")
    plot.add(volumes, energies, SERIES_COLORS[1], label="points", style="points")
    v0 = fit.get("V0")
    if v0 is not None:
        plot.vlines.append((float(v0), _FERMI, f"V₀={float(v0):.2f}"))
    return plot


def phonon_bands_plot(reader, section_id: str, cols: int, rows: int) -> Plot:
    """Phonon dispersion, with the ω=0 line marked."""
    from vibeview.renderers.bands import _band_axis_ticks

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
    positions, labels, _b = _band_axis_ticks(data.qpath, n_q)
    if positions:
        plot.xticks = [(float(p), lbl) for p, lbl in zip(positions, labels)]
    else:
        plot.xlabel = "q-point index"
    return plot


def phonon_dos_plot(reader, section_id: str, cols: int, rows: int) -> Plot:
    data = reader.read_phonon_dos(section_id)
    plot = Plot(cols, rows, title="Phonon DOS", xlabel="ω (cm⁻¹)", ylabel="g(ω)")
    plot.add(np.asarray(data.frequencies), np.asarray(data.dos), SERIES_COLORS[0])
    return plot


def coop_plot(reader, section_id: str, cols: int, rows: int) -> Plot:
    """COOP / COHP bonding analysis, one trace per atom pair."""
    section = reader.get_section(section_id)
    data = reader.read_dos_coop(section_id)
    energies = np.asarray(data.energies, dtype=np.float64)
    projections = np.asarray(data.projections, dtype=np.float64)
    if projections.ndim == 1:
        projections = projections[None, :]
    labels = data.meta.get("pair_labels", []) if isinstance(data.meta, dict) else []

    xlabel = "Energy (eV)"
    fermi = data.meta.get("fermi_energy_ev") if isinstance(data.meta, dict) else None
    if fermi is not None and energies.size and energies.min() <= float(fermi) <= energies.max():
        energies = energies - float(fermi)
        xlabel = "E − E_F (eV)"

    plot = Plot(cols, rows, title=section.kind, xlabel=xlabel, ylabel=section.kind.split(".")[-1])
    for i in range(projections.shape[0]):
        label = str(labels[i]) if i < len(labels) else f"pair {i + 1}"
        plot.add(energies, projections[i], label=label)
    plot.hlines.append((0.0, _GRID, ""))
    return plot


def reaction_plot(reader, section_id: str, cols: int, rows: int) -> Plot:
    """Energy profile along a reaction path, with waypoints marked."""
    data = reader.read_reaction_path(section_id)
    energies = data.energies
    if not energies:
        return Plot(cols, rows, title="Reaction path (no energies)")
    energies = np.asarray(energies, dtype=np.float64)
    # Relative to the first frame in kcal/mol: an absolute Hartree total on
    # the y-axis hides the barrier in the noise of the leading digits.
    relative = (energies - energies[0]) * 627.509474
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


def trajectory_energy_plot(reader, section_id: str, cols: int, rows: int) -> Plot:
    """Per-frame energy of a trajectory.

    Returns an empty, titled plot rather than None when the archive carries
    no energies, so the caller never has to special-case the return before
    calling ``.render()``.
    """
    data = reader.read_trajectory(section_id)
    if not data.energies:
        return Plot(cols, rows, title="Trajectory carries no per-frame energies")
    energies = np.asarray(data.energies, dtype=np.float64)
    relative = (energies - energies.min()) * 627.509474
    plot = Plot(cols, rows, title="Trajectory energy", xlabel="frame", ylabel="ΔE (kcal/mol)")
    plot.add(np.arange(len(energies), dtype=np.float64), relative, SERIES_COLORS[0])
    return plot


def scan_surface_plot(reader, section_id: str, cols: int, rows: int) -> CellGrid:
    """2D relaxed scan as a colour heat map.

    Rendered in half-block mode rather than braille: a surface carries its
    information in colour, and half-blocks give two true colours per cell
    where braille would average eight dots into one.
    """
    data = reader.read_scan_surface(section_id)
    energies = np.asarray(data.energies, dtype=np.float64)
    grid = CellGrid(cols, rows)

    title = "Relaxed scan (kcal/mol vs minimum)"
    grid.text(0, max(0, (cols - len(title)) // 2), title, _TITLE)
    margin = 10
    plot_cols = max(4, cols - margin - 1)
    plot_rows = max(2, rows - 4)

    width, height = pixel_size(plot_cols, plot_rows, "half")
    finite = energies[np.isfinite(energies)]
    if finite.size == 0:
        grid.text(2, 2, "no finite energies in scan", _LABEL)
        return grid
    relative = (energies - finite.min()) * 627.509474

    # Nearest-neighbour resample onto the pixel grid; energies[a, b] maps to
    # (x=b, y=a) with y flipped so axis_a increases upward like a plot.
    ai = np.clip((np.arange(height) / max(height - 1, 1) * (relative.shape[0] - 1)), 0, None)
    bi = np.clip((np.arange(width) / max(width - 1, 1) * (relative.shape[1] - 1)), 0, None)
    sampled = relative[np.round(ai[::-1]).astype(int)[:, None], np.round(bi).astype(int)[None, :]]

    hi = float(np.nanmax(sampled)) or 1.0
    t = np.clip(np.nan_to_num(sampled) / hi, 0.0, 1.0)
    pixels = np.zeros((height, width, 3), dtype=np.uint8)
    # Viridis-like ramp: dark blue (low) → teal → yellow (high).
    pixels[..., 0] = np.clip(70 * t + 250 * np.clip(t - 0.5, 0, None) * 2, 0, 255)
    pixels[..., 1] = np.clip(40 + 215 * t, 0, 255)
    pixels[..., 2] = np.clip(110 + 120 * (1 - t) - 200 * np.clip(t - 0.5, 0, None) * 2, 0, 255)
    grid.blit(pixels, np.ones((height, width), dtype=bool), 1, margin, mode="half")

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


def _num(value) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out


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
