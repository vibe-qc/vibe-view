#!/usr/bin/env python
"""Live-QA the QVF job-container lifecycle with trusted Playwright events.

Drives the real viewer end-to-end through a container's whole life, the
way a user watches a queued job:

  1. build a *pending* H2/STO-3G container and open the viewer on it —
     expect the amber "pending" chip and the Job Spec panel's
     "not yet run" banner;
  2. flip **Auto-reload on file change** (trusted click);
  3. execute the container with the real `vibeqc run` in a subprocess —
     on disk it transitions pending → running → converged via atomic
     replaces — and expect the chip to settle "converged";
  4. open the Run Record panel — expect the executed job.spec input,
     the complete log, and the rendered System manifest / Performance
     log / Structured events attachments.

Dev/QA tool, not part of the package (Playwright is not a dependency)::

    PYVISTA_OFF_SCREEN=True ../.venv/bin/python docs/qa_job_container.py

Kills anything already listening on its port first.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VENV_PY = ROOT / ".venv" / "bin" / "python"
VIEWER = ROOT / ".venv" / "bin" / "vibe-view"
PORT = 8143  # isolated from dev (8080) + the other QA harnesses


def _kill_port(port: int) -> None:
    out = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True
    ).stdout.split()
    for pid in out:
        subprocess.run(["kill", "-9", pid], check=False)
    if out:
        time.sleep(1.0)


def _wait_port(port: int, timeout: float = 45.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    raise TimeoutError(f"server never came up on :{port}")


def _state(page, key):
    return page.evaluate(
        "(k) => { try { const v = window.trame.state.get(k);"
        " return v === undefined ? null : v; } catch (e) { return null; } }",
        key,
    )


def _wait_state(page, key, pred, timeout=30.0):
    end = time.time() + timeout
    last = _state(page, key)
    while time.time() < end:
        last = _state(page, key)
        try:
            if pred(last):
                return last
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
    return last


def main() -> int:
    if not VIEWER.exists():
        print("viewer entry point missing", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="vibeview-container-"))
    live = workdir / "job.qvf"
    build = subprocess.run(
        [
            str(VENV_PY),
            "-c",
            (
                "import sys, vibeqc as vq\n"
                "from vibeqc._vibeqc_core import Atom, Molecule\n"
                "mol = Molecule([Atom(1,[0,0,0]), Atom(1,[0,0,1.4])], 0, 1)\n"
                "vq.write_pending_qvf(mol, sys.argv[1][:-4], method='rhf',"
                " basis='sto-3g',"
                " options={'perf_log': True, 'structured_log': True})\n"
            ),
            str(live),
        ],
        capture_output=True,
        text=True,
    )
    if build.returncode != 0 or not live.exists():
        print(f"pending-container build failed: {build.stderr}", file=sys.stderr)
        return 2

    _kill_port(PORT)
    proc = subprocess.Popen(
        [str(VIEWER), "open", str(live), "--no-browser",
         "--host", "127.0.0.1", "--port", str(PORT)],
        env={**os.environ, "PYVISTA_OFF_SCREEN": "True"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    failures: list[str] = []
    ok = lambda name: print(f"  PASS  {name}")  # noqa: E731

    def fail(name, detail):
        failures.append(name)
        print(f"  FAIL  {name}: {detail}")

    try:
        _wait_port(PORT)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(
                viewport={"width": 1440, "height": 880},
                device_scale_factor=1,
            )
            page.goto(
                f"http://127.0.0.1:{PORT}/", wait_until="domcontentloaded"
            )
            page.wait_for_selector(".v-list-item", timeout=30_000)
            time.sleep(3.0)

            # 1. pending chip + Job Spec banner
            status = _state(page, "live_run_status")
            if status == "pending":
                ok("pending chip state on open")
            else:
                fail("pending chip state on open", f"live_run_status={status!r}")
            chip = page.locator(".v-chip", has_text="pending")
            if chip.count() >= 1 and chip.first.is_visible():
                ok("pending chip visible in app bar")
            else:
                fail("pending chip visible in app bar", "no visible chip")

            page.locator(".v-list-item", has_text="Job Spec").first.click()
            html = _wait_state(
                page, "properties_html", lambda v: v and "not yet run" in v
            )
            if html and "not yet run" in html:
                ok("Job Spec panel shows 'not yet run'")
            else:
                fail("Job Spec panel shows 'not yet run'", "banner missing")

            # 2. enable auto-reload (trusted click), speed the poll up
            page.evaluate(
                "() => window.trame.state.set('file_watcher_interval', 1.0)"
            )
            page.locator(
                ".v-switch", has_text="Auto-reload on file change"
            ).click()
            if _wait_state(page, "file_watcher_enabled", bool):
                ok("auto-reload enabled")
            else:
                fail("auto-reload enabled", "switch did not take")

            # 3. run the container for real; the file transitions
            #    pending -> running -> converged on disk.
            run = subprocess.run(
                [
                    str(VENV_PY),
                    "-c",
                    "import sys, vibeqc as vq; vq.run_container(sys.argv[1])",
                    str(live),
                ],
                capture_output=True,
                text=True,
            )
            if run.returncode == 0:
                ok("vibeqc run_container executed the pending archive")
            else:
                fail("vibeqc run_container executed", run.stderr[-400:])

            status = _wait_state(
                page, "live_run_status", lambda v: v == "converged"
            )
            if status == "converged":
                ok("chip settled 'converged' via auto-reload")
            else:
                fail("chip settled 'converged'", f"live_run_status={status!r}")

            # 4. Run Record panel: executed input + log + attachments
            page.locator(".v-list-item", has_text="Run Record").first.click()
            html = _wait_state(
                page,
                "properties_html",
                lambda v: v and "System manifest" in v,
            )
            checks = (
                "System manifest",
                "Performance log",
                "Structured events",
                "Executed declarative job specification",
            )
            for needle in checks:
                if html and needle in html:
                    ok(f"run record renders: {needle}")
                else:
                    fail(f"run record renders: {needle}", "missing")

            page.screenshot(
                path=str(workdir / "qa_job_container_final.png"),
                full_page=False,
            )
            print(f"  shot  {workdir / 'qa_job_container_final.png'}")
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    if failures:
        print(f"\n{len(failures)} FAILURE(S): {failures}", file=sys.stderr)
        return 1
    print("\nall QA checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
