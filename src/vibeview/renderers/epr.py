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

import html
import json
from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import EPRData, QVFReader, Section


def _kv_strip(items: dict[str, object]) -> str:
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


def _fmt(v: object) -> str:
    if isinstance(v, float):
        return f"{v:.4f}"
    if isinstance(v, list):
        return ", ".join(_fmt(x) for x in v)
    return str(v)


def _table(rows: list[dict], columns: list[str], caption: str) -> str:
    body_rows: list[str] = []
    for row in rows:
        if not any(c in row for c in columns):
            continue
        cells = [
            f'<td style="padding: 3px 12px 3px 0;">{html.escape(_fmt(row.get(c, "—")))}</td>'
            for c in columns
        ]
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
        f"<tbody>{''.join(body_rows)}</tbody></table></div>"
    )


def _hyperfine_columns(rows: list[dict]) -> list[str]:
    preferred = [
        "atom_index", "symbol", "element", "isotope",
        "a_iso_mhz", "a_iso", "a_tensor_mhz",
    ]
    seen: set[str] = set()
    cols: list[str] = []
    for k in preferred:
        if any(k in row for row in rows):
            cols.append(k)
            seen.add(k)
    for row in rows:
        for k in row:
            if k not in seen:
                cols.append(k)
                seen.add(k)
    # a_tensor is a 3×3 — don't inline it in the table; the raw-JSON details
    # view below carries the full tensors.
    return [c for c in cols if "tensor" not in c]


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
            g_html = _kv_strip(
                {
                    "g_iso": g.get("isotropic"),
                    "g_principal": (
                        _fmt(principal) if isinstance(principal, list) else None
                    ),
                }
            )
        elif isinstance(g, list):
            g_html = _kv_strip({"g_principal": _fmt(g)})

        # Hyperfine table
        hf = data.get("hyperfine")
        hf_html = ""
        if isinstance(hf, list) and hf and all(isinstance(r, dict) for r in hf):
            hf_html = _table(hf, _hyperfine_columns(hf), "Hyperfine (A) tensors")

        # Zero-field splitting
        zfs = data.get("zero_field_splitting")
        zfs_html = ""
        if isinstance(zfs, dict):
            zfs_html = _kv_strip(
                {"D (MHz)": zfs.get("d_mhz"), "E (MHz)": zfs.get("e_mhz")}
            )

        # Raw JSON fallback for full tensors
        raw_html = (
            f'<div style="padding: 0 12px 8px 12px;">'
            f'<details style="font-size: 0.88em;"><summary>Show raw EPR JSON'
            f'</summary><pre style="max-height: 220px; overflow: auto; '
            f'font-family: monospace; font-size: 0.9em;">'
            f"{html.escape(json.dumps(data, indent=2))}</pre></details></div>"
        )

        body = g_html + hf_html + zfs_html
        if not body:
            return (
                '<div style="padding: 16px;">'
                "spectra.epr section is empty (no g_tensor, hyperfine or "
                "zero_field_splitting recorded)." + raw_html + "</div>"
            )
        return (
            '<div style="max-height: 480px; overflow: auto;">'
            + '<div style="font-weight: 600; padding: 8px 12px 0;">g-tensor</div>'
            + body + raw_html + "</div>"
        )
