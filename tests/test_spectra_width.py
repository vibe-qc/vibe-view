"""Native spectral widths agree across real browser and terminal callers."""

import json

import numpy as np
import pytest

from tests.conftest import build_qvf
from vibeview.qvf import QVFReader
from vibeview.renderers.spectra import SpectraRenderer
from vibeview.tui.plots import spectra_plot


@pytest.mark.parametrize(
    "kind,frequencies,gamma",
    [
        ("spectra.ir", [300.0, 3300.0], 5.0),
        ("spectra.raman", [300.0, 3300.0], 5.0),
        ("spectra.vcd", [300.0, 3300.0], 5.0),
        ("spectra.uvvis", [4.0, 24.0], 0.1),
        ("spectra.ecd", [4.0, 24.0], 0.1),
        ("spectra.generic", [10.0, 1010.0], 5.0),
    ],
)
@pytest.mark.parametrize("intensity", [1.0, -1.0])
def test_default_half_width_matches_native_kind_policy(
    tmp_path,
    monkeypatch,
    kind,
    frequencies,
    gamma,
    intensity,
):
    raw = json.dumps({"frequencies": frequencies, "intensities": [intensity, 0.0]}).encode()
    path = build_qvf(
        tmp_path / "spectrum.qvf",
        [
            ("sp", kind, {"spectrum": ("json", "spectrum.json", raw)}),
        ],
    )
    captured = []
    monkeypatch.setattr(
        "plotly.graph_objects.Figure.to_html",
        lambda self, **kwargs: captured.append(self) or "<div>plot</div>",
    )
    with QVFReader(path) as reader:
        terminal = spectra_plot(reader, "sp", 100, 26)
        SpectraRenderer(reader.get_section("sp"), reader).render_to_html()
        sticks = spectra_plot(reader, "sp", 100, 26, broaden=False)
    envelope = terminal.series[0]
    assert f"γ={gamma:.3g}" in envelope.label
    # Independently specified Lorentzian: height I at the centre and I/2
    # one native half-width away. No production formula in the expectation.
    x = np.array([frequencies[0], frequencies[0] + gamma])
    np.testing.assert_allclose(
        np.interp(x, envelope.x, envelope.y), [intensity, intensity / 2], atol=0.025, rtol=0
    )
    browser = next(t for t in captured[0].data if t.mode == "lines")
    np.testing.assert_array_equal(envelope.x, browser.x)
    np.testing.assert_array_equal(envelope.y, browser.y)
    np.testing.assert_array_equal(terminal.series[-1].x, frequencies)
    np.testing.assert_array_equal(terminal.series[-1].y, [intensity, 0.0])
    assert len(sticks.series) == 1
    assert sticks.series[0].style == "sticks"


def test_empty_spectrum_remains_a_valid_empty_plot(tmp_path):
    path = build_qvf(
        tmp_path / "empty.qvf",
        [
            (
                "sp",
                "spectra.ir",
                {"spectrum": ("json", "spectrum.json", b'{"frequencies": [], "intensities": []}')},
            ),
        ],
    )
    with QVFReader(path) as reader:
        plot = spectra_plot(reader, "sp", 80, 24)
    assert len(plot.series) == 1
    assert plot.series[0].x.size == 0
    assert plot.render().to_plain()
