"""Regressions for the terminal-viewer refactor, documentation and performance rows in #50.

Each test names the audit row it pins (#38-N in the issue body).
"""

from __future__ import annotations

import inspect
import re
import types
from pathlib import Path

import numpy as np
import pytest

from vibeview.qvf import QVFReader
from vibeview.qvf_writer import structure_archive
from vibeview.tui import braille, plots
from vibeview.tui import scene as scenelib

_ROOT = Path(__file__).resolve().parent.parent
_WATER = [
    {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.1173]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.7572, -0.4692]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, -0.7572, -0.4692]},
]


# ── #38-9 / #38-11 / #38-12: plots ───────────────────────────────────────


def test_bands_comment_describes_the_series_it_adds():
    src = inspect.getsource(plots.bands_plot)
    assert "One Series per band" in src and "One Series per spin channel" not in src


def test_scan_surface_uses_matplotlib_viridis():
    src = inspect.getsource(plots.scan_surface_plot)
    assert 'colormaps["viridis"]' in src and "Viridis-like" not in src


def test_dead_section_meta_is_gone():
    assert not hasattr(plots, "_section_meta")


# ── #38-16 / #38-24 / #38-25 / #38-26 / #38-27 / #38-29: scene ───────────


def test_scene_dead_and_duplicated_helpers_are_gone():
    from vibeview.tui import raster

    assert not hasattr(scenelib.Scene, "bounds")
    assert not hasattr(scenelib, "hex_to_rgb") and not hasattr(scenelib, "_NEAR_FIT")
    src = inspect.getsource(scenelib)
    assert "cell_edges(" in src and "supercell_offsets(" in src  # #38-24, #38-25 via cell.py
    assert "palette.rgb(cpk_color" in src
    assert "NEAR_PLANE" in src and raster.NEAR_PLANE == 0.05
    structure_src = inspect.getsource(scenelib.structure_scene)
    assert "reader.read_structure(structure_id)" in structure_src
    assert "read_structure()" not in structure_src


# ── #38-30: one frontier rule ────────────────────────────────────────────


def test_terminal_uses_the_renderers_frontier_rule():
    from vibeview.renderers import wavefunction as wf
    from vibeview.tui import app, panes

    assert panes.frontier_orbitals is wf.frontier_orbitals
    assert panes.natural_value_semantics is wf.natural_value_semantics
    for module in (panes, app):
        assert not hasattr(module, "_ELECTRON_OCCUPATION")
        assert not hasattr(module, "_frontier_orbitals")
    occ = np.array([2.0, 2.0, 0.0])
    assert wf.frontier_orbitals(occ, 3, "canonical") == (1, 2, None)
    assert wf.frontier_orbitals(np.array([2.0, 1.5, 0.0]), 3, "canonical")[2]
    assert wf._block_frontier("canonical", occ, 3, natural_confirmed=False) == (1, 2)


def test_occupation_problem_is_shared_by_browser_and_terminal():
    from vibeview.renderers.wavefunction import occupation_problem
    from vibeview.tui import app

    src = inspect.getsource(app.VibeViewTUI._wavefunction_density_is_physical)
    assert "occupation_problem(" in src
    natural = types.SimpleNamespace(
        spin="restricted", occupations=np.array([1.9, 0.1]), orbital_kind="natural",
        density_unavailable_reason=None,
    )
    assert occupation_problem(natural, None, None)
    assert occupation_problem(natural, "transition_weight", None)
    assert occupation_problem(natural, "electron_occupation", 2) is None
    assert occupation_problem(natural, "electron_occupation", 4)
    canonical = types.SimpleNamespace(
        spin="restricted", occupations=np.array([2.0, 0.0]), orbital_kind="canonical",
        density_unavailable_reason=None,
    )
    assert occupation_problem(canonical, None, None) is None


# ── #38-32: the three key maps agree ─────────────────────────────────────


_KEY_GLYPHS = {
    "question_mark": "?",
    "plus": "+",
    "minus": "-",
    "number_sign": "#",
    "space": "space",
    "left_square_bracket": "[",
    "right_square_bracket": "]",
    "greater_than_sign": ">",
    "less_than_sign": "<",
    "tab": "tab",
    "shift+tab": "shift+tab",
}
_ALIASES = {"equals_sign", "equal", "underscore", "j", "k", "h", "l"}


def _primary_keys() -> set[str]:
    from vibeview.tui.app import VibeViewTUI

    keys: set[str] = set()
    for binding in VibeViewTUI.BINDINGS:
        for key in binding.key.split(","):
            if key in _ALIASES:
                continue
            keys.add(_KEY_GLYPHS.get(key, key))
    return keys


