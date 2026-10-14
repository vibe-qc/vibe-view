"""The full viewer under mouse drags, a production-sized grid, and the GPU at hand.

``bench_volume_payload.py`` and ``bench_volume_fps.py`` cleared the first
version of direct volume rendering, but each used a bare page and a small
grid. The roadmap listed three cases they left unmeasured: a production-sized
cube, a weak or software-rendered GPU, and the full viewer under mouse drags
rather than camera steps. This script measures them against the real viewer,
which is step 5 of ``docs/volume_rendering_design.md``.

What it does, per grid size N:

* **Builds** a synthetic archive: a water structure and one ``volume.density``
  section on an N³ grid, 12 bohr across, a sum of Gaussians around the atoms.
  Production cubes run 100 to 200 points per axis; 160 and 200 are the sizes
  that matter.
* **Payload, in process**: the time to build the volume dataset (range,
  resampling, quantisation), the time trame-vtk takes to serialise the scene,
  and the bytes on the wire raw and zlib-compressed, for the default cap of
  128 and for the grid uncapped.
* **Browser**: starts ``vibeview open`` on the archive, drives it with
  Playwright, dismisses the welcome card, opens the density, and drags the
  mouse across the canvas for a few seconds with the isosurface (today's
  default). It then switches *Render as* to *Volume*, times the switch until
  the client holds the sent grid and has drawn it, and drags again. A drag
  step ends when vtk.js has issued WebGL draw calls and a one-pixel
  ``readPixels`` has returned, so its cost is pointer-move to GPU-done. The
  WebGL renderer string says which GPU answered: headless Chromium reports
  SwiftShader, the software renderer, which is the weak-GPU case for free;
  ``--headed --channel chrome`` uses the installed Chrome and the hardware
  GPU.

This is a dev/QA tool, not part of the package or CI. Playwright is not a
project dependency; install it in a throwaway environment that also has the
viewer extras, then::

    PYVISTA_OFF_SCREEN=True /tmp/pw/bin/python docs/bench_volume_viewer.py 128 160 200
    PYVISTA_OFF_SCREEN=True /tmp/pw/bin/python docs/bench_volume_viewer.py \\
        --headed --channel chrome 160

``--max-dim M`` raises the viewer's cap for the volume case, to measure an
uncapped grid; ``--vsync`` keeps the display-refresh cap, which ties fast cases
at the refresh rate; ``--dpr 2`` emulates a Retina pixel ratio; ``--archive
PATH`` measures a computed archive instead of the synthetic grids, its first
``volume.density`` section or ``--section ID``. Compare cases on one machine;
the absolute numbers belong to that machine's GPU.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "docs" / "_build" / "bench"
DEFAULT_SIZES = [128, 160, 200]
SECONDS = 5
SECTION = "vol_dens_0"


# ── The archive ───────────────────────────────────────────────────────────


def density_like(n):
    """A water-like density on an n³ grid, 12 bohr across, float32."""
    import numpy as np

    ax = np.linspace(-6.0, 6.0, n, dtype=np.float32)
    x, y, z = np.meshgrid(ax, ax, ax, indexing="ij")
    bohr = 1.0 / 0.529177210903

    def shell(cx, cy, cz, width, height):
        return height * np.exp(-((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2) / (2 * width**2))

    oxygen = shell(0.0, 0.0, 0.1173 * bohr, 0.45, 60.0) + shell(0.0, 0.0, 0.1173 * bohr, 1.3, 1.2)
    hydrogens = shell(0.0, 0.7572 * bohr, -0.4692 * bohr, 0.9, 0.5) + shell(
        0.0, -0.7572 * bohr, -0.4692 * bohr, 0.9, 0.5
    )
    return (oxygen + hydrogens).astype(np.float32)


def build_archive(n, path):
    """Write the n³ density archive to ``path`` and return it."""
    import numpy as np

    from vibeview.qvf_writer import QVFArchiveBuilder

    field = density_like(n)
    spacing = 12.0 / (n - 1)
    builder = QVFArchiveBuilder(calculation="volume rendering benchmark")
    builder.add_json(
        "structure",
        "structure",
        "structure",
        {
            "atoms": [
                {"symbol": "O", "position": [0.0, 0.0, 0.1173], "atomic_number": 8},
                {"symbol": "H", "position": [0.0, 0.7572, -0.4692], "atomic_number": 1},
                {"symbol": "H", "position": [0.0, -0.7572, -0.4692], "atomic_number": 1},
            ],
            "pbc": [False, False, False],
        },
    )
    builder.add_json(
        SECTION,
        "volume.density",
        "grid",
        {
            "origin": [-6.0, -6.0, -6.0],
            "voxel_vectors": [[spacing, 0, 0], [0, spacing, 0], [0, 0, spacing]],
            "shape": [n, n, n],
        },
    )
    builder.add_binary(SECTION, "volume.density", "data", np.ascontiguousarray(field), "float32")
    path.parent.mkdir(parents=True, exist_ok=True)
    return builder.write(path)


# ── The payload, in process ───────────────────────────────────────────────


def payload_timing(path, max_dims, section=SECTION, kind="volume.density"):
    """Build and serialise the volume for each cap; return rows of numbers."""
    import numpy as np
    import pyvista as pv
    from trame_vtk.modules.vtk import serializers as tv

    from vibeview.qvf import QVFReader
    from vibeview.renderers.volume_render import (
        add_volume_actor,
        default_transfer_function,
        volume_actor_name,
        volume_image,
    )

    tv.initialize_serializers()
    rows = []
    reader = QVFReader(path)
    try:
        grid = reader.read_volume_grid(section)
        data = reader.read_volume_data(section)
        for max_dim in max_dims:
            t0 = time.perf_counter()
            volume = volume_image(data, grid, kind, max_dim=max_dim)
            t_build = time.perf_counter() - t0
            tf = default_transfer_function(kind, volume.range)
            pl = pv.Plotter(off_screen=True)
            try:
                add_volume_actor(pl, volume, tf, name=volume_actor_name(section))
                t1 = time.perf_counter()
                ctx = tv.SynchronizationContext()
                scene = tv.serialize(None, pl.ren_win, tv.reference_id(pl.ren_win), ctx, 0)
                raw = b"".join(
                    np.asarray(entry["array"]).tobytes() for entry in ctx.data_array_cache.values()
                )
                t_serialize = time.perf_counter() - t1
                packed = len(zlib.compress(raw, 1)) + len(json.dumps(scene).encode())
            finally:
                pl.close()
            rows.append(
                {
                    "max_dim": max_dim,
                    "sent": list(volume.sent_shape),
                    "build_s": t_build,
                    "serialize_s": t_serialize,
                    "raw_mb": len(raw) / 1e6,
                    "wire_mb": packed / 1e6,
                }
            )
    finally:
        reader.close()
    return rows


# ── The browser ───────────────────────────────────────────────────────────

# Counts WebGL draw calls so the probe knows when vtk.js has rendered.
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

# Runs in the page: press on the canvas centre and drag back and forth for
# ``secs`` seconds. Each step dispatches one pointermove, then waits until
# vtk.js has drawn and a one-pixel readPixels has returned, so the step's
# cost is pointer-move to GPU-done, rAF-aligned like a real drag.
_DRAG = """async (secs) => {
  const canvas = document.querySelector('canvas');
  const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  const renderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : 'unknown';
  const r = canvas.getBoundingClientRect();
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const ev = (type, x, y) => new PointerEvent(type, {
    clientX: x, clientY: y, bubbles: true, cancelable: true, pointerId: 1,
    pointerType: 'mouse', button: 0, buttons: type === 'pointerup' ? 0 : 1,
    isPrimary: true,
  });
  canvas.dispatchEvent(ev('pointerdown', cx, cy));
  const px = new Uint8Array(4);
  const costs = [], draws = [];
  let x = cx, y = cy, dir = 1;
  const end = performance.now() + secs * 1000;
  while (performance.now() < end) {
    x += 6 * dir; y += 2 * dir;
    if (Math.abs(x - cx) > r.width / 3) dir = -dir;
    const d0 = window.__draws, t0 = performance.now();
    canvas.dispatchEvent(ev('pointermove', x, y));
    while (window.__draws === d0) {
      await new Promise((res) => requestAnimationFrame(res));
      if (performance.now() - t0 > 5000) break;
    }
    gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    costs.push(performance.now() - t0);
    draws.push(window.__draws - d0);
  }
  canvas.dispatchEvent(ev('pointerup', x, y));
  return { renderer, costs, draws, steps: costs.length };
}"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port, proc, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError("viewer exited before binding")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    raise TimeoutError("viewer did not bind")


