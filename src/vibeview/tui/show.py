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

from vibeview.kinds import classify_section
from vibeview.qvf import QVFReader
from vibeview.tui import braille, panes, plots
from vibeview.tui import scene as scenelib
from vibeview.tui.raster import Camera, Canvas, rotation_matrix
from vibeview.tui.scene import GEOMETRIC_KINDS
from vibeview.tui.scene import VOLUME_KINDS as _VOLUME_KINDS

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
    rotation: tuple[float, float, float] = (-0.25, -0.55, 0.0),
    show_labels: bool = False,
    frame: int = 0,
    chart: bool = False,
) -> braille.CellGrid | None:
    """Render one section, or None when the kind has no graphical form.

    ``chart`` selects the energy profile for the kinds that have both a
    geometry and a chart (reaction paths, trajectories); geometry otherwise.
    """
    kind = reader.get_section(section_id).kind

    if kind == "scan.surface":
        return plots.scan_surface_plot(reader, section_id, cols, rows)
    builder = plots.PLOT_BUILDERS.get(kind)
    charting = chart and kind in plots.DUAL_KINDS
    if builder is not None and (charting or kind not in GEOMETRIC_KINDS):
        return builder(reader, section_id, cols, rows).render()
    if kind not in GEOMETRIC_KINDS:
        return None

    positions = None
    if kind == "vibrations":
        coords, _freq = scenelib.vibration_frames(reader, section_id, 0)
        positions = coords[frame % len(coords)]
    elif kind in {"trajectory", "reaction.path"}:
        coords, _energies = scenelib.trajectory_frames(reader, section_id)
        positions = coords[frame % len(coords)]

    scene = scenelib.structure_scene(
        reader,
        representation=representation,
        color_mode=color_mode,
        replication=replication,
        positions_override=positions,
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
        _label_atoms(grid, scene, canvas, camera, cols, rows, mode)
    return grid


def _label_atoms(grid, scene, canvas, camera, cols, rows, mode) -> None:
    import numpy as np

    xy, z = scenelib.atom_screen_positions(scene, canvas, camera)
    px_col, px_row = (2, 4) if mode == "braille" else (1, 2)
    for idx in np.argsort(-z):
        if not np.isfinite(xy[idx]).all() or z[idx] <= 0:
            continue
        col = int(xy[idx, 0] // px_col)
        row = int(xy[idx, 1] // px_row) - 1
        if 0 <= row < rows and 0 <= col < cols:
            grid.text(row, col, scene.labels[idx], (245, 245, 245))


def default_section(reader: QVFReader) -> str | None:
    """Pick the section a reader most likely wants: the structure, else first."""
    for section in reader.sections:
        if section.kind == "structure":
            return section.id
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
    **kwargs,
) -> str:
    """Render one section of ``path`` as a printable string."""
    reader = QVFReader(path)
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
    **kwargs,
) -> str:
    """Render every graphable section, in a reading order, one after another."""
    reader = QVFReader(path)
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
            blocks.append(f"── {section.id} ({section.kind}): {type(exc).__name__}: {exc}")
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
