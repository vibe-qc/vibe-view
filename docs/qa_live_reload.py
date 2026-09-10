#!/usr/bin/env python
"""Live-QA the M2 auto-reload path with trusted Playwright events.

Drives the real viewer end-to-end against a *live* QVF that is rewritten
on disk mid-session, the way a running vibe-qc job rewrites its
checkpoint QVF:

  1. copy the water showcase to a temp file and open the viewer on it;
  2. activate the SCF Convergence panel;
  3. flip the **Auto-reload on file change** switch (trusted click —
     synthetic events are ignored by Vuetify, see interact_controls.py);
  4. append an SCF iteration to the file (member + manifest sha updated,
     as the producer's checkpoint writer does) → expect the *panel-only*
     hot-reload: panel refreshes, selection kept, no scene rebuild;
  5. clone a section under a new id → expect the *full* reload: sidebar
     grows, selection restored.

Dev/QA tool, not part of the package (Playwright is not a dependency)::

    PYVISTA_OFF_SCREEN=True ../.venv/bin/python docs/qa_live_reload.py

Kills anything already listening on its port first — a stale server
serving old templates has burned two sessions before.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VIEWER = ROOT / ".venv" / "bin" / "vibe-view"
WATER = ROOT / "examples/vibe_view/runs/qvf_showcase/water.qvf"
PORT = 8141  # isolated from dev (8080), capture (8137), interact (8139)


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


def _wait_state(page, key, pred, timeout=20.0):
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


def _rewrite_qvf(path, mutate):
    with zipfile.ZipFile(path) as zin:
        manifest = json.loads(zin.read("manifest.json"))
        members = {i.filename: zin.read(i.filename) for i in zin.infolist()}
    mutate(manifest, members)
    members["manifest.json"] = json.dumps(manifest).encode()
    tmp = path.with_suffix(".qvf.tmp")
    with zipfile.ZipFile(tmp, "w") as zout:
        for name, blob in members.items():
            zout.writestr(name, blob)
    tmp.replace(path)  # atomic, like a careful checkpoint writer


def _append_scf_iteration(path, energy_eh):
    def _mutate(manifest, members):
        sec = next(s for s in manifest["sections"] if s["kind"] == "scf_history")
        mpath = sec["members"]["iterations"]["path"]
        data = json.loads(members[mpath])
        data["iterations"].append(
            {"iter": len(data["iterations"]) + 1, "energy_eh": energy_eh}
        )
        blob = json.dumps(data).encode()
        members[mpath] = blob
        sec["members"]["iterations"]["sha256"] = hashlib.sha256(blob).hexdigest()

    _rewrite_qvf(path, _mutate)


def _clone_section(path, src_id, new_id):
    def _mutate(manifest, members):
        src = next(s for s in manifest["sections"] if s["id"] == src_id)
        clone = json.loads(json.dumps(src))
        clone["id"] = new_id
        manifest["sections"].append(clone)

    _rewrite_qvf(path, _mutate)


def main() -> int:
    if not VIEWER.exists() or not WATER.exists():
        print("viewer or water showcase missing", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="vibeview-live-"))
    live = workdir / "live-job.qvf"
    shutil.copy(WATER, live)

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
                viewport={"width": 1440, "height": 880}, device_scale_factor=1
            )
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="domcontentloaded")
            page.wait_for_selector(".v-list-item", timeout=30_000)
            time.sleep(3.0)

            # 1. open the SCF panel
            page.locator(".v-list-item", has_text="SCF Convergence").first.click()
            _wait_state(page, "selected_section", lambda v: v == "scf_hist0")
            baseline = _state(page, "chart_html")
            if baseline:
                ok("scf panel active")
            else:
                fail("scf panel active", "chart_html empty")

            # 2. speed the poll up for QA, then flip the switch (trusted)
            page.evaluate("() => window.trame.state.set('file_watcher_interval', 1.0)")
            page.locator(".v-switch", has_text="Auto-reload on file change").click()
            st = _wait_state(page, "file_watcher_enabled", bool)
            if st:
                ok("auto-reload switch enables watcher")
            else:
                fail("auto-reload switch enables watcher", f"state={st!r}")

            # 3. checkpoint append → panel-only hot-reload
            _append_scf_iteration(live, -99.9)
            msg = _wait_state(
                page, "status_message",
                lambda v: v and "Reloaded from disk" in v and "scf_hist0" in v,
            )
            if msg and "scf_hist0" in (msg or ""):
                ok(f"panel-only reload fired ({msg})")
            else:
                fail("panel-only reload fired", f"status={msg!r}")
            html = _state(page, "chart_html")
            if html and html != baseline:
                ok("scf panel re-rendered from fresh reader")
            else:
                fail("scf panel re-rendered from fresh reader",
                     "html unchanged — stale zip handle?")
            sel = _state(page, "selected_section")
            if sel == "scf_hist0":
                ok("selection preserved on panel-only reload")
            else:
                fail("selection preserved on panel-only reload", f"selected={sel!r}")

            # 4. new section appears → full reload, sidebar grows, selection restored
            n_before = len(_state(page, "sidebar_entries") or [])
            _clone_section(live, "scf_hist0", "scf_hist1")
            msg = _wait_state(
                page, "status_message",
                lambda v: v and "scf_hist1" in v,
            )
            entries = _wait_state(
                page, "sidebar_entries", lambda v: v and len(v) > n_before
            )
            if entries and len(entries) > n_before:
                ok("full reload picked up the new section in the sidebar")
            else:
                fail("full reload picked up the new section",
                     f"{n_before} → {len(entries or [])} entries; status={msg!r}")
            sel = _wait_state(page, "selected_section", lambda v: v == "scf_hist0")
            if sel == "scf_hist0":
                ok("selection restored after full reload")
            else:
                fail("selection restored after full reload", f"selected={sel!r}")

            page.screenshot(path=str(workdir / "live_reload_qa.png"))
            print(f"screenshot: {workdir / 'live_reload_qa.png'}")
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"{'OK' if not failures else 'FAILED'} — {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
