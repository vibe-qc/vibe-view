#!/usr/bin/env python3
"""Client performance qualification for the browser viewer (#114).

    python scripts/client_qualification.py [--channel chrome] [--json FILE] [--gate]

Opens the real viewer in a Chromium driven by Playwright and times what a
user does with it: dragging a molecule, switching its representation,
dragging it with atom labels on (the label followers of #111), dragging a
density isosurface plain and in the Publication look (the outline pass of
#112), and switching that density to direct volume rendering and dragging it.

A drag step is one pointer move: it ends when vtk.js has drawn and a
one-pixel ``readPixels`` has returned, so it measures until the GPU is done
(the method of ``docs/bench_volume_fps.py`` and ``docs/bench_volume_viewer.py``).
A switch is timed from the click until the scene the server pushes has been
drawn. Every case runs at the same viewport, device pixel ratio 1 and with
the display-refresh cap off.

The WebGL renderer string places the run in a class, ``gpu`` or ``software``
(SwiftShader, Mesa llvmpipe and other software rasterisers), and
``client_thresholds.json`` beside this script holds each class's limits. With
``--gate`` the exit status is 1 when a case is over its limit or failed to
run; a limit of ``null`` reports a case without gating it. ``--json`` writes
every number and verdict. The candidate pipeline runs this on Linux, where
headless Chromium renders with SwiftShader; ``docs/client_performance.md``
holds the measured matrix and how to add a machine to it.

Needs the viewer extras and Playwright with a Chromium
(``pip install playwright && python -m playwright install chromium``), or
``--channel chrome`` for an installed Google Chrome.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

THRESHOLDS = Path(__file__).with_name("client_thresholds.json")
VIEWPORT = (1280, 800)
SOFTWARE_RENDERERS = re.compile(r"swiftshader|llvmpipe|softpipe|software|basic render", re.I)

#: Every case, in the order it runs, with what it measures.
CASES: dict[str, str] = {
    "drag.structure": "drag a 150-atom molecule, ball and stick",
    "switch.space_filling": "switch it to space filling",
    "switch.ball_and_stick": "switch it back to ball and stick",
    "drag.labels": "drag it with atom labels on (label followers)",
    "drag.isosurface": "drag a 64³ water density isosurface",
    "drag.outlines": "drag it in the Publication look (outline pass)",
    "switch.volume": "switch the density to direct volume rendering",
    "drag.volume": "drag the volume",
}

# Counts WebGL draw calls, so a step or a switch knows when vtk.js has drawn.
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

# One drag across the canvas centre for ``secs`` seconds; each step is one
# pointer move until vtk.js has drawn and a one-pixel readPixels returned.
_DRAG = """async (secs) => {
  const canvas = Array.from(document.querySelectorAll('canvas'))
    .sort((a, b) => b.width * b.height - a.width * a.height)[0];
  const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
  const r = canvas.getBoundingClientRect();
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const ev = (type, x, y) => new PointerEvent(type, {
    clientX: x, clientY: y, bubbles: true, cancelable: true, pointerId: 1,
    pointerType: 'mouse', button: 0, buttons: type === 'pointerup' ? 0 : 1,
    isPrimary: true,
  });
  canvas.dispatchEvent(ev('pointerdown', cx, cy));
  const px = new Uint8Array(4);
  const costs = [];
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
  }
  canvas.dispatchEvent(ev('pointerup', x, y));
  return costs;
}"""

# The browser's WebGL version and renderer string, as the About dialog shows.
_GRAPHICS = """() => {
  const canvas = document.createElement('canvas');
  let gl = canvas.getContext('webgl2'), version = 'WebGL2';
  if (!gl) { gl = canvas.getContext('webgl'); version = gl ? 'WebGL1' : 'none'; }
  if (!gl) return { version, renderer: 'unavailable' };
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  const renderer = ext
    ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)
    : gl.getParameter(gl.RENDERER);
  const lose = gl.getExtension('WEBGL_lose_context');
  if (lose) lose.loseContext();
  return { version, renderer: String(renderer) };
}"""

# What the scene holds now: changes when the server pushes a new one.
_SCENE = """() => {
  for (const ref of Object.values(window.trame.refs || {})) {
    try {
      const view = ref.$.provides.view;
      if (view && view.renderer) {
        return view.renderer.getViewProps().map((p) => {
          const mapper = p.getMapper && p.getMapper();
          const data = mapper && mapper.getInputData && mapper.getInputData();
          const points = data && data.getPoints && data.getPoints();
          return p.getClassName() + ':' + (points ? points.getNumberOfPoints() : '-');
        }).join(',');
      }
    } catch (error) { /* not a vtk view */ }
  }
  return '';
}"""


def renderer_class(renderer: str) -> str:
    """``software`` for a software rasteriser, else ``gpu``."""
    return "software" if SOFTWARE_RENDERERS.search(renderer) else "gpu"


def summary(costs: list[float]) -> dict[str, float] | None:
    """Median, 95th percentile and steps of a drag, without its first steps."""
    costs = sorted(costs[3:]) if len(costs) > 6 else sorted(costs)
    if not costs:
        return None
    median = statistics.median(costs)
    return {
        "median_ms": median,
        "p95_ms": costs[int(0.95 * (len(costs) - 1))],
        "fps": 1000.0 / median if median else 0.0,
        "steps": float(len(costs)),
    }


def limit_for(thresholds: dict[str, Any], env_class: str, case: str) -> float | None:
    """The case's limit in its environment class; ``None`` reports only."""
    limits = thresholds["classes"][env_class]
    table = limits["drag_p95_ms"] if case.startswith("drag.") else limits["switch_s"]
    value = table.get(case, table["default"])
    return None if value is None else float(value)


