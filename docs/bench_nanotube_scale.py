#!/usr/bin/env python
"""Atom budget of the three front ends, measured on carbon nanotube scenes.

Generates the four test scenes of :mod:`vibeview.nanotube` (an open (10,10)
tube, a five-wall tube, a 19-tube bundle and an aligned forest) at 10^3 to
10^6 atoms and times, in each front end:

* **load** — from opening the archive until the first frame is drawn;
* **orbit** — one camera step while orbiting (median / 95th percentile);
* **capture** — one image of the view at the window size.

Front ends:

* ``headless`` — PyVista off screen (``vibe-view capture``'s path): load is
  reading plus scene build plus the first render, an orbit step is an
  azimuth turn and a render, capture is a PNG snapshot.
* ``terminal`` — the Textual viewer's CPU rasterizer at a 160 x 48 braille
  pane: load is the scene build plus one raster, an orbit step is one
  re-raster at a turned camera, capture is the braille text.
* ``browser`` — ``vibe-view open`` driven in Chromium by Playwright, as in
  ``scripts/client_qualification.py``: load is from starting the server
  until vtk.js has drawn (the server binds only after its first render, so
  this includes the server's scene build), an orbit step is one drag step
  until drawn, capture is the browser screenshot export
  (``window.vvCapture``).

Every case runs in its own process with a time limit, so a case past the
budget is recorded as such and the run continues.

    python docs/bench_nanotube_scale.py --json result.json
    python docs/bench_nanotube_scale.py --frontends headless --sizes 1000,10000

Needs the viewer extras; the browser front end also needs Playwright and its
Chromium (see docs/client_performance.md).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

ROOT = Path(__file__).resolve().parents[1]
FRONTENDS = ("headless", "terminal", "browser")
SIZES = (1_000, 10_000, 100_000, 1_000_000)
WINDOW = (1280, 800)


def summary(costs: list[float]) -> dict[str, float]:
    ordered = sorted(costs)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    return {"median_ms": round(statistics.median(ordered), 2), "p95_ms": round(p95, 2),
            "steps": len(ordered)}


def write_scene(kind: str, size: int, directory: Path) -> tuple[Path, int]:
    from vibeview.nanotube import scene
    from vibeview.qvf_writer import structure_archive

    tube = scene("multi" if kind == "trajectory" else kind, size)
    path = directory / f"{kind}-{size}.qvf"
    builder = structure_archive(tube.atoms(), tube.pbc, tube.lattice,
                                calculation=f"nanotube scene {kind}")
    if kind == "trajectory":
        import numpy as np

        positions = np.asarray([a["position"] for a in tube.atoms()])
        # Synthetic breathing motion; no calculation or private geometry.
        coords = np.stack([positions * [1 + 0.04 * np.sin(i / 2),
                                        1 + 0.04 * np.sin(i / 2), 1] for i in range(12)])
        builder.add_json("trajectory", "trajectory", "metadata",
                         {"atoms": tube.atoms(), "n_frames": len(coords)})
        builder.add_binary("trajectory", "trajectory", "coords", coords, dtype="float32")
    builder.write(path)
    return path, tube.n_atoms


# ── One case, in its own process ──────────────────────────────────────────


def run_headless(archive: Path, orbit_steps: int) -> dict[str, Any]:
    from vibeview.headless import add_structure, frame, headless_plotter, snapshot
    from vibeview.qvf import QVFReader

    start = time.perf_counter()
    with headless_plotter(WINDOW) as plotter:
        reader = QVFReader(archive)
        add_structure(plotter, reader)
        frame(plotter)
        plotter.render()
        load = time.perf_counter() - start
        costs = []
        for _ in range(orbit_steps):
            t0 = time.perf_counter()
            plotter.camera.Azimuth(3.0)
            plotter.ren_win.Render()
            costs.append((time.perf_counter() - t0) * 1000.0)
        t0 = time.perf_counter()
        snapshot(plotter, archive.with_suffix(".png"))
        capture = time.perf_counter() - t0
        cells = sum(a.GetMapper().GetInput().GetNumberOfCells()
                    for a in plotter.renderer.GetActors()
                    if a.GetMapper() is not None and a.GetMapper().GetInput() is not None)
    return {"load_s": round(load, 3), "orbit": summary(costs), "capture_s": round(capture, 3),
            "cells": int(cells)}


def run_terminal(archive: Path, orbit_steps: int) -> dict[str, Any]:
    from vibeview.qvf import QVFReader
    from vibeview.tui import braille
    from vibeview.tui import scene as scenelib
    from vibeview.tui.raster import DEFAULT_ROTATION, Camera, Canvas, rotation_matrix

    cols, rows = 160, 48
    start = time.perf_counter()
    reader = QVFReader(archive)
    scene = scenelib.structure_scene(reader)
    width, height = braille.pixel_size(cols, rows, "braille")
    canvas = Canvas(width, height)
    camera = Camera(rotation=rotation_matrix(*DEFAULT_ROTATION))
    scenelib.fit_camera(scene, canvas, camera)
    scenelib.render(scene, canvas, camera)
    load = time.perf_counter() - start
    costs = []
    for _ in range(orbit_steps):
        t0 = time.perf_counter()
        camera.rotate_by(0.05, 0.0)
        canvas.clear()
        scenelib.render(scene, canvas, camera)
        costs.append((time.perf_counter() - t0) * 1000.0)
    t0 = time.perf_counter()
    grid = braille.CellGrid(cols, rows)
    grid.blit(canvas.pixels, canvas.covered, 0, 0, "braille")
    grid.to_plain()
    capture = time.perf_counter() - t0
    return {"load_s": round(load, 3), "orbit": summary(costs), "capture_s": round(capture, 3)}


def run_browser(archive: Path, seconds: float, channel: str | None) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "scripts"))
    import client_qualification as cq  # type: ignore[import-not-found]
    from playwright.sync_api import sync_playwright

    launch_args = ["--enable-unsafe-swiftshader", "--disable-frame-rate-limit",
                   "--disable-gpu-vsync"]
    start = time.perf_counter()
    viewer = cq.Viewer(sys.executable, archive, archive.with_suffix(".log"))
    bound = time.perf_counter() - start
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, channel=channel, args=launch_args)
            page = browser.new_page(viewport={"width": WINDOW[0], "height": WINDOW[1]},
                                    device_scale_factor=1)
            page.add_init_script(cq._INIT)
            page.goto(f"http://127.0.0.1:{viewer.port}/", wait_until="domcontentloaded")
            # First frame with the structure in it: a draw after the scene
            # arrived (the scene probe lists its actors).
            page.wait_for_function(
                f"() => window.__draws > 0 && ({cq._SCENE})().includes('Actor')",
                timeout=600_000)
            load = time.perf_counter() - start
            with contextlib.suppress(Exception):
                page.get_by_role("button", name="GOT IT", exact=True).click(timeout=5_000)
            page.wait_for_timeout(500)
            playback = None
            if archive.stem.startswith("trajectory-"):
                cq._open_section(page, "trajectory")
                page.get_by_role("button", name="Next trajectory frame", exact=True).wait_for()
                # Wait for the bounded filmstrip before timing live frames.
                page.wait_for_function(
                    "() => (window.trame.state.get('trajectory_thumbnails') || []).length >= 4",
                    timeout=600_000)
                costs = []
                for _ in range(6):
                    draws = page.evaluate("() => window.__draws")
                    t0 = time.perf_counter()
                    page.get_by_role("button", name="Next trajectory frame", exact=True).click()
                    page.wait_for_function("d => window.__draws > d", arg=draws, timeout=600_000)
                    costs.append((time.perf_counter() - t0) * 1000)
                playback = summary(costs)
                page.screenshot(path=str(archive.with_suffix(".browser.png")))
            graphics = page.evaluate(cq._GRAPHICS)
            drag = cq._DRAG.replace(
                "canvas.dispatchEvent(ev('pointerup', x, y));",
                "const restStart = performance.now(); "
                "canvas.dispatchEvent(ev('pointerup', x, y)); "
                "await new Promise(r => requestAnimationFrame(r)); "
                "while (window.vvGlyphLod && "
                "window.vvGlyphLod.stats.coarse > window.vvGlyphLod.stats.restored) { "
                "if (performance.now()-restStart > 5000) throw Error('LOD did not restore'); "
                "await new Promise(r => requestAnimationFrame(r)); } gl.finish();")
            drag = drag.replace("return costs;",
                                "return {costs, rest_ms: performance.now()-restStart};")
            measured = page.evaluate(drag, seconds)
            orbit = summary(measured["costs"])
            rest_ms = measured["rest_ms"]
            t0 = time.perf_counter()
            page.evaluate("() => window.vvCapture.capture({scale: 1})"
                          ".then((r) => r.dataUrl.length)")
            capture = time.perf_counter() - t0
            browser.close()
    finally:
        viewer.close()
    return {"server_ready_s": round(bound, 3), "load_s": round(load, 3), "orbit": orbit,
            "capture_s": round(capture, 3), "renderer": graphics.get("renderer"),
            "playback": playback, "rest_ms": round(rest_ms, 2)}


def one_case(args: argparse.Namespace) -> int:
    import vibeview

    archive = Path(args.case_archive)
    if args.case_frontend == "headless":
        result = run_headless(archive, args.orbit_steps)
    elif args.case_frontend == "terminal":
        result = run_terminal(archive, args.orbit_steps)
    else:
        result = run_browser(archive, args.seconds, args.channel)
    result["source"] = str(Path(vibeview.__file__).parents[1])
    print(json.dumps(result))
    return 0


# ── The matrix ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--frontends", default=",".join(FRONTENDS))
    parser.add_argument("--scenes", default="single,multi,bundle,forest")
    parser.add_argument("--sizes", default=",".join(str(s) for s in SIZES))
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds per case")
    parser.add_argument("--orbit-steps", type=int, default=20)
    parser.add_argument("--seconds", type=float, default=3.0, help="browser drag time")
    parser.add_argument("--channel", default=None, help="Playwright browser channel")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--src", type=Path,
                        help="measure another checkout's src/ (for a before/after)")
    parser.add_argument("--case-frontend", help=argparse.SUPPRESS)
    parser.add_argument("--case-archive", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.case_frontend:
        return one_case(args)

    work = Path(tempfile.mkdtemp(prefix="vibeview-nanotube-scale-"))
    results: list[dict[str, Any]] = []
    for kind in args.scenes.split(","):
        for size in (int(s) for s in args.sizes.split(",")):
            t0 = time.perf_counter()
            archive, n_atoms = write_scene(kind, size, work)
            generate = time.perf_counter() - t0
            for frontend in args.frontends.split(","):
                row: dict[str, Any] = {"scene": kind, "target": size, "atoms": n_atoms,
                                       "frontend": frontend, "generate_s": round(generate, 3)}
                cmd = [sys.executable, __file__, "--case-frontend", frontend,
                       "--case-archive", str(archive), "--orbit-steps", str(args.orbit_steps),
                       "--seconds", str(args.seconds)]
                if args.channel:
                    cmd += ["--channel", args.channel]
                env = dict(os.environ)
                if args.src:
                    env["PYTHONPATH"] = str(args.src.resolve())
                try:
                    done = subprocess.run(cmd, capture_output=True, text=True, env=env,
                                          timeout=args.timeout, check=False)
                    if done.returncode == 0:
                        row.update(json.loads(done.stdout.strip().splitlines()[-1]))
                    else:
                        row["error"] = (done.stderr.strip().splitlines() or ["failed"])[-1][:300]
                except subprocess.TimeoutExpired:
                    row["error"] = f"over the {args.timeout:.0f} s limit"
                results.append(row)
                print(json.dumps(row), flush=True)
    out = {"platform": platform.platform(), "machine": platform.machine(), "results": results}
    if args.json:
        args.json.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
