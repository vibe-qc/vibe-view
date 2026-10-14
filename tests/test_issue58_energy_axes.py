"""Display unit for band-structure and DOS energy axes (#58, energy-axes slice).

Bands and DOS are stored in eV (QVF §4.3). Their axes follow a setting of
their own, ``band_energy_unit``, eV by default, so default charts are
unchanged; orbital energies keep ``energy_unit`` and its Hartree default.
Energy windows stay in eV internally and are converted at the axis and at
the panel's window fields.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

from tests.test_energy_window import (
    _bands_section,
    _dos_section,
    _make_qvf,
    _panel_state,
    _ranges,
    _silicon_bands,
    _silicon_pdos,
)
from vibeview.qvf import QVFReader
from vibeview.settings import load_settings, save_settings
from vibeview.units import (
    HARTREE_TO_EV,
    HARTREE_TO_KCAL_MOL,
    band_energy_unit,
    energy_from_ev,
    energy_to_ev,
)


@pytest.fixture(autouse=True)
def _own_settings_file(monkeypatch, tmp_path):
    """Each test starts from the defaults; a saved unit must not leak."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _approx_ranges(html: str, expected: list[list[float]]) -> None:
    got = _ranges(html)
    assert len(got) == len(expected), got
    for g, e in zip(got, expected, strict=True):
        assert g == pytest.approx(e)


def _axis_titles(html: str) -> list[str]:
    text = html.replace("\\u2212", "−").replace("\\u002f", "/")
    return re.findall(r'"title":\{"text":"([^"]*)"', text)


def _without_ids(html: str) -> str:
    """Plotly gives every figure a fresh div id."""
    return re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "ID", html)


# ── the helpers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("unit", "value"),
    [("ev", 27.211386245988), ("hartree", 1.0), ("kcal_mol", HARTREE_TO_KCAL_MOL)],
)
def test_energy_from_ev_and_back(unit, value):
    assert energy_from_ev(HARTREE_TO_EV, unit) == pytest.approx(value)
    assert energy_to_ev(value, unit) == pytest.approx(HARTREE_TO_EV)


@pytest.mark.parametrize("bad", [None, "", "rydberg", 3])
def test_unknown_band_unit_falls_back_to_ev(bad):
    assert band_energy_unit(bad) == "ev"


# ── the renderers ───────────────────────────────────────────────────────────


@pytest.fixture
def bands_and_dos():
    b_section, b_files = _bands_section(_silicon_bands(), fermi=0.0001)
    e, dos = _silicon_pdos()
    d_section, d_files = _dos_section(e, dos)
    path = _make_qvf([b_section, d_section], {**b_files, **d_files})
    reader = QVFReader(path)
    yield reader
    reader.close()
    path.unlink()


def test_bands_axis_and_window_follow_the_unit(bands_and_dos):
    from vibeview.renderers.bands import BandsRenderer

    renderer = BandsRenderer(bands_and_dos.get_section("bands0"), bands_and_dos)
    window = (-10.0, 5.0)
    default = renderer.render_to_html(include_plotlyjs=False, energy_window=window)
    assert "E − E_F (eV)" in _axis_titles(default)
    assert _ranges(default) == [[-10.0, 5.0]]
    explicit = renderer.render_to_html(
        include_plotlyjs=False, energy_window=window, energy_unit="ev"
    )
    assert _without_ids(explicit) == _without_ids(default)

    hartree = renderer.render_to_html(
        include_plotlyjs=False, energy_window=window, energy_unit="hartree"
    )
    assert "E − E_F (Eh)" in _axis_titles(hartree)
    _approx_ranges(hartree, [[-10.0 / HARTREE_TO_EV, 5.0 / HARTREE_TO_EV]])


def test_dos_energies_and_density_convert_together(bands_and_dos, monkeypatch):
    """The DOS is per unit energy: in Eh its values grow by 27.2 eV/Eh, so
    the number of states under the curve does not change."""
    from vibeview.renderers import dos as dos_mod

    renderer = dos_mod.DOSRenderer(bands_and_dos.get_section("dos_total"), bands_and_dos)
    e, dos = _silicon_pdos()
    html = renderer.render_to_html(
        include_plotlyjs=False, energy_window=(-10.0, 5.0), energy_unit="hartree"
    )
    titles = _axis_titles(html)
    assert "E − E_F (Eh)" in titles
    assert "DOS (states / Eh)" in titles
    assert "E: %{x:.3f} Eh" in html
    _approx_ranges(html, [[-10.0 / HARTREE_TO_EV, 5.0 / HARTREE_TO_EV]])

    figures = []
    monkeypatch.setattr(dos_mod, "figure_to_html", lambda fig, **_kw: figures.append(fig) or "")
    renderer.render_to_html(include_plotlyjs=False, energy_unit="hartree")
    trace = figures[0].data[0]
    x, y = np.asarray(trace.x, dtype=float), np.asarray(trace.y, dtype=float)
    assert x == pytest.approx(e / HARTREE_TO_EV)
    assert y == pytest.approx(dos * HARTREE_TO_EV)
    assert np.trapezoid(y, x) == pytest.approx(np.trapezoid(dos, e))


