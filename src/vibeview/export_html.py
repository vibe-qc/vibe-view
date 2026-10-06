"""Standalone HTML export — self-contained 3D viewer for QVF files.

Generates a single .html file that renders the structure with Three.js
loaded from CDN.  No installation, no server, no Python — just open the
file in any modern browser.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from vibeview.bonds import bond_endpoints, structure_bonds
from vibeview.elements import COVALENT_RADII, CPK_HEX, cpk_hex

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>vibe-view — {title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #1a1a2e; overflow: hidden; font-family: sans-serif; }}
  canvas {{ display: block; }}
  #info {{
    position: absolute; top: 12px; left: 16px;
    color: #e0e0e0; font-size: 14px;
    background: rgba(26,26,46,0.85); padding: 8px 14px; border-radius: 6px;
  }}
  #info .title {{ color: #7c8aff; font-weight: 600; }}
  #info .detail {{ color: #888; font-size: 11px; margin-top: 2px; }}
  #legend {{
    position: absolute; bottom: 12px; left: 16px;
    color: #888; font-size: 11px;
    background: rgba(26,26,46,0.85); padding: 6px 10px; border-radius: 4px;
  }}
  .atom-label {{
    position: absolute; color: #ccc; font-size: 9px;
    pointer-events: none; transform: translate(-50%, -50%);
    text-shadow: 0 0 3px #000;
  }}
</style>
</head>
<body>
<div id="info">
  <div class="title">{title}</div>
  <div class="detail">{program} {version} — {calculation}<br>{n_atoms} atoms</div>
</div>
<div id="legend">🖱 drag to rotate · scroll to zoom · right-drag to pan</div>

<script type="importmap">
{{ "imports": {{ "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
  "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/" }} }}
</script>

<script type="module">
import * as THREE from 'three';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';

const data = {data_json};

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1a2e);

const camera = new THREE.PerspectiveCamera(45, window.innerWidth/window.innerHeight, 0.1, 100);
camera.position.set(6, 4, 8);
camera.lookAt(0, 0, 0);

const renderer = new THREE.WebGLRenderer({{ antialias: true }});
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
document.body.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.target.set(0, 0, 0);
controls.update();

// Lights
scene.add(new THREE.AmbientLight(0x404060, 2.5));
const dir = new THREE.DirectionalLight(0xffffff, 1.2);
dir.position.set(5, 10, 7);
scene.add(dir);
const dir2 = new THREE.DirectionalLight(0x8080ff, 0.5);
dir2.position.set(-3, -1, -5);
scene.add(dir2);

// CPK colours: vibe-view's table, for the elements present
const cpk = {cpk_json};

// Covalent radii for atom display sizes (Angstrom); connectivity comes
// Covalent radii for atom display sizes (Angstrom), vibe-view's table;
// connectivity comes precomputed in data.bonds from the same rule the viewer
// draws with.
const radii = {radii_json};

// Atom spheres
const sphereGeo = new THREE.SphereGeometry(1, 24, 24);
const atomMats = {{}};
const atomPositions = [];
// Atom display radius from the same per-element covalent radii used for
// bond detection (ball-and-stick fraction), instead of a flat H/other split.
const atomScale = 0.4; // covalent-radius fraction for ball-and-stick spheres
data.atoms.forEach((a, i) => {{
  const pos = a.position;
  const z = a.atomic_number || 0;
  const r = (radii[z] || 0.7) * atomScale;
  const color = cpk[z] || 0x888888;
  // One material per element rather than one per atom.
  if (!atomMats[z]) {{
    atomMats[z] = new THREE.MeshPhongMaterial({{ color, specular: 0x222222, shininess: 30 }});
  }}
  const mat = atomMats[z];
  const mesh = new THREE.Mesh(sphereGeo, mat);
  mesh.position.set(pos[0], pos[1], pos[2]);
  mesh.scale.setScalar(r);
  scene.add(mesh);
  atomPositions.push({{ x: pos[0], y: pos[1], z: pos[2], zNum: z, symbol: a.symbol, idx: i }});
}});

// Bonds: [i, j] pairs computed by vibe-view (explicit bonds from the
// archive when present, otherwise the viewer's covalent-radius inference);
// a bond through a periodic cell face adds the [x, y, z] end it reaches.
const bondMat = new THREE.MeshPhongMaterial({{ color: 0x666666, specular: 0x111111 }});
const cylGeo = new THREE.CylinderGeometry(0.08, 0.08, 1, 8);
data.bonds.forEach(([i, j, end]) => {{
  const a = atomPositions[i];
  const b = end ? {{ x: end[0], y: end[1], z: end[2] }} : atomPositions[j];
  const dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
  const dist = Math.sqrt(dx*dx + dy*dy + dz*dz);
  if (dist < 1e-6) return;
  const mid = new THREE.Vector3((a.x+b.x)/2, (a.y+b.y)/2, (a.z+b.z)/2);
  const dir = new THREE.Vector3(dx, dy, dz).normalize();
  const cyl = new THREE.Mesh(cylGeo, bondMat);
  cyl.position.copy(mid);
  cyl.scale.set(1, dist, 1);
  // Align cylinder with bond direction
  const quat = new THREE.Quaternion();
  quat.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
  cyl.setRotationFromQuaternion(quat);
  scene.add(cyl);
}});

function animate() {{
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}}
animate();

window.addEventListener('resize', () => {{
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}});
</script>
</body>
</html>"""


