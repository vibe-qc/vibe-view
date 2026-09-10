#!/usr/bin/env python
"""Live-exercise vibe-view's *interactive* controls with Playwright.

The headless ``mcp__Claude_Preview__preview_eval`` harness (and any
``element.click()`` / synthetic-``dispatchEvent`` approach) can drive section
*activation* but **cannot** reliably fire Vuetify control *events* — switches,
sliders, icon play-buttons — or vtk.js hardware picking. Synthetic DOM events
are not *trusted* (``event.isTrusted === false``), so Vuetify's
``update:modelValue`` handlers and vtk.js's interactor often ignore them.

Playwright dispatches **trusted** input into a real (headless) Chromium, so it
*can* drive the controls a real user touches. This tool walks the viewer
through them on a multi-section QVF and, for each, reads the resulting Trame
client state (``window.trame.state.get(...)``) to confirm the handler actually
ran — then saves a screenshot as evidence:

  * **Measure mode** — toggle the switch, click atoms in the 3D viewport,
    confirm a distance readout (``on_pick`` → ``_measure_text``).
  * **Trajectory** — play/pause, confirm the frame advances.
  * **Reaction path** — play, confirm the waypoint label advances.
  * **Vibrations** — play/pause, confirm the animation loop starts.
  * **Clip plane** — toggle the ``Enable`` switch, confirm ``clip_enabled``
    flips *and* the clip actually applies (the switch→``toggle_clip``→
    ``_rebuild_clip`` path, which the unit tests exercise only at the
    ``_rebuild_clip`` end).

This is a **dev/QA tool**, not part of the package: Playwright is not a
runtime, test, or CI dependency, and this script is never imported by
``vibeview``. Install the one-off tooling with::

    pip install playwright && playwright install chromium

Then run it against a QVF that carries the interactive sections (the
``all_sections`` showcase has them all)::

    PYVISTA_OFF_SCREEN=True python vibe-view/docs/interact_controls.py \
        [path/to/file.qvf]

Exit status is non-zero if any check fails, so it can gate a manual QA pass.

By default screenshots land in ``vibe-view/docs/images/`` alongside the static
captures from ``capture_screenshots.py``. Pass ``--tmp`` to write to a
throwaway temp dir instead (useful for automated QA passes where you don't want
to overwrite committed images). The example QVFs carry a sanitized provenance
host (``example-host``) — keep it that way.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VIEWER = ROOT / ".venv" / "bin" / "vibe-view"
VIEWPORT = {"width": 1440, "height": 880}
PORT = 8139  # distinct from the dev default (8080) and the capture tool (8137)
DEFAULT_QVF = ROOT / "examples/vibe_view/runs/all_sections/all_sections.qvf"

# Viewport-centre region of the 3D canvas (sidebar ≈ 290 px, right drawer
# 300 px, app bar ≈ 64 px). The structure auto-frames here on load.
CANVAS_CX, CANVAS_CY = 720, 400


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


# ── page / state helpers ──────────────────────────────────────────────


def _wait_ready(page: Page) -> None:
    """Wait for the Vue app + viewport to be live (sidebar present, spinner gone)."""
    page.wait_for_selector(".v-list-item", timeout=30_000)
    for _ in range(40):
        has_canvas = page.evaluate("() => !!document.querySelector('canvas')")
        loading = page.evaluate(
            "() => /Loading|Awaiting|Connection closed/.test(document.body.innerText||'')"
        )
        if has_canvas and not loading:
            break
        time.sleep(0.5)
    time.sleep(2.0)  # let the first VtkLocalView frame paint


def _state(page: Page, key: str):
    """Read a Trame client-state value (None if unset / client not ready)."""
    return page.evaluate(
        "(k) => { try { const v = window.trame.state.get(k);"
        " return v === undefined ? null : v; } catch (e) { return null; } }",
        key,
    )


def _wait_state(page: Page, key: str, pred, timeout: float = 6.0):
    """Poll a state key until ``pred(value)`` is truthy or timeout; return last value."""
    end = time.time() + timeout
    last = _state(page, key)
    while time.time() < end:
        last = _state(page, key)
        try:
            if pred(last):
                return last
        except Exception:  # noqa: BLE001 — predicate is best-effort
            pass
        time.sleep(0.3)
    return last


def _activate(page: Page, label: str) -> None:
    """Trusted-click a sidebar section entry by visible label."""
    page.locator(".v-list-item", has_text=label).first.click()
    time.sleep(1.6)


def _nudge(page: Page) -> None:
    """Force a fresh WebGL frame before a screenshot (VtkLocalView renders
    on demand; the canvas is ``preserveDrawingBuffer: false``)."""
    if not page.evaluate("() => !!document.querySelector('canvas')"):
        return
    page.mouse.move(CANVAS_CX, CANVAS_CY)
    page.mouse.down()
    page.mouse.move(CANVAS_CX + 10, CANVAS_CY + 8, steps=4)
    page.mouse.up()
    time.sleep(0.6)


def _shot(page: Page, outdir: Path, name: str) -> None:
    _nudge(page)
    page.screenshot(path=str(outdir / name))


# ── control drivers (trusted events) ──────────────────────────────────


def _toggle_switch(page: Page, label: str, key: str, want: bool, timeout: float = 5.0) -> bool:
    """Trusted-click a ``v-switch`` (located by its label) until ``key`` == ``want``.

    Tries the inner selection-control input, then the whole switch, then the
    track — Vuetify renders all three as part of the clickable control.
    """
    sw = page.locator(".v-switch", has_text=label).first
    candidates = [
        sw.locator(".v-selection-control__input").first,
        sw,
        sw.locator(".v-switch__track").first,
    ]
    for target in candidates:
        if _state(page, key) == want:
            return True
        try:
            target.click(timeout=3000)
        except Exception:  # noqa: BLE001 — try the next click target
            continue
        if _wait_state(page, key, lambda v: v == want, timeout) == want:
            return True
    return _state(page, key) == want


def _click_play(page: Page) -> bool:
    """Trusted-click the play/pause icon button (mdi-play ↔ mdi-pause)."""
    for sel in ("button:has(.mdi-play)", "button:has(.mdi-pause)"):
        btn = page.locator(sel).first
        if btn.count() and btn.is_visible():
            btn.click()
            return True
    return False


def _pick_atoms(page: Page, target: int = 2) -> tuple[int, str]:
    """Trusted-click a spread of viewport points; ``on_pick`` maps each to the
    nearest atom (≤ 2 Å). Returns (max atoms simultaneously selected,
    measure_result)."""
    cx, cy = CANVAS_CX, CANVAS_CY
    pts = [
        (cx, cy - 70), (cx + 60, cy - 40), (cx - 60, cy - 40),
        (cx, cy + 40), (cx + 70, cy + 50), (cx - 70, cy + 50),
        (cx, cy + 90), (cx + 90, cy), (cx - 90, cy), (cx, cy - 100),
    ]
    best = 0
    for x, y in pts:
        page.mouse.click(x, y)
        time.sleep(0.5)
        sel = _state(page, "selected_atoms") or []
        best = max(best, len(sel))
        if best >= target:
            break
    return best, (_state(page, "measure_result") or "")


# ── checks ─────────────────────────────────────────────────────────────


def _check_measure(page: Page, outdir: Path) -> tuple[str, bool, str]:
    _activate(page, "Structure")
    on = _toggle_switch(page, "Measure mode", "measure_mode", True)
    best, mr = (0, "")
    if on:
        best, mr = _pick_atoms(page, target=2)
    _shot(page, outdir, "01-measure.png")
    _toggle_switch(page, "Measure mode", "measure_mode", False)  # restore
    first = mr.replace("\n", " | ")[:80] if mr else "—"
    ok = on and best >= 2 and ("Distance" in mr or "Å" in mr)
    return (
        "measure-mode switch + atom pick",
        ok,
        f"measure_mode={on}, atoms_selected={best}, result={first!r}",
    )


def _check_trajectory(page: Page, outdir: Path) -> tuple[str, bool, str]:
    _activate(page, "Trajectory")
    f0 = _state(page, "trajectory_frame")
    started = _click_play(page)
    playing = _wait_state(page, "trajectory_playing", lambda v: v is True, 4.0)
    # Frame should advance while playing.
    advanced = _wait_state(page, "trajectory_frame", lambda v: v != f0, 5.0)
    _shot(page, outdir, "02-trajectory.png")
    if _state(page, "trajectory_playing"):
        _click_play(page)  # pause
    paused = _wait_state(page, "trajectory_playing", lambda v: v is False, 4.0)
    ok = bool(started) and playing is True and advanced != f0 and paused is False
    return (
        "trajectory play/pause",
        ok,
        f"play_clicked={started}, playing={playing}, frame {f0}→{advanced}, paused={paused is False}",
    )


def _check_reaction(page: Page, outdir: Path) -> tuple[str, bool, str]:
    _activate(page, "Reaction Path")
    f0 = _state(page, "trajectory_frame")
    started = _click_play(page)
    # Either the frame advances or the waypoint label becomes non-empty.
    advanced = _wait_state(page, "trajectory_frame", lambda v: v != f0, 5.0)
    label = _wait_state(page, "reaction_current_label", lambda v: bool(v), 5.0)
    _shot(page, outdir, "03-reaction.png")
    if _state(page, "trajectory_playing"):
        _click_play(page)  # pause
    ok = bool(started) and (advanced != f0 or bool(label))
    return (
        "reaction-path play (label advance)",
        ok,
        f"play_clicked={started}, frame {f0}→{advanced}, label={(label or '—')!r}",
    )


def _check_vibration(page: Page, outdir: Path) -> tuple[str, bool, str]:
    _activate(page, "Vibrations")
    started = _click_play(page)
    playing = _wait_state(page, "vibration_playing", lambda v: v is True, 4.0)
    _shot(page, outdir, "04-vibration.png")
    if _state(page, "vibration_playing"):
        _click_play(page)  # pause
    paused = _wait_state(page, "vibration_playing", lambda v: v is False, 4.0)
    ok = bool(started) and playing is True and paused is False
    return (
        "vibration play/pause",
        ok,
        f"play_clicked={started}, playing={playing}, paused={paused is False}",
    )


def _check_clip(page: Page, outdir: Path) -> tuple[str, bool, str]:
    _activate(page, "Difference Density")
    time.sleep(2.0)  # let the isosurface build
    on = _toggle_switch(page, "Enable", "clip_enabled", True)
    # ``clip_enabled`` flips via ``v_model`` regardless of whether the handler
    # ran; the ``toggle_clip`` → ``_rebuild_clip`` status message is the
    # client-observable proof the clip *actually applied*. Asserting only the
    # flag would miss a handler that errored (UI-OBS-G: the zero-arg
    # ``toggle_clip`` raised ``TypeError`` on the ``[$event]`` payload, so the
    # flag flipped but the clip never applied).
    applied = _wait_state(
        page, "status_message",
        lambda v: v and "clip plane enabled" in str(v).lower(), 4.0,
    )
    _shot(page, outdir, "05-clip.png")
    _toggle_switch(page, "Enable", "clip_enabled", False)  # restore
    handler_ran = bool(applied and "clip plane enabled" in str(applied).lower())
    ok = on and handler_ran
    detail = f"clip_enabled={on}, status={(applied or '—')!r}"
    if on and not handler_ran:
        detail += "  (flag flipped but handler never ran — toggle_clip wiring?)"
    return ("clip Enable switch", ok, detail)


def _check_hover_tooltip(page: Page, outdir: Path) -> tuple[str, bool, str]:
    """Hover an atom → the tooltip must appear with 'SYM · atom N'.

    Runs on vtk.js hover picking (debounced: fires when the pointer
    pauses), which also feeds the right-click menu's target. Sweep the
    canvas centre row, pausing at each step so the pick fires.
    """
    _activate(page, "Structure")
    hits: list[str] = []
    for dx in range(-200, 201, 25):
        page.mouse.move(CANVAS_CX + dx, CANVAS_CY)
        time.sleep(0.35)
        if _state(page, "hover_tooltip_visible"):
            hits.append(str(_state(page, "hover_tooltip")))
    _shot(page, outdir, "06-hover-tooltip.png")
    ok = any("atom" in h for h in hits)
    return (
        "hover tooltip over atoms",
        ok,
        f"{len(hits)} hover positions hit, e.g. {hits[:2]}",
    )


def _check_context_menu(page: Page, outdir: Path) -> tuple[str, bool, str]:
    """Right-click over an atom → menu opens targeted (atom_idx >= 0).

    The DOM contextmenu event has no world position; the target comes from
    the last debounced hover pick, snapshotted by context_menu_opened. A
    fresh pick always precedes the click because the pointer pauses to
    right-click.
    """
    _activate(page, "Structure")
    # Find an atom by hovering. Take the MIDDLE of the run of hits, not the
    # last: the state read lags the pick by roughly one sweep step, so the
    # last "visible" position can sit just past the atom's edge, where the
    # fresh pick fired by the pre-click pause resolves to empty space.
    hit_dx: list[int] = []
    for dx in range(-200, 201, 25):
        page.mouse.move(CANVAS_CX + dx, CANVAS_CY)
        time.sleep(0.35)
        if _state(page, "hover_tooltip_visible"):
            hit_dx.append(dx)
    if not hit_dx:
        return ("right-click menu targeting", False, "no atom found by hover")
    spot = (CANVAS_CX + hit_dx[len(hit_dx) // 2], CANVAS_CY)
    # On heavier scenes the hardware-pick round trip can outlast any fixed
    # sleep, and a right-click that lands before the pick at the spot
    # completes snapshots the previous (possibly empty) pick. A user just
    # pauses on the atom (the tooltip visibly catches up) or right-clicks
    # again, so the harness retries a few times. Park on-canvas between
    # attempts; note that "tooltip not visible" never proves picking is
    # dead — a pick resolving just past the 1.5 Å atom threshold keeps
    # the tooltip hidden while picking is alive (a visibility-only probe
    # once misread that as hover dying after an off-canvas excursion;
    # pick-count instrumentation showed picking fine throughout).
    idx, shown = -1, False
    for attempt in range(3):
        page.mouse.move(CANVAS_CX - 320, CANVAS_CY - 220)  # empty canvas
        time.sleep(0.8)
        page.mouse.move(*spot)
        _wait_state(page, "hover_tooltip_visible", bool, timeout=5.0)
        page.mouse.click(*spot, button="right")
        time.sleep(1.5)
        idx = _state(page, "context_menu_atom_idx")
        shown = bool(_state(page, "context_menu_show"))
        if attempt == 0:
            _shot(page, outdir, "07-context-menu.png")
        page.keyboard.press("Escape")  # close the menu again
        time.sleep(0.5)
        if shown and idx is not None and int(idx) >= 0:
            break
    ok = shown and idx is not None and int(idx) >= 0
    return (
        "right-click menu targeting",
        ok,
        f"menu_shown={shown}, atom_idx={idx}",
    )


def _check_toon(page: Page, outdir: Path) -> tuple[str, bool, str]:
    """Toon switch → toon_mode flips and the canvas visibly changes.

    Pinned here because the switch was dead in the real UI while every
    server-side test passed: it sends [$event] but the controller took
    zero arguments (TypeError on each flip), and the flip-vs-v_model
    double-toggle would have cancelled it anyway.
    """
    _activate(page, "Structure")
    _nudge(page)
    img0 = page.locator("canvas").first.screenshot()
    on = _toggle_switch(page, "Toon / NPR shading", "toon_mode", True)
    time.sleep(2)
    _nudge(page)  # force a fresh WebGL frame; the buffer is not preserved
    img1 = page.locator("canvas").first.screenshot()
    _shot(page, outdir, "08-toon.png")
    _toggle_switch(page, "Toon / NPR shading", "toon_mode", False)  # restore
    changed = img0 != img1  # byte-inequality is enough alongside the state check
    ok = on and changed
    return (
        "toon shading switch",
        ok,
        f"toon_mode={on}, canvas_changed={changed}",
    )


def _check_element_picker(page: Page, outdir: Path) -> tuple[str, bool, str]:
    """Periodic-table picker: open from 'New: <El>', click nitrogen,
    expect edit_new_element to change and the dialog to close."""
    _activate(page, "Structure")
    # Keyboard shortcuts are unreliable mid-run (focus may sit on a form
    # control from an earlier check, which swallows them) — click the
    # toolbar pencil like a user would, and poll for the card itself.
    if not _state(page, "edit_mode"):
        page.locator("button[title='Edit mode']").first.click()
        if not _wait_state(page, "edit_mode", bool, timeout=4.0):
            return ("periodic-table element picker", False, "edit mode never engaged")
    end = time.time() + 6.0
    while time.time() < end:
        if page.evaluate("() => document.body.innerText.includes('Atom Editor')"):
            break
        time.sleep(0.4)
    else:
        return (
            "periodic-table element picker", False,
            f"Atom Editor card never rendered (edit_mode={_state(page, 'edit_mode')})",
        )
    btn = page.locator("button", has_text="New:").first
    btn.scroll_into_view_if_needed(timeout=8000)
    btn.click()
    time.sleep(1.2)
    opened = bool(_state(page, "element_picker_open"))
    page.locator("button[title='N · Z=7']").first.click()
    time.sleep(1.2)
    picked = _state(page, "edit_new_element")
    closed = not _state(page, "element_picker_open")
    _shot(page, outdir, "09-element-picker.png")
    # restore defaults for later checks
    btn.click()
    time.sleep(1.0)
    page.locator("button[title='C · Z=6']").first.click()
    time.sleep(0.8)
    page.locator("button[title='Edit mode']").first.click()  # leave edit mode
    time.sleep(0.5)
    ok = opened and picked == "N" and closed
    return (
        "periodic-table element picker",
        ok,
        f"opened={opened}, picked={picked!r}, closed={closed}",
    )


def _check_symmetrize(page: Page, outdir: Path) -> tuple[str, bool, str]:
    """Detect the point group via the command palette, then click
    Symmetrize. Passing means the wiring ran end to end: either a
    'Symmetrized …' status or the action's honest refusal — never a
    silent nothing."""
    _activate(page, "Structure")
    page.keyboard.press("ControlOrMeta+k")
    time.sleep(1.0)
    if not _state(page, "palette_open"):
        return ("symmetrize via palette", False, "palette did not open")
    page.keyboard.type("point group")
    time.sleep(0.6)
    page.locator(".v-list-item", has_text="Detect point group").first.click()
    ok_detect = bool(
        _wait_state(page, "point_group", lambda v: bool(v), timeout=6.0)
    )
    if not ok_detect:
        return ("symmetrize via palette", False, "point group never detected")
    btn = page.locator("button", has_text="Symmetrize to").first
    btn.scroll_into_view_if_needed(timeout=8000)
    btn.click()
    time.sleep(2.0)
    status = str(_state(page, "status_message") or "")
    _shot(page, outdir, "10-symmetrize.png")
    ok = status.startswith("Symmetrized") or status.startswith("Symmetrize:")
    return (
        "symmetrize via palette",
        ok,
        f"point_group={_state(page, 'point_group')!r}, status={status[:70]!r}",
    )


CHECKS = [
    _check_measure,
    _check_trajectory,
    _check_reaction,
    _check_vibration,
    _check_clip,
    _check_hover_tooltip,
    _check_context_menu,
    _check_toon,
    _check_element_picker,
    _check_symmetrize,
]


# ── session driver ─────────────────────────────────────────────────────


def _run(qvf: Path, outdir: Path) -> list[tuple[str, bool, str]]:
    proc = subprocess.Popen(
        [
            str(VIEWER), "open", str(qvf),
            "--no-browser", "--host", "127.0.0.1", "--port", str(PORT),
        ],
        env={**os.environ, "PYVISTA_OFF_SCREEN": "True"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    results: list[tuple[str, bool, str]] = []
    try:
        _wait_port(PORT)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="domcontentloaded")
            _wait_ready(page)
            for check in CHECKS:
                try:
                    results.append(check(page, outdir))
                except Exception as e:  # noqa: BLE001 — one bad check shouldn't abort the rest
                    results.append((check.__name__.removeprefix("_check_"), False, f"exception: {e}"))
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    return results


def main(argv: list[str]) -> int:
    # Parse: optional --tmp flag + optional QVF path.
    use_tmp = "--tmp" in argv
    args = [a for a in argv[1:] if a != "--tmp"]

    qvf = Path(args[0]).resolve() if args else DEFAULT_QVF
    if not VIEWER.exists():
        print(f"viewer not found at {VIEWER}", file=sys.stderr)
        return 2
    if not qvf.exists():
        print(
            f"QVF not found: {qvf}\n"
            "Pass a .qvf with structure/trajectory/reaction/vibrations/volume "
            "sections (e.g. the all_sections showcase).",
            file=sys.stderr,
        )
        return 2

    if use_tmp:
        outdir = Path(tempfile.mkdtemp(prefix="vibe-view-interact-"))
    else:
        # Default: write alongside the static captures so everything is in one place.
        outdir = Path(__file__).resolve().parent / "images"
        outdir.mkdir(parents=True, exist_ok=True)

    print(f"Driving interactive controls on {qvf.name} …")
    print(f"  screenshots → {outdir.relative_to(outdir.parents[2])}")
    results = _run(qvf, outdir)

    print("\nInteractive-control checks:")
    width = max(len(name) for name, _, _ in results)
    n_pass = 0
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        n_pass += ok
        print(f"  [{mark}] {name.ljust(width)}  {detail}")
    shot_loc = outdir if use_tmp else outdir.relative_to(outdir.parents[2])
    print(f"\n{n_pass}/{len(results)} passed. Screenshots: {shot_loc}")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
