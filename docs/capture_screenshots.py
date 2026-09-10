#!/usr/bin/env python
"""Regenerate the vibe-view documentation screenshots and animations.

Launches the viewer on the project-authored showcase QVFs, drives it through a wide range of
UI states with Playwright (headless Chromium), and writes to
``docs/images/``:

  * **Static PNGs** — structure, density, orbital, vibrations frame, periodic
    structure, bands+DOS, ECD spectrum, NMR panel, symmetry panel, atomic
    properties + charge overlay, measure-mode controls, clip plane.
  * **Animated GIFs** — trajectory playback, vibrational mode animation,
    reaction-path playback (with waypoint label transitions).

This is a **dev/docs tool**, not part of the package: Playwright and Pillow
(for GIF assembly) are not runtime, test, or CI dependencies, and this script
is never imported by ``vibeview`` or run in CI. Install the one-off tooling
with::

    pip install playwright pillow && playwright install chromium

Then regenerate everything with::

    VIBE_VIEW_EXAMPLES=/path/to/vibe-qc/examples/vibe_view \
        PYVISTA_OFF_SCREEN=True python docs/capture_screenshots.py

It drives the *real* Vue/Trame UI in a real (headless) browser, so all captures
include the app bar, sidebar, 3D viewport, and side panels exactly as a user
sees them — and Playwright's compositor screenshot captures the WebGL viewport
reliably (no ``preserveDrawingBuffer`` blank-canvas issue).

The example QVFs carry a sanitized provenance host (``example-host``); keep it
that way so no real hostname is baked into a committed image.
"""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
IMAGES = Path(__file__).resolve().parent / "images"
PORT = 8137  # distinct from the dev default (8080) and interact_controls (8139)
VIEWER = Path(sys.executable)
VIEWPORT = {"width": 1440, "height": 1100}

ALL_SECTIONS: Path
H2CO: Path
NACL: Path


# ── server lifecycle ──────────────────────────────────────────────────


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_port(port: int, timeout: float = 45.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if _port_open(port):
            return
        time.sleep(0.5)
    raise TimeoutError(f"server never came up on :{port}")


# ── page helpers ──────────────────────────────────────────────────────


def _wait_ready(page: Page) -> None:
    """Wait for the Vue app + viewport to be live."""
    page.wait_for_selector(".v-list-item", timeout=30_000)
    for _ in range(40):
        has_canvas = page.evaluate("() => !!document.querySelector('canvas')")
        loading = page.evaluate("() => /Loading|Awaiting/.test(document.body.innerText||'')")
        if has_canvas and not loading:
            break
        time.sleep(0.5)
    if not has_canvas or loading:
        raise RuntimeError("viewer did not finish loading")
    time.sleep(2.0)  # let the first VtkLocalView frame paint
    welcome = page.get_by_role("button", name="GOT IT", exact=True)
    if welcome.is_visible():
        welcome.click()
    background = page.get_by_title("Toggle dark/light background", exact=True)
    if background.count() and background.is_visible():
        background.click()
    _activate(page, "Structure")


def _activate(page: Page, label: str, settle: float = 1.5) -> None:
    """Trusted-click a sidebar section entry by visible label."""
    if label != "Structure":
        page.locator(".v-list-item", has_text="Structure").first.click()
        time.sleep(0.4)
    page.locator(".v-list-item", has_text=label).first.click()
    time.sleep(settle)
    status = _state(page, "status_message") or ""
    if status.startswith(("Could not open", "Error")):
        raise RuntimeError(status)


def _click_button(page: Page, text: str) -> None:
    """Synthetic-click a v-btn by text pattern (fine for content buttons)."""
    page.evaluate(
        "(t) => { const b=[...document.querySelectorAll('.v-btn')]"
        ".find(x=>new RegExp(t,'i').test((x.textContent||'').trim()));"
        "if(b)b.click(); }",
        text,
    )


def _toggle_switch(page: Page, label: str) -> None:
    """Trusted-click a v-switch by its label text."""
    sw = page.locator(".v-switch", has_text=label).first
    sw.locator(".v-selection-control__input").first.click(timeout=4_000)
    time.sleep(0.5)


def _state(page: Page, key: str):
    return page.evaluate(
        "(k)=>{try{const v=window.trame.state.get(k);"
        "return v===undefined?null:v;}catch(e){return null;}}",
        key,
    )


def _wait_state(page: Page, key: str, pred, timeout: float = 7.0):
    end = time.time() + timeout
    v = _state(page, key)
    while time.time() < end:
        v = _state(page, key)
        try:
            if pred(v):
                return v
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    return v


def _nudge(page: Page, dx: int = 9, dy: int = 7) -> None:
    """Force a fresh WebGL frame by dragging the camera a few pixels."""
    if _state(page, "measure_mode"):
        return  # a camera drag must not accidentally select an atom
    if not page.evaluate("() => !!document.querySelector('canvas')"):
        return
    bounds = page.locator("canvas").first.bounding_box()
    if bounds is None:
        raise RuntimeError("no visible viewport")
    cx, cy = bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + dx, cy + dy, steps=4)
    page.mouse.up()
    time.sleep(0.6)


