"""Open vibe-sketch from the viewer and take back the structures it sends.

vibe-sketch is the family's 2D structure and reaction sketcher. It works
without vibe-view; the optional ``[sketch]`` extra installs it beside the
viewer, and the **Sketch** button then becomes the way to draw a molecule
(#77). Without the extra the button explains how to install it, and the
SMILES field keeps working as before.

The hand-off contract, agreed on vibe-view #77 for vibe-sketch #1:

* vibe-view starts ``python -m vibesketch serve --host 127.0.0.1 --port PORT
  --open`` in its own environment, with :data:`HANDOFF_ENV` naming a private
  directory. The variable tells the sketcher that a viewer is waiting, so it
  offers *Send to vibe-view*.
* *Send to vibe-view* writes the structure as a QVF or XYZ file wherever the
  sketcher chooses. It then writes a record into that directory, a JSON
  object ``{"schema": "vibe-view-sketch-handoff/1", "path": "<absolute
  path>"}``. The record is written under a name that does not end in
  ``.json`` and then renamed, so the viewer never reads half a record.
* vibe-view reads ``*.json`` records in name order and deletes each one after
  reading it. It reads the structure into memory, so a later send that
  overwrites the same file leaves what is already open unchanged.
* The viewer stops the sketcher it started, and removes the directory, when
  it exits. A missing directory tells the sketcher that the viewer has gone.

Nothing here imports ``vibesketch``: the sketcher runs as a child process,
so the only coupling is the command line and the record above.
"""

from __future__ import annotations

import atexit
import contextlib
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibeview.worker_client import terminate_and_wait

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader

__all__ = [
    "DEFAULT_PORT",
    "HANDOFF_ENV",
    "HANDOFF_SCHEMA",
    "SketchHandoffError",
    "SketchSession",
    "load_sketched_structure",
    "read_handoff_record",
    "sketch_available",
    "sketch_missing_message",
]

#: Environment variable that names the hand-off directory for the sketcher.
HANDOFF_ENV = "VIBEVIEW_SKETCH_HANDOFF"
#: The ``schema`` value every hand-off record carries.
HANDOFF_SCHEMA = "vibe-view-sketch-handoff/1"
#: The structure formats the contract allows the sketcher to send.
HANDOFF_SUFFIXES = (".qvf", ".xyz")
#: vibe-sketch's own default port, one above vibe-view's 8765.
DEFAULT_PORT = 8766
#: How many ports from :data:`DEFAULT_PORT` to try before asking the OS.
PORT_TRIES = 10
#: The interface the sketcher binds. Only this machine reaches it.
HOST = "127.0.0.1"


class SketchHandoffError(ValueError):
    """A hand-off record that does not name a structure the viewer can open."""


def sketch_available() -> bool:
    """Whether the optional ``[sketch]`` extra (``vibesketch``) is installed."""
    try:
        return importlib.util.find_spec("vibesketch") is not None
    except (ImportError, ValueError):
        return False


def sketch_missing_message() -> str:
    """The remediation shown when ``vibesketch`` is not importable.

    vibe-sketch is published on no package index yet, so the extra alone
    resolves only against a vibe-sketch checkout that is already installed.
    Say both, in that order, as :func:`install_hints.queue_missing_message`
    does for vq.
    """
    from vibeview.install_hints import install_hint

    return (
        "Sketching needs vibe-sketch, which is not installed. Install it from "
        "its checkout (pip install <path-to-vibe-sketch>; Python 3.12 or newer), "
        f"then: {install_hint('sketch')}"
    )


