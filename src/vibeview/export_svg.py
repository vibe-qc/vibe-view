"""SVG vector export for publication-quality figures.

Renders the current molecular structure as an SVG with CPK-colored
circles for atoms and lines for bonds.  Vector output is ideal for
journal submissions that require line-art figures.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader


# van der Waals radii (Angstrom) for space-filling display. Bondi, J. Phys.
# Chem. 68, 441 (1964) values for the common elements; others fall back to
# 1.75x the covalent radius (see _vdw_radius).
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

    Constraint: a fixed XY orthographic projection collapses planar molecules
    lying in a plane that contains z (e.g. water in the y-z plane -> all x
    equal -> a vertical line). Projecting onto the two largest-variance axes of
    the (already centred) atom cloud keeps any planar molecule spread out in
    the drawing plane. Degenerate cases (0/1 atom) return zeros.
    """
    pos = np.asarray(positions, dtype=float)
    if pos.shape[0] < 2:
        return np.zeros((pos.shape[0], 2), dtype=float)
    centred = pos - pos.mean(axis=0)
    cov = centred.T @ centred
    # eigh returns eigenvalues in ascending order; take the two largest.
    _evals, evecs = np.linalg.eigh(cov)
    axes = evecs[:, ::-1][:, :2]  # columns = leading two principal axes
    return centred @ axes


def export_svg(
    reader: "QVFReader",
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
        svg += '<text x="50%" y="50%" text-anchor="middle" font-family="sans-serif">No structure data</text>'
        svg += "</svg>"
        Path(output_path).write_text(svg)
        return svg

    symbols = [a.symbol for a in sdata.atoms]
    positions = np.array([a.position for a in sdata.atoms], dtype=float)

    # CPK colors
    _CPK = {
        "H": "#ffffff",
        "He": "#d9ffff",
        "Li": "#cc80ff",
        "Be": "#c2ff00",
        "B": "#ffb5b5",
        "C": "#333333",
        "N": "#3050f8",
        "O": "#ff0d0d",
        "F": "#90e050",
        "Ne": "#b3e3f5",
        "Na": "#ab5cf2",
        "Mg": "#8aff00",
        "Al": "#bfa6a6",
        "Si": "#f0c8a0",
        "P": "#ff8000",
        "S": "#ffff30",
        "Cl": "#1ff01f",
        "Ar": "#80d1e3",
        "K": "#8f40d4",
        "Ca": "#3dff00",
        "Fe": "#e06633",
        "Ni": "#50d050",
        "Cu": "#c88033",
        "Zn": "#7d80b0",
        "Br": "#a62929",
        "Ag": "#c0c0c0",
        "I": "#940094",
        "Pt": "#d0d0e0",
        "Au": "#ffd123",
    }

    # Center the molecule
    if len(positions) > 0:
        centroid = positions.mean(axis=0)
        positions = positions - centroid

    # Project onto the two leading PCA axes (see _project_2d) so planar
    # molecules never collapse to a line.
    coords2d = _project_2d(positions)

    # Find bounds
    min_vals = coords2d.min(axis=0) - 2.0
    max_vals = coords2d.max(axis=0) + 2.0
    span = np.where((max_vals - min_vals) > 1e-9, max_vals - min_vals, 1.0)
    scale_factor = min(
        (width - 2 * padding) / span[0],
        (height - 2 * padding) / span[1],
    )

    def _to_svg(p):
        x = padding + (p[0] - min_vals[0]) * scale_factor
        y = height - padding - (p[1] - min_vals[1]) * scale_factor
        return x, y

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        f'<rect width="100%" height="100%" fill="{background}"/>',
    ]

    # Determine atom sizes
    cov_radii_tbl = {
        "H": 0.31,
        "C": 0.76,
        "N": 0.71,
        "O": 0.66,
        "F": 0.57,
        "P": 1.07,
        "S": 1.05,
        "Cl": 1.02,
        "Br": 1.20,
        "I": 1.39,
    }
    if style == "space_filling":
        # True vdW radii for space-filling (not covalent). Fallback 1.75x cov.
        radii = {
            s: _vdw_radius(s, cov_radii_tbl.get(s, 0.8)) * scale_factor * 0.7
            for s in set(symbols)
        }
    elif style == "ball_and_stick":
        radii = {s: cov_radii_tbl.get(s, 0.8) * scale_factor * 0.25 for s in set(symbols)}
    else:
        radii = {s: scale_factor * 0.08 for s in set(symbols)}

    # Draw bonds first (behind atoms)
    if style != "space_filling":
        cov_radii = [cov_radii_tbl.get(s, 0.8) for s in symbols]
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                d = np.linalg.norm(positions[i] - positions[j])
                threshold = (cov_radii[i] + cov_radii[j]) * 1.2
                if 0.4 < d < threshold:
                    x1, y1 = _to_svg(coords2d[i])
                    x2, y2 = _to_svg(coords2d[j])
                    bond_width = max(1, scale_factor * 0.05)
                    lines.append(
                        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                        f'stroke="#888888" stroke-width="{bond_width:.1f}" stroke-linecap="round"/>'
                    )

    # Draw atoms
    for i, (symbol, pos) in enumerate(zip(symbols, coords2d)):
        x, y = _to_svg(pos)
        r = radii.get(symbol, scale_factor * 0.2)
        color = _CPK.get(symbol, "#888888")
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
