"""CLI entry point for vibe-view.

Usage:
    vibe-view open <chemistry-file> Open a QVF or supported loose format
    vibe-view import <file.xyz>     Persist a loose file as QVF
    vibe-view --version
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import click

_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _disable_http_caching() -> None:
    """Force ``Cache-Control: no-store`` on every HTTP response.

    The viewer is a local dev tool; users routinely reload after the
    server-side template or JS bundle changes. Firefox + Chrome aggressively
    cache the bundle with ETag/304 even on Cmd+Shift+R, so without this the
    UI silently runs against a stale template after every server-side edit.

    wslink technically supports a ``WSLINK_HTTP_HEADERS`` env var pointing to
    a JSON file, but it's parsed once at module-import time and the parsed
    dict isn't reachable from any public API — so we just patch the module's
    ``HTTP_HEADERS`` attribute directly after ``wslink.backends.aiohttp`` is
    on ``sys.modules``. Idempotent.
    """
    import wslink.backends.aiohttp as backend  # noqa: PLC0415 — needs trame imports first

    if not isinstance(backend.HTTP_HEADERS, dict):
        backend.HTTP_HEADERS = dict(_NO_CACHE_HEADERS)
    else:
        backend.HTTP_HEADERS.update(_NO_CACHE_HEADERS)
    logging.getLogger("vibeview").info("no-cache headers installed: %r", backend.HTTP_HEADERS)

    # WebAppServer is constructed inside ``Server.start``; it captures
    # ``HTTP_HEADERS`` at __init__ time to decide whether to register the
    # ``http_headers`` middleware. Wrap __init__ so we re-assert the dict
    # right before it samples the global (defensive: harmless if the
    # patch above already stuck).
    orig_init = backend.WebAppServer.__init__

    def _patched_init(self, server_config):
        backend.HTTP_HEADERS = dict(_NO_CACHE_HEADERS)
        return orig_init(self, server_config)

    backend.WebAppServer.__init__ = _patched_init


# Representation names offered by `vibe-view show`. Duplicated from
# vibeview.tui.scene.REPRESENTATIONS because a click.Choice is evaluated at
# import time and that module pulls in numpy + PyVista, which would make
# every `vibe-view --help` pay for the render stack. Pinned against the real
# table by tests/test_tui_cli.py so the two cannot drift.
_TUI_REPRESENTATIONS = (
    "ball_and_stick", "licorice", "spacefill", "wireframe", "points", "backbone",
)

_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB per file
_LOG_BACKUPS = 3  # keep N rolled files alongside the active one


def _default_log_path() -> Path:
    """User-level cache location, kept out of any git checkout.

    Honors ``XDG_CACHE_HOME`` so power users / CI can redirect, falls back
    to ``~/.cache`` on Linux + macOS.
    """
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "vibe-view" / "vibe-view.log"


def _interpreter_record_path() -> Path:
    """Persistent record of a vibe-view-capable interpreter, in the shared
    cache dir. The Electron desktop app's ``findPython()`` reads this so a
    plain double-click on a ``.qvf`` (which sets no VIBEVIEW_DESKTOP_CONFIG)
    can reuse the venv vibe-view was installed in, instead of falling back to
    a bare ``python3`` on PATH that lacks vibeview."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "vibe-view" / "interpreter.json"


def _record_interpreter() -> None:
    """Record this interpreter (best-effort) for the desktop app to reuse."""
    import json as _json

    from vibeview import __version__

    try:
        path = _interpreter_record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _json.dumps({"python": sys.executable, "version": __version__}, indent=2)
        )
    except OSError:
        pass


def _configure_logging(log_path: Path) -> None:
    """Route Trame / wslink / aiohttp logs into ``log_path`` with rotation.

    wslink uses Python ``logging`` (e.g. the "is not expecting text message"
    critical, and aiohttp's ``_handle_request`` tracebacks on trigger errors
    that previously only surfaced via shell stderr redirection). Attaching a
    rotating FileHandler at root makes them land in a file users can tail
    without unbounded growth.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUPS,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    # Trame / aiohttp emit a torrent of INFO-level internals (a `js_key` +
    # namespace-token line per widget on every template build, an access line
    # per HTTP request). At INFO they bury vibe-view's own messages and bloat
    # the file, so a real debugging log is unreadable. Quiet them to WARNING;
    # wslink CRITICALs and aiohttp request tracebacks (the reason this handler
    # exists, see docstring) are above WARNING and still land. vibe-view stays
    # at INFO so its render/timing breadcrumbs remain.
    for _noisy in ("trame", "trame_server", "trame_client", "wslink", "aiohttp.access"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)
    logging.getLogger("vibeview").setLevel(logging.INFO)

    def _log_uncaught(exc_type, exc, tb):
        logging.getLogger("vibeview").error("Uncaught exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _log_uncaught


def _wait_until_bound_and_announce(
    host: str,
    port: int,
    *,
    open_browser: bool,
    timeout: float = 10.0,
) -> None:
    """Background-thread helper. Polls the port until uvicorn binds, then
    prints the "ready" message and (optionally) opens the browser. Runs
    only the announcement once the listener is actually accepting TCP
    connections, so users do not refresh the browser too early and see
    a misleading "Unable to connect" page (the historical CLI printed
    "server starting" *before* the bind happened).

    On binds that never complete within ``timeout`` the announcement is
    skipped and a stderr warning is emitted instead. The main thread is
    still blocked in :func:`vibeview.app.serve` regardless, so this
    helper never interferes with the SCF-server lifecycle.
    """
    # When uvicorn binds 0.0.0.0 the TCP listener is reachable via
    # localhost; probe that instead of literally connecting to the
    # wildcard address (which Python refuses to dial).
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{host}:{port}"
    probe_url = f"http://{probe_host}:{port}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((probe_host, port), timeout=0.5):
                pass
        except OSError:
            time.sleep(0.2)
            continue
        click.echo(f"vibe-view ready at {probe_url}")
        click.echo("Press Ctrl+C to stop.")
        if open_browser:
            try:
                webbrowser.open(probe_url)
            except Exception:
                pass
        return
    click.echo(
        f"vibe-view: warning, server did not bind within {timeout:.0f}s "
        f"on {url}. Check stderr for uvicorn errors.",
        err=True,
    )


def _port_in_use(host: str, port: int) -> bool:
    """True if something is already accepting TCP connections on (host, port).

    ``vibe-view open`` otherwise *races* on a busy port: the announce thread
    (:func:`_wait_until_bound_and_announce`) connects to the **existing**
    listener, prints "ready", and opens the browser against that stale server —
    while the freshly launched server dies with ``EADDRINUSE`` buried in the log
    file. The user is left looking at the old session's last rendered frame,
    which displays fine but no longer responds to the mouse ("I can see the
    molecule but can't rotate it"). Pre-checking turns that silent footgun into
    an actionable error.
    """
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    try:
        with socket.create_connection((probe_host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _abort_if_port_in_use(host: str, port: int) -> None:
    """Fail loudly (don't announce "ready" / open the browser) on a busy port."""
    if _port_in_use(host, port):
        click.echo(
            f"vibe-view: port {port} is already in use on {host}.\n"
            "Another vibe-view server is probably still running there (servers "
            "stay up until you press Ctrl+C in their terminal). Either stop it, "
            "or open this file on a different port, e.g.  --port 8090.",
            err=True,
        )
        raise SystemExit(1)


@click.group(invoke_without_command=True)
@click.option(
    "--version", "show_version", is_flag=True, default=False, help="Show version and exit."
)
@click.pass_context
def main(ctx: click.Context, show_version: bool) -> None:
    """Interactive browser, desktop, and terminal viewer for chemistry data."""
    if show_version:
        import platform

        import pyvista

        from vibeview.codenames import version_label

        click.echo(f"vibe-view {version_label()}")
        click.echo(f"Python    {platform.python_version()}")
        click.echo(f"PyVista   {pyvista.__version__}")
        click.echo(f"VTK       {pyvista.vtk_version_info}")
        ctx.exit()
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        click.echo("\nRun 'vibe-view demo' for a standalone first example.\n")


def _load_app():
    """Import the interactive-viewer app, or exit with a clear hint.

    The interactive viewer (`vibe-view open` / `serve` / `dashboard`) needs
    the ``[viewer]`` extra (trame/uvicorn). A lean capture-only install omits
    it, so a raw ``No module named 'trame'`` becomes an actionable message.
    """
    try:
        from vibeview.app import create_app, serve

        return create_app, serve
    except ImportError as exc:
        from vibeview.install_hints import install_hint

        raise SystemExit(
            "The interactive viewer needs the 'viewer' extra. Install it with:\n"
            f"    {install_hint('viewer')}\n"
            "(headless screenshot capture and QVF reading do not require it.)\n"
            f"Original import error: {exc}"
        ) from None


@main.command("open")
@click.argument(
    "input_files",
    nargs=-1,
    type=click.Path(exists=True, path_type=Path),
    required=True,
)
@click.option(
    "--port",
    default=8080,
    show_default=True,
    type=click.IntRange(min=1, max=65535),
    help="TCP port for the web server.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Interface to bind. Default = localhost-only.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    default=False,
    help="Don't open the browser automatically.",
)
@click.option(
    "--log-file",
    "log_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Path to write the server log. Defaults to "
        "$XDG_CACHE_HOME/vibe-view/vibe-view.log (~/.cache/vibe-view/vibe-view.log) "
        "with size-based rotation (5 MiB x 3 backups)."
    ),
)
@click.option(
    "--section",
    "-s",
    default=None,
    help="Auto-activate this section on startup.",
)
@click.option(
    "--auto-compare",
    is_flag=True,
    default=False,
    help="Enable compare mode when opening multiple files.",
)
def open_cmd(
    input_files: tuple[Path, ...],
    port: int,
    host: str,
    no_browser: bool,
    log_file: Path | None,
    section: str | None,
    auto_compare: bool,
) -> None:
    """Open QVF or supported loose files in the interactive viewer.

    When multiple files are given, a Files dropdown in the app bar lets
    you switch between them in the same session.

    Examples:

        vibe-view open h2co.qvf

        vibe-view open structure.xyz density.cube
    """
    from vibeview.banner import print_banner
    from vibeview.qvf import QVFReader

    if log_file is None:
        log_file = _default_log_path()

    from click.core import ParameterSource

    from vibeview.config import get as _cfg

    # The config file supplies DEFAULTS only — an explicitly passed
    # --port/--host must win (the old unconditional read made
    # `--port 9999` silently run on the config's port).
    ctx = click.get_current_context()
    if ctx.get_parameter_source("port") == ParameterSource.DEFAULT:
        port = int(_cfg("server.port", port))
    if ctx.get_parameter_source("host") == ParameterSource.DEFAULT:
        host = str(_cfg("server.host", host))
    if not no_browser:
        no_browser = not _cfg("server.open_browser", True)

    _configure_logging(log_file)
    click.echo(f"Logging server activity to {log_file}")

    # Fail fast on a busy port, before opening files / printing banners —
    # otherwise the announce thread opens the browser against whatever stale
    # server already owns the port (see _port_in_use), masking this server's
    # EADDRINUSE death.
    _abort_if_port_in_use(host, port)

    # ── Open all files ────────────────────────────────────────────────
    readers: list[QVFReader] = []
    for input_file in input_files:
        try:
            # Auto-detect non-QVF formats and convert on the fly.
            from vibeview.converters import detect_format

            fmt = detect_format(input_file)
            if fmt is not None and fmt != "qvf":
                from vibeview.converters import convert_to_qvf

                click.echo(f"Converting {input_file} ({fmt} → qvf)...")
                qvf_buf = convert_to_qvf(input_file)
                reader = QVFReader(qvf_buf)
            else:
                reader = QVFReader(input_file)
            readers.append(reader)
        except Exception as e:
            for opened_reader in readers:
                opened_reader.close()
            detail = str(e) or type(e).__name__
            click.echo(f"Error opening {input_file}: {detail}", err=True)
            raise SystemExit(1) from None

    # Print banner for each file.
    for i, r in enumerate(readers):
        if len(readers) > 1:
            click.echo(f"\n── File {i + 1}/{len(readers)}: {r.path or '<in-memory>'}")
        print_banner(r)
        # Track in recent files
        if r.path:
            from vibeview.recent import add_recent

            add_recent(r.path)

    # ── Step 7: start Trame server ────────────────────────────────────
    url = f"http://{host}:{port}"

    click.echo(f"vibe-view starting up on {url}...")

    # Import inside the command so --help is fast without loading VTK.
    create_app, serve = _load_app()

    try:
        app = create_app(readers)
    except ImportError as exc:
        # A missing [viewer] extra is the single most likely first-run
        # failure, and a raw traceback is the wrong way to say "run one
        # more pip command". Match `vibe-view tui`, which already does this.
        raise click.ClickException(str(exc)) from exc

    if auto_compare and len(readers) >= 2:
        app.state.compare_mode = True
        click.echo("Auto-compare mode enabled.")

    if section and readers:
        r0 = readers[0]
        if r0.has_section(section):
            app.state.selected_section = section
            click.echo(f"Auto-activating section: {section}")
        else:
            available = ", ".join(s.id for s in r0.sections)
            click.echo(
                f"Warning: no section {section!r} in this file (have: {available}).",
                err=True,
            )

    # Launch a watcher thread that announces "ready" and opens the
    # browser once uvicorn has actually bound the port. Avoids the
    # historical race where the CLI printed "starting" + opened the
    # browser before uvicorn finished binding, so users saw "Unable to
    # connect" in Firefox and assumed the server was broken.
    #
    # IMPORTANT: the watcher starts *after* the heavy imports and
    # create_app so VTK cold-start overhead doesn't eat into the
    # 10 s timeout before the port binds.
    threading.Thread(
        target=_wait_until_bound_and_announce,
        kwargs={
            "host": host,
            "port": port,
            "open_browser": not no_browser,
        },
        daemon=True,
    ).start()

    serve(app, host=host, port=port)


@main.command("compare")
@click.argument("qvf_a", type=click.Path(exists=True, path_type=Path))
@click.argument("qvf_b", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--port",
    default=8080,
    show_default=True,
    type=click.IntRange(min=1, max=65535),
    help="TCP port.",
)
@click.option("--host", default="127.0.0.1", show_default=True, help="Interface to bind.")
@click.option("--no-browser", is_flag=True, default=False, help="Don't open the browser.")
@click.option(
    "--log-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Server log path.",
)
def compare_cmd(
    qvf_a: Path, qvf_b: Path, port: int, host: str, no_browser: bool, log_file: Path | None
) -> None:
    """Open two .qvf files side by side with compare mode enabled.

    Equivalent to ``vibe-view open a.qvf b.qvf`` with automatic compare-mode
    activation so the two structures appear overlaid immediately.
    """
    from vibeview.banner import print_banner
    from vibeview.qvf import QVFError, QVFReader

    if log_file is None:
        log_file = _default_log_path()
    _configure_logging(log_file)
    click.echo(f"Logging server activity to {log_file}")
    _abort_if_port_in_use(host, port)

    readers = []
    for qvf_file in [qvf_a, qvf_b]:
        try:
            readers.append(QVFReader(qvf_file))
        except (QVFError, ValueError) as e:
            click.echo(f"Error opening {qvf_file}: {e}", err=True)
            raise SystemExit(1) from None

    click.echo(f"\nComparing: {qvf_a.name}  vs  {qvf_b.name}")
    for r in readers:
        print_banner(r)

    import threading

    create_app, serve = _load_app()

    app = create_app(readers)
    app.state.compare_mode = True
    app.state.status_message = f"Compare mode: {qvf_a.name} vs {qvf_b.name}"

    threading.Thread(
        target=_wait_until_bound_and_announce,
        kwargs={"host": host, "port": port, "open_browser": not no_browser},
        daemon=True,
    ).start()
    serve(app, host=host, port=port)


@main.command("from-vq")
@click.argument("job_ids", nargs=-1, required=True)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=("Directory to fetch job workspaces into. Defaults to $PWD/vq-fetched."),
)
@click.option(
    "--port",
    default=8080,
    show_default=True,
    type=click.IntRange(min=1, max=65535),
    help="TCP port for the web server.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Interface to bind.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    default=False,
    help="Don't open the browser automatically.",
)
@click.option(
    "--log-file",
    "log_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to write the server log.",
)
def from_vq_cmd(
    job_ids: tuple[str, ...],
    output_dir: Path | None,
    port: int,
    host: str,
    no_browser: bool,
    log_file: Path | None,
) -> None:
    """Fetch job outputs from vq and open their .qvf files in vibe-view.

    Runs ``vq fetch`` for each job ID, finds .qvf archives in the
    fetched workspaces, and launches the viewer with all of them.

    Requires ``vq`` (vibe-queue) to be installed and on PATH.

    Examples:

        vibe-view from-vq abc123

        vibe-view from-vq abc123 def456 -o ./my-results
    """
    from pathlib import Path as _Path

    # ── Fetch jobs via vq ─────────────────────────────────────────────
    try:
        from vq.fetch import fetch_local
    except ImportError:
        from vibeview.install_hints import queue_missing_message

        click.echo(f"Error: {queue_missing_message('fetch jobs')}", err=True)
        raise SystemExit(1) from None

    if output_dir is None:
        output_dir = _Path.cwd() / "vq-fetched"
    output_dir.mkdir(parents=True, exist_ok=True)

    fetched_paths: list[_Path] = []
    for jid in job_ids:
        jid = jid.strip()
        if not jid:
            continue
        try:
            dst = fetch_local(jid, output_dir)
            fetched_paths.append(dst)
            click.echo(f"  Fetched {jid} → {dst}")
        except FileNotFoundError:
            click.echo(
                f"  Error: job {jid} not found in the local queue. "
                f"Check the job ID with 'vq status'.",
                err=True,
            )
        except FileExistsError:
            click.echo(
                f"  Error: destination for {jid} already exists. "
                f"Remove it or use -o to pick a different output directory.",
                err=True,
            )
        except Exception as e:
            click.echo(f"  Error fetching {jid}: {e}", err=True)

    if not fetched_paths:
        click.echo("No jobs were fetched successfully.", err=True)
        raise SystemExit(1)

    # ── Find .qvf files in fetched workspaces ─────────────────────────
    qvf_files: list[_Path] = []
    for dst in fetched_paths:
        for qvf in sorted(dst.rglob("*.qvf")):
            qvf_files.append(qvf)
            click.echo(f"  Found {qvf.name} in {dst.name}")

    if not qvf_files:
        click.echo(
            "No .qvf files found in fetched workspaces. Did the jobs produce output_qvf=True ?",
            err=True,
        )
        raise SystemExit(1)

    # ── Open in vibe-view ─────────────────────────────────────────────
    from vibeview.banner import print_banner
    from vibeview.qvf import QVFError, QVFReader

    if log_file is None:
        log_file = _default_log_path()
    _configure_logging(log_file)

    readers: list[QVFReader] = []
    for qvf_file in qvf_files:
        try:
            readers.append(QVFReader(qvf_file))
        except QVFError as e:
            click.echo(f"Error opening {qvf_file}: {e}", err=True)
            continue

    if not readers:
        raise SystemExit(1)

    for i, r in enumerate(readers):
        if len(readers) > 1:
            click.echo(f"\n── File {i + 1}/{len(readers)}: {r.path or '<in-memory>'}")
        print_banner(r)

    _abort_if_port_in_use(host, port)
    url = f"http://{host}:{port}"
    click.echo(f"\nvibe-view starting up on {url}...")

    threading.Thread(
        target=_wait_until_bound_and_announce,
        kwargs={"host": host, "port": port, "open_browser": not no_browser},
        daemon=True,
    ).start()

    create_app, serve = _load_app()

    app = create_app(readers)
    serve(app, host=host, port=port)


