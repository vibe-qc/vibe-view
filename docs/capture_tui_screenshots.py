#!/usr/bin/env python
"""Regenerate the terminal-mode documentation screenshots.

Companion to ``capture_screenshots.py``, which drives the *browser* viewer
through Playwright. Terminal mode has no browser, so its captures come from
the two renderers themselves:

  * ``vibe-view tui`` frames via Textual's own ``export_screenshot()``, run
    under ``App.run_test()`` at a fixed size. That draws the real widget
    tree -- sidebar, viewport, status line, footer -- exactly as a user
    sees it.
  * ``vibe-view show`` frames via Rich's ``Console.export_svg()`` over the
    ANSI the command actually prints.

Output is **SVG**, not PNG, for three reasons: the content is text, so it
stays crisp at any zoom and readable to screen readers; the files are a few
tens of kB instead of a few hundred; and a reader can select and copy the
commands out of the figure. ``docs/_static/lattices/*.svg`` already
establishes SVG figures in this docs set.

This is a dev/docs tool, never imported by ``vibeview`` and never run in CI.
It needs the ``[tui]`` extra (Textual) and the project-authored showcase archives.
Regenerate everything with::

    VIBE_VIEW_EXAMPLES=/path/to/vibe-qc/examples/vibe_view \
        PYVISTA_OFF_SCREEN=True python docs/capture_tui_screenshots.py

The showcase archives carry a sanitized provenance host (``<host>``); the
captures inherit it, so nothing here leaks a real machine name.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
OUT = REPO / "docs" / "images"
WATER: Path
H2CO: Path

# One terminal size for every capture, so the figures sit at a consistent
# scale down the page. 100x30 is a comfortable real-world window and keeps
# the SVG narrow enough to read at docs width.
SIZE = (100, 30)


# Both exporters embed an @font-face whose `src` pulls Fira Code from
# cdnjs.cloudflare.com. A committed docs asset must not fetch from a
# third-party CDN when the page renders: it leaks a request per reader,
# breaks an offline or air-gapped docs build, and adds a supply-chain edge
# for a *font*. The rule keeps `local()` so anyone who has Fira Code
# installed still gets it, and the existing `Fira Code, monospace` fallback
# covers everyone else.
_REMOTE_FONT_SRC = re.compile(
    r"""(src:\s*local\([^)]*\))(\s*,\s*url\("https?://[^"]+"\)\s*format\("[^"]+"\))+""",
    re.VERBOSE,
)


def _strip_remote_fonts(svg: str) -> str:
    return _REMOTE_FONT_SRC.sub(r"\1", svg)


# Only `url(http...)` is a fetch. `xmlns="http://www.w3.org/2000/svg"` is a
# namespace identifier that is never dereferenced, so the guard must not
# trip on it.
_REMOTE_FETCH = re.compile(r"""url\(\s*["']?https?://""")


def _write(name: str, svg: str) -> None:
    svg = "\n".join(line.rstrip() for line in _strip_remote_fonts(svg).splitlines()) + "\n"
    if _REMOTE_FETCH.search(svg):
        raise SystemExit(f"{name}: a remote fetch survived the font strip")
    path = OUT / name
    path.write_text(svg, encoding="utf-8")
    print(f"  {path.relative_to(REPO)}  ({len(svg):,} bytes)")


# ── the interactive viewer ────────────────────────────────────────────────


async def _tui_frame(
    qvf: Path,
    title: str,
    *,
    kind: str | None = None,
    keys: tuple[str, ...] = (),
) -> str:
    """Drive the real app to a state and export that frame as SVG.

    ``kind`` selects the first section of that kind *by name* rather than by
    counting Tab presses. Counting is how an early draft of this script
    captured a "spectrum" figure that was actually showing bond_orders:
    section order varies per archive, so a fixed key count is not a
    selector.
    """
    from vibeview.tui.app import VibeViewTUI

    app = VibeViewTUI(qvf)
    async with app.run_test(size=SIZE) as pilot:
        if kind is not None:
            matches = [i for i, (_id, k, _s) in enumerate(app.entries) if k == kind]
            if not matches:
                raise SystemExit(f"{qvf.name} has no {kind!r} section to capture")
            app.selected = matches[0]
            app._on_selection_changed()
        for key in keys:
            await pilot.press(key)
        await pilot.pause()
        assert app.current[1] == (kind or app.current[1]), (
            f"expected a {kind} frame, got {app.current}"
        )
        return app.export_screenshot(title=title)


def capture_tui() -> None:
    print("vibe-view tui:")

    # Opens on the structure, which is what `vibe-view tui file.qvf` shows.
    _write(
        "tui-01-structure.svg",
        asyncio.run(_tui_frame(WATER, "vibe-view tui water.qvf", kind="structure")),
    )
    _write(
        "tui-02-orbital.svg",
        asyncio.run(
            _tui_frame(WATER, "Molecular orbital, both signed lobes", kind="volume.orbital")
        ),
    )
    # The geometry table over the structure ('t' toggles the data pane).
    _write(
        "tui-03-table.svg",
        asyncio.run(_tui_frame(WATER, "Geometry table (t)", kind="structure", keys=("t",))),
    )
    _write(
        "tui-04-spectrum.svg",
        asyncio.run(_tui_frame(H2CO, "IR spectrum, sticks and envelope", kind="spectra.ir")),
    )
    _write(
        "tui-05-scf.svg",
        asyncio.run(_tui_frame(H2CO, "SCF convergence on a log axis", kind="scf_history")),
    )


# ── the one-shot renderer ─────────────────────────────────────────────────


def capture_show(demo: Path) -> None:
    """`vibe-view show` output, captured from the ANSI it really prints."""
    from rich.console import Console

    from vibeview.tui import show as show_mod

    print("vibe-view show:")
    cases = [
        (
            "show-00-demo.svg",
            "vibe-view show vibe-view-demo.qvf",
            dict(section_id=None, representation="ball_and_stick", show_labels=True),
        ),
        (
            "show-01-structure.svg",
            "vibe-view show water.qvf",
            dict(section_id=None, representation="ball_and_stick", show_labels=True),
        ),
        (
            "show-02-orbital.svg",
            "vibe-view show water.qvf --section vol_mo_0 --isovalue 0.05",
            dict(section_id="vol_mo_0", representation="licorice", isovalue=0.05),
        ),
    ]
    import io

    from rich.text import Text

    for name, caption, kwargs in cases:
        ansi = show_mod.show(demo if name == "show-00-demo.svg" else WATER, size=SIZE, **kwargs)
        console = Console(record=True, width=SIZE[0], file=io.StringIO())
        # from_ansi, not a bare print: the renderer's output *is* SGR escape
        # sequences, and printing it as a plain string escapes them into
        # literal "[0m" text in the figure instead of colouring it.
        console.print(Text.from_ansi(ansi), markup=False, highlight=False)
        _write(name, console.export_svg(title=caption, clear=True))


def main() -> int:
    from capture_inputs import prepare_inputs

    global WATER, H2CO
    inputs = prepare_inputs()
    WATER, H2CO = inputs["water"], inputs["h2co"]
    missing = [p for p in (WATER, H2CO) if not p.exists()]
    if missing:
        print("missing showcase archives:", *(str(p) for p in missing), sep="\n  ")
        return 1
    try:
        import textual  # noqa: F401
    except ModuleNotFoundError:
        print("From the viewer checkout root, install the [tui] extra: "
              "python -m pip install -e '.[tui]'")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    capture_tui()
    capture_show(inputs["demo"])
    print("\nDone. Figures are saved to docs/images/ for the standalone manual.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
