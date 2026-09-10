"""Standalone HTML export — self-contained 3D viewer for QVF files.

Generates a single .html file that renders the structure with Three.js
loaded from CDN.  No installation, no server, no Python — just open the
file in any modern browser.
"""

from __future__ import annotations

import json
from pathlib import Path

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

// CPK colours (Jmol)
const cpk = {{
  1: 0xffffff, 2: 0xd9ffff, 3: 0xcc80ff, 4: 0xc2ff00, 5: 0xffb5b5,
  6: 0x909090, 7: 0x3050f8, 8: 0xff0d0d, 9: 0x90e050, 10: 0xb3e3f5,
  11: 0xab5cf2, 12: 0x8aff00, 13: 0xbfa6a6, 14: 0xf0c8a0, 15: 0xff8000,
  16: 0xffff30, 17: 0x1ff01f, 18: 0x80d1e3, 19: 0x8f40d4, 20: 0x3dff00,
  21: 0xe6e6e6, 22: 0xbfc2c7, 23: 0xa6a6ab, 24: 0x8a99c7, 25: 0x9c7ac7,
  26: 0xe06633, 27: 0xf090a0, 28: 0x50d050, 29: 0xc88033, 30: 0x7d80b0,
  31: 0xc28f8f, 32: 0x668f8f, 33: 0xbd80e3, 34: 0xffa100, 35: 0xa62929,
  36: 0x5cb8d1, 53: 0x940094, 79: 0xffd123,
}};

// Covalent radii for bond detection (Angstrom) — extended set
const radii = {{
  1: 0.31, 2: 0.28, 3: 1.28, 4: 0.96, 5: 0.84, 6: 0.76, 7: 0.71, 8: 0.66,
  9: 0.57, 10: 0.58, 11: 1.66, 12: 1.41, 13: 1.21, 14: 1.11, 15: 1.07,
  16: 1.05, 17: 1.02, 18: 1.06, 19: 2.03, 20: 1.76, 21: 1.70, 22: 1.60,
  23: 1.53, 24: 1.39, 25: 1.39, 26: 1.32, 27: 1.26, 28: 1.24, 29: 1.32,
  30: 1.22, 31: 1.22, 32: 1.20, 33: 1.19, 34: 1.20, 35: 1.20, 36: 1.16,
  46: 1.39, 47: 1.45, 48: 1.44, 53: 1.39, 78: 1.36, 79: 1.36, 80: 1.32,
  82: 1.46,
}};

const bondCutoff = 1.2; // tolerance factor

// Atom spheres
const sphereGeo = new THREE.SphereGeometry(1, 24, 24);
const atomPositions = [];
// Atom display radius from the same per-element covalent radii used for
// bond detection (ball-and-stick fraction), instead of a flat H/other split.
const atomScale = 0.4; // covalent-radius fraction for ball-and-stick spheres
data.atoms.forEach((a, i) => {{
  const pos = a.position;
  const z = a.atomic_number || 0;
  const r = (radii[z] || 0.7) * atomScale;
  const color = cpk[z] || 0x888888;
  const mat = new THREE.MeshPhongMaterial({{ color, specular: 0x222222, shininess: 30 }});
  const mesh = new THREE.Mesh(sphereGeo, mat);
  mesh.position.set(pos[0], pos[1], pos[2]);
  mesh.scale.setScalar(r);
  scene.add(mesh);
  atomPositions.push({{ x: pos[0], y: pos[1], z: pos[2], zNum: z, symbol: a.symbol, idx: i }});
}});

// Bonds
const bondMat = new THREE.MeshPhongMaterial({{ color: 0x666666, specular: 0x111111 }});
const cylGeo = new THREE.CylinderGeometry(0.08, 0.08, 1, 8);
for (let i = 0; i < atomPositions.length; i++) {{
  for (let j = i + 1; j < atomPositions.length; j++) {{
    const a = atomPositions[i], b = atomPositions[j];
    const dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
    const dist = Math.sqrt(dx*dx + dy*dy + dz*dz);
    const ra = radii[a.zNum] || 0.7, rb = radii[b.zNum] || 0.7;
    const cut = (ra + rb) * bondCutoff;
    if (dist < cut && dist > 1e-6) {{
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
    }}
  }}
}}

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


def export_html(reader, path: Path, *, title: str | None = None) -> Path:
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

    src = reader.manifest.source
    page_title = title or f"{src.calculation}"
    html = _HTML_TEMPLATE.format(
        title=page_title,
        program=src.program,
        version=src.version,
        calculation=src.calculation,
        n_atoms=len(atoms_out),
        data_json=json.dumps({"atoms": atoms_out}),
    )
    path.write_text(html)
    return path
