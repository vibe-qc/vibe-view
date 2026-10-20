"""The display units of #58 reach COOP/COHP, static captures, symmetrize and compare.

The roadmap's last unit-coverage item. The Band / DOS energy unit now drives
COOP and COHP charts in the browser and the terminal and the band-structure
PNG that ``vibe-view capture`` writes; the Length unit reads out the compare
legend's RMSD, the Bond Orders panel, the shift Symmetrize reports, and the
RMSD lines of ``vibe-view diff`` and ``vibe-view batch-compare``. Archives,
JSON output and CSV downloads keep their stored units.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import types
from pathlib import Path

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
from tests.test_issue45_renderers import _coop_reader
from tests.test_issue58_energy_axes import _axis_titles
from vibeview import app as appmod
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder
from vibeview.settings import load_settings, save_settings
from vibeview.units import ANGSTROM_TO_BOHR, HARTREE_TO_EV

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

_OH_ANG = (0.7572**2 + (0.1173 + 0.4692) ** 2) ** 0.5
_WATER = [
    {"symbol": "O", "position": [0.0, 0.0, 0.1173], "atomic_number": 8},
    {"symbol": "H", "position": [0.0, 0.7572, -0.4692], "atomic_number": 1},
    {"symbol": "H", "position": [0.0, -0.7572, -0.4692], "atomic_number": 1},
]


@pytest.fixture(autouse=True)
def _own_settings_file(monkeypatch, tmp_path):
    """Each test starts from the defaults; a saved unit must not leak."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _builder(atoms=None) -> QVFArchiveBuilder:
    b = QVFArchiveBuilder(calculation="units")
    b.add_json(
        "structure", "structure", "structure",
        {"atoms": atoms or _WATER, "pbc": [False, False, False]},
    )
    return b


def _app(reader):
    app = appmod.create_app(reader)
    app.controller.view_update = lambda *args, **kwargs: None
    return app


@pytest.fixture
def make_app(sample_qvf):
    opened = []

    def build():
        reader = QVFReader(sample_qvf)
        opened.append(reader)
        return _app(reader)

    yield build
    for reader in opened:
        reader.close()


# ── COOP / COHP ─────────────────────────────────────────────────────────────


def test_coop_chart_axis_follows_the_unit(tmp_path, monkeypatch):
    from vibeview.renderers import coop as coop_mod
    from vibeview.renderers.coop import COOPRenderer

    figures = []

    def grab(fig, **kwargs):
        figures.append(fig)
        return "<div/>"

    monkeypatch.setattr(coop_mod, "figure_to_html", grab)
    reader = _coop_reader(tmp_path, {"pair_labels": ["C1-C2", "C2-O3"], "fermi_energy_ev": 1.0})
    renderer = COOPRenderer(reader.get_section("coop0"), reader)
    renderer.render_to_html(include_plotlyjs=False)
    renderer.render_to_html(include_plotlyjs=False, energy_unit="hartree")
    ev, eh = figures
    assert ev.layout.xaxis.title.text == "Energy (eV)"
    assert eh.layout.xaxis.title.text == "Energy (Eh)"
    assert "E: %{x:.3f} eV" in ev.data[0].hovertemplate
    assert "E: %{x:.3f} Eh" in eh.data[0].hovertemplate
    # The grid runs from -5 eV, and the Fermi tag at 1 eV moves with it.
    assert np.asarray(eh.data[0].x) == pytest.approx(np.asarray(ev.data[0].x) / HARTREE_TO_EV)
    assert np.asarray(eh.data[0].x)[0] == pytest.approx(-5.0 / HARTREE_TO_EV)
    fermi_x = [shape.x0 for shape in eh.layout.shapes if shape.x0 == shape.x1]
    assert any(x == pytest.approx(1.0 / HARTREE_TO_EV) for x in fermi_x), fermi_x


def test_terminal_coop_plot_follows_the_unit(tmp_path):
    from vibeview.tui import plots

    reader = _coop_reader(tmp_path, {"pair_labels": ["C1-C2", "C2-O3"]})
    ev = plots.coop_plot(reader, "coop0", 80, 20)
    eh = plots.coop_plot(reader, "coop0", 80, 20, energy_unit="hartree")
    assert ev.xlabel == "Energy (eV)" and eh.xlabel == "Energy (Eh)"
    assert np.asarray(eh.series[0].x) == pytest.approx(np.asarray(ev.series[0].x) / HARTREE_TO_EV)
    assert np.asarray(eh.series[0].y) == pytest.approx(np.asarray(ev.series[0].y))


