"""Offscreen batch rendering for vibe-view (Phase D3).

Render each QVF's structure to a PNG with shared view defaults, so a glob of
results becomes a comparison gallery without standing up the interactive server.
Used by the ``vibe-view batch`` CLI command.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from vibeview.headless import (
    add_structure,
    frame,
    headless_plotter,
    render_structure_frame,
    snapshot,
)
from vibeview.plotter_compat import cmap_argument

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader


def render_structure_png(reader: QVFReader, path: Path, size: tuple[int, int] = (900, 600)) -> bool:
    """Render reader's structure to a PNG at path (offscreen)."""
    return bool(render_structure_frame(reader, path, size=size))


def render_volume_png(
    reader: QVFReader,
    path: Path,
    section_id: str,
    isovalue: float = 0.05,
    colormap: str = "viridis",
    size: tuple[int, int] = (900, 600),
) -> bool:
    """Render a volume section to PNG (density, orbital, etc.)."""
    from vibeview.renderers.volume import VolumeRenderer
    from vibeview.viewer_defaults import VolumeHints

    section = reader.get_section(section_id) if reader.has_section(section_id) else None
    if section is None:
        return False

    renderer = VolumeRenderer(section, reader)
    try:
        # Both calls are the availability probe; the payloads are re-read
        # by build_isosurface_mesh below.
        renderer.load_grid()
        renderer.load_data()
    except Exception:
        return False

    hints = VolumeHints(isovalue=isovalue, colormap=colormap, opacity=0.6)
    with headless_plotter(size) as plotter:
        # Draw structure for context
        add_structure(plotter, reader)
        mesh = renderer.make_mesh(hints)
        if mesh is not None and mesh.n_points:
            plotter.add_mesh(
                mesh,
                scalars="values",
                cmap=cmap_argument(colormap),
                opacity=0.6,
                name="vol",
                show_scalar_bar=False,
            )
        frame(plotter)
        snapshot(plotter, path)
    return True


def render_all_png(
    reader: QVFReader,
    out_dir: Path,
    size: tuple[int, int] = (900, 600),
    include_volumes: bool = False,
) -> list[str]:
    """Render structure + optionally each volume section to PNG files.

    Returns list of paths written."""

    written: list[str] = []

    # Always render structure
    struct_path = out_dir / "structure.png"
    if render_structure_png(reader, struct_path, size=size):
        written.append(str(struct_path))

    if include_volumes:
        for sec in reader.sections:
            if sec.kind.startswith("volume.") or sec.kind == "basis.ao":
                vol_path = out_dir / f"{sec.id}.png"
                hints = {
                    "volume.density": (0.05, "viridis"),
                    "volume.orbital": (0.04, "coolwarm"),
                    "volume.spin": (0.005, "RdBu"),
                    "volume.elf": (0.5, "plasma"),
                    "volume.potential": (0.03, "coolwarm"),
                    "volume.rdg": (0.3, "coolwarm"),
                }.get(sec.kind, (0.05, "viridis"))
                if render_volume_png(
                    reader, vol_path, sec.id, isovalue=hints[0], colormap=hints[1], size=size
                ):
                    written.append(str(vol_path))
    return written