def test_every_binding_is_in_the_help_text_and_the_manual():
    from rich.text import Text

    from vibeview.tui.panes import HELP

    help_text = Text.from_markup(HELP).plain
    manual = (_ROOT / "docs" / "terminal.md").read_text()
    manual_keys = re.findall(r"<kbd>([^<]+)</kbd>", manual)
    manual_text = " ".join(manual_keys).replace("&lt;", "<").replace("&gt;", ">").lower()
    arrows = {"up", "down", "left", "right"}
    for key in _primary_keys():
        needle = "arrows" if key in arrows else key
        assert needle in help_text, f"{key} bound but not in HELP"
        if key in arrows:
            assert "arrows" in manual  # the key table says "arrows or h j k l"
        else:
            assert needle.lower().replace("+", " ") in manual_text, f"{key} not in the manual"


# ── #38-37: one cache policy ─────────────────────────────────────────────


def test_bounded_cache_and_the_viewer_caches(sample_qvf):
    pytest.importorskip("textual")
    from vibeview.tui.app import VibeViewTUI, _BoundedCache

    cache = _BoundedCache(2)
    cache["a"], cache["b"], cache["c"] = 1, 2, 3
    assert list(cache) == ["b", "c"]
    cache["b"] = 4  # re-inserting refreshes
    assert list(cache) == ["c", "b"]

    app = VibeViewTUI(sample_qvf)
    try:
        for name in ("_scene_cache", "_mesh_cache", "_wavefunction_field_cache"):
            assert isinstance(getattr(app, name), _BoundedCache), name
    finally:
        app.reader.close()
    src = inspect.getsource(VibeViewTUI.action_isovalue)
    assert "_mesh_cache.clear" not in src
    assert "tobytes" not in inspect.getsource(VibeViewTUI._structure_scene)


# ── #38-38 / #38-40 / #38-43 / #38-44 / #38-48 / #38-49 / #38-51 ────────


def test_kind_dispatch_and_label_overlay_are_shared():
    from vibeview.tui import app, show

    assert "render_chart(" in inspect.getsource(app.VibeViewTUI._build_grid)
    assert "scenelib.label_atoms(" in inspect.getsource(app.VibeViewTUI._overlay_labels)
    assert not hasattr(show, "_label_atoms")
    assert "PLOT_BUILDERS" not in inspect.getsource(app.VibeViewTUI._build_grid)


def test_orbital_table_uses_public_textual_handlers():
    from vibeview.tui.app import ActivatingDataTable

    assert "_on_click" not in ActivatingDataTable.__dict__
    assert "on_mouse_down" in ActivatingDataTable.__dict__


def test_toggle_documents_why_it_is_a_coroutine():
    from vibeview.tui.app import VibeViewTUI

    assert inspect.iscoroutinefunction(VibeViewTUI.action_toggle)
    assert "DOMNode" in inspect.getsource(VibeViewTUI.action_toggle)


def test_dead_raster_and_braille_code_is_gone():
    from vibeview.tui.raster import DEFAULT_FOV, Camera, Canvas

    assert not hasattr(Canvas, "draw_pixels")
    assert not hasattr(braille, "to_ansi") and not hasattr(braille, "to_plain")
    assert Camera().fov == DEFAULT_FOV
    for name in ("draw_spheres", "draw_cylinders", "draw_mesh", "draw_lines"):
        assert inspect.signature(getattr(Canvas, name)).parameters["fov"].default == DEFAULT_FOV


def test_volume_kinds_are_one_set():
    from vibeview import kinds

    assert scenelib.VOLUME_KINDS is kinds.VOLUME_KINDS
    assert not hasattr(kinds, "LAZY_KINDS")  # the one set is VOLUME_KINDS (#44)
    assert kinds.VOLUME_KINDS <= scenelib.GEOMETRIC_KINDS


# ── #38-42: row runs ─────────────────────────────────────────────────────


def test_row_runs_match_a_cell_by_cell_walk():
    rng = np.random.default_rng(5)
    grid = braille.CellGrid(40, 3)
    for c in range(40):
        if rng.random() < 0.7:
            colour = tuple(int(v) for v in rng.integers(0, 3, size=3) * 100)
            grid.text(1, c, "x" if c % 3 else "y", colour)

    def naive(row: int):
        runs = []
        for c in range(grid.cols):
            if grid.filled[row, c]:
                fg = tuple(int(v) for v in grid.fg[row, c])
                style = (fg, tuple(int(v) for v in grid.bg[row, c]))
                char = str(grid.chars[row, c])
            else:
                style = (None, grid.background)
                char = " "
            if runs and runs[-1][1:] == style:
                runs[-1] = (runs[-1][0] + char, *style)
            else:
                runs.append((char, *style))
        return runs

    for row in range(3):
        assert grid.row_runs(row) == naive(row)
        assert "".join(text for text, _fg, _bg in grid.row_runs(row)) == "".join(grid.chars[row])