def test_render_chart_hands_the_unit_to_coop(tmp_path, monkeypatch):
    from vibeview.tui import plots
    from vibeview.tui.show import render_chart

    seen: list[str] = []
    real = plots.coop_plot

    def spy(reader, sid, cols, rows, unit):
        seen.append(unit)
        return real(reader, sid, cols, rows, unit)

    monkeypatch.setitem(plots.ENERGY_AXIS_BUILDERS, "dos.coop", spy)
    reader = _coop_reader(tmp_path, {})
    assert render_chart(reader, "coop0", 80, 20, band_energy_unit="kcal_mol") is not None
    assert seen == ["kcal_mol"]


def test_browser_coop_panel_follows_the_band_unit_and_redraws_on_save(tmp_path):
    save_settings({**load_settings(), "band_energy_unit": "hartree"})
    b = _builder()
    b.add_binary("coop0", "dos.coop", "energies", np.linspace(-5.0, 5.0, 11))
    b.add_binary("coop0", "dos.coop", "projections", np.ones((2, 11)) * 0.1)
    b.add_binary("coop0", "dos.coop", "integrated", np.array([0.5, -0.25]))
    b.add_json("coop0", "dos.coop", "meta", {"pair_labels": ["a", "b"]})
    reader = QVFReader(b.write(tmp_path / "coop.qvf"))
    try:
        app = _app(reader)
        app.controller.activate_section("coop0")
        assert app.state.chart_title == "COOP"
        assert "Energy (Eh)" in _axis_titles(app.state.chart_html)

        app.state.settings_band_energy_unit = "kcal_mol"
        app.controller.save_settings()
        assert "Energy (kcal/mol)" in _axis_titles(app.state.chart_html)
    finally:
        reader.close()


# ── the Bond Orders table ───────────────────────────────────────────────────


class _BondOrders:
    method = "mayer"
    pairs = [
        {"i": 0, "j": 1, "order": 0.95, "distance_ang": _OH_ANG, "symbol_i": "O", "symbol_j": "H"}
    ]


class _BondOrderReader:
    def read_bond_orders(self, _sid):
        return _BondOrders()


def _csv_of(html: str) -> str:
    encoded = re.search(r"base64,([A-Za-z0-9+/=]+)", html)
    assert encoded is not None
    return base64.b64decode(encoded.group(1)).decode()


def test_bond_orders_table_follows_the_length_unit_and_the_csv_stays_in_angstrom():
    from vibeview.renderers.bond_orders import BondOrdersRenderer

    section = types.SimpleNamespace(id="bo", kind="bond_orders")
    renderer = BondOrdersRenderer(section, _BondOrderReader())
    default = renderer.render_to_html()
    bohr = renderer.render_to_html(length_unit="bohr")
    assert default == renderer.render_to_html(length_unit="angstrom")
    assert "Distance (Å)" in default and f"{_OH_ANG:.3f} Å" in default
    assert "Distance (bohr)" in bohr and f"{_OH_ANG * ANGSTROM_TO_BOHR:.3f} bohr" in bohr
    assert "Å" not in bohr
    assert _csv_of(bohr) == _csv_of(default)
    assert _csv_of(bohr).splitlines()[0] == "pair,i,j,order,distance_ang"
    assert f"{_OH_ANG:.4f}" in _csv_of(bohr)


def test_browser_bond_orders_panel_follows_the_length_unit(tmp_path):
    b = _builder()
    b.add_json(
        "bo0", "bond_orders", "bond_orders",
        {"method": "mayer", "pairs": [{"i": 0, "j": 1, "order": 0.95, "distance_ang": _OH_ANG}]},
    )
    reader = QVFReader(b.write(tmp_path / "bo.qvf"))
    try:
        app = _app(reader)
        app.controller.activate_section("bo0")
        assert "Distance (Å)" in app.state.properties_html

        app.state.settings_measure_unit = "bohr"
        app.controller.save_settings()
        assert "Distance (bohr)" in app.state.properties_html
        assert f"{_OH_ANG * ANGSTROM_TO_BOHR:.3f} bohr" in app.state.properties_html
    finally:
        reader.close()


