"""
POV-Ray scene export for vibe-view.

Produces .pov files that can be rendered with POV-Ray 3.7+ to create
publication-quality molecular graphics.  Inspired by Gabedit's POV-Ray
export templates.

Usage:
    from vibeview.povray_export import export_povray

    # From a QVF reader:
    export_povray(reader, "molecule.pov", style="ball_and_stick")

    # Then render with:
    #   povray +W1920 +H1080 +A +Q11 molecule.pov
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from vibeview.bonds import bond_endpoints, structure_bonds
from vibeview.elements import CPK_HEX, covalent_radius, cpk_hex, vdw_radius
from vibeview.renderers import palette
from vibeview.renderers.cell import cell_edges

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader


def _cpk_rgb(z: int) -> str:
    """POV-Ray colour vector for atomic number ``z``: the viewer's CPK hex, or
    a neutral grey for an element the table does not know."""
    if z not in CPK_HEX:
        return "<0.700, 0.700, 0.700>"
    r, g, b = palette.rgb(cpk_hex(z))
    return f"<{r / 255:.3f}, {g / 255:.3f}, {b / 255:.3f}>"


# ── POV-Ray scene template ─────────────────────────────────────────────


def _pov_header(width: int = 1920, height: int = 1080, bg_color: str = "<0.1, 0.1, 0.15>") -> str:
    """Generate the POV-Ray scene header with global settings."""
    return f"""// vibe-view POV-Ray export
// Render: povray +W{width} +H{height} +A +Q11 +Ooutput.png

#version 3.7;

global_settings {{
    assumed_gamma 1.0
    max_trace_level 10
    ambient_light rgb <0.1, 0.1, 0.1>
}}

// Background
background {{ color rgb {bg_color} }}

// Camera (auto-positioned to frame the molecule)
camera {{
    perspective
    location  <15, 8, -20>
    look_at   <0, 0, 0>
    right     x*image_width/image_height
    angle     35
}}

// ── Lighting (3-point) ──
// Key light
light_source {{
    <15, 20, -15>
    color rgb <0.9, 0.9, 0.95>
}}
// Fill light
light_source {{
    <-15, 5, 10>
    color rgb <0.3, 0.3, 0.35>
}}
// Rim / back light
light_source {{
    <0, -5, 20>
    color rgb <0.4, 0.4, 0.5>
}}

// ── Default finish for atoms ──
#declare atom_finish = finish {{
    ambient 0.15
    diffuse 0.7
    specular 0.4
    roughness 0.02
    phong 0.5
    phong_size 60
    reflection 0.08
}}

// ── Bond finish ──
#declare bond_finish = finish {{
    ambient 0.1
    diffuse 0.6
    specular 0.3
    roughness 0.03
    phong 0.4
    phong_size 40
    reflection 0.05
}}