def read_handoff_record(record: Path) -> Path:
    """Return the structure file that one hand-off record names.

    Raises :class:`SketchHandoffError` with the record's name and the reason
    when the record is unreadable or breaks the contract.
    """
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SketchHandoffError(f"{record.name}: unreadable hand-off record ({exc})") from exc
    if not isinstance(data, dict):
        raise SketchHandoffError(f"{record.name}: a hand-off record must be a JSON object")
    schema = data.get("schema")
    if schema != HANDOFF_SCHEMA:
        raise SketchHandoffError(
            f"{record.name}: unsupported hand-off schema {schema!r}, expected {HANDOFF_SCHEMA!r}"
        )
    raw = data.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise SketchHandoffError(f"{record.name}: the record names no structure path")
    path = Path(raw)
    if not path.is_absolute():
        raise SketchHandoffError(f"{record.name}: the structure path {raw!r} is not absolute")
    if path.suffix.lower() not in HANDOFF_SUFFIXES:
        raise SketchHandoffError(f"{record.name}: {path.name} is neither a QVF nor an XYZ file")
    if not path.is_file():
        raise SketchHandoffError(f"{record.name}: {path} does not exist")
    return path


def read_handoff_view(record: Path) -> dict[str, Any]:
    """Optional result selectors are additive to handoff/1, with CLI numbering."""
    data = json.loads(record.read_text(encoding="utf-8"))
    options = {key: data[key] for key in ("view", "mode", "orbital") if key in data}
    if "view" in options and options["view"] not in ("orbitals", "density", "mode", "spectrum"):
        raise SketchHandoffError("Unknown result view in sketch hand-off")
    if "mode" in options and (type(options["mode"]) is not int or options["mode"] < 1):
        raise SketchHandoffError("Sketch hand-off mode must be an integer numbered from 1")
    if "orbital" in options and (not isinstance(options["orbital"], str)
                                 or not options["orbital"].strip()):
        raise SketchHandoffError("Sketch hand-off orbital must be a nonempty label")
    return options


def load_sketched_structure(path: Path) -> QVFReader:
    """Read a structure the sketcher sent into an in-memory reader.

    The bytes are read once, here, so the reader does not change if the
    sketcher later overwrites the same file. ``source_path`` keeps the file's
    name for the Files list.
    """
    from vibeview.converters import convert_to_qvf
    from vibeview.qvf import QVFReader

    if path.suffix.lower() == ".qvf":
        reader = QVFReader(path.read_bytes())
    else:
        reader = QVFReader(convert_to_qvf(path, format_name="xyz"))
    reader.source_path = path.resolve()
    return reader


