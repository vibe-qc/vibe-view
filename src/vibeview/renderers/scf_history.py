"""SCF convergence renderer — energy + DIIS-error vs iteration.

The QVF `scf_history` section carries a JSON document of per-iteration
records (`iter`, `energy_eh`, `delta_e`, `diis_error`). We plot:

* Energy vs iteration on the left Y-axis.
* DIIS error (log-scale) on the right Y-axis, when present.

DIIS error is usually the more useful convergence indicator, but it's
optional — solvers that don't run DIIS (e.g. unaccelerated SCF, certain
periodic paths) ship history records without it. The renderer degrades
gracefully: missing DIIS error means a single-axis plot, missing
energy means an iteration-count-only sanity plot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import plotly.graph_objects as go

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, SCFHistoryData, Section


def _extract_xy(
    iters: list[dict], key: str, *, x_key: str = "iter"
) -> tuple[list[float], list[float]]:
    """Return ``(xs, ys)`` for the records that carry a numeric ``key``.

    Each ``y`` is paired with that record's ``x_key`` (the iteration number),
    or its 1-based position when ``x_key`` is absent. Records missing ``key``
    are skipped *individually* — a solver that reports ``diis_error`` on every
    cycle except the first still yields a DIIS curve for the rest, instead of
    the whole series being discarded the moment one record lacks the key.
    """
    xs: list[float] = []
    ys: list[float] = []
    for pos, rec in enumerate(iters, start=1):
        v = rec.get(key)
        if v is None:
            continue
        try:
            y = float(v)
        except (TypeError, ValueError):
            continue
        xv = rec.get(x_key)
        try:
            x = float(xv) if xv is not None else float(pos)
        except (TypeError, ValueError):
            x = float(pos)
        xs.append(x)
        ys.append(y)
    return xs, ys


class SCFHistoryRenderer(BaseRenderer):
    """Plotly convergence plot for the SCF iteration trail."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: SCFHistoryData | None = None

    def load(self) -> SCFHistoryData:
        if self._data is None:
            self._data = self.reader.read_scf_history(self.section_id)
        return self._data

    def render_to_html(self, skip_first: bool = False, log_energy: bool = False) -> str:
        data = self.load()
        iters = data.iterations
        # The initial-guess energy is often hundreds of Hartree above the
        # converged value; hiding it lets the Y axis resolve the actual
        # convergence tail (design refresh 2026, item 4).
        if skip_first and len(iters) > 1:
            iters = iters[1:]
        if not iters:
            return (
                '<div style="padding: 16px;">'
                "SCF history section is empty (no iteration records)."
                "</div>"
            )

        # Each series carries its own x (the iteration number, or 1..N) so a
        # record missing one field doesn't shift or drop the others.
        e_x, energies = _extract_xy(iters, "energy_eh")
        d_x, diis = _extract_xy(iters, "diis_error")
        # |ΔE| per cycle, zeros dropped so the log axis stays defined.
        _dx_raw, _de_raw = _extract_xy(iters, "delta_e")
        delta_x: list[float] = []
        delta_e: list[float] = []
        for xi, dv in zip(_dx_raw, _de_raw, strict=True):
            mag = abs(float(dv))
            if mag > 0.0:
                delta_x.append(xi)
                delta_e.append(mag)

        # Log-convergence view: plot |E - E_final| on a log axis so the last
        # orders of magnitude of the tail become visible (a linear energy axis
        # flattens everything after the first couple of cycles). The final
        # point is exactly zero (log-undefined), so it's dropped (design
        # refresh 2026, SCF item).
        log_x: list[float] = []
        log_de: list[float] = []
        if log_energy and len(energies) > 1:
            e_final = energies[-1]
            for xi, e in zip(e_x, energies, strict=True):
                de = abs(e - e_final)
                if de > 1e-14:
                    log_x.append(xi)
                    log_de.append(de)

        fig = go.Figure()
        if log_energy and log_de:
            fig.add_trace(
                go.Scatter(
                    x=log_x,
                    y=log_de,
                    mode="lines+markers",
                    name="|E − E_final| (Eh)",
                    line={"color": "#CC3333", "width": 1.5},
                    marker={"size": 5},
                    hovertemplate="iter %{x}<br>|ΔE| = %{y:.2e} Eh<extra></extra>",
                    yaxis="y",
                )
            )
        elif energies:
            fig.add_trace(
                go.Scatter(
                    x=e_x,
                    y=energies,
                    mode="lines+markers",
                    name="Energy (Eh)",
                    line={"color": "#CC3333", "width": 1.5},
                    marker={"size": 5},
                    hovertemplate="iter %{x}<br>E = %{y:.10f} Eh<extra></extra>",
                    yaxis="y",
                )
            )
        if diis:
            # Log Y for DIIS — convergence spans many decades.
            fig.add_trace(
                go.Scatter(
                    x=d_x,
                    y=diis,
                    mode="lines+markers",
                    name="DIIS error",
                    line={"color": "#3366CC", "width": 1.5, "dash": "dot"},
                    marker={"size": 5},
                    hovertemplate="iter %{x}<br>‖e‖ = %{y:.2e}<extra></extra>",
                    yaxis="y2",
                )
            )
        # Per-cycle energy change as bars on the same log residual axis.
        # vibe-qc's solvers record delta_e on every cycle but (as of 2026-07)
        # emit no diis_error, so without this the right-hand axis stayed empty
        # and the file's only convergence signal went unplotted. |ΔE| because
        # the sign flips while the energy settles, and log because it spans
        # decades; exact zeros are dropped (log-undefined).
        if delta_x and delta_e:
            fig.add_trace(
                go.Bar(
                    x=delta_x,
                    y=delta_e,
                    name="|ΔE| per cycle",
                    marker={"color": "#3366CC", "opacity": 0.45},
                    hovertemplate="iter %{x}<br>|ΔE| = %{y:.2e} Eh<extra></extra>",
                    yaxis="y2",
                )
            )

        layout: dict = {
            "title": "SCF Convergence",
            "xaxis": {"title": "Iteration"},
            "template": "plotly_dark",
            "hovermode": "x unified",
            "height": 380,
            "margin": {"l": 70, "r": 70, "t": 50, "b": 50},
            "showlegend": bool(energies and (diis or delta_e)),
        }
        if log_energy and log_de:
            layout["yaxis"] = {
                "title": "|E − E_final| (Eh)",
                "side": "left",
                "type": "log",
            }
        elif energies:
            layout["yaxis"] = {"title": "Energy (Eh)", "side": "left"}
        if diis or delta_e:
            # One shared log residual axis. Title names whichever series the
            # file actually carries (in practice that is |ΔE|).
            if diis and delta_e:
                right_title = "DIIS error / |ΔE| (log)"
            elif diis:
                right_title = "DIIS error (log)"
            else:
                right_title = "|ΔE| per cycle (Eh, log)"
            layout["yaxis2"] = {
                "title": right_title,
                "overlaying": "y" if energies else None,
                "side": "right",
                "type": "log",
            }
        fig.update_layout(**layout)

        return fig.to_html(full_html=False, include_plotlyjs="cdn")