@main.command("batch")
@click.argument("patterns", nargs=-1, required=True)
@click.option(
    "--output",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory for the rendered PNGs (default ./vibe-view-gallery).",
)
@click.option("--size", default="900x600", show_default=True, help="Image size as WxH.")
@click.option(
    "--volumes",
    is_flag=True,
    default=False,
    help="Also render each volume section (density, orbitals, etc.)",
)
def batch_cmd(patterns: tuple[str, ...], output: Path | None, size: str, volumes: bool) -> None:
    """Render a gallery of PNGs from many .qvf files (offscreen).

    PATTERNS are file paths or globs; each matched .qvf is rendered with the same
    view defaults to one PNG per section. No interactive server is started.

    Examples:

        vibe-view batch runs/*.qvf

        vibe-view batch a.qvf -o gallery/ --size 1200x800

        vibe-view batch a.qvf --volumes    # render structure + each volume
    """
    import glob as _glob

    from vibeview.batch import render_all_png, render_structure_png
    from vibeview.qvf import QVFError, QVFReader

    try:
        width, height = (int(x) for x in size.lower().split("x"))
    except ValueError:
        click.echo(f"batch: invalid --size {size!r}, expected WxH (e.g. 900x600)", err=True)
        raise SystemExit(1) from None

    files: list[Path] = []
    for pattern in patterns:
        p = Path(pattern)
        if p.is_file():
            files.append(p)
        else:
            files.extend(Path(m) for m in sorted(_glob.glob(pattern)))
    files = [f for f in files if f.suffix == ".qvf"]
    if not files:
        click.echo("batch: no .qvf files matched", err=True)
        raise SystemExit(1)

    out = output or (Path.cwd() / "vibe-view-gallery")
    out.mkdir(parents=True, exist_ok=True)
    click.echo(f"Rendering {len(files)} file(s) to {out} ...")

    n_ok = 0
    for f in files:
        try:
            reader = QVFReader(f)
            if volumes:
                sub = out / f.stem
                sub.mkdir(exist_ok=True)
                paths = render_all_png(reader, sub, size=(width, height), include_volumes=True)
                if paths:
                    click.echo(f"  {f.name}: {len(paths)} PNG(s)")
                    n_ok += 1
                else:
                    click.echo(f"  {f.name}: nothing to render", err=True)
            else:
                png = out / f"{f.stem}.png"
                if render_structure_png(reader, png, size=(width, height)):
                    click.echo(f"  {f.name} -> {png.name}")
                    n_ok += 1
                else:
                    click.echo(f"  {f.name}: no structure section, skipped", err=True)
        except QVFError as e:
            click.echo(f"  {f.name}: error ({e})", err=True)
        except Exception as e:  # noqa: BLE001
            click.echo(f"  {f.name}: render failed ({e})", err=True)

    click.echo(f"batch: rendered {n_ok}/{len(files)} file(s) to {out}")
    if n_ok == 0:
        raise SystemExit(1)


