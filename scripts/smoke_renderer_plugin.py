#!/usr/bin/env python3
"""Exercise the sample renderer plugin from a real installation (#113).

    python scripts/smoke_renderer_plugin.py \\
        --example-root docs/examples/renderer-plugin --source-root src

Run it with the interpreter of an environment where vibe-view and the sample
plugin (``docs/examples/renderer-plugin``) are installed as packages, from a
directory outside the checkout, so discovery goes through the installed
entry-point metadata. It checks, in order, that the entry point loads, that
``vibe-view info --json`` lists the plugin's kind as rendered via the plugin,
that the browser app lists the section with the plugin's title and icon and
fills the details panel with its table, and that the terminal falls back to
its generic pane. The candidate pipeline runs it in the installed-wheel venv.
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

KIND = "x_example.timings"
DISTRIBUTION = "vibeview-example-renderer"


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"renderer plugin smoke test failed: {message}")
    print(f"ok  {message}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--example-root",
        type=Path,
        required=True,
        help="docs/examples/renderer-plugin, for its make_archive.py",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="checkout src directory that imports must not resolve below",
    )
    return parser.parse_args(argv)


def _outside(module_file: str | None, root: Path) -> bool:
    if module_file is None:
        return False
    return not Path(module_file).resolve().is_relative_to(root.resolve())


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

    import vibeview_example_renderer  # type: ignore[import-not-found]

    import vibeview
    from vibeview.renderer_plugins import discover_renderers

    _check(_outside(vibeview.__file__, args.source_root), "vibeview is the installed package")
    _check(
        _outside(vibeview_example_renderer.__file__, args.example_root),
        "the sample plugin is the installed package",
    )

    statuses = {status.entry_point: status for status in discover_renderers()}
    status = statuses.get(KIND)
    _check(status is not None and status.available, f"entry point {KIND!r} loads")
    assert status is not None
    _check(status.distribution == DISTRIBUTION, f"it comes from {DISTRIBUTION}")

    with tempfile.TemporaryDirectory() as tmp:
        build = runpy.run_path(str(args.example_root / "make_archive.py"))["build"]
        archive = Path(build(Path(tmp) / "timings.qvf"))
        _check_info(archive)
        _check_browser(archive)
        _check_terminal(archive)
    return 0


def _check_info(archive: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "vibeview", "info", "--json", str(archive)],
        check=False,
        capture_output=True,
        text=True,
    )
    _check(proc.returncode == 0, "vibe-view info --json exits 0")
    rows = {row["id"]: row for row in json.loads(proc.stdout)["sections"]}
    timings = rows.get("timings", {})
    _check(
        (timings.get("status"), timings.get("status_detail")) == ("rendered", "via plugin"),
        "vibe-view info lists the plugin kind as rendered via plugin",
    )


def _check_browser(archive: Path) -> None:
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    app: Any = create_app(QVFReader(archive))
    entry = next((e for e in app.state.sidebar_entries if e.get("id") == "timings"), None)
    _check(entry is not None, "the browser lists the plugin section")
    assert entry is not None
    _check(entry["title"] == "Step Timings", "with the plugin's title")
    _check(entry["kind_icon"] == "mdi-timer-outline", "and the plugin's icon")
    app.controller.activate_section("timings")
    html = str(app.state.properties_html or "")
    _check(
        app.state.properties_title == "Step Timings" and "<table>" in html
        and "SCF iterations" in html,
        "opening it fills the details panel with the plugin's table",
    )


def _check_terminal(archive: Path) -> None:
    from vibeview.tui.app import VibeViewTUI

    app = VibeViewTUI(archive)
    try:
        app.selected = next(i for i, entry in enumerate(app.entries) if entry[0] == "timings")
        text = app._text_for_current()
    finally:
        app.reader.close()
    _check(
        KIND in text and "rendered (via plugin)" in text,
        "the terminal falls back to its generic pane",
    )


if __name__ == "__main__":
    sys.exit(main())
