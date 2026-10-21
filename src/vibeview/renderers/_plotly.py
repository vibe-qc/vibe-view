"""Plotly for the chart renderers: one layout, one serializer, one guide line.

Eleven ``to_html`` calls carried five different ``include_plotlyjs``
policies, so the app could not bundle Plotly for offline or Electron use
uniformly, and ten layouts each typed their own template, height, margins
and legend. The layout every chart shares, the serializer every chart
returns through, and the Fermi, zero and segment guide lines live here.
"""

from __future__ import annotations

from typing import Any, Literal

import plotly.graph_objects as go

from vibeview.renderers.palette import ACCENT_RED, GUIDE_GREY, TICK_GREY

TEMPLATE = "plotly_dark"
DEFAULT_HEIGHT = 450
MARGIN: dict[str, int] = {"l": 60, "r": 20, "t": 50, "b": 50}
BOTTOM_LEGEND_MARGIN: dict[str, int] = {"l": 60, "r": 20, "t": 50, "b": 80}
BOTTOM_LEGEND: dict[str, Any] = {
    "orientation": "h",
    "yanchor": "top",
    "y": -0.15,
    "xanchor": "center",
    "x": 0.5,
}

Legend = Literal["none", "bottom", "default"]


def chart_layout(
    fig: go.Figure,
    *,
    title: str,
    x_title: str | None = None,
    y_title: str | None = None,
    xaxis: dict[str, Any] | None = None,
    yaxis: dict[str, Any] | None = None,
    height: int = DEFAULT_HEIGHT,
    legend: Legend = "none",
    hovermode: str = "closest",
    margin: dict[str, int] | None = None,
    **extra: Any,
) -> go.Figure:
    """Apply the shared dark layout.

    ``xaxis``/``yaxis`` are merged over the titles, so a caller can pass
    tick labels or a range without losing the axis title. ``legend`` is
    ``"none"`` (hidden), ``"bottom"`` (horizontal, centred under the plot,
    with the extra bottom margin it needs) or ``"default"`` (Plotly's).
    ``extra`` goes straight to ``update_layout`` for the odd axis-2 case.
    """
    x_axis: dict[str, Any] = {}
    if x_title is not None:
        x_axis["title"] = x_title
    x_axis.update(xaxis or {})
    y_axis: dict[str, Any] = {}
    if y_title is not None:
        y_axis["title"] = y_title
    y_axis.update(yaxis or {})
    layout: dict[str, Any] = {
        "title": title,
        "template": TEMPLATE,
        "hovermode": hovermode,
        "height": height,
        "margin": dict(margin or (BOTTOM_LEGEND_MARGIN if legend == "bottom" else MARGIN)),
    }
    # "default" leaves showlegend unset, so Plotly hides the legend of a
    # single-trace chart and shows it once there are several traces.
    if legend == "none":
        layout["showlegend"] = False
    elif legend == "bottom":
        layout["showlegend"] = True
    if x_axis:
        layout["xaxis"] = x_axis
    if y_axis:
        layout["yaxis"] = y_axis
    if legend == "bottom":
        layout["legend"] = dict(BOTTOM_LEGEND)
    layout.update(extra)
    fig.update_layout(**layout)
    return fig


def figure_to_html(
    fig: go.Figure, *, include_plotlyjs: str | bool = "cdn", extra_script: str = "",
    fill_height: bool = False,
) -> str:
    """The HTML fragment for ``fig``, with the one Plotly-runtime policy.

    ``include_plotlyjs`` is passed through so the app can choose ``"cdn"``,
    ``False`` (the page already loaded Plotly) or ``True`` (inline, for
    offline and Electron use) for every chart alike.
    """
    # A fixed-height plot inside a shorter result iframe clips its lower axis.
    # Spectrum controls already reserve their own space above the iframe.
    if fill_height:
        fig.update_layout(height=None, autosize=True)
        return ("<style>html,body,body>div{height:100%;margin:0}</style>"
                + str(fig.to_html(full_html=False, include_plotlyjs=include_plotlyjs,
                                  default_height="100%", default_width="100%")) + extra_script)
    # Plotly does not ship stubs; its serializer returns HTML text.
    return str(fig.to_html(full_html=False, include_plotlyjs=include_plotlyjs)) + extra_script


def fermi_line(
    fig: go.Figure,
    *,
    axis: Literal["x", "y"] = "y",
    at: float = 0.0,
    position: str = "top right",
    row: int | None = None,
    col: int | None = None,
) -> None:
    """The dashed red E_F guide on ``axis``, labelled.

    ``at`` is zero for a Fermi-referenced axis and the Fermi energy itself
    on an absolute one.
    """
    line = {"color": ACCENT_RED, "width": 1, "dash": "dash"}
    where: dict[str, Any] = {}
    if row is not None and col is not None:
        where = {"row": row, "col": col}
    if axis == "y":
        fig.add_hline(
            y=at, line=line, annotation_text="E<sub>F</sub>", annotation_position=position, **where
        )
    else:
        fig.add_vline(
            x=at, line=line, annotation_text="E<sub>F</sub>", annotation_position=position, **where
        )


def zero_line(fig: go.Figure, *, axis: Literal["x", "y"] = "y", width: float = 1.0) -> None:
    """The dotted grey guide at zero, for a floor or a sign change."""
    line = {"color": GUIDE_GREY, "width": width, "dash": "dot"}
    if axis == "y":
        fig.add_hline(y=0.0, line=line)
    else:
        fig.add_vline(x=0.0, line=line)


def segment_lines(
    fig: go.Figure, boundaries: Any, *, row: int | None = None, col: int | None = None
) -> None:
    """Dotted guides at the k-path or q-path segment boundaries."""
    where: dict[str, Any] = {}
    if row is not None and col is not None:
        where = {"row": row, "col": col}
    for start in boundaries:
        fig.add_vline(x=start, line={"color": TICK_GREY, "width": 0.5, "dash": "dot"}, **where)
