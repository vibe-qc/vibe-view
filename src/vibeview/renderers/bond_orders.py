"""Bond-order renderer — Mayer/Wiberg bond-order table."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import BondOrdersData, QVFReader, Section


class BondOrdersRenderer(BaseRenderer):
    """Renders bond-order analysis as an HTML table."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: BondOrdersData | None = None

    def load(self) -> BondOrdersData:
        if self._data is None:
            self._data = self.reader.read_bond_orders(self.section_id)
        return self._data

    def render_to_html(self) -> str:
        """Build a sortable HTML table of bond orders with CSV export."""
        data = self.load()
        pairs = data.pairs
        if not pairs:
            return "<p>No bond-order data in this section.</p>"

        threshold = 0.05  # filter noise
        visible = [p for p in pairs if float(p.get("order", 0)) > threshold]
        visible.sort(key=lambda p: float(p.get("order", 0)), reverse=True)

        rows: list[str] = []
        csv_lines = ["pair,i,j,order,distance_ang"]
        for p in visible:
            i = int(p.get("i", 0))
            j = int(p.get("j", 0))
            order = float(p.get("order", 0))
            dist = p.get("distance_ang")
            si = str(p.get("symbol_i", ""))
            sj = str(p.get("symbol_j", ""))
            label = f"{si}{i + 1}–{sj}{j + 1}" if si and sj else f"{i + 1}–{j + 1}"
            dist_str = f"{float(dist):.3f} Å" if dist is not None else ""
            rows.append(f"<tr><td>{label}</td><td>{order:.3f}</td><td>{dist_str}</td></tr>")
            csv_lines.append(
                f"{label},{i},{j},{order:.4f},{float(dist):.4f}"
                if dist is not None
                else f"{label},{i},{j},{order:.4f},"
            )

        csv_text = "\n".join(csv_lines)
        csv_b64 = base64.b64encode(csv_text.encode()).decode()

        # Summary statistics.
        orders = [float(p.get("order", 0)) for p in visible]
        avg_order = sum(orders) / len(orders) if orders else 0.0
        summary = (
            f"<p style='margin:4px 0;font-size:11px;color:#888'>"
            f"{len(visible)} bonds shown (threshold {threshold:.2f}), "
            f"avg order {avg_order:.3f}"
            f"</p>"
        )

        body = (
            f"{summary}"
            f"<table>"
            f"<tr><th>Pair</th><th>{data.method.capitalize()} order</th>"
            f"<th>Distance</th></tr>"
            f"{''.join(rows)}"
            f"</table>"
            f"<p style='margin-top:8px'>"
            f"<a href='data:text/csv;base64,{csv_b64}' "
            f"download='bond_orders_{data.method}.csv' "
            f"style='color:#88aacc;text-decoration:none;font-size:11px'>"
            f"⬇ Download CSV</a>"
            f"</p>"
        )

        return (
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            "html,body{margin:0;padding:8px;font-family:system-ui,sans-serif;"
            "font-size:13px;line-height:1.4;}"
            "body{height:100%;overflow:auto;}"
            "table{border-collapse:collapse;width:100%;}"
            "th,td{padding:2px 12px;text-align:right;}"
            "th{border-bottom:1px solid #888;text-align:left;font-weight:600;}"
            "td:first-child,th:first-child{text-align:left;}"
            "tr:nth-child(even){background:rgba(255,255,255,0.03);}"
            "</style></head><body>"
            f"{body}"
            "</body></html>"
        )
