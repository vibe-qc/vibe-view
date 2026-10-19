"""Client-side frame rate of volume rendering vs isosurfaces in vtk.js.

``bench_volume_payload.py`` measures what crosses the websocket. This is the
other half of the go/no-go the roadmap asks for before direct volume
rendering: once the grid has arrived, can the browser's vtk.js client turn it
at an interactive frame rate? That depends on the viewer's GPU and browser, so
it needs a live page.

Serves a bare Trame VtkLocalView (the client the viewer uses) holding one
synthetic orbital-like field as an isosurface or as a volume, opens it in
Chrome with Playwright, then rotates the camera for a few seconds and times
every step. A step is ``setCamera`` on the view, then waiting until vtk.js has
drawn (WebGL draw calls are counted) and a one-pixel ``readPixels`` has returned, so it
ends when the GPU is done, not when work is merely queued. The time includes
vtk.js's rAF-aligned scheduling, so it has a floor of about one display refresh
that the surface case shows; compare cases, not absolutes. Reported per case:

  * ready   -- page load to first complete render
  * median / p95 step time (ms) and the fps that median implies
  * the WebGL renderer string, so a software fallback (SwiftShader) is not
    mistaken for a GPU result

Cases: surface (today), volume float32, volume uint8, at each grid size.

This is a dev/QA tool, not part of the package or CI. Playwright is not a
project dependency; install it in a throwaway environment and drive the
installed Chrome (no browser download)::

    python -m venv /tmp/pw && /tmp/pw/bin/pip install playwright
    PYVISTA_OFF_SCREEN=True /tmp/pw/bin/python docs/bench_volume_fps.py \\
        --server-python .venv/bin/python [N ...]

``--headed`` opens a visible window; headless Chrome may fall back to software
rendering on some machines, so check the renderer line before trusting a
number. Run on the hardware the question is about.
"""

from __future__ import annotations

import argparse
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_SIZES = [64, 128, 192, 256]
MODES = ["surface", "volume", "volume_u8"]
SECONDS = 5
ISO_FRACTION = 0.1


def orbital_like(n):
    """Smooth two-lobe signed field, float32 (same field as the payload bench)."""
    import numpy as np

    ax = np.linspace(-6.0, 6.0, n, dtype=np.float32)
    x, y, z = np.meshgrid(ax, ax, ax, indexing="ij")

    def lobe(cx):
        return np.exp(-((x - cx) ** 2 + y**2 + z**2) / 2.0)

    return (lobe(-1.5) - lobe(1.5)).astype(np.float32)


def serve(mode, n, port):
    """Run the Trame server for one case. Blocks."""
    import numpy as np
    import pyvista as pv
    from trame.app import get_server
    from trame.ui.html import DivLayout
    from trame_vtk.widgets import vtk as vtk_widgets

    field = orbital_like(n)
    if mode == "volume_u8":
        lo, hi = float(field.min()), float(field.max())
        field = np.round((field - lo) / (hi - lo) * 255).astype(np.uint8)
    spacing = 12.0 / (n - 1)
    grid = pv.ImageData(dimensions=field.shape, spacing=(spacing,) * 3)
    grid.point_data["v"] = field.ravel(order="F")

    pl = pv.Plotter(off_screen=True)
    if mode == "surface":
        iso = ISO_FRACTION * float(np.abs(field).max())
        pl.add_mesh(
            grid.contour([iso, -iso], scalars="v"),
            color="#4363d8",
            smooth_shading=True,
        )
    else:
        # U-shaped ramp: a signed orbital is opaque at both lobes, clear at zero.
        pl.add_volume(
            grid, scalars="v", opacity=[0.5, 0.25, 0.0, 0.0, 0.25, 0.5],
            cmap="coolwarm",
        )
    pl.reset_camera()

    server = get_server(client_type="vue3")
    with DivLayout(server) as layout:
        layout.root.style = "position:absolute;inset:0"
        vtk_widgets.VtkLocalView(pl.ren_win, ref="view")
    server.start(port=port, open_browser=False, show_connection_info=False)


# Counts WebGL draw calls so the driver knows when vtk.js has rendered. The
# view component exposes only getCamera/setCamera, not the render window.
_INIT = """(() => {
  window.__draws = 0;
  const wrap = (P) => {
    for (const f of ['drawElements', 'drawArrays', 'drawElementsInstanced',
                     'drawArraysInstanced']) {
      const o = P[f];
      if (o) P[f] = function (...a) { window.__draws++; return o.apply(this, a); };
    }
  };
  wrap(WebGL2RenderingContext.prototype);
  wrap(WebGLRenderingContext.prototype);
})();"""

