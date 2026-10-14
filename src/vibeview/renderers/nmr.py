"""spectra.nmr renderer — chemical-shift / J-coupling / shielding panel.

NMR data is shape-different from the other spectra kinds (no
frequency / intensity pair — instead per-atom chemical shifts and
optionally J-couplings + full shielding tensors). The renderer
surfaces whichever subset the producer supplied:

* Top metadata strip: isotope, reference compound, solvent.
* Chemical shifts table — one row per atom with whatever fields
  the producer included (atom_index, symbol, isotropic_shift_ppm,
  anisotropy_ppm, asymmetry, …). Defensive against missing keys.
* J-couplings table — one row per (i, j) pair.
* Shielding tensors — collapsed to "N tensors recorded" plus a
  download-style raw JSON view (tensors are 3×3 each — printing
  every component for every atom would dwarf the chemical-shifts
  panel that users actually care about).

The producer dict is opaque per the writer's docstring; renders
gracefully when a field is absent, never raises on a key miss.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer
from vibeview.renderers._html import (
    column_order,
    data_table,
    details_block,
    empty_panel,
    heading,
    kv_strip,
    panel_document,
    preformatted,
)

if TYPE_CHECKING:
    from vibeview.qvf import NMRData, QVFReader, Section


_SHIFT_COLUMNS = (
    "atom_index", "symbol", "element",
    "isotropic_shift_ppm", "isotropic_ppm", "shift_ppm",
    "anisotropy_ppm", "asymmetry",
)


class NMRRenderer(BaseRenderer):
    """spectra.nmr panel — metadata + chemical-shift table."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: NMRData | None = None

    def load(self) -> NMRData:
        if self._data is None:
            self._data = self.reader.read_nmr(self.section_id)
        return self._data

    def render_to_html(self) -> str:
        data = self.load().raw

        # Top metadata strip
        meta = kv_strip(
            {
                "Isotope": data.get("isotope"),
                "Reference": data.get("reference"),
                "Solvent": data.get("solvent"),
            }
        )

        # Chemical shifts table
        shifts = data.get("chemical_shifts")
        if isinstance(shifts, list) and shifts and all(
            isinstance(r, dict) for r in shifts
        ):
            columns = column_order(shifts, _SHIFT_COLUMNS)
            shifts_html = data_table(columns, shifts, caption="Chemical shifts")
        else:
            shifts_html = ""

        # J-couplings table
        jc = data.get("j_couplings")
        if isinstance(jc, list) and jc and all(isinstance(r, dict) for r in jc):
            jc_cols = ["i", "j", "atom_i", "atom_j", "j_hz", "coupling_hz", "value_hz"]
            jc_cols = [
                c for c in jc_cols if any(c in row for row in jc)
            ] or list(jc[0].keys())
            jc_html = data_table(jc_cols, jc, caption="J-couplings")
        else:
            jc_html = ""

        # Shielding tensors — collapsed summary
        tensors = data.get("shielding_tensors")
        tensors_html = ""
        if isinstance(tensors, list | dict) and tensors:
            n = len(tensors)
            tensors_html = heading("Shielding tensors") + details_block(
                f"{n} tensor{'s' if n != 1 else ''} recorded — show raw JSON",
                preformatted(json.dumps(tensors, indent=2)),
            )

        body = meta + shifts_html + jc_html + tensors_html
        if not body:
            return empty_panel(
                "spectra.nmr section is empty (no chemical_shifts, j_couplings "
                "or shielding_tensors recorded)."
            )
        return panel_document(body)