def _print_text_table(cols: list[str], rows: list[list]) -> None:
    str_rows = [["" if c is None else str(c) for c in r] for r in rows]
    widths = [max([len(cols[i])] + [len(r[i]) for r in str_rows]) for i in range(len(cols))]

    def _fmt(cells: list[str]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    click.echo(_fmt(cols))
    click.echo("  ".join("-" * w for w in widths))
    for r in str_rows:
        click.echo(_fmt(r))


@main.command("table")
@click.argument("qvf_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--kind",
    default=None,
    help="Section kind to tabulate (vibrations, atom_properties, wavefunction.gto). "
    "Omit to list what the file offers.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["table", "csv", "json"]),
    default="table",
    show_default=True,
    help="Output format.",
)
def table_cmd(qvf_file: Path, kind: str | None, fmt: str) -> None:
    """Dump tabular data (frequencies, charges, MO energies) from a .qvf.

    With no --kind, lists the tabulatable sections present; otherwise prints the
    chosen table as text, CSV, or JSON to stdout — handy for piping into a
    spreadsheet or a script.

    Examples:

        vibe-view table water.qvf --kind wavefunction.gto --format csv

        vibe-view table h2co.qvf --kind atom_properties
    """
    from vibeview.qvf import QVFError, QVFReader
    from vibeview.tables import TABULATABLE, extract_table

    try:
        reader = QVFReader(qvf_file)
    except QVFError as e:
        click.echo(f"table: error opening {qvf_file}: {e}", err=True)
        raise SystemExit(1) from None

    present = [k for k in TABULATABLE if any(s.kind == k for s in reader.sections)]
    if not kind:
        if present:
            click.echo("Tabulatable sections in this file:")
            for k in present:
                click.echo(f"  {k}")
            click.echo("\nRe-run with --kind <kind> [--format csv|json].")
        else:
            click.echo("No tabulatable sections (vibrations / atom_properties / wavefunction.gto).")
        return

    try:
        cols, rows = extract_table(reader, kind)
    except ValueError as e:
        click.echo(f"table: {e}", err=True)
        raise SystemExit(1) from None

    if fmt == "json":
        import json as _json

        click.echo(_json.dumps([dict(zip(cols, r)) for r in rows], indent=2))
    elif fmt == "csv":
        import csv as _csv
        import io as _io

        buf = _io.StringIO()
        writer = _csv.writer(buf)
        writer.writerow(cols)
        writer.writerows(rows)
        click.echo(buf.getvalue().rstrip("\n"))
    else:
        _print_text_table(cols, rows)


@main.command("info")
@click.argument("qvf_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--json", "fmt_json", is_flag=True, default=False, help="Output as JSON for machine parsing."
)
@click.option("--short", "short_fmt", is_flag=True, default=False, help="One-line summary.")
def info_cmd(qvf_file: Path, fmt_json: bool, short_fmt: bool) -> None:
    """Print detailed metadata and section summary for a .qvf file."""
    from vibeview.qvf import QVFError, QVFReader

    try:
        reader = QVFReader(qvf_file)
    except QVFError as e:
        if fmt_json:
            click.echo(json.dumps({"error": str(e)}))
        else:
            click.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from None

    import json as _json
    import zipfile

    try:
        src = reader.manifest.source
        prov = getattr(reader.manifest, "provenance", None) or {}
        if hasattr(prov, "model_dump"):
            prov = prov.model_dump()
        if not isinstance(prov, dict):
            prov = {}

        if short_fmt:
            energy = prov.get("scf_energy", {})
            if isinstance(energy, dict):
                energy = energy.get("value")
            e_str = f"{float(energy):.8f}" if energy is not None else "?"
            conv = (
                "✓"
                if prov.get("scf_converged")
                else ("✗" if prov.get("scf_converged") is False else "?")
            )
            click.echo(
                f"{qvf_file.name}: {src.calculation}  E={e_str} Eh  conv={conv}  {len(reader.sections)} sections"
            )
            return

        sections_out = []
        total_size = 0
        for sec in reader.sections:
            sec_size = 0
            for m in sec.members.values():
                try:
                    zi = reader._zf.getinfo(m.path)
                    sec_size += zi.file_size
                except Exception:
                    pass
            total_size += sec_size
            sections_out.append(
                {
                    "id": sec.id,
                    "kind": sec.kind,
                    "n_members": len(sec.members),
                    "size_bytes": sec_size,
                    "size": _human_size(sec_size),
                }
            )

        if fmt_json:
            energy_val = None
            e = prov.get("scf_energy", {})
            if isinstance(e, dict):
                energy_val = e.get("value")
            click.echo(
                _json.dumps(
                    {
                        "file": str(qvf_file.name),
                        "program": src.program,
                        "version": src.version,
                        "calculation": src.calculation,
                        "qvf_version": reader.manifest.qvf_version,
                        "method": prov.get("method"),
                        "functional": prov.get("functional"),
                        "basis": prov.get("basis"),
                        "charge": prov.get("charge"),
                        "multiplicity": prov.get("multiplicity"),
                        "n_electrons": prov.get("n_electrons"),
                        "scf_converged": prov.get("scf_converged"),
                        "n_scf_iterations": prov.get("n_scf_iterations"),
                        "scf_energy_eh": energy_val,
                        "wall_seconds": prov.get("wall_seconds"),
                        "hostname": prov.get("hostname"),
                        "dimensionality": prov.get("dimensionality"),
                        "n_sections": len(reader.sections),
                        "sections": sections_out,
                        "total_size_bytes": total_size,
                        "total_size": _human_size(total_size),
                        "archive_size_bytes": qvf_file.stat().st_size,
                        "archive_size": _human_size(qvf_file.stat().st_size),
                    },
                    indent=2,
                )
            )
            return

        # Text output
        click.echo(f"{'File':20s} {qvf_file.name}")
        click.echo(f"{'Program':20s} {src.program} {src.version}")
        click.echo(f"{'Calculation':20s} {src.calculation}")
        click.echo(f"{'QVF version':20s} {reader.manifest.qvf_version}")

        if prov.get("method"):
            click.echo(f"{'Method':20s} {prov['method']}")
        if prov.get("functional"):
            click.echo(f"{'Functional':20s} {prov['functional']}")
        if prov.get("basis"):
            click.echo(f"{'Basis':20s} {prov['basis']}")
        if prov.get("charge") is not None:
            click.echo(f"{'Charge':20s} {prov['charge']}")
        if prov.get("multiplicity") is not None:
            click.echo(f"{'Multiplicity':20s} {prov['multiplicity']}")
        if prov.get("n_electrons") is not None:
            click.echo(f"{'Electrons':20s} {prov['n_electrons']}")
        if "scf_converged" in prov:
            status = "yes" if prov["scf_converged"] else "NO"
            click.echo(f"{'SCF converged':20s} {status}")
        if prov.get("n_scf_iterations") is not None:
            click.echo(f"{'SCF iterations':20s} {prov['n_scf_iterations']}")
        energy = prov.get("scf_energy", {})
        if isinstance(energy, dict):
            energy = energy.get("value")
        if energy is not None:
            click.echo(f"{'SCF energy':20s} {float(energy):.10f} Eh")
        if prov.get("wall_seconds") is not None:
            click.echo(f"{'Wall time':20s} {float(prov['wall_seconds']):.2f} s")
        if prov.get("hostname"):
            click.echo(f"{'Host':20s} {prov['hostname']}")
        if prov.get("dimensionality") is not None:
            dims = ["0D (molecule)", "1D", "2D", "3D"]
            d = int(prov["dimensionality"])
            click.echo(f"{'Dimensionality':20s} {dims[d] if 0 <= d <= 3 else str(d)}")

        click.echo()
        click.echo(f"{'Sections':20s} {len(reader.sections)}")
        click.echo(f"{'-' * 70}")
        click.echo(f"{'ID':25s} {'Kind':25s} {'Members':>8s}")
        click.echo(f"{'-' * 70}")

        for s in sections_out:
            size_str = s["size"]
            click.echo(
                f"{s['id']:25s} {s['kind']:25s} {s['n_members']:>5d} member(s)  {size_str:>8s}"
            )

        click.echo(f"{'-' * 70}")
        click.echo(f"{'Total':20s} {_human_size(total_size)}")
        click.echo(f"{'Archive size':20s} {_human_size(qvf_file.stat().st_size)}")
    finally:
        reader.close()


def _human_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


@main.command("export")
@click.argument("qvf_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(
        ["xyz", "cif", "obj", "gltf", "html", "json", "py", "pov", "blend", "svg", "pdf", "cml"]
    ),
    default="xyz",
    help="Output format.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output file (default: <stem>.<fmt>).",
)
def export_cmd(qvf_file: Path, fmt: str, output: Path | None) -> None:
    """Export geometry from a .qvf.

    Structure: XYZ, CIF, CML, JSON, or a vibe-qc input script (PY).
    Meshes:    OBJ, glTF. Scenes: POV-Ray, Blender. Pages/figures: HTML,
    SVG, PDF.
    """
    import numpy as np

    from vibeview.qvf import QVFError, QVFReader

    try:
        reader = QVFReader(qvf_file)
    except QVFError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from None

    try:
        sdata = reader.read_structure()
    except Exception as e:
        click.echo(f"Error reading structure: {e}", err=True)
        reader.close()
        raise SystemExit(1) from None

    out = output or qvf_file.with_suffix(f".{fmt}")

    if fmt == "xyz":
        lines = [str(len(sdata.atoms)), f"vibe-view export from {qvf_file.name}"]
        for a in sdata.atoms:
            x, y, z = a.position
            lines.append(f"{a.symbol:<2} {x:12.6f} {y:12.6f} {z:12.6f}")
        out.write_text("\n".join(lines) + "\n")
    elif fmt == "cif":
        lines = ["data_vibe-view", f"# CIF exported by vibe-view from {qvf_file.name}"]
        if sdata.lattice_vectors is not None and any(sdata.pbc):
            lat = np.asarray(sdata.lattice_vectors)
            a, b, c = (
                float(np.linalg.norm(lat[0])),
                float(np.linalg.norm(lat[1])),
                float(np.linalg.norm(lat[2])),
            )
            al = float(np.degrees(np.arccos(np.dot(lat[1], lat[2]) / (b * c))))
            be = float(np.degrees(np.arccos(np.dot(lat[0], lat[2]) / (a * c))))
            ga = float(np.degrees(np.arccos(np.dot(lat[0], lat[1]) / (a * b))))
            lines += [
                f"_cell_length_a {a:.4f}",
                f"_cell_length_b {b:.4f}",
                f"_cell_length_c {c:.4f}",
                f"_cell_angle_alpha {al:.4f}",
                f"_cell_angle_beta {be:.4f}",
                f"_cell_angle_gamma {ga:.4f}",
            ]
        lines.append("loop_")
        lines.append("_atom_site_label")
        if sdata.lattice_vectors is not None:
            lines.append("_atom_site_fract_x")
            lines.append("_atom_site_fract_y")
            lines.append("_atom_site_fract_z")
            # Row convention: QVF lattice rows are the vectors, so
            # frac = cart @ inv(L) (inv @ cart is the transpose — wrong
            # for any non-symmetric cell).
            inv = np.linalg.inv(np.asarray(sdata.lattice_vectors))
            for a in sdata.atoms:
                frac = np.asarray(a.position) @ inv
                lines.append(f"{a.symbol:<4} {frac[0]:10.6f} {frac[1]:10.6f} {frac[2]:10.6f}")
        else:
            # No cell → these are Cartesian Å; labelling them fract_*
            # would make every consumer misread the file.
            lines.append("_atom_site_Cartn_x")
            lines.append("_atom_site_Cartn_y")
            lines.append("_atom_site_Cartn_z")
            for a in sdata.atoms:
                x, y, z = a.position
                lines.append(f"{a.symbol:<4} {x:10.6f} {y:10.6f} {z:10.6f}")
        out.write_text("\n".join(lines) + "\n")
    elif fmt in ("obj", "gltf"):
        import pyvista as pv

        from vibeview.renderers.structure import StructureRenderer

        sec = next((s for s in reader.sections if s.kind == "structure"), None)
        if sec is None:
            click.echo("No structure section found.", err=True)
            reader.close()
            raise SystemExit(1)
        plotter = pv.Plotter(off_screen=True)
        try:
            StructureRenderer(sec, reader).add_to_plotter(plotter)
            if fmt == "obj":
                plotter.export_obj(str(out))
            else:
                plotter.export_gltf(str(out))
        finally:
            plotter.close()
    elif fmt == "html":
        from vibeview.export_html import export_html

        export_html(reader, out)
    elif fmt == "json":
        import json as _json

        sdata = reader.read_structure()
        atoms = [
            {
                "symbol": a.symbol,
                "atomic_number": a.atomic_number,
                "position": [float(a.position[0]), float(a.position[1]), float(a.position[2])],
            }
            for a in sdata.atoms
        ]
        secs = [{"id": s.id, "kind": s.kind} for s in reader.sections]
        src = reader.manifest.source
        data = {
            "program": src.program,
            "version": src.version,
            "calculation": src.calculation,
            "qvf_version": reader.manifest.qvf_version,
            "structure": {"atoms": atoms, "pbc": list(sdata.pbc)},
            "sections": secs,
        }
        out.write_text(_json.dumps(data, indent=2))
    elif fmt == "py":
        from vibeview.input_generator import (
            _structure_calculation_params,
            generate_input,
        )

        sdata = reader.read_structure()
        try:
            structure_params = _structure_calculation_params(sdata)
        except ValueError as exc:
            reader.close()
            raise click.ClickException(f"Cannot export vibe-qc input: {exc}") from exc
        # Reuse the file's own provenance (method/basis/functional) so the
        # regenerated input reproduces the original calculation, and carry
        # the lattice so periodic files export a PeriodicSystem script
        # instead of silently degrading to a molecular one.
        prov = getattr(reader.manifest, "provenance", None) or {}
        if hasattr(prov, "model_dump"):
            prov = prov.model_dump()
        if not isinstance(prov, dict):
            prov = {}
        script = generate_input(
            structure_params["atoms"],
            basis=str(prov.get("basis") or "sto-3g"),
            method=str(prov.get("method") or "rhf"),
            functional=str(prov.get("functional") or "PBE"),
            charge=int(prov.get("charge") or 0),
            multiplicity=int(prov.get("multiplicity") or 1),
            output=qvf_file.stem,
            template="full",
            lattice_vectors=structure_params.get("lattice_vectors"),
            dimensionality=structure_params.get("dimensionality"),
        )
        out.write_text(script)
    elif fmt == "pov":
        from vibeview.povray_export import export_povray

        export_povray(reader, str(out), style="ball_and_stick")
    elif fmt == "blend":
        from vibeview.blender_export import export_blender_script

        export_blender_script(reader, str(out), style="ball_and_stick")
    elif fmt == "svg":
        from vibeview.export_svg import export_svg

        export_svg(reader, str(out), style="ball_and_stick")
    elif fmt == "pdf":
        from vibeview.export_pdf import export_pdf

        export_pdf(reader, str(out), style="ball_and_stick")
    elif fmt == "cml":
        from vibeview.export_cml import export_cml

        export_cml(reader, str(out))

    reader.close()
    click.echo(f"Exported {fmt.upper()} to {out}")


