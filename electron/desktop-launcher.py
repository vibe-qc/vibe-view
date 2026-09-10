#!/usr/bin/env python3
"""vibe-view desktop launcher — starts the server and opens the browser.

A lightweight alternative to Electron. No Node.js required.
On macOS, also registers as a proper application via py2app when bundled.
"""

from __future__ import annotations

import subprocess
import sys
import time
import webbrowser
from pathlib import Path


def main():
    import argparse

    parser = argparse.ArgumentParser(description="vibe-view desktop launcher")
    parser.add_argument("file", nargs="?", help="QVF file to open")
    parser.add_argument("--port", type=int, default=8765, help="Server port")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    port = args.port
    url = f"http://127.0.0.1:{port}"
    if args.file:
        url += f"?file={args.file}"

    print(f"vibe-view desktop launcher")
    print(f"  Server: http://127.0.0.1:{port}")
    print(f"  Press Ctrl+C to quit")

    # Start the vibe-view server
    server = subprocess.Popen(
        [sys.executable, "-m", "vibeview.cli", "serve", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for server to be ready
    import http.client

    ready = False
    for _ in range(30):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            conn.request("HEAD", "/")
            conn.getresponse()
            ready = True
            break
        except Exception:
            time.sleep(0.5)

    if not ready:
        print("Error: vibe-view server did not start.")
        server.terminate()
        sys.exit(1)

    print(f"  Server ready at http://127.0.0.1:{port}")

    # Open browser
    if not args.no_browser:
        webbrowser.open(url)
        print(f"  Browser opened")

    # Keep running until Ctrl+C
    try:
        server.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.terminate()
        server.wait()


if __name__ == "__main__":
    main()