def export_html(reader: QVFReader, path: Path, *, title: str | None = None) -> Path:
    """Export a QVF to a standalone HTML file with embedded Three.js viewer.

    Parameters
    ----------
    reader : QVFReader
        An open QVF reader.
    path : Path
        Output .html file path.
    title : str, optional
        Override the page title.

    Returns
    -------
    Path
        The written file path.
    """
    from vibeview.qvf import QVFReader

    if not isinstance(reader, QVFReader):
        raise TypeError("export_html requires a QVFReader instance")

    sdata = reader.read_structure()
    atoms_out = []
    for a in sdata.atoms:
        atoms_out.append(
            {
                "symbol": a.symbol,
                "atomic_number": a.atomic_number or 0,
                "position": [float(a.position[0]), float(a.position[1]), float(a.position[2])],
            }
        )

    # The viewer's connectivity, precomputed here so the page does not run an
    # O(N^2) loop in the browser. An in-cell bond is ``[i, j]``; a bond through
    # a periodic cell face carries the Cartesian end it reaches, ``[i, j,
    # [x, y, z]]``, and is drawn to that image as the viewer draws it (#40-19).
    positions = np.asarray([a.position for a in sdata.atoms], dtype=float).reshape(-1, 3)
    bonds_out: list[list[object]] = []
    for bond in structure_bonds(sdata):
        i, j, _order, image = bond
        if any(image):
            _start, end = bond_endpoints(positions, bond, sdata.lattice_vectors)
            bonds_out.append([i, j, [float(v) for v in end]])
        else:
            bonds_out.append([i, j])
    src = reader.manifest.source
    page_title = title or f"{src.calculation}"
    present = sorted({int(a.atomic_number or 0) for a in sdata.atoms})
    html = _HTML_TEMPLATE.format(
        title=page_title,
        program=src.program,
        version=src.version,
        calculation=src.calculation,
        n_atoms=len(atoms_out),
        data_json=json.dumps({"atoms": atoms_out, "bonds": bonds_out}),
        # The page carries only the rows for the elements present, from the
        # viewer's own tables.
        cpk_json=json.dumps(
            {z: int(cpk_hex(z).lstrip("#"), 16) for z in present if z in CPK_HEX}
        ),
        radii_json=json.dumps({z: COVALENT_RADII[z] for z in present if z in COVALENT_RADII}),
    )
    path.write_text(html)
    return path
