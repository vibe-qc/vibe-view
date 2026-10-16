"""preview renderer — the picture an archive carries of itself.

A `preview` section (QVF spec § 5.15) holds a raster `thumbnail` and an
optional larger or vector `image`, so a file manager can show the archive
without understanding it. The picture describes the archive and is not
data: vibe-view never draws it in the 3D scene and reads no chemistry from
it. The panel shows the raster thumbnail as an image whose type the
manifest declares, and lists the members. An SVG `image` is listed, not
rendered.
"""

from __future__ import annotations

import base64
import html
from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer
from vibeview.renderers._html import heading, kv_table, note, panel_document

if TYPE_CHECKING:
    from vibeview.qvf import PreviewImage, QVFReader, Section


class PreviewRenderer(BaseRenderer):
    """Thumbnail panel for a preview section."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: PreviewImage | None = None

    def load(self) -> PreviewImage | None:
        """The verified thumbnail, or None when it cannot be shown."""
        if self._data is None:
            self._data = self.reader.read_preview(self.section_id, "thumbnail")
        return self._data

    def render_to_html(self) -> str:
        from vibeview.qvf import PREVIEW_RASTER_TYPES

        thumbnail = self.load()
        rows: list[tuple[str, object]] = []
        for role, member in sorted(self.section.members.items()):
            size = self.reader.stored_size(member)
            rows.append((role, f"{member.media_type or 'no media_type'}, {size or 0} bytes"))
        parts = [
            heading("Preview"),
            note("A picture of the archive's contents; it is not drawn in the 3D view."),
        ]
        if thumbnail is not None and thumbnail.media_type in PREVIEW_RASTER_TYPES:
            encoded = base64.b64encode(thumbnail.data).decode("ascii")
            parts.append(
                "<p><img alt='archive preview' style='max-width:100%;height:auto;' "
                f"src='data:{html.escape(thumbnail.media_type, quote=True)};base64,{encoded}'>"
                "</p>"
            )
        else:
            parts.append(note("The thumbnail cannot be shown (missing, too large or untyped)."))
        parts.append(kv_table(rows))
        return panel_document("".join(parts))
