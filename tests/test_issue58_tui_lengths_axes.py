"""Terminal mode reads the length and band/DOS energy units (#58).

The browser's ``measure_unit`` (Å / bohr) and ``band_energy_unit`` (eV / Eh /
kcal/mol) settings now also drive the terminal: the geometry and bond-order
tables, and the bands and DOS charts, in both ``vibe-view tui`` and
``vibe-view show``. Archives stay in Å and eV; the defaults are unchanged.
"""

from __future__ import annotations

import re

import numpy as np
import pytest
from click.testing import CliRunner

from tests.test_energy_window import (
    _bands_section,
    _dos_section,
    _make_qvf,
    _silicon_bands,
    _silicon_pdos,
)
from vibeview.qvf import QVFReader
from vibeview.settings import load_settings, save_settings
from vibeview.tui import panes, plots
from vibeview.units import ANGSTROM_TO_BOHR, HARTREE_TO_EV

_OH_ANG = (0.7572**2 + (0.1173 + 0.4692) ** 2) ** 0.5


@pytest.fixture(autouse=True)
def _own_settings_file(monkeypatch, tmp_path):
    """Each test starts from the defaults; a saved unit must not leak."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _plain(text: str) -> str:
    return re.sub(r"\[/?[^]]*\]", "", text)


@pytest.fixture
def bands_and_dos():
    b_section, b_files = _bands_section(_silicon_bands(), fermi=0.0001)
    e, dos = _silicon_pdos()
    d_section, d_files = _dos_section(e, dos)
    path = _make_qvf([b_section, d_section], {**b_files, **d_files})
    yield path
    path.unlink()


# ── the geometry tables ─────────────────────────────────────────────────────


def test_structure_table_defaults_to_angstrom(sample_qvf):
    with QVFReader(sample_qvf) as reader:
        default = panes.structure_table(reader)
        explicit = panes.structure_table(reader, length_unit="angstrom")
    assert default == explicit
    assert "x (Å)" in _plain(default)


def test_structure_table_in_bohr(sample_qvf):
    with QVFReader(sample_qvf) as reader:
        text = _plain(panes.structure_table(reader, length_unit="bohr"))
    assert "x (bohr)" in text
    assert "Å" not in text
    # H2 sits at y = 0.7572 Å.
    assert f"{0.7572 * ANGSTROM_TO_BOHR:10.5f}" in text


@pytest.mark.parametrize(
    ("unit", "label", "factor"), [("angstrom", "Å", 1.0), ("bohr", "bohr", ANGSTROM_TO_BOHR)]
)
def test_bond_order_distances_follow_the_unit(unit, label, factor):
    class _Data:
        method = "mayer"
        pairs = [{"i": 1, "j": 2, "order": 0.95, "distance_ang": _OH_ANG}]

    class _Reader:
        def read_bond_orders(self, _sid):
            return _Data()

    text = _plain(panes.bond_orders(_Reader(), "bo", length_unit=unit))
    assert f"dist ({label})" in text
    assert f"{_OH_ANG * factor:.4f}" in text


def test_the_cell_readout_follows_the_unit(tmp_path):
    import json

    from tests._qvf_builders import build_qvf

    payload = json.dumps(
        {
            "atoms": [{"symbol": "Si", "atomic_number": 14, "position": [0, 0, 0]}],
            "pbc": [True, True, True],
            "lattice_vectors": [[3.0, 0, 0], [0, 3.0, 0], [0, 0, 3.0]],
        }
    ).encode()
    section = ("structure", "structure", {"structure": ("json", "s.json", payload)})
    path = build_qvf(tmp_path / "c.qvf", [section])
    with QVFReader(path) as reader:
        text = _plain(panes.structure_table(reader, length_unit="bohr"))
    assert f"|a| = {3.0 * ANGSTROM_TO_BOHR:8.4f} bohr" in text


# ── the charts ──────────────────────────────────────────────────────────────


def test_bands_plot_default_is_unchanged_and_hartree_converts(bands_and_dos):
    with QVFReader(bands_and_dos) as reader:
        ev = plots.bands_plot(reader, "bands0", 80, 20)
        eh = plots.bands_plot(reader, "bands0", 80, 20, energy_unit="hartree")
    assert ev.ylabel == "E − E_F (eV)"
    assert eh.ylabel == "E − E_F (Eh)"
    assert eh.series[0].y == pytest.approx(ev.series[0].y / HARTREE_TO_EV)


def test_dos_plot_rescales_energy_and_density_together(bands_and_dos):
    with QVFReader(bands_and_dos) as reader:
        ev = plots.dos_plot(reader, "dos_total", 80, 20)
        eh = plots.dos_plot(reader, "dos_total", 80, 20, energy_unit="hartree")
    assert (ev.xlabel, ev.ylabel) == ("E − E_F (eV)", "DOS")
    assert (eh.xlabel, eh.ylabel) == ("E − E_F (Eh)", "DOS (/Eh)")
    x_ev, y_ev = ev.series[0].x, ev.series[0].y
    x_eh, y_eh = eh.series[0].x, eh.series[0].y
    assert x_eh == pytest.approx(x_ev / HARTREE_TO_EV)
    assert np.trapezoid(y_eh, x_eh) == pytest.approx(np.trapezoid(y_ev, x_ev))


def test_render_chart_hands_the_unit_to_the_energy_axis_builders(bands_and_dos, monkeypatch):
    from vibeview.tui.show import render_chart

    seen: list[tuple[str, str]] = []

    def spy(name, real):
        def wrapped(reader, sid, cols, rows, unit):
            seen.append((name, unit))
            return real(reader, sid, cols, rows, unit)

        return wrapped

    monkeypatch.setitem(plots.ENERGY_AXIS_BUILDERS, "bands", spy("bands", plots.bands_plot))
    monkeypatch.setitem(plots.ENERGY_AXIS_BUILDERS, "dos.total", spy("dos", plots.dos_plot))
    with QVFReader(bands_and_dos) as reader:
        render_chart(reader, "bands0", 80, 20, band_energy_unit="kcal_mol")
        render_chart(reader, "dos_total", 80, 20)
    assert seen == [("bands", "kcal_mol"), ("dos", "ev")]


# ── the settings reach both terminal commands ───────────────────────────────


def test_the_tui_reads_both_settings(sample_qvf):
    pytest.importorskip("textual")
    from vibeview.tui.app import VibeViewTUI

    default = VibeViewTUI(sample_qvf)
    assert (default.length_unit, default.band_energy_unit) == ("angstrom", "ev")

    save_settings({**load_settings(), "measure_unit": "bohr", "band_energy_unit": "hartree"})
    app = VibeViewTUI(sample_qvf)
    assert (app.length_unit, app.band_energy_unit) == ("bohr", "hartree")
    assert app.current[1] == "structure"
    assert "x (bohr)" in _plain(app._text_for_current())


def test_malformed_saved_units_fall_back(sample_qvf):
    pytest.importorskip("textual")
    from vibeview.tui.app import VibeViewTUI

    save_settings({**load_settings(), "measure_unit": "furlong", "band_energy_unit": "rydberg"})
    app = VibeViewTUI(sample_qvf)
    assert (app.length_unit, app.band_energy_unit) == ("angstrom", "ev")


def test_show_passes_the_saved_band_unit(bands_and_dos, monkeypatch):
    from vibeview.cli import main
    from vibeview.tui import show as show_mod

    seen = {}

    def fake_show(path, section, **kwargs):
        seen.update(kwargs)
        return "chart"

    monkeypatch.setattr(show_mod, "show", fake_show)
    save_settings({**load_settings(), "band_energy_unit": "hartree"})
    result = CliRunner().invoke(main, ["show", str(bands_and_dos), "-s", "bands0"])
    assert result.exit_code == 0, result.output
    assert seen["band_energy_unit"] == "hartree"
