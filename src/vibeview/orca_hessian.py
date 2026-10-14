"""ORCA Hessian-file geometry, stored normal modes and harmonic IR sticks.

The block layout and Cartesian normal-mode convention are documented in
ORCA 6.1 §5.18.8 (isotope shifts):
https://www.faccts.de/docs/orca/6.1/manual/contents/spectroscopyproperties/vibrations.html
This reader uses the stored frequencies/vectors; it does not diagonalize or
mass-weight the Hessian again, or interpret a Cartesian Hessian as modes.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from vibeview.elements import SYMBOL_TO_Z
from vibeview.qvf_writer import QVFArchiveBuilder, structure_payload
from vibeview.units import BOHR_TO_ANGSTROM


def _blocks(text: str) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("$"):
            key = line.lower()
            if key in blocks:
                raise ValueError(f"Duplicate ORCA Hessian block {key}")
            current = blocks[key] = []
        elif current is not None:
            current.append(line)
    if "$orca_hessian_file" not in blocks:
        raise ValueError("Expected an ORCA $orca_hessian_file header")
    return blocks


def _float(text: str) -> float:
    value = float(text.replace("D", "E").replace("d", "e"))
    if not math.isfinite(value):
        raise ValueError("ORCA Hessian values must be finite")
    return value


def _counted(blocks: dict[str, list[str]], key: str) -> list[str]:
    lines = blocks.get(key, [])
    if not lines:
        raise ValueError(f"Missing ORCA Hessian block {key}")
    count = int(lines[0])
    if count < 1 or len(lines) != count + 1:
        raise ValueError(f"Invalid row count in ORCA Hessian block {key}")
    return lines[1:]


def _normal_modes(lines: list[str], size: int) -> np.ndarray:
    if not lines or lines[0].split() != [str(size), str(size)]:
        raise ValueError("ORCA normal_modes dimensions must both equal 3 × atom count")
    # Bound allocation by actual input data, not an untrusted declared count.
    if sum(len(row.split()) for row in lines[1:]) < size * size:
        raise ValueError("Incomplete ORCA normal_modes matrix")
    matrix = np.full((size, size), np.nan)
    columns_seen: set[int] = set()
    cursor = 1
    while cursor < len(lines):
        columns = [int(value) for value in lines[cursor].split()]
        if (not columns or len(set(columns)) != len(columns)
                or any(col < 0 or col >= size or col in columns_seen for col in columns)):
            raise ValueError("Invalid or duplicate ORCA normal_modes column indices")
        columns_seen.update(columns)
        cursor += 1
        rows_seen: set[int] = set()
        for _ in range(size):
            if cursor >= len(lines):
                raise ValueError("Incomplete ORCA normal_modes matrix")
            fields = lines[cursor].split()
            cursor += 1
            if len(fields) != len(columns) + 1:
                raise ValueError("Invalid ORCA normal_modes row width")
            row = int(fields[0])
            if row < 0 or row >= size or row in rows_seen:
                raise ValueError("Invalid or duplicate ORCA normal_modes row index")
            rows_seen.add(row)
            matrix[row, columns] = [_float(value) for value in fields[1:]]
    if len(columns_seen) != size or not np.isfinite(matrix).all():
        raise ValueError("Incomplete ORCA normal_modes matrix")
    # File rows are atom Cartesian coordinates; columns are mode numbers.
    return matrix.T.reshape(size, size // 3, 3)


def add_hessian(builder: QVFArchiveBuilder, content: bytes) -> None:
    """Add one geometry and its modes, preserving zero/imaginary frequencies."""
    blocks = _blocks(content.decode("utf-8-sig"))
    if "$multiplicity" in blocks:
        lines = blocks["$multiplicity"]
        if len(lines) != 1 or int(lines[0]) < 1:
            raise ValueError("Invalid ORCA Hessian multiplicity")
        builder.set_provenance({"multiplicity": int(lines[0])})
    atoms: list[dict[str, Any]] = []
    masses: list[float] = []
    for line in _counted(blocks, "$atoms"):
        fields = line.split()
        if len(fields) != 5 or fields[0] not in SYMBOL_TO_Z:
            raise ValueError("ORCA Hessian atoms require element, mass and three bohr coordinates")
        mass = _float(fields[1])
        if mass <= 0:
            raise ValueError("ORCA Hessian atomic masses must be positive")
        masses.append(mass)
        atoms.append({
            "symbol": fields[0], "atomic_number": SYMBOL_TO_Z[fields[0]],
            "position": [_float(value) * BOHR_TO_ANGSTROM for value in fields[2:]],
        })
    size = 3 * len(atoms)
    rows = _counted(blocks, "$vibrational_frequencies")
    if len(rows) != size:
        raise ValueError("ORCA vibrational frequency count must equal 3 × atom count")
    frequencies = np.full(size, np.nan)
    for line in rows:
        fields = line.split()
        if len(fields) != 2:
            raise ValueError("ORCA frequencies require mode index and wavenumber")
        index = int(fields[0])
        if index < 0 or index >= size or not np.isnan(frequencies[index]):
            raise ValueError("Invalid or duplicate ORCA vibrational frequency index")
        frequencies[index] = _float(fields[1])
    modes = _normal_modes(blocks.get("$normal_modes", []), size)
    builder.add_json("structure", "structure", "structure", structure_payload(atoms, [False] * 3))
    builder.add_json("orca_vibrations", "vibrations", "metadata", {
        "atoms": atoms, "frequencies": frequencies.tolist(),
        "orca": {"atomic_masses_amu": masses, "mode_indices": list(range(size)),
                 "displacement_convention": "stored Cartesian normal modes"},
    }, section_extra={"label": "ORCA harmonic vibrations", "structure_ref": "structure"})
    builder.add_binary("orca_vibrations", "vibrations", "displacements", modes)
    if "$ir_spectrum" in blocks:
        _add_ir(builder, _counted(blocks, "$ir_spectrum"), frequencies)


def _add_ir(builder: QVFArchiveBuilder, lines: list[str], frequencies: np.ndarray) -> None:
    rows = [[_float(value) for value in line.split()] for line in lines]
    widths = {len(row) for row in rows}
    if len(rows) != len(frequencies) or widths not in ({5}, {6}):
        raise ValueError("ORCA IR spectrum requires one five- or six-column row per mode")
    values = np.asarray(rows)
    # The spectrum prints only two decimals, while mode frequencies retain
    # full precision. Allow half a last printed digit plus float roundoff.
    if not np.allclose(values[:, 0], frequencies, atol=0.005001, rtol=0):
        raise ValueError("ORCA IR spectrum frequencies disagree with the normal modes")
    # ORCA 6 writes frequency, epsilon, intensity (km/mol), Tx, Ty, Tz.
    # Older five-column files store T² directly, not km/mol intensities.
    modern = widths == {6}
    unit = "km/mol" if modern else "a.u."
    quantity = "Intensity (km/mol)" if modern else "Transition dipole squared (a.u.)"
    builder.add_json("orca_ir", "spectra.ir" if modern else "spectra.generic", "spectrum", {
        "frequencies": frequencies.tolist(),
        "intensities": values[:, 2 if modern else 1].tolist(),
        "labels": [f"Mode {index}" for index in range(len(frequencies))],
        "units": {"frequency": "cm^-1", "intensity": unit},
    }, section_extra={"label": "ORCA harmonic IR", "spectrum_style": {
        "x_label": "Wavenumber (cm⁻¹)", "x_unit_short": "cm⁻¹", "y_label": quantity,
    }})
