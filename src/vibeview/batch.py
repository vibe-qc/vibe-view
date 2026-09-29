"""Offscreen batch rendering for vibe-view (Phase D3).

Render each QVF's structure to a PNG with shared view defaults, so a glob of
results becomes a comparison gallery without standing up the interactive server.
Used by the ``vibe-view batch`` CLI command.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from vibeview.headless import render_structure_frame

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader


def render_structure_png(
    reader: QVFReader,
    path: Path,
    size: tuple[int, int] = (900, 600),
    representation: str = "ball_and_stick",
) -> bool:
    """Render reader's structure to a PNG at path (offscreen)."""
    return bool(render_structure_frame(reader, path, size=size, representation=representation))


def render_volume_png(
    reader: QVFReader,
    path: Path,
    section_id: str,
    isovalue: float | None = None,
    colormap: str | None = None,
    size: tuple[int, int] = (900, 600),
) -> bool:
    """Render a volume section to PNG (density, orbital, etc.).

    The same render as ``vibe-view capture``: signed fields get both lobes,
    and the isosurface is tiled and clamped to the periodic axes. This used
    to be a second, simpler renderer with a single unsigned surface (#39-25).
    ``isovalue`` and ``colormap`` default to the section's viewer hints.
    """
    from vibeview.capture import capture_volume
    from vibeview.viewer_defaults import section_volume_hints

    if not reader.has_section(section_id):
        return False
    hints = section_volume_hints(reader, section_id)
    return capture_volume(
        reader,
        section_id,
        path,
        isovalue=hints.isovalue if isovalue is None else isovalue,
        colormap=colormap or hints.colormap,
        opacity=hints.opacity,
        size=size,
    )


def render_all_png(
    reader: QVFReader,
    out_dir: Path,
    size: tuple[int, int] = (900, 600),
    include_volumes: bool = False,
    representation: str = "ball_and_stick",
) -> list[str]:
    """Render structure + optionally each volume section to PNG files.

    Returns list of paths written."""

    written: list[str] = []

    # Always render structure
    struct_path = out_dir / "structure.png"
    if render_structure_png(reader, struct_path, size=size, representation=representation):
        written.append(str(struct_path))

    if include_volumes:
        for sec in reader.sections:
            if sec.kind.startswith("volume.") or sec.kind == "basis.ao":
                vol_path = out_dir / f"{sec.id}.png"
                if render_volume_png(reader, vol_path, sec.id, size=size):
                    written.append(str(vol_path))
    return written
