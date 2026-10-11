"""Go/no-go for direct volume rendering on Trame/vtk.js: what does it cost?

The roadmap's "Transfer functions and volume rendering" proposal says to
measure before building, as the cartoon renderer was
(``bench_cartoon_payload.py``). The binding constraint is the scene payload
VtkLocalView serialises and pushes over the websocket, because the client
draws it. Today an orbital crosses the wire as an isosurface mesh. Direct
volume rendering must send the scalar grid itself, which grows with N**3
where a surface grows with N**2.

Measures, on a synthetic two-lobe orbital-like field at several grid sizes:
  * the isosurface mesh the viewer ships today (both lobes), as the baseline
  * the volume scene: vtkImageData + vtkVolume mapper/property, as float32
    (the QVF dtype), quantised to uint8, and downsampled to a capped grid
  * the serialised scene size each produces, raw and zlib-compressed
  * the server-side build time of each

The wire size comes from trame-vtk's own serializers (the ones VtkLocalView
pushes), summed over the scene's state and its data arrays, so it is the
real payload rather than an estimate.

What this does NOT measure: client-side frame rate. vtk.js ray-casting cost
depends on the viewer's GPU and browser, and needs a live page (see
``interact_controls.py``). The payload numbers decide whether to proceed; the
frame-rate check belongs in the page, not here.

usage: bench_volume_payload.py [N ...] [file.qvf ...]
  integers are synthetic grid sizes (default 48 64 96 128 192 256); each
  .qvf path adds one row group per volume section, measured on its real field.
"""
import json
import sys
import time
import zlib

import numpy as np
import pyvista as pv

ARGS = sys.argv[1:]
QVFS = [a for a in ARGS if a.endswith(".qvf")]
SIZES = [int(a) for a in ARGS if not a.endswith(".qvf")] or (
    [] if QVFS else [48, 64, 96, 128, 192, 256]
)
CAP = 128  # the grid edge a capped volume would be downsampled to
ISO_FRACTION = 0.1  # isovalue as a fraction of the peak, both lobes


def orbital_like(n):
    """A smooth two-lobe signed field on an n**3 grid, float32.

    Most voxels sit near zero and the structure is concentrated, as in a
    molecular orbital; random noise would be unrepresentative of both the
    surface and the compressed volume.
    """
    ax = np.linspace(-6.0, 6.0, n, dtype=np.float32)
    x, y, z = np.meshgrid(ax, ax, ax, indexing="ij")
    lobe = lambda cx: np.exp(-((x - cx) ** 2 + y**2 + z**2) / 2.0)  # noqa: E731
    return (lobe(-1.5) - lobe(1.5)).astype(np.float32)


def image(field, spacing):
    grid = pv.ImageData(dimensions=field.shape, spacing=(spacing,) * 3)
    grid.point_data["v"] = field.ravel(order="F")
    return grid


def scene_wire(plotter):
    """(json_bytes, array_bytes, array_bytes_zlib) the client receives.

    Serialises every view prop with trame-vtk and counts the state JSON plus
    each referenced data array, once raw and once deflated (trame can
    compress the binary attachments).
    """
    from trame_vtk.modules.vtk.serializers.initialize import initialize_serializers
    from trame_vtk.modules.vtk.serializers.serialize import serialize
    from trame_vtk.modules.vtk.serializers.synchronization_context import (
        SynchronizationContext,
    )
    from trame_vtk.modules.vtk.serializers.utils import reference_id

    initialize_serializers()
    ctx = SynchronizationContext()
    renderer = plotter.renderer
    states = []
    props = renderer.GetViewProps()
    for i in range(props.GetNumberOfItems()):
        prop = props.GetItemAsObject(i)
        state = serialize(renderer, prop, reference_id(prop), ctx, 0)
        if state is not None:
            states.append(state)
    state_bytes = len(json.dumps(states, default=str).encode())
    raw = comp = 0
    for key in list(ctx.data_array_cache):
        buf = ctx.get_cached_data_array(key, binary=True)
        raw += len(buf)
        comp += len(zlib.compress(buf, 1))
    return state_bytes, raw, comp


