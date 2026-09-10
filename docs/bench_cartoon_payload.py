"""M5 go/no-go: is a cartoon/ribbon renderer viable on Trame/vtk.js?

The roadmap flags workstream D as "a big, latency-sensitive rendering
piece on Trame; benchmark on a few-thousand-atom protein early", and
§11 makes it an explicit gate. The binding constraint is not server
render time — it is the scene payload VtkLocalView serialises and
pushes over the websocket, because the client draws it.

Measures, on a REAL protein (cp2k's ClC benchmark, 885 residues):
  * the shipped cartoon mesh — the extruded ribbon, built through the
    renderer's own frame + extrusion helpers so the number tracks what
    users actually receive
  * the spline tube it replaced, for the before/after
  * ball-and-stick of the same protein for comparison
  * the serialised scene size each produces

Nothing here is triangulated before measuring. Both the ribbon and the
tube emit triangle strips, and triangulating a copy first inflates the
payload by ~1.8x — earlier runs of this script did that, which is why
the roadmap once carried 1.06 MB for a mesh that goes over the wire at
0.60 MB.
"""
import sys
import time

import numpy as np
import pyvista as pv

if len(sys.argv) < 2:
    sys.exit(
        "usage: bench_cartoon_payload.py <protein.pdb>\n"
        "  any PDB with a CA trace works; the numbers in the roadmap came\n"
        "  from cp2k's QMMM_ClC benchmark (885 residues)."
    )
PDB = sys.argv[1]


def ca_trace(path):
    pts, chains = [], []
    with open(path) as fh:
        lines = fh.readlines()
    for line in lines:
        if line.startswith("ATOM") and line[12:16] == " CA ":
            pts.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
            chains.append(line[21])
    return np.array(pts, float), chains


def protein_atoms(path, limit=None):
    pts = []
    with open(path) as fh:
        lines = fh.readlines()
    for line in lines:
        if line.startswith("ATOM"):
            pts.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
            if limit and len(pts) >= limit:
                break
    return np.array(pts, float)


def scene_bytes(plotter):
    """Bytes the client must receive for this scene.

    Uses trame-vtk's own mesh serializer (the one VtkLocalView pushes),
    summed over every actor, so this is the real wire payload rather
    than an estimate. Buffers travel as binary attachments, so count
    those raw rather than as JSON text.
    """
    import json as _json

    from trame_vtk.modules.vtk.serializers.mesh import mesh as vtk_mesh

    total = 0
    for actor in plotter.actors.values():
        try:
            mapper = actor.GetMapper()
            # Update() first: an actor added with scalars=..., rgb=True has
            # an ActiveScalarsAlgorithm spliced into its mapper input, and
            # that output is empty until the pipeline runs. Without this an
            # rgb-coloured ribbon measures 0.00 MB.
            mapper.Update()
            dataset = mapper.GetInput()
        except Exception:
            continue
        if dataset is None:
            continue
        payload = vtk_mesh(dataset)
        if payload is None:
            continue
        total += len(_json.dumps(payload, default=str).encode())
    return total


def tri_count(plotter):
    """Triangles, counted on a triangulated copy.

    Raw ``n_cells`` is not the triangle count for a strip mesh: a whole
    ribbon face is one cell. Triangulate to compare with glyph meshes,
    but never serialise the triangulated copy (see the module docstring).
    """
    import contextlib

    n = 0
    for a in plotter.actors.values():
        with contextlib.suppress(Exception):
            a.GetMapper().Update()  # see scene_bytes
            n += a.mapper.dataset.extract_surface().triangulate().n_cells
    return n


# warm the serializer so the first measurement is not import-dominated
_w = pv.Plotter(off_screen=True)
_w.add_mesh(pv.Sphere())
scene_bytes(_w)

print(f"protein: {PDB}")
ca, chains = ca_trace(PDB)
print(f"  Ca trace: {len(ca)} residues, {len(set(chains))} chain(s)")

def _chain_selections():
    for ch in sorted(set(chains)):
        sel = np.array([i for i, c in enumerate(chains) if c == ch])
        if len(sel) >= 4:
            yield ch, sel


# ── the shipped cartoon: extruded ribbon through the Ca trace, per chain ──
from vibeview.renderers.structure import (  # noqa: E402
    _CARTOON_SAMPLES_PER_RESIDUE,
    _CARTOON_SIDES,
    _cartoon_ribbon_frames,
    _extrude_ribbon,
)

t0 = time.time()
pl = pv.Plotter(off_screen=True)
total_pts = 0
for _ch, sel in _chain_selections():
    n = max(len(sel) * _CARTOON_SAMPLES_PER_RESIDUE, 16)
    pts = np.asarray(pv.Spline(ca[sel], n).points, dtype=float)
    side, normal = _cartoon_ribbon_frames(ca[sel], pts)
    # Helix width/thickness throughout: the widest profile the renderer
    # ever uses, so this is the payload ceiling rather than an average.
    mesh = _extrude_ribbon(pts, side, normal, np.full(n, 1.10), np.full(n, 0.20))
    pl.add_mesh(mesh, color="#4363d8", smooth_shading=True)
    total_pts += mesh.n_points
build_cartoon = time.time() - t0
tri_cartoon = tri_count(pl)
t0 = time.time()
bytes_cartoon = scene_bytes(pl)
ser_cartoon = time.time() - t0
print(f"\nCARTOON (extruded ribbon, {len(ca)} Ca):")
print(f"  build {build_cartoon:5.2f}s | {tri_cartoon:,} tris | {total_pts:,} pts | "
      f"wire {bytes_cartoon/1e6:6.2f} MB | serialise {ser_cartoon:5.2f}s")

# ── the spline tube it replaced, same sampling ──
t0 = time.time()
pl_tube = pv.Plotter(off_screen=True)
tube_pts = 0
for _ch, sel in _chain_selections():
    poly = pv.Spline(ca[sel], max(len(sel) * _CARTOON_SAMPLES_PER_RESIDUE, 16))
    tube = poly.tube(radius=0.8, n_sides=_CARTOON_SIDES)
    pl_tube.add_mesh(tube, color="#4363d8", smooth_shading=True)
    tube_pts += tube.n_points
build_tube = time.time() - t0
bytes_tube = scene_bytes(pl_tube)
print("CARTOON (old spline tube):")
print(f"  build {build_tube:5.2f}s | {tri_count(pl_tube):,} tris | {tube_pts:,} pts | "
      f"wire {bytes_tube/1e6:6.2f} MB")

# ── ball-and-stick comparison at protein scale ──
for n in (1000, 3000, 8000):
    atoms = protein_atoms(PDB, limit=n)
    if len(atoms) < n:
        break
    t0 = time.time()
    p2 = pv.Plotter(off_screen=True)
    glyph = pv.PolyData(atoms).glyph(
        geom=pv.Sphere(radius=0.4, theta_resolution=8, phi_resolution=6),
        scale=False, orient=False)
    p2.add_mesh(glyph, color="#909090")
    build = time.time() - t0
    t0 = time.time()
    sb = scene_bytes(p2)
    ser = time.time() - t0
    print(f"BALL&STICK {n:>5} atoms: build {build:5.2f}s | "
          f"{glyph.n_cells:,} cells | wire {sb/1e6:6.2f} MB | serialise {ser:5.2f}s")