def evaluate(
    cases: dict[str, Any], thresholds: dict[str, Any], env_class: str
) -> list[dict[str, Any]]:
    """One verdict per case: its value, its limit and whether it passes.

    A drag is judged on its 95th-percentile step, a switch on its seconds. A
    case that did not run fails, unless its limit is ``None``.
    """
    verdicts = []
    for case in CASES:
        result = cases.get(case)
        limit = limit_for(thresholds, env_class, case)
        if isinstance(result, dict) and "error" not in result:
            value = result["p95_ms"] if case.startswith("drag.") else result["seconds"]
        else:
            value = None
        ok = limit is None or (value is not None and value <= limit)
        verdicts.append({"case": case, "value": value, "limit": limit, "ok": ok})
    return verdicts


# ── The scenes ─────────────────────────────────────────────────────────────


def molecule_xyz(atoms: int = 150) -> bytes:
    """``atoms`` atoms on a jittered cubic lattice, 1.5 Å apart."""
    elements = ("C", "N", "O", "H")
    side = max(2, round(atoms ** (1 / 3) + 0.5))
    lines = [str(atoms), "client qualification molecule"]
    index = 0
    for i in range(side):
        for j in range(side):
            for k in range(side):
                if index >= atoms:
                    break
                jitter = ((index * 7919) % 97) / 97.0 * 0.3 - 0.15
                lines.append(
                    f"{elements[index % len(elements)]} {i * 1.5 + jitter:.4f} "
                    f"{j * 1.5 - jitter:.4f} {k * 1.5 + jitter / 2:.4f}"
                )
                index += 1
    return ("\n".join(lines) + "\n").encode()


def density_archive(path: Path, n: int = 64) -> Path:
    """A water structure and a water-like density on an n³ grid, 12 bohr wide."""
    import numpy as np

    from vibeview.qvf_writer import QVFArchiveBuilder

    ax = np.linspace(-6.0, 6.0, n, dtype=np.float32)
    x, y, z = np.meshgrid(ax, ax, ax, indexing="ij")
    bohr = 1.0 / 0.529177210903

    def shell(cx: float, cy: float, cz: float, width: float, height: float) -> Any:
        return height * np.exp(-((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2) / (2 * width**2))

    field = (
        shell(0.0, 0.0, 0.1173 * bohr, 0.45, 60.0)
        + shell(0.0, 0.0, 0.1173 * bohr, 1.3, 1.2)
        + shell(0.0, 0.7572 * bohr, -0.4692 * bohr, 0.9, 0.5)
        + shell(0.0, -0.7572 * bohr, -0.4692 * bohr, 0.9, 0.5)
    ).astype(np.float32)
    spacing = 12.0 / (n - 1)
    builder = QVFArchiveBuilder(calculation="client qualification density")
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
        "density",
        "volume.density",
        "grid",
        {
            "origin": [-6.0, -6.0, -6.0],
            "voxel_vectors": [[spacing, 0, 0], [0, spacing, 0], [0, 0, spacing]],
            "shape": [n, n, n],
        },
        section_extra={"label": "Electron Density"},
    )
    builder.add_binary("density", "volume.density", "data", np.ascontiguousarray(field), "float32")
    return Path(builder.write(path))


