"""The Textual application behind ``vibe-view tui``.

Requires the ``[tui]`` extra (run ``vibe-view doctor`` for the exact install
command); every other
module in this package runs on the core install so the one-shot ``show``
path never needs Textual.

The app is one dispatcher over section kinds. A section is either
*geometric* (drawn by the rasterizer into a braille canvas), *chartable*
(drawn by :mod:`~vibeview.tui.plots`), or *textual* (rendered by
:mod:`~vibeview.tui.panes`). :data:`GEOMETRIC_KINDS`, the evaluated
``wavefunction.gto`` surface path, and ``plots.PLOT_BUILDERS`` decide which;
anything unrecognised falls through to ``panes.generic_section``, so a kind
the writer emits before this viewer learns to draw it still shows its
manifest entry rather than an error.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from rich.segment import Segment
from rich.style import Style
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Click
from textual.strip import Strip
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Static

from vibeview.kinds import VOLUME_KINDS as _VOLUME_KINDS
from vibeview.kinds import classify_section
from vibeview.qvf import QVFReader
from vibeview.renderers.wavefunction import occupation_problem
from vibeview.settings import get_setting, set_setting
from vibeview.tui import braille, panes, plots
from vibeview.tui import scene as scenelib
from vibeview.tui.raster import DEFAULT_ROTATION, Camera, Canvas, rotation_matrix
from vibeview.tui.scene import GEOMETRIC_KINDS
from vibeview.tui.show import render_chart
from vibeview.units import ENERGY_UNITS, band_energy_unit, energy_label, energy_unit, length_unit
from vibeview.vibration_amplitude import peak_excursion

if TYPE_CHECKING:
    from vibeview.qvf import GridData, Section, WavefunctionGTOData
    from vibeview.renderers.wavefunction import WavefunctionRenderer

REPRESENTATION_CYCLE = [
    "ball_and_stick", "licorice", "spacefill", "wireframe", "points", "backbone",
]
COLOR_CYCLE = ["element", "chain", "secondary", "bfactor"]

_STATUS_COLOR = {"rendered": (140, 200, 150), "skipped": (150, 150, 160), "error": (235, 110, 110)}
_DIM = (130, 136, 150)
_BLANK_STYLE = Style()
_WAVEFUNCTION_KIND = "wavefunction.gto"
# A terminal viewport cannot display the extra detail of the browser's 60^3
# default. 48^3 keeps interactive orbital stepping responsive while retaining
# ample resolution for a braille canvas.
_WAVEFUNCTION_GRID_SIZE = 48
_MESH_CACHE_SIZE = 4
_SCENE_CACHE_SIZE = 64
_FIELD_CACHE_SIZE = 32


class _BoundedCache(dict[Any, Any]):
    """An insertion-ordered dict that drops its oldest entry past ``limit``.

    The viewer's caches (scenes, meshes, sampled fields) all want the same
    thing: keep the last few, forget the oldest, and never grow without
    bound during playback. One policy, one place.
    """

    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit

    def __setitem__(self, key: Any, value: Any) -> None:
        if key in self:
            del self[key]
        super().__setitem__(key, value)
        while len(self) > self.limit:
            del self[next(iter(self))]


class GridView(Widget):
    """Renders a :class:`~vibeview.tui.braille.CellGrid` supplied by a callback.

    The grid is rebuilt only when the size changes or the app invalidates it,
    so holding a key to rotate re-renders the scene but a plain repaint does
    not.
    """

    def __init__(
        self, builder: Callable[[int, int], braille.CellGrid | None], **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.builder = builder
        self._strips: list[Strip] = []
        self._built: tuple[int, int] | None = None

    def invalidate(self) -> None:
        self._built = None
        self.refresh()

    def render_line(self, y: int) -> Strip:
        size = (self.size.width, self.size.height)
        if self._built != size:
            self._rebuild(*size)
        if 0 <= y < len(self._strips):
            return self._strips[y]
        return Strip.blank(self.size.width, _BLANK_STYLE)

    def _rebuild(self, cols: int, rows: int) -> None:
        self._built = (cols, rows)
        if cols <= 0 or rows <= 0:
            self._strips = []
            return
        grid = self.builder(cols, rows)
        if grid is None:
            self._strips = [Strip.blank(cols, _BLANK_STYLE) for _ in range(rows)]
            return
        strips = []
        for r in range(min(rows, grid.rows)):
            segments = [
                Segment(
                    text,
                    Style(
                        color=None if fg is None else f"rgb({fg[0]},{fg[1]},{fg[2]})",
                        bgcolor=f"rgb({bg[0]},{bg[1]},{bg[2]})",
                    ),
                )
                for text, fg, bg in grid.row_runs(r)
            ]
            strips.append(Strip(segments, grid.cols))
        while len(strips) < rows:
            strips.append(Strip.blank(cols, _BLANK_STYLE))
        self._strips = strips


# Kinds the terminal shows as text tables (see VibeViewTUI._text_for_current).
TEXT_PANE_KINDS = frozenset(
    {
        "structure",
        "atom_properties",
        "bond_orders",
        "wavefunction.gto",
        "citations",
        "run.record",
        "job.spec",
        "structure.symmetry",
        "spectra.nmr",
        "spectra.epr",
        "topology.qtaim",
        "vibrations",
    }
)


def terminal_renders(kind: str) -> bool:
    """True when the terminal draws, charts or tabulates ``kind``."""
    return (
        kind in GEOMETRIC_KINDS
        or kind in plots.PLOT_BUILDERS
        or kind == "scan.surface"
        or kind == _WAVEFUNCTION_KIND
        or kind in TEXT_PANE_KINDS
    )


class SectionList(Widget):
    """The section browser. Mirrors ``kinds.classify_section`` verbatim.

    A section the viewer cannot draw still appears, with the honest reason
    from the kind registry ("not yet rendered" is a different statement from
    "unsupported"), so the list is a true inventory of the archive rather
    than a list of what happens to be implemented.
    """

    def __init__(self, entries: list[tuple[str, str, str]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.entries = entries
        self.index = 0
        # First entry shown below the header. Follows the selection so it
        # stays visible; without it a long archive's selection scrolled off
        # the bottom and rows past the height were unreachable (#38-36).
        self.first_row = 0

    def _visible_rows(self) -> int:
        return max(1, self.size.height - 1)

    def _follow_selection(self) -> None:
        rows = self._visible_rows()
        if self.index < self.first_row:
            self.first_row = self.index
        elif self.index >= self.first_row + rows:
            self.first_row = self.index - rows + 1
        self.first_row = max(0, min(self.first_row, max(0, len(self.entries) - rows)))

    def entry_at(self, y: int) -> int | None:
        """Entry index drawn on widget row ``y`` (row 0 is the header)."""
        if y < 1:
            return None
        idx = self.first_row + y - 1
        return idx if 0 <= idx < len(self.entries) else None

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if y == 0:
            return Strip(
                [Segment("Sections".ljust(width), Style(color="rgb(215,220,232)", bold=True))],
                width,
            )
        self._follow_selection()
        idx = self.first_row + y - 1
        if idx >= len(self.entries):
            return Strip.blank(width, _BLANK_STYLE)
        section_id, kind, status = self.entries[idx]
        selected = idx == self.index
        color = _STATUS_COLOR.get(status, _DIM)
        marker = "▸ " if selected else "  "
        text = f"{marker}{section_id}"[: max(0, width)]
        style = Style(
            color=f"rgb({color[0]},{color[1]},{color[2]})",
            bgcolor="rgb(32,36,48)" if selected else None,
            bold=selected,
        )
        detail = f" {kind}"
        remaining = width - len(text)
        line = text + detail[:remaining] if remaining > 0 else text
        return Strip([Segment(line.ljust(width), style)], width)


class ActivatingDataTable(DataTable[Any]):
    """A row table whose first mouse click activates the chosen row.

    Textual's default table uses a first click to move the cursor and emits
    ``RowSelected`` only after a second click on that highlighted row. A
    surface picker should activate on the first click. Keyboard arrows keep
    their normal inexpensive highlight-only behavior until Enter is pressed.
    """

    _click_pending = False

    def on_mouse_down(self, event: object) -> None:
        # A press starts a click. Textual's own click handler then moves the
        # cursor, and that move lands in the coordinate watcher below, which
        # is where the row activates. Public handlers only: Textual runs
        # this class's ``on_click`` before DataTable's private ``_on_click``,
        # so the click itself cannot see the moved cursor.
        self._click_pending = True

    def watch_cursor_coordinate(self, old: Any, new: Any) -> None:
        super().watch_cursor_coordinate(old, new)
        if not self._click_pending or old == new:
            return
        self._click_pending = False
        row_index, _column_index = self.cursor_coordinate
        row_key, _column_key = self.coordinate_to_cell_key(self.cursor_coordinate)
        self.post_message(DataTable.RowSelected(self, row_index, row_key))

    def on_click(self, event: Click) -> None:
        # A click on the already highlighted row leaves the cursor where it
        # is (Textual posts the selection itself); drop the pending flag once
        # this event has fully dispatched so a later arrow key cannot claim it.
        self.call_after_refresh(self._forget_click)

    def _forget_click(self) -> None:
        self._click_pending = False


class VibeViewTUI(App[None]):
    """Terminal viewer for one QVF archive."""

    CSS = """
    Screen { background: #000000; }
    #sidebar { width: 30; background: #0a0c12; border-right: solid #1e2230; }
    #body { width: 1fr; }
    #content { height: 1fr; }
    #viewport { width: 1fr; height: 1fr; background: #000000; }
    #orbital_table {
        width: 42%;
        min-width: 28;
        max-width: 46;
        height: 1fr;
        background: #05070c;
        border-left: solid #1e2230;
    }
    #status { height: 1; background: #0a0c12; color: #8a93a8; }
    #textscroll { height: 1fr; background: #05070c; }
    #textpane { height: auto; padding: 0 1; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "help", "Help"),
        # priority=True: Textual binds Tab to focus traversal at the screen
        # level, and without the override the section browser would never
        # see the key.
        Binding("tab", "next_section", "Next section", priority=True),
        Binding("shift+tab", "prev_section", "Prev section", priority=True),
        Binding("down,j", "rotate('down')", "Rotate", show=False),
        Binding("up,k", "rotate('up')", "Rotate", show=False),
        Binding("left,h", "rotate('left')", "Rotate", show=False),
        Binding("right,l", "rotate('right')", "Rotate", show=False),
        Binding("J", "pan('down')", "Pan", show=False),
        Binding("K", "pan('up')", "Pan", show=False),
        Binding("H", "pan('left')", "Pan", show=False),
        Binding("L", "pan('right')", "Pan", show=False),
        Binding("plus,equals_sign,equal", "zoom(0.85)", "Zoom in", show=False),
        Binding("minus,underscore", "zoom(1.18)", "Zoom out", show=False),
        Binding("r", "reset_view", "Reset"),
        Binding("m", "cycle_representation", "Repr"),
        Binding("c", "cycle_colors", "Colour"),
        Binding("b", "toggle('bonds')", "Bonds", show=False),
        Binding("u", "toggle('cell')", "Cell", show=False),
        Binding("o", "toggle('iso')", "Iso", show=False),
        Binding("number_sign", "toggle('labels')", "Labels", show=False),
        Binding("d", "toggle('mode')", "Dots", show=False),
        Binding("s", "toggle('sidebar')", "Sidebar", show=False),
        Binding("t", "toggle('table')", "Table"),
        Binding("g", "toggle('chart')", "Chart"),
        Binding("x", "replicate(0, 1)", "a+", show=False),
        Binding("y", "replicate(1, 1)", "b+", show=False),
        Binding("z", "replicate(2, 1)", "c+", show=False),
        Binding("X", "replicate(0, -1)", "a-", show=False),
        Binding("Y", "replicate(1, -1)", "b-", show=False),
        Binding("Z", "replicate(2, -1)", "c-", show=False),
        Binding("i", "isovalue(0.7)", "Iso-", show=False),
        Binding("I", "isovalue(1.4)", "Iso+", show=False),
        Binding("n", "step_volume(1)", "Next vol/MO", show=False),
        Binding("p", "step_volume(-1)", "Prev vol/MO", show=False),
        Binding("D", "render_density", "Density", show=False),
        Binding("S", "render_spin_density", "Spin density", show=False),
        Binding("e", "cycle_energy_unit", "Energy unit", show=False),
        Binding("space", "play", "Play"),
        Binding("right_square_bracket", "step_frame(1)", "Frame+", show=False),
        Binding("left_square_bracket", "step_frame(-1)", "Frame-", show=False),
        Binding("greater_than_sign", "step_mode(1)", "Mode+", show=False),
        Binding("less_than_sign", "step_mode(-1)", "Mode-", show=False),
        Binding("w", "write_frame", "Write"),
    ]

    def __init__(self, path: str | Path, mode: str = "braille") -> None:
        super().__init__()
        self.path = Path(path)
        self.reader = QVFReader(self.path)
        # on_unmount releases the archive once the app runs; until then a
        # failing constructor must release it itself (#39-18).
        try:
            self.entries = [
                (s.id, s.kind, self._status_of(s)) for s in self.reader.sections
            ]
            self.selected = self._initial_selection()
        except BaseException:
            self.reader.close()
            raise

        self.camera = Camera()
        self.pixel_mode = mode if mode in braille.MODES else "braille"
        self.representation = "ball_and_stick"
        self.color_mode = "element"
        self.show_bonds = True
        self.show_cell = True
        self.show_labels = False
        self.show_iso = True
        self.show_sidebar = True
        self.show_table = False
        # Chart view for kinds that have both a geometry and an energy
        # profile (reaction paths, trajectories).
        self.show_chart = False
        self.replication = [1, 1, 1]
        self.isovalue = 0.05
        self.frame = 0
        self.mode_index = 0
        self.playing = False
        self.status_text = ""
        # Set whenever the framing is stale (new section, new supercell, view
        # reset); the next viewport rebuild re-fits and clears it.
        self._needs_fit = True
        self._last_scene: scenelib.Scene | None = None

        self._scene_cache: dict[tuple[Any, ...], scenelib.Scene] = _BoundedCache(
            _SCENE_CACHE_SIZE
        )
        self._mesh_cache: dict[tuple[Any, ...], list[scenelib.Mesh]] = _BoundedCache(
            _MESH_CACHE_SIZE
        )
        self._mesh_details: dict[tuple[Any, ...], str] = {}
        self._wavefunction_field_cache: dict[
            tuple[Any, ...], tuple[GridData, np.ndarray, bool, str]
        ] = _BoundedCache(_FIELD_CACHE_SIZE)
        self._wavefunction_renderers: dict[str, WavefunctionRenderer] = {}
        self._wavefunction_rows: dict[str, list[dict[str, Any]]] = {}
        # The browser's persisted display unit for orbital energies (#58);
        # an unknown saved value falls back to Hartree.
        self.energy_unit = energy_unit(get_setting("energy_unit"))
        # The browser's other display units (#58): lengths in the geometry
        # tables, and the energy axis of bands and DOS charts.
        self.length_unit = length_unit(get_setting("measure_unit"))
        self.band_energy_unit = band_energy_unit(get_setting("band_energy_unit"))
        self._wavefunction_selections: dict[str, str] = {}
        self._wavefunction_density_notes: dict[str, str] = {}
        self._wavefunction_spin_notes: dict[str, str] = {}
        self._wavefunction_table_keys: list[str] = []
        self._frames_cache: dict[str, np.ndarray] = {}
        self._timer: Timer | None = None

    # ── setup ─────────────────────────────────────────────────────────────

    def _status_of(self, section: Section) -> str:
        if self.reader.section_error(section.id):
            return "error"
        status = classify_section(section.kind)[0]
        # The kind registry describes the browser. A kind the terminal can
        # neither draw nor tabulate (fermi_surface, reaction.waypoints, …)
        # only gets the generic manifest dump here, so it is not "rendered"
        # in this viewer (#38-39).
        if status == "rendered" and not terminal_renders(section.kind):
            return "skipped"
        return status

    def _initial_selection(self) -> int:
        """Open on the structure if there is one — it is what a reader wants."""
        for i, (_id, kind, _status) in enumerate(self.entries):
            if kind == "structure":
                return i
        return 0

    def compose(self) -> ComposeResult:
        self.section_list = SectionList(self.entries, id="sidebar")
        self.viewport = GridView(self._build_grid, id="viewport")
        # markup=False: the status line carries section ids verbatim, and a
        # bracketed id like [vol_mo_1] would otherwise be parsed as a Rich
        # style tag and vanish from the display.
        self.status_bar = Static("", id="status", markup=False)
        self.text_pane = Static("", id="textpane", markup=True)
        self.text_scroll = VerticalScroll(self.text_pane, id="textscroll")
        self.orbital_table = ActivatingDataTable(
            id="orbital_table",
            cursor_type="row",
            show_row_labels=False,
            zebra_stripes=True,
        )
        self.orbital_table.add_column("Surface  (↑/↓, Enter)", key="surface")
        self.content = Horizontal(self.viewport, self.orbital_table, id="content")
        yield Header()
        yield Horizontal(
            self.section_list,
            Vertical(
                self.content,
                self.status_bar,
                self.text_scroll,
                id="body",
            ),
        )
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"vibe-view · {self.path.name}"
        self._on_selection_changed()

    def on_unmount(self) -> None:
        """Release the archive when the app exits (#38-41)."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        with contextlib.suppress(Exception):
            self.reader.close()

    # ── current section ───────────────────────────────────────────────────

    @property
    def current(self) -> tuple[str, str, str]:
        if not self.entries:
            return ("", "", "skipped")
        return self.entries[max(0, min(self.selected, len(self.entries) - 1))]

    @property
    def wavefunction_surface(self) -> str:
        """Selected computed surface for the active wavefunction section."""
        if self.current[1] != _WAVEFUNCTION_KIND:
            return ""
        return self._wavefunction_selections.get(self.current[0], "")

    def _is_geometric(self) -> bool:
        return self.current[1] in GEOMETRIC_KINDS or self.current[1] == _WAVEFUNCTION_KIND

    def _volume_sections(self) -> list[str]:
        return [sid for sid, kind, _s in self.entries if kind in _VOLUME_KINDS]

    def _wavefunction_renderer(self, section_id: str) -> WavefunctionRenderer:
        renderer = self._wavefunction_renderers.get(section_id)
        if renderer is None:
            from vibeview.renderers.wavefunction import WavefunctionRenderer

            renderer = WavefunctionRenderer(self.reader.get_section(section_id), self.reader)
            self._wavefunction_renderers[section_id] = renderer
        return renderer

    def _rows_for_wavefunction(self, section_id: str) -> list[dict[str, Any]]:
        rows = self._wavefunction_rows.get(section_id)
        if rows is None:
            rows = self._wavefunction_renderer(section_id).mo_table(energy_unit=self.energy_unit)
            self._wavefunction_rows[section_id] = rows
        return rows

    def _wavefunction_structure_id(self, section_id: str) -> str:
        """Resolve the structure whose atoms are centres for this basis."""
        structure_id = self._wavefunction_renderer(section_id).load().structure_ref
        if not structure_id or not self.reader.has_section(structure_id):
            return "structure"
        return structure_id

    @staticmethod
    def _mo_surface_key(value: str) -> str:
        return f"mo:{value}"

    def _surface_label(self, section_id: str, key: str) -> str:
        if key == "density":
            return "total density"
        if key == "spin_density":
            return "spin density (α−β)"
        if key.startswith("mo:"):
            value = key.removeprefix("mo:")
            row = next(
                (row for row in self._rows_for_wavefunction(section_id) if row["value"] == value),
                None,
            )
            return row["title"] if row is not None else value
        return key

    def _wavefunction_density_is_physical(
        self, section_id: str, wavefunction: WavefunctionGTOData
    ) -> bool:
        """Whether occupations describe electrons rather than NTO weights."""
        renderer = self._wavefunction_renderer(section_id)
        semantics = (
            renderer.natural_value_semantics() if wavefunction.orbital_kind == "natural" else None
        )
        problem = occupation_problem(wavefunction, semantics, renderer.electron_count())
        self._wavefunction_density_notes[section_id] = (
            "" if problem is None else f"total density unavailable: {problem}"
        )
        return problem is None

    def _activate_wavefunction(self, section_id: str) -> None:
        """Populate the focusable surface picker and choose a default MO."""
        renderer = self._wavefunction_renderer(section_id)
        wavefunction = renderer.load()
        rows = self._rows_for_wavefunction(section_id)

        density_available = self._wavefunction_density_is_physical(
            section_id, wavefunction
        )
        valid_keys = ["density"] if density_available else []
        if wavefunction.spin == "unrestricted" and density_available:
            valid_keys.append("spin_density")
            self._wavefunction_spin_notes[section_id] = ""
        elif wavefunction.spin == "unrestricted":
            self._wavefunction_spin_notes[section_id] = self._wavefunction_density_notes[
                section_id
            ].replace("total density", "spin density", 1)
        else:
            self._wavefunction_spin_notes[section_id] = (
                "spin density is unavailable for a restricted wavefunction "
                "(rho-alpha equals rho-beta)"
            )
        valid_keys.extend(self._mo_surface_key(row["value"]) for row in rows)
        self._wavefunction_table_keys = valid_keys

        selected = self._wavefunction_selections.get(section_id)
        if selected not in valid_keys:
            default = next(
                (
                    row
                    for row in rows
                    if " HOMO" in row["title"] or " HONO" in row["title"]
                ),
                None,
            )
            if default is None and rows:
                default = rows[0]
            selected = (
                self._mo_surface_key(default["value"])
                if default
                else ("density" if density_available else "")
            )
            self._wavefunction_selections[section_id] = selected

        self.orbital_table.clear()
        if density_available:
            self.orbital_table.add_row("Total density", key="density")
        if wavefunction.spin == "unrestricted" and density_available:
            self.orbital_table.add_row("Spin density  α − β", key="spin_density")
        for row in rows:
            self.orbital_table.add_row(
                row["title"],
                key=self._mo_surface_key(row["value"]),
            )
        if valid_keys:
            self.orbital_table.move_cursor(row=valid_keys.index(selected))

        kind = str(wavefunction.orbital_kind or "canonical").replace("_", " ")
        self.status_text = (
            f"{kind} set · {len(rows)} orbitals · ↑/↓ then Enter to render"
        )

    def _replication_tuple(self) -> tuple[int, int, int]:
        r = self.replication
        return (int(r[0]), int(r[1]), int(r[2]))

    # ── scene assembly ────────────────────────────────────────────────────

    def _structure_scene(self, positions: np.ndarray | None = None) -> scenelib.Scene:
        section_id, kind, _status = self.current
        structure_id = (
            self._wavefunction_structure_id(section_id)
            if kind == _WAVEFUNCTION_KIND
            else "structure"
        )
        animated = positions is not None and kind in {"trajectory", "reaction.path", "vibrations"}
        frames = self._frames_for(section_id, kind) if animated else None
        frame_index = self.frame % len(frames) if frames is not None and len(frames) else 0
        key = (
            (section_id, kind, frame_index, self.mode_index) if animated else None,
            structure_id,
            self.representation,
            self.color_mode,
            self.show_bonds,
            self.show_cell,
            self._replication_tuple(),
        )
        cached = self._scene_cache.get(key)
        if cached is None:
            if animated and positions is not None:
                cached = scenelib.frame_scene(
                    self.reader,
                    section_id,
                    positions,
                    frame_index=frame_index,
                    representation=self.representation,
                    color_mode=self.color_mode,
                    show_bonds=self.show_bonds,
                    show_cell=self.show_cell,
                    replication=self._replication_tuple(),
                )
            else:
                cached = scenelib.structure_scene(
                    self.reader,
                    structure_id=structure_id,
                    representation=self.representation,
                    color_mode=self.color_mode,
                    show_bonds=self.show_bonds,
                    show_cell=self.show_cell,
                    replication=self._replication_tuple(),
                    positions_override=positions,
                )
            self._scene_cache[key] = cached
        return cached

    def _frames_for(self, section_id: str, kind: str) -> np.ndarray | None:
        if kind == "vibrations":
            key = f"{section_id}:{self.mode_index}"
            if key not in self._frames_cache:
                coords, freq = scenelib.vibration_frames(
                    self.reader, section_id, self.mode_index
                )
                self._frames_cache[key] = coords
                self.status_text = (
                    f"mode {self.mode_index + 1}  ω = {freq:.2f} cm⁻¹  "
                    f"amplitude ×1.0 (peak {peak_excursion():.2f} Å)"
                )
            return self._frames_cache[key]
        if kind in {"trajectory", "reaction.path"}:
            if section_id not in self._frames_cache:
                coords, _energies = scenelib.trajectory_frames(self.reader, section_id)
                self._frames_cache[section_id] = coords
            return self._frames_cache[section_id]
        return None

    def _wavefunction_meshes(self, section_id: str) -> list[scenelib.Mesh]:
        """Evaluate and contour the selected wavefunction-backed surface."""
        surface = self._wavefunction_selections.get(section_id)
        if not surface:
            return []
        field_key = (section_id, surface, _WAVEFUNCTION_GRID_SIZE)
        mesh_key = (
            "wavefunction",
            section_id,
            surface,
            self.isovalue,
            self._replication_tuple(),
        )
        cached_meshes = self._mesh_cache.get(mesh_key)
        if cached_meshes is not None:
            cached_field = self._wavefunction_field_cache.get(field_key)
            detail = self._surface_detail(
                section_id,
                surface,
                self._mesh_details.get(
                    mesh_key, cached_field[3] if cached_field is not None else ""
                ),
            )
            if cached_meshes:
                self.status_text = (
                    f"rendered {detail} "
                    f"at |iso|={self.isovalue:.4g} (cached)"
                )
            else:
                self.status_text = (
                    f"{detail}: no surface crosses |iso|={self.isovalue:.4g}; "
                    "press i to lower it (cached)"
                )
            if self.is_running:
                self._update_status()
            return cached_meshes

        field = self._wavefunction_field_cache.get(field_key)
        cacheable = True
        try:
            if field is None:
                renderer = self._wavefunction_renderer(section_id)
                if surface == "density":
                    grid, values, integral = renderer.evaluate_density(
                        n_per_dim=_WAVEFUNCTION_GRID_SIZE
                    )
                    signed = False
                    detail = f"total density · integral {integral:.4g} e"
                elif surface == "spin_density":
                    grid, values, integral = renderer.evaluate_spin_density(
                        n_per_dim=_WAVEFUNCTION_GRID_SIZE
                    )
                    signed = True
                    detail = f"spin density α−β · integral {integral:.4g} e"
                else:
                    _prefix, spin, index_text = surface.split(":", 2)
                    grid, values = renderer.evaluate_mo(
                        int(index_text),
                        spin=spin,
                        n_per_dim=_WAVEFUNCTION_GRID_SIZE,
                    )
                    signed = True
                    # The orbital's title carries its energy in the display
                    # unit, so it is added when shown, never cached (#58).
                    detail = ""
                sampling_notes = self._wavefunction_sampling_notes(
                    renderer, section_id
                )
                if sampling_notes:
                    detail += " · " + " · ".join(sampling_notes)
                field = (grid, values, signed, detail)
                self._wavefunction_field_cache[field_key] = field

            grid, values, signed, detail = field
            shown = self._surface_detail(section_id, surface, detail)
            try:
                structure_id = self._wavefunction_structure_id(section_id)
                structure = self.reader.read_structure(structure_id)
            except Exception:  # noqa: BLE001 — a standalone field can still draw
                structure = None
            lattice = structure.lattice_vectors if structure is not None else None
            pbc = structure.pbc if structure is not None else None
            meshes = scenelib.sampled_field_meshes(
                grid,
                values,
                isovalue=self.isovalue,
                signed=signed,
                replication=self._replication_tuple(),
                lattice_vectors=lattice,
                pbc=pbc,
            )
            if meshes:
                self.status_text = f"rendered {shown} at |iso|={self.isovalue:.4g}"
            else:
                self.status_text = (
                    f"{shown}: no surface crosses |iso|={self.isovalue:.4g}; "
                    "press i to lower it"
                )
        except Exception as exc:  # noqa: BLE001 — preserve geometry and report in place
            meshes = []
            cacheable = False
            self.status_text = f"wavefunction surface failed: {exc}"

        if cacheable:
            self._remember_meshes(mesh_key, meshes, detail=detail)
        if self.is_running:
            self._update_status()
        return meshes

    def _surface_detail(self, section_id: str, surface: str, cached: str) -> str:
        """The status-line description of a surface from its cached part.

        Density details (their integrals) are cached whole. An orbital's
        cached part is only its sampling notes; its title is looked up now,
        so it follows the current energy unit.
        """
        if not surface.startswith("mo:"):
            return cached
        return self._surface_label(section_id, surface) + cached

    def _wavefunction_sampling_notes(
        self, renderer: WavefunctionRenderer, section_id: str
    ) -> list[str]:
        """Accuracy qualifications for the scalar field just sampled."""
        notes = []
        fraction = float(getattr(renderer, "last_dropped_l_fraction", 0.0))
        if fraction > 0.005:
            l_max = int(getattr(renderer, "last_dropped_l_max", 0))
            notes.append(
                f"WARNING: {fraction * 100:.0f}% lies in l={l_max} (g+) "
                "shells not rendered; surface is incomplete"
            )
        try:
            structure_id = self._wavefunction_structure_id(section_id)
            periodic = any(self.reader.read_structure(structure_id).pbc)
        except Exception:  # noqa: BLE001 - a standalone field still renders
            periodic = False
        if periodic:
            notes.append(
                "periodic Gamma field uses central-cell AOs; image-AO tails are omitted"
            )
        return notes

    def _remember_meshes(
        self,
        key: tuple[Any, ...],
        meshes: list[scenelib.Mesh],
        *,
        detail: str | None = None,
    ) -> None:
        """Keep a small mesh cache; contours can be large."""
        self._mesh_cache[key] = meshes
        if detail is None:
            self._mesh_details.pop(key, None)
        else:
            self._mesh_details[key] = detail
        for stale in [k for k in self._mesh_details if k not in self._mesh_cache]:
            del self._mesh_details[stale]

    @staticmethod
    def _scene_with_meshes(
        scene: scenelib.Scene, meshes: list[scenelib.Mesh]
    ) -> scenelib.Scene:
        """Copy a cached structure scene before attaching transient meshes."""
        return scenelib.Scene(
            positions=scene.positions,
            radii=scene.radii,
            colors=scene.colors,
            labels=scene.labels,
            bond_starts=scene.bond_starts,
            bond_ends=scene.bond_ends,
            bond_colors_a=scene.bond_colors_a,
            bond_colors_b=scene.bond_colors_b,
            bond_radius=scene.bond_radius,
            line_starts=scene.line_starts,
            line_ends=scene.line_ends,
            line_color=scene.line_color,
            meshes=list(meshes),
            color_notes=list(scene.color_notes),
        )

    def _current_scene(self) -> scenelib.Scene:
        section_id, kind, _status = self.current
        frames = self._frames_for(section_id, kind)
        positions = None
        if frames is not None and len(frames):
            positions = frames[self.frame % len(frames)]
        scene = self._structure_scene(positions)

        meshes = None
        if kind in _VOLUME_KINDS and self.show_iso:
            key = ("volume", section_id, self.isovalue, self._replication_tuple())
            meshes = self._mesh_cache.get(key)
            if meshes is None:
                try:
                    meshes = scenelib.volume_meshes(
                        self.reader,
                        section_id,
                        isovalue=self.isovalue,
                        replication=self._replication_tuple(),
                    )
                except Exception as exc:  # noqa: BLE001 — report, don't crash the UI
                    meshes = []
                    self.status_text = f"isosurface failed: {exc}"
                self._remember_meshes(key, meshes)
        elif kind == _WAVEFUNCTION_KIND and self.show_iso:
            meshes = self._wavefunction_meshes(section_id)
        if meshes is not None:
            scene = self._scene_with_meshes(scene, meshes)
        return scene

    # ── grid building ─────────────────────────────────────────────────────

    def _build_grid(self, cols: int, rows: int) -> braille.CellGrid | None:
        section_id, kind, _status = self.current
        # Geometry wins for a reaction path or trajectory; `g` swaps to the
        # chart. The dispatch itself is the one `vibe-view show` uses.
        try:
            chart = render_chart(
                self.reader,
                section_id,
                cols,
                rows,
                mode=self.pixel_mode,
                chart=self.show_chart,
                band_energy_unit=self.band_energy_unit,
            )
        except Exception as exc:  # noqa: BLE001 — a bad section must not kill the app
            grid = braille.CellGrid(cols, rows)
            grid.text(1, 1, f"{kind}: {exc}", (235, 110, 110))
            return grid
        if chart is not None:
            return chart
        if self._is_geometric():
            try:
                return self._build_3d(cols, rows)
            except Exception as exc:  # noqa: BLE001 — an unreadable member must not kill the app
                # A structure member that fails its CRC (or cannot be decoded)
                # used to escape here and end the session with a traceback
                # (#39-35); report it in the viewport like a failed chart.
                grid = braille.CellGrid(cols, rows)
                grid.text(1, 1, f"{kind}: {exc}", (235, 110, 110))
                return grid
        return None

    def _build_3d(self, cols: int, rows: int) -> braille.CellGrid:
        grid = braille.CellGrid(cols, rows)
        scene = self._current_scene()
        self._last_scene = scene
        width, height = braille.pixel_size(cols, rows, self.pixel_mode)
        canvas = Canvas(width, height)
        if self.camera.distance <= 0.0 or self._needs_fit:
            scenelib.fit_camera(scene, canvas, self.camera)
            self._needs_fit = False
        scenelib.render(scene, canvas, self.camera)
        grid.blit(canvas.pixels, canvas.covered, 0, 0, self.pixel_mode)

        if self.show_labels and len(scene.positions):
            self._overlay_labels(grid, scene, canvas, cols, rows)
        return grid

    def _overlay_labels(
        self,
        grid: braille.CellGrid,
        scene: scenelib.Scene,
        canvas: Canvas,
        cols: int,
        rows: int,
    ) -> None:
        scenelib.label_atoms(grid, scene, canvas, self.camera, cols, rows, self.pixel_mode)

    # ── text pane ─────────────────────────────────────────────────────────

    def _text_for_current(self) -> str:
        section_id, kind, _status = self.current
        try:
            if kind == "structure":
                return panes.structure_table(self.reader, length_unit=self.length_unit)
            if kind == "atom_properties":
                return panes.atom_properties(self.reader, section_id)
            if kind == "bond_orders":
                return panes.bond_orders(self.reader, section_id, length_unit=self.length_unit)
            if kind == "wavefunction.gto":
                return panes.wavefunction(
                    self.reader,
                    section_id,
                    energy_unit=self.energy_unit,
                    length_unit=self.length_unit,
                )
            if kind == "citations":
                return panes.citations(self.reader, section_id)
            if kind == "run.record":
                return panes.run_record(self.reader, section_id)
            if kind == "job.spec":
                return panes.job_spec(self.reader, section_id)
            if kind == "structure.symmetry":
                return panes.symmetry(self.reader, section_id)
            if kind == "spectra.nmr":
                return panes.tensor_pane(self.reader, section_id, "NMR")
            if kind == "spectra.epr":
                return panes.tensor_pane(self.reader, section_id, "EPR")
            if kind == "topology.qtaim":
                return panes.qtaim(self.reader, section_id)
            if kind == "vibrations":
                return panes.vibrations_table(self.reader, section_id)
            return panes.generic_section(self.reader, section_id)
        except Exception as exc:  # noqa: BLE001 — surface the failure in place
            return f"[bold #eb6e6e]{type(exc).__name__}: {exc}[/]"

    def _on_selection_changed(self) -> None:
        self.frame = 0
        self._needs_fit = True
        self.status_text = ""
        self.section_list.index = self.selected
        self.section_list.refresh()
        section_id, kind, _status = self.current
        if kind == _WAVEFUNCTION_KIND:
            try:
                self._activate_wavefunction(section_id)
            except Exception as exc:  # noqa: BLE001 — keep the rest of the archive usable
                self._wavefunction_table_keys = []
                self.orbital_table.clear()
                self.status_text = f"wavefunction load failed: {exc}"
        self._refresh_all()
        if kind == _WAVEFUNCTION_KIND and self._wavefunction_table_keys:
            self.orbital_table.focus()
        elif self.focused is self.orbital_table:
            self.set_focus(None)

    def _refresh_all(self) -> None:
        section_id, kind, status = self.current
        graphic = self._is_geometric() or kind in plots.PLOT_BUILDERS or kind == "scan.surface"
        show_text = self.show_table or not graphic

        self.content.display = graphic
        self.viewport.display = graphic
        wavefunction_active = kind == _WAVEFUNCTION_KIND
        self.orbital_table.display = wavefunction_active
        self.orbital_table.disabled = not wavefunction_active
        self.text_scroll.display = show_text
        self.section_list.display = self.show_sidebar
        if show_text:
            self.text_pane.update(self._text_for_current())
        self.viewport.invalidate()
        self._update_status()

    def _update_status(self) -> None:
        section_id, kind, status = self.current
        bits = [f"{section_id} · {kind}"]
        if status != "rendered":
            bits.append(status)
        if self._is_geometric():
            bits.append(self.representation)
            bits.append(f"colour:{self.color_mode}")
            # A requested scheme that could not be honoured must say so here:
            # the label above would otherwise assert a colouring the viewport
            # is not showing.
            for note in getattr(self._last_scene, "color_notes", []) or []:
                bits.append(note)
            if self._replication_tuple() != (1, 1, 1):
                bits.append("×".join(str(r) for r in self.replication))
            if kind in _VOLUME_KINDS or kind == _WAVEFUNCTION_KIND:
                bits.append(f"iso={self.isovalue:.4g}{'' if self.show_iso else ' (off)'}")
            if kind == _WAVEFUNCTION_KIND and self.wavefunction_surface:
                bits.append(self._surface_label(section_id, self.wavefunction_surface))
        if kind in plots.DUAL_KINDS:
            bits.append("energy profile" if self.show_chart else "geometry (g for energy)")
        if kind in plots.DUAL_KINDS or kind == "vibrations":
            # Vibrations animate too; their counter sat inside the DUAL_KINDS
            # branch, which vibrations is not part of (#38-34).
            frames = self._frames_cache.get(
                f"{section_id}:{self.mode_index}" if kind == "vibrations" else section_id
            )
            if frames is not None and len(frames) > 1:
                bits.append(f"frame {self.frame % len(frames) + 1}/{len(frames)}")
                if self.playing:
                    bits.append("▶")
        if self.status_text:
            bits.append(self.status_text)
        self.status_bar.update("  ·  ".join(bits))

    # ── actions ───────────────────────────────────────────────────────────

    def action_next_section(self) -> None:
        if self.entries:
            self.selected = (self.selected + 1) % len(self.entries)
            self._on_selection_changed()

    def action_prev_section(self) -> None:
        if self.entries:
            self.selected = (self.selected - 1) % len(self.entries)
            self._on_selection_changed()

    def action_rotate(self, direction: str) -> None:
        step = 0.16
        deltas = {
            "left": (-step, 0.0),
            "right": (step, 0.0),
            "up": (0.0, -step),
            "down": (0.0, step),
        }
        dx, dy = deltas.get(direction, (0.0, 0.0))
        self.camera.rotate_by(dx, dy)
        self.viewport.invalidate()

    def action_pan(self, direction: str) -> None:
        # Pan is a camera-frame translation applied after the rotation, so a
        # step scaled by distance moves the view by a constant fraction of
        # the screen whatever the zoom level.
        step = 0.08 * max(self.camera.distance, 1.0)
        dx, dy = {
            "left": (step, 0.0),
            "right": (-step, 0.0),
            "up": (0.0, -step),
            "down": (0.0, step),
        }.get(direction, (0.0, 0.0))
        self.camera.pan = (self.camera.pan[0] + dx, self.camera.pan[1] + dy)
        self.viewport.invalidate()

    def action_zoom(self, factor: float) -> None:
        self.camera.distance = max(0.4, self.camera.distance * factor)
        self.viewport.invalidate()

    def action_reset_view(self) -> None:
        self.camera.rotation = rotation_matrix(*DEFAULT_ROTATION)
        self.camera.pan = (0.0, 0.0)
        self._needs_fit = True
        self.viewport.invalidate()

    def action_cycle_representation(self) -> None:
        idx = REPRESENTATION_CYCLE.index(self.representation)
        self.representation = REPRESENTATION_CYCLE[(idx + 1) % len(REPRESENTATION_CYCLE)]
        self.viewport.invalidate()
        self._update_status()

    def action_cycle_energy_unit(self) -> None:
        """Step the orbital-energy unit and save it, as the browser's
        Settings -> Energy unit does (#58). The orbital table is relabelled
        in place; the chosen surface stays selected."""
        keys = list(ENERGY_UNITS)
        self.energy_unit = keys[(keys.index(self.energy_unit) + 1) % len(keys)]
        self._wavefunction_rows.clear()
        # A toast, not the status line: the viewport rewrites that as soon
        # as it redraws.
        label = energy_label(self.energy_unit)
        try:
            set_setting("energy_unit", self.energy_unit)
        except OSError as exc:
            self.notify(f"energy unit: {label} (not saved: {exc})", severity="warning")
        else:
            self.notify(f"energy unit: {label}")
        section_id, kind, _status = self.current
        if kind == _WAVEFUNCTION_KIND and self._wavefunction_table_keys:
            try:
                rows = self._rows_for_wavefunction(section_id)
            except Exception as exc:  # noqa: BLE001 — keep the old labels, say why
                self.notify(f"orbital table not relabelled: {exc}", severity="error")
            else:
                for row in rows:
                    self.orbital_table.update_cell(
                        self._mo_surface_key(row["value"]), "surface", row["title"]
                    )
        self._refresh_all()

    def action_cycle_colors(self) -> None:
        idx = COLOR_CYCLE.index(self.color_mode)
        self.color_mode = COLOR_CYCLE[(idx + 1) % len(COLOR_CYCLE)]
        self.viewport.invalidate()
        self._update_status()

    async def action_toggle(self, what: str) -> None:
        # ``async`` because Textual's DOMNode declares ``action_toggle`` as a
        # coroutine (it toggles a reactive by name); a sync override fails the
        # type gate. Nothing here awaits (#38-43).
        if what == "bonds":
            self.show_bonds = not self.show_bonds
        elif what == "cell":
            self.show_cell = not self.show_cell
        elif what == "labels":
            self.show_labels = not self.show_labels
        elif what == "iso":
            self.show_iso = not self.show_iso
        elif what == "mode":
            self.pixel_mode = "half" if self.pixel_mode == "braille" else "braille"
        elif what == "sidebar":
            self.show_sidebar = not self.show_sidebar
        elif what == "table":
            self.show_table = not self.show_table
        elif what == "chart":
            self.show_chart = not self.show_chart
        self._refresh_all()
        if (
            what == "table"
            and not self.show_table
            and self.current[1] == _WAVEFUNCTION_KIND
        ):
            self.orbital_table.focus()

    def action_replicate(self, axis: int, delta: int) -> None:
        self.replication[axis] = max(1, min(8, self.replication[axis] + delta))
        self._scene_cache.clear()
        self._needs_fit = True
        self._refresh_all()

    def action_isovalue(self, factor: float) -> None:
        # Mesh keys carry the isovalue, so an earlier level stays cached for
        # the way back; nothing to wipe here.
        self.isovalue = float(np.clip(self.isovalue * factor, 1e-6, 1e3))
        self.viewport.invalidate()
        self._update_status()

    def _select_wavefunction_surface(self, surface: str) -> None:
        if self.current[1] != _WAVEFUNCTION_KIND:
            return
        if surface not in self._wavefunction_table_keys:
            self.status_text = f"surface {surface!r} is unavailable for this wavefunction"
            self._update_status()
            return
        section_id = self.current[0]
        self._wavefunction_selections[section_id] = surface
        self.orbital_table.move_cursor(row=self._wavefunction_table_keys.index(surface))
        self.status_text = f"rendering {self._surface_label(section_id, surface)} …"
        self._needs_fit = True
        self.viewport.invalidate()
        self._update_status()

    def _step_wavefunction_orbital(self, delta: int) -> None:
        section_id = self.current[0]
        orbitals = [
            self._mo_surface_key(row["value"])
            for row in self._rows_for_wavefunction(section_id)
        ]
        if not orbitals:
            return
        current = self.wavefunction_surface
        if current in orbitals:
            target = orbitals[(orbitals.index(current) + delta) % len(orbitals)]
        else:
            target = orbitals[0 if delta >= 0 else -1]
        self._select_wavefunction_surface(target)

    def action_step_volume(self, delta: int) -> None:
        """Step the active MO, or jump to the next stored volume section."""
        if self.current[1] == _WAVEFUNCTION_KIND:
            self._step_wavefunction_orbital(delta)
            return
        volumes = self._volume_sections()
        if not volumes:
            return
        current_id = self.current[0]
        if current_id in volumes:
            nxt = volumes[(volumes.index(current_id) + delta) % len(volumes)]
        else:
            nxt = volumes[0]
        self.selected = next(i for i, e in enumerate(self.entries) if e[0] == nxt)
        self._on_selection_changed()

    def action_render_density(self) -> None:
        if self.current[1] != _WAVEFUNCTION_KIND:
            return
        if "density" not in self._wavefunction_table_keys:
            self.status_text = self._wavefunction_density_notes.get(
                self.current[0], "total density is unavailable for this wavefunction"
            )
            self._update_status()
            return
        self._select_wavefunction_surface("density")

    def action_render_spin_density(self) -> None:
        if self.current[1] != _WAVEFUNCTION_KIND:
            return
        if "spin_density" not in self._wavefunction_table_keys:
            self.status_text = self._wavefunction_spin_notes.get(
                self.current[0], "spin density is unavailable for this wavefunction"
            )
            self._update_status()
            return
        self._select_wavefunction_surface("spin_density")

    def action_step_frame(self, delta: int) -> None:
        section_id, kind, _status = self.current
        frames = self._frames_for(section_id, kind)
        if frames is None or not len(frames):
            return
        self.frame = (self.frame + delta) % len(frames)
        self.viewport.invalidate()
        self._update_status()

    def action_step_mode(self, delta: int) -> None:
        section_id, kind, _status = self.current
        if kind != "vibrations":
            return
        try:
            n_modes = len(self.reader.read_vibrations(section_id).frequencies)
        except Exception:  # noqa: BLE001 — an unreadable section has no modes to step
            n_modes = 0
        # Clamp at both ends: past the last mode the status read "mode 10" on
        # a 6-mode molecule and '<' had to be pressed repeatedly (#38-35).
        self.mode_index = int(np.clip(self.mode_index + delta, 0, max(0, n_modes - 1)))
        self._frames_cache.clear()
        self.frame = 0
        self.viewport.invalidate()
        self._update_status()

    def action_play(self) -> None:
        section_id, kind, _status = self.current
        if self._frames_for(section_id, kind) is None:
            return
        self.playing = not self.playing
        if self.playing:
            self._timer = self.set_interval(1 / 12, self._advance)
        elif self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._update_status()

    def _advance(self) -> None:
        self.action_step_frame(1)

    def action_write_frame(self) -> None:
        """Dump the current view next to the archive as ANSI text."""
        grid = self._build_grid(self.viewport.size.width, self.viewport.size.height)
        if grid is None:
            self.status_text = "nothing to write for this section"
            self._update_status()
            return
        target = self.path.with_suffix(f".{self.current[0]}.txt")
        target.write_text(grid.to_ansi() + "\n", encoding="utf-8")
        self.status_text = f"wrote {target.name}"
        self._update_status()

    def action_help(self) -> None:
        self.show_table = True
        self.text_scroll.display = True
        self.text_pane.update(panes.HELP)
        self.text_scroll.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Render the surface chosen with Enter."""
        if event.data_table is not self.orbital_table:
            return
        self._select_wavefunction_surface(str(event.row_key.value))

    # ── mouse ─────────────────────────────────────────────────────────────

    def on_click(self, event: Click) -> None:
        """Clicking the sidebar selects a section."""
        if not self.show_sidebar:
            return
        widget = getattr(event, "widget", None)
        if widget is not self.section_list:
            return
        idx = self.section_list.entry_at(event.y)
        if idx is not None:
            self.selected = idx
            self._on_selection_changed()


def run(path: str | Path, mode: str = "braille") -> None:
    """Entry point used by ``vibe-view tui``."""
    app = VibeViewTUI(path, mode=mode)
    try:
        app.run()
    finally:
        # on_unmount already closed it after a normal session; this covers a
        # run() that fails before the app mounts (#39-18). close() is idempotent.
        with contextlib.suppress(Exception):
            app.reader.close()
