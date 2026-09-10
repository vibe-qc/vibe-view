"""Terminal mode — a braille-canvas viewer for QVF archives.

The interactive web viewer (``vibe-view open``) and the headless capture
API (``vibe-view capture``) both drive VTK through an OpenGL context. That
context is exactly what a compute node reached over SSH does not have, and
it is the one place a QVF most often lands. This package is the third
front-end: a pure-numpy software rasterizer that paints into a character
grid, so ``vibe-view tui job.qvf`` works on any terminal with no display
server, no GL, and no X forwarding.

Layout:

* :mod:`vibeview.tui.raster` — z-buffered software rasterizer (spheres,
  cylinders, triangle meshes, lines) with Blinn-Phong shading.
* :mod:`vibeview.tui.braille` — pixel array to terminal cells, via the
  2x4 braille block (U+2800) with per-cell 24-bit colour.
* :mod:`vibeview.tui.scene` — QVFReader to renderable scene.
* :mod:`vibeview.tui.plots` — braille XY plots for the chart-shaped kinds.
* :mod:`vibeview.tui.app` — the Textual application (needs ``[tui]``).
* :mod:`vibeview.tui.show` — one-shot ANSI dump, no Textual needed.

Only :mod:`~vibeview.tui.app` requires the ``[tui]`` extra; everything
else runs on the core install.
"""

from __future__ import annotations

__all__ = ["braille", "plots", "raster", "scene", "show"]
