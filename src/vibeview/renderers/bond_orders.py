"""Bond-order renderer — Mayer/Wiberg bond-order table."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer
from vibeview.renderers._coerce import as_float, as_int
from vibeview.renderers._html import (
    csv_download_link,
    data_table,
    empty_panel,
    note,
    panel_document,
)
from vibeview.units import format_length, length_label

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

    def render_to_html(self, *, length_unit: str = "angstrom") -> str:
        """Build a sortable HTML table of bond orders with CSV export.

        ``length_unit`` (``vibeview.units.LENGTH_UNITS``) is the unit of the
        table's distance column, the browser's Length unit (#58). The CSV
        keeps its ``distance_ang`` column in Å, as the archive stores it.
        """
        data = self.load()
        pairs = data.pairs
        if not pairs:
            return empty_panel("No bond-order data in this section.")

        threshold = 0.05  # filter noise
        visible = [p for p in pairs if as_float(p.get("order"), 0.0) > threshold]
        visible.sort(key=lambda p: as_float(p.get("order"), 0.0), reverse=True)

        rows: list[list[object]] = []
        csv_lines = ["pair,i,j,order,distance_ang"]
        for p in visible:
            i = as_int(p.get("i"), 0)
            j = as_int(p.get("j"), 0)
            order = as_float(p.get("order"), 0.0)
            raw_dist = p.get("distance_ang")
            dist = as_float(raw_dist)
            si = str(p.get("symbol_i", ""))
            sj = str(p.get("symbol_j", ""))
            label = f"{si}{i + 1}–{sj}{j + 1}" if si and sj else f"{i + 1}–{j + 1}"
            shown = format_length(dist, length_unit) if dist is not None else ""
            rows.append([label, order, shown])
            csv_lines.append(
                f"{label},{i},{j},{order:.4f},{dist:.4f}"
                if dist is not None
                else f"{label},{i},{j},{order:.4f},"
            )

        csv_text = "\n".join(csv_lines)

        # Summary statistics.
        orders = [as_float(p.get("order"), 0.0) for p in visible]
        avg_order = sum(orders) / len(orders) if orders else 0.0
        summary = note(
            f"{len(visible)} bonds shown (threshold {threshold:.2f}), avg order {avg_order:.3f}"
        )
        distance = f"Distance ({length_label(length_unit)})"
        headers = ["Pair", f"{data.method.capitalize()} order", distance]
        table = data_table(headers, rows, numeric_format=".3f")
        return panel_document(
            summary + table + csv_download_link(csv_text, f"bond_orders_{data.method}.csv")
        )
