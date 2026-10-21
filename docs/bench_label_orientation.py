#!/usr/bin/env python
"""Camera-facing atom labels: the render comparison and the drag frame time.

**Render comparison** (roadmap D5)::

    python docs/bench_label_orientation.py [outdir]

Renders the same three labels from an off-axis camera twice — once with
the old XY-plane placement, once billboarded — and reports lit pixels for
each, writing `label_flat.png` and `label_billboard.png` for eyeballing.

**Look at the images.** Lit-pixel count is a weak proxy and is reported
only as a summary: `pv.Text3D` is *extruded*, so edge-on text is still a
visible slab occupying plenty of pixels. A browser-side attempt to measure
this by counting label ink before and after a rotation reported the
un-billboarded labels retaining *more* ink, which says nothing about
whether the text could be read. Legibility is a visual property; the
render is the evidence and the count is a regression tripwire.

**Drag frame time in the browser** (#111, 3.0 WP 3.1)::

    PYVISTA_OFF_SCREEN=True /tmp/pw/bin/python docs/bench_label_orientation.py --browser
    PYVISTA_OFF_SCREEN=True /tmp/pw/bin/python docs/bench_label_orientation.py \\
        --browser --headed --channel chrome --atoms 300

Builds a synthetic molecule of ``--atoms`` atoms, opens it in the real
viewer with atom labels on, drives it with Playwright and drags the mouse
across the canvas for ``--seconds`` seconds on two pages of the same viewer:
one with the label follower switched off before the page loads, which leaves
the labels as the geometry the server sent, what a drag drew before #111, and
one with the follower facing every glyph in the vertex shader, in ``--rounds``
pairs of alternating order whose steps are pooled per case. A drag step
ends when vtk.js has drawn and a one-pixel ``readPixels`` has returned, the
method of ``bench_volume_viewer.py``, whose helpers this reuses. Headless
Chromium renders with SwiftShader, the software-GPU case, unless
``--channel chrome`` picks the installed Chrome; ``--headed`` gives a visible
window. Playwright is not a project dependency: install it in a throwaway
environment that also has the viewer extras, as ``bench_volume_viewer.py``
describes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

# Off to the side and slightly above: the ordinary viewpoint that the
# fixed XY-plane placement renders as skewed, squashed text.
CAMERA = ((0.0, -14.0, 3.0), (3.0, 0.0, 0.0), (0.0, 0.0, 1.0))
ELEMENTS = ("C", "N", "O", "H")


def render_comparison(outdir: Path) -> int:
    import numpy as np
    import pyvista as pv

    from vibeview.renderers.structure import build_label_mesh

    outdir.mkdir(parents=True, exist_ok=True)

    anchors = [np.array([0.0, 0.0, 0.0]), np.array([3.0, 0.0, 0.0]),
               np.array([6.0, 0.0, 0.0])]
    strings = ["O1", "H2", "H3"]
    position, focal_point, view_up = CAMERA

    for tag, camera in (("flat", None), ("billboard", CAMERA)):
        plotter = pv.Plotter(off_screen=True, window_size=(760, 300))
        plotter.set_background("#101018")
        mesh = build_label_mesh(anchors, strings, camera=camera, scale=0.9)
        if mesh is None:
            print(f"{tag}: nothing to draw")
            continue
        plotter.add_mesh(mesh, color="white", lighting=False)
        plotter.camera.position = position
        plotter.camera.focal_point = focal_point
        plotter.camera.up = view_up
        out = outdir / f"label_{tag}.png"
        plotter.screenshot(str(out))
        img = np.asarray(plotter.screenshot(return_img=True))
        lit = int((img.sum(axis=2) > 300).sum())
        total = img.shape[0] * img.shape[1]
        print(f"{tag:10s} {lit:>7,} lit px of {total:,}   -> {out}")

    print("\nOpen both PNGs. The flat labels are skewed and squashed from "
          "this viewpoint; the billboarded ones read head-on.")
    return 0


# ── The browser ───────────────────────────────────────────────────────────


def synthetic_xyz(atoms: int) -> bytes:
    """``atoms`` atoms on a jittered cubic lattice, 1.5 Å apart, four elements."""
    side = max(2, round(atoms ** (1 / 3) + 0.5))
    lines = [str(atoms), "synthetic label bench"]
    index = 0
    for i in range(side):
        for j in range(side):
            for k in range(side):
                if index >= atoms:
                    break
                jitter = ((index * 7919) % 97) / 97.0 * 0.3 - 0.15
                lines.append(
                    f"{ELEMENTS[index % len(ELEMENTS)]} "
                    f"{i * 1.5 + jitter:.4f} {j * 1.5 - jitter:.4f} {k * 1.5 + jitter / 2:.4f}"
                )
                index += 1
    return ("\n".join(lines) + "\n").encode()


_STATS = "() => window.vvLabelFollow ? Object.assign({}, window.vvLabelFollow.stats) : null"
# Set before the page script runs: the follower does not install, and the
# labels are the plain geometry the server sent, as before #111.
_FOLLOWER_OFF = "window.__vvLabelFollowOff = true;"


def _drag_case(browser, bvv, args: argparse.Namespace, port: int, *, follower: bool) -> dict:
    """Open one page, make sure labels are on, and time a drag across it."""
    page = browser.new_page(
        viewport={"width": args.width, "height": args.height},
        device_scale_factor=args.dpr,
    )
    page.add_init_script(bvv._INIT)
    if not follower:
        page.add_init_script(_FOLLOWER_OFF)
    page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
    page.wait_for_selector(".v-list-item", timeout=120_000)
    page.wait_for_function(
        "() => document.querySelector('canvas') && window.__draws > 0", timeout=120_000
    )
    welcome = page.get_by_role("button", name="GOT IT", exact=True)
    if welcome.is_visible():
        welcome.click()
    # The trame state is shared by every page on the server: the first case
    # switches labels on, the second finds them on.
    if not bvv._state(page, "show_atom_labels"):
        draws = page.evaluate("() => window.__draws")
        page.locator('button[title="Toggle atom labels"]').first.click()
        page.wait_for_function(
            "(d) => window.trame.state.get('show_atom_labels') === true && window.__draws > d",
            arg=draws,
            timeout=120_000,
        )
    page.wait_for_timeout(2000)
    # A hover hands the follower its renderer before the drag starts.
    box = page.locator("canvas").first.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(300)
    before = page.evaluate(_STATS)
    drag = page.evaluate(bvv._DRAG, args.seconds)
    after = page.evaluate(_STATS)
    case = {"renderer": drag["renderer"], "costs": drag["costs"]}
    if follower and before and after:
        case["configured"] = after["configured"]
        steps = max(1, len(drag["costs"]))
        case["draws_per_step"] = (after["draws"] - before["draws"]) / steps
        case["scans_per_step"] = (after["scans"] - before["scans"]) / steps
    if args.screenshots:
        Path(args.screenshots).mkdir(parents=True, exist_ok=True)
        tag = "follower" if follower else "geometry"
        page.screenshot(path=f"{args.screenshots}/labels-{args.atoms}-{tag}.png")
    page.close()
    return case


def browser_drag(args: argparse.Namespace) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bench_volume_viewer as bvv
    from playwright.sync_api import sync_playwright

    from vibeview.converters import xyz_to_qvf

    bvv.WORK.mkdir(parents=True, exist_ok=True)
    archive = bvv.WORK / f"labels-{args.atoms}.qvf"
    archive.write_bytes(xyz_to_qvf(synthetic_xyz(args.atoms)).getvalue())
    port = bvv._free_port()
    log = bvv.WORK / f"labels-{args.atoms}.log"
    with log.open("w") as handle:
        proc = subprocess.Popen(
            [
                args.server_python, "-m", "vibeview", "open", str(archive),
                "--no-browser", "--host", "127.0.0.1", "--port", str(port),
            ],
            env={**os.environ, "PYVISTA_OFF_SCREEN": "True", "PYTHONPATH": str(bvv.ROOT / "src")},
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    result: dict = {"atoms": args.atoms}
    try:
        bvv._wait_port(port, proc)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=not args.headed,
                channel=args.channel,
                args=[] if args.vsync else ["--disable-frame-rate-limit", "--disable-gpu-vsync"],
            )
            # Alternate the order round by round (ABBA...), so warm-up and
            # page order cannot pass for the follower's cost.
            costs: dict[bool, list[float]] = {False: [], True: []}
            follower: dict = {}
            for round_ in range(args.rounds):
                for on in ((False, True) if round_ % 2 == 0 else (True, False)):
                    case = _drag_case(browser, bvv, args, port, follower=on)
                    costs[on].extend(case["costs"])
                    if on:
                        follower = case
            browser.close()
        result["renderer"] = follower["renderer"]
        result["rounds"] = args.rounds
        result["geometry"] = bvv._summary(costs[False])
        result["follower"] = bvv._summary(costs[True])
        result["configured"] = follower.get("configured")
        result["draws_per_step"] = follower.get("draws_per_step")
        result["scans_per_step"] = follower.get("scans_per_step")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    return result


def _fmt(summary: dict | None) -> str:
    if not summary:
        return f"{'-':>9} {'-':>9} {'-':>6}"
    return f"{summary['median_ms']:7.1f}ms {summary['p95_ms']:7.1f}ms {summary['fps']:6.1f}"


def browser_main(args: argparse.Namespace) -> int:
    result = browser_drag(args)
    print(f"\n{result['atoms']} atoms with labels, {result['rounds']} rounds, "
          f"renderer: {result.get('renderer', '?')}")
    print(f"{'labels':12s} {'median':>9} {'p95':>9} {'fps':>6}")
    print(f"{'geometry':12s} {_fmt(result.get('geometry'))}")
    print(f"{'follower':12s} {_fmt(result.get('follower'))}")
    geometry, follower = result.get("geometry"), result.get("follower")
    if geometry and follower:
        ratio = follower["median_ms"] / geometry["median_ms"]
        print(f"\nfollower / geometry median: {ratio:.2f}; "
              f"label mappers on the shader: {result.get('configured')}, "
              f"shader draws per drag step: {result.get('draws_per_step') or 0:.1f}, "
              f"label scans per drag step: {result.get('scans_per_step') or 0:.2f}")
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2))
        print(f"wrote {args.json}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir", nargs="?", default=None,
                    help="where the render comparison writes its PNGs")
    ap.add_argument("--browser", action="store_true",
                    help="measure the drag frame time in the real viewer instead")
    ap.add_argument("--atoms", type=int, default=120, help="atoms, hence labels, to draw")
    ap.add_argument("--seconds", type=float, default=5.0, help="drag time per case")
    ap.add_argument("--rounds", type=int, default=2,
                    help="pairs of cases, in alternating order (default 2)")
    ap.add_argument("--server-python", default=sys.executable,
                    help="interpreter that runs the viewer (needs the viewer extras)")
    ap.add_argument("--headed", action="store_true", help="a visible window, with the GPU")
    ap.add_argument("--channel", default=None,
                    help="Playwright browser channel, e.g. chrome for the installed Chrome")
    ap.add_argument("--vsync", action="store_true",
                    help="keep the display-refresh cap (off by default, to see the cost)")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--dpr", type=float, default=1.0)
    ap.add_argument("--screenshots", metavar="DIR", help="save a PNG of the labelled scene")
    ap.add_argument("--json", metavar="FILE", help="write every number to FILE")
    args = ap.parse_args(argv[1:])
    if args.browser:
        return browser_main(args)
    return render_comparison(Path(args.outdir) if args.outdir else Path.cwd())


if __name__ == "__main__":
    sys.exit(main(sys.argv))
