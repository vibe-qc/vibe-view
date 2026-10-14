"""Test the standalone HTML export module."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.elements import COVALENT_RADII
from vibeview.qvf_writer import structure_archive


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_structure_qvf() -> Path:
    structure = json.dumps(
        {
            "atoms": [
                {"symbol": "O", "position": [0, 0, 0], "atomic_number": 8},
                {"symbol": "H", "position": [0, 0.757, 0.587], "atomic_number": 1},
                {"symbol": "H", "position": [0, -0.757, 0.587], "atomic_number": 1},
            ],
            "pbc": [False, False, False],
        }
    ).encode()
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "test"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "sections/s.json",
                        "format": "json",
                        "sha256": _sha256(structure),
                    }
                },
            }
        ],
    }
    with (
        tempfile.NamedTemporaryFile(suffix=".qvf", delete=False) as tmp,
        zipfile.ZipFile(tmp, "w") as zf,
    ):
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("sections/s.json", structure)
    return Path(tmp.name)


class TestExportHTML:
    def test_export_html_produces_valid_file(self) -> None:
        from vibeview.export_html import export_html
        from vibeview.qvf import QVFReader

        q = _make_structure_qvf()
        try:
            reader = QVFReader(q)
            out = q.parent / "test.html"
            result = export_html(reader, out)
            assert result.exists()
            html = result.read_text()
            assert "<!DOCTYPE html>" in html
            assert "three" in html.lower()
            assert "OrbitControls" in html
            assert "O" in html  # oxygen atom
            reader.close()
        finally:
            q.unlink()
            out.unlink(missing_ok=True)

    def test_atom_radius_uses_element_table(self) -> None:
        """Regression: atom sphere radius was hardcoded (z===1?0.25:0.4);
        it must derive from the per-element covalent radii table."""
        from vibeview.export_html import _HTML_TEMPLATE

        # Old hardcoded expression must be gone.
        assert "z === 1 ? 0.25 : 0.4" not in _HTML_TEMPLATE
        # Radius now comes from the shared per-element radii table.
        assert "radii[z]" in _HTML_TEMPLATE
        assert "atomScale" in _HTML_TEMPLATE

    def test_export_html_custom_title(self) -> None:
        from vibeview.export_html import export_html
        from vibeview.qvf import QVFReader

        q = _make_structure_qvf()
        try:
            reader = QVFReader(q)
            out = q.parent / "test2.html"
            export_html(reader, out, title="My Molecule")
            html = out.read_text()
            assert "My Molecule" in html
            reader.close()
        finally:
            q.unlink()
            out.unlink(missing_ok=True)

    def test_export_html_with_bonds(self) -> None:
        """H2O has O-H bonds that should be detected."""
        from vibeview.export_html import export_html
        from vibeview.qvf import QVFReader

        q = _make_structure_qvf()
        try:
            reader = QVFReader(q)
            out = q.parent / "test3.html"
            export_html(reader, out)
            html = out.read_text()
            # Should contain bond cylinder rendering code
            assert "CylinderGeometry" in html or "cylGeo" in html.lower()
            reader.close()
        finally:
            q.unlink()
            out.unlink(missing_ok=True)


class TestExportHTMLExtended:
    def test_export_html_import(self):
        from vibeview.export_html import export_html

        assert callable(export_html)

    def test_export_html_creates_file(self, sample_qvf, tmp_path):
        from vibeview.export_html import export_html
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        out = tmp_path / "test.html"
        export_html(reader, out)
        assert out.exists()
        reader.close()


# Execute the exported page with a recording renderer. Geometry and camera
# state are retained without requiring WebGL, a browser, or CDN access.
_VIEW_PROBE = r"""
const fs = require('node:fs');
const vm = require('node:vm');
class Vector3 {
  constructor(x=0, y=0, z=0) { this.set(x, y, z); }
  set(x, y, z) { this.x=x; this.y=y; this.z=z; return this; }
  setScalar(s) { return this.set(s, s, s); }
  copy(v) { return this.set(v.x, v.y, v.z); }
  add(v) { return this.set(this.x+v.x, this.y+v.y, this.z+v.z); }
  multiplyScalar(s) { return this.set(this.x*s, this.y*s, this.z*s); }
  normalize() { return this.multiplyScalar(1/Math.hypot(this.x, this.y, this.z)); }
  toArray() { return [this.x, this.y, this.z]; }
}
class Scene {
  constructor() { this.children=[]; }
  add(object) { this.children.push(object); }
}
class Mesh {
  constructor(geometry) {
    this.geometry=geometry; this.position=new Vector3(); this.scale=new Vector3(1,1,1);
  }
  setRotationFromQuaternion(q) { this.axis=q.axis; }
}
class SphereGeometry { constructor(radius) { this.radius=radius; } }
class CylinderGeometry { constructor(top, bottom, height) { this.radius=top; this.height=height; } }
class Quaternion { setFromUnitVectors(from, to) { this.axis=to; } }
class Sphere { constructor() { this.center=new Vector3(); this.radius=0; } }
class Box3 {
  constructor() { this.min=[Infinity,Infinity,Infinity]; this.max=[-Infinity,-Infinity,-Infinity]; }
  setFromObject(scene) {
    for (const mesh of scene.children.filter(m => m.geometry)) {
      const p=mesh.position.toArray(), s=mesh.scale.toArray(), g=mesh.geometry;
      const extent = g.height === undefined ? s.map(v => v*g.radius) :
        mesh.axis.toArray().map(v => Math.abs(v)*g.height*s[1]/2 + g.radius);
      for (let i=0; i<3; i++) {
        this.min[i]=Math.min(this.min[i], p[i]-extent[i]);
        this.max[i]=Math.max(this.max[i], p[i]+extent[i]);
      }
    }
    return this;
  }
  isEmpty() { return this.min[0]>this.max[0]; }
  getBoundingSphere(sphere) {
    sphere.center.set(...this.min.map((v,i) => (v+this.max[i])/2));
    sphere.radius=Math.hypot(...this.max.map((v,i) => (v-this.min[i])/2));
    return sphere;
  }
}
class PerspectiveCamera {
  constructor(fov, aspect, near, far) {
    Object.assign(this, {fov,aspect,near,far}); this.position=new Vector3();
  }
  lookAt(x,y,z) { this.target=x instanceof Vector3 ? x.toArray() : [x,y,z]; }
  updateProjectionMatrix() {}
}
class OrbitControls {
  constructor() { this.target=new Vector3(); }
  update() {}
}
class WebGLRenderer {
  constructor() { this.domElement={}; }
  setSize() {}
  setPixelRatio() {}
  render() {}
}
class Light { constructor() { this.position=new Vector3(); } }
const page=fs.readFileSync(process.argv[1], 'utf8');
let script=page.match(/<script type="module">([\s\S]*?)<\/script>/)[1];
script=script.replace(/^import .*;$/gm, '');
script += `\nconsole.log(JSON.stringify({position:camera.position.toArray(),
  target:camera.target, orbitTarget:controls.target.toArray(),
  near:camera.near, far:camera.far, fov:camera.fov, aspect:camera.aspect, data}));`;