# ── static captures ─────────────────────────────────────────────────────────


def test_capture_bands_png_axis_follows_the_unit(tmp_path, monkeypatch):
    from vibeview.capture import capture_bands
    from vibeview.renderers import bands as bands_mod

    seen = {}

    def grab(fig):
        axis = fig.axes[0]
        seen["ylabel"], seen["ylim"] = axis.get_ylabel(), axis.get_ylim()
        return b"\x89PNG"

    monkeypatch.setattr(bands_mod, "figure_to_png", grab)
    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf([section], files)
    try:
        with QVFReader(path) as reader:
            assert capture_bands(
                reader, tmp_path / "b.png", energy_window=(-2.0, 2.0), energy_unit="hartree"
            )
    finally:
        path.unlink()
    assert seen["ylabel"] == "E − E_F (Eh)"
    assert seen["ylim"] == pytest.approx((-2.0 / HARTREE_TO_EV, 2.0 / HARTREE_TO_EV))


def test_capture_dos_follows_the_unit(tmp_path):
    from vibeview.capture import capture_dos

    energies, dos = _silicon_pdos()
    section, files = _dos_section(energies, dos)
    path = _make_qvf([section], files)
    try:
        with QVFReader(path) as reader:
            assert capture_dos(reader, tmp_path / "d.html", energy_unit="hartree")
            assert capture_dos(reader, tmp_path / "d_ev.html")
    finally:
        path.unlink()
    assert "E − E_F (Eh)" in _axis_titles((tmp_path / "d.html").read_text())
    assert "E − E_F (eV)" in _axis_titles((tmp_path / "d_ev.html").read_text())


def test_capture_energy_diagram_and_bond_orders_hand_their_units_on(tmp_path, monkeypatch):
    from vibeview.capture import capture_bond_orders, capture_energy_diagram
    from vibeview.renderers import bond_orders as bo_mod
    from vibeview.renderers import wavefunction as wf_mod

    seen = {}

    class _Diagram:
        def __init__(self, section, reader):
            pass

        def render_energy_diagram(self, **kwargs):
            seen["diagram"] = kwargs
            return "<html>diagram</html>"

    class _Table:
        def __init__(self, section, reader):
            pass

        def render_to_html(self, **kwargs):
            seen["table"] = kwargs
            return "<html>table</html>"

    monkeypatch.setattr(wf_mod, "WavefunctionRenderer", _Diagram)
    monkeypatch.setattr(bo_mod, "BondOrdersRenderer", _Table)
    wf = types.SimpleNamespace(id="wf0", kind="wavefunction.gto")
    bo = types.SimpleNamespace(id="bo0", kind="bond_orders")
    reader = types.SimpleNamespace(
        sections=[wf, bo], get_section=lambda sid: {"wf0": wf, "bo0": bo}[sid]
    )
    assert capture_energy_diagram(reader, tmp_path / "e.html", energy_unit="ev")
    assert capture_bond_orders(reader, tmp_path / "b.html", length_unit="bohr")
    assert seen == {"diagram": {"energy_unit": "ev"}, "table": {"length_unit": "bohr"}}
    assert capture_energy_diagram(reader, tmp_path / "e2.html")
    assert seen["diagram"] == {"energy_unit": "hartree"}


def test_cli_capture_passes_the_saved_band_unit(tmp_path, monkeypatch):
    from vibeview import capture as capture_mod
    from vibeview.cli import main

    seen = {}

    def fake(reader, out, **kwargs):
        seen.update(kwargs)
        Path(out).write_bytes(b"png")
        return True

    monkeypatch.setattr(capture_mod, "capture_bands", fake)
    save_settings({**load_settings(), "band_energy_unit": "kcal_mol"})
    section, files = _bands_section(_silicon_bands(), fermi=0.0001)
    path = _make_qvf([section], files)
    try:
        result = CliRunner().invoke(
            main, ["capture", str(path), "-s", "bands0", "-o", str(tmp_path / "b.png")]
        )
    finally:
        path.unlink()
    assert result.exit_code == 0, result.output
    assert seen["energy_unit"] == "kcal_mol"


# ── symmetrize ──────────────────────────────────────────────────────────────


