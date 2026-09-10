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

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from rich.segment import Segment
from rich.style import Style
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Static

from vibeview.kinds import classify_section
from vibeview.qvf import QVFReader
from vibeview.tui import braille, panes, plots
from vibeview.tui import scene as scenelib
from vibeview.tui.raster import Camera, Canvas, rotation_matrix
from vibeview.tui.scene import GEOMETRIC_KINDS
from vibeview.tui.scene import VOLUME_KINDS as _VOLUME_KINDS

if TYPE_CHECKING:
    from vibeview.qvf import GridData
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
_ELECTRON_OCCUPATION = "electron_occupation"
_TRANSITION_WEIGHT = "transition_weight"


class GridView(Widget):
    """Renders a :class:`~vibeview.tui.braille.CellGrid` supplied by a callback.

    The grid is rebuilt only when the size changes or the app invalidates it,
    so holding a key to rotate re-renders the scene but a plain repaint does
    not.
    """

    def __init__(self, builder, **kwargs) -> None:
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


class SectionList(Widget):
    """The section browser. Mirrors ``kinds.classify_section`` verbatim.

    A section the viewer cannot draw still appears, with the honest reason
    from the kind registry ("not yet rendered" is a different statement from
    "unsupported"), so the list is a true inventory of the archive rather
    than a list of what happens to be implemented.
    """

    def __init__(self, entries, **kwargs) -> None:
        super().__init__(**kwargs)
        self.entries = entries
        self.index = 0

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if y == 0:
            return Strip(
                [Segment("Sections".ljust(width), Style(color="rgb(215,220,232)", bold=True))],
                width,
            )
        idx = y - 1
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


class ActivatingDataTable(DataTable):
    """A row table whose first mouse click activates the chosen row.

    Textual's default table uses a first click to move the cursor and emits
    ``RowSelected`` only after a second click on that highlighted row. A
    surface picker should activate on the first click. Keyboard arrows keep
    their normal inexpensive highlight-only behavior until Enter is pressed.
    """

    async def _on_click(self, event) -> None:
        before = self.cursor_coordinate
        await super()._on_click(event)
        if self.cursor_coordinate != before:
            row_index, _column_index = self.cursor_coordinate
            row_key, _column_key = self.coordinate_to_cell_key(
                self.cursor_coordinate
            )
            self.post_message(DataTable.RowSelected(self, row_index, row_key))