vm.runInNewContext(script, {
  THREE:{Vector3,Scene,Mesh,SphereGeometry,CylinderGeometry,Quaternion,Sphere,Box3,
    PerspectiveCamera,WebGLRenderer,MeshPhongMaterial:class {},Color:class {},
    AmbientLight:Light,DirectionalLight:Light,MathUtils:{degToRad:d => d*Math.PI/180}},
  OrbitControls, console, requestAnimationFrame:() => {},
  window:{innerWidth:Number(process.argv[2]),innerHeight:Number(process.argv[3]),
    devicePixelRatio:1,addEventListener:() => {}},
  document:{body:{appendChild:() => {}}}
});
"""


@pytest.mark.parametrize("shape", ["translated", "large", "single", "periodic", "origin", "empty"])
@pytest.mark.parametrize("size", [(1600, 900), (400, 1000)], ids=["landscape", "portrait"])
def test_html_export_frames_the_entire_structure(tmp_path, shape, size):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the standalone HTML viewer script")
    positions = {
        "translated": [[100, -200, 300], [101, -200, 300]],
        "large": [[-250, 0, 0], [250, 0, 0]],
        "single": [[10, 20, 30]],
        "periodic": [[0.1, 0, 0], [9.8, 0, 0]],
        "origin": [[0, 0, 0]],
        "empty": [],
    }[shape]
    periodic = shape == "periodic"
    atoms = [{"symbol": "H", "atomic_number": 1, "position": p} for p in positions]
    source = structure_archive(
        atoms, [periodic] * 3, np.eye(3) * 10 if periodic else None,
        bonds=None if periodic else [],
    ).write(tmp_path / "structure.qvf")
    output = tmp_path / "structure.html"
    result = CliRunner().invoke(main, ["export", str(source), "-f", "html", "-o", str(output)])
    assert result.exit_code == 0, result.output
    probe = subprocess.run(
        [node, "-e", _VIEW_PROBE, str(output), *map(str, size)],
        capture_output=True, text=True, timeout=10, check=True,
    )
    view = json.loads(probe.stdout)
    np.testing.assert_allclose([a["position"] for a in view["data"]["atoms"]], positions)
    np.testing.assert_allclose(view["target"], view["orbitTarget"])
    assert np.isfinite([*view["position"], *view["target"], view["near"], view["far"]]).all()
    assert 0 < view["near"] < view["far"]
    if not positions:
        return

    # Project all atom sphere bounds and the periodic image endpoint through
    # the emitted camera. This checks visibility, not a particular fit formula.
    points = []
    offsets = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)])
    for position in positions:
        points.extend(np.asarray(position) + offsets * COVALENT_RADII[1] * 0.4)
    for bond in view["data"]["bonds"]:
        if len(bond) == 3:
            points.extend(np.asarray(bond[2]) + offsets * 0.08)
    forward = np.asarray(view["target"], dtype=float) - view["position"]
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0, 1, 0])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    relative = np.asarray(points) - view["position"]
    depth = relative @ forward
    assert np.all(depth > view["near"])
    assert np.all(depth < view["far"])
    half_height = depth * np.tan(np.deg2rad(view["fov"] / 2))
    assert np.all(np.abs(relative @ up) < half_height)
    assert np.all(np.abs(relative @ right) < half_height * view["aspect"])
