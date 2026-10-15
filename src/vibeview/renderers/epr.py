"""spectra.epr renderer — g-tensor / hyperfine / zero-field-splitting panel.

EPR data is shape-different from the frequency/intensity spectra kinds
(like spectra.nmr): the producer supplies EPR parameters as an opaque dict.
The renderer surfaces whichever subset was provided:

* g-tensor strip — isotropic g and principal values, plus the raw matrix in a
  collapsed details view.
* Hyperfine (A) table — one row per nucleus with whatever fields the producer
  included (atom_index, symbol, isotope, a_iso_mhz, …).
* Zero-field splitting — D and E (MHz).

The producer dict is opaque per the writer's docstring; renders gracefully when
a field is absent, never raises on a key miss. EPR parameter conventions:
g-values dimensionless, hyperfine and zero-field-splitting in MHz.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer
from vibeview.renderers._html import (
    column_order,
    data_table,
    details_block,
    heading,
    kv_strip,
    panel_document,
    preformatted,
)

if TYPE_CHECKING:
    from vibeview.qvf import EPRData, QVFReader, Section


def _fmt(v: object) -> str:
    if isinstance(v, float):
        return f"{v:.4f}"
    if isinstance(v, list):
        return ", ".join(_fmt(x) for x in v)
    return str(v)


_HYPERFINE_COLUMNS = (
    "atom_index", "symbol", "element", "isotope",
    "a_iso_mhz", "a_iso", "a_tensor_mhz",
)


class EPRRenderer(BaseRenderer):
    """spectra.epr panel — g-tensor + hyperfine + zero-field-splitting."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: EPRData | None = None

    def load(self) -> EPRData:
        if self._data is None:
            self._data = self.reader.read_epr(self.section_id)
        return self._data

    def render_to_html(self) -> str:
        data = self.load().raw

        # g-tensor strip
        g = data.get("g_tensor")
        g_html = ""
        if isinstance(g, dict):
            principal = g.get("principal")
            g_html = kv_strip(
                {
                    "g_iso": g.get("isotropic"),
                    "g_principal": (
                        _fmt(principal) if isinstance(principal, list) else None
                    ),
                }
            )
        elif isinstance(g, list):
            g_html = kv_strip({"g_principal": _fmt(g)})

        # Hyperfine table
        hf = data.get("hyperfine")
        hf_html = ""
        if isinstance(hf, list) and hf and all(isinstance(r, dict) for r in hf):
            columns = column_order(hf, _HYPERFINE_COLUMNS)
            hf_html = data_table(columns, hf, caption="Hyperfine (A) tensors")

        # Zero-field splitting
        zfs = data.get("zero_field_splitting")
        zfs_html = ""
        if isinstance(zfs, dict):
            zfs_html = kv_strip(
                {"D (MHz)": zfs.get("d_mhz"), "E (MHz)": zfs.get("e_mhz")}
            )

        # Raw JSON fallback for full tensors
        raw_html = details_block("Show raw EPR JSON", preformatted(json.dumps(data, indent=2)))

        body = g_html + hf_html + zfs_html
        if not body:
            return panel_document(
                "<p class='empty'>spectra.epr section is empty (no g_tensor, hyperfine or "
                "zero_field_splitting recorded).</p>" + raw_html
            )
        return panel_document(heading("g-tensor") + body + raw_html)
