#!/usr/bin/env python
"""Live-exercise the M3 Auto-optimize flow with trusted Playwright events.

Synthetic events (preview_eval / dispatchEvent) are not trusted and Vuetify
switches routinely ignore them — this drives the real control path a user
touches (see interact_controls.py for the rationale):

  1. build a *distorted* water QVF (O–H 1.3 / 1.4 Å) in a temp dir;
  2. open it in the viewer, enter edit mode (keyboard ``e``);
  3. trusted-flip the **Auto-optimize** switch → the viewer probes the
     worker subprocess, schedules a relax of the sketch, streams MSINDO
     steps, and settles at "relaxed in N steps";
  4. confirm one undo entry was pushed (undo returns to the sketch);
  5. flip the switch off (status clears) and on again → a second relax
     cycle runs end-to-end;
  6. screenshot as evidence.

Dev/QA tool, not part of the package (Playwright is not a dependency).
Run::

    PYVISTA_OFF_SCREEN=True .venv/bin/python vibe-view/docs/qa_live_opt.py [--tmp]

``--tmp`` writes the screenshot to a throwaway dir instead of docs/images/.
Exit status is non-zero if any check fails. Requires vibe-qc importable in
the venv (the whole point is driving the real worker).
"""

from __future__ import annotations

import os
import re
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
PORT = 8141  # isolated: dev 8080, capture 8137, interact 8139

_DISTORTED_WATER_XYZ = """3
distorted water sketch
O 0.0 0.0 0.0
H 1.3 0.0 0.0
H 0.0 1.4 0.2
"""


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


def _state(page: Page, key: str):
    return page.evaluate(
        "(k) => { try { const v = window.trame.state.get(k);"
        " return v === undefined ? null : v; } catch (e) { return null; } }",
        key,
    )


def _wait_state(page: Page, key: str, pred, timeout: float = 10.0):
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


def _wait_ready(page: Page) -> None:
    page.wait_for_selector(".v-list-item", timeout=30_000)
    for _ in range(40):
        has_canvas = page.evaluate("() => !!document.querySelector('canvas')")
        loading = page.evaluate(
            "() => /Loading|Awaiting|Connection closed/.test(document.body.innerText||'')"
        )
        if has_canvas and not loading:
            break
        time.sleep(0.5)
    time.sleep(2.0)


def _toggle_switch(page: Page, label: str, key: str, want: bool, timeout: float = 8.0) -> bool:
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


_RELAXED = re.compile(r"relaxed in \d+ steps")


def _wait_relaxed(page: Page, timeout: float = 120.0) -> str:
    """Wait for a relax cycle to finish (probe + subprocess + stream)."""
    status = _wait_state(
        page,
        "live_opt_status",
        lambda v: isinstance(v, str) and (_RELAXED.search(v) or "error" in v or "unavailable" in v),
        timeout,
    )
    return status or ""


def _run(qvf: Path, outdir: Path) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    proc = subprocess.Popen(
        [
            str(VIEWER), "open", str(qvf),
            "--no-browser", "--host", "127.0.0.1", "--port", str(PORT),
        ],
        env={**os.environ, "PYVISTA_OFF_SCREEN": "True"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_port(PORT)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="domcontentloaded")
            _wait_ready(page)

            # 0. Activate the Structure section — the right control drawer
            # (which hosts the Atom Editor card) is v_if-gated on
            # selected_section.
            page.locator(".v-list-item", has_text="Structure").first.click()
            ok = bool(_wait_state(page, "selected_section", lambda v: v))
            results.append(
                ("structure section active", ok, f"selected={_state(page, 'selected_section')}")
            )

            # 1. Edit mode via the keyboard shortcut (trusted key event).
            page.keyboard.press("e")
            ok = _wait_state(page, "edit_mode", lambda v: v is True) is True
            results.append(("edit mode (key e)", ok, f"edit_mode={_state(page, 'edit_mode')}"))

            # 2. Auto-optimize switch → probe + first relax of the sketch.
            ok = _toggle_switch(page, "Auto-optimize", "live_opt_enabled", True)
            results.append(
                ("Auto-optimize switch on", ok, f"enabled={_state(page, 'live_opt_enabled')}")
            )

            status = _wait_relaxed(page)
            ok = bool(_RELAXED.search(status))
            results.append(("first relax completes", ok, f"status={status!r}"))

            hist = _state(page, "edit_history") or []
            ok = len(hist) >= 1
            results.append(
                ("undo entry pushed", ok, f"edit_history.length={len(hist)}")
            )

            # 3. Off → status clears; on again → a second relax cycle.
            ok = _toggle_switch(page, "Auto-optimize", "live_opt_enabled", False)
            cleared = _wait_state(page, "live_opt_status", lambda v: v == "")
            results.append(
                ("switch off clears status", ok and cleared == "", f"status={cleared!r}")
            )

            ok = _toggle_switch(page, "Auto-optimize", "live_opt_enabled", True)
            status = _wait_relaxed(page)
            ok = ok and bool(_RELAXED.search(status))
            results.append(("second relax completes", ok, f"status={status!r}"))

            page.screenshot(path=str(outdir / "qa_live_opt_final.png"))
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    return results


def main(argv: list[str]) -> int:
    use_tmp = "--tmp" in argv
    if not VIEWER.exists():
        print(f"viewer not found at {VIEWER}", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="vibe-view-liveopt-"))
    xyz = workdir / "distorted_water.xyz"
    xyz.write_text(_DISTORTED_WATER_XYZ)
    from vibeview.converters import xyz_to_qvf

    qvf = workdir / "distorted_water.qvf"
    qvf.write_bytes(xyz_to_qvf(xyz).getvalue())

    if use_tmp:
        outdir = workdir
    else:
        outdir = Path(__file__).resolve().parent / "images"
        outdir.mkdir(parents=True, exist_ok=True)

    print(f"Driving Auto-optimize on {qvf.name} …")
    results = _run(qvf, outdir)

    print("\nLive-opt checks:")
    width = max(len(name) for name, _, _ in results)
    n_pass = 0
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        n_pass += ok
        print(f"  [{mark}] {name.ljust(width)}  {detail}")
    print(f"\n{n_pass}/{len(results)} passed. Screenshot: {outdir / 'qa_live_opt_final.png'}")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
