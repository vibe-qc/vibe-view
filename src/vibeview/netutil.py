"""TCP helpers shared by the command line, the launcher and the file browser.

Three modules each polled a port with their own loop: the CLI's
"ready" announcer, the launcher's browser opener (its docstring said it
"matches the CLI's helper minus the echo") and the file browser's
viewer probe; the CLI and the file browser each had their own
"is this port busy" check. These are those helpers, once.

A server bound to the wildcard address ``0.0.0.0`` is reachable through
the loopback address, and Python refuses to *dial* the wildcard, so every
probe goes to :func:`probe_host`.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
import webbrowser
from collections.abc import Callable

WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", ""})
DEFAULT_BIND_TIMEOUT = 10.0
PROBE_TIMEOUT = 0.5
POLL_INTERVAL = 0.2


def probe_host(host: str) -> str:
    """The address to dial for a server bound to ``host``."""
    return "127.0.0.1" if host in WILDCARD_HOSTS else host


def viewer_url(host: str, port: int) -> str:
    """The URL a browser on this machine opens for a server bound to ``host``."""
    return f"http://{probe_host(host)}:{port}"


def port_open(host: str, port: int, *, timeout: float = PROBE_TIMEOUT) -> bool:
    """True if something accepts TCP connections on ``(host, port)`` right now."""
    try:
        with socket.create_connection((probe_host(host), port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port(
    host: str,
    port: int,
    *,
    timeout: float = DEFAULT_BIND_TIMEOUT,
    interval: float = POLL_INTERVAL,
) -> bool:
    """Block until ``(host, port)`` accepts connections; False on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        if port_open(host, port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def announce_when_bound(
    host: str,
    port: int,
    *,
    open_browser: bool,
    announce: Callable[[str], None] | None = None,
    warn: Callable[[str], None] | None = None,
    timeout: float = DEFAULT_BIND_TIMEOUT,
) -> threading.Thread:
    """Start a daemon thread that reports readiness once the server binds.

    The thread polls the port, then calls ``announce`` with the URL and,
    when ``open_browser`` is set, opens it in the system browser. Only
    then: announcing before the listener accepts connections sent users
    to a browser page that said "Unable to connect". If the bind never
    completes within ``timeout``, ``warn`` receives one message instead.

    Start it *after* the application is built: a cold import of the
    render stack can take longer than ``timeout``, and the clock must not
    run while that happens.
    """
    url = viewer_url(host, port)

    def _run() -> None:
        if not wait_for_port(host, port, timeout=timeout):
            if warn is not None:
                warn(
                    f"server did not bind within {timeout:.0f}s on http://{host}:{port}. "
                    "Check stderr for uvicorn errors."
                )
            return
        if announce is not None:
            announce(url)
        if open_browser:
            with contextlib.suppress(Exception):
                webbrowser.open(url)

    thread = threading.Thread(target=_run, name="vibe-view-announce", daemon=True)
    thread.start()
    return thread


def find_free_port(start: int, host: str = "127.0.0.1", *, span: int = 200) -> int:
    """The first port in ``[start, start + span)`` that can be bound on ``host``."""
    for port in range(start, start + span):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
            return port
        except OSError:
            continue
    raise OSError(f"No free port in range {start}-{start + span - 1}")