@main.command("diff")
@click.argument("qvf_a", type=click.Path(exists=True, path_type=Path))
@click.argument("qvf_b", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--json", "fmt_json", is_flag=True, default=False, help="Machine-readable JSON output."
)
def diff_cmd(qvf_a: Path, qvf_b: Path, fmt_json: bool) -> None:
    """Compare two .qvf files: energy delta, section differences, geometry RMSD."""
    import numpy as np

    from vibeview.align import rmsd
    from vibeview.qvf import QVFError, QVFReader

    readers = []
    for label, path in [("A", qvf_a), ("B", qvf_b)]:
        try:
            readers.append((label, QVFReader(path)))
        except QVFError as e:
            click.echo(f"Error opening {label} ({path.name}): {e}", err=True)
            raise SystemExit(1) from None

    (la, ra), (lb, rb) = readers

    try:
        prov_a = _get_prov(ra)
        prov_b = _get_prov(rb)

        kinds_a = {s.kind for s in ra.sections}
        kinds_b = {s.kind for s in rb.sections}
        only_a = sorted(kinds_a - kinds_b)
        only_b = sorted(kinds_b - kinds_a)
        common = sorted(kinds_a & kinds_b)

        ea = _energy_eh(prov_a)
        eb = _energy_eh(prov_b)
        delta_e = ea - eb if ea is not None and eb is not None else None

        # Geometry RMSD
        try:
            sa = ra.read_structure()
            sb = rb.read_structure()
            pos_a = np.array([a.position for a in sa.atoms])
            pos_b = np.array([a.position for a in sb.atoms])
            geo_rmsd = float(rmsd(pos_a, pos_b)) if pos_a.shape == pos_b.shape else None
        except Exception:
            geo_rmsd = None

        if fmt_json:
            import json as _json

            click.echo(
                _json.dumps(
                    {
                        "file_a": str(qvf_a.name),
                        "file_b": str(qvf_b.name),
                        "energy_a_eh": ea,
                        "energy_b_eh": eb,
                        "delta_e_eh": delta_e,
                        "delta_e_kcal_mol": (delta_e * 627.509) if delta_e else None,
                        "delta_e_ev": (delta_e * 27.2114) if delta_e else None,
                        "geo_rmsd_a": geo_rmsd,
                        "n_sections_a": len(ra.sections),
                        "n_sections_b": len(rb.sections),
                        "kinds_only_a": only_a,
                        "kinds_only_b": only_b,
                        "kinds_common": common,
                        "converged_a": prov_a.get("scf_converged"),
                        "converged_b": prov_b.get("scf_converged"),
                    },
                    indent=2,
                )
            )
            return

        click.echo(f"{'File A':20s} {qvf_a.name}")
        click.echo(f"{'File B':20s} {qvf_b.name}")
        click.echo()

        # Energy comparison
        if ea is not None and eb is not None:
            click.echo(f"{'Energy A':20s} {ea:.10f} Eh")
            click.echo(f"{'Energy B':20s} {eb:.10f} Eh")
            if delta_e:
                # delta_e = E_A - E_B: positive delta means B sits BELOW A.
                # (The old test was inverted — 'B is X higher than A' was
                # printed exactly when B was lower.)
                sign = "lower" if delta_e > 0 else "higher"
                click.echo(
                    f"{'Delta (A-B)':20s} {delta_e:+.10f} Eh  ({delta_e * 627.509:+.2f} kcal/mol, {delta_e * 27.2114:+.3f} eV)"
                )
                click.echo(f"{'':20s} B is {abs(delta_e * 627.509):.2f} kcal/mol {sign} than A")

        # Section comparison
        click.echo()
        click.echo(f"{'Sections A':20s} {len(ra.sections)}")
        click.echo(f"{'Sections B':20s} {len(rb.sections)}")
        if only_a:
            click.echo(f"{'Only in A':20s} {', '.join(only_a)}")
        if only_b:
            click.echo(f"{'Only in B':20s} {', '.join(only_b)}")
        click.echo(f"{'Common':20s} {len(common)} kinds: {', '.join(common)}")

        # Geometry
        if geo_rmsd is not None:
            click.echo()
            click.echo(f"{'Geom. RMSD':20s} {geo_rmsd:.4f} A")

        # Convergence
        conv_a = (
            "yes"
            if prov_a.get("scf_converged")
            else ("NO" if prov_a.get("scf_converged") is False else "?")
        )
        conv_b = (
            "yes"
            if prov_b.get("scf_converged")
            else ("NO" if prov_b.get("scf_converged") is False else "?")
        )
        click.echo()
        click.echo(f"{'SCF converged':20s} A: {conv_a}, B: {conv_b}")

    finally:
        ra.close()
        rb.close()


def _get_prov(reader):
    prov = getattr(reader.manifest, "provenance", None) or {}
    if hasattr(prov, "model_dump"):
        prov = prov.model_dump()
    return prov if isinstance(prov, dict) else {}


def _energy_eh(prov: dict) -> float | None:
    e = prov.get("scf_energy", {})
    if isinstance(e, dict):
        e = e.get("value")
    return float(e) if e is not None else None


@main.command("validate")
@click.argument("qvf_files", nargs=-1, type=click.Path(exists=True, path_type=Path), required=True)
def validate_cmd(qvf_files: tuple[Path, ...]) -> None:
    """Validate QVF file integrity: schema, SHA-256 hashes, member presence."""
    from vibeview.qvf import QVFError, QVFReader, SHA256MismatchError

    n_ok = 0
    n_bad = 0
    for qvf_file in qvf_files:
        click.echo(f"{qvf_file.name}: ", nl=False)
        reader = None
        try:
            reader = QVFReader(qvf_file)
            # Verify every member's SHA-256
            n_checked = 0
            for sec in reader.sections:
                for name, member in sec.members.items():
                    try:
                        reader._verify_and_read(member)
                        n_checked += 1
                    except SHA256MismatchError as e:
                        click.echo(f"SHA-256 MISMATCH in {sec.id}/{name}: {e}")
                        n_bad += 1
                        break
                else:
                    continue
                break
            else:
                click.echo(f"OK ({len(reader.sections)} sections, {n_checked} members verified)")
                n_ok += 1
        except QVFError as e:
            click.echo(f"INVALID: {e}")
            n_bad += 1
        finally:
            if reader:
                reader.close()

    if n_bad:
        click.echo(f"\n{n_ok} OK, {n_bad} FAILED")
        raise SystemExit(1)
    click.echo(f"\nAll {n_ok} file(s) valid.")


@main.command("capture-selftest")
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Where to write the test PNG (default: a temp dir, removed afterwards).",
)
@click.option("--size", default="400x300", show_default=True, help="Image size WxH.")
def capture_selftest_cmd(output: Path | None, size: str) -> None:
    """Validate that headless PyVista capture can render a nonblank image.

    Renders a tiny built-in structure offscreen through the same
    ``vibeview.capture`` path the real capture jobs use, checks the PNG is
    not uniformly blank, and reports the render backend. Exit 0 means
    capture works here (safe for docs-artifact / queue jobs); non-zero means
    the environment is missing the capture stack or offscreen GL (no OSMesa
    and no X / xvfb). Ideal as a queue-program or CI healthcheck before
    running example capture jobs.
    """
    import shutil

    from vibeview.capture import capture_selftest

    try:
        w, h = (int(x) for x in size.lower().split("x"))
    except ValueError:
        click.echo(f"Invalid --size {size!r}", err=True)
        raise SystemExit(1) from None

    # Deliberately no PYVISTA_OFF_SCREEN=True here: capture.py always passes
    # off_screen=True explicitly, and setting the env var would force the
    # reported off_screen_default diagnostic to True on every host.
    try:
        info = capture_selftest(output, size=(w, h))
    except Exception as e:
        click.echo(f"capture-selftest: FAILED: {type(e).__name__}: {e}", err=True)
        raise SystemExit(1) from None

    # A broken offscreen GL stack can still write a PNG — a uniformly blank
    # one. Reading it back is the difference between "wrote a file" and
    # "rendered something".
    rendered = Path(info["output"])
    try:
        import matplotlib.image as mpimg
        import numpy as np

        arr = np.asarray(mpimg.imread(rendered))
        blank = arr.size == 0 or arr.max() == arr.min()
    except Exception as e:
        click.echo(f"capture-selftest: cannot read back {rendered}: {e}", err=True)
        raise SystemExit(1) from None

    if output is None:
        # capture_selftest() drops its default PNG in a fresh mkdtemp dir; a
        # queue host runs this healthcheck before every capture job, so don't
        # leak one temp dir per run.
        if rendered.parent.name.startswith("vibeview-selftest-"):
            shutil.rmtree(rendered.parent, ignore_errors=True)
        else:
            rendered.unlink(missing_ok=True)
        info.pop("output", None)

    if blank:
        click.echo("capture-selftest: rendered image is blank", err=True)
        raise SystemExit(1)

    click.echo("capture-selftest: OK")
    for key, value in info.items():
        click.echo(f"  {key}: {value}")


