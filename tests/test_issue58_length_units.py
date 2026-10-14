"""Display unit for measured lengths (#58, length slice).

Structures are stored in Angstrom; the ``measure_unit`` setting changes only
what the browser's measurement readouts show. The water in ``sample_qvf``
has O-H = 0.9578 Å = 1.810 bohr.
"""

from __future__ import annotations

import pytest

from vibeview import app as appmod
from vibeview.qvf import QVFReader
from vibeview.settings import load_settings, save_settings
from vibeview.units import ANGSTROM_TO_BOHR, format_length, length_label, length_unit

_OH_ANG = (0.7572**2 + (0.1173 + 0.4692) ** 2) ** 0.5
_O = [0.0, 0.0, 0.1173]
_H1 = [0.0, 0.7572, -0.4692]


@pytest.fixture(autouse=True)
def _own_settings_file(monkeypatch, tmp_path):
    """Each test starts from the defaults; a saved unit must not leak."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture
def make_app(sample_qvf):
    opened = []

    def build():
        reader = QVFReader(sample_qvf)
        app = appmod.create_app(reader)
        app.controller.view_update = lambda *args, **kwargs: None
        opened.append(reader)
        return app

    yield build
    for reader in opened:
        reader.close()


# ── the helper ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("unit", "text"), [("angstrom", "0.958 Å"), ("bohr", "1.810 bohr")]
)
def test_format_length_converts_from_angstrom(unit, text):
    assert format_length(_OH_ANG, unit) == text


@pytest.mark.parametrize("bad", [None, "", "nm", 3, ["bohr"]])
def test_unknown_length_unit_falls_back_to_angstrom(bad):
    assert length_unit(bad) == "angstrom"
    assert length_label(bad) == "Å"


def test_measure_text_defaults_to_angstrom(sample_qvf):
    from vibeview.app import _measure_text

    with QVFReader(sample_qvf) as reader:
        sdata = reader.read_structure()
    default = _measure_text(sdata, [0, 1])
    bohr = _measure_text(sdata, [0, 1], "bohr")
    assert "Distance O1–H2: 0.958 Å" in default
    assert f"Distance O1–H2: {_OH_ANG * ANGSTROM_TO_BOHR:.3f} bohr" in bohr


# ── the browser ─────────────────────────────────────────────────────────────


def test_saved_unit_seeds_the_session(make_app):
    save_settings({**load_settings(), "measure_unit": "bohr"})
    app = make_app()
    assert app.state.measure_unit == "bohr"
    assert app.state.settings_measure_unit == "bohr"
    app.controller.compute_bonds()
    assert "O1–H2: 1.810 bohr" in app.state.measure_result
    assert "Å" not in app.state.measure_result


def test_malformed_saved_unit_starts_in_angstrom(make_app):
    save_settings({**load_settings(), "measure_unit": "furlong"})
    app = make_app()
    assert app.state.measure_unit == "angstrom"


def test_saving_a_unit_rebuilds_the_bond_table(make_app):
    app = make_app()
    app.controller.compute_bonds()
    assert "O1–H2: 0.958 Å" in app.state.measure_result

    app.state.settings_measure_unit = "bohr"
    app.controller.save_settings()

    assert load_settings()["measure_unit"] == "bohr"
    assert app.state.measure_unit == "bohr"
    assert "O1–H2: 1.810 bohr" in app.state.measure_result
    assert "── Bond Angles ──" in app.state.measure_result
    assert app.state.status_message == "Settings saved"


def test_saving_a_unit_re_measures_open_picks(make_app):
    app = make_app()
    app.controller.toggle_measure_mode(True)
    app.controller.on_pick({"worldPosition": _O})
    app.controller.on_pick({"worldPosition": _H1})
    assert app.state.selected_atoms == [0, 1]
    assert "Distance O1–H2: 0.958 Å" in app.state.measure_result

    app.state.settings_measure_unit = "bohr"
    app.controller.save_settings()

    assert app.state.selected_atoms == [0, 1]
    assert "Distance O1–H2: 1.810 bohr" in app.state.measure_result
    # New picks keep reading in the saved unit.
    app.controller.on_pick({"worldPosition": _H1})
    app.controller.on_pick({"worldPosition": _H1})
    assert "1.810 bohr" in app.state.measure_result


def test_saving_leaves_a_lengthless_readout_alone(make_app):
    app = make_app()
    app.controller.toggle_measure_mode(True)
    hint = app.state.measure_result
    app.state.settings_measure_unit = "bohr"
    app.controller.save_settings()
    assert app.state.measure_result == hint
