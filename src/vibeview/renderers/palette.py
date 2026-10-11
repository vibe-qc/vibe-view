"""The colours vibe-view charts and scenes share.

One accent blue was typed 26 times across ten renderer modules, the series
palette existed at 10 and 20 entries (so PNG and HTML fat bands coloured
channel 11 differently), and the spin, comparison, chain and secondary-
structure palettes each had a copy per front end. They live here, once,
with the terminal's RGB tuples derived from the same hex values.
"""

from __future__ import annotations

from collections.abc import Sequence

# ── Accents ───────────────────────────────────────────────────────────
ACCENT_BLUE = "#3366CC"
ACCENT_RED = "#CC3333"
ACCENT_GREEN = "#66AA55"
ACCENT_ORANGE = "#CC8833"
ACCENT_PURPLE = "#AA66CC"
GUIDE_GREY = "#888888"  # zero lines and reference guides
TICK_GREY = "#999999"  # high-symmetry-point and band-window guides
DARK_BACKGROUND = "#1a1a2e"
LIGHT_BACKGROUND = "#f0f0f0"

# Signed scalar fields: positive lobe, negative lobe.
ISO_POSITIVE = ACCENT_BLUE
ISO_NEGATIVE = ACCENT_RED

# ── Series ────────────────────────────────────────────────────────────
#: Distinct series for bands, COOP and multi-channel charts. Twenty entries,
#: cycled by :func:`series_color`, so the eleventh channel is the same
#: colour in every output.
SERIES: tuple[str, ...] = (
    "#3366CC",
    "#CC3333",
    "#66AA55",
    "#CC8833",
    "#AA66CC",
    "#33AAAA",
    "#CCCC33",
    "#CC6699",
    "#33CC66",
    "#9966CC",
    "#CC6633",
    "#3399CC",
    "#99CC33",
    "#CC3399",
    "#66CCAA",
    "#CCAA33",
    "#336699",
    "#993366",
    "#669933",
    "#996633",
)

#: Spin channels: up, down, then the next two for four-component data.
SPIN: tuple[str, ...] = (ACCENT_BLUE, ACCENT_ORANGE, ACCENT_GREEN, ACCENT_PURPLE)

#: Spectra overlaid for comparison.
COMPARE: tuple[str, ...] = ("#CC3333", "#3366CC", "#33AA55", "#CC8800", "#8844CC", "#00AABB")

#: Reaction-path waypoint kinds.
WAYPOINT_KIND: dict[str, str] = {
    "reactant": "#2ca02c",
    "transition_state": "#d62728",
    "intermediate": "#ff7f0e",
    "product": "#1f77b4",
    "point": "#7f7f7f",
}

# ── Biomolecules ──────────────────────────────────────────────────────
#: One colour per chain, cycled.
CHAIN: tuple[str, ...] = (
    "#4363d8",
    "#e6194b",
    "#3cb44b",
    "#f58231",
    "#911eb4",
    "#42d4f4",
    "#f032e6",
    "#bfef45",
    "#fabed4",
    "#469990",
)

#: Secondary structure by DSSP letter: the cartoon renderer's convention,
#: which the terminal now shares. Helix (H), strand (E), coil (C), plus the
#: 3-10 helix (G), pi helix (I) and isolated bridge (B).
SS: dict[str, str] = {
    "H": "#E03636",
    "E": "#E8C43A",
    "C": "#B0B4BC",
    "G": "#E870A0",
    "I": "#AA40BB",
    "B": "#D09832",
}

#: B-factor ramp endpoints and the colour for a residue without one.
BFACTOR_COLD = "#3054C8"
BFACTOR_WARM = "#E03636"
BFACTOR_ABSENT = "#6E7480"


# ── Scene overlays ────────────────────────────────────────────────────
# The browser viewer's own 3D accents. They were literals in app.py; naming
# them here keeps one place to restyle the viewer (#37-R4).

#: Bond-order colour anchors, interpolated between neighbours: the bond
#: colouring and the legend beside it read the same table.
BOND_ORDER_ANCHORS: tuple[tuple[float, str], ...] = (
    (1.0, GUIDE_GREY),
    (1.5, "#9933CC"),
    (2.0, ACCENT_BLUE),
    (3.0, ACCENT_RED),
)
PICK_MARKER = "#FFD400"  # measure-mode atom picks
EDIT_HIGHLIGHT = "#FF6600"  # atoms selected in the editor
FROZEN_HIGHLIGHT = "#29B6F6"  # atoms frozen for live optimization
DIPOLE_ARROW = "#FF6600"
WANNIER_CENTRE = "#FFCC33"
FRAME_VOLUME = "#3DDC84"  # per-frame reaction-path volume
FRAME_CELL_EDGE = "#444444"  # per-frame trajectory cell
ELF_SURFACE = "#00B894"
LAPLACIAN_CONCENTRATION = "#8E44AD"  # nabla^2 rho < 0
LAPLACIAN_DEPLETION = "#F39C12"  # nabla^2 rho > 0
SLICE_OUTLINE = "#FFFFFF"
ELEMENT_OVERRIDE_DEFAULT = "#FF8800"  # the colour picker's starting value
#: Extra isosurfaces added over the active volume, cycled.
EXTRA_ISOSURFACES: tuple[str, ...] = (ACCENT_GREEN, ACCENT_ORANGE, ACCENT_PURPLE)
#: Fermi-surface sheets, cycled.
FERMI_SHEETS: tuple[str, ...] = (
    ACCENT_BLUE,
    ACCENT_ORANGE,
    ACCENT_GREEN,
    ACCENT_PURPLE,
    ACCENT_RED,
    SERIES[5],
)
#: One tint per loaded file in compare mode.
COMPARE_FILES: tuple[str, ...] = SERIES[:8]


def series_color(index: int) -> str:
    """The colour for series ``index``, cycling through :data:`SERIES`."""
    return SERIES[index % len(SERIES)]


def chain_color(index: int) -> str:
    """The colour for chain ``index``, cycling through :data:`CHAIN`."""
    return CHAIN[index % len(CHAIN)]


def rgb(hex_color: str) -> tuple[int, int, int]:
    """``"#RRGGBB"`` as an integer triple, for terminals and raster code."""
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_palette(colors: Sequence[str]) -> list[tuple[int, int, int]]:
    """Every entry of a hex palette as an integer triple."""
    return [rgb(color) for color in colors]


def sequential_ramp(
    values: Sequence[float], cold: str = BFACTOR_COLD, warm: str = BFACTOR_WARM
) -> list[tuple[int, int, int]]:
    """Linear cold-to-warm colours for ``values`` scaled over their own range.

    NaN (an absent value) is :data:`BFACTOR_ABSENT`, never the cold end. A
    column with no variation sits at the ramp midpoint, as the browser's
    cartoon colouring does (#38-22): there is no gradient to show.
    """
    finite = [v for v in values if v == v]
    if not finite:
        return [rgb(BFACTOR_ABSENT)] * len(values)
    lo, hi = min(finite), max(finite)
    span = hi - lo
    c0, c1 = rgb(cold), rgb(warm)
    out: list[tuple[int, int, int]] = []
    for v in values:
        if v != v:
            out.append(rgb(BFACTOR_ABSENT))
            continue
        t = 0.5 if span <= 0 else (v - lo) / span
        out.append(tuple(int(round(a + (b - a) * t)) for a, b in zip(c0, c1, strict=True)))  # type: ignore[arg-type]
    return out
