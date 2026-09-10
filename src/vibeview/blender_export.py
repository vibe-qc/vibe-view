"""Blender scene export for vibe-view.

Generates a self-contained Python script that builds a molecular scene
in Blender (2.8+) when run via ``blender --python`` command line.

Usage:
    from vibeview.blender_export import export_blender_script

    # Generate the script:
    export_blender_script(reader, "molecule_scene.py", style="ball_and_stick")

    # Render in Blender:
    #   blender --background --python molecule_scene.py -o //output.png -f 1

    # Or open interactively:
    #   blender --python molecule_scene.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader


# ── CPK colours (R, G, B) ───────────────────────────────────────────────
CPK_COLORS: dict[int, tuple[float, float, float]] = {
    1: (1.000, 1.000, 1.000),  # H
    3: (0.800, 0.502, 0.992),  # Li
    6: (0.200, 0.200, 0.200),  # C
    7: (0.188, 0.314, 0.973),  # N
    8: (0.941, 0.000, 0.000),  # O
    9: (0.565, 0.878, 0.314),  # F
    11: (0.671, 0.361, 0.949),  # Na
    12: (0.541, 1.000, 0.000),  # Mg
    15: (1.000, 0.502, 0.000),  # P
    16: (1.000, 0.800, 0.200),  # S
    17: (0.122, 0.941, 0.122),  # Cl
    19: (0.561, 0.251, 0.831),  # K
    20: (0.239, 1.000, 0.000),  # Ca
    26: (0.878, 0.400, 0.200),  # Fe
    29: (0.784, 0.502, 0.200),  # Cu
    30: (0.490, 0.502, 0.690),  # Zn
    35: (0.651, 0.161, 0.161),  # Br
    53: (0.580, 0.000, 0.580),  # I
}

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

_ELEMENT_SYMBOLS = [
    "",
    "H",
    "He",
    "Li",
    "Be",
    "B",
    "C",
    "N",
    "O",
    "F",
    "Ne",
    "Na",
    "Mg",
    "Al",
    "Si",
    "P",
    "S",
    "Cl",
    "Ar",
    "K",
    "Ca",
    "Sc",
    "Ti",
    "V",
    "Cr",
    "Mn",
    "Fe",
    "Co",
    "Ni",
    "Cu",
    "Zn",
]

_Z_TO_SYMBOL = {i: s for i, s in enumerate(_ELEMENT_SYMBOLS) if s}
_SYMBOL_TO_Z = {s: i for i, s in enumerate(_ELEMENT_SYMBOLS) if s}


# ── Blender scene templates ─────────────────────────────────────────────


def _blender_header(style: str, width: int = 1920, height: int = 1080) -> str:
    """Generate the Blender Python script header (imports, render setup,
    lighting, material factory)."""
    return f'''"""vibe-view Blender scene — {style} style.