// ── Scale: 1 Angstrom = 1 POV-Ray unit ──
"""


def _atom_sphere(
    position: np.ndarray,
    z: int,
    radius: float = 0.3,
) -> str:
    """Generate a POV-Ray sphere for one atom."""
    x, y, z_pos = position[0], position[1], position[2]
    color = _cpk_rgb(z)
    return f"""sphere {{
    <{x:.4f}, {y:.4f}, {z_pos:.4f}>, {radius:.4f}
    pigment {{ color rgb {color} }}
    finish {{ atom_finish }}
}}"""


def _bond_cylinder(
    start: np.ndarray,
    end: np.ndarray,
    radius: float = 0.1,
    color: str = "<0.5, 0.5, 0.5>",
) -> str:
    """Generate a POV-Ray cylinder for a bond."""
    sx, sy, sz = start[0], start[1], start[2]
    ex, ey, ez = end[0], end[1], end[2]
    dx, dy, dz = ex - sx, ey - sy, ez - sz
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    if length < 1e-6:
        return ""
    return f"""cylinder {{
    <{sx:.4f}, {sy:.4f}, {sz:.4f}>, <{ex:.4f}, {ey:.4f}, {ez:.4f}>, {radius:.4f}
    pigment {{ color rgb {color} }}
    finish {{ bond_finish }}
}}"""



# ── Public API ──────────────────────────────────────────────────────────


def export_povray(
    reader: QVFReader,
    output_path: str,
    *,
    style: str = "ball_and_stick",
    width: int = 1920,
    height: int = 1080,
    bg_color: str = "<0.1, 0.1, 0.15>",
    atom_radius_factor: float = 1.0,
) -> str:
    """Export a .pov POV-Ray scene file from a QVF reader.

    Parameters
    ----------
    reader : QVFReader
        The QVF reader with the structure data.
    output_path : str
        Path for the output .pov file.
    style : str
        One of ``"ball_and_stick"``, ``"space_filling"``,
        ``"sticks_only"``, ``"wireframe"``.
    width, height : int
        Output image resolution.
    bg_color : str
        POV-Ray colour string for background.
    atom_radius_factor : float
        Multiplier for atom radii.

    Returns
    -------
    str
        The generated POV-Ray scene as a string.
    """
    parts = [_pov_header(width, height, bg_color)]

    # ── Structure ──
    try:
        sdata = reader.read_structure()
    except Exception:
        parts.append("// No structure data available")
        result = "\n".join(parts)
        Path(output_path).write_text(result)
        return result

    # Extract atom data from StructureData
    z_list = [a.atomic_number for a in sdata.atoms]
    positions = np.array([a.position for a in sdata.atoms], dtype=float)

    # Determine atom radii based on style
    cov_radii = [covalent_radius(z, 0.8) for z in z_list]

    if style == "space_filling":
        # True vdW radii for space-filling (was incorrectly using covalent).
        radii = [vdw_radius(z) * atom_radius_factor for z in z_list]
    elif style == "ball_and_stick":
        radii = [r * 0.4 * atom_radius_factor for r in cov_radii]
    elif style == "sticks_only":
        radii = [0.15 * atom_radius_factor for _ in cov_radii]
    else:  # wireframe
        radii = [0.08 * atom_radius_factor for _ in cov_radii]

    # Centre the molecule
    centroid = positions.mean(axis=0) if len(positions) > 0 else np.zeros(3)
    positions = positions - centroid

    # Write atoms
    parts.append("\n// ── Atoms ──")
    for pos, z, r in zip(positions, z_list, radii, strict=True):
        parts.append(_atom_sphere(pos, z, radius=r))

    # Bonds: the viewer's connectivity. A bond through a periodic cell face
    # is drawn to the image it reaches, since the scene has coordinates.
    if style != "space_filling":
        parts.append("\n// ── Bonds ──")
        for bond in structure_bonds(sdata):
            start, end = bond_endpoints(positions, bond, sdata.lattice_vectors)
            parts.append(_bond_cylinder(start, end, radius=0.1))

    # ── Unit cell (periodic) ──
    if any(sdata.pbc) and sdata.lattice_vectors is not None:
        parts.append("\n// ── Unit cell ──")
        lattice = np.asarray(sdata.lattice_vectors)
        if lattice.shape == (3, 3):
            starts, ends = cell_edges(lattice, sdata.pbc)
            for start, end in zip(starts, ends, strict=True):
                parts.append(_bond_cylinder(start, end, radius=0.03, color="<0.8, 0.8, 0.8>"))

    result = "\n".join(parts)
    Path(output_path).write_text(result)
    return result


# ── CLI integration ─────────────────────────────────────────────────────


def export_from_qvf(
    qvf_path: str,
    output_dir: str = ".",
    style: str = "ball_and_stick",
    width: int = 1920,
    height: int = 1080,
) -> str:
    """Convenience: read a .qvf and export a .pov."""
    from vibeview.qvf import QVFReader

    # Owned here and closed on every exit (#39-18).
    with QVFReader(qvf_path) as reader:
        stem = Path(qvf_path).stem
        pov_path = Path(output_dir) / f"{stem}.pov"
        export_povray(reader, str(pov_path), style=style, width=width, height=height)
    return str(pov_path)