# ── Driving the viewer ─────────────────────────────────────────────────────


class Viewer:
    """``vibe-view open`` on one archive, on a free local port."""

    def __init__(self, python: str, archive: Path, log: Path) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = int(s.getsockname()[1])
        self._log = log.open("w")
        self.proc = subprocess.Popen(
            [
                python, "-m", "vibeview", "open", str(archive), "--no-browser",
                "--host", "127.0.0.1", "--port", str(self.port),
            ],
            env={**os.environ, "PYVISTA_OFF_SCREEN": "True"},
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 240
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"the viewer exited before binding; see {log}")
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            time.sleep(0.5)
        raise TimeoutError("the viewer did not bind within 240 s")

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._log.close()


def _open_page(browser: Any, port: int) -> Any:
    page = browser.new_page(
        viewport={"width": VIEWPORT[0], "height": VIEWPORT[1]}, device_scale_factor=1
    )
    page.add_init_script(_INIT)
    page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
    page.wait_for_selector(".v-list-item", timeout=120_000)
    page.wait_for_function(
        "() => document.querySelector('canvas') && window.__draws > 0", timeout=120_000
    )
    welcome = page.get_by_role("button", name="GOT IT", exact=True)
    # Not every page shows the welcome card.
    with contextlib.suppress(Exception):
        welcome.click(timeout=10_000)
    page.wait_for_timeout(1500)
    return page


def _open_section(page: Any, title: str) -> None:
    """Select a section row; the section controls appear beside the view."""
    page.locator(".v-list-item", has_text=title).first.click()
    page.get_by_role("combobox", name="View preset", exact=True).first.wait_for(
        timeout=60_000
    )


def _select(page: Any, label: str, option: str) -> None:
    field = page.get_by_role("combobox", name=label, exact=True).first
    field.scroll_into_view_if_needed()
    field.click(force=True)
    page.get_by_role("option", name=option, exact=True).click()


def _switch(page: Any, action: Any, ready: str = "() => true") -> dict[str, float]:
    """Seconds from ``action`` until a new scene, and ``ready``, are drawn."""
    scene, draws = page.evaluate(_SCENE), page.evaluate("() => window.__draws")
    start = time.perf_counter()
    action()
    page.wait_for_function(
        f"([scene, draws]) => ({_SCENE})() !== scene && window.__draws > draws && ({ready})()",
        arg=[scene, draws],
        timeout=240_000,
    )
    seconds = time.perf_counter() - start
    page.wait_for_timeout(1000)
    return {"seconds": seconds}


def _drag(page: Any, seconds: float) -> dict[str, float]:
    result = summary(page.evaluate(_DRAG, seconds))
    if result is None:
        raise RuntimeError("the drag recorded no steps")
    return result


def _run(cases: dict[str, Any], name: str, measure: Any) -> None:
    """Record one case; a failure is recorded, and the later cases still run."""
    try:
        cases[name] = measure()
    except Exception as exc:  # noqa: BLE001 — every case reports, none aborts the run
        cases[name] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}


