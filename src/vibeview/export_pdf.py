"""PDF export for vibe-view — render structure as a vector PDF.

Produces publication-ready PDF figures suitable for inclusion in
LaTeX documents via \\includegraphics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader


# van der Waals radii (Angstrom) for space-filling display. Bondi, J. Phys.
# Chem. 68, 441 (1964); others fall back to 1.75x the covalent radius.
VDW_RADII: dict[str, float] = {
    "H": 1.2,
    "C": 1.7,
    "N": 1.55,
    "O": 1.52,
    "F": 1.47,
    "P": 1.8,
    "S": 1.8,
    "Cl": 1.75,
}


def _vdw_radius(symbol: str, covalent: float) -> float:
    """vdW radius for ``symbol``; 1.75x the covalent radius as fallback."""
    return VDW_RADII.get(symbol, covalent * 1.75)


def _project_2d(positions: np.ndarray) -> np.ndarray:
    """Project 3D atom positions onto the two leading PCA axes.

    Constraint: a fixed XY projection collapses planar molecules lying in a
    plane containing z (water in the y-z plane -> all x equal -> a vertical
    line). Projecting onto the two largest-variance axes of the centred atom
    cloud keeps planar molecules spread out. Degenerate (0/1 atom) -> zeros.
    """
    pos = np.asarray(positions, dtype=float)
    if pos.shape[0] < 2:
        return np.zeros((pos.shape[0], 2), dtype=float)
    centred = pos - pos.mean(axis=0)
    cov = centred.T @ centred
    _evals, evecs = np.linalg.eigh(cov)  # ascending eigenvalues
    axes = evecs[:, ::-1][:, :2]  # leading two principal axes
    return centred @ axes


def export_pdf(
    reader: "QVFReader",
    output_path: str,
    *,
    width: float = 6.0,  # inches
    height: float = 4.5,  # inches
    style: str = "ball_and_stick",
    background: str = "#ffffff",
    dpi: int = 300,
) -> str:
    """Export a high-resolution raster PNG suitable for PDF embedding.

    Uses matplotlib to render a clean 2D projection of the structure
    with CPK-colored circles and bond lines, then saves as PNG at the
    specified DPI.  The PNG can be embedded in LaTeX or used directly.

    For true vector PDF output, use the SVG export instead
    (vibe-view export --format svg).

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
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle
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

    symbols = [a.symbol for a in sdata.atoms]
    positions = np.array([a.position for a in sdata.atoms], dtype=float)

    # CPK colors
    _CPK = {
        "H": "#ffffff",
        "C": "#333333",
        "N": "#3050f8",
        "O": "#ff0d0d",
        "F": "#90e050",
        "P": "#ff8000",
        "S": "#ffff30",
        "Cl": "#1ff01f",
        "Br": "#a62929",
        "I": "#940094",
        "Si": "#f0c8a0",
        "Li": "#cc80ff",
        "Na": "#ab5cf2",
        "K": "#8f40d4",
        "Fe": "#e06633",
        "Ni": "#50d050",
        "Cu": "#c88033",
        "Zn": "#7d80b0",
        "Pt": "#d0d0e0",
        "Au": "#ffd123",
    }

    # Center
    if len(positions) > 0:
        centroid = positions.mean(axis=0)
        positions = positions - centroid

    # Project onto the two leading PCA axes (see _project_2d).
    coords2d = _project_2d(positions)

    fig, ax = plt.subplots(figsize=(width, height))
    ax.set_aspect("equal")
    ax.set_facecolor(background)
    ax.axis("off")

    # Compute bounds
    margin = 1.5
    x_min, x_max = coords2d[:, 0].min() - margin, coords2d[:, 0].max() + margin
    y_min, y_max = coords2d[:, 1].min() - margin, coords2d[:, 1].max() + margin
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)

    cov_radii_tbl = {
        "H": 0.25,
        "C": 0.35,
        "N": 0.32,
        "O": 0.30,
        "F": 0.27,
        "P": 0.45,
        "S": 0.40,
        "Cl": 0.38,
        "Br": 0.42,
        "I": 0.48,
    }

    if style == "space_filling":
        # True vdW radii for space-filling (not covalent). Fallback 1.75x cov.
        radii = {s: _vdw_radius(s, cov_radii_tbl.get(s, 0.8)) * 0.8 for s in set(symbols)}
    elif style == "ball_and_stick":
        radii = {s: cov_radii_tbl.get(s, 0.35) for s in set(symbols)}
    else:
        radii = {s: 0.06 for s in set(symbols)}

    # Draw bonds
    if style != "space_filling":
        cov_r = [cov_radii_tbl.get(s, 0.35) for s in symbols]
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                d = np.linalg.norm(positions[i] - positions[j])
                threshold = (cov_r[i] + cov_r[j]) * 1.2
                if 0.4 < d < threshold:
                    ax.plot(
                        [coords2d[i, 0], coords2d[j, 0]],
                        [coords2d[i, 1], coords2d[j, 1]],
                        color="#666666",
                        linewidth=2.0,
                        solid_capstyle="round",
                    )

    # Draw atoms
    for symbol, pos in zip(symbols, coords2d):
        r = radii.get(symbol, 0.25)
        color = _CPK.get(symbol, "#888888")
        circle = Circle(
            (pos[0], pos[1]),
            r,
            facecolor=color,
            edgecolor="#000000",
            linewidth=0.5,
            zorder=10,
        )
        ax.add_patch(circle)

    fig.savefig(
        output_path,
        dpi=dpi,
        bbox_inches="tight",
        facecolor=background,
        edgecolor="none",
    )
    plt.close(fig)
    return output_path


def _export_pdf_fallback_pil(reader, output_path, width, height, style, background, dpi):
    """Fallback export using PIL when matplotlib is unavailable.

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

    symbols = [a.symbol for a in sdata.atoms]
    positions = np.array([a.position for a in sdata.atoms], dtype=float)

    if len(positions) > 0:
        centroid = positions.mean(axis=0)
        positions = positions - centroid

    # Project onto the two leading PCA axes (see _project_2d).
    coords2d = _project_2d(positions)

    margin = 1.5
    x_min, x_max = coords2d[:, 0].min() - margin, coords2d[:, 0].max() + margin
    y_min, y_max = coords2d[:, 1].min() - margin, coords2d[:, 1].max() + margin

    def _to_pixel(p):
        x = (p[0] - x_min) / (x_max - x_min) * (w_px - 40) + 20
        y = h_px - ((p[1] - y_min) / (y_max - y_min) * (h_px - 40) + 20)
        return x, y

    img = Image.new("RGB", (w_px, h_px), background)
    draw = ImageDraw.Draw(img)

    _CPK = {
        "H": (255, 255, 255),
        "C": (51, 51, 51),
        "N": (48, 80, 248),
        "O": (255, 13, 13),
        "F": (144, 224, 80),
        "P": (255, 128, 0),
        "S": (255, 255, 48),
        "Cl": (31, 240, 31),
    }

    for symbol, pos in zip(symbols, coords2d):
        x, y = _to_pixel(pos)
        r = dpi * 0.12
        color = _CPK.get(symbol, (136, 136, 136))
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=(0, 0, 0), width=1)

    img.save(output_path, img_format)
    return output_path
