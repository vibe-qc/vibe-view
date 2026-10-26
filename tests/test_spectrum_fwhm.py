"""Physical line kernels and identical CLI/browser exports (#121)."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import numpy as np
import pytest
from click.testing import CliRunner

from tests.conftest import build_qvf
from vibeview.cli import main
from vibeview.qvf import QVFReader
from vibeview.spectrum import broaden_lines


@pytest.mark.parametrize('shape', ['gaussian', 'lorentzian'])
def test_normalized_kernel_peak_half_width_and_area(shape):
    width, strength = 2.0, -3.0
    y = broaden_lines(np.array([0.0]), np.array([strength]), np.array([0., 1., -1.]),
                      fwhm=width, lineshape=shape)
    peak = strength * (np.sqrt(4 * np.log(2) / np.pi) / width
                       if shape == 'gaussian' else 2 / (np.pi * width))
    np.testing.assert_allclose(y, [peak, peak / 2, peak / 2], rtol=1e-14)
    # Cauchy tails are infinite; compare the finite-window integral analytically.
    grid = np.linspace(-100., 100., 200001)
    y = broaden_lines(np.array([0.0]), np.array([strength]), grid,
                      fwhm=width, lineshape=shape)
    expected = strength if shape == 'gaussian' else strength * 2 / np.pi * np.arctan(100.)
    assert np.trapezoid(y, grid) == pytest.approx(expected, abs=1e-10)


@pytest.fixture
def spectrum(tmp_path):
    raw = json.dumps({'frequencies': [1000., 1500.], 'intensities': [3., -2.]}).encode()
    return build_qvf(tmp_path / 'spectrum.qvf',
                     [('sp', 'spectra.vcd', {'spectrum': ('json', 's.json', raw)}),
                      ('sp2', 'spectra.raman', {'spectrum': ('json', 'r.json', raw)})])


@pytest.mark.parametrize('shape', ['gaussian', 'lorentzian'])
@pytest.mark.parametrize('normalize', [False, True])
def test_browser_export_equals_cli_and_plot(spectrum, tmp_path, monkeypatch, shape, normalize):
    from vibeview.app.display import DisplayControllers
    from vibeview.renderers.spectra import SpectraRenderer

    output = tmp_path / 'curve.dat'
    args = ['spectrum', str(spectrum), '--section', 'sp', '-o', str(output),
            '--lineshape', shape, '--fwhm', '20', '--unit', 'cm-1',
            '--grid-min', '800', '--grid-max', '1800', '--grid-step', '1']
    if normalize:
        args += ['--normalize']
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    state = SimpleNamespace(selected_section='sp', spectra_lineshape=shape,
                            spectra_fwhm=20., spectra_width_unit='cm⁻¹',
                            spectra_normalize=normalize, spectra_x_unit='native',
                            spectra_grid_min=800., spectra_grid_max=1800., spectra_grid_step=1.)
    downloads, figures = [], []

    def offer(data, *args, **kwargs):
        downloads.append(data)
        state.export_filename = 'curve.txt'

    monkeypatch.setattr('plotly.graph_objects.Figure.to_html',
                        lambda self, **kwargs: figures.append(self) or '<div/>')
    with QVFReader(spectrum) as reader:
        viewer = SimpleNamespace(reader=reader, server=SimpleNamespace(state=state),
                                 exports=SimpleNamespace(_offer_download=offer))
        DisplayControllers(viewer).export_spectra_ascii()
        assert downloads[0] == output.read_bytes()
        renderer = SpectraRenderer(reader.get_section('sp'), reader)
        renderer.render_to_html(lineshape=shape, fwhm=20, unit='cm-1', normalize=normalize,
                                grid_min=800, grid_max=1800, grid_step=1)
        rows = output.read_text().split('# envelope: position intensity\n')[1].split('# sticks:')[0]
        from io import StringIO
        sampled = np.loadtxt(StringIO(rows))
        np.testing.assert_allclose(sampled[:, 0], figures[0].data[0].x, rtol=1e-11)
        np.testing.assert_allclose(sampled[:, 1], figures[0].data[0].y, rtol=1e-11)
        np.testing.assert_array_equal(renderer.load().intensities, [3., -2.])
        assert ('# sticks: position stored_strength (not normalized)\n1000 3\n1500 -2'
                in output.read_text())


def test_width_conversion_and_wavelength_mapping(spectrum, monkeypatch):
    from vibeview.renderers.spectra import SpectraRenderer
    from vibeview.units import EV_NM, EV_TO_CM1

    figures = []
    monkeypatch.setattr('plotly.graph_objects.Figure.to_html',
                        lambda self, **kwargs: figures.append(self) or '<div/>')
    with QVFReader(spectrum) as reader:
        renderer = SpectraRenderer(reader.get_section('sp'), reader)
        renderer.render_to_html(lineshape='gaussian', fwhm=20, unit='cm-1')
        renderer.render_to_html(lineshape='gaussian', fwhm=20 / EV_TO_CM1, unit='eV', x_unit='nm')
    np.testing.assert_allclose(figures[0].data[0].y, figures[1].data[0].y)
    np.testing.assert_allclose(figures[1].data[0].x,
                               EV_NM * EV_TO_CM1 / np.asarray(figures[0].data[0].x))


def test_terminal_dialog_applies_per_section(spectrum):
    from textual.widgets import Input, Select

    from vibeview.tui.app import VibeViewTUI

    async def drive():
        app = VibeViewTUI(spectrum)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.press('B')
            await pilot.pause()
            app.screen.query_one('#shape', Select).value = 'gaussian'
            app.screen.query_one('#width', Input).value = '24'
            await pilot.click('#apply')
            await pilot.pause()
            assert app.spectrum_options['sp']['fwhm'] == 24.
            assert app.spectrum_options['sp']['lineshape'] == 'gaussian'
            assert app._build_grid(80, 24) is not None
            assert 'sp2' not in app.spectrum_options
    asyncio.run(drive())


def test_bad_width_and_grid_refuse_export(spectrum, tmp_path):
    output = tmp_path / 'bad.txt'
    for tail in (['--fwhm', 'nan'], ['--grid-step', '1e-30']):
        result = CliRunner().invoke(main, ['spectrum', str(spectrum), '--section', 'sp',
                                          '-o', str(output), *tail])
        assert result.exit_code != 0
        assert not output.exists()


def test_orca_hessian_headless_export(tmp_path):
    from tests.test_orca_hessian import _hessian
    from vibeview.converters import convert_to_qvf

    source = tmp_path / "sample.hess"
    source.write_bytes(_hessian())
    with QVFReader(convert_to_qvf(source)) as reader:
        section = next(s for s in reader.sections if s.kind.startswith("spectra."))
    output = tmp_path / "ir.txt"
    result = CliRunner().invoke(main, ["spectrum", str(source), "--section", section.id,
                                     "-o", str(output), "--lineshape", "gaussian",
                                     "--fwhm", "10", "--unit", "cm-1"])
    assert result.exit_code == 0, result.output
    assert "gaussian" in output.read_text()
    assert "1000.126 15" in output.read_text()


def test_cli_wrong_section_names_the_spectrum_sections(tmp_path):
    """A missing or non-spectrum --section lists the file's spectra (#147)."""
    from pathlib import Path

    water = Path(__file__).resolve().parents[1] / "docs/examples/orca-water/frequencies.hess"
    output = tmp_path / "out.dat"
    missing = CliRunner().invoke(main, ["spectrum", str(water), "--section", "orca_raman",
                                        "-o", str(output)])
    assert missing.exit_code == 1
    assert ("no section 'orca_raman' in this file; spectrum sections: orca_ir (spectra.ir)"
            in missing.output)
    wrong = CliRunner().invoke(main, ["spectrum", str(water), "--section", "structure",
                                      "-o", str(output)])
    assert wrong.exit_code == 1
    assert "section 'structure' is a structure, not a spectrum; spectrum sections:" in wrong.output
    assert not output.exists()

    xyz = tmp_path / "water.xyz"
    xyz.write_text("1\n\nO 0 0 0\n")
    none = CliRunner().invoke(main, ["spectrum", str(xyz), "--section", "structure",
                                     "-o", str(output)])
    assert "this file has no spectrum section" in none.output
