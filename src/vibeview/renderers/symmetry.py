"""structure.symmetry renderer — spglib-style symmetry summary panel.

The producer's writer (qvf.py::_write_symmetry_section) passes
whatever symmetry dict the caller hands it through to JSON. Common
keys come from spglib (space_group_number, space_group_symbol,
international_symbol, hall_symbol, hall_number, point_group,
schoenflies, number_of_symmetry_operations, transformation_matrix,
origin_shift, …). We render whichever keys are present as a tidy
two-column table; producer-private internal keys (those starting
with ``_``) are skipped.

Long values (lists, matrices) are rendered as ``<pre>`` so a
3-row rotation matrix doesn't blow out the layout.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer
from vibeview.renderers._html import empty_panel, kv_table, panel_document

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, Section, SymmetryData


# Conventional spglib keys in display order. Anything else gets
# appended after these (alphabetically) so the renderer stays
# forward-compatible with new producer keys.
_KEY_ORDER = (
    "space_group_number",
    "space_group_symbol",
    "international_symbol",
    "hall_symbol",
    "hall_number",
    "point_group",
    "schoenflies",
    "crystal_system",
    "lattice_system",
    "number_of_symmetry_operations",
    "transformation_matrix",
    "origin_shift",
)

# Producer bookkeeping that we don't want to surface in the panel.
_SUPPRESSED_KEYS = frozenset({"kind", "version"})


def _format_value(v: object) -> tuple[str, bool]:
    """Render a single value. Returns (html, is_block) where
    ``is_block`` flags multi-line content (rendered inside ``<pre>``)."""
    if isinstance(v, list | tuple):
        # Multi-line for nested sequences (matrices); single-line for
        # flat lists.
        if v and isinstance(v[0], list | tuple):
            return json.dumps(v, indent=2), True
        return json.dumps(v), False
    if isinstance(v, dict):
        return json.dumps(v, indent=2), True
    return str(v), False


class SymmetryRenderer(BaseRenderer):
    """structure.symmetry panel — two-column key/value table."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: SymmetryData | None = None

    def load(self) -> SymmetryData:
        if self._data is None:
            self._data = self.reader.read_symmetry(self.section_id)
        return self._data

    def render_to_html(self) -> str:
        data = self.load()
        items = {
            k: v
            for k, v in data.raw.items()
            if k not in _SUPPRESSED_KEYS and not k.startswith("_")
        }
        if not items:
            return empty_panel("structure.symmetry section is empty.")

        # Order: conventional keys first (in _KEY_ORDER), then anything
        # else alphabetically.
        ordered: list[tuple[str, object]] = []
        for k in _KEY_ORDER:
            if k in items:
                ordered.append((k, items.pop(k)))
        for k in sorted(items):
            ordered.append((k, items[k]))

        # Multi-line values (matrices, nested dicts) render as <pre> blocks;
        # kv_table decides that from the text itself.
        rows = [(key, _format_value(value)[0]) for key, value in ordered]
        return panel_document(kv_table(rows))
