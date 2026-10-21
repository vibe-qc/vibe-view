"""PDF export for vibe-view — render structure as a vector PDF.

Produces publication-ready PDF figures suitable for inclusion in
LaTeX documents via \\includegraphics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from vibeview.figure2d import BOND_COLOR, figure_bounds, flat_figure

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader


def export_pdf(
    reader: QVFReader,
    output_path: str,
    *,
    width: float = 6.0,  # inches
    height: float = 4.5,  # inches
    style: str = "ball_and_stick",
    background: str = "#ffffff",
    dpi: int = 300,
) -> str:
    """Export a 2-D projection of the structure as a PDF figure.

    Uses matplotlib to draw CPK-coloured circles and bond lines on the two
    leading principal axes of the structure, and saves to ``output_path``
    in the format its extension names: a vector PDF for ``.pdf`` (the
    intended use, for LaTeX ``\\includegraphics``), a raster image at
    ``dpi`` for ``.png``. Without matplotlib, a PIL fallback draws the same
    figure (same bonds, colours and ``style``) as a raster PDF or PNG.

    Parameters
    ----------
    reader : QVFReader
    output_path : str
    width, height : float
        Figure dimensions in inches.
    style : str
        "ball_and_stick", "space_filling", or "sticks_only".
    background : str
        Background color.
    dpi : int
        Output resolution.

    Returns
    -------
    str
        Path to the generated file.
    """
    try:
        from matplotlib.patches import Circle

        from vibeview.renderers._mpl import agg_pyplot

        plt = agg_pyplot()
    except ImportError:
        # Fallback: export PNG via PIL
        return _export_pdf_fallback_pil(reader, output_path, width, height, style, background, dpi)

    try:
        sdata = reader.read_structure()
    except Exception:
        fig, ax = plt.subplots(figsize=(width, height))
        ax.text(
            0.5,
            0.5,
            "No structure data",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        fig.savefig(output_path, dpi=dpi, bbox_inches="tight", facecolor=background)
        plt.close(fig)
        return output_path

    circles, segments = flat_figure(sdata, style)

    fig, ax = plt.subplots(figsize=(width, height))
    ax.set_aspect("equal")
    ax.set_facecolor(background)
    ax.axis("off")
    x_min, x_max, y_min, y_max = figure_bounds(circles, segments)
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)

    for start, end in segments:
        ax.plot(
            [start[0], end[0]],
            [start[1], end[1]],
            color=BOND_COLOR,
            linewidth=2.0,
            solid_capstyle="round",
        )

    for xy, r, color in circles:
        ax.add_patch(
            Circle(
                (xy[0], xy[1]),
                r,
                facecolor=color,
                edgecolor="#000000",
                linewidth=0.5,
                zorder=10,
            )
        )

    fig.savefig(
        output_path,
        dpi=dpi,
        bbox_inches="tight",
        facecolor=background,
        edgecolor="none",
    )
    plt.close(fig)
    return output_path


def _export_pdf_fallback_pil(
    reader: QVFReader,
    output_path: str,
    width: float,
    height: float,
    style: str,
    background: str,
    dpi: int,
) -> str:
    """Fallback export using PIL when matplotlib is unavailable.

    Draws the same figure as :func:`export_pdf` (:func:`_flat_figure`):
    bonds, ``style`` radii and the shared colour table (#40-17).

    Constraint: writing PNG bytes to a ``.pdf`` path produces an invalid PDF.
    PIL can encode PDF directly, so when the output extension is ``.pdf`` we
    save with the ``PDF`` format; otherwise PNG.
    """
    from PIL import Image, ImageDraw

    # Pick the save format from the output extension so a .pdf path gets a
    # valid PDF, not mislabelled PNG bytes.
    img_format = "PDF" if str(output_path).lower().endswith(".pdf") else "PNG"

    w_px = int(width * dpi)
    h_px = int(height * dpi)

    try:
        sdata = reader.read_structure()
    except Exception:
        img = Image.new("RGB", (w_px, h_px), background)
        img.save(output_path, img_format)
        return output_path

    circles, segments = flat_figure(sdata, style)
    x_min, x_max, y_min, y_max = figure_bounds(circles, segments)
    # One scale for both axes, as the matplotlib figure's equal aspect.
    scale = min((w_px - 40) / (x_max - x_min), (h_px - 40) / (y_max - y_min))
    x_off = (w_px - (x_max - x_min) * scale) / 2
    y_off = (h_px - (y_max - y_min) * scale) / 2

    def _to_pixel(p: np.ndarray) -> tuple[float, float]:
        x = x_off + (p[0] - x_min) * scale
        y = h_px - (y_off + (p[1] - y_min) * scale)
        return x, y

    img = Image.new("RGB", (w_px, h_px), background)
    draw = ImageDraw.Draw(img)

    bond_width = max(1, int(round(0.1 * scale)))
    for start, end in segments:
        draw.line([_to_pixel(start), _to_pixel(end)], fill=BOND_COLOR, width=bond_width)

    for xy, r, color in circles:
        x, y = _to_pixel(xy)
        rp = r * scale
        draw.ellipse([x - rp, y - rp, x + rp, y + rp], fill=color, outline=(0, 0, 0), width=1)

    img.save(output_path, img_format)
    return output_path