def test_combined_panel_shares_one_converted_axis(bands_and_dos):
    from vibeview.renderers.bands import BandsRenderer
    from vibeview.renderers.dos import DOSRenderer, render_bands_dos_combined

    bands = BandsRenderer(bands_and_dos.get_section("bands0"), bands_and_dos)
    dos_r = DOSRenderer(bands_and_dos.get_section("dos_total"), bands_and_dos)
    html = render_bands_dos_combined(
        bands,
        dos_r,
        include_plotlyjs=False,
        energy_window=(-10.0, 5.0),
        energy_unit="kcal_mol",
    )
    titles = _axis_titles(html)
    assert "E − E_F (kcal/mol)" in titles
    assert "DOS (states/kcal/mol)" in titles
    scale = HARTREE_TO_KCAL_MOL / HARTREE_TO_EV
    _approx_ranges(html, [[-10.0 * scale, 5.0 * scale]] * 2)


# ── the browser ─────────────────────────────────────────────────────────────


def test_panel_fields_show_the_window_in_the_axis_unit():
    """A producer's window hint is in eV; the fields show it in the axis unit."""
    from vibeview.viewer_defaults import ViewerState

    b_section, b_files = _bands_section(_silicon_bands(), fermi=0.0001)
    e, dos = _silicon_pdos()
    d_section, d_files = _dos_section(e, dos)
    path = _make_qvf(
        [b_section, d_section],
        {**b_files, **d_files},
        viewer_defaults={"bands0": {"energy_window": [-12.0, 8.0]}},
    )
    reader = QVFReader(path)
    try:
        _check_panel_fields(reader, ViewerState.from_manifest(reader.viewer_defaults))
    finally:
        reader.close()
        path.unlink()


def _check_panel_fields(bands_and_dos, viewer_state) -> None:
    from vibeview.app import _activate_bands

    state = _panel_state(band_energy_unit="hartree")
    _activate_bands(bands_and_dos, state, bands_and_dos.get_section("bands0"), viewer_state)
    assert state.bands_emin == pytest.approx(-12.0 / HARTREE_TO_EV)
    assert state.bands_emax == pytest.approx(8.0 / HARTREE_TO_EV)
    _approx_ranges(state.bands_html, [[state.bands_emin, state.bands_emax]] * 2)

    # A window typed in Hartree is read in Hartree.
    state.bands_window_auto = False
    state.bands_emin, state.bands_emax = -0.5, 0.25
    _activate_bands(bands_and_dos, state, bands_and_dos.get_section("bands0"), viewer_state)
    _approx_ranges(state.bands_html, [[-0.5, 0.25]] * 2)
    assert (state.bands_emin, state.bands_emax) == pytest.approx((-0.5, 0.25))


@pytest.fixture
def make_app(bands_and_dos):
    from vibeview import app as appmod

    def build():
        app = appmod.create_app(bands_and_dos)
        app.controller.view_update = lambda *args, **kwargs: None
        return app

    return build


def test_defaults_are_unchanged(make_app):
    app = make_app()
    assert app.state.band_energy_unit == "ev"
    assert app.state.settings_band_energy_unit == "ev"
    assert app.state.band_energy_label == "eV"
    # The orbital-energy setting is separate and keeps its own default.
    assert app.state.energy_unit == "hartree"


def test_saved_unit_seeds_the_panel(make_app):
    save_settings({**load_settings(), "band_energy_unit": "hartree"})
    app = make_app()
    assert app.state.band_energy_unit == "hartree"
    assert app.state.band_energy_label == "Eh"
    app.controller.activate_section("bands0")
    assert "E − E_F (Eh)" in _axis_titles(app.state.bands_html)


def test_malformed_saved_unit_starts_in_ev(make_app):
    save_settings({**load_settings(), "band_energy_unit": "rydberg"})
    assert make_app().state.band_energy_unit == "ev"


def test_saving_a_unit_redraws_the_panel_and_keeps_a_typed_window(make_app):
    app = make_app()
    state, ctrl = app.state, app.controller
    ctrl.activate_section("bands0")
    state.bands_emin, state.bands_emax = "-8", "6"
    ctrl.update_bands_energy_window()
    assert _ranges(state.bands_html) == [[-8.0, 6.0]] * 2

    state.settings_band_energy_unit = "hartree"
    ctrl.save_settings()

    assert load_settings()["band_energy_unit"] == "hartree"
    assert load_settings()["energy_unit"] == "hartree"  # untouched
    assert state.band_energy_label == "Eh"
    assert state.bands_window_auto is False
    lo, hi = -8.0 / HARTREE_TO_EV, 6.0 / HARTREE_TO_EV
    assert (state.bands_emin, state.bands_emax) == pytest.approx((lo, hi))
    _approx_ranges(state.bands_html, [[lo, hi]] * 2)
    assert "E − E_F (Eh)" in _axis_titles(state.bands_html)
    assert state.status_message == "Settings saved"
