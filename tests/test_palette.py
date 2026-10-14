"""renderers.palette is the one source of chart and scene colours."""

from __future__ import annotations

import importlib
import inspect
import re

import pytest

from vibeview.renderers import palette

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def test_palettes_are_well_formed_and_distinct() -> None:
    for name in ("SERIES", "SPIN", "COMPARE", "CHAIN"):
        colors = getattr(palette, name)
        assert all(HEX.match(c) for c in colors), name
        assert len(set(c.lower() for c in colors)) == len(colors), name
    assert len(palette.SERIES) == 20
    assert set(palette.SS) == {"H", "E", "C", "G", "I", "B"}
    assert set(palette.WAYPOINT_KIND) == {
        "reactant",
        "transition_state",
        "intermediate",
        "product",
        "point",
    }


def test_series_and_chain_colors_cycle() -> None:
    assert palette.series_color(0) == palette.SERIES[0]
    assert palette.series_color(len(palette.SERIES) + 3) == palette.SERIES[3]
    assert palette.chain_color(len(palette.CHAIN)) == palette.CHAIN[0]


def test_rgb_conversions() -> None:
    assert palette.rgb("#3366CC") == (0x33, 0x66, 0xCC)
    assert palette.rgb("ffffff") == (255, 255, 255)
    assert palette.rgb_palette(("#000000", "#FF0000")) == [(0, 0, 0), (255, 0, 0)]


def test_sequential_ramp() -> None:
    cold, warm = palette.rgb(palette.BFACTOR_COLD), palette.rgb(palette.BFACTOR_WARM)
    assert palette.sequential_ramp([0.0, 1.0]) == [cold, warm]
    # A flat column has no gradient: the midpoint, as the browser cartoon (#38-22).
    mid = tuple(int(round((a + b) / 2)) for a, b in zip(cold, warm, strict=True))
    assert palette.sequential_ramp([5.0, 5.0]) == [mid, mid]
    nan = float("nan")
    assert palette.sequential_ramp([nan, 0.0, 1.0])[0] == palette.rgb(palette.BFACTOR_ABSENT)
    assert palette.sequential_ramp([nan]) == [palette.rgb(palette.BFACTOR_ABSENT)]


def test_front_ends_share_the_palettes() -> None:
    """The terminal's RGB tables are the browser's hex tables, converted."""
    from vibeview.renderers import structure
    from vibeview.tui import plots, scene

    assert palette.rgb_palette(palette.SERIES) == plots.SERIES_COLORS
    assert palette.rgb_palette(palette.CHAIN) == scene._CHAIN_COLORS
    assert scene._SS_COLORS == structure._CARTOON_SS_COLOR
    assert structure._CARTOON_SS_COLOR["H"] == palette.rgb(palette.SS["H"])
    assert palette.rgb(palette.BFACTOR_COLD) == structure._CARTOON_BFACTOR_COLD


@pytest.mark.parametrize(
    "module",
    [
        "vibeview.renderers.bands",
        "vibeview.renderers.coop",
        "vibeview.renderers.dos",
        "vibeview.renderers.eos",
        "vibeview.renderers.phonon",
        "vibeview.renderers.reaction",
        "vibeview.renderers.scf_history",
        "vibeview.renderers.spectra",
        "vibeview.renderers.trajectory",
        "vibeview.renderers.wavefunction",
        "vibeview.tui.plots",
        "vibeview.tui.scene",
    ],
)
def test_no_module_retypes_the_accent_colours(module: str) -> None:
    source = inspect.getsource(importlib.import_module(module))
    for literal in ("#3366cc", "#cc3333", "#66aa55", "#999999", "#4363d8"):
        assert literal not in source.lower(), (module, literal)
