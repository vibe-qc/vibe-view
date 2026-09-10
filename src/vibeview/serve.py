"""Lightweight HTTP directory browser for QVF files.

Starts a minimal web server that lists .qvf files in a directory and
serves vibe-view via iframe embedding.  Used by ``vibe-view serve``.

Also the backend of the Electron desktop app (``vibe-view desktop``):
``GET /open?file=/abs/path.qvf`` (or ``/?file=...``) spawns a dedicated
3D-viewer process (``vibe-view open FILE --no-browser``) on a free port
and redirects to it once bound.  A loading page polls ``/open-status``
in the meantime, so the caller never blocks on VTK cold-start.
"""

from __future__ import annotations

import html
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path

_DIR_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>vibe-view — {title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
  h1 {{ font-size: 1.4em; margin-bottom: 8px; color: #7c8aff; }}
  .path {{ font-size: 0.85em; color: #888; margin-bottom: 20px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #2a2a4e; }}
  th {{ color: #7c8aff; font-weight: 600; font-size: 0.85em; }}
  tr:hover {{ background: #2a2a4e; }}
  a {{ color: #7c8aff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .size {{ text-align: right; font-family: monospace; font-size: 0.85em; color: #888; }}
  .ok {{ color: #4caf50; }}
  .err {{ color: #f44336; }}
  .dim {{ color: #555; cursor: not-allowed; }}
  .parent {{ margin-bottom: 16px; }}
  .footer {{ margin-top: 24px; font-size: 0.8em; color: #555; }}
  .welcome {{ background: #2a2a4e; border-radius: 8px; padding: 16px 20px;
             margin-bottom: 20px; }}
  .welcome h2 {{ font-size: 1.05em; color: #e0e0e0; margin-bottom: 8px; }}
  .welcome ul {{ margin: 8px 0 0 20px; line-height: 1.7; font-size: 0.9em; }}
  .welcome code {{ background: #1a1a2e; padding: 1px 6px; border-radius: 4px;
                  font-size: 0.9em; }}
</style>
</head>
<body>
<h1>vibe-view</h1>
<div class="path">{path_display}</div>
{welcome}
<div class="parent">{parent_link}</div>
<table>
<tr><th>File</th><th>Calculation</th><th>Energy (Eh)</th><th>Sections</th><th>Size</th><th>Modified</th></tr>
{rows}
</table>
<div class="footer">vibe-view {version} — {n_files} file(s)</div>
</body>
</html>"""


_WELCOME_CARD = """<div class="welcome">
<h2>👋 Welcome to vibe-view</h2>
Click a <b>.qvf</b> file below to open it in the interactive 3D viewer.
<ul>
<li><b>Open a file</b> — File → Open (Cmd+O in the desktop app), or drag &amp; drop a .qvf onto the window</li>
<li><b>Start from scratch</b> — ➕ New structure (above) creates an editable file:
press <code>e</code> for edit mode, sketch with clicks / Add Hydrogens / fragments,
then build the input from the Calculate dialog</li>
<li><b>Change folder</b> — File → Open Folder… (Cmd+Shift+O), or the ⬆ Parent / ⌂ Home links above the listing</li>
<li><b>Structure</b> — atoms and bonds in 3D; rotate with the mouse</li>
<li><b>Molecular orbitals</b> — HOMO, LUMO and friends as isosurfaces</li>
<li><b>Keyboard</b> — press <code>?</code> in the viewer for shortcuts (e=edit, m=measure, r=reset, s=screenshot)</li>
</ul>
</div>"""


def _html_text(value: object) -> str:
    """Escape one untrusted value for HTML text or a quoted attribute."""
    return html.escape(str(value), quote=True)


def _script_string(value: object) -> str:
    """Serialize one string inside an inline script without ending the tag."""
    encoded = json.dumps(str(value), ensure_ascii=True)
    return (
        encoded.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _attachment_disposition(filename: str) -> str:
    """Build a header-safe UTF-8 download filename (RFC 5987)."""
    encoded = urllib.parse.quote(filename, safe="")
    return f"attachment; filename*=UTF-8''{encoded}"


def _dir_writable(dir_path: Path) -> bool:
    """Return whether vibe-view may create files in ``dir_path``.

    The New-structure action writes a scratch .qvf into the browsed folder.
    A Dock-launched desktop app can land on the filesystem root, and users
    can browse into read-only folders (system dirs, mounted volumes) where
    that write fails with EROFS/EACCES after the click. ``os.access`` runs
    the real-uid/gid check so read-only folders are detectable before the
    click instead of surfacing as a 500.
    """
    return os.access(dir_path, os.W_OK)


def _create_new_qvf(target_dir: Path) -> Path:
    """Write a fresh scratch structure QVF into ``target_dir``.

    This is the from-scratch entry point: launching the app without a QVF
    (exactly the "I want to build an input" case) used to dead-end in an
    empty listing with nothing to click. The scratch file holds a single
    carbon atom — one "Add Hydrogens" in edit mode makes it methane, and
    the sketch grows from there.
    """
    from vibeview.converters import atoms_to_qvf

    name = "new-structure.qvf"
    counter = 1
    while (target_dir / name).exists():
        counter += 1
        name = f"new-structure-{counter}.qvf"
    out = target_dir / name
    buf = atoms_to_qvf([(6, 0.0, 0.0, 0.0)], label="new structure")
    out.write_bytes(buf.getvalue())
    return out


_SKIP_DIRS = {
    "node_modules",
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    ".tox",
    # macOS noise a home/disk-rooted walk would otherwise drown in.
    "Library",
    "Applications",
    "System",
    "Volumes",
    "private",
}
_RECURSIVE_LIMIT = 500
_RECURSIVE_TIME_BUDGET_S = 8.0


def _find_qvfs(
    root: Path,
    limit: int = _RECURSIVE_LIMIT,
    time_budget_s: float = _RECURSIVE_TIME_BUDGET_S,
) -> tuple[list[Path], bool]:
    """Walk ``root`` and collect every ``.qvf`` beneath it, newest first.

    Hidden directories and the usual junk (`node_modules`, `.venv`, macOS
    `Library`, …) are skipped, and the walk carries a wall-clock budget:
    the desktop app's browse root can be the home directory or even ``/``
    (a dock launch has no cwd handshake), and an unbounded walk there
    takes minutes while the page shows nothing at all. Returns
    ``(paths, truncated)``: at most ``limit`` paths sorted by modification
    time (newest first), and whether the walk was cut off by count or by
    time — the caller must say so rather than presenting a partial list
    as complete.
    """
    import time

    deadline = time.monotonic() + time_budget_s
    hits: list[Path] = []
    truncated = False
    for cur, dirnames, filenames in os.walk(root, followlinks=False):
        if time.monotonic() > deadline:
            truncated = True
            break
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS
        ]
        for fn in filenames:
            if fn.endswith(".qvf"):
                hits.append(Path(cur) / fn)
                if len(hits) > limit * 4:
                    # Hard stop on pathological trees; sort what we have.
                    truncated = True
                    break
        if truncated:
            break
    hits.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    if len(hits) > limit:
        truncated = True
        hits = hits[:limit]
    return hits, truncated


def _browse_recursive(dir_path: Path) -> str:
    """One flat listing of every QVF under ``dir_path``: job name,
    location, modified date — newest first, so 'where did that run land'
    does not require knowing the directory layout."""
    import time

    from vibeview import __version__
    from vibeview.qvf import QVFError, QVFReader

    dir_path = dir_path.resolve()
    files, truncated = _find_qvfs(dir_path)
    rows = []
    # Reading each manifest costs a zip-open (~50 ms apiece — half a
    # minute for a full 500-row listing), so the enrichment gets its own
    # wall-clock budget. Rows past it still list from stat() alone: a
    # complete, fast index beats a slow, decorated one.
    meta_deadline = time.monotonic() + _RECURSIVE_TIME_BUDGET_S
    meta_read = 0
    meta_skipped = False
    for entry in files:
        try:
            mtime_str = datetime.fromtimestamp(entry.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M"
            )
            size = _human_size(entry.stat().st_size)
        except OSError:
            continue
        if time.monotonic() > meta_deadline:
            meta_skipped = True
            calc, n_sec = "", ""
        else:
            meta_read += 1
            try:
                reader = QVFReader(entry)
                calc = str(reader.manifest.source.calculation)
                n_sec = len(reader.sections)
                reader.close()
            except (QVFError, Exception):
                calc, n_sec = "(unreadable)", 0
        rel = entry.parent.relative_to(dir_path)
        loc = str(rel) if str(rel) != "." else ""
        open_url = "/open?file=" + urllib.parse.quote(str(entry.resolve()))
        view_url = "/view?file=" + urllib.parse.quote(str(entry.resolve()))
        loc_html = f'<div class="size">{_html_text(loc)}/</div>' if loc else ""
        rows.append(
            f'<tr><td><a href="{_html_text(open_url)}" '
            f'title="Open in 3D viewer">{_html_text(entry.name)}</a> '
            f'<a href="{_html_text(view_url)}" title="File details" '
            f'style="opacity:0.6">ⓘ</a>'
            f"{loc_html}</td>"
            f"<td>{_html_text(calc)}</td><td></td><td>{n_sec}</td>"
            f'<td class="size">{size}</td>'
            f'<td class="size">{mtime_str}</td></tr>'
        )

    back = "/browse?dir=" + urllib.parse.quote(str(dir_path))
    nav = [f'<a href="{_html_text(back)}">📁 Folder view</a>']
    note = ""
    if truncated:
        note += (
            f'<tr><td colspan="6" class="size">Partial result: the scan '
            f"stopped at {_RECURSIVE_LIMIT} files or "
            f"{_RECURSIVE_TIME_BUDGET_S:.0f} s — browse into a more "
            f"specific folder and try again.</td></tr>"
        )
    if meta_skipped:
        note += (
            f'<tr><td colspan="6" class="size">Job names were read for the '
            f"{meta_read} newest files only, to keep this page fast.</td></tr>"
        )
    return _DIR_TEMPLATE.format(
        title=_html_text(f"All QVFs under {dir_path.name or dir_path}"),
        path_display=_html_text(f"{dir_path} (recursive, newest first)"),
        welcome="",
        parent_link=" &nbsp;·&nbsp; ".join(nav),
        rows=("\n".join(rows) + note)
        if rows
        else '<tr><td colspan="6">No .qvf files found anywhere below here.</td></tr>',
        version=_html_text(__version__),
        n_files=len(rows),
    )


def _human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def _browse_dir(dir_path: Path, rel_path: str = "", show_welcome: bool | None = None) -> str:
    """Generate an HTML directory listing for QVF files.

    Directory links use the absolute ``/browse?dir=…`` form, so the
    user can walk anywhere on the filesystem (parent + Home links
    included) — the launch directory is just the starting point, not a
    jail. ``rel_path`` is kept for backwards compatibility with the old
    ``/browse/<sub>`` route and only affects the welcome-card default.
    """
    from vibeview import __version__
    from vibeview.qvf import QVFError, QVFReader

    dir_path = dir_path.resolve()
    rows = []
    n_files = 0
    try:
        entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        entries = []

    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            sub_url = "/browse?dir=" + urllib.parse.quote(str(entry))
            rows.append(
                f'<tr><td><a href="{_html_text(sub_url)}">'
                f"📁 {_html_text(entry.name)}/</a></td>"
                f'<td></td><td></td><td></td><td class="size"></td></tr>'
            )
        elif entry.suffix == ".qvf":
            n_files += 1
            try:
                reader = QVFReader(entry)
                src = reader.manifest.source
                prov = getattr(reader.manifest, "provenance", None) or {}
                if hasattr(prov, "model_dump"):
                    prov = prov.model_dump()
                energy = prov.get("scf_energy", {}) if isinstance(prov, dict) else {}
                if isinstance(energy, dict):
                    energy = energy.get("value")
                e_str = f"{float(energy):.8f}" if energy else "--"
                conv = prov.get("scf_converged") if isinstance(prov, dict) else None
                conv_cls = "ok" if conv else ("err" if conv is False else "")
                n_sec = len(reader.sections)
                calc = f"{src.calculation}"
                mtime = entry.stat().st_mtime
                from datetime import datetime

                mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                open_url = "/open?file=" + urllib.parse.quote(str(entry.resolve()))
                view_url = "/view?file=" + urllib.parse.quote(str(entry.resolve()))
                rows.append(
                    f'<tr><td><a href="{_html_text(open_url)}" class="{conv_cls}" '
                    f'title="Open in 3D viewer">{_html_text(entry.name)}</a> '
                    f'<a href="{_html_text(view_url)}" title="File details" '
                    f'style="opacity:0.6">ⓘ</a></td>'
                    f"<td>{_html_text(calc)}</td><td>{e_str}</td><td>{n_sec}</td>"
                    f'<td class="size">{_human_size(entry.stat().st_size)}</td>'
                    f'<td class="size">{mtime_str}</td></tr>'
                )
                reader.close()
            except (QVFError, Exception):
                rows.append(
                    f"<tr><td>{_html_text(entry.name)}</td>"
                    f'<td colspan="5" class="err">Unreadable</td></tr>'
                )

    nav = []
    if dir_path.parent != dir_path:
        up = "/browse?dir=" + urllib.parse.quote(str(dir_path.parent))
        nav.append(f'<a href="{_html_text(up)}">⬆ Parent directory</a>')
    home = "/browse?dir=" + urllib.parse.quote(str(Path.home()))
    nav.append(f'<a href="{_html_text(home)}">⌂ Home</a>')
    new_url = "/new?dir=" + urllib.parse.quote(str(dir_path))
    if _dir_writable(dir_path):
        nav.append(
            f'<a href="{_html_text(new_url)}" '
            f'title="Create an editable scratch structure '
            f'in this folder and open it">➕ New structure</a>'
        )
    else:
        nav.append(
            '<span class="dim" title="This folder is read-only; open a writable '
            'folder to create a new structure">➕ New structure (read-only)</span>'
        )
    rec_url = "/browse?dir=" + urllib.parse.quote(str(dir_path)) + "&recursive=1"
    nav.append(
        f'<a href="{_html_text(rec_url)}" '
        f'title="Every .qvf in this folder and all '
        f'subfolders, newest first">🔍 All QVFs below here</a>'
    )
    parent_link = " &nbsp;·&nbsp; ".join(nav)

    if show_welcome is None:
        show_welcome = not rel_path
    return _DIR_TEMPLATE.format(
        title=_html_text(dir_path.name or "QVFs"),
        path_display=_html_text(dir_path),
        # Quickstart panel on the landing page (no file loaded yet);
        # navigated pages skip it to keep the listing compact.
        welcome=_WELCOME_CARD if show_welcome else "",
        parent_link=parent_link,
        rows="\n".join(rows) if rows else '<tr><td colspan="5">No .qvf files found.</td></tr>',
        version=_html_text(__version__),
        n_files=n_files,
    )


def _view_page(qvf_rel: str, dir_path: Path) -> str:
    """Return an HTML page with QVF metadata and download link."""
    from vibeview.qvf import QVFError, QVFReader

    qvf_path = dir_path / qvf_rel.lstrip("/")
    if not qvf_path.exists():
        return "<html><body><h1>File not found</h1></body></html>"

    try:
        reader = QVFReader(qvf_path)
        src = reader.manifest.source
        program = _html_text(f"{src.program} {src.version}")
        calc = _html_text(src.calculation)
        prov = getattr(reader.manifest, "provenance", None) or {}
        if hasattr(prov, "model_dump"):
            prov = prov.model_dump()
        if not isinstance(prov, dict):
            prov = {}
        energy = prov.get("scf_energy", {})
        if isinstance(energy, dict):
            energy = energy.get("value")
        e_str = f"{float(energy):.8f} Eh" if energy else "N/A"
        conv = (
            "Yes"
            if prov.get("scf_converged")
            else ("No" if prov.get("scf_converged") is False else "Unknown")
        )
        sec_rows = "".join(
            f"<tr><td>{_html_text(s.id)}</td><td>{_html_text(s.kind)}</td></tr>"
            for s in reader.sections
        )
        n_sec = len(reader.sections)
        size = _human_size(qvf_path.stat().st_size)
        reader.close()
    except (QVFError, Exception):
        program, calc, e_str, conv, sec_rows, n_sec, size = (
            "?",
            "?",
            "N/A",
            "Error",
            "",
            0,
            _human_size(qvf_path.stat().st_size),
        )
    conv_class = "ok" if conv == "Yes" else ("err" if conv == "No" else "")
    resolved = urllib.parse.quote(str(qvf_path.resolve()))
    open_href = _html_text(f"/open?file={resolved}")
    download_href = _html_text(f"/serve-qvf?file={resolved}")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>vibe-view — {_html_text(qvf_rel)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #1a1a2e; color: #e0e0e0; padding: 24px; }}
  .bar {{ margin-bottom: 20px; }} .bar a {{ color: #7c8aff; }}
  h1 {{ font-size: 1.3em; color: #7c8aff; }}
  .meta {{ background: #2a2a4e; border-radius: 8px; padding: 16px; margin: 12px 0; }}
  .meta dt {{ color: #888; font-size: 0.85em; }} .meta dd {{ margin: 4px 0 12px 0; }}
  table {{ width: 100%%; border-collapse: collapse; margin-top: 16px; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #2a2a4e; }}
  th {{ color: #7c8aff; font-size: 0.85em; }}
  .ok {{ color: #4caf50; }} .err {{ color: #f44336; }}
  .btn {{ display: inline-block; background: #7c8aff; color: #fff; padding: 8px 16px;
          border-radius: 6px; text-decoration: none; margin-right: 8px; }}
</style>
</head>
<body>
<div class="bar"><a href="/browse">← Browse</a></div>
<h1>{_html_text(qvf_path.name)}</h1>
<div class="meta">
  <dl>
    <dt>Program</dt><dd>{program}</dd>
    <dt>Calculation</dt><dd>{calc}</dd>
    <dt>Energy</dt><dd>{e_str}</dd>
    <dt>Converged</dt><dd class="{conv_class}">{conv}</dd>
    <dt>Sections</dt><dd>{n_sec}</dd>
    <dt>Size</dt><dd>{size}</dd>
  </dl>
</div>
<a class="btn" href="{open_href}">🔬 Open in 3D viewer</a>
<a class="btn" href="{download_href}" download>⬇ Download .qvf</a>
<h2>Sections</h2>
<table>
<tr><th>ID</th><th>Kind</th></tr>
{sec_rows or '<tr><td colspan="2">No sections</td></tr>'}
</table>
</body>
</html>"""


# ── Per-file 3D viewer processes ───────────────────────────────────────
#
# The directory browser itself is a plain HTTP server; the interactive
# 3D viewer is the Trame app behind ``vibe-view open``, which needs a
# loaded file (create_app requires at least one QVFReader).  So each
# ``?file=`` request gets its own viewer process on its own port; this
# registry reuses live viewers and reaps dead ones.

_viewers: dict[str, dict] = {}  # resolved path → {"port": int, "proc": Popen}
_viewers_lock = threading.Lock()


def _port_responding(port: int, host: str = "127.0.0.1") -> bool:
    """True once something accepts TCP connections on (host, port)."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _find_free_port(start: int, host: str = "127.0.0.1") -> int:
    """First bindable port at or above ``start``."""
    for port in range(start, start + 200):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, port))
            return port
        except OSError:
            continue
    raise OSError(f"No free port in range {start}-{start + 199}")


def _openable(path: Path) -> bool:
    """True if ``vibe-view open`` can load this file (QVF or convertible)."""
    from vibeview.converters import detect_format

    return path.is_file() and detect_format(path) is not None


def _ensure_viewer(file_path: Path, base_port: int, host: str = "127.0.0.1") -> int:
    """Return the port of a live viewer for ``file_path``, spawning one if needed.

    The viewer is ``vibe-view open FILE --no-browser`` in a subprocess —
    the same code path as the CLI, so format auto-conversion (.xyz, .cif,
    vibe-qc .py inputs, …) and recent-files tracking behave identically.
    """
    key = str(file_path.resolve())
    with _viewers_lock:
        entry = _viewers.get(key)
        if entry is not None:
            if entry["proc"].poll() is None:
                return entry["port"]
            del _viewers[key]  # viewer died — respawn below

        port = _find_free_port(base_port + 1, host)
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "vibeview.cli",
                "open",
                key,
                "--port",
                str(port),
                "--no-browser",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _viewers[key] = {"port": port, "proc": proc}
        return port


def _shutdown_viewers() -> None:
    """Terminate all spawned viewer processes (called on server shutdown)."""
    with _viewers_lock:
        for entry in _viewers.values():
            if entry["proc"].poll() is None:
                entry["proc"].terminate()
        _viewers.clear()


_LOADING_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>vibe-view — loading {name}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #1a1a2e; color: #e0e0e0; display: flex; align-items: center;
         justify-content: center; height: 100vh; margin: 0; flex-direction: column; }}
  .spinner {{ width: 48px; height: 48px; border: 4px solid #2a2a4e;
             border-top-color: #7c8aff; border-radius: 50%;
             animation: spin 0.9s linear infinite; margin-bottom: 24px; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  h1 {{ font-size: 1.1em; font-weight: 500; color: #7c8aff; }}
  .hint {{ color: #888; font-size: 0.85em; margin-top: 8px; }}
</style>
</head>
<body>
<div class="spinner"></div>
<h1>Opening {name}…</h1>
<div class="hint">Starting the 3D viewer — the first open can take up to a minute while VTK loads</div>
<script>
  const poll = () => fetch({status_url})
    .then(r => r.json())
    .then(d => {{ if (d.ready) window.location = d.url; else setTimeout(poll, 500); }})
    .catch(() => setTimeout(poll, 500));
  poll();
</script>
</body>
</html>"""


def _loading_page(name: str, status_url: str) -> str:
    """Render the cold-start page with HTML and script contexts isolated."""
    return _LOADING_TEMPLATE.format(
        name=_html_text(name),
        status_url=_script_string(status_url),
    )


def serve_directory(dir_path: Path, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Start an HTTP server that browses and serves QVF files.

    The server provides:
      - ``/browse`` or ``/browse/<subpath>`` — directory listing
      - ``/serve-qvf/<path>`` — serve the raw .qvf file
      - ``/view/<path>`` — viewing page for a QVF
      - ``/open?file=<abs-path>`` — spawn/reuse a 3D viewer, loading page
      - ``/open-status?file=<abs-path>`` — JSON readiness poll
    """
    from vibeview import __version__

    dir_path = dir_path.resolve()
    base_port = port

    class QVFHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dir_path), **kwargs)

        def _file_query(self, query: str) -> Path | None:
            """Extract + validate the ``file=`` param. None if unusable."""
            params = urllib.parse.parse_qs(query)
            values = params.get("file")
            if not values:
                return None
            candidate = Path(values[0]).expanduser()
            if not candidate.is_absolute():
                candidate = dir_path / candidate
            return candidate if _openable(candidate) else None

        def _handle_open(self, query: str) -> None:
            """Spawn/reuse a viewer; redirect if ready, else loading page."""
            target = self._file_query(query)
            if target is None:
                self.send_error(404, "file= parameter missing or not an openable file")
                return
            viewer_port = _ensure_viewer(target, base_port)
            url = f"http://127.0.0.1:{viewer_port}"
            if _port_responding(viewer_port):
                self.send_response(302)
                self.send_header("Location", url)
                self.end_headers()
                return
            status_url = "/open-status?file=" + urllib.parse.quote(str(target.resolve()))
            self._serve_html(_loading_page(target.name, status_url))

        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parsed.query

            params = urllib.parse.parse_qs(query)

            if path == "/" and "file" in params:
                # Electron drag-drop / dock-drop navigates here.
                self._handle_open(query)
            elif (path == "/" or path == "/browse") and "dir" in params:
                # Absolute-path navigation (parent links, Open Folder…).
                target = Path(params["dir"][0]).expanduser()
                if target.is_dir():
                    if params.get("recursive"):
                        self._serve_html(_browse_recursive(target))
                    else:
                        self._serve_html(_browse_dir(target, show_welcome=False))
                else:
                    self.send_error(404, "dir= parameter is not a directory")
            elif path == "/" or path == "/browse":
                if params.get("recursive"):
                    self._serve_html(_browse_recursive(dir_path))
                else:
                    self._serve_html(_browse_dir(dir_path))
            elif path == "/open":
                self._handle_open(query)
            elif path == "/new":
                # Create a scratch structure and open it in the viewer —
                # the from-scratch input-building entry point.
                target_dir = dir_path
                if "dir" in params:
                    candidate = Path(params["dir"][0]).expanduser()
                    if candidate.is_dir():
                        target_dir = candidate
                if not _dir_writable(target_dir):
                    # ``send_error`` copies its message into the HTTP status
                    # line.  A POSIX filename may contain CR/LF, so never put
                    # a filesystem value there.
                    self.send_error(403, "folder is read-only")
                    return
                try:
                    new_file = _create_new_qvf(target_dir)
                except OSError:
                    self.send_error(500, "could not create a new structure here")
                    return
                self.send_response(302)
                self.send_header(
                    "Location",
                    "/open?file=" + urllib.parse.quote(str(new_file.resolve())),
                )
                self.end_headers()
            elif path == "/view" and "file" in params:
                target = Path(params["file"][0]).expanduser()
                if target.is_file():
                    self._serve_html(_view_page(target.name, target.parent))
                else:
                    self.send_error(404)
            elif path == "/serve-qvf" and "file" in params:
                # Raw download by absolute path (restricted to .qvf).
                target = Path(params["file"][0]).expanduser()
                if target.is_file() and target.suffix == ".qvf":
                    data = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header(
                        "Content-Disposition", _attachment_disposition(target.name)
                    )
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_error(404)
            elif path == "/open-status":
                target = self._file_query(query)
                if target is None:
                    self._serve_json({"ready": False, "error": "unknown file"})
                    return
                viewer_port = _ensure_viewer(target, base_port)
                self._serve_json(
                    {
                        "ready": _port_responding(viewer_port),
                        "url": f"http://127.0.0.1:{viewer_port}",
                    }
                )
            elif path.startswith("/browse/"):
                sub = urllib.parse.unquote(path[len("/browse/") :])
                target = dir_path / sub
                if target.is_dir():
                    self._serve_html(_browse_dir(target, sub))
                else:
                    self.send_error(404)
            elif path.startswith("/view/"):
                qvf_rel = urllib.parse.unquote(path[len("/view/") :])
                self._serve_html(_view_page(qvf_rel, dir_path))
            elif path.startswith("/serve-qvf/"):
                # Serve raw QVF file
                self.path = path[len("/serve-qvf") :]
                super().do_GET()
            elif path == "/api/version":
                self._serve_json({"version": __version__})
            else:
                super().do_GET()

        def _serve_html(self, html: str) -> None:
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve_json(self, obj: dict) -> None:
            data = json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass  # Suppress access logs

    # The Electron shell stops us with SIGTERM on quit; route that into
    # the KeyboardInterrupt path so the viewer children get reaped too.
    import signal

    def _terminate(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _terminate)
    except ValueError:
        pass  # not the main thread (e.g. tests) — skip

    # Threading server: /open requests spawn viewer subprocesses and the
    # loading page polls /open-status concurrently — a single-threaded
    # server would deadlock the poll behind the slow first request.
    server = http.server.ThreadingHTTPServer((host, port), QVFHandler)
    url = f"http://{host}:{port}"
    print(f"vibe-view serve: {dir_path}")
    print(f"  Browse: {url}")
    print(f"  Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
    finally:
        _shutdown_viewers()
