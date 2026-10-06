"""renderers._plotly is the one layout, serializer and guide line for charts."""

from __future__ import annotations

import plotly.graph_objects as go
import pytest

from vibeview.renderers import _plotly
from vibeview.renderers.palette import ACCENT_RED, GUIDE_GREY, TICK_GREY


def _figure() -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], name="s"))
    return fig


def test_chart_layout_defaults() -> None:
    fig = _plotly.chart_layout(_figure(), title="T", x_title="x", y_title="y")
    layout = fig.layout
    assert layout.title.text == "T"
    assert (
        layout.template.layout.paper_bgcolor
        == go.Figure().update_layout(template="plotly_dark").layout.template.layout.paper_bgcolor
    )
    assert layout.height == _plotly.DEFAULT_HEIGHT
    assert layout.xaxis.title.text == "x" and layout.yaxis.title.text == "y"
    assert layout.showlegend is False
    assert layout.margin.b == _plotly.MARGIN["b"]


def test_chart_layout_bottom_legend_and_axis_merge() -> None:
    fig = _plotly.chart_layout(
        _figure(),
        title="T",
        x_title="x",
        xaxis={"tickvals": [0, 1], "ticktext": ["Γ", "X"]},
        legend="bottom",
        height=300,
    )
    assert fig.layout.showlegend is True
    assert fig.layout.legend.orientation == "h"
    assert fig.layout.margin.b == _plotly.BOTTOM_LEGEND_MARGIN["b"]
    assert fig.layout.xaxis.title.text == "x"
    assert list(fig.layout.xaxis.ticktext) == ["Γ", "X"]
    assert fig.layout.height == 300
    auto = _plotly.chart_layout(_figure(), title="T", legend="default")
    assert auto.layout.showlegend is None


@pytest.mark.parametrize("policy", ["cdn", False, True])
def test_figure_to_html_passes_the_runtime_policy_through(policy) -> None:
    html = _plotly.figure_to_html(
        _figure(), include_plotlyjs=policy, extra_script="<script>1</script>"
    )
    assert html.endswith("<script>1</script>")
    assert "<html>" not in html
    if policy == "cdn":
        assert "cdn.plot.ly" in html or "plotly" in html
    if policy is False:
        assert "cdn.plot.ly" not in html
        assert len(html) < 100_000
    if policy is True:
        assert len(html) > 1_000_000


def test_guide_lines() -> None:
    fig = _figure()
    _plotly.fermi_line(fig)
    _plotly.zero_line(fig, axis="x")
    _plotly.segment_lines(fig, [0.5, 1.5])
    shapes = list(fig.layout.shapes)
    colours = [s.line.color for s in shapes]
    assert colours.count(ACCENT_RED) == 1
    assert colours.count(GUIDE_GREY) == 1
    assert colours.count(TICK_GREY) == 2
    assert fig.layout.annotations[0].text == "E<sub>F</sub>"


def test_every_chart_renderer_exposes_the_runtime_policy() -> None:
    """The app can bundle or share Plotly for every chart alike."""
    import inspect

    from vibeview.renderers import bands, coop, dos, eos, phonon, scf_history, spectra, wavefunction

    for method in (
        bands.BandsRenderer.render_to_html,
        coop.COOPRenderer.render_to_html,
        dos.DOSRenderer.render_to_html,
        eos.EquationOfStateRenderer.render_to_html,
        phonon.PhononBandsRenderer.render_to_html,
        phonon.PhononDOSRenderer.render_to_html,
        scf_history.SCFHistoryRenderer.render_to_html,
        spectra.SpectraRenderer.render_to_html,
        wavefunction.WavefunctionRenderer.render_energy_diagram,
    ):
        assert "include_plotlyjs" in inspect.signature(method).parameters, method.__qualname__
    assert "include_plotlyjs" in inspect.signature(spectra.render_comparison_html).parameters
    assert "include_plotlyjs" in inspect.signature(dos.render_bands_dos_combined).parameters