def _port_free(port: int) -> bool:
    """Whether :data:`HOST` can bind *port* now, with the options uvicorn uses."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((HOST, port))
        except OSError:
            return False
    return True


def free_port() -> int:
    """A port for the sketcher: its default if free, else the next free one.

    vibe-view chooses the port rather than letting ``vibe-sketch serve`` do it,
    so it knows the address and can reopen the browser there.
    """
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_TRIES):
        if _port_free(port):
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


#: Sessions still running when the interpreter exits.
_LIVE_SESSIONS: set[SketchSession] = set()
_LIVE_LOCK = threading.Lock()
_EXIT_HOOK_INSTALLED = False


def _close_live_sessions() -> None:
    with _LIVE_LOCK:
        sessions = list(_LIVE_SESSIONS)
    for session in sessions:
        session.close()


def _install_exit_hook() -> None:
    """Stop live sketchers when the viewer exits, including on SIGTERM.

    ``atexit`` covers Ctrl+C and a normal exit. The desktop shell and the
    dashboard stop a viewer with SIGTERM, which by default ends Python without
    running ``atexit`` and would orphan the sketcher. When SIGTERM still has its
    default disposition, route it into the KeyboardInterrupt path, as
    ``vibe-view serve`` does for its own children. A handler someone else
    installed is left alone.
    """
    global _EXIT_HOOK_INSTALLED
    if _EXIT_HOOK_INSTALLED:
        return
    _EXIT_HOOK_INSTALLED = True
    atexit.register(_close_live_sessions)

    def _terminate(signum: int, frame: Any) -> None:  # noqa: ARG001
        raise KeyboardInterrupt

    # ValueError: not the main thread. signal.SIGTERM exists on every platform.
    with contextlib.suppress(ValueError):
        if signal.getsignal(signal.SIGTERM) is signal.SIG_DFL:
            signal.signal(signal.SIGTERM, _terminate)


@dataclass(eq=False)
class SketchSession:
    """One sketcher this viewer started, and the directory it sends into."""

    process: subprocess.Popen[bytes]
    url: str
    handoff_dir: Path
    log_path: Path
    _root: Path = field(repr=False)
    _closed: bool = field(default=False, repr=False)
    view_options: dict[Path, dict[str, Any]] = field(default_factory=dict, repr=False)

    @classmethod
    def launch(cls, *, python: str | None = None, open_browser: bool = True) -> SketchSession:
        """Start ``vibe-sketch serve`` and return its session.

        *python* defaults to this interpreter, so the sketcher runs from the
        environment the ``[sketch]`` extra installed it into. Raises
        ``OSError`` when the process cannot be started.
        """
        root = Path(tempfile.mkdtemp(prefix="vibeview-sketch-"))
        handoff_dir = root / "handoff"
        log_path = root / "sketcher.log"
        try:
            handoff_dir.mkdir(mode=0o700)
            (root / "return").mkdir(mode=0o700)
            port = free_port()
            argv = [
                python or sys.executable,
                "-m",
                "vibesketch",
                "serve",
                "--host",
                HOST,
                "--port",
                str(port),
            ]
            if open_browser:
                argv.append("--open")
            env = {**os.environ, HANDOFF_ENV: str(handoff_dir),
                   "VIBEVIEW_SKETCH_RETURN": str(root / "return")}
            with log_path.open("wb") as log:
                process = subprocess.Popen(
                    argv,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise
        session = cls(
            process=process,
            url=f"http://{HOST}:{port}/",
            handoff_dir=handoff_dir,
            log_path=log_path,
            _root=root,
        )
        _install_exit_hook()
        with _LIVE_LOCK:
            _LIVE_SESSIONS.add(session)
        return session

    @property
    def running(self) -> bool:
        """Whether the sketcher process is still alive."""
        return not self._closed and self.process.poll() is None

    def pending_records(self) -> list[Path]:
        """Complete hand-off records waiting to be read, oldest name first."""
        try:
            return sorted(p for p in self.handoff_dir.glob("*.json") if p.is_file())
        except OSError:
            return []

    def receive(self) -> list[Path | SketchHandoffError]:
        """Read and delete every pending record.

        Each entry is the structure path a record names, or the error that
        says why the record was refused. A refused record is deleted too, so
        it is reported once rather than on every poll.
        """
        received: list[Path | SketchHandoffError] = []
        self.view_options.clear()
        for record in self.pending_records():
            try:
                path = read_handoff_record(record)
                options = read_handoff_view(record)
                self.view_options[path] = options
                received.append(path)
            except SketchHandoffError as exc:
                received.append(exc)
            finally:
                with contextlib.suppress(OSError):
                    record.unlink()
        return received

    def open_browser(self) -> None:
        """Bring the sketcher up in the browser again."""
        webbrowser.open(self.url)

    def exit_message(self) -> str:
        """What to tell the user once the sketcher has exited."""
        code = self.process.poll()
        if code in (0, -signal.SIGTERM, -signal.SIGINT):
            return "vibe-sketch closed"
        detail = ""
        with contextlib.suppress(OSError):
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            detail = next((line.strip() for line in reversed(lines) if line.strip()), "")
        message = f"vibe-sketch exited with status {code}"
        return f"{message}: {detail}" if detail else message

    def close(self) -> None:
        """Stop the sketcher if it still runs and remove the hand-off directory."""
        if self._closed:
            return
        self._closed = True
        with _LIVE_LOCK:
            _LIVE_SESSIONS.discard(self)
        terminate_and_wait(self.process)
        shutil.rmtree(self._root, ignore_errors=True)
