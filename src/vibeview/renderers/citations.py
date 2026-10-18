"""Citations renderer — render the embedded BibTeX bibliography as a panel.

The QVF `citations` section carries a BibTeX file as utf-8 bytes
(design § 1.4). We pull the text out and render it as a scrollable
<pre> in HTML. No formatting beyond escaping — BibTeX entries are
human-readable as-is and the user can copy them straight into a
manuscript.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer
from vibeview.renderers._html import note, panel_document, preformatted

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
        plural = "y" if n_entries == 1 else "ies"
        return panel_document(
            note(f"{n_entries} bibliography entr{plural}") + preformatted(data.bibtex),
            # The bibliography is the whole panel: let it use the height.
            extra_css="pre{max-height:none;}",
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