# ── #38-53: every terminal plot builder renders ─────────────────────────


def _archive_for(kind: str, tmp_path: Path) -> tuple[Path, str]:
    b = structure_archive(_WATER, (False, False, False), calculation=kind)
    sid = "s0"
    grid = np.linspace(-5.0, 5.0, 11)
    if kind == "bands":
        b.add_json(sid, kind, "kpath", {"n_kpoints": 4, "n_bands": 2, "n_spin": 1, "segments": [],
                                        "fermi": 0.5})
        b.add_binary(sid, kind, "eigenvalues", np.linspace(-3, 3, 8).reshape(1, 4, 2))
    elif kind == "dos.total":
        b.add_binary(sid, kind, "energies", grid)
        b.add_binary(sid, kind, "dos", np.abs(grid))
    elif kind == "dos.projected":
        b.add_binary(sid, kind, "energies", grid)
        b.add_binary(sid, kind, "projections", np.ones((2, 11)),
                     section_extra={"channels": [{"label": "s"}, {"label": "p"}]})
    elif kind in ("dos.coop", "dos.cohp"):
        b.add_binary(sid, kind, "energies", grid)
        b.add_binary(sid, kind, "projections", np.ones((1, 11)) * 0.1)
        b.add_binary(sid, kind, "integrated", np.array([0.5]))
        b.add_json(sid, kind, "meta", {"pair_labels": ["O-H"]})
    elif kind.startswith("spectra."):
        b.add_json(sid, kind, "spectrum", {"frequencies": [500.0, 1500.0, 3000.0],
                                           "intensities": [1.0, 0.5, 2.0]})
    elif kind == "scf_history":
        b.add_json(sid, kind, "iterations", {"iterations": [
            {"iter": i + 1, "energy_eh": -76.0 - 0.1 / (i + 1), "delta_e": 0.1 / (i + 1),
             "diis_error": 0.01 / (i + 1)} for i in range(5)]})
    elif kind == "equation_of_state":
        b.add_binary(sid, kind, "volumes", np.linspace(10.0, 20.0, 6))
        b.add_binary(sid, kind, "energies", (np.linspace(10.0, 20.0, 6) - 15.0) ** 2)
        b.add_json(sid, kind, "fit", {"model": "birch_murnaghan", "V0": 15.0, "E0": 0.0,
                                      "B0": 1.0, "B0_prime": 4.0})
    elif kind == "phonon_bands":
        b.add_json(sid, kind, "qpath", {"segments": [], "n_modes": 2})
        b.add_binary(sid, kind, "frequencies", np.abs(np.linspace(-1, 1, 8)).reshape(4, 2) * 100)
    elif kind == "phonon_dos":
        b.add_json(sid, kind, "meta", {})
        b.add_binary(sid, kind, "frequencies", np.linspace(0, 500, 11))
        b.add_binary(sid, kind, "dos", np.ones(11))
    elif kind in ("reaction.path", "trajectory"):
        b.add_json(sid, kind, "metadata", {"atoms": [{"symbol": a["symbol"],
                                                       "atomic_number": a["atomic_number"]}
                                                      for a in _WATER],
                                           "energies": [0.0, 0.5, -0.2], "waypoints": []})
        b.add_binary(sid, kind, "coords", np.zeros((3, 3, 3)))
    return b.write(tmp_path / f"{kind}.qvf"), sid


@pytest.mark.parametrize("kind", sorted(plots.PLOT_BUILDERS))
def test_every_plot_builder_renders(kind, tmp_path):
    path, sid = _archive_for(kind, tmp_path)
    with QVFReader(path) as reader:
        grid = plots.PLOT_BUILDERS[kind](reader, sid, 60, 16).render()
    assert grid.filled.any(), kind


# ── #38-57: the manual's tabulated list ─────────────────────────────────


def test_manual_lists_structure_and_vibrations_as_tabulated():
    manual = (_ROOT / "docs" / "terminal.md").read_text()
    tabulated = manual[manual.index("**Tabulated:**"):]
    tabulated = tabulated[: tabulated.index("\n\n")]
    assert "`structure`" in tabulated and "`vibrations`" in tabulated