Run with: blender --python this_script.py
"""
import bpy
import math
import mathutils

# ── Clear existing scene ──
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

# ── Render settings ──
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.render.resolution_x = {width}
scene.render.resolution_y = {height}
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.image_settings.color_depth = '16'
scene.render.film_transparent = True

# Cycles settings
cycles = scene.cycles
cycles.samples = 256
cycles.max_bounces = 8
cycles.diffuse_bounces = 4
cycles.glossy_bounces = 4
cycles.transmission_bounces = 4
cycles.volume_bounces = 2
cycles.use_denoising = True

# ── World / background ──
world = bpy.data.worlds.new("vibe_view_world")
world.use_nodes = True
scene.world = world
bg = world.node_tree.nodes["Background"]
bg.inputs["Strength"].default_value = 0.5

# ── Camera ──
cam_data = bpy.data.cameras.new("vibe_view_camera")
cam_data.type = 'PERSP'
cam_data.lens = 50
cam = bpy.data.objects.new("vibe_view_camera", cam_data)
scene.collection.objects.link(cam)
cam.location = (15, -20, 8)
cam.rotation_euler = (math.radians(70), 0, math.radians(35))
scene.camera = cam

# ── Lighting (3-point) ──
def add_light(name, location, energy, color=(1.0, 1.0, 1.0)):
    light_data = bpy.data.lights.new(name=name, type='AREA')
    light_data.energy = energy
    light_data.color = color
    light_data.size = 5.0
    light = bpy.data.objects.new(name, light_data)
    scene.collection.objects.link(light)
    light.location = location
    return light

add_light("Key", (10, -10, 10), 500, (1.0, 0.98, 0.95))
add_light("Fill", (-8, -2, 5), 150, (0.6, 0.65, 0.7))
add_light("Rim", (0, 10, -5), 200, (0.7, 0.7, 0.8))

# ── Material factory ──
def create_material(name, base_color, roughness=0.3, metallic=0.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*base_color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Specular"].default_value = 0.5
    return mat

'''


_DATA_TEMPLATE = """
# ══ Embedded atom data (auto-generated, do not edit) ══
ATOM_DATA = {atom_json}
BOND_DATA = {bond_json}
Z_LIST = {z_json}
SYMBOLS = {symbols_json}
LATTICE_VECTORS = {lattice_json}
HAS_PBC = {pbc_json}
"""


def _blender_body(style: str) -> str:
    """Generate the body of the Blender script that creates atoms and bonds,
    then frames the camera."""
    radii_factor = {
        "space_filling": 1.0,
        "ball_and_stick": 0.4,
        "sticks_only": 0.15,
        "wireframe": 0.08,
    }.get(style, 0.4)

    bond_radius = 0.12 if style != "space_filling" else 0.0

    return f"""
# ── Parse embedded data ──
import json
atom_data = json.loads(ATOM_DATA)
bond_data = json.loads(BOND_DATA)
z_list = json.loads(Z_LIST)
symbols = json.loads(SYMBOLS)
lattice_vectors = json.loads(LATTICE_VECTORS)
pbc = json.loads(HAS_PBC)  # per-axis [a, b, c] periodicity, not a scalar flag

# ── Covalent radii for sizing ──
COVALENT_RADII = {{
    1: 0.31, 6: 0.76, 7: 0.71, 8: 0.66, 9: 0.57,
    15: 1.07, 16: 1.05, 17: 1.02, 35: 1.20, 53: 1.39,
}}

# ── CPK Colors ──
CPK_COLORS = {{
    1: (1.0, 1.0, 1.0), 3: (0.8, 0.502, 0.992),
    6: (0.2, 0.2, 0.2), 7: (0.188, 0.314, 0.973),
    8: (0.941, 0.0, 0.0), 9: (0.565, 0.878, 0.314),
    11: (0.671, 0.361, 0.949), 12: (0.541, 1.0, 0.0),
    15: (1.0, 0.502, 0.0), 16: (1.0, 0.8, 0.2),
    17: (0.122, 0.941, 0.122), 19: (0.561, 0.251, 0.831),
    20: (0.239, 1.0, 0.0), 26: (0.878, 0.4, 0.2),
    29: (0.784, 0.502, 0.2), 30: (0.49, 0.502, 0.69),
    35: (0.651, 0.161, 0.161), 53: (0.58, 0.0, 0.58),
}}

# ── Create atoms ──
atom_radius = {radii_factor}
for i, pos in enumerate(atom_data):
    z = z_list[i] if i < len(z_list) else 6
    radius = COVALENT_RADII.get(z, 0.8) * atom_radius
    if radius < 0.02:
        radius = 0.02

    # Create UV sphere
    bpy.ops.mesh.primitive_uv_sphere_add(
        radius=radius,
        location=pos,
        segments=32,
        ring_count=16,
    )
    obj = bpy.context.active_object
    obj.name = f"atom_{{i}}_{{symbols[i] if i < len(symbols) else 'X'}}"

    # Material
    rgb = CPK_COLORS.get(z, (0.7, 0.7, 0.7))
    mat = create_material(f"mat_atom_{{i}}", rgb, roughness=0.25, metallic=0.05)
    obj.data.materials.append(mat)