# Runs in the page: orbit the camera about the view-up axis, one step per
# completed render. A step ends when vtk.js has drawn and a 1-pixel readPixels returns,
# so its time is the set-camera-to-GPU-done latency (rAF-aligned, so it has a
# ~one-refresh floor that the surface case shows).
_RUN = """async (secs) => {
  const view = window.trame.refs.view;
  const canvas = document.querySelector('canvas');
  const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  const renderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : 'unknown';
  const c0 = view.getCamera();
  const f = c0.focalPoint, up = c0.viewUp;
  const r0 = c0.position.map((v, i) => v - f[i]);
  const dot = (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
  const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
  const rot = (v, k, th) => {  // Rodrigues rotation of v about unit axis k
    const c = Math.cos(th), s = Math.sin(th), kv = cross(k, v), kd = dot(k, v);
    return v.map((x, i) => x*c + kv[i]*s + k[i]*kd*(1 - c));
  };
  const px = new Uint8Array(4);
  const costs = [], draws = [];
  const end = performance.now() + secs * 1000;
  let th = 0;
  while (performance.now() < end) {
    th += 0.05;
    const d0 = window.__draws, t0 = performance.now();
    const r = rot(r0, up, th);
    view.setCamera(Object.assign({}, c0, { position: r.map((v, i) => v + f[i]) }));
    while (window.__draws === d0) {
      await new Promise((res) => requestAnimationFrame(res));
      if (performance.now() - t0 > 5000) break;
    }
    gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    costs.push(performance.now() - t0);
    draws.push(window.__draws - d0);
  }
  return { renderer, costs, draws };
}"""


def _wait_port(port, proc, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError("server exited before binding")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    raise TimeoutError("server did not bind")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def drive(args):
    from playwright.sync_api import sync_playwright

    rows = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome",
            headless=not args.headed,
            # Without these Chrome caps every case at the display refresh and
            # a fast GPU cannot tell the cases apart.
            args=[] if args.vsync else [
                "--disable-frame-rate-limit", "--disable-gpu-vsync",
            ],
        )
        for n in args.sizes:
            for mode in MODES:
                port = _free_port()
                proc = subprocess.Popen(
                    [args.server_python, str(Path(__file__).resolve()),
                     "--serve", mode, str(n), str(port)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                try:
                    _wait_port(port, proc)
                    page = browser.new_page(
                        viewport={"width": args.width, "height": args.height},
                        device_scale_factor=args.dpr,
                    )
                    page.add_init_script(_INIT)
                    t0 = time.time()
                    page.goto(f"http://127.0.0.1:{port}/")
                    page.wait_for_function(
                        "window.trame && window.trame.refs.view && "
                        "document.querySelector('canvas') && window.__draws > 0",
                        timeout=240_000,
                    )
                    page.wait_for_timeout(500)  # let the first frame land
                    ready_ms = (time.time() - t0) * 1000
                    out = page.evaluate(_RUN, SECONDS)
                    if args.screenshots:
                        page.screenshot(path=f"{args.screenshots}/{mode}_{n}.png")
                    page.close()
                    costs = sorted(out["costs"][3:])  # drop warm-up frames
                    rows.append((n, mode, ready_ms, costs, out["renderer"]))
                except Exception as exc:  # report the case, keep going
                    rows.append((n, mode, None, [], f"FAILED: {exc}"))
                finally:
                    proc.terminate()
                    proc.wait(timeout=20)
        browser.close()

    print(f"{'N':>4} {'case':<10} {'ready':>8} {'median':>9} {'p95':>9} {'fps':>6}")
    renderer = ""
    for n, mode, ready, costs, info in rows:
        if not costs:
            print(f"{n:>4} {mode:<10} {info}")
            continue
        renderer = info
        med = statistics.median(costs)
        p95 = costs[int(0.95 * (len(costs) - 1))]
        print(f"{n:>4} {mode:<10} {ready / 1000:7.1f}s {med:7.1f}ms {p95:7.1f}ms "
              f"{1000 / med:6.1f}")
    print(f"\nWebGL renderer: {renderer}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sizes", nargs="*", type=int, default=DEFAULT_SIZES)
    ap.add_argument("--server-python", default=sys.executable,
                    help="python with pyvista + trame (default: this one)")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--vsync", action="store_true",
                    help="keep the display-refresh cap (cases will tie at ~60 fps)")
    ap.add_argument("--width", type=int, default=1000, help="viewport width, CSS px")
    ap.add_argument("--height", type=int, default=800, help="viewport height, CSS px")
    ap.add_argument("--dpr", type=float, default=1.0,
                    help="device pixel ratio; 2 stresses fill-rate like a Retina display")
    ap.add_argument("--screenshots", metavar="DIR",
                    help="save one PNG per case, to check the scene really drew")
    ap.add_argument("--serve", nargs=3, metavar=("MODE", "N", "PORT"),
                    help="internal: run one server case")
    args = ap.parse_args()
    if args.serve:
        serve(args.serve[0], int(args.serve[1]), int(args.serve[2]))
    else:
        drive(args)


if __name__ == "__main__":
    main()