def qualify(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    from vibeview.converters import xyz_to_qvf

    work = Path(tempfile.mkdtemp(prefix="vibeview-client-qualification-"))
    molecule = work / "molecule.qvf"
    molecule.write_bytes(xyz_to_qvf(molecule_xyz()).getvalue())
    density = density_archive(work / "density.qvf")
    cases: dict[str, Any] = {}
    launch_args = ["--enable-unsafe-swiftshader"]
    if not args.vsync:
        launch_args += ["--disable-frame-rate-limit", "--disable-gpu-vsync"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not args.headed, channel=args.channel, args=launch_args
        )
        environment = {
            "browser": f"{args.channel or browser.browser_type.name} {browser.version}",
            "platform": platform.platform(),
            "machine": platform.machine(),
            "viewport": list(VIEWPORT),
        }

        viewer = Viewer(args.server_python, molecule, work / "molecule.log")
        try:
            page = _open_page(browser, viewer.port)
            environment.update(page.evaluate(_GRAPHICS))
            _open_section(page, "Structure")
            page.wait_for_timeout(1000)
            seconds = args.seconds
            _run(cases, "drag.structure", lambda: _drag(page, seconds))
            _run(cases, "switch.space_filling", lambda: _switch(
                page, lambda: _select(page, "Representation", "Space Filling (vdW)")))
            _run(cases, "switch.ball_and_stick", lambda: _switch(
                page, lambda: _select(page, "Representation", "Ball & Stick")))
            _run(cases, "drag.labels", lambda: (
                _switch(page, lambda: page.locator('button[title="Toggle atom labels"]')
                        .first.click()),
                _drag(page, seconds),
            )[1])
            page.close()
        finally:
            viewer.close()

        viewer = Viewer(args.server_python, density, work / "density.log")
        try:
            page = _open_page(browser, viewer.port)
            _run(cases, "drag.isosurface", lambda: (
                _switch(
                    page,
                    lambda: page.locator(".v-list-item", has_text="Electron Density").first
                    .click(),
                    "() => window.trame.state.get('volume_loaded') === true",
                ),
                _drag(page, seconds),
            )[1])
            _run(cases, "drag.outlines", lambda: (
                _switch(
                    page,
                    lambda: _select(page, "View preset", "Publication (white, clean)"),
                    "() => window.vvSilhouette && window.vvSilhouette.stats.frames > 0",
                ),
                _drag(page, seconds),
            )[1])
            _run(cases, "switch.volume", lambda: _switch(
                page,
                lambda: _select(page, "Render as", "Volume"),
                "() => window.trame.state.get('vol_sent_shape') !== null",
            ))
            _run(cases, "drag.volume", lambda: _drag(page, seconds))
            page.close()
        finally:
            viewer.close()
        browser.close()

    thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    env_class = renderer_class(str(environment.get("renderer", "")))
    verdicts = evaluate(cases, thresholds, env_class)
    passed = all(v["ok"] for v in verdicts)
    # The archives and the viewer logs: kept only when a case failed, for
    # the logs, and named in the result.
    if passed:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "version": 1,
        "environment": environment,
        "class": env_class,
        "cases": cases,
        "verdicts": verdicts,
        "passed": passed,
        "work_dir": None if passed else str(work),
    }


def report(result: dict[str, Any]) -> str:
    env = result["environment"]
    lines = [
        f"renderer: {env.get('renderer')} ({env.get('version')}), class {result['class']}",
        f"browser: {env['browser']} on {env['platform']}",
        f"{'case':24s} {'median':>10s} {'p95 / s':>10s} {'limit':>9s}  verdict",
    ]
    for verdict in result["verdicts"]:
        case = verdict["case"]
        data = result["cases"].get(case, {})
        if "error" in data:
            lines.append(f"{case:24s} {'-':>10s} {'-':>10s} {'':>9s}  ERROR {data['error']}")
            continue
        limit = "report" if verdict["limit"] is None else f"{verdict['limit']:g}"
        if case.startswith("drag."):
            measured = f"{data['median_ms']:8.1f}ms {data['p95_ms']:8.1f}ms"
        else:
            measured = f"{'':>10s} {data['seconds']:9.2f}s"
        lines.append(
            f"{case:24s} {measured} {limit:>9s}  {'pass' if verdict['ok'] else 'FAIL'}"
        )
    lines.append("qualified" if result["passed"] else "NOT qualified")
    if result.get("work_dir"):
        lines.append(f"archives and viewer logs kept in {result['work_dir']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--server-python", default=sys.executable,
                        help="interpreter that runs the viewer (needs the viewer extras)")
    parser.add_argument("--channel", default=None,
                        help="Playwright channel, e.g. chrome for an installed Chrome")
    parser.add_argument("--headed", action="store_true", help="a visible browser window")
    parser.add_argument("--vsync", action="store_true",
                        help="keep the display-refresh cap (off by default)")
    parser.add_argument("--seconds", type=float, default=4.0, help="drag time per case")
    parser.add_argument("--json", type=Path, help="write the full result to this file")
    parser.add_argument("--gate", action="store_true",
                        help="exit 1 when a case is over its limit or did not run")
    args = parser.parse_args(argv)
    result = qualify(args)
    print(report(result))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 1 if args.gate and not result["passed"] else 0


if __name__ == "__main__":
    sys.exit(main())
