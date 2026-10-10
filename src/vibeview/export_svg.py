"""SVG vector export for publication-quality figures.

Renders the current molecular structure as an SVG with CPK-colored
circles for atoms and lines for bonds.  Vector output is ideal for
journal submissions that require line-art figures.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from vibeview.figure2d import BOND_COLOR, flat_figure

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader


def export_svg(
    reader: QVFReader,
    output_path: str,
    *,
    width: int = 800,
    height: int = 600,
    style: str = "ball_and_stick",
    background: str = "#ffffff",
    label_atoms: bool = False,
    padding: float = 50.0,
) -> str:
    """Export molecular structure as an SVG file.

    Parameters
    ----------
    reader : QVFReader
    output_path : str
    width, height : int
        SVG canvas dimensions.
    style : str
        One of "ball_and_stick", "space_filling", "sticks_only".
    background : str
        Background color (hex or named).
    label_atoms : bool
        Whether to show element labels.
    padding : float
        Padding around the molecule in SVG units.

    Returns
    -------
    str
        The SVG content.
    """
    try:
        sdata = reader.read_structure()
    except Exception:
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        svg += f'<rect width="100%" height="100%" fill="{background}"/>'
        svg += (
            '<text x="50%" y="50%" text-anchor="middle" '
            'font-family="sans-serif">No structure data</text>'
        )
        svg += "</svg>"
        Path(output_path).write_text(svg)
        return svg

    symbols = [a.symbol for a in sdata.atoms]
    circles, segments = flat_figure(sdata, style)
    coords2d = np.array([c[0] for c in circles]).reshape(len(circles), 2)
    # Find bounds, including image bond endpoints outside the atom cloud.
    extent = np.vstack([coords2d, *[np.vstack(seg) for seg in segments]])
    min_vals = extent.min(axis=0) - 2.0
    max_vals = extent.max(axis=0) + 2.0
    span = np.where((max_vals - min_vals) > 1e-9, max_vals - min_vals, 1.0)
    scale_factor = min(
        (width - 2 * padding) / span[0],
        (height - 2 * padding) / span[1],
    )

    def _to_svg(p: np.ndarray) -> tuple[float, float]:
        x = padding + (p[0] - min_vals[0]) * scale_factor
        y = height - padding - (p[1] - min_vals[1]) * scale_factor
        return x, y

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        f'<rect width="100%" height="100%" fill="{background}"/>',
    ]

    # Draw bonds first (behind atoms).
    if segments:
        bond_width = max(1, scale_factor * 0.05)
        for start2d, end2d in segments:
            x1, y1 = _to_svg(start2d)
            x2, y2 = _to_svg(end2d)
            lines.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="{BOND_COLOR}" stroke-width="{bond_width:.1f}" stroke-linecap="round"/>'
            )

    # Draw atoms
    for symbol, (pos, radius, color) in zip(symbols, circles, strict=True):
        x, y = _to_svg(pos)
        r = radius * scale_factor
        # Atom sphere
        lines.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" '
            f'fill="{color}" stroke="#000000" stroke-width="0.5"/>'
        )
        # Label
        if label_atoms:
            lines.append(
                f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" '
                f'dominant-baseline="central" font-family="sans-serif" '
                f'font-size="{r * 0.8:.1f}" fill="#ffffff">{symbol}</text>'
            )

    lines.append("</svg>")
    svg = "\n".join(lines)
    Path(output_path).write_text(svg)
    return svg
