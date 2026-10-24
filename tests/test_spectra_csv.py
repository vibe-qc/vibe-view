"""Spectrum downloads preserve the normalization shown in the browser."""

import io
import json
from types import SimpleNamespace

import numpy as np
import pytest

from tests.conftest import build_qvf
from vibeview.qvf import QVFReader
from vibeview.renderers.spectra import SpectraRenderer


def _csv_blocks(csv):
    envelope, peaks = csv.split("# envelope\n", 1)[1].split("# peaks\n")
    return tuple(
        np.loadtxt(io.StringIO(block), delimiter=",", skiprows=1)
        for block in (envelope, peaks)
    )


@pytest.mark.parametrize("normalize", [False, True])
@pytest.mark.parametrize(
    "kind,frequencies,intensities,normalized",
    [
        ("spectra.ir", [500, 1500, 3000], [20, 40, 0], [0.5, 1, 0]),
        ("spectra.ecd", [4, 5, 6], [20, -40, 0], [0.5, -1, 0]),
        ("spectra.vcd", [500, 1500, 3000], [2e-12, -4e-12, 0], [0.5, -1, 0]),
        ("spectra.ir", [500, 1500, 3000], [0, 0, 0], [0, 0, 0]),
    ],
    ids=["positive", "signed", "tiny-signed", "zero"],
)
def test_csv_matches_displayed_envelope_and_peak_strengths(
    tmp_path, monkeypatch, kind, frequencies, intensities, normalized, normalize
):
    raw = json.dumps({"frequencies": frequencies, "intensities": intensities}).encode()
    path = build_qvf(
        tmp_path / "spectrum.qvf",
        [("sp", kind, {"spectrum": ("json", "spectrum.json", raw)})],
    )
    figures = []
    monkeypatch.setattr(
        "plotly.graph_objects.Figure.to_html",
        lambda self, **kwargs: figures.append(self) or "<div>plot</div>",
    )
    options = {"gamma_scale": 2.0, "normalize": normalize, "x_unit": "nm"}
    with QVFReader(path) as reader:
        renderer = SpectraRenderer(reader.get_section("sp"), reader)
        renderer.render_to_html(**options)
        envelope, peaks = _csv_blocks(renderer.to_csv(**options))
        # Exporting a normalized view must not change the cached source data.
        np.testing.assert_array_equal(renderer.load().intensities, intensities)
        _, raw_peaks = _csv_blocks(renderer.to_csv())
        np.testing.assert_array_equal(raw_peaks[:, 1], intensities)

    expected = np.asarray(normalized if normalize else intensities)
    np.testing.assert_array_equal(peaks[:, 1], expected)
    displayed_envelope = figures[0].data[0]
    np.testing.assert_allclose(envelope[:, 0], displayed_envelope.x, rtol=5e-6)
    np.testing.assert_allclose(envelope[:, 1], displayed_envelope.y, rtol=5e-6, atol=0)
    sticks = next((trace for trace in figures[0].data if trace.name == "Sticks"), None)
    if np.any(expected):
        assert sticks is not None
        np.testing.assert_allclose(peaks[expected != 0, 0], sticks.x[1::3], rtol=5e-6)
        np.testing.assert_array_equal(peaks[expected != 0, 1], sticks.y[1::3])
    else:
        assert sticks is None
        assert np.isfinite(envelope).all()
        assert not envelope[:, 1].any()


def test_browser_csv_download_honors_normalize_control(tmp_path):
    from vibeview.app.display import DisplayControllers

    path = build_qvf(
        tmp_path / "spectrum.qvf",
        [
            (
                "sp",
                "spectra.ecd",
                {
                    "spectrum": (
                        "json",
                        "spectrum.json",
                        b'{"frequencies": [4, 5], "intensities": [20, -40]}',
                    )
                },
            )
        ],
    )
    state = SimpleNamespace(
        selected_section="sp",
        spectra_gamma_scale=3.0,
        spectra_normalize=True,
        spectra_x_unit="nm",
    )
    downloads = []

    def offer_download(payload, mime, filename, **kwargs):
        downloads.append((payload, mime, filename))
        state.export_filename = filename

    with QVFReader(path) as reader:
        viewer = SimpleNamespace(
            reader=reader,
            server=SimpleNamespace(state=state),
            exports=SimpleNamespace(_offer_download=offer_download),
        )
        DisplayControllers(viewer).export_spectra_csv()

    payload, mime, filename = downloads[0]
    assert mime == "text/csv"
    assert filename.endswith("-sp.csv")
    envelope, peaks = _csv_blocks(payload.decode("utf-8"))
    np.testing.assert_array_equal(peaks[:, 1], [0.5, -1.0])
    assert np.max(np.abs(envelope[:, 1])) == pytest.approx(1.0)