# ── Create bonds ──
if bond_data:
    bond_radius = {bond_radius}
    bond_mat = create_material("mat_bond", (0.4, 0.4, 0.4), roughness=0.4, metallic=0.1)

    for b in bond_data:
        i, j = b[0], b[1]
        if i >= len(atom_data) or j >= len(atom_data):
            continue
        p1 = mathutils.Vector(atom_data[i])
        p2 = mathutils.Vector(atom_data[j])
        mid = (p1 + p2) / 2
        direction = p2 - p1
        length = direction.length
        if length < 1e-6:
            continue

        # Create cylinder at origin pointing up
        bpy.ops.mesh.primitive_cylinder_add(
            radius=bond_radius,
            depth=length,
            location=mid,
            vertices=16,
        )
        obj = bpy.context.active_object
        obj.name = f"bond_{{i}}_{{j}}"
        obj.data.materials.append(bond_mat)

        # Align cylinder with bond axis
        direction.normalize()
        z_axis = mathutils.Vector((0, 0, 1))
        if abs(direction.dot(z_axis)) < 0.999:
            rot_axis = z_axis.cross(direction)
            rot_angle = math.acos(direction.dot(z_axis))
            obj.rotation_mode = 'AXIS_ANGLE'
            obj.rotation_axis_angle = (rot_angle, rot_axis[0], rot_axis[1], rot_axis[2])

# ── Unit cell (periodic structures) ──
# Only axes flagged periodic in `pbc` are real cell edges. For a 2D slab the
# third lattice column is a synthesized normal (non-physical) and is never
# drawn; a 3D crystal keeps the full 8-corner parallelepiped.
if lattice_vectors and any(pbc):
    vecs = [mathutils.Vector(lattice_vectors[i]) for i in range(3) if pbc[i]]
    n = len(vecs)

    corners = []
    for mask in range(1 << n):
        corner = mathutils.Vector((0.0, 0.0, 0.0))
        for k in range(n):
            if mask & (1 << k):
                corner = corner + vecs[k]
        corners.append(corner)

    # An edge joins two corners differing by exactly one periodic vector.
    edge_pairs = [
        (mask, mask | (1 << k))
        for mask in range(1 << n)
        for k in range(n)
        if not (mask & (1 << k))
    ]
    cell_mat = create_material("mat_cell", (0.8, 0.8, 0.8), roughness=0.5, metallic=0.05)

    for ei, (si, sj) in enumerate(edge_pairs):
        p1 = corners[si]
        p2 = corners[sj]
        mid = (p1 + p2) / 2
        direction = p2 - p1
        length = direction.length
        if length < 1e-6:
            continue

        bpy.ops.mesh.primitive_cylinder_add(
            radius=0.03,
            depth=length,
            location=mid,
            vertices=8,
        )
        obj = bpy.context.active_object
        obj.name = f"cell_edge_{{ei}}"
        obj.data.materials.append(cell_mat)

        direction.normalize()
        z_axis = mathutils.Vector((0, 0, 1))
        if abs(direction.dot(z_axis)) < 0.999:
            rot_axis = z_axis.cross(direction)
            rot_angle = math.acos(direction.dot(z_axis))
            obj.rotation_mode = 'AXIS_ANGLE'
            obj.rotation_axis_angle = (rot_angle, rot_axis[0], rot_axis[1], rot_axis[2])

# ── Frame camera on the molecule ──
if atom_data:
    min_x = min(p[0] for p in atom_data)
    max_x = max(p[0] for p in atom_data)
    min_y = min(p[1] for p in atom_data)
    max_y = max(p[1] for p in atom_data)
    min_z = min(p[2] for p in atom_data)
    max_z = max(p[2] for p in atom_data)
    center = mathutils.Vector((
        (min_x + max_x) / 2,
        (min_y + max_y) / 2,
        (min_z + max_z) / 2,
    ))
    size = max(max_x - min_x, max_y - min_y, max_z - min_z) * 1.5
    if size < 3:
        size = 3

    cam = scene.camera
    cam.location = center + mathutils.Vector((size * 0.8, -size * 1.2, size * 0.6))
    direction = center - cam.location
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()

