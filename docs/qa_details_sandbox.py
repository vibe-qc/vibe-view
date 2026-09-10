"""Live check: the details iframe's sandbox attribute in the real DOM."""
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
PY = str(REPO / ".venv" / "bin" / "python")
qvf = Path(sys.argv[1])
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
proc = subprocess.Popen(
    [PY, "-m", "vibeview.cli", "open", str(qvf), "--no-browser", "--port", str(port)],
    env={"PYVISTA_OFF_SCREEN": "True", "PATH": "/usr/bin:/bin",
         "PYTHONPATH": f"{REPO}/vibe-view/src:{REPO}/python"},
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
        page.locator(".v-list-item", has_text="Run Record").first.click()
        page.frame_locator("iframe[srcdoc]").locator("body").inner_text(timeout=15000)
        sb = page.evaluate(
            "() => { const f=document.querySelector('iframe[srcdoc]');"
            " return f ? f.getAttribute('sandbox') : 'NO IFRAME'; }")
        # does script actually execute inside it?
        ran = page.evaluate(
            "() => { const f=document.querySelector('iframe[srcdoc]');"
            " try { return !!(f.contentWindow && f.contentWindow.__ranScript); }"
            " catch(e) { return 'blocked:'+e.name; } }")
        print(f"run.record panel sandbox attr = {sb!r}")
        print(f"allow-scripts present        = {'allow-scripts' in (sb or '')}")
        print(f"script executed in panel     = {ran}")
        b.close()
finally:
    proc.terminate()
