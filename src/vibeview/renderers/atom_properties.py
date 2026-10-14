"""Atom properties renderer — Mulliken/Löwdin charges + spin populations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer
from vibeview.renderers._html import data_table, empty_panel, panel_document

if TYPE_CHECKING:
    import numpy as np

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

        def _table(title: str, charges: np.ndarray) -> str:
            # Summary row: the total charge, which should sum to the system's.
            return data_table(
                ["Atom", title],
                [[i + 1, float(q)] for i, q in enumerate(charges)],
                numeric_format="+.4f",
                footer=["Total", float(sum(charges))],
            )

        blocks: list[str] = []
        if (charge_kind is None or charge_kind == "mulliken") and (
            data.mulliken_charges is not None
        ):
            blocks.append(_table("Mulliken", data.mulliken_charges))
        if (charge_kind is None or charge_kind == "loewdin") and (
            data.loewdin_charges is not None
        ):
            blocks.append(_table("Löwdin", data.loewdin_charges))
        if (charge_kind is None or charge_kind == "hirshfeld") and (
            data.hirshfeld_charges is not None
        ):
            blocks.append(_table("Hirshfeld", data.hirshfeld_charges))
        if (charge_kind is None or charge_kind == "iao") and data.iao_charges is not None:
            blocks.append(_table("IAO", data.iao_charges))
        if data.spin_populations is not None:
            blocks.append(_table("Spin pop.", data.spin_populations))
        if not blocks:
            return empty_panel("No charge data in this section.")
        # The charge tables sit side by side and wrap when the panel is narrow.
        return panel_document(
            "".join(blocks),
            extra_css="body{display:flex;gap:24px;flex-wrap:wrap;}table{flex:0 0 auto;}",
        )
