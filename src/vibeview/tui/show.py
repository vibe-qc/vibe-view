"""One-shot terminal rendering — ``vibe-view show``.

The interactive app needs Textual; this does not. It renders a section (or
every renderable section) to a string and returns it, which makes it usable
three ways that the full TUI is not: over a plain pipe, inside a CI log, and
from a script that wants the frame as text.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

from vibeview.kinds import VOLUME_KINDS as _VOLUME_KINDS
from vibeview.kinds import classify_section
from vibeview.qvf import QVFError, QVFReader
from vibeview.tui import braille, panes, plots
from vibeview.tui import scene as scenelib
from vibeview.tui.raster import DEFAULT_ROTATION, Camera, Canvas, rotation_matrix
from vibeview.tui.scene import GEOMETRIC_KINDS

# Kinds worth drawing in a sweep, in the order a reader wants to meet them.
_SWEEP_ORDER = [
    "structure",
    "volume.orbital",
    "volume.density",
    "bands",
    "dos.total",
    "spectra.ir",
    "scf_history",
]


def terminal_size(default: tuple[int, int] = (100, 30)) -> tuple[int, int]:
    """Character grid to render into, honouring COLUMNS/LINES when piped."""
    try:
        size = shutil.get_terminal_size()
        cols, rows = size.columns, size.lines
    except OSError:
        cols, rows = default
    if not sys.stdout.isatty() and "COLUMNS" not in os.environ:
        cols, rows = default
    # Leave the last row for the shell prompt.
    return max(24, cols), max(8, rows - 1)


def render_chart(
    reader: QVFReader,
    section_id: str,
    cols: int,
    rows: int,
    *,
    mode: str = "braille",
    chart: bool = False,
    band_energy_unit: str = "ev",
) -> braille.CellGrid | None:
    """The chart for a section, or None when it is drawn as geometry instead.

    One dispatch for the interactive viewer and ``vibe-view show``: a
    ``scan.surface`` is always its heat map; a kind with a plot builder
    charts unless it is a geometric kind, and a dual kind (reaction path,
    trajectory) charts only when ``chart`` asks for the energy profile.
    ``band_energy_unit`` is the axis unit of bands and DOS charts (#58).
    """
    kind = reader.get_section(section_id).kind
    if kind == "scan.surface":
        return plots.scan_surface_plot(reader, section_id, cols, rows, mode)
    builder = plots.PLOT_BUILDERS.get(kind)
    charting = chart and kind in plots.DUAL_KINDS
    if builder is not None and (charting or kind not in GEOMETRIC_KINDS):
        axis_builder = plots.ENERGY_AXIS_BUILDERS.get(kind)
        if axis_builder is not None:
            return axis_builder(reader, section_id, cols, rows, band_energy_unit).render(mode)
        return builder(reader, section_id, cols, rows).render(mode)
    return None


def render_section(
    reader: QVFReader,
    section_id: str,
    cols: int,
    rows: int,
    *,
    mode: str = "braille",
    representation: str = "ball_and_stick",
    color_mode: str = "element",
    replication: tuple[int, int, int] = (1, 1, 1),
    isovalue: float = 0.05,
    rotation: tuple[float, float, float] = DEFAULT_ROTATION,
    show_labels: bool = False,
    frame: int = 0,
    normal_mode: int = 0,
    chart: bool = False,
    band_energy_unit: str = "ev",
) -> braille.CellGrid | None:
    """Render one section, or None when the kind has no graphical form.

    ``chart`` selects the energy profile for the kinds that have both a
    geometry and a chart (reaction paths, trajectories); geometry otherwise.
    ``band_energy_unit`` is the energy-axis unit of bands and DOS (#58).
    """
    kind = reader.get_section(section_id).kind
    chart_grid = render_chart(
        reader,
        section_id,
        cols,
        rows,
        mode=mode,
        chart=chart,
        band_energy_unit=band_energy_unit,
    )
    if chart_grid is not None:
        return chart_grid
    if kind not in GEOMETRIC_KINDS:
        return None

    positions = None
    if kind == "vibrations":
        # The mode was hard-coded to 0 although --frame promised normal-mode
        # frames (#38-50); vibration_frames clamps an out-of-range index.
        coords, _freq = scenelib.vibration_frames(reader, section_id, normal_mode)
        positions = coords[frame % len(coords)]
    elif kind in {"trajectory", "reaction.path"}:
        coords, _energies = scenelib.trajectory_frames(reader, section_id)
        positions = coords[frame % len(coords)]

    if positions is not None:
        scene = scenelib.frame_scene(
            reader,
            section_id,
            positions,
            frame_index=frame % len(coords),
            representation=representation,
            color_mode=color_mode,
            replication=replication,
        )
    else:
        scene = scenelib.structure_scene(
            reader,
            # A structure section draws itself; another kind the shown one.
            structure_id=section_id if kind == "structure" else None,
            representation=representation,
            color_mode=color_mode,
            replication=replication,
        )
    if kind in _VOLUME_KINDS:
        scene.meshes = scenelib.volume_meshes(
            reader, section_id, isovalue=isovalue, replication=replication
        )

    width, height = braille.pixel_size(cols, rows, mode)
    canvas = Canvas(width, height)
    camera = Camera(rotation=rotation_matrix(*rotation))
    scenelib.fit_camera(scene, canvas, camera)
    scenelib.render(scene, canvas, camera)

    grid = braille.CellGrid(cols, rows)
    grid.blit(canvas.pixels, canvas.covered, 0, 0, mode)
    if show_labels and len(scene.positions):
        scenelib.label_atoms(grid, scene, canvas, camera, cols, rows, mode)
    return grid


def default_section(reader: QVFReader) -> str | None:
    """Pick the section a reader most likely wants: the first structure
    (QVF spec § 4.1), else the first rendered section, else the first."""
    if reader.first_structure_id is not None:
        return reader.first_structure_id
    for section in reader.sections:
        if classify_section(section.kind)[0] == "rendered":
            return section.id
    return reader.sections[0].id if reader.sections else None


def show(
    path: str | Path,
    section_id: str | None = None,
    *,
    size: tuple[int, int] | None = None,
    plain: bool = False,
    info: bool = False,
    **kwargs: Any,
) -> str:
    """Render one section of ``path`` as a printable string."""
    # The reader is closed on return (#38-41, #39-18); everything below produces a
    # string and keeps no reference to it.
    with QVFReader(path) as reader:
        return _show(reader, section_id, size=size, plain=plain, info=info, **kwargs)


def _show(
    reader: QVFReader,
    section_id: str | None,
    *,
    size: tuple[int, int] | None,
    plain: bool,
    info: bool,
    **kwargs: Any,
) -> str:
    cols, rows = size or terminal_size()
    target = section_id or default_section(reader)
    if target is None:
        return "archive contains no sections"

    if info:
        from rich.console import Console

        console = Console(width=cols, file=None, record=True, force_terminal=not plain)
        console.begin_capture()
        console.print(panes.overview(reader))
        return console.end_capture()

    grid = render_section(reader, target, cols, rows, **kwargs)
    if grid is None:
        kind = reader.get_section(target).kind
        return (
            f"section {target!r} ({kind}) has no graphical form — "
            f"use `vibe-view tui` or `vibe-view show --info` for its data"
        )
    return grid.to_plain() if plain else grid.to_ansi()


def show_all(
    path: str | Path,
    *,
    size: tuple[int, int] | None = None,
    plain: bool = False,
    **kwargs: Any,
) -> str:
    """Render every graphable section, in a reading order, one after another."""
    with QVFReader(path) as reader:
        return _show_all(reader, size=size, plain=plain, **kwargs)


def _show_all(
    reader: QVFReader,
    *,
    size: tuple[int, int] | None,
    plain: bool,
    **kwargs: Any,
) -> str:
    cols, rows = size or terminal_size()
    ordered = sorted(
        reader.sections,
        key=lambda s: (_SWEEP_ORDER.index(s.kind) if s.kind in _SWEEP_ORDER else len(_SWEEP_ORDER)),
    )
    blocks: list[str] = []
    for section in ordered:
        try:
            grid = render_section(reader, section.id, cols, rows, **kwargs)
        except Exception as exc:  # noqa: BLE001 — one bad section must not stop the sweep
            # A section error already names the section and member (#66);
            # anything else keeps its type, as the last-resort report does.
            reason = str(exc) if isinstance(exc, QVFError) else f"{type(exc).__name__}: {exc}"
            blocks.append(f"── {section.id} ({section.kind}): {reason}")
            continue
        if grid is None:
            continue
        caption = f"── {section.id} · {section.kind} "
        header = caption + "─" * max(0, cols - len(caption))
        blocks.append(header)
        blocks.append(grid.to_plain() if plain else grid.to_ansi())
    if not blocks:
        return "no graphable sections in this archive"
    return "\n".join(blocks)
