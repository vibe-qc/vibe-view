#!/usr/bin/env python
"""Drag frame time with the browser's silhouette pass on and off (#112).

    PYVISTA_OFF_SCREEN=True /tmp/pw/bin/python docs/bench_silhouette.py
    PYVISTA_OFF_SCREEN=True /tmp/pw/bin/python docs/bench_silhouette.py --channel chrome

The two scenes of ``capture_silhouette_figures.py`` (a water density on a 64³
grid, drawn translucent, and three helices as a cartoon) plus the density on
a 128³ grid, each in the Publication view preset. On one page per scene it
drags the mouse across the canvas for ``--seconds`` seconds with the pass on
and off, in ``--rounds`` pairs of alternating order. A drag step ends when
vtk.js has drawn and a one-pixel ``readPixels`` has returned, the method of
``bench_volume_viewer.py``, whose helpers this reuses. Headless Chromium is
the SwiftShader software renderer, ``--channel chrome`` the installed Chrome
and its GPU. A dev tool, not part of the package or CI; see
``bench_volume_viewer.py`` for the throwaway environment it needs.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_volume_viewer as bvv  # noqa: E402
import capture_silhouette_figures as figures  # noqa: E402

_SET = "(on) => { window.vvSilhouette.stats.enabled = on; }"


def measure(args: argparse.Namespace, browser, archive: Path, scene: str, section: str) -> dict:
    port = figures._free_port()
    proc = subprocess.Popen(
        [args.server_python, "-c", figures._SERVER, str(archive), str(port), scene, section],
        env={**os.environ, "PYVISTA_OFF_SCREEN": "True", "PYTHONPATH": str(figures.ROOT / "src")},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        figures._wait_port(port, proc)
        page = browser.new_page(viewport={"width": args.width, "height": args.height})
        page.add_init_script(bvv._INIT)
        page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => window.vvSilhouette && window.vvSilhouette.stats.frames > 0 && "
            "window.vvSilhouette.stats.targets > 0",
            timeout=120_000,
        )
        page.wait_for_timeout(1500)
        costs: dict[bool, list[float]] = {False: [], True: []}
        renderer = "?"
        for round_ in range(args.rounds):
            for on in ((False, True) if round_ % 2 == 0 else (True, False)):
                page.evaluate(_SET, on)
                drag = page.evaluate(bvv._DRAG, args.seconds)
                renderer = drag["renderer"]
                costs[on].extend(drag["costs"])
        targets = page.evaluate("() => window.vvSilhouette.stats.targets")
        page.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    return {
        "scene": scene,
        "renderer": renderer,
        "targets": targets,
        "off": bvv._summary(costs[False]),
        "on": bvv._summary(costs[True]),
    }


def main(argv: list[str]) -> int:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--server-python", default=sys.executable)
    ap.add_argument("--channel", default=None, help="Playwright channel, e.g. chrome")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--vsync", action="store_true", help="keep the display-refresh cap")
    ap.add_argument("--seconds", type=float, default=4.0, help="drag time per case")
    ap.add_argument("--rounds", type=int, default=2, help="pairs of cases, alternating")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    args = ap.parse_args(argv[1:])
    figures.WORK.mkdir(parents=True, exist_ok=True)
    density, section = figures.build_density(figures.WORK / "density.qvf")
    large = Path(bvv.build_archive(128, figures.WORK / "density-128.qvf"))
    ribbons = figures.build_ribbons(figures.WORK / "ribbons.qvf")
    rows = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not args.headed,
            channel=args.channel,
            args=[] if args.vsync else ["--disable-frame-rate-limit", "--disable-gpu-vsync"],
        )
        for label, archive, scene in (
            ("density 64³", density, "density"),
            ("density 128³", large, "density"),
            ("3 helices", ribbons, "ribbons"),
        ):
            print(f"  {label} …", flush=True)
            row = measure(args, browser, archive, scene, section)
            row["label"] = label
            rows.append(row)
        browser.close()
    print(f"\nrenderer: {rows[0]['renderer']}")
    print(f"{'scene':14s} {'outlines off':>22s} {'outlines on':>22s}")
    for row in rows:
        off, on = row["off"], row["on"]
        print(f"{row['label']:14s} {off['median_ms']:8.1f}ms {off['p95_ms']:6.1f}ms p95"
              f" {on['median_ms']:8.1f}ms {on['p95_ms']:6.1f}ms p95")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
