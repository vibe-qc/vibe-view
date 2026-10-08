"""EOS fit traces use fitted parameters, independently of sampled energies."""

import json

import numpy as np
import pytest

from tests.conftest import build_qvf
from vibeview.qvf import QVFReader
from vibeview.renderers.eos import EquationOfStateRenderer
from vibeview.tui.plots import eos_plot


def _archive(tmp_path, fit):
    volumes = np.array([4.0, 8.0, 16.0], dtype='<f8')
    energies = np.array([40.0, 80.0, 160.0], dtype='<f8')
    return build_qvf(tmp_path / 'eos.qvf', [
        ('eos', 'equation_of_state', {
            'volumes': ('binary', 'v.bin', volumes.tobytes(), 'float64', [3]),
            'energies': ('binary', 'e.bin', energies.tobytes(), 'float64', [3]),
            'fit': ('json', 'fit.json', json.dumps(fit).encode()),
        }),
    ])


@pytest.mark.parametrize('model,label', [
    ('birch_murnaghan', 'Birch-Murnaghan fit'), ('murnaghan', 'Murnaghan fit'),
])
def test_terminal_uses_same_fitted_curve_as_browser(tmp_path, monkeypatch, model, label):
    fit = {'model': model, 'V0': 8, 'E0': -3, 'B0': 160.2176634, 'B0_prime': 4}
    path = _archive(tmp_path, fit)
    captured = []
    monkeypatch.setattr('plotly.graph_objects.Figure.to_html',
                        lambda self, **kwargs: captured.append(self) or '<div>plot</div>')
    with QVFReader(path) as reader:
        terminal = eos_plot(reader, 'eos', 100, 26)
        EquationOfStateRenderer(reader.get_section('eos'), reader).render_to_html()
    curve = next(s for s in terminal.series if s.style == 'line')
    assert curve.label == label
    assert len(curve.x) == 200
    assert np.max(curve.y) < 10  # Deliberately unrelated raw energies must not become the fit.
    browser = next(t for t in captured[0].data if t.name == label)
    np.testing.assert_array_equal(curve.x, browser.x)
    np.testing.assert_array_equal(curve.y, browser.y)
    points = next(s for s in terminal.series if s.style == 'points')
    np.testing.assert_array_equal(points.x, [4, 8, 16])
    np.testing.assert_array_equal(points.y, [40, 80, 160])
    assert terminal.vlines[0][0] == 8


@pytest.mark.parametrize('fit', [{}, {'V0': 'bad'}, {'V0': 8, 'B0': 75},
                                   {'V0': 8, 'E0': -3, 'B0': 75, 'B0_prime': 1}])
def test_missing_or_unusable_fit_keeps_computed_points(tmp_path, fit):
    with QVFReader(_archive(tmp_path, fit)) as reader:
        plot = eos_plot(reader, 'eos', 100, 26)
    assert len(plot.series) == 1
    assert plot.series[0].style == 'points'
    np.testing.assert_array_equal(plot.series[0].y, [40, 80, 160])
    assert not plot.vlines


def test_energy_formulas_at_independently_calculated_volumes():
    from vibeview.renderers.eos import _birch_murnaghan_energy, _murnaghan_energy

    # B0=160.2176634 GPa is exactly 1 eV/Å³. These volumes make the
    # powers rational; expected values are hand calculated, not sampled
    # through the helper used by either viewer.
    np.testing.assert_allclose(
        _birch_murnaghan_energy(np.array([1., 8., 27.]), 8, -3, 160.2176634, 4),
        [78, -3, -2 / 9], atol=1e-12,
    )
    np.testing.assert_allclose(
        _murnaghan_energy(np.array([4., 8., 16.]), 8, -3, 160.2176634, 4),
        [2 / 3, -3, -19 / 12], atol=1e-12,
    )
