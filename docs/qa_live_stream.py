#!/usr/bin/env python
"""Live-QA the M4 streaming path: the viewer follows a checkpoint QVF that
is rewritten while a job "runs", ending on a converged snapshot.

Rather than a real vibe-qc job (whose molecular checkpoint carries only a
``structure`` section and finishes in seconds — too fast and too sparse to
observe the streaming story), this drives a *synthetic progressive
producer*: a background thread that atomically rewrites ``checkpoint.qvf``
with a growing subset of the water showcase's sections and
``provenance.run_status`` climbing ``running`` → ``converged``. That is a
faithful stand-in for a producer's ``QvfCheckpointer`` output (same
manifest fields: ``run_status``, monotonic ``checkpoint.seq``,
``partial``), and it is deterministic — the sleeps make each frame
observable and the atomic tmp+replace exercises the watcher's settle-delay
/ mid-write hold.

The viewer opens the first snapshot, auto-reload is flipped on with trusted
Playwright input, and the test asserts the viewer follows the stream:

  * the app-bar chip shows ``running`` on open;
  * sections stream in across reloads (the sidebar grows);
  * the chip flips to ``converged`` and the status bar announces the finish.

Dev/QA tool, not part of the package (Playwright is not a dependency)::

    PYVISTA_OFF_SCREEN=True .venv/bin/python docs/qa_live_stream.py

Kills anything already listening on its port first — a stale server
serving old templates has burned sessions before.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VIEWER = ROOT / ".venv" / "bin" / "vibe-view"
WATER = ROOT / "examples/vibe_view/runs/qvf_showcase/water.qvf"
PORT = 8142  # isolated from dev (8080) and the other QA tools (8137/39/41)

# Section reveal order: each frame lists one more of water.qvf's sections,
# so the sidebar visibly grows as the "job" streams results. All member
# blobs live in every zip (harmless extras), so each listed section's
# sha256 already matches — the manifest subset is the only thing that
# changes frame to frame.
_REVEAL = [
    ["structure"],
    ["structure", "scf_hist0"],
    ["structure", "scf_hist0", "traj0"],
    ["structure", "scf_hist0", "traj0", "vib0", "ir_spec", "citations0"],
]


def _kill_port(port: int) -> None:
    pids = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True
    ).stdout.split()
    for pid in pids:
        subprocess.run(["kill", "-9", pid], check=False)
    if pids:
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


def _load_water():
    """(base manifest dict, {member filename: bytes}) from the showcase."""
    with zipfile.ZipFile(WATER) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        members = {i.filename: zf.read(i.filename) for i in zf.infolist()}
    by_id = {s["id"]: s for s in manifest["sections"]}
    return manifest, members, by_id


def _write_checkpoint(path: Path, manifest, members, by_id, section_ids, seq, status):
    """Atomically write a checkpoint snapshot listing ``section_ids`` with
    the given streaming provenance — as a producer's QvfCheckpointer does."""
    snap = dict(manifest)
    snap["sections"] = [by_id[sid] for sid in section_ids if sid in by_id]
    prov = dict(snap.get("provenance", {}))
    prov["run_status"] = status
    prov["checkpoint"] = {"seq": seq, "wall_time_s": float(seq), "written_at": "n/a"}
    snap["provenance"] = prov
    tmp = path.with_suffix(".qvf.tmp")
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr("manifest.json", json.dumps(snap))
        for name, blob in members.items():
            if name != "manifest.json":
                z.writestr(name, blob)
    tmp.replace(path)  # atomic, like a careful checkpoint writer


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
    if not VIEWER.exists() or not WATER.exists():
        print("viewer or water showcase missing", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="vibeview-stream-"))
    ckpt = workdir / "checkpoint.qvf"
    manifest, members, by_id = _load_water()

    # First snapshot: structure only, job running.
    _write_checkpoint(ckpt, manifest, members, by_id, _REVEAL[0], seq=1, status="running")

    failures: list[str] = []
    ok = lambda name: print(f"  PASS  {name}")  # noqa: E731

    def fail(name, detail):
        failures.append(name)
        print(f"  FAIL  {name}: {detail}")

    # The producer thread reveals the remaining frames on a slow cadence,
    # ending converged. Started once the viewer is watching.
    start_evt = threading.Event()

    def _produce():
        start_evt.wait()
        for i, ids in enumerate(_REVEAL[1:], start=2):
            time.sleep(1.6)
            status = "converged" if i == len(_REVEAL) else "running"
            _write_checkpoint(ckpt, manifest, members, by_id, ids, seq=i, status=status)

    producer = threading.Thread(target=_produce, daemon=True)
    producer.start()

    _kill_port(PORT)
    viewer = subprocess.Popen(
        [str(VIEWER), "open", str(ckpt), "--no-browser",
         "--host", "127.0.0.1", "--port", str(PORT)],
        env={**os.environ, "PYVISTA_OFF_SCREEN": "True"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
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

            status = _state(page, "live_run_status")
            if status == "running":
                ok("live chip shows running on open")
            else:
                fail("live chip shows running on open", f"live_run_status={status!r}")

            # The right control panel (host of the auto-reload switch) only
            # renders once a section is selected.
            page.locator(".v-list-item", has_text="Structure").first.click()
            _wait_state(page, "selected_section", bool, timeout=10)

            # speed the poll up for QA, then trusted-flip the switch and
            # release the producer thread.
            page.evaluate("() => window.trame.state.set('file_watcher_interval', 1.0)")
            page.locator(".v-switch", has_text="Auto-reload on file change").click()
            if _wait_state(page, "file_watcher_enabled", bool, timeout=10):
                ok("auto-reload enabled")
            else:
                fail("auto-reload enabled", "switch did not enable watcher")
            start_evt.set()

            n0 = len(_state(page, "sidebar_entries") or [])
            entries = _wait_state(
                page, "sidebar_entries", lambda v: v and len(v) > n0, timeout=30
            )
            if entries and len(entries) > n0:
                ok(f"sections stream in while running ({n0} -> {len(entries)})")
            else:
                fail("sections stream in while running", f"sidebar stuck at {n0}")

            final = _wait_state(
                page, "live_run_status",
                lambda v: v in ("converged", "failed"), timeout=30,
            )
            if final == "converged":
                ok("chip settles to converged")
            else:
                fail("chip settles to converged", f"live_run_status={final!r}")
            msg = _wait_state(
                page, "status_message", lambda v: v and "converged" in v, timeout=10
            )
            if msg and "converged" in msg:
                ok(f"finish announced ({msg})")
            else:
                fail("finish announced", f"status={msg!r}")

            page.screenshot(path=str(workdir / "live_stream_qa.png"))
            print(f"screenshot: {workdir / 'live_stream_qa.png'}")
            browser.close()
    finally:
        viewer.terminate()
        try:
            viewer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            viewer.kill()

    print(f"{'OK' if not failures else 'FAILED'} — {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