class VibeViewTUI(App):
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
        self.entries = [
            (s.id, s.kind, self._status_of(s)) for s in self.reader.sections
        ]
        self.selected = self._initial_selection()

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
        self._last_scene = None

        self._scene_cache: dict[tuple, scenelib.Scene] = {}
        self._mesh_cache: dict[tuple, list[scenelib.Mesh]] = {}
        self._mesh_details: dict[tuple, str] = {}
        self._wavefunction_field_cache: dict[
            tuple, tuple[GridData, np.ndarray, bool, str]
        ] = {}
        self._wavefunction_renderers: dict[str, WavefunctionRenderer] = {}
        self._wavefunction_rows: dict[str, list[dict]] = {}
        self._wavefunction_selections: dict[str, str] = {}
        self._wavefunction_density_notes: dict[str, str] = {}
        self._wavefunction_spin_notes: dict[str, str] = {}
        self._wavefunction_table_keys: list[str] = []
        self._frames_cache: dict[str, np.ndarray] = {}
        self._timer = None

    # ── setup ─────────────────────────────────────────────────────────────

    def _status_of(self, section) -> str:
        if self.reader.section_error(section.id):
            return "error"
        return classify_section(section.kind)[0]

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

    def _rows_for_wavefunction(self, section_id: str) -> list[dict]:
        rows = self._wavefunction_rows.get(section_id)
        if rows is None:
            rows = self._wavefunction_renderer(section_id).mo_table()
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

    def _wavefunction_density_is_physical(self, section_id: str, wavefunction) -> bool:
        """Whether occupations describe electrons rather than NTO weights."""
        natural_semantics = (
            self._wavefunction_renderer(section_id).natural_value_semantics()
            if wavefunction.orbital_kind == "natural"
            else None
        )
        if natural_semantics == _TRANSITION_WEIGHT:
            self._wavefunction_density_notes[section_id] = (
                "total density unavailable: natural transition orbitals carry "
                "transition weights, not electron occupations"
            )
            return False
        if wavefunction.spin == "unrestricted":
            occupation_blocks = (
                wavefunction.alpha_occupations,
                wavefunction.beta_occupations,
            )
        else:
            occupation_blocks = (wavefunction.occupations,)
        if any(block is None for block in occupation_blocks):
            self._wavefunction_density_notes[section_id] = (
                "total density unavailable: this wavefunction has no occupations"
            )
            return False
        if wavefunction.orbital_kind != "natural":
            self._wavefunction_density_notes[section_id] = ""
            return True
        if natural_semantics != _ELECTRON_OCCUPATION:
            self._wavefunction_density_notes[section_id] = (
                "total density unavailable: this natural-orbital section does not "
                "declare whether its values are electron occupations or transition "
                "weights"
            )
            return False
        n_electrons = self.reader.provenance.get("n_electrons")
        total = sum(float(np.asarray(block, dtype=float).sum()) for block in occupation_blocks)
        if n_electrons is not None and abs(total - float(n_electrons)) > 0.05:
            self._wavefunction_density_notes[section_id] = (
                f"total density unavailable: values sum to {total:.4g}, not "
                f"{n_electrons} electrons; this is a partial or inconsistent "
                "occupation set"
            )
            return False
        self._wavefunction_density_notes[section_id] = ""
        return True

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

    # ── scene assembly ────────────────────────────────────────────────────

    def _structure_scene(self, positions=None) -> scenelib.Scene:
        section_id, kind, _status = self.current
        structure_id = (
            self._wavefunction_structure_id(section_id)
            if kind == _WAVEFUNCTION_KIND
            else "structure"
        )
        key = (
            structure_id,
            self.representation,
            self.color_mode,
            self.show_bonds,
            self.show_cell,
            tuple(self.replication),
            None if positions is None else positions.tobytes(),
        )
        cached = self._scene_cache.get(key)
        if cached is None:
            cached = scenelib.structure_scene(
                self.reader,
                structure_id=structure_id,
                representation=self.representation,
                color_mode=self.color_mode,
                show_bonds=self.show_bonds,
                show_cell=self.show_cell,
                replication=tuple(self.replication),
                positions_override=positions,
            )
            # A frame-by-frame animation would otherwise grow this without
            # bound; the scene for a given frame is cheap to rebuild.
            if len(self._scene_cache) > 64:
                self._scene_cache.clear()
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
                self.status_text = f"mode {self.mode_index + 1}  ω = {freq:.2f} cm⁻¹"
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
            tuple(self.replication),
        )
        cached_meshes = self._mesh_cache.get(mesh_key)
        if cached_meshes is not None:
            cached_field = self._wavefunction_field_cache.get(field_key)
            detail = self._mesh_details.get(
                mesh_key,
                cached_field[3]
                if cached_field is not None
                else self._surface_label(section_id, surface),
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
            if self.is_mounted:
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
                    detail = self._surface_label(section_id, surface)
                sampling_notes = self._wavefunction_sampling_notes(
                    renderer, section_id
                )
                if sampling_notes:
                    detail += " · " + " · ".join(sampling_notes)
                field = (grid, values, signed, detail)
                if len(self._wavefunction_field_cache) >= 32:
                    self._wavefunction_field_cache.clear()
                self._wavefunction_field_cache[field_key] = field

            grid, values, signed, detail = field
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
                grid_units="angstrom",
                replication=tuple(self.replication),
                lattice_vectors=lattice,
                pbc=pbc,
            )
            if meshes:
                self.status_text = f"rendered {detail} at |iso|={self.isovalue:.4g}"
            else:
                self.status_text = (
                    f"{detail}: no surface crosses |iso|={self.isovalue:.4g}; "
                    "press i to lower it"
                )
        except Exception as exc:  # noqa: BLE001 — preserve geometry and report in place
            meshes = []
            cacheable = False
            self.status_text = f"wavefunction surface failed: {exc}"

        if cacheable:
            self._remember_meshes(mesh_key, meshes, detail=detail)
        if self.is_mounted:
            self._update_status()
        return meshes

    def _wavefunction_sampling_notes(self, renderer, section_id: str) -> list[str]:
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
        key: tuple,
        meshes: list[scenelib.Mesh],
        *,
        detail: str | None = None,
    ) -> None:
        """Keep a small insertion-ordered mesh cache; contours can be large."""
        self._mesh_cache.pop(key, None)
        self._mesh_cache[key] = meshes
        if detail is None:
            self._mesh_details.pop(key, None)
        else:
            self._mesh_details[key] = detail
        while len(self._mesh_cache) > _MESH_CACHE_SIZE:
            oldest = next(iter(self._mesh_cache))
            del self._mesh_cache[oldest]
            self._mesh_details.pop(oldest, None)

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
            key = ("volume", section_id, self.isovalue, tuple(self.replication))
            meshes = self._mesh_cache.get(key)
            if meshes is None:
                try:
                    meshes = scenelib.volume_meshes(
                        self.reader,
                        section_id,
                        isovalue=self.isovalue,
                        replication=tuple(self.replication),
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

    def _build_grid(self, cols: int, rows: int):
        section_id, kind, _status = self.current
        if kind == "scan.surface":
            return plots.scan_surface_plot(self.reader, section_id, cols, rows)
        builder = plots.PLOT_BUILDERS.get(kind)
        # A reaction path and a trajectory have both a geometry and an energy
        # profile. Geometry wins by default; `g` swaps to the chart.
        charting = self.show_chart and kind in plots.DUAL_KINDS
        if builder is not None and (charting or kind not in GEOMETRIC_KINDS):
            try:
                return builder(self.reader, section_id, cols, rows).render()
            except Exception as exc:  # noqa: BLE001 — a bad section must not kill the app
                grid = braille.CellGrid(cols, rows)
                grid.text(1, 1, f"{kind}: {exc}", (235, 110, 110))
                return grid
        if self._is_geometric():
            return self._build_3d(cols, rows)
        return None

    def _build_3d(self, cols: int, rows: int):
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

    def _overlay_labels(self, grid, scene, canvas, cols, rows) -> None:
        """Print atom indices above their projected centre.

        Placed in *character* cells, not pixels — a braille dot cannot carry
        a digit. Nearer atoms are drawn last so their label wins the cell.
        """
        xy, z = scenelib.atom_screen_positions(scene, canvas, self.camera)
        px_per_col, px_per_row = (
            (2, 4) if self.pixel_mode == "braille" else (1, 2)
        )
        order = np.argsort(-z)
        for idx in order:
            if not np.isfinite(xy[idx]).all() or z[idx] <= 0:
                continue
            col = int(xy[idx, 0] // px_per_col)
            row = int(xy[idx, 1] // px_per_row) - 1
            if not (0 <= row < rows and 0 <= col < cols):
                continue
            label = scene.labels[idx] if idx < len(scene.labels) else str(idx + 1)
            grid.text(row, col, label, (245, 245, 245))

    # ── text pane ─────────────────────────────────────────────────────────

    def _text_for_current(self) -> str:
        section_id, kind, _status = self.current
        try:
            if kind == "structure":
                return panes.structure_table(self.reader)
            if kind == "atom_properties":
                return panes.atom_properties(self.reader, section_id)
            if kind == "bond_orders":
                return panes.bond_orders(self.reader, section_id)
            if kind == "wavefunction.gto":
                return panes.wavefunction(self.reader, section_id)
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
            if tuple(self.replication) != (1, 1, 1):
                bits.append("×".join(str(r) for r in self.replication))
            if kind in _VOLUME_KINDS or kind == _WAVEFUNCTION_KIND:
                bits.append(f"iso={self.isovalue:.4g}{'' if self.show_iso else ' (off)'}")
            if kind == _WAVEFUNCTION_KIND and self.wavefunction_surface:
                bits.append(self._surface_label(section_id, self.wavefunction_surface))
        if kind in plots.DUAL_KINDS:
            bits.append("energy profile" if self.show_chart else "geometry (g for energy)")
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
        self.camera.rotation = rotation_matrix(-0.25, -0.55, 0.0)
        self.camera.pan = (0.0, 0.0)
        self._needs_fit = True
        self.viewport.invalidate()

    def action_cycle_representation(self) -> None:
        idx = REPRESENTATION_CYCLE.index(self.representation)
        self.representation = REPRESENTATION_CYCLE[(idx + 1) % len(REPRESENTATION_CYCLE)]
        self.viewport.invalidate()
        self._update_status()

    def action_cycle_colors(self) -> None:
        idx = COLOR_CYCLE.index(self.color_mode)
        self.color_mode = COLOR_CYCLE[(idx + 1) % len(COLOR_CYCLE)]
        self.viewport.invalidate()
        self._update_status()

    def action_toggle(self, what: str) -> None:
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
        self.isovalue = float(np.clip(self.isovalue * factor, 1e-6, 1e3))
        self._mesh_cache.clear()
        self._mesh_details.clear()
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
        if self.current[1] != "vibrations":
            return
        self.mode_index = max(0, self.mode_index + delta)
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

    def on_click(self, event) -> None:
        """Clicking the sidebar selects a section."""
        if not self.show_sidebar:
            return
        widget = getattr(event, "widget", None)
        if widget is not self.section_list:
            return
        idx = event.y - 1
        if 0 <= idx < len(self.entries):
            self.selected = idx
            self._on_selection_changed()


def run(path: str | Path, mode: str = "braille") -> None:
    """Entry point used by ``vibe-view tui``."""
    VibeViewTUI(path, mode=mode).run()