print(f"Scene built: {{len(atom_data)}} atoms, {{len(bond_data)}} bonds")
print("Ready! Render with: bpy.ops.render.render(write_still=True)")
"""


# ── Public API ──────────────────────────────────────────────────────────


def export_blender_script(
    reader: "QVFReader",
    output_path: str,
    *,
    style: str = "ball_and_stick",
    width: int = 1920,
    height: int = 1080,
) -> str:
    """Export a Blender Python scene script from a QVF reader.

    Parameters
    ----------
    reader : QVFReader
        The QVF reader with the structure data.
    output_path : str
        Path for the output .py script.
    style : str
        One of ``"ball_and_stick"``, ``"space_filling"``,
        ``"sticks_only"``, ``"wireframe"``.
    width, height : int
        Output render resolution.

    Returns
    -------
    str
        The generated script content.
    """
    # Read structure
    try:
        sdata = reader.read_structure()
    except Exception as e:
        script = f'"""Blender scene — ERROR: {e}"""\n# No structure data available\n'
        Path(output_path).write_text(script)
        return script

    symbols = [a.symbol for a in sdata.atoms]
    positions = [list(a.position) for a in sdata.atoms]
    z_list = [a.atomic_number for a in sdata.atoms]

    # Centre the molecule
    if positions:
        centroid = np.mean(positions, axis=0)
        positions = (np.array(positions) - centroid).tolist()

    # Detect bonds (or use explicit ones)
    if style != "space_filling":
        if sdata.bonds is not None:
            bonds = [[b[0], b[1]] for b in sdata.bonds]
        else:
            bonds = _detect_bonds(positions, z_list)
    else:
        bonds = []

    # Lattice / PBC info. The full 3x3 is emitted even for dim<3 (grid and cube
    # extents still need it), but the per-axis `pbc` flags travel with it so the
    # generated script only draws the columns that are real cell edges.
    if sdata.lattice_vectors is not None and any(sdata.pbc):
        lattice = sdata.lattice_vectors.tolist()
        pbc = list(sdata.pbc)
    else:
        lattice = None
        pbc = [False, False, False]

    # Serialize as JSON for embedding into the generated script.
    # Constraint: the generated file must be valid *Python*. Raw json.dumps
    # output (e.g. ``null``, ``[false, ...]``) is not valid Python, and the
    # body calls json.loads() on these names — so each payload is embedded as
    # a Python *string literal* (double-encoded via json.dumps) that the body
    # then parses back with json.loads.
    parts = [
        _blender_header(style, width, height),
        _DATA_TEMPLATE.format(
            atom_json=json.dumps(json.dumps(positions)),
            bond_json=json.dumps(json.dumps(bonds)),
            z_json=json.dumps(json.dumps(z_list)),
            symbols_json=json.dumps(json.dumps(symbols)),
            lattice_json=json.dumps(json.dumps(lattice)),
            pbc_json=json.dumps(json.dumps(pbc)),
        ),
        _blender_body(style),
    ]

    script = "\n".join(parts)
    Path(output_path).write_text(script)
    return script


def _detect_bonds(
    positions: list[list[float]],
    z_list: list[int],
    tolerance: float = 1.2,
) -> list[list[int]]:
    """Detect bonds between atoms based on covalent radii."""
    pos = np.asarray(positions, dtype=float)
    radii = [_COVALENT_RADII.get(z, 0.8) for z in z_list]
    n = len(z_list)
    bonds = []
    for i in range(n):
        for j in range(i + 1, n):
            d = float(np.linalg.norm(pos[i] - pos[j]))
            threshold = (radii[i] + radii[j]) * tolerance
            if 0.4 < d < threshold:
                bonds.append([i, j])
    return bonds


# ── Convenience wrapper ─────────────────────────────────────────────────


def export_from_qvf(
    qvf_path: str,
    output_dir: str = ".",
    style: str = "ball_and_stick",
) -> str:
    """Convenience: read a .qvf and export a Blender scene script.

    Parameters
    ----------
    qvf_path : str
        Path to the .qvf archive.
    output_dir : str
        Directory for the output .py script.
    style : str
        One of ``"ball_and_stick"``, ``"space_filling"``,
        ``"sticks_only"``, ``"wireframe"``.

    Returns
    -------
    str
        Path to the generated .py script.
    """
    from vibeview.qvf import QVFReader

    reader = QVFReader(qvf_path)
    stem = Path(qvf_path).stem
    py_path = Path(output_dir) / f"{stem}_blender.py"
    export_blender_script(reader, str(py_path), style=style)
    return str(py_path)