def mb(x):
    return f"{x / 1e6:7.2f}"


def measure_surface(grid, field):
    peak = float(np.abs(field).max())
    iso = ISO_FRACTION * peak
    t0 = time.time()
    surf = grid.contour([iso, -iso], scalars="v")
    pl = pv.Plotter(off_screen=True)
    pl.add_mesh(surf, color="#4363d8", smooth_shading=True)
    build = time.time() - t0
    return build, surf.n_cells, scene_wire(pl)


def measure_volume(grid, label):
    t0 = time.time()
    pl = pv.Plotter(off_screen=True)
    # |v| drives opacity so both lobes show; a ramp is the cheapest honest
    # transfer function, and the payload does not depend on its shape.
    pl.add_volume(grid, scalars="v", opacity="sigmoid", cmap="coolwarm")
    build = time.time() - t0
    return build, grid.n_points, scene_wire(pl)


def row(name, build, count, wire):
    state, raw, comp = wire
    print(
        f"  {name:<26} build {build:5.2f}s | {count:>11,} | "
        f"state {state / 1e3:6.1f} kB | arrays {mb(raw)} MB | zlib {mb(comp)} MB"
    )
    return state + raw


# warm the serializers and VTK so the first row is not import-dominated
_w = pv.Plotter(off_screen=True)
_w.add_mesh(pv.Sphere())
scene_wire(_w)

print(f"grid sizes: {SIZES}; cap = {CAP}; iso = {ISO_FRACTION:.0%} of peak")
print("reference (docs/ROADMAP_AVOGADRO_PARITY.md): ribbon 0.60 MB, "
      "1,000-atom ball-and-stick 1.23 MB\n")
def report(title, field, spacing):
    """One row group: isosurface, volume float32, uint8 and (if large) capped."""
    n = max(field.shape)
    grid = image(field, spacing)
    shape = "x".join(str(d) for d in field.shape)
    print(f"{title}  ({shape} = {field.size:,} voxels, float32 grid = "
          f"{field.nbytes / 1e6:.2f} MB)")
    b, c, w = measure_surface(grid, field)
    row("isosurface (today)", b, c, w)
    b, c, w = measure_volume(grid, "float32")
    row("volume float32", b, c, w)
    lo, hi = float(field.min()), float(field.max())
    q = np.round((field - lo) / (hi - lo or 1.0) * 255).astype(np.uint8)
    qgrid = pv.ImageData(dimensions=q.shape, spacing=(spacing,) * 3)
    qgrid.point_data["v"] = q.ravel(order="F")
    b, c, w = measure_volume(qgrid, "uint8")
    row("volume uint8", b, c, w)
    if n > CAP:
        step = int(np.ceil(n / CAP))
        small = np.ascontiguousarray(field[::step, ::step, ::step])
        b, c, w = measure_volume(image(small, spacing * step), "capped")
        row(f"volume float32 capped {max(small.shape)}", b, c, w)
        sq = np.ascontiguousarray(q[::step, ::step, ::step])
        qs = pv.ImageData(dimensions=sq.shape, spacing=(spacing * step,) * 3)
        qs.point_data["v"] = sq.ravel(order="F")
        b, c, w = measure_volume(qs, "capped uint8")
        row(f"volume uint8 capped {max(sq.shape)}", b, c, w)
    print()


for n in SIZES:
    report(f"N = {n}", orbital_like(n), 12.0 / (n - 1))

if QVFS:
    from vibeview.qvf import QVFReader

    for path in QVFS:
        reader = QVFReader(path)
        for sec in reader.sections:
            if not str(sec.kind).startswith("volume"):
                continue
            data = np.asarray(reader.read_volume_data(sec.id), dtype=np.float32)
            if data.ndim != 3 or not np.isfinite(data).all() or not data.any():
                print(f"skip {path}:{sec.id} (not a finite non-zero 3-D field)\n")
                continue
            report(f"{path.rsplit('/', 1)[-1]}:{sec.id}", data, 0.2)