@main.command("capture")
@click.argument("qvf_file", type=click.Path(exists=True, path_type=Path))
@click.option("--section", "-s", default=None, help="Section ID to capture (default: structure).")
@click.option("--output", "-o", default=None, help="Output file (default: <section>.png).")
@click.option("--isovalue", type=float, default=None, help="Isovalue for volume sections.")
@click.option("--colormap", default=None, help="Colormap for volume sections.")
@click.option("--size", default="900x600", help="Image size WxH.")
def capture_cmd(
    qvf_file: Path,
    section: str | None,
    output: str | None,
    isovalue: float | None,
    colormap: str | None,
    size: str,
) -> None:
    """Render a section to PNG via the headless capture API."""
    from pathlib import Path as P

    from vibeview.capture import capture_bands, capture_structure, capture_volume
    from vibeview.qvf import QVFError, QVFReader

    try:
        w, h = (int(x) for x in size.lower().split("x"))
    except ValueError:
        click.echo(f"Invalid --size {size!r}", err=True)
        raise SystemExit(1) from None

    try:
        reader = QVFReader(qvf_file)
    except QVFError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from None

    try:
        if section is None:
            out = P(output) if output else P(f"{qvf_file.stem}_structure.png")
            ok = capture_structure(reader, out, size=(w, h))
        elif reader.has_section(section):
            sec = reader.get_section(section)
            out = P(output) if output else P(f"{qvf_file.stem}_{section}.png")
            if sec.kind.startswith("volume.") or sec.kind == "basis.ao":
                iso = isovalue if isovalue is not None else 0.05
                cmap = (
                    colormap
                    if colormap
                    else (
                        "coolwarm"
                        if sec.kind in ("volume.orbital", "volume.spin", "volume.potential")
                        else "viridis"
                    )
                )
                ok = capture_volume(reader, section, out, isovalue=iso, colormap=cmap, size=(w, h))
            elif sec.kind == "bands":
                ok = capture_bands(reader, out)
            elif sec.kind == "structure":
                # Same render the no-section default uses — naming the
                # structure section explicitly used to be rejected.
                ok = capture_structure(reader, out, size=(w, h))
            else:
                click.echo(f"Cannot capture section kind: {sec.kind}", err=True)
                ok = False
        else:
            click.echo(f"Section {section!r} not found.", err=True)
            ok = False

        if ok:
            click.echo(f"Captured: {out}")
        else:
            click.echo("Capture failed.", err=True)
            raise SystemExit(1)
    finally:
        reader.close()


def _parse_size(value: str | None) -> tuple[int, int] | None:
    """``"120x40"`` to ``(120, 40)``; None means "ask the terminal"."""
    if not value:
        return None
    try:
        cols, rows = value.lower().split("x", 1)
        return (max(24, int(cols)), max(8, int(rows)))
    except ValueError:
        raise click.BadParameter("size must look like COLSxROWS, e.g. 120x40") from None


def _parse_rotation(value: str) -> tuple[float, float, float]:
    """Comma-separated Euler angles in degrees, X,Y,Z."""
    import math

    try:
        parts = [float(p) for p in value.split(",")]
    except ValueError:
        raise click.BadParameter("rotation must be comma-separated degrees, e.g. -15,-30,0") from None
    while len(parts) < 3:
        parts.append(0.0)
    return tuple(math.radians(p) for p in parts[:3])


@main.command("tui")
@click.argument("qvf_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--mode",
    type=click.Choice(["braille", "half"]),
    default="braille",
    show_default=True,
    help="braille = 2x4 dots per cell (sharpest); half = 2 true colours per cell.",
)
def tui_cmd(qvf_file: Path, mode: str) -> None:
    """Open a QVF in the interactive terminal viewer.

    A full 3D viewer that needs no display server, no GL and no X
    forwarding — for reading a QVF straight off the compute node it was
    produced on. Press ? inside for the key map.

    Needs the [tui] extra; ``vibe-view doctor`` prints the exact install command.
    """
    try:
        from vibeview.tui.app import run
    except ModuleNotFoundError as exc:
        from vibeview.install_hints import install_hint

        raise click.ClickException(
            f"the terminal viewer needs the [tui] extra ({exc.name} is missing).\n"
            f"  {install_hint('tui')}\n"
            f"`vibe-view show` renders a single frame with no extra dependencies."
        ) from exc
    run(qvf_file, mode=mode)


@main.command("show")
@click.argument("qvf_file", type=click.Path(exists=True, path_type=Path))
@click.option("--section", "-s", default=None, help="Section ID to render (default: the structure).")
@click.option("--all", "render_all", is_flag=True, help="Render every graphable section in turn.")
@click.option("--info", is_flag=True, help="Print the archive summary instead of a picture.")
@click.option("--size", default=None, help="Character grid as COLSxROWS (default: this terminal).")
@click.option(
    "--mode", type=click.Choice(["braille", "half"]), default="braille", show_default=True
)
@click.option("--plain", is_flag=True, help="No colour — plain characters, for pipes and logs.")
@click.option(
    "--representation",
    type=click.Choice(list(_TUI_REPRESENTATIONS)),
    default="ball_and_stick",
    show_default=True,
)
@click.option(
    "--color-by",
    type=click.Choice(["element", "chain", "secondary", "bfactor"]),
    default="element",
    show_default=True,
)
@click.option("--rotate", default="-14,-31,0", show_default=True, help="Euler angles in degrees.")
@click.option("--isovalue", default=0.05, show_default=True, type=float)
@click.option("--replicate", default="1,1,1", show_default=True, help="Supercell as nx,ny,nz.")
@click.option("--labels", is_flag=True, help="Overlay atom indices.")
@click.option("--frame", default=0, show_default=True, type=int, help="Frame of an animated kind.")
@click.option(
    "--chart",
    is_flag=True,
    help="For a reaction path or trajectory, draw the energy profile instead of the geometry.",
)
def show_cmd(
    qvf_file: Path,
    section: str | None,
    render_all: bool,
    info: bool,
    size: str | None,
    mode: str,
    plain: bool,
    representation: str,
    color_by: str,
    rotate: str,
    isovalue: float,
    replicate: str,
    labels: bool,
    frame: int,
    chart: bool,
) -> None:
    """Print one frame of a QVF to the terminal and exit.

    The non-interactive half of terminal mode: no Textual, no event loop, so
    it works over a pipe, in a CI log, and inside a script. Examples:

    \b
        vibe-view show job.qvf
        vibe-view show job.qvf -s vol_mo_3 --isovalue 0.03
        vibe-view show job.qvf --all --plain > frames.txt
    """
    from vibeview.tui import show as show_mod

    try:
        counts = tuple(int(v) for v in replicate.split(","))
    except ValueError:
        raise click.BadParameter("replicate must be nx,ny,nz") from None
    if len(counts) != 3:
        raise click.BadParameter("replicate must be nx,ny,nz")

    options = {
        "mode": mode,
        "representation": representation,
        "color_mode": color_by,
        "replication": counts,
        "isovalue": isovalue,
        "rotation": _parse_rotation(rotate),
        "show_labels": labels,
        "frame": frame,
        "chart": chart,
    }
    grid_size = _parse_size(size)
    # click.echo strips ANSI when stdout is not a terminal, which would throw
    # the colour away on the two things people actually do with this — pipe it
    # into `less -R`, or capture it to replay later. Keep the escapes unless
    # --plain (or NO_COLOR) asked for text.
    keep_color = None if (plain or os.environ.get("NO_COLOR")) else True
    if render_all:
        click.echo(
            show_mod.show_all(qvf_file, size=grid_size, plain=plain, **options), color=keep_color
        )
        return
    click.echo(
        show_mod.show(qvf_file, section, size=grid_size, plain=plain, info=info, **options),
        color=keep_color,
    )


@main.command("serve")
@click.argument(
    "directory", default=None, required=False, type=click.Path(exists=True, file_okay=False)
)
@click.option("--port", default=8080, show_default=True, type=click.IntRange(min=1, max=65535))
@click.option("--host", default="127.0.0.1", show_default=True)
def serve_cmd(directory: str | None, port: int, host: str) -> None:
    """Start a web-based QVF file browser for a directory."""
    from vibeview.serve import serve_directory

    target = Path(directory) if directory else Path.cwd()
    serve_directory(target, host=host, port=port)


@main.command("slice")
@click.argument("qvf_file", type=click.Path(exists=True, path_type=Path))
@click.option("--keep", "-k", default=None, help="Comma-separated section IDs or kinds to keep.")
@click.option("--drop", "-d", default=None, help="Comma-separated section IDs or kinds to drop.")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="Output file.")
def slice_cmd(qvf_file: Path, keep: str | None, drop: str | None, output: Path | None) -> None:
    """Extract a subset of sections into a new .qvf file.

    Keep only the sections you need for sharing or analysis. Example:

        vibe-view slice big.qvf -k structure,vol_dens_0 -o small.qvf
        vibe-view slice big.qvf -d citations0 -o no-cites.qvf
    """
    from vibeview.api import slice_qvf
    from vibeview.qvf import QVFError

    out_path = output or qvf_file.with_stem(f"{qvf_file.stem}_sliced")
    try:
        result = slice_qvf(
            qvf_file, out_path,
            keep=[v.strip() for v in keep.split(",")] if keep else None,
            drop=[v.strip() for v in drop.split(",")] if drop else None,
        )
    except (QVFError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Sliced → {result}")


@main.command("merge")
@click.argument("qvf_files", nargs=-1, type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="Output file.")
def merge_cmd(qvf_files: tuple[Path, ...], output: Path | None) -> None:
    """Combine sections from multiple .qvf files into one archive.

    Structure and provenance are taken from the first file. Duplicate section
    IDs are renamed. Useful for merging density from one calculation with
    orbitals from another.
    """
    import json as _json
    import zipfile
    from collections import Counter

    from vibeview.qvf import QVFError, QVFReader

    if len(qvf_files) < 2:
        click.echo("Need at least two .qvf files to merge.", err=True)
        raise SystemExit(1)

    readers = []
    for qvf_file in qvf_files:
        try:
            readers.append(QVFReader(qvf_file))
        except QVFError as e:
            for r in readers:
                r.close()
            click.echo(f"Error opening {qvf_file}: {e}", err=True)
            raise SystemExit(1) from None

    try:
        primary = readers[0]
        all_sections = []
        seen_ids: set[str] = set()
        id_counter: Counter[str] = Counter()
        # Map original member path → (source_qvf_path, new_member_path)
        member_sources: list[tuple[str, Path]] = []

        for i, reader in enumerate(readers):
            src_qvf = qvf_files[i]
            for sec in reader.sections:
                sid = sec.id
                if sid in seen_ids:
                    id_counter[sid] += 1
                    sid = f"{sid}_{id_counter[sid]}"
                seen_ids.add(sid)
                id_counter[sid] = 0

                members = {}
                for name, member in sec.members.items():
                    new_path = member.path
                    if i > 0:
                        # Prefix member path with file index to avoid collisions
                        new_path = f"{i}/{member.path}"
                    m = {"path": new_path, "format": member.format, "sha256": member.sha256}
                    if member.dtype:
                        m["dtype"] = member.dtype
                    if member.shape:
                        m["shape"] = member.shape
                    members[name] = m
                    member_sources.append((member.path, src_qvf, new_path))

                sec_dict: dict = {"id": sid, "kind": sec.kind, "members": members}
                if getattr(sec, "label", None):
                    sec_dict["label"] = sec.label
                if getattr(sec, "component", None):
                    sec_dict["component"] = sec.component
                if getattr(sec, "critical", False):
                    sec_dict["critical"] = True
                if getattr(sec, "trajectory_ref", None):
                    sec_dict["trajectory_ref"] = sec.trajectory_ref
                # Kind-specific peer fields (run.record's required `program`,
                # reaction.waypoints' `trajectory_ref`, volume.difference's
                # operand links, …) live in the model's extra fields — carry
                # them through instead of silently flattening the section.
                # (Cross-section id references are kept verbatim; they stay
                # valid because only *duplicate* ids are renamed.)
                for key, value in (getattr(sec, "model_extra", None) or {}).items():
                    sec_dict.setdefault(key, value)
                all_sections.append(sec_dict)

        src = primary.manifest.source
        new_source = {
            "program": src.program,
            "version": src.version,
            "calculation": f"merge of {len(qvf_files)} files",
        }
        manifest: dict = {
            "qvf_version": primary.manifest.qvf_version,
            "source": new_source,
            "sections": all_sections,
        }
        # Help text promises "provenance taken from the first file" —
        # actually carry it (and the viewer hints) through.
        prov = getattr(primary.manifest, "provenance", None)
        if prov:
            manifest["provenance"] = prov.model_dump() if hasattr(prov, "model_dump") else prov
        vd = getattr(primary.manifest, "viewer_defaults", None)
        if vd:
            manifest["viewer_defaults"] = vd.model_dump() if hasattr(vd, "model_dump") else vd

        out_path = output or Path(f"merged_{qvf_files[0].stem}.qvf")
        if out_path.suffix != ".qvf":
            out_path = out_path.with_suffix(".qvf")

        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf_out:
            zf_out.writestr("manifest.json", _json.dumps(manifest))
            done: set[str] = set()
            for orig_path, src_qvf, new_path in member_sources:
                if new_path in done:
                    continue
                done.add(new_path)
                with zipfile.ZipFile(src_qvf, "r") as zf:
                    zf_out.writestr(new_path, zf.read(orig_path))

        total = sum(len(r.sections) for r in readers)
        click.echo(f"Merged: {total} sections from {len(qvf_files)} files → {out_path}")
        click.echo(f"Size: {_human_size(out_path.stat().st_size)}")
    finally:
        for r in readers:
            r.close()


@main.command("import")
@click.argument(
    "inputs",
    nargs=-1,
    type=click.Path(exists=True, path_type=Path),
    required=True,
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Output QVF for one file, or output directory for multiple files/a directory. "
        "Defaults to a sibling .qvf or ./vibe-view-imports."
    ),
)
@click.option(
    "--from",
    "format_name",
    default=None,
    metavar="FORMAT",
    help="Force a built-in or installed-plugin format; see 'vibe-view formats'.",
)
@click.option("--force", is_flag=True, help="Replace QVF outputs that already exist.")
def import_cmd(
    inputs: tuple[Path, ...],
    output: Path | None,
    format_name: str | None,
    force: bool,
) -> None:
    """Persist loose chemistry files as validated QVF archives.

    A directory is searched recursively for formats claimed by a built-in,
    optional, or installed third-party importer. Multiple inputs produce one
    QVF per source in OUTPUT; this command does not merge unrelated sidecars.

    Examples:

        vibe-view import molecule.xyz

        vibe-view import calculation.cube -o density.qvf

        vibe-view import job-directory/ -o imported/

        vibe-view import calculation.out --from my-code
    """
    from vibeview.import_workflow import (
        ImportWorkflowError,
        execute_import,
        plan_imports,
    )

    try:
        plans = plan_imports(inputs, output=output, format_name=format_name)
    except ImportWorkflowError as exc:
        raise click.ClickException(str(exc)) from exc

    existing = [plan.destination for plan in plans if plan.destination.exists()]
    if existing and not force:
        preview = ", ".join(str(path) for path in existing[:3])
        if len(existing) > 3:
            preview += f", and {len(existing) - 3} more"
        raise click.ClickException(
            f"output already exists: {preview}; pass --force to replace it"
        )

    results = []
    for plan in plans:
        try:
            results.append(execute_import(plan, format_name=format_name, force=force))
        except ImportWorkflowError as exc:
            completed = f" ({len(results)} earlier import(s) completed)" if results else ""
            raise click.ClickException(f"{plan.source}: {exc}{completed}") from exc

    for result in results:
        kinds = ", ".join(dict.fromkeys(result.section_kinds)) or "no sections"
        click.echo(
            f"Imported {result.source} ({result.detected_format}) -> {result.destination}\n"
            f"  source: {result.source_program}; data: {kinds}; "
            f"sha256: {result.sha256}"
        )
    click.echo(f"Completed {len(results)} import(s).")


