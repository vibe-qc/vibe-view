"""excited_states renderer — the states of one calculation as a table.

An `excited_states` section (QVF spec § 5.12) lists the electronically
excited states of a TD-DFT, CIS, ADC or similar calculation: their
excitation energies, transition properties and dominant orbital excitations.
The panel tabulates them and draws nothing in the 3D scene; the natural
transition orbitals and transition densities a state links to open from
their own sidebar rows (#109).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vibeview.renderers import BaseRenderer
from vibeview.renderers._html import data_table, heading, kv_table, note, panel_document
from vibeview.tables import contribution_text

if TYPE_CHECKING:
    from vibeview.qvf import ExcitedStatesData, QVFReader, Section

#: Display names for the methods producers write in lower case.
_METHOD_LABELS: dict[str, str] = {
    "tddft": "TD-DFT",
    "tda": "TDA",
    "cis": "CIS",
    "adc2": "ADC(2)",
    "eom-ccsd": "EOM-CCSD",
}

COLUMNS = [
    "State",
    "E (eV)",
    "λ (nm)",
    "f",
    "R (10⁻⁴⁰ cgs)",
    "Mult.",
    "Symmetry",
    "Contributions",
    "NTO",
    "Density",
]


def method_label(meta: dict[str, Any]) -> str | None:
    """The calculation's method as a label, or None when ``meta`` names none."""
    method = meta.get("method")
    if not isinstance(method, str) or not method.strip():
        return None
    label = _METHOD_LABELS.get(method.lower(), method)
    if meta.get("tda") is True and method.lower() == "tddft":
        label += " (TDA)"
    return label


class ExcitedStatesRenderer(BaseRenderer):
    """State table for an excited_states section."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: ExcitedStatesData | None = None

    def load(self) -> ExcitedStatesData:
        if self._data is None:
            self._data = self.reader.read_excited_states(self.section_id)
        return self._data

    @property
    def title(self) -> str:
        """``Excited States (TD-DFT)``, or the plain title without a method."""
        label = method_label(self.load().meta)
        return f"Excited States ({label})" if label else "Excited States"

    def render_to_html(self) -> str:
        data = self.load()
        meta = data.meta
        summary: list[tuple[str, object]] = []
        label = method_label(meta)
        if label:
            summary.append(("Method", label))
        reference = meta.get("reference_state")
        if isinstance(reference, str) and reference:
            summary.append(("Reference state", reference))
        summary.append(("States", len(data.states)))
        if data.wavefunction_ref:
            summary.append(("Reference wavefunction", data.wavefunction_ref))
        if meta.get("spin_flip") is True:
            summary.append(("Spin flip", "yes"))
        rows = []
        for state in data.states:
            wavelength = state.wavelength_nm
            rows.append(
                {
                    "State": state.index,
                    "E (eV)": round(state.excitation_energy_ev, 3),
                    "λ (nm)": None if wavelength is None else round(wavelength, 1),
                    "f": state.oscillator_strength,
                    "R (10⁻⁴⁰ cgs)": state.rotatory_strength_cgs,
                    "Mult.": state.multiplicity,
                    "Symmetry": state.symmetry,
                    "Contributions": (
                        "; ".join(contribution_text(c) for c in state.contributions) or None
                    ),
                    "NTO": state.nto_ref,
                    "Density": state.density_ref,
                }
            )
        parts = [heading(self.title), kv_table(summary)]
        if rows:
            parts.append(
                note(
                    "Contributions are from→to rows of the reference wavefunction's "
                    "coefficients (0-based) with their weight. Select a linked NTO or "
                    "density section in the sidebar to show it in the 3D view."
                )
            )
            parts.append(data_table(COLUMNS, rows))
        else:
            parts.append(note("This section lists no states."))
        return panel_document("".join(parts))