def _state(page, key):
    return page.evaluate(
        "(k)=>{try{const v=window.trame.state.get(k);return v===undefined?null:v;}"
        "catch(e){return null;}}",
        key,
    )


def _summary(costs):
    costs = sorted(costs[3:]) if len(costs) > 6 else sorted(costs)
    if not costs:
        return None
    med = statistics.median(costs)
    return {
        "median_ms": med,
        "p95_ms": costs[int(0.95 * (len(costs) - 1))],
        "fps": 1000.0 / med if med else None,
        "steps": len(costs),
    }


def drive_case(browser, args, n, archive, title="Electron Density"):
    """One viewer, one page: isosurface drag, the switch, volume drag."""
    port = _free_port()
    log = WORK / f"viewer-{n}.log"
    with log.open("w") as handle:
        proc = subprocess.Popen(
            [
                args.server_python, "-m", "vibeview", "open", str(archive),
                "--no-browser", "--host", "127.0.0.1", "--port", str(port),
            ],
            env={**os.environ, "PYVISTA_OFF_SCREEN": "True", "PYTHONPATH": str(ROOT / "src")},
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    result = {"n": n}
    try:
        _wait_port(port, proc)
        page = browser.new_page(
            viewport={"width": args.width, "height": args.height}, device_scale_factor=args.dpr
        )
        page.add_init_script(_INIT)
        t0 = time.time()
        page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
        page.wait_for_selector(".v-list-item", timeout=120_000)
        page.wait_for_function("() => document.querySelector('canvas') && window.__draws > 0",
                               timeout=120_000)
        welcome = page.get_by_role("button", name="GOT IT", exact=True)
        if welcome.is_visible():
            welcome.click()
        page.locator(".v-list-item", has_text=title).first.click()
        page.wait_for_function("() => window.trame.state.get('volume_loaded') === true",
                               timeout=120_000)
        page.wait_for_timeout(2500)
        result["ready_s"] = time.time() - t0

        iso = page.evaluate(_DRAG, args.seconds)
        result["renderer"] = iso["renderer"]
        result["isosurface"] = _summary(iso["costs"])

        if args.max_dim:
            page.evaluate("(m) => window.trame.state.set('vol_max_dim', m)", args.max_dim)
            page.wait_for_timeout(500)
        draws_before = page.evaluate("() => window.__draws")
        field = page.get_by_role("combobox", name="Render as", exact=True).first
        field.scroll_into_view_if_needed()
        t1 = time.time()
        field.click(force=True)
        page.get_by_role("option", name="Volume", exact=True).click()
        page.wait_for_function(
            "(d) => window.trame.state.get('vol_sent_shape') !== null && window.__draws > d",
            arg=draws_before,
            timeout=240_000,
        )
        result["switch_s"] = time.time() - t1
        result["sent"] = _state(page, "vol_sent_shape")
        result["status"] = _state(page, "status_message")
        page.wait_for_timeout(2500)
        vol = page.evaluate(_DRAG, args.seconds)
        result["volume"] = _summary(vol["costs"])
        if args.screenshots:
            Path(args.screenshots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=f"{args.screenshots}/viewer-volume-{n}.png")
        page.close()
    except Exception as exc:  # keep the other sizes
        result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    return result


def drive(args, archives):
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not args.headed,
            channel=args.channel,
            args=[] if args.vsync else ["--disable-frame-rate-limit", "--disable-gpu-vsync"],
        )
        for n, archive, *rest in archives:
            print(f"  viewer at N={n} …", flush=True)
            title = rest[0]["title"] if rest else "Electron Density"
            results.append(drive_case(browser, args, n, archive, title=title))
        browser.close()
    return results


