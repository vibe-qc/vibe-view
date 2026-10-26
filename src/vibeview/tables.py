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
TABULATABLE = (
    "vibrations", "atom_properties", "wavefunction.gto", "excited_states", "forces", "hessian",
    "properties.electric", "properties.elastic", "properties.dielectric", "wavefunction.bloch",
)


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
    if kind in ("forces", "hessian"):
        return _derivatives(reader, section)
    if kind.startswith("properties."):
        return _properties(reader, section)
    if kind == "wavefunction.bloch":
        from vibeview.renderers.bloch import ORBITAL_COLUMNS, orbital_rows

        return ORBITAL_COLUMNS, orbital_rows(reader.read_wavefunction_bloch(section.id))
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


# Per-mode quantities: (column stem, VibrationsData field, its schema unit,
# companion spectrum kind, that kind's conventional unit). QVF spec § 5.5
# and § 4.3; Raman and VCD intensities have no unit unless declared (#150).
_PER_MODE = (
    ("ir_intensity", "ir_intensities", "km/mol", "spectra.ir", "km/mol"),
    ("raman_activity", "raman_activities", "angstrom^4/amu", "spectra.raman", None),
    ("vcd_rotatory_strength", None, None, "spectra.vcd", None),
    ("reduced_mass", "reduced_masses", "amu", None, None),
    ("force_constant", "force_constants", "mdyn/angstrom", None, None),
)


def _vibrations(reader: QVFReader, section: Section) -> tuple[list[str], list[list[Any]]]:
    """One row per mode, with every per-mode quantity the file has (#150).

    A value in the vibrations section wins; a companion ``spectra.ir``,
    ``spectra.raman`` or ``spectra.vcd`` fills a quantity the section lacks.
    """
    import numpy as np

    data = reader.read_vibrations(section.id)
    freqs = np.asarray(data.frequencies, dtype=float)
    columns = ["mode", "frequency_cm-1"]
    values: list[list[Any]] = []
    if data.symmetry_labels is not None:
        columns.append("symmetry")
        values.append(list(data.symmetry_labels))
    for stem, field, unit, kind, default_unit in _PER_MODE:
        own = getattr(data, field) if field else None
        if own is not None:
            column: list[Any] = [round(float(v), 4) for v in own]
        elif kind is not None:
            found = _companion_values(reader, kind, freqs, default_unit)
            if found is None:
                continue
            column, unit = found
        else:
            continue
        columns.append(f"{stem}_{unit}" if unit else stem)
        values.append(column)
    rows = [
        [i + 1, round(float(f), 2), *[column[i] for column in values]]
        for i, f in enumerate(freqs)
    ]
    return columns, rows


def mode_ir_intensities(
    reader: QVFReader, data: Any
) -> tuple[list[float | None], str | None] | None:
    """Each mode's IR intensity and its unit, as the vibrations table finds it.

    The vibrations section's own ``ir_intensities`` (km/mol) come first,
    then a companion ``spectra.ir``; ``None`` when the file has neither.
    Shared with the browser's mode picker so the two agree (#151).
    """
    if data.ir_intensities is not None:
        return [float(v) for v in data.ir_intensities], "km/mol"
    return _companion_values(reader, "spectra.ir", data.frequencies, "km/mol")


def _companion_values(
    reader: QVFReader, kind: str, freqs: Any, default_unit: str | None
) -> tuple[list[Any], str | None] | None:
    """Per-mode intensities of the first ``kind`` spectrum, and their unit.

    A spectrum listing every mode is matched by index. Otherwise each peak's
    ``assignment.mode_index`` names its mode (QVF spec § 5.4), or else a
    mode with exactly its frequency does: ORCA's VCD table lists only the
    real modes. A spectrum that matches no mode adds no column.
    """
    section = next((s for s in reader.sections if s.kind == kind), None)
    if section is None:
        return None
    try:
        payload = reader.read_json_member(section.id, "spectrum")
        spectrum = reader.read_spectra(section.id)
    except Exception:  # noqa: BLE001 — a broken companion only loses its column
        return None
    n_modes = len(freqs)
    column: list[Any] = [None] * n_modes
    if len(spectrum.intensities) == n_modes:
        column = [round(float(v), 4) for v in spectrum.intensities]
    else:
        peaks = payload.get("peaks") if isinstance(payload, dict) else None
        assigned = [
            (p["assignment"].get("mode_index"), p.get("y"))
            for p in peaks or []
            if isinstance(p, dict) and isinstance(p.get("assignment"), dict)
        ]
        if assigned:
            for index, y in assigned:
                if isinstance(index, int) and 0 <= index < n_modes and y is not None:
                    column[index] = round(float(y), 4)
        else:
            for x, y in zip(spectrum.frequencies, spectrum.intensities, strict=True):
                matches = [i for i, f in enumerate(freqs) if float(f) == float(x)]
                if len(matches) == 1:
                    column[matches[0]] = round(float(y), 4)
    if all(v is None for v in column):
        return None
    return column, _declared_unit(payload) or default_unit


def _declared_unit(payload: Any) -> str | None:
    """``y_units`` (QVF spec § 5.4), else the older ``units.intensity``."""
    if not isinstance(payload, dict):
        return None
    unit = payload.get("y_units")
    if unit is None and isinstance(payload.get("units"), dict):
        unit = payload["units"].get("intensity")
    return str(unit).replace(" ", "") if unit else None


def _derivatives(reader: QVFReader, section: Section) -> tuple[list[str], list[list[Any]]]:
    """Forces (F = −∇E) per atom, or the Hessian's harmonic frequencies (#159)."""
    from vibeview.renderers import derivatives

    if section.kind == "forces":
        forces = reader.read_forces(section.id)
        atoms = reader.read_structure(forces.structure_id, apply_edits=False).atoms
        return derivatives.FORCE_COLUMNS, derivatives.forces_rows(
            forces, [a.symbol for a in atoms]
        )
    hessian = reader.read_hessian(section.id)
    atoms = reader.read_structure(hessian.structure_id, apply_edits=False).atoms
    numbers = [a.atomic_number for a in atoms]
    rows = derivatives.hessian_rows(hessian, numbers)
    if not rows:
        _wavenumbers, _eigenvalues, reason = derivatives.hessian_frequencies(hessian, numbers)
        raise ValueError(f"no frequencies for {section.id!r}: {reason}")
    return derivatives.HESSIAN_COLUMNS, rows


def _properties(reader: QVFReader, section: Section) -> tuple[list[str], list[list[Any]]]:
    """One row per stored number of a ``properties.*`` section (#161)."""
    from vibeview.renderers.properties import PROPERTY_COLUMNS, property_rows

    data = reader.read_properties(section.id)
    symbols = None
    if data.structure_id is not None:
        atoms = reader.read_structure(data.structure_id, apply_edits=False).atoms
        symbols = [a.symbol for a in atoms]
    return PROPERTY_COLUMNS, property_rows(data, symbols)


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
