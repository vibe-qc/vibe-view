"""Programmatic entry point for spinning up the vibe-view web server.

`launch_qvf` lets vibe-qc (or any other producer) hand off an in-memory
QVF directly to the viewer without going through a temp file. It is the
in-process equivalent of ``vibe-view open <path>``.
"""

from __future__ import annotations

import socket
import threading
import time
import webbrowser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, QVFSource


def _wait_until_bound_and_open(
    host: str,
    port: int,
    *,
    timeout: float = 10.0,
) -> None:
    """Poll the port until uvicorn binds, then open the browser. Background
    thread, daemon. Matches the CLI's helper at
    ``vibeview.cli._wait_until_bound_and_announce`` minus the click
    echo lines (programmatic callers do their own UI).
    """
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    probe_url = f"http://{probe_host}:{port}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((probe_host, port), timeout=0.5):
                pass
        except OSError:
            time.sleep(0.2)
            continue
        try:
            webbrowser.open(probe_url)
        except Exception:
            pass
        return


def launch_qvf(
    source: QVFSource | QVFReader,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = True,
    print_banner_to_stdout: bool = True,
) -> None:
    """Open a QVF and launch the interactive viewer.

    Parameters
    ----------
    source:
        Either an already-constructed :class:`QVFReader`, or anything
        :class:`QVFReader` accepts: a filesystem path, raw zip bytes,
        or a seekable binary file-like (``BytesIO``, opened file, …).
    host, port:
        Address the Trame server binds to. Default = localhost-only.
    open_browser:
        When True (default), opens ``http://host:port`` in the system
        browser as soon as the server starts.
    print_banner_to_stdout:
        When True (default), prints the section summary banner the same
        way the CLI does. Set False when calling from a programmatic
        context that has its own UI.

    Blocks until the Trame server stops (Ctrl+C). On return the
    underlying :class:`QVFReader` is closed.
    """
    from vibeview.app import create_app, serve
    from vibeview.banner import print_banner
    from vibeview.qvf import QVFReader

    owns_reader = False
    if isinstance(source, QVFReader):
        reader = source
    else:
        reader = QVFReader(source)
        owns_reader = True

    try:
        if print_banner_to_stdout:
            print_banner(reader)

        if open_browser:
            # Deferred to a watcher thread so the open() fires after
            # uvicorn binds. Avoids the race where the browser opens
            # before the port is listening (the user-visible failure
            # mode was an "Unable to connect" page in Firefox even
            # though the CLI banner already said "server starting").
            threading.Thread(
                target=_wait_until_bound_and_open,
                kwargs={"host": host, "port": port},
                daemon=True,
            ).start()

        app = create_app(reader)
        serve(app, host=host, port=port)
    finally:
        if owns_reader:
            reader.close()
