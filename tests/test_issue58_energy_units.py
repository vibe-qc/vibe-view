"""Display unit for orbital energies (#58, slice 1).

The archive stores Hartree; the setting changes only what the browser shows.
The ``tables_qvf`` wavefunction has one orbital at -0.5 Eh, which reads
-13.61 eV and -313.8 kcal/mol.
"""

from __future__ import annotations

import pytest

from vibeview import app as appmod
from vibeview.qvf import QVFReader
from vibeview.renderers.wavefunction import WavefunctionRenderer
from vibeview.settings import load_settings, save_settings
from vibeview.units import energy_unit, format_energy


@pytest.fixture(autouse=True)
def _own_settings_file(monkeypatch, tmp_path):
    """Each test starts from the defaults; a saved unit must not leak."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _plotly_text(html: str) -> str:
    """Plotly's embedded JSON writes ``/`` as ``\\u002f``."""
    return html.replace("\\u002f", "/")


@pytest.mark.parametrize(
    ("unit", "text"),
    [("hartree", "-0.500 Eh"), ("ev", "-13.61 eV"), ("kcal_mol", "-313.8 kcal/mol")],
)
def test_format_energy_converts_from_hartree(unit, text):
    assert format_energy(-0.5, unit) == text


@pytest.mark.parametrize("bad", [None, "", "rydberg", 3, ["ev"]])
def test_unknown_unit_falls_back_to_hartree(bad):
    assert energy_unit(bad) == "hartree"
    assert format_energy(-0.5, bad) == "-0.500 Eh"


def test_mo_table_titles_follow_the_unit_and_keep_hartree_data(tables_qvf):
    with QVFReader(tables_qvf) as reader:
        renderer = WavefunctionRenderer(reader.get_section("wf"), reader)
        (default,) = renderer.mo_table()
        (ev,) = renderer.mo_table(energy_unit="ev")
    assert "-0.500 Eh" in default["title"]
    assert "-13.61 eV" in ev["title"]
    assert "Eh" not in ev["title"]
    assert ev["energy_eh"] == default["energy_eh"] == -0.5
    assert ev["value"] == default["value"]


def test_energy_diagram_axis_and_hover_use_the_unit(tables_qvf):
    with QVFReader(tables_qvf) as reader:
        renderer = WavefunctionRenderer(reader.get_section("wf"), reader)
        html = _plotly_text(
            renderer.render_energy_diagram(include_plotlyjs=False, energy_unit="kcal_mol")
        )
    assert "Energy (kcal/mol)" in html
    assert "-313.75 kcal/mol" in html
    assert "Energy (Eh)" not in html


@pytest.fixture
def make_app(tables_qvf):
    opened = []

    def build():
        reader = QVFReader(tables_qvf)
        app = appmod.create_app(reader)
        app.controller.view_update = lambda *args, **kwargs: None
        opened.append(reader)
        return app

    yield build
    for reader in opened:
        reader.close()


def test_saved_unit_seeds_the_picker(make_app):
    save_settings({**load_settings(), "energy_unit": "ev"})
    app = make_app()
    assert app.state.energy_unit == "ev"
    assert app.state.settings_energy_unit == "ev"
    app.controller.activate_section("wf")
    (row,) = app.state.wf_mo_rows
    assert "-13.61 eV" in row["title"]


def test_saving_a_unit_relabels_the_open_picker_and_diagram(make_app):
    app = make_app()
    app.controller.activate_section("wf")
    app.controller.show_energy_diagram()
    selected = app.state.wf_selected_mo
    assert "Energy (Eh)" in app.state.chart_html

    app.state.settings_energy_unit = "kcal_mol"
    app.controller.save_settings()

    assert load_settings()["energy_unit"] == "kcal_mol"
    assert app.state.energy_unit == "kcal_mol"
    (row,) = app.state.wf_mo_rows
    assert "-313.8 kcal/mol" in row["title"]
    assert app.state.wf_selected_mo == selected
    assert "Energy (kcal/mol)" in _plotly_text(app.state.chart_html)
    assert app.state.status_message == "Settings saved"


def test_a_malformed_stored_unit_starts_in_hartree(make_app):
    save_settings({**load_settings(), "energy_unit": "rydberg"})
    app = make_app()
    assert app.state.energy_unit == "hartree"
    app.controller.activate_section("wf")
    (row,) = app.state.wf_mo_rows
    assert "-0.500 Eh" in row["title"]
