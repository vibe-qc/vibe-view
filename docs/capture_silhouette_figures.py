#!/usr/bin/env python
"""Figures for the outline comparison: browser silhouette pass and headless capture (#112).

    PYVISTA_OFF_SCREEN=True /tmp/pw/bin/python docs/capture_silhouette_figures.py
    PYVISTA_OFF_SCREEN=True /tmp/pw/bin/python docs/capture_silhouette_figures.py \\
        --channel chrome --check

Two scenes, a water density isosurface at its default opacity and a bundle of
three helices drawn as a cartoon. For each it opens the real viewer in the
Publication view preset at the isometric camera, waits for the browser's
silhouette pass to draw, and saves the viewport; then it renders the same
scene headlessly, as ``vibe-view capture --publication`` does, at the same
size and camera. It writes ``docs/images/silhouette-{browser,headless}-
{density,ribbons}.png``. ``--check`` prints the pass's statistics and the
dark-pixel count of each image instead of only writing them.

Like the other capture scripts this is a dev tool, not part of the package or
CI: install Playwright in a throwaway environment with the viewer extras, as
``bench_volume_viewer.py`` describes. Headless Chromium renders with
SwiftShader, ``--channel chrome`` with the installed Chrome and its GPU.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs" / "images"
WORK = ROOT / "docs" / "_build" / "silhouette"

# The viewer for one scene, in a child process: the scene is set up before
# the server starts, as the served-browser tests do.
_SERVER = r"""
import os, sys
os.environ["PYVISTA_OFF_SCREEN"] = "True"
archive, port, scene, section = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
del sys.argv[1:]
from vibeview.app import create_app
from vibeview.qvf import QVFReader
server = create_app(QVFReader(archive))
with server.state:
    server.controller.apply_view_preset("publication")
    if scene == "density":
        server.controller.activate_section(section)
    else:
        server.controller.set_representation_style("cartoon")
    server.controller.camera_preset("isometric")
server.start(host="127.0.0.1", port=port, open_browser=False,
             show_connection_info=False, timeout=0)
"""


def helix(n: int, center: tuple[float, float], phase: float):
    import numpy as np

    t = np.arange(n)
    a = np.deg2rad(100.0) * t + phase
    return np.stack(
        [center[0] + 2.3 * np.cos(a), center[1] + 2.3 * np.sin(a), 1.5 * t], axis=1
    )


def build_ribbons(path: Path) -> Path:
    """Three 18-residue CA-trace helices side by side, as a QVF."""
    from vibeview.converters import pdb_to_qvf

    lines, serial = [], 1
    for chain, (center, phase) in zip(
        "ABC", (((0.0, 0.0), 0.0), ((9.0, 0.0), 1.0), ((4.5, 7.8), 2.0)), strict=True
    ):
        for seq, pos in enumerate(helix(18, center, phase), start=1):
            lines.append(
                "ATOM  " + f"{serial:>5}" + "  CA  ALA " + chain + f"{seq:>4}" + "    "
                + f"{pos[0]:8.3f}{pos[1]:8.3f}{pos[2]:8.3f}" + f"{1.0:6.2f}{0.0:6.2f}"
            )
            serial += 1
    pdb = path.with_suffix(".pdb")
    pdb.write_text("\n".join(lines) + "\nEND\n")
    path.write_bytes(pdb_to_qvf(pdb).getvalue())
    return path


def build_density(path: Path) -> tuple[Path, str]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bench_volume_viewer as bvv

    return Path(bvv.build_archive(64, path)), bvv.SECTION


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_port(port: int, proc: subprocess.Popen, timeout: float = 240) -> None:
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("viewer exited before binding")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    raise TimeoutError("viewer did not bind")


def browser_figure(args: argparse.Namespace, archive: Path, scene: str, section: str) -> tuple:
    from playwright.sync_api import sync_playwright

    port = _free_port()
    proc = subprocess.Popen(
        [args.server_python, "-c", _SERVER, str(archive), str(port), scene, section],
        env={**os.environ, "PYVISTA_OFF_SCREEN": "True", "PYTHONPATH": str(ROOT / "src")},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_port(port, proc)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed, channel=args.channel)
            page = browser.new_page(viewport={"width": args.width, "height": args.height})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => window.vvSilhouette && window.vvSilhouette.stats.frames > 0 && "
                "window.vvSilhouette.stats.targets > 0",
                timeout=120_000,
            )
            page.wait_for_timeout(1500)
            canvas = page.locator("canvas").first
            box = canvas.bounding_box()
            out = IMAGES / f"silhouette-browser-{scene}.png"
            canvas.screenshot(path=str(out))
            stats = page.evaluate("() => Object.assign({}, window.vvSilhouette.stats)")
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    return out, (round(box["width"]), round(box["height"])), stats


def headless_figure(archive: Path, scene: str, section: str, size: tuple[int, int]) -> Path:
    from vibeview.capture import capture_structure, capture_volume
    from vibeview.qvf import QVFReader
    from vibeview.viewer_defaults import section_volume_hints

    out = IMAGES / f"silhouette-headless-{scene}.png"
    with QVFReader(archive) as reader:
        if scene == "density":
            hints = section_volume_hints(reader, section)
            ok = capture_volume(
                reader, section, out, isovalue=hints.isovalue, colormap=hints.colormap,
                opacity=hints.opacity, size=size, publication=True,
            )
        else:
            ok = capture_structure(
                reader, out, size=size, representation="cartoon", publication=True
            )
    if not ok:
        raise RuntimeError(f"headless capture of {scene} failed")
    return out


def _dark_pixels(path: Path) -> int:
    import numpy as np
    from PIL import Image

    rgb = np.asarray(Image.open(path).convert("RGB"))
    return int((rgb.max(axis=2) < 70).sum())


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--server-python", default=sys.executable,
                    help="interpreter that runs the viewer (needs the viewer extras)")
    ap.add_argument("--headed", action="store_true", help="a visible window")
    ap.add_argument("--channel", default=None, help="Playwright channel, e.g. chrome")
    ap.add_argument("--width", type=int, default=1240, help="browser window width")
    ap.add_argument("--height", type=int, default=760, help="browser window height")
    ap.add_argument("--check", action="store_true", help="print statistics as well")
    args = ap.parse_args(argv[1:])
    WORK.mkdir(parents=True, exist_ok=True)
    density, section = build_density(WORK / "density.qvf")
    ribbons = build_ribbons(WORK / "ribbons.qvf")
    for scene, archive in (("density", density), ("ribbons", ribbons)):
        browser_png, size, stats = browser_figure(args, archive, scene, section)
        headless_png = headless_figure(archive, scene, section, size)
        print(f"{scene}: {browser_png.name} and {headless_png.name} at {size[0]}x{size[1]}")
        if args.check:
            print(f"  pass: {stats}")
            print(f"  dark pixels: browser {_dark_pixels(browser_png)}, "
                  f"headless {_dark_pixels(headless_png)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
