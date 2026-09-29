"""Terminal DOS keeps the QVF energy grid referenced to Fermi exactly once."""

from __future__ import annotations

import numpy as np
import pytest

from tests.conftest import build_qvf
from vibeview.qvf import QVFReader
from vibeview.tui.plots import dos_plot


@pytest.mark.parametrize("fermi", [None, 0.0, -1.0, 20.0])
@pytest.mark.parametrize("projected", [False, True])
def test_dos_reference_and_channel_labels(tmp_path, fermi, projected):
    energies = np.array([-2., 0., 3.], dtype="<f8")
    values = np.array([[1., 2., 3.], [2., 3., 4.]], dtype="<f8")
    kind = "dos.projected" if projected else "dos.total"
    member = "projections" if projected else "dos"
    metadata = {"channels": [{"label": "Fe d"}, "O p"]} if projected else {}
    if fermi is not None:
        metadata["fermi_energy_ev"] = fermi
    path = build_qvf(tmp_path / "dos.qvf", [("dos", kind, {
        "energies": ("binary", "e.bin", energies.tobytes(), "float64", [3]),
        member: ("binary", "d.bin", values.tobytes(), "float64", [2, 3]),
    })], extra={"dos": metadata})
    with QVFReader(path) as reader:
        plot = dos_plot(reader, "dos", 80, 24)
        assert plot.xlabel == "E − E_F (eV)"
        assert [x for x, _, _ in plot.vlines] == [0.0]
        for index, series in enumerate(plot.series):
            np.testing.assert_array_equal(series.x, [-2., 0., 3.])
            np.testing.assert_array_equal(series.y, values[index])
        if projected:
            assert [s.label for s in plot.series] == ["Fe d", "O p"]
        rendered = plot.render().to_plain()
        assert "E − E_F (eV)" in rendered
        if projected:
            assert "Fe d" in rendered and "O p" in rendered
            assert "{'label'" not in rendered


@pytest.mark.parametrize(
    ("channels", "expected"),
    [([], ["ch0", "ch1"]), ([{}], ["ch0", "ch1"]), ([{"label": "Fe d"}], ["Fe d", "ch1"])],
)
def test_spin_projected_dos_sums_spins_and_fills_missing_labels(tmp_path, channels, expected):
    from vibeview.renderers.dos import DOSRenderer

    energies = np.array([-1., 0., 1.], dtype="<f8")
    # Spin axis first; preserve the channel axis rather than summing channels.
    values = np.array([[[1., 2., 3.], [4., 5., 6.]],
                       [[10., 20., 30.], [40., 50., 60.]]], dtype="<f8")
    path = build_qvf(tmp_path / "spin.qvf", [("dos", "dos.projected", {
        "energies": ("binary", "e.bin", energies.tobytes(), "float64", [3]),
        "projections": ("binary", "d.bin", values.tobytes(), "float64", [2, 2, 3]),
    })], extra={"dos": {"channels": channels}})
    with QVFReader(path) as reader:
        plot = dos_plot(reader, "dos", 80, 24)
        assert [s.label for s in plot.series] == expected
        np.testing.assert_array_equal(plot.series[0].y, [11., 22., 33.])
        np.testing.assert_array_equal(plot.series[1].y, [44., 55., 66.])
        renderer = DOSRenderer(reader.get_section("dos"), reader)
        assert renderer.projected_channel_labels() == expected
        html = renderer.render_to_html(include_plotlyjs=False)
        assert all(label in html for label in expected)
