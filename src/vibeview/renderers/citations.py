"""Citations renderer — render the embedded BibTeX bibliography as a panel.

The QVF `citations` section carries a BibTeX file as utf-8 bytes
(design § 1.4). We pull the text out and render it as a scrollable
<pre> in HTML. No formatting beyond escaping — BibTeX entries are
human-readable as-is and the user can copy them straight into a
manuscript.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import CitationsData, QVFReader, Section


class CitationsRenderer(BaseRenderer):
    """Embedded-BibTeX bibliography panel."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: CitationsData | None = None

    def load(self) -> CitationsData:
        if self._data is None:
            self._data = self.reader.read_citations(self.section_id)
        return self._data

    def render_to_html(self) -> str:
        """Render the BibTeX as a self-contained scrollable HTML document.

        Hosted in the bottom-panel iframe (srcdoc, sandbox=allow-scripts).
        The body scrolls internally; the BibTeX <pre> wraps long lines so
        nothing overflows when the viewer window is narrow.
        """
        data = self.load()
        n_entries = _count_bib_entries(data.bibtex)
        body = html.escape(data.bibtex)
        plural = "y" if n_entries == 1 else "ies"
        return (
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            "html,body{margin:0;padding:0;height:100%;"
            "font-family:system-ui,sans-serif;}"
            "body{display:flex;flex-direction:column;}"
            ".count{padding:6px 10px;font-size:0.9em;opacity:0.7;"
            "border-bottom:1px solid #ddd;}"
            "pre{flex:1 1 auto;overflow:auto;margin:0;padding:8px 12px;"
            "font-family:ui-monospace,Menlo,monospace;font-size:12px;"
            "line-height:1.4;white-space:pre-wrap;word-break:break-word;}"
            "</style></head><body>"
            f"<div class='count'>{n_entries} bibliography entr{plural}</div>"
            f"<pre>{body}</pre>"
            "</body></html>"
        )


def _count_bib_entries(bibtex: str) -> int:
    """Cheap BibTeX entry count — looks for top-level `@kind{...,`
    openings. Not a full parser; comments containing `@` would
    over-count but the writer doesn't emit those."""
    count = 0
    for line in bibtex.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("@") and "{" in stripped:
            count += 1
    return count