def test_symmetrize_reports_the_shift_in_the_length_unit(tmp_path):
    nudged = [
        {"symbol": "O", "position": [0.01, 0.02, 0.13], "atomic_number": 8},
        {"symbol": "H", "position": [0.77, -0.02, -0.47], "atomic_number": 1},
        {"symbol": "H", "position": [-0.75, 0.01, -0.49], "atomic_number": 1},
    ]
    reader = QVFReader(_builder(nudged).write(tmp_path / "sym.qvf"))
    try:
        app = _app(reader)
        app.controller.activate_section("structure")
        app.state.measure_unit = "bohr"
        app.controller.edit_symmetrize()
        message = app.state.status_message
        assert "Symmetrized" in message and "max shift" in message, message
        assert re.search(r"max shift \d+\.\d{3} bohr\)$", message), message
    finally:
        reader.close()


# ── compare ─────────────────────────────────────────────────────────────────


def test_compare_legend_reports_the_rmsd_in_the_length_unit(tmp_path):
    import pyvista as pv

    from vibeview.app import _render_compare_overlay

    moved = [{**atom, "position": [p + 1.0 for p in atom["position"]]} for atom in _WATER]
    readers = [
        QVFReader(_builder().write(tmp_path / "a.qvf")),
        QVFReader(_builder(moved).write(tmp_path / "b.qvf")),
    ]
    try:
        plotter = pv.Plotter(off_screen=True)
        default = _render_compare_overlay(plotter, readers, align=True)
        bohr = _render_compare_overlay(plotter, readers, align=True, length_unit="bohr")
        plotter.close()
    finally:
        for reader in readers:
            reader.close()
    assert default[1]["rmsd"] == pytest.approx(0.0, abs=1e-6)
    assert default[1]["label"].endswith("(RMSD 0.00 Å)")
    assert bohr[1]["label"].endswith("(RMSD 0.00 bohr)")
    assert bohr[1]["rmsd"] == default[1]["rmsd"], "the stored RMSD stays in Å"


def test_saving_the_length_unit_relabels_the_compare_legend(make_app):
    app = make_app()
    app.state.compare_mode = True
    app.state.compare_legend = [
        {"name": "a.qvf", "color": "#000", "rmsd": 0.5, "label": "a.qvf (RMSD 0.50 Å)"},
        {"name": "b.qvf", "color": "#000", "rmsd": None, "label": "b.qvf"},
    ]
    app.state.settings_measure_unit = "bohr"
    app.controller.save_settings()
    assert app.state.compare_legend[0]["label"] == f"a.qvf (RMSD {0.5 * ANGSTROM_TO_BOHR:.2f} bohr)"
    assert app.state.compare_legend[0]["rmsd"] == 0.5
    assert app.state.compare_legend[1]["label"] == "b.qvf"


def test_diff_and_batch_compare_report_the_rmsd_in_the_saved_unit(sample_qvf, tmp_path):
    from vibeview.cli import main

    copy = tmp_path / "copy.qvf"
    shutil.copy(sample_qvf, copy)
    runner = CliRunner()

    diff = runner.invoke(main, ["diff", str(sample_qvf), str(copy)])
    assert diff.exit_code == 0, diff.output
    assert re.search(r"Geom\. RMSD\s+0\.0000 Å", diff.output), diff.output

    save_settings({**load_settings(), "measure_unit": "bohr"})
    diff = runner.invoke(main, ["diff", str(sample_qvf), str(copy)])
    assert re.search(r"Geom\. RMSD\s+0\.0000 bohr", diff.output), diff.output
    as_json = json.loads(runner.invoke(main, ["diff", "--json", str(sample_qvf), str(copy)]).output)
    assert as_json["geo_rmsd_a"] == pytest.approx(0.0, abs=1e-9)

    batch = runner.invoke(main, ["batch-compare", str(sample_qvf), str(copy)])
    assert batch.exit_code == 0, batch.output
    assert "copy.qvf: RMSD to ref = 0.0000 bohr" in batch.output
    assert "| File | Energy (Eh) | RMSD (bohr) | Note |" in batch.output
    rows = json.loads(
        runner.invoke(main, ["batch-compare", "--json", str(sample_qvf), str(copy)]).output
    )["rmsd"]
    assert rows[1]["rmsd_angstrom"] == pytest.approx(0.0, abs=1e-9), "the JSON keeps Å"