@main.command("demo")
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("vibe-view-demo.qvf"),
    show_default=True,
    help="Where to write the standalone demo QVF.",
)
@click.option("--force", is_flag=True, help="Replace an existing output file.")
@click.option(
    "--open",
    "open_browser_viewer",
    is_flag=True,
    help="Launch the browser viewer after creating the demo.",
)
@click.option(
    "--port",
    default=8080,
    show_default=True,
    type=click.IntRange(min=1, max=65535),
    help="Browser-viewer port used with --open.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Start the server without opening a browser (used with --open).",
)
@click.pass_context
def demo_cmd(
    ctx: click.Context,
    output: Path,
    force: bool,
    open_browser_viewer: bool,
    port: int,
    no_browser: bool,
) -> None:
    """Create a standalone water demo without requiring vibe-qc.

    The geometry is a small project-authored package resource. The generated
    QVF works with the browser, desktop, and terminal modes.
    """
    from vibeview.onboarding import write_demo
    from vibeview.qvf import QVFError, QVFReader

    if no_browser and not open_browser_viewer:
        raise click.UsageError("--no-browser requires --open")

    try:
        written = write_demo(output, force=force)
    except FileExistsError:
        raise click.ClickException(
            f"{output} already exists; choose another --output or pass --force"
        ) from None
    except OSError as exc:
        raise click.ClickException(f"could not write {output}: {exc}") from exc

    try:
        reader = QVFReader(written)
        section_count = len(reader.sections)
        reader.close()
    except QVFError as exc:
        written.unlink(missing_ok=True)
        raise click.ClickException(f"generated demo did not validate: {exc}") from exc

    click.echo(f"Created demo QVF: {written} ({section_count} section)")
    display_path = f'"{written}"' if " " in str(written) else str(written)
    click.echo("Try it in any vibe-view mode:")
    click.echo(f"  vibe-view show {display_path} --plain")
    click.echo(f"  vibe-view open {display_path}")
    click.echo(f"  vibe-view tui {display_path}")
    click.echo(f"  vibe-view desktop {display_path}")

    if open_browser_viewer:
        ctx.invoke(
            open_cmd,
            input_files=(written,),
            port=port,
            host="127.0.0.1",
            no_browser=no_browser,
            log_file=None,
            section=None,
            auto_compare=False,
        )


@main.command("doctor")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def doctor_cmd(json_output: bool) -> None:
    """Diagnose the core install and optional viewer modes."""
    from vibeview.onboarding import doctor_report

    report = doctor_report()
    if json_output:
        click.echo(json.dumps(report, indent=2, sort_keys=True))
    else:
        version = report["vibeview"]["version"]
        python = report["python"]
        system = report["platform"]
        click.echo(f"vibe-view doctor (vibeview {version})")
        click.echo(
            f"Python: {python['version']} ({python['implementation']}) at {python['executable']}"
        )
        click.echo(f"Platform: {system['system']} {system['release']} ({system['machine']})")
        click.echo(f"Core install: {'ready' if report['healthy'] else 'needs attention'}")

        click.echo("\nPackaged resources:")
        for name, available in report["resources"].items():
            click.echo(f"  {'OK' if available else 'MISSING':7s} {name}")

        click.echo("\nModes and optional capabilities:")
        for name, capability in report["capabilities"].items():
            status = "ready" if capability["available"] else "unavailable"
            requirement = "required" if capability["required"] else "optional"
            click.echo(f"  {name:12s} {status:11s} ({requirement})")
            if not capability["available"]:
                missing = ", ".join(capability.get("missing", ())) or "unknown requirement"
                click.echo(f"    missing: {missing}")
                click.echo(f"    fix: {capability['install_hint']}")
                if capability.get("note"):
                    click.echo(f"    note: {capability['note']}")

    if not report["healthy"]:
        raise SystemExit(1)


@main.command("formats")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
def formats_cmd(json_output: bool) -> None:
    """Show built-in, optional, and installed-plugin import formats."""
    from vibeview.onboarding import format_report

    report = format_report()
    if json_output:
        click.echo(json.dumps(report, indent=2, sort_keys=True))
        return

    click.echo("vibe-view import formats")
    for row in report["formats"]:
        status = "ready" if row["available"] else "unavailable"
        extensions = ", ".join(row["extensions"]) or "(no extensions reported)"
        click.echo(
            f"\n{row['format_name']} [{status}]\n"
            f"  extensions: {extensions}\n"
            f"  provider:   {row['provider']}\n"
            f"  data:       {', '.join(row['data_kinds']) or 'unspecified'}\n"
            f"  {row['description']}"
        )
        if row["install_hint"]:
            click.echo(f"  install: {row['install_hint']}")
        if row["error"]:
            click.echo(f"  error: {row['error']}")


@main.command("examples")
@click.option(
    "--copy",
    "copy_to",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    metavar="DIRECTORY",
    help="Copy the bundled project-authored examples to DIRECTORY.",
)
@click.option("--force", is_flag=True, help="Replace existing copied example files.")
def examples_cmd(copy_to: Path | None, force: bool) -> None:
    """Show workflows or copy the bundled standalone examples."""
    if force and copy_to is None:
        raise click.UsageError("--force requires --copy DIRECTORY")
    if copy_to is not None:
        from vibeview.onboarding import copy_examples

        try:
            written = copy_examples(copy_to, force=force)
        except FileExistsError as exc:
            raise click.ClickException(
                f"{exc.filename or exc.args[0]} already exists; pass --force to replace it"
            ) from None
        except OSError as exc:
            raise click.ClickException(f"could not copy examples to {copy_to}: {exc}") from exc
        click.echo(f"Copied {len(written)} files to {copy_to}")
        for path in written:
            click.echo(f"  {path.name}")
        return

    click.echo("vibe-view examples\n==================")
    click.echo("""
Quickstart:
  vibe-view demo                    # create a standalone water QVF
  vibe-view demo --open             # create it + open browser viewer
  vibe-view examples --copy demo/   # copy authored example inputs

Open:
  vibe-view open result.qvf         # interactive 3D viewer
  vibe-view open structure.xyz      # loose formats convert in memory
  vibe-view open a.qvf b.qvf        # open multiple files
  vibe-view compare a.qvf b.qvf     # open with compare mode

Import:
  vibe-view import structure.xyz    # persist as structure.qvf
  vibe-view import job-dir/ -o qvf/ # recursively convert a job directory
  vibe-view formats                 # list built-in and plugin importers

Inspect:
  vibe-view info result.qvf         # metadata + sections
  vibe-view info result.qvf --short # one-line summary
  vibe-view info result.qvf --json  # machine-readable

Extract:
  vibe-view table result.qvf --kind atom_properties --format csv > charges.csv
  vibe-view export result.qvf -f xyz -o geometry.xyz
  vibe-view export result.qvf -f html -o viewer.html

Generate figures:
  vibe-view capture result.qvf -s vol_dens_0 -o density.png
  vibe-view batch results/*.qvf --volumes -o gallery/

Compare:
  vibe-view diff hf.qvf pbe.qvf
  vibe-view diff hf.qvf pbe.qvf --json | jq '.delta_e_kcal_mol'

Validate:
  vibe-view validate *.qvf

Installation:
  vibe-view doctor                  # diagnose optional viewer modes

Manipulate:
  vibe-view slice big.qvf -k structure,vol_dens_0 -o small.qvf
  vibe-view merge a.qvf b.qvf -o combined.qvf

Serve & share:
  vibe-view serve .                 # web file browser
  vibe-view from-vq JOB_ID          # fetch from vibe-queue

Utils:
  vibe-view config --init           # create default config
  vibe-view recent                  # recently opened files
  vibe-view stats .                 # directory statistics

Python SDK:
  from vibeview import info, diff, validate, get_structure

Jupyter:
  %load_ext vibeview.jupyter
  %vibeview result.qvf

Full guide: https://vibe-qc.com/vibe-view/docs/
""")


@main.command("config")
@click.option("--init", "do_init", is_flag=True, default=False, help="Create default config file.")
@click.option("--path", "show_path", is_flag=True, default=False, help="Show config file path.")
def config_cmd(do_init: bool, show_path: bool) -> None:
    """Manage vibe-view configuration."""
    from vibeview.config import _config_path, init_config, read_config

    if show_path:
        click.echo(str(_config_path()))
        return

    if do_init:
        path = init_config()
        click.echo(f"Config created: {path}")
        return

    path = _config_path()
    config = read_config()
    if not config:
        click.echo(f"No config found at {path}")
        click.echo("Run 'vibe-view config --init' to create a default config.")
        return
    import json

    click.echo(json.dumps(config, indent=2))