def _fmt(summary):
    if not summary:
        return f"{'-':>9} {'-':>9} {'-':>6}"
    return f"{summary['median_ms']:7.1f}ms {summary['p95_ms']:7.1f}ms {summary['fps']:6.1f}"


def archive_section(path, section_id=None):
    """The volume section of ``path`` to measure: id, kind, sidebar title, largest dimension."""
    from vibeview.app.sidebar import _KIND_TITLES
    from vibeview.qvf import QVFReader

    reader = QVFReader(path)
    try:
        volumes = [s for s in reader.sections if s.kind.startswith("volume.")]
        if section_id is not None:
            chosen = [s for s in volumes if s.id == section_id]
        else:
            chosen = [s for s in volumes if s.kind == "volume.density"] or volumes
        if not chosen:
            wanted = f" {section_id}" if section_id else ""
            raise SystemExit(f"{path}: no volume section{wanted}")
        section = chosen[0]
        shape = reader.read_volume_grid(section.id).shape
    finally:
        reader.close()
    return {
        "id": section.id,
        "kind": section.kind,
        "title": str(_KIND_TITLES.get(section.kind, section.kind)),
        "n": int(max(shape)),
        "shape": [int(v) for v in shape],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sizes", nargs="*", type=int, default=DEFAULT_SIZES)
    ap.add_argument("--server-python", default=sys.executable,
                    help="python with the viewer extras (default: this one)")
    ap.add_argument("--headed", action="store_true", help="a visible window, with the GPU")
    ap.add_argument("--channel", default=None,
                    help="Playwright browser channel, e.g. chrome for the installed Chrome")
    ap.add_argument("--vsync", action="store_true",
                    help="keep the display-refresh cap (fast cases tie at the refresh rate)")
    ap.add_argument("--max-dim", type=int, default=None,
                    help="raise the viewer's grid cap for the volume case (default 128)")
    ap.add_argument("--seconds", type=float, default=SECONDS, help="drag time per case")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--dpr", type=float, default=1.0)
    ap.add_argument("--archive", metavar="PATH",
                    help="measure this computed archive instead of synthetic grids")
    ap.add_argument("--section", default=None,
                    help="section id in --archive (default: its first volume.density)")
    ap.add_argument("--no-browser", action="store_true", help="payload timing only")
    ap.add_argument("--screenshots", metavar="DIR", help="save one PNG per size in volume mode")
    ap.add_argument("--json", metavar="FILE", help="write every number to FILE")
    args = ap.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    archives = []
    if args.archive:
        path = Path(args.archive).resolve()
        info = archive_section(path, args.section)
        shape = "×".join(str(v) for v in info["shape"])
        print(f"{path.name}: {info['id']} ({info['kind']}, {shape})", flush=True)
        archives.append((info["n"], path, info))
        args.sizes = [info["n"]]
    else:
        for n in args.sizes:
            path = WORK / f"volume-{n}.qvf"
            if not path.exists():
                print(f"Building {path.name} …", flush=True)
                build_archive(n, path)
            archives.append((n, path))

    report = {"sizes": args.sizes, "archive": args.archive, "payload": {}, "viewer": []}
    print(f"{'N':>4} {'cap':>4} {'sent':>12} {'build':>8} {'serial':>8} "
          f"{'raw MB':>7} {'wire MB':>8}")
    for n, path, *rest in archives:
        caps = [128] + ([n] if n != 128 else []) + ([args.max_dim] if args.max_dim else [])
        info = rest[0] if rest else {"id": SECTION, "kind": "volume.density"}
        rows = payload_timing(path, sorted(set(caps)), section=info["id"], kind=info["kind"])
        report["payload"][n] = rows
        for row in rows:
            sent = "×".join(str(v) for v in row["sent"])
            print(f"{n:>4} {row['max_dim']:>4} {sent:>12} {row['build_s']:7.2f}s "
                  f"{row['serialize_s']:7.2f}s {row['raw_mb']:7.2f} {row['wire_mb']:8.2f}")

    if not args.no_browser:
        results = drive(args, archives)
        report["viewer"] = results
        print()
        print(f"{'N':>4} {'ready':>7} {'switch':>7} {'sent':>12} "
              f"{'iso med':>9} {'iso p95':>9} {'fps':>6}  {'vol med':>9} {'vol p95':>9} {'fps':>6}")
        renderer = ""
        for r in results:
            if "error" in r:
                print(f"{r['n']:>4} {r['error']}")
                continue
            renderer = r.get("renderer", renderer)
            sent = "×".join(str(v) for v in (r.get("sent") or [])) or "-"
            print(f"{r['n']:>4} {r['ready_s']:6.1f}s {r['switch_s']:6.1f}s {sent:>12} "
                  f"{_fmt(r['isosurface'])}  {_fmt(r['volume'])}")
        print(f"\nWebGL renderer: {renderer}")
        print("(a drag step is pointer-move to GPU-done; headless Chromium is SwiftShader, "
              "the software renderer)")

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, default=float))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