def _prepare_shot(page: Page) -> None:
    """Frame the real viewport and current result panel before capture."""
    # Resize using the real accessible splitter, preserving the UI's content.
    splitter = page.get_by_role("separator", name="Result details", exact=True)
    splitter.wait_for(state="visible")
    splitter.press("Home")
    has_panel = bool(
        _state(page, "chart_html")
        or _state(page, "bands_html")
        or _state(page, "properties_html")
        or _state(page, "spectra_html")
        or _state(page, "trajectory_energy_image")
    )
    target = 780 if _state(page, "spectra_html") else (580 if has_panel else 130)
    for _ in range((target - 80) // 50):
        splitter.press("Shift+ArrowUp")
    for drawer in (
        "#vv-right-panel .v-navigation-drawer__content",
        ".v-navigation-drawer__content",
    ):
        page.locator(drawer).evaluate_all("els => els.forEach(e => e.scrollTop = 0)")
    time.sleep(0.6)
    page.keyboard.press("r")
    time.sleep(0.8)
    _nudge(page)
    if _state(page, "chart_html") or _state(page, "bands_html") or _state(page, "spectra_html"):
        page.frame_locator("#vv-bottom-panel iframe").first.locator(".main-svg").first.wait_for(
            state="visible", timeout=30_000
        )
    page.mouse.move(280, 45)  # keep hover tooltips out of the capture
    time.sleep(0.3)


def _shot(page: Page, name: str) -> None:
    """Capture a full-window screenshot to docs/images/<name>."""
    _prepare_shot(page)
    out = IMAGES / name
    out.write_bytes(_capture_frame(page))
    print(f"  wrote {out.relative_to(ROOT)}")


def _capture_frame(page: Page) -> bytes:
    """Accept only an actual compositor frame containing a rendered molecule."""
    import numpy as np
    from PIL import Image

    for attempt in range(8):
        raw = page.screenshot()
        box = page.locator("canvas").first.bounding_box()
        if box is None:
            raise RuntimeError("viewport disappeared")
        pixels = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
        x, y, w, h = (int(box[k]) for k in ("x", "y", "width", "height"))
        viewport = pixels[y + 16 : y + h - 16, x + 16 : x + w - 16].astype(int)
        # Every capture input has coloured atoms or lobes. A blank WebGL
        # canvas (or a lone black axis grid) must not replace an existing PNG.
        if (
            np.count_nonzero(np.ptp(viewport, axis=2) > 35)
            > viewport.shape[0] * viewport.shape[1] * 0.002
        ):
            return raw
        if attempt == 1:
            # Reconnect the browser view to the unchanged server scene.
            # Chromium can lose its WebGL backing buffer after a splitter resize.
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("canvas", timeout=30_000)
            time.sleep(3)
        page.keyboard.press("r")
        time.sleep(1.5)
        _nudge(page)
    raise RuntimeError("viewport stayed blank after camera redraw; no image saved")


# ── GIF assembly (Pillow, no ffmpeg needed) ───────────────────────────


def _frame_bytes(page: Page) -> bytes:
    """Capture the full window as raw PNG bytes (no disk write)."""
    _nudge(page, dx=0, dy=0)  # redraw without rotating the animation camera
    return _capture_frame(page)


def _save_gif(
    frames: list[bytes],
    name: str,
    duration_ms: int = 180,
    loop: int = 0,
) -> None:
    """Assemble a list of raw PNG byte-strings into an animated GIF."""
    from PIL import Image  # type: ignore[import]

    pil_frames: list[Image.Image] = []
    for raw in frames:
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        # Convert to palette mode for compact GIF (256-colour).
        pil_frames.append(img.convert("P", dither=Image.Dither.FLOYDSTEINBERG))

    out = IMAGES / name
    pil_frames[0].save(
        str(out),
        format="GIF",
        save_all=True,
        append_images=pil_frames[1:],
        loop=loop,
        duration=duration_ms,
        optimize=False,
    )
    print(f"  wrote {out.relative_to(ROOT)} ({len(frames)} frames @ {duration_ms}ms)")


# ── animated capture helpers ──────────────────────────────────────────


def _capture_trajectory_gif(page: Page, name: str) -> None:
    """Step through all trajectory frames and assemble as an animated GIF."""
    n = _state(page, "trajectory_n_frames") or 0
    if n < 2:
        print(f"  skip {name} (only {n} frames)")
        return

    _prepare_shot(page)
    frames: list[bytes] = []
    # Land on frame 0 first (slider may be mid-way from prior actions).
    page.evaluate(
        "(v)=>{try{window.trame.state.set('trajectory_frame',v);}catch(e){}}",
        0,
    )
    time.sleep(0.8)
    frames.append(_frame_bytes(page))

    # Step forward to cover all frames.
    step_btn = page.locator("button:has(.mdi-skip-next)").first
    for _ in range(n - 1):
        if step_btn.count() and step_btn.is_visible():
            step_btn.click()
        time.sleep(0.6)
        frames.append(_frame_bytes(page))

    _save_gif(frames, name, duration_ms=220)


def _capture_vibration_gif(page: Page, name: str, n_frames: int = 12) -> None:
    """Play the vibration animation and capture frames over ~2 s."""
    _prepare_shot(page)
    # Start play via the play button (trusted click).
    play = page.locator("button:has(.mdi-play)").first
    if not (play.count() and play.is_visible()):
        print(f"  skip {name} (no play button)")
        return
    play.click()
    _wait_state(page, "vibration_playing", lambda v: v is True, 3.0)

    frames: list[bytes] = []
    interval = 2.5 / n_frames  # spread across ~2.5 s (≈ one cycle)
    for _ in range(n_frames):
        time.sleep(interval)
        frames.append(_frame_bytes(page))

    # Pause.
    pause = page.locator("button:has(.mdi-pause)").first
    if pause.count() and pause.is_visible():
        pause.click()

    _save_gif(frames, name, duration_ms=int(interval * 1000))


def _capture_reaction_gif(page: Page, name: str) -> None:
    """Step through reaction-path frames, capture, including waypoint chips."""
    n = _state(page, "trajectory_n_frames") or 0
    if n < 2:
        print(f"  skip {name} (only {n} frames)")
        return

    _prepare_shot(page)
    frames: list[bytes] = []
    page.evaluate(
        "(v)=>{try{window.trame.state.set('trajectory_frame',v);}catch(e){}}",
        0,
    )
    time.sleep(0.8)
    frames.append(_frame_bytes(page))

    step_btn = page.locator("button:has(.mdi-skip-next)").first
    for _ in range(n - 1):
        if step_btn.count() and step_btn.is_visible():
            step_btn.click()
        time.sleep(0.7)  # extra settle so waypoint label fades in
        frames.append(_frame_bytes(page))

    # Hold the last frame a bit longer (waypoint label visible).
    frames.extend([frames[-1]] * 3)
    _save_gif(frames, name, duration_ms=280)


# ── scene functions ───────────────────────────────────────────────────


def _all_sections_shots(page: Page) -> None:
    """All interactive sections in the multi-section fixture."""

    # ── static shots ─────────────────────────────────────────────────

    # 1. Structure (default on load).
    _shot(page, "01-structure.png")

    # 2. Electron density.
    _activate(page, "Electron Density", settle=4)
    _shot(page, "02-density.png")

    # 3. Difference density (two-color, red + / blue −).
    _activate(page, "Difference Density", settle=4)
    _shot(page, "03-difference-density.png")

    # 4. ELF.
    _activate(page, "ELF", settle=4)
    _shot(page, "04-elf.png")

    # 5. Atomic Properties — charge table + 3D overlay.
    _activate(page, "Atomic Properties", settle=2)
    _shot(page, "05-atomic-properties.png")

    _activate(page, "QTAIM Topology", settle=2)
    _shot(page, "19-qtaim.png")

    # 6. Show the actual measure controls, ready for atom selection.
    _activate(page, "Structure", settle=1)
    _toggle_switch(page, "Measure mode")
    time.sleep(0.5)
    _shot(page, "06-measure-distance.png")
    _toggle_switch(page, "Measure mode")  # restore

    # 7. Clip plane on difference density.
    _activate(page, "Difference Density", settle=4)
    _toggle_switch(page, "Enable")  # clip switch
    time.sleep(2.0)
    _shot(page, "07-clip-plane.png")
    _toggle_switch(page, "Enable")  # restore

    # 8. Band structure (+ DOS as a combined figure).
    _activate(page, "Band Structure", settle=4)
    _shot(page, "08-bands-dos.png")

    # 9. ECD spectrum.
    _activate(page, "ECD Spectrum", settle=2)
    _shot(page, "09-ecd-spectrum.png")

    # 10. NMR shifts panel.
    _activate(page, "NMR Shifts", settle=2)
    _shot(page, "10-nmr.png")

    # 11. Symmetry panel.
    _activate(page, "Symmetry", settle=1)
    _shot(page, "11-symmetry.png")

    # 12. SCF convergence.
    _activate(page, "SCF Convergence", settle=2)
    _shot(page, "12-scf-convergence.png")

    # 13. Citations BibTeX panel.
    _activate(page, "Citations", settle=1)
    _shot(page, "13-citations.png")

    # ── animated GIFs ────────────────────────────────────────────────

    # trajectory.gif — step through all frames.
    _activate(page, "Trajectory", settle=2)
    _capture_trajectory_gif(page, "anim-trajectory.gif")

    # vibration.gif — play the normal mode.
    _activate(page, "Structure", settle=1)
    _activate(page, "Vibrations", settle=2)
    _shot(page, "04-vibrations.png")
    _capture_vibration_gif(page, "anim-vibration.gif", n_frames=12)

    # reaction.gif — step through reaction path + waypoint labels.
    _activate(page, "Reaction Path", settle=2)
    _capture_reaction_gif(page, "anim-reaction.gif")


def _h2co_shots(page: Page) -> None:
    """H₂CO showcase: molecular orbital HOMO."""
    _activate(page, "Structure", settle=1)
    _shot(page, "14-structure-h2co.png")

    _activate(page, "Wavefunction", settle=2)
    _click_button(page, "RENDER MO")
    time.sleep(5)
    _shot(page, "15-orbital-homo.png")

    _activate(page, "IR Spectrum", settle=2)
    _shot(page, "20-ir-spectrum.png")

    # Stored molecular orbital (the volume.orbital HOMO/LUMO cubes), reached
    # via the collapsed "Molecular Orbitals" sidebar group. This is the H1 fix:
    # a stored MO is a signed field and now renders BOTH the + and − lobe
    # (blue / red), like the on-demand RENDER MO path above — previously the
    # stored path drew the positive lobe only.
    _activate(page, "Molecular Orbitals", settle=4)
    _shot(page, "18-stored-orbital.png")


def _nacl_shots(page: Page) -> None:
    """NaCl showcase: periodic structure + DOS."""
    _shot(page, "16-periodic-structure.png")
    _activate(page, "Density of States", settle=5)
    _shot(page, "17-periodic-dos.png")


# ── session driver ────────────────────────────────────────────────────


def _session(files: list[Path], shots_fn) -> None:
    """Launch a viewer server, run ``shots_fn(page)``, tear down."""
    if _port_open(PORT):
        raise RuntimeError(f"capture port {PORT} is already in use; leave that server alone")
    log = ROOT / "docs" / "_build" / f"capture-{files[0].stem}.log"
    server_log = log.open("w")
    proc = subprocess.Popen(
        [
            str(VIEWER),
            "-m",
            "vibeview",
            "open",
            *[str(f) for f in files],
            "--no-browser",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
        ],
        env={**os.environ, "PYVISTA_OFF_SCREEN": "True", "PYTHONPATH": str(ROOT / "src")},
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_port(PORT)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="domcontentloaded")
            _wait_ready(page)
            shots_fn(page)
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        server_log.close()


def main() -> int:
    from capture_inputs import prepare_inputs

    global ALL_SECTIONS, H2CO, NACL
    inputs = prepare_inputs()
    ALL_SECTIONS, H2CO, NACL = inputs["all"], inputs["h2co"], inputs["nacl"]
    if not VIEWER.exists():
        print(f"viewer not found at {VIEWER}", file=sys.stderr)
        return 1
    try:
        from PIL import Image as _  # noqa: F401
    except ImportError:
        print(
            "Pillow is required for GIF assembly: pip install pillow",
            file=sys.stderr,
        )
        return 1

    IMAGES.mkdir(parents=True, exist_ok=True)

    if ALL_SECTIONS.exists():
        print("Capturing multi-section fixture (all_sections)…")
        _session([ALL_SECTIONS], _all_sections_shots)
    else:
        print(f"  (skipped — {ALL_SECTIONS.name} not found)", file=sys.stderr)

    if H2CO.exists():
        print("Capturing H₂CO showcase (orbital)…")
        _session([H2CO], _h2co_shots)

    if NACL.exists():
        print("Capturing NaCl showcase (periodic + DOS)…")
        _session([NACL], _nacl_shots)

    print("Capturing the standalone demo…")
    _session([inputs["demo"]], lambda page: _shot(page, "00-demo.png"))
    print(f"Done. Captures saved to {IMAGES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