@main.command("stats")
@click.argument("directory", type=click.Path(exists=True, file_okay=False), default=".")
def stats_cmd(directory: str) -> None:
    """Show aggregate statistics for all .qvf files in a directory."""
    from pathlib import Path

    from vibeview.qvf import QVFError, QVFReader

    dir_path = Path(directory)
    qvfs = sorted(dir_path.glob("*.qvf"))
    if not qvfs:
        click.echo(f"No .qvf files found in {dir_path}")
        return

    methods: dict[str, int] = {}
    converged = 0
    not_converged = 0
    smallest: tuple[str | None, float] = (None, float("inf"))
    largest: tuple[str | None, float] = (None, float("-inf"))
    total_size = 0
    total_sections = 0
    kinds: dict[str, int] = {}

    for qvf in qvfs:
        total_size += qvf.stat().st_size
        try:
            reader = QVFReader(qvf)
            try:
                prov = getattr(reader.manifest, "provenance", None) or {}
                if hasattr(prov, "model_dump"):
                    prov = prov.model_dump()
                m = prov.get("method", "?")
                methods[m] = methods.get(m, 0) + 1
                # Three-valued: None means the file carries no convergence
                # flag at all — counting that as "Not converged" misreports
                # perfectly fine files.
                conv_flag = prov.get("scf_converged")
                if conv_flag:
                    converged += 1
                elif conv_flag is False:
                    not_converged += 1
                energy = prov.get("scf_energy", {})
                if isinstance(energy, dict):
                    energy = energy.get("value")
                if energy is not None:
                    e = float(energy)
                    if e < smallest[1]:
                        smallest = (qvf.name, e)
                    if e > largest[1]:
                        largest = (qvf.name, e)
                total_sections += len(reader.sections)
                for sec in reader.sections:
                    kinds[sec.kind] = kinds.get(sec.kind, 0) + 1
            finally:
                reader.close()
        except QVFError:
            pass

    click.echo(f"Directory: {dir_path}")
    click.echo(f"Files:     {len(qvfs)}")
    click.echo(f"Total size:{_human_size(total_size)}")
    click.echo(f"Sections:  {total_sections} ({len(kinds)} kinds)")
    if methods:
        click.echo(f"\nMethods:")
        for m, n in sorted(methods.items(), key=lambda x: -x[1]):
            click.echo(f"  {m:20s} {n}")
    click.echo(f"\nConverged:     {converged}/{len(qvfs)}")
    if not_converged:
        click.echo(f"Not converged: {not_converged}")
    if smallest[0]:
        click.echo(f"Lowest energy:  {smallest[0]} ({smallest[1]:.8f} Eh)")
    if largest[0]:
        click.echo(f"Highest energy: {largest[0]} ({largest[1]:.8f} Eh)")
    if len(kinds) > 1:
        click.echo(f"\nSection kinds:")
        for k, n in sorted(kinds.items(), key=lambda x: -x[1]):
            click.echo(f"  {k:25s} {n}")


@main.command("recent")
@click.option("--limit", "-n", default=10, show_default=True, help="Number of entries.")
def recent_cmd(limit: int) -> None:
    """Show recently opened QVF files."""
    from vibeview.recent import get_recent

    files = get_recent(limit)
    if not files:
        click.echo("No recent files.")
        return
    for i, f in enumerate(files, 1):
        click.echo(f"{i:>2d}. {f}")


@main.command("quickstart")
@click.option("--port", default=8080, type=click.IntRange(min=1, max=65535))
def quickstart_cmd(port: int) -> None:
    """Run a demo calculation and open it in vibe-view — fastest path to a 3D molecule."""
    click.echo("vibe-view quickstart — running H2O RHF/STO-3G...")
    try:
        from vibeqc import Atom, Molecule, run_job
    except ImportError:
        click.echo(
            "vibe-qc is not installed in this environment. Install it from a "
            "vibe-qc checkout (pip install -e <vibeqc-repo>) — `pip install -e .` "
            "here would only reinstall vibe-view.",
            err=True,
        )
        raise SystemExit(1) from None

    mol = Molecule(
        [
            Atom(8, [0.0, 0.0, 0.0]),
            Atom(1, [0.0, 1.43, -0.98]),
            Atom(1, [0.0, -1.43, -0.98]),
        ]
    )
    run_job(
        mol,
        basis="sto-3g",
        method="rhf",
        output="vibe-view-demo",
        output_qvf=True,
        write_cube=["density", "homo", "lumo"],
        write_molden_file=True,
    )

    click.echo("Opening vibe-view...")
    create_app, serve = _load_app()
    from vibeview.banner import print_banner
    from vibeview.qvf import QVFReader

    reader = QVFReader("vibe-view-demo.qvf")
    print_banner(reader)
    app = create_app([reader])
    serve(app, host="127.0.0.1", port=port)


@main.command("animate")
@click.argument("qvf_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--kind",
    "-k",
    default="auto",
    help="Section kind: trajectory, vibration, reaction, orbital, turntable, or auto-detect.",
)
@click.option("--output", "-o", default=None, help="Output file (default: <stem>_<kind>.mp4).")
@click.option("--fps", default=5, type=float, help="Frames per second.")
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["mp4", "gif", "frames"]),
    default="mp4",
    help="Output format.",
)
@click.option("--size", default="900x600", help="Image size WxH.")
@click.option("--mode", default=0, type=int, help="Vibrational mode index (0-based).")
@click.option("--isovalue", default=0.04, type=float, help="Isovalue for orbital animation.")
def animate_cmd(
    qvf_file: Path,
    kind: str,
    output: str | None,
    fps: float,
    fmt: str,
    size: str,
    mode: int,
    isovalue: float,
) -> None:
    """Render animated sections (trajectory, reaction paths, vibrations, orbitals) to MP4/GIF.

    Requires ffmpeg for MP4 output (auto-detected). Falls back to GIF if
    ffmpeg is not available and Pillow is installed.
    """
    from vibeview.animation import (
        render_orbital_animation,
        render_reaction_video,
        render_trajectory_video,
        render_turntable,
        render_vibration_video,
    )
    from vibeview.qvf import QVFError, QVFReader

    try:
        w, h = (int(x) for x in size.lower().split("x"))
    except ValueError:
        click.echo(f"Invalid --size {size!r}", err=True)
        raise SystemExit(1) from None

    try:
        reader = QVFReader(qvf_file)
    except QVFError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from None

    try:
        if kind == "auto":
            kinds = {s.kind for s in reader.sections}
            if "trajectory" in kinds:
                kind = "trajectory"
            elif "reaction.path" in kinds:
                kind = "reaction"
            elif "vibrations" in kinds:
                kind = "vibration"
            elif "wavefunction.gto" in kinds:
                kind = "orbital"
            else:
                click.echo(
                    "No animatable section found "
                    "(trajectory, reaction.path, vibrations, wavefunction.gto).",
                    err=True,
                )
                raise SystemExit(1) from None

        stem = qvf_file.stem
        ext = fmt if fmt != "frames" else ""
        out = (
            Path(output)
            if output
            else Path(f"{stem}_{kind}.{ext}")
            if ext
            else Path(f"{stem}_{kind}")
        )

        if kind == "trajectory":
            result = render_trajectory_video(reader, out, fps=fps, size=(w, h), format=fmt)
        elif kind == "reaction":
            result = render_reaction_video(reader, out, fps=fps, size=(w, h), format=fmt)
        elif kind == "vibration":
            result = render_vibration_video(
                reader, out, mode=mode, fps=fps, size=(w, h), format=fmt
            )
        elif kind == "orbital":
            result = render_orbital_animation(
                reader, out, fps=fps, isovalue=isovalue, size=(w, h), format=fmt
            )
        elif kind == "turntable":
            # Turntable needs a live plotter, so build a PyVista scene
            import pyvista as pv

            from vibeview.renderers.structure import StructureRenderer

            struct_sec = next((s for s in reader.sections if s.kind == "structure"), None)
            if struct_sec is None:
                click.echo("No structure section for turntable animation.", err=True)
                raise SystemExit(1) from None
            plotter = pv.Plotter(off_screen=True, window_size=[w, h])
            try:
                plotter.set_background("#1a1a2e")
                StructureRenderer(struct_sec, reader).add_to_plotter(plotter)
                plotter.view_isometric()
                plotter.reset_camera()
                result = render_turntable(
                    plotter,
                    out,
                    num_frames=int(fps * 4),
                    duration=4.0,
                    resolution=(w, h),
                    format=fmt,
                )
            finally:
                plotter.close()
        else:
            click.echo(
                f"Unknown kind: {kind}. Use trajectory, vibration, orbital, or turntable.",
                err=True,
            )
            raise SystemExit(1) from None

        if result:
            if fmt == "frames":
                n_frames = len(list(result.glob("frame_*.png")))
                click.echo(f"Animation: {n_frames} frames in {result}/")
            else:
                size_str = _human_size(result.stat().st_size)
                click.echo(f"Animation: {result} ({size_str})")
        else:
            click.echo("Animation failed.", err=True)
            raise SystemExit(1)
    finally:
        reader.close()


