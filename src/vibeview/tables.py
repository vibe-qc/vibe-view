"""Extract tabular data from a QVF for the ``vibe-view table`` CLI (Phase E1).

Returns ``(columns, rows)`` for the kinds with naturally tabular data, for
dumping to CSV / JSON or driving a UI data grid. Only data that is meaningful as
a table is exposed here (frequencies, charges, MO energies / occupations) — not
volumetric or geometry payloads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, Section

# Section kinds `extract_table` can tabulate.
TABULATABLE = ("vibrations", "atom_properties", "wavefunction.gto", "excited_states")


def extract_table(reader: QVFReader, kind: str) -> tuple[list[str], list[list[Any]]]:
    """Return ``(columns, rows)`` for a tabulatable section ``kind``.

    Raises ``ValueError`` if the kind is unsupported or absent from the file.
    """
    if kind not in TABULATABLE:
        raise ValueError(f"kind {kind!r} is not tabulatable; try one of {TABULATABLE}")
    section = next((s for s in reader.sections if s.kind == kind), None)
    if section is None:
        raise ValueError(f"no {kind!r} section in this file")
    if kind == "vibrations":
        return _vibrations(reader, section)
    if kind == "atom_properties":
        return _atom_properties(reader, section)
    if kind == "excited_states":
        return _excited_states(reader, section)
    return _mo_table(reader, section)


def contribution_text(contribution: Any) -> str:
    """``4→5 (0.99)``, with the spin channel when the reference is unrestricted."""
    arrow = f"{contribution.from_mo}→{contribution.to_mo}"
    if contribution.spin:
        arrow += f" {contribution.spin[0]}"
    if contribution.weight is not None:
        arrow += f" ({contribution.weight:.2f})"
    return arrow


def _excited_states(reader: QVFReader, section: Section) -> tuple[list[str], list[list[Any]]]:
    """One row per state (QVF spec § 5.12), energies in eV and nm (#109)."""
    data = reader.read_excited_states(section.id)
    rows = []
    for state in data.states:
        wavelength = state.wavelength_nm
        rows.append([
            state.index,
            round(state.excitation_energy_ev, 4),
            None if wavelength is None else round(wavelength, 1),
            None if state.total_energy_eh is None else round(state.total_energy_eh, 8),
            state.multiplicity,
            state.symmetry,
            state.oscillator_strength,
            state.rotatory_strength_cgs,
            "; ".join(contribution_text(c) for c in state.contributions),
            state.nto_ref,
            state.density_ref,
        ])
    return [
        "state", "energy_ev", "wavelength_nm", "total_energy_eh", "multiplicity",
        "symmetry", "oscillator_strength", "rotatory_strength_cgs", "contributions",
        "nto_ref", "density_ref",
    ], rows


def _vibrations(reader: QVFReader, section: Section) -> tuple[list[str], list[list[Any]]]:
    import numpy as np

    data = reader.read_vibrations(section.id)
    freqs = np.asarray(data.frequencies, dtype=float)
    rows = [[i + 1, round(float(f), 2)] for i, f in enumerate(freqs)]
    return ["mode", "frequency_cm-1"], rows


def _atom_properties(reader: QVFReader, section: Section) -> tuple[list[str], list[list[Any]]]:
    data = reader.read_atom_properties(section.id)
    try:
        symbols = [a.symbol for a in reader.read_structure().atoms]
    except Exception:  # noqa: BLE001 — symbols are a convenience, not required
        symbols = None

    properties = [
        ("mulliken", data.mulliken_charges),
        ("loewdin", data.loewdin_charges),
        ("hirshfeld", data.hirshfeld_charges),
    ]
    if data.iao_charges is not None:
        properties.append(("iao", data.iao_charges))
    if data.spin_populations is not None:
        properties.append(("spin_population", data.spin_populations))
    n = max(
        (len(values) for _name, values in properties if values is not None),
        default=0,
    )
    rows = []
    for i in range(n):
        rows.append([
            i + 1,
            symbols[i] if symbols and i < len(symbols) else "",
            *[
                None if values is None else round(float(values[i]), 4)
                for _name, values in properties
            ],
        ])
    return ["atom", "element", *[name for name, _values in properties]], rows


def _mo_table(reader: QVFReader, section: Section) -> tuple[list[str], list[list[Any]]]:
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    table = WavefunctionRenderer(section, reader).mo_table()
    rows = [
        [r["index"], r["spin"], r.get("energy_eh"), r.get("occupation"), r.get("label", "")]
        for r in table
    ]
    return ["index", "spin", "energy_eh", "occupation", "label"], rows
