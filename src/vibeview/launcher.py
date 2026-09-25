"""Programmatic entry point for spinning up the vibe-view web server.

`launch_qvf` lets vibe-qc (or any other producer) hand off an in-memory
QVF directly to the viewer without going through a temp file. It is the
in-process equivalent of ``vibe-view open <path>``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibeview.netutil import announce_when_bound

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, QVFSource


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
    reader is closed if this function opened it. A supplied reader remains
    open and its lifetime stays under the caller's control.
    """
    from vibeview.api import closing_reader
    from vibeview.app import create_app, serve
    from vibeview.banner import print_banner

    with closing_reader(source) as reader:
        if print_banner_to_stdout:
            print_banner(reader)

        app = create_app(reader)
        if open_browser:
            # Opens once uvicorn has bound, never before (an early open()
            # showed "Unable to connect"), and only after create_app so
            # the render stack's cold start does not eat the bind timeout.
            announce_when_bound(host, port, open_browser=True)
        serve(app, host=host, port=port)
