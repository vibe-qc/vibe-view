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

import html
import json
from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import NMRData, QVFReader, Section


def _kv_strip(items: dict[str, object]) -> str:
    """Render a small key=value strip for top metadata."""
    parts = [
        f'<span style="opacity: 0.7;">{html.escape(str(k))}:</span> '
        f"<strong>{html.escape(str(v))}</strong>"
        for k, v in items.items()
        if v is not None
    ]
    if not parts:
        return ""
    return (
        '<div style="padding: 6px 12px; opacity: 0.9; font-size: 0.92em;">'
        + " &nbsp;·&nbsp; ".join(parts)
        + "</div>"
    )


def _table(rows: list[dict], columns: list[str], caption: str) -> str:
    """Render a list-of-dicts as an HTML table with the named columns.

    Cells default to "—" when a row lacks the column. Rows with no
    matching columns are skipped; if all rows are skipped, returns ""
    (so the caller can omit the section)."""
    body_rows: list[str] = []
    for row in rows:
        if not any(c in row for c in columns):
            continue
        cells = []
        for c in columns:
            v = row.get(c, "—")
            if isinstance(v, float):
                v = f"{v:.4f}"
            cells.append(
                f'<td style="padding: 3px 12px 3px 0;">{html.escape(str(v))}</td>'
            )
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    if not body_rows:
        return ""
    header_cells = "".join(
        f'<th style="padding: 4px 12px 4px 0; text-align: left; '
        f'border-bottom: 1px solid currentColor;">{html.escape(c)}</th>'
        for c in columns
    )
    return (
        f'<div style="padding: 0 12px 8px 12px;">'
        f'<div style="font-weight: 600; padding: 8px 0 4px;">{html.escape(caption)}</div>'
        f'<table style="border-collapse: collapse; font-size: 0.88em;">'
        f"<thead><tr>{header_cells}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        f"</table>"
        f"</div>"
    )


def _shift_columns(shifts: list[dict]) -> list[str]:
    """Pick a sensible column order for chemical shifts: the
    conventional keys first, then any extras in encounter order."""
    preferred = [
        "atom_index", "symbol", "element",
        "isotropic_shift_ppm", "isotropic_ppm", "shift_ppm",
        "anisotropy_ppm", "asymmetry",
    ]
    seen: set[str] = set()
    cols: list[str] = []
    for k in preferred:
        if any(k in row for row in shifts):
            cols.append(k)
            seen.add(k)
    for row in shifts:
        for k in row:
            if k not in seen:
                cols.append(k)
                seen.add(k)
    return cols


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
        meta = _kv_strip(
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
            shifts_html = _table(shifts, _shift_columns(shifts), "Chemical shifts")
        else:
            shifts_html = ""

        # J-couplings table
        jc = data.get("j_couplings")
        if isinstance(jc, list) and jc and all(isinstance(r, dict) for r in jc):
            jc_cols = ["i", "j", "atom_i", "atom_j", "j_hz", "coupling_hz", "value_hz"]
            jc_cols = [
                c for c in jc_cols if any(c in row for row in jc)
            ] or list(jc[0].keys())
            jc_html = _table(jc, jc_cols, "J-couplings")
        else:
            jc_html = ""

        # Shielding tensors — collapsed summary
        tensors = data.get("shielding_tensors")
        tensors_html = ""
        if isinstance(tensors, list | dict) and tensors:
            n = len(tensors) if isinstance(tensors, list) else len(tensors)
            tensors_html = (
                f'<div style="padding: 0 12px 8px 12px;">'
                f'<div style="font-weight: 600; padding: 8px 0 4px;">'
                f"Shielding tensors</div>"
                f'<details style="font-size: 0.88em;"><summary>{n} '
                f'tensor{"s" if n != 1 else ""} recorded — show raw JSON</summary>'
                f'<pre style="max-height: 220px; overflow: auto; '
                f'font-family: monospace; font-size: 0.9em;">'
                f"{html.escape(json.dumps(tensors, indent=2))}"
                f"</pre></details></div>"
            )

        body = meta + shifts_html + jc_html + tensors_html
        if not body:
            return (
                '<div style="padding: 16px;">'
                "spectra.nmr section is empty (no chemical_shifts, j_couplings "
                "or shielding_tensors recorded)."
                "</div>"
            )
        return (
            '<div style="max-height: 480px; overflow: auto;">' + body + "</div>"
        )
