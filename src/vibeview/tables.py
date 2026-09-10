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
TABULATABLE = ("vibrations", "atom_properties", "wavefunction.gto")


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
    return _mo_table(reader, section)


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

    mulliken = data.mulliken_charges
    loewdin = data.loewdin_charges
    hirshfeld = data.hirshfeld_charges
    n = max(
        len(mulliken) if mulliken is not None else 0,
        len(loewdin) if loewdin is not None else 0,
        len(hirshfeld) if hirshfeld is not None else 0,
    )
    rows = []
    for i in range(n):
        rows.append([
            i + 1,
            symbols[i] if symbols and i < len(symbols) else "",
            None if mulliken is None else round(float(mulliken[i]), 4),
            None if loewdin is None else round(float(loewdin[i]), 4),
            None if hirshfeld is None else round(float(hirshfeld[i]), 4),
        ])
    return ["atom", "element", "mulliken", "loewdin", "hirshfeld"], rows


def _mo_table(reader: QVFReader, section: Section) -> tuple[list[str], list[list[Any]]]:
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    table = WavefunctionRenderer(section, reader).mo_table()
    rows = [
        [r["index"], r["spin"], r.get("energy_eh"), r.get("occupation"), r.get("label", "")]
        for r in table
    ]
    return ["index", "spin", "energy_eh", "occupation", "label"], rows
