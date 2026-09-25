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
from vibeview.renderers.cell import cell_edges

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader


# ── CPK colours (R, G, B) ───────────────────────────────────────────────
CPK_COLORS: dict[int, tuple[float, float, float]] = {
    1: (0.800, 0.800, 0.800),  # H - white
    2: (0.851, 0.925, 0.992),  # He
    3: (0.800, 0.502, 0.992),  # Li
    4: (0.761, 0.000, 0.000),  # Be
    5: (1.000, 0.710, 0.710),  # B
    6: (0.200, 0.200, 0.200),  # C - dark grey
    7: (0.188, 0.314, 0.973),  # N - blue
    8: (0.941, 0.000, 0.000),  # O - red
    9: (0.565, 0.878, 0.314),  # F
    10: (0.702, 0.890, 0.961),  # Ne
    11: (0.671, 0.361, 0.949),  # Na
    12: (0.541, 1.000, 0.000),  # Mg
    13: (0.749, 0.651, 0.651),  # Al
    14: (0.941, 0.784, 0.627),  # Si
    15: (1.000, 0.502, 0.000),  # P
    16: (1.000, 0.800, 0.200),  # S - yellow
    17: (0.122, 0.941, 0.122),  # Cl - green
    18: (0.502, 0.820, 0.890),  # Ar
    19: (0.561, 0.251, 0.831),  # K
    20: (0.239, 1.000, 0.000),  # Ca
    26: (0.878, 0.400, 0.200),  # Fe
    27: (0.941, 0.565, 0.627),  # Co
    28: (0.314, 0.816, 0.314),  # Ni
    29: (0.784, 0.502, 0.200),  # Cu
    30: (0.490, 0.502, 0.690),  # Zn
    35: (0.651, 0.161, 0.161),  # Br
    47: (0.753, 0.753, 0.753),  # Ag
    53: (0.580, 0.000, 0.580),  # I
    78: (0.816, 0.816, 0.878),  # Pt
    79: (1.000, 0.820, 0.137),  # Au
    80: (0.722, 0.722, 0.816),  # Hg
}


def _cpk_rgb(z: int) -> str:
    """Return POV-Ray colour vector string for atomic number z."""
    r, g, b = CPK_COLORS.get(z, (0.7, 0.7, 0.7))
    return f"<{r:.3f}, {g:.3f}, {b:.3f}>"


# ── Covalent radii for bond detection ──────────────────────────────────
_COVALENT_RADII: dict[int, float] = {
    1: 0.31,
    6: 0.76,
    7: 0.71,
    8: 0.66,
    9: 0.57,
    15: 1.07,
    16: 1.05,
    17: 1.02,
    35: 1.20,
    53: 1.39,
}

# van der Waals radii (Angstrom) keyed by atomic number, for space-filling
# display. Bondi, J. Phys. Chem. 68, 441 (1964) for the common elements;
# others fall back to 1.75x the covalent radius (see _vdw_radius).
_VDW_RADII: dict[int, float] = {
    1: 1.2,  # H
    6: 1.7,  # C
    7: 1.55,  # N
    8: 1.52,  # O
    9: 1.47,  # F
    15: 1.8,  # P
    16: 1.8,  # S
    17: 1.75,  # Cl
}


def _vdw_radius(z: int) -> float:
    """vdW radius for atomic number ``z``; 1.75x covalent as fallback."""
    return _VDW_RADII.get(z, _COVALENT_RADII.get(z, 0.8) * 1.75)


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
    cov_radii = [_COVALENT_RADII.get(z, 0.8) for z in z_list]

    if style == "space_filling":
        # True vdW radii for space-filling (was incorrectly using covalent).
        radii = [_vdw_radius(z) * atom_radius_factor for z in z_list]
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

    reader = QVFReader(qvf_path)
    stem = Path(qvf_path).stem
    pov_path = Path(output_dir) / f"{stem}.pov"
    export_povray(reader, str(pov_path), style=style, width=width, height=height)
    return str(pov_path)
