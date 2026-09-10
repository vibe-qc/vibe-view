"""Can archive-supplied strings execute inside the scripted plotly panels?"""
import contextlib
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv" / "bin" / "python")
qvf = Path(sys.argv[1])
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
proc = subprocess.Popen(
    [PY, "-m", "vibeview.cli", "open", str(qvf), "--no-browser", "--port", str(port)],
    env={"PYVISTA_OFF_SCREEN": "True", "PATH": "/usr/bin:/bin",
         "PYTHONPATH": f"{ROOT}/vibe-view/src:{ROOT}/python"},
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(90):
        try:
            with socket.create_connection(("127.0.0.1", port), 1):
                break
        except OSError:
            time.sleep(1)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1500, "height": 950})
        page.goto(f"http://127.0.0.1:{port}/", timeout=60000)
        page.wait_for_selector("canvas", timeout=60000)
        time.sleep(4)
        page.locator(".v-list-item", has_text="Spectrum").first.click()
        time.sleep(4)
        top = page.evaluate("() => !!window.__pwned")
        inner = []
        for f in page.frames:
            if f is page.main_frame:
                continue
            with contextlib.suppress(Exception):  # frame may detach
                inner.append(bool(f.evaluate("() => !!window.__pwned")))
        print("payload executed in TOP window :", top)
        print("payload executed in any IFRAME :", any(inner), f"({len(inner)} frames checked)")
        print("spectra panel rendered         :", bool(page.evaluate(
            "()=>window.trame.state.get('spectra_html')")))
        b.close()
finally:
    proc.terminate()
