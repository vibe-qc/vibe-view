"""Terminal mode reads the display unit for orbital energies (#58, slice 4).

The browser's ``energy_unit`` setting (slice 1) now also drives the
terminal's orbital table, its HOMO-LUMO gap and the MO picker, and the
terminal can step the unit with ``e``. The archive stays in Hartree.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

from vibeview.settings import load_settings, save_settings


@pytest.fixture(autouse=True)
def _own_settings_file(monkeypatch, tmp_path):
    """Each test starts from the defaults; a saved unit must not leak."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def _pane(energy_unit: str | None) -> str:
    """The orbital pane for a closed-shell stub: HOMO -0.5 Eh, LUMO +0.1 Eh."""
    from vibeview.tui import panes

    class _Data:
        spin = "restricted"
        orbital_kind = "canonical"
        occupation_semantics = None
        n_ao = 2
        shells: list[object] = []
        pure = True
        symmetry_labels = None
        energies = np.array([-0.5, 0.1])
        occupations = np.array([2.0, 0.0])
        alpha_energies = alpha_occupations = None
        beta_energies = beta_occupations = symmetry_labels_beta = None

    class _Reader:
        provenance = {"n_electrons": 2}

        def read_wavefunction_gto(self, _sid):
            return _Data()

    if energy_unit is None:
        text = panes.wavefunction(_Reader(), "wf")
    else:
        text = panes.wavefunction(_Reader(), "wf", energy_unit=energy_unit)
    return re.sub(r"\[/?[^]]*\]", "", text)


def _header(text: str) -> list[str]:
    line = next(ln for ln in text.splitlines() if ln.strip().startswith("#"))
    return re.findall(r"ε \([^)]*\)", line)


def _gap(text: str) -> str:
    return next(ln for ln in text.splitlines() if "gap" in ln).split("gap", 1)[1].strip()


# ── the pane ────────────────────────────────────────────────────────────────


def test_default_pane_is_unchanged():
    """Hartree first with eV beside it, and the gap in eV, as before."""
    text = _pane(None)
    assert _header(text) == ["ε (Ha)", "ε (eV)"]
    assert "-0.500000" in text
    assert "-13.6057" in text
    assert _gap(text) == f"{0.6 * 27.211386245988:.4f} eV"
    assert _pane("hartree") == text


def test_ev_pane_leads_with_ev_and_keeps_the_stored_hartree():
    text = _pane("ev")
    assert _header(text) == ["ε (eV)", "ε (Ha)"]
    homo = next(ln for ln in text.splitlines() if "HOMO" in ln and "gap" not in ln)
    assert homo.split()[1:3] == ["-13.6057", "-0.500000"]
    assert _gap(text) == f"{0.6 * 27.211386245988:.4f} eV"


def test_kcal_mol_pane_quotes_the_gap_in_kcal_mol():
    text = _pane("kcal_mol")
    assert _header(text) == ["ε (kcal/mol)", "ε (Ha)"]
    assert "-313.75" in text
    assert _gap(text) == f"{0.6 * 627.509474:.4f} kcal/mol"


def test_unknown_unit_falls_back_to_hartree():
    assert _pane("rydberg") == _pane(None)


# ── the app ─────────────────────────────────────────────────────────────────

pytest.importorskip("textual", reason="the terminal viewer needs the [tui] extra")


def _drive(coro_factory):
    import asyncio

    return asyncio.run(coro_factory())


def _titles(app) -> list[str]:
    return [
        str(app.orbital_table.get_cell(key, "surface"))
        for key in app._wavefunction_table_keys
        if key.startswith("mo:")
    ]


def test_saved_unit_seeds_the_terminal(tables_qvf):
    save_settings({**load_settings(), "energy_unit": "ev"})
    from vibeview.tui.app import VibeViewTUI

    app = VibeViewTUI(tables_qvf)
    assert app.energy_unit == "ev"
    (row,) = app._rows_for_wavefunction("wf")
    assert "-13.61 eV" in row["title"]
    assert row["energy_eh"] == -0.5


def test_malformed_saved_unit_starts_in_hartree(tables_qvf):
    save_settings({**load_settings(), "energy_unit": "rydberg"})
    from vibeview.tui.app import VibeViewTUI

    assert VibeViewTUI(tables_qvf).energy_unit == "hartree"


def test_e_cycles_saves_and_relabels_in_place(tables_qvf):
    from vibeview.tui.app import VibeViewTUI

    async def body():
        app = VibeViewTUI(tables_qvf)
        async with app.run_test(size=(140, 40)) as pilot:
            while app.current[1] != "wavefunction.gto":
                await pilot.press("tab")
            await pilot.pause()
            selected = app.wavefunction_surface
            assert any("-0.500 Eh" in t for t in _titles(app))

            await pilot.press("e")
            await pilot.pause()
            assert app.energy_unit == "ev"
            assert load_settings()["energy_unit"] == "ev"
            assert [n.message for n in app._notifications][-1] == "energy unit: eV"
            # The rendered orbital's status line follows the unit, even from
            # the mesh cache.
            assert "-13.61 eV" in app.status_text
            assert any("-13.61 eV" in t for t in _titles(app))
            assert not any("Eh" in t for t in _titles(app))
            assert app.wavefunction_surface == selected

            await pilot.press("t")  # the orbital pane follows too
            await pilot.pause()
            assert "ε (eV)" in str(app.text_pane.render())

            await pilot.press("e")
            await pilot.press("e")
            await pilot.pause()
            assert app.energy_unit == "hartree"
            assert load_settings()["energy_unit"] == "hartree"
            assert any("-0.500 Eh" in t for t in _titles(app))

    _drive(body)