@main.command("h-add")
@click.argument("qvf_file", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", default=None, help="Output QVF file (default: <stem>_h.qvf).")
def hadd_cmd(qvf_file: Path, output: str | None) -> None:
    """Add hydrogen atoms to saturate all open valences."""
    import json as _json
    import zipfile

    from vibeview.build_tools import add_hydrogens
    from vibeview.qvf import QVFError, QVFReader

    try:
        reader = QVFReader(qvf_file)
    except QVFError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from None

    try:
        sdata = reader.read_structure()
        atoms = [
            {
                "symbol": a.symbol,
                "atomic_number": a.atomic_number or 0,
                "position": [float(a.position[0]), float(a.position[1]), float(a.position[2])],
            }
            for a in sdata.atoms
        ]
        new_atoms = add_hydrogens(atoms)
        added = len(new_atoms) - len(atoms)
        click.echo(f"Added {added} hydrogen atom(s). Total: {len(new_atoms)}")

        out = Path(output) if output else qvf_file.with_stem(f"{qvf_file.stem}_h")
        if out.suffix != ".qvf":
            out = out.with_suffix(".qvf")

        # Write new QVF with updated structure — carry the lattice through,
        # otherwise a periodic input comes out with pbc=[True]*3 but no
        # lattice_vectors (self-inconsistent structure).
        import hashlib

        lat = (
            sdata.lattice_vectors.tolist()
            if hasattr(sdata.lattice_vectors, "tolist")
            else sdata.lattice_vectors
        )
        structure = _json.dumps(
            {"atoms": new_atoms, "pbc": list(sdata.pbc), "lattice_vectors": lat}
        ).encode()
        manifest = {
            "qvf_version": 1,
            "source": {"program": "vibe-view", "version": "1.0", "calculation": "h-add"},
            "sections": [
                {
                    "id": "structure",
                    "kind": "structure",
                    "members": {
                        "structure": {
                            "path": "s.json",
                            "format": "json",
                            "sha256": hashlib.sha256(structure).hexdigest(),
                        }
                    },
                }
            ],
        }
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", _json.dumps(manifest))
            zf.writestr("s.json", structure)
        click.echo(f"Written: {out}")
    finally:
        reader.close()


@main.command("supercell")
@click.argument("qvf_file", type=click.Path(exists=True, path_type=Path))
@click.option("--nx", default=1, type=int, help="Replication in x direction.")
@click.option("--ny", default=1, type=int, help="Replication in y direction.")
@click.option("--nz", default=1, type=int, help="Replication in z direction.")
@click.option("--output", "-o", default=None, help="Output QVF file.")
def supercell_cmd(qvf_file: Path, nx: int, ny: int, nz: int, output: str | None) -> None:
    """Build a supercell by replicating the unit cell Nx x Ny x Nz times."""
    import json as _json
    import zipfile

    from vibeview.build_tools import build_supercell
    from vibeview.qvf import QVFError, QVFReader

    try:
        reader = QVFReader(qvf_file)
    except QVFError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from None

    try:
        sdata = reader.read_structure()
        if sdata.lattice_vectors is None:
            click.echo("Not a periodic system (no lattice vectors).", err=True)
            raise SystemExit(1) from None
        lat = (
            sdata.lattice_vectors.tolist()
            if hasattr(sdata.lattice_vectors, "tolist")
            else sdata.lattice_vectors
        )
        atoms = [
            {
                "symbol": a.symbol,
                "atomic_number": a.atomic_number or 0,
                "position": [float(a.position[0]), float(a.position[1]), float(a.position[2])],
            }
            for a in sdata.atoms
        ]
        new_atoms = build_supercell(atoms, lat, (nx, ny, nz))
        click.echo(f"Supercell {nx}x{ny}x{nz}: {len(atoms)} → {len(new_atoms)} atoms")

        out = Path(output) if output else qvf_file.with_stem(f"{qvf_file.stem}_{nx}x{ny}x{nz}")
        if out.suffix != ".qvf":
            out = out.with_suffix(".qvf")

        import hashlib

        # Scale each lattice VECTOR (row) — the old expression built the
        # transpose, wrong for any non-symmetric cell.
        super_lat = [
            [c * n for c in vec] for vec, n in zip(lat[:3], (nx, ny, nz), strict=True)
        ]
        structure = _json.dumps(
            {"atoms": new_atoms, "pbc": list(sdata.pbc), "lattice_vectors": super_lat}
        ).encode()
        manifest = {
            "qvf_version": 1,
            "source": {
                "program": "vibe-view",
                "version": "1.0",
                "calculation": f"supercell {nx}x{ny}x{nz}",
            },
            "sections": [
                {
                    "id": "structure",
                    "kind": "structure",
                    "members": {
                        "structure": {
                            "path": "s.json",
                            "format": "json",
                            "sha256": hashlib.sha256(structure).hexdigest(),
                        }
                    },
                }
            ],
        }
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", _json.dumps(manifest))
            zf.writestr("s.json", structure)
        click.echo(f"Written: {out}")
    finally:
        reader.close()


@main.command("batch-compare")
@click.argument("qvf_files", nargs=-1, type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--reference", "-r", type=int, default=0, help="Index of reference file")
@click.option("--json", "fmt_json", is_flag=True, help="Output JSON")
def batch_compare_cmd(qvf_files, reference, fmt_json):
    """Compare multiple .qvf files: energies, RMSD, sections."""
    from vibeview.batch_compare import batch_compare_to_table, compare_batch

    if len(qvf_files) < 2:
        click.echo("Need at least 2 .qvf files to compare.")
        raise SystemExit(1)

    paths = [str(p) for p in qvf_files]
    results = compare_batch(paths, reference_idx=reference)

    if fmt_json:
        click.echo(json.dumps(results, indent=2, default=str))
    else:
        click.echo(results["summary"])
        click.echo("")
        click.echo(batch_compare_to_table(results))


@main.command("dashboard")
@click.argument("patterns", nargs=-1, required=True)
@click.option("--output", "-o", type=click.Path(path_type=Path), help="Output file")
@click.option("--cols", type=int, default=3, help="Grid columns")
@click.option("--cell-size", type=int, default=300, help="Cell size in pixels")
@click.option("--html", "fmt_html", is_flag=True, help="Generate HTML dashboard")
def dashboard_cmd(patterns, output, cols, cell_size, fmt_html):
    """Render a grid preview of multiple QVF files."""
    import glob as _glob
    from pathlib import Path as _Path

    paths = []
    for p in patterns:
        expanded = _glob.glob(p, recursive=True)
        if expanded:
            paths.extend([_Path(x) for x in expanded])
        else:
            paths.append(_Path(p))

    # Unmatched patterns fall through as literal paths — check they exist
    # instead of reporting success for files that were never rendered.
    missing = [p for p in paths if not p.exists()]
    if missing:
        for p in missing:
            click.echo(f"Error: no such file: {p}", err=True)
        raise SystemExit(1)

    paths = sorted(set(p for p in paths if p.suffix == ".qvf"))
    if not paths:
        click.echo("No .qvf files found matching the patterns.", err=True)
        raise SystemExit(1)

    paths_str = [str(p) for p in paths]

    if fmt_html:
        from vibeview.dashboard import dashboard_to_html

        html = dashboard_to_html(paths_str, cols=cols, cell_size=cell_size)
        out = output or _Path("dashboard.html")
        out.write_text(html)
        click.echo(f"Dashboard: {out} ({len(paths)} files)")
    else:
        from vibeview.dashboard import render_dashboard

        out = output or _Path("dashboard.png")
        render_dashboard(paths_str, str(out), cols=cols, cell_size=cell_size)
        click.echo(f"Dashboard: {out} ({len(paths)} files)")


@main.command("vq-features")
@click.argument("feature", required=False)
def vq_features_cmd(feature):
    """Show what vibe-qc needs to produce for specific visualizations."""
    from vibeview.feature_requests import VIS_NEEDS, list_needed, list_supported, what_do_i_need

    if feature:
        click.echo(what_do_i_need(feature))
    else:
        click.echo("## Visualization features and their vibe-qc requirements\n")
        supported = list_supported()
        needed = list_needed()
        click.echo(f"Already supported ({len(supported)}): {', '.join(supported)}")
        click.echo(f"Needs backend work ({len(needed)}): {', '.join(needed)}")
        click.echo("\nUse: vibe-view vq-features <feature> for details.")


def _electron_dir() -> Path:
    """The Electron app directory at the standalone checkout root (electron/)."""
    from vibeview.install_hints import source_project_dir

    project = source_project_dir()
    if project is not None:
        return project / "electron"
    return Path(__file__).resolve().parent.parent.parent / "electron"


def _electron_binary(electron_dir: Path) -> Path:
    """Platform-specific path of the downloaded Electron executable."""
    dist = electron_dir / "node_modules" / "electron" / "dist"
    if sys.platform == "darwin":
        return dist / "Electron.app" / "Contents" / "MacOS" / "Electron"
    if sys.platform == "win32":
        return dist / "electron.exe"
    return dist / "electron"


def _write_desktop_config(port: int, file: str | None) -> Path:
    """Write the launcher → Electron handshake config.

    main.js reads this via the VIBEVIEW_DESKTOP_CONFIG env var: which
    port to serve on, which file to open at startup, which Python
    interpreter runs the server (so the venv that launched us is the
    one Electron spawns), and the cwd whose .qvf files the browse page
    lists — that's what makes ``vibe-view desktop`` work from any
    directory.

    ``version`` and ``codename`` ride along so the Electron About box
    reports what this Python package says rather than a string pasted
    into main.js. main.js keeps a FALLBACK_CODENAME for the double-click
    launch, which sets no config at all; the drift test in
    ``tests/test_release_codenames.py`` holds the two together.
    """
    import json
    import tempfile

    from vibeview import __version__
    from vibeview.codenames import codename_for_version

    config = {
        "port": port,
        "file": str(Path(file).resolve()) if file else None,
        "python": sys.executable,
        "cwd": os.getcwd(),
        "version": __version__,
        "codename": codename_for_version(__version__),
    }
    fd, config_path = tempfile.mkstemp(prefix="vibe-view-desktop-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(config, f, indent=2)
    return Path(config_path)


@main.command("desktop")
@click.option(
    "--port",
    default=8765,
    show_default=True,
    type=click.IntRange(min=1, max=65535),
    help="Port for the vibe-view server behind the window.",
)
@click.option(
    "--no-sandbox",
    is_flag=True,
    help="Disable Electron's Linux sandbox (unsafe; restricted environments only).",
)
@click.argument("file", required=False, type=click.Path(exists=True))
def desktop_cmd(file, port, no_sandbox):
    """Launch the vibe-view desktop application (native Electron window).

    Opens the 3D molecular viewer in its own window with native menus,
    file dialogs, drag-and-drop, and an Open Recent list. On first run,
    downloads Electron (~120 MB). Works from any directory.

    Examples:
        vibe-view desktop
        vibe-view desktop results/water.qvf
        vibe-view desktop --port 9090
    """
    import subprocess

    getuid = getattr(os, "geteuid", None)
    if sys.platform.startswith("linux") and getuid and getuid() == 0:
        click.echo(
            "vibe-view desktop must run as a regular Linux user, not as root.",
            err=True,
        )
        click.echo(
            "Run it without sudo so Electron can keep its security sandbox enabled.",
            err=True,
        )
        raise SystemExit(1)
    if no_sandbox and not sys.platform.startswith("linux"):
        raise click.UsageError("--no-sandbox is supported only on Linux")

    electron_dir = _electron_dir()
    if not electron_dir.exists():
        click.echo(f"Electron directory not found: {electron_dir}", err=True)
        click.echo(
            "The desktop app requires electron/ in a standalone vibe-view checkout.", err=True
        )
        raise SystemExit(1)

    # First run or damaged/stale runtime: install the reviewed Electron binary
    # and source wrapper. The check uses ELECTRON_RUN_AS_NODE, so a branded
    # macOS bundle cannot accidentally open a window during validation.
    binary = _electron_binary(electron_dir)
    installer = electron_dir / "install-electron.py"
    checked = subprocess.run(
        [sys.executable, str(installer), "--check"],
        cwd=str(electron_dir),
        capture_output=True,
        text=True,
    )
    if checked.returncode == 2:
        click.echo(checked.stderr.strip(), err=True)
        raise SystemExit(2)
    if checked.returncode != 0:
        if binary.exists():
            click.echo("Repairing the reviewed Electron runtime...")
        else:
            click.echo("First run: downloading Electron (~120 MB, one-time)...")
        installed = subprocess.run(
            [sys.executable, str(installer)],
            cwd=str(electron_dir),
        )
        if installed.returncode != 0 or not binary.exists() or not os.access(binary, os.X_OK):
            click.echo("Electron installation or validation failed.", err=True)
            raise SystemExit(installed.returncode or 1)

    # The Electron main process owns the server lifecycle: it spawns
    # `vibe-view serve` with our interpreter and kills it on quit.
    _abort_if_port_in_use("127.0.0.1", port)
    # Persist this interpreter so a later plain double-click on a .qvf finds
    # vibe-view even without the live launcher handshake.
    _record_interpreter()
    config_path = _write_desktop_config(port, file)

    env = dict(os.environ)
    env["VIBEVIEW_DESKTOP_CONFIG"] = str(config_path)
    launch_command = [str(binary)]
    if no_sandbox:
        click.echo(
            "Warning: Electron's security sandbox is disabled for this launch.",
            err=True,
        )
        launch_command.append("--no-sandbox")
    launch_command.append(str(electron_dir))

    click.echo(f"vibe-view desktop: http://127.0.0.1:{port}  (native window)")
    if file:
        click.echo(f"  Opening: {Path(file).resolve()}")
    click.echo("  Quit the window (or Ctrl+C here) to stop.")

    app_proc = None
    return_code = 1
    interrupted = False
    try:
        app_proc = subprocess.Popen(launch_command, env=env)
        return_code = app_proc.wait()
    except OSError as exc:
        click.echo(f"Could not launch Electron: {exc}", err=True)
    except KeyboardInterrupt:
        interrupted = True
        click.echo()
        if app_proc is not None:
            app_proc.terminate()
            app_proc.wait()
    finally:
        config_path.unlink(missing_ok=True)
    if not interrupted and return_code != 0:
        exit_status = 128 + abs(return_code) if return_code < 0 else return_code
        if return_code < 0:
            click.echo(
                f"Electron exited after signal {abs(return_code)} "
                f"(status {exit_status}).",
                err=True,
            )
        else:
            click.echo(f"Electron exited with status {exit_status}.", err=True)
        if sys.platform.startswith("linux") and not no_sandbox:
            click.echo(
                "If Electron reported a sandbox error, enable unprivileged user "
                "namespaces or an administrator-managed Electron sandbox.",
                err=True,
            )
            click.echo(
                "For a trusted local file only, retry with --no-sandbox.",
                err=True,
            )
        raise SystemExit(exit_status or 1)


if __name__ == "__main__":
    main()
