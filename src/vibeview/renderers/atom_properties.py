"""Atom properties renderer — Mulliken/Löwdin charges + spin populations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import AtomPropertiesData, QVFReader, Section


class AtomPropertiesRenderer(BaseRenderer):
    """Renders Mulliken/Löwdin charges and spin populations as HTML tables."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: AtomPropertiesData | None = None

    def load(self) -> AtomPropertiesData:
        if self._data is None:
            self._data = self.reader.read_atom_properties(self.section_id)
        return self._data

    def render_to_html(self, charge_kind: str | None = None) -> str:
        """Build an HTML table of charges.

        Designed to live inside the bottom-panel iframe (sandbox=
        allow-scripts, fixed height). The body has its own scrollbars and a
        compact two-column layout so the table reads cleanly even when the
        viewer window is narrow.

        When ``charge_kind`` is provided (``"mulliken"`` / ``"loewdin"`` /
        ``"hirshfeld"``), only that method's table is shown. When ``None``
        or unrecognised, all available methods are displayed side-by-side.
        """
        data = self.load()

        def _table(title: str, charges) -> str:
            total = sum(charges)
            rows = [
                f"<tr><th>Atom</th><th>{title}</th></tr>",
                *[f"<tr><td>{i + 1}</td><td>{q:+.4f}</td></tr>" for i, q in enumerate(charges)],
                # Summary row — total charge (should sum to net charge of system).
                (
                    f"<tr style='border-top:2px solid #888;font-weight:600'>"
                    f"<td>Total</td><td>{total:+.4f}</td></tr>"
                ),
            ]
            return f"<table>{''.join(rows)}</table>"

        blocks: list[str] = []
        if charge_kind is None or charge_kind == "mulliken":
            if data.mulliken_charges is not None:
                blocks.append(_table("Mulliken", data.mulliken_charges))
        if charge_kind is None or charge_kind == "loewdin":
            if data.loewdin_charges is not None:
                blocks.append(_table("Löwdin", data.loewdin_charges))
        if charge_kind is None or charge_kind == "hirshfeld":
            if data.hirshfeld_charges is not None:
                blocks.append(_table("Hirshfeld", data.hirshfeld_charges))
        if charge_kind is None or charge_kind == "iao":
            if data.iao_charges is not None:
                blocks.append(_table("IAO", data.iao_charges))
        if data.spin_populations is not None:
            blocks.append(_table("Spin pop.", data.spin_populations))
        body = "".join(blocks) if blocks else "<p>No charge data in this section.</p>"

        return (
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            "html,body{margin:0;padding:8px;font-family:system-ui,sans-serif;"
            "font-size:13px;line-height:1.4;}"
            "body{height:100%;overflow:auto;display:flex;gap:24px;flex-wrap:wrap;}"
            "table{border-collapse:collapse;flex:0 0 auto;}"
            "th,td{padding:2px 12px;text-align:right;}"
            "th{border-bottom:1px solid #888;text-align:left;font-weight:600;}"
            "td:first-child,th:first-child{text-align:left;color:#666;}"
            "</style></head><body>"
            f"{body}"
            "</body></html>"
        )
