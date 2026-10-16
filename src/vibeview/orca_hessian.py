"""ORCA Hessian geometry, normal modes, IR/Raman and output-log VCD sticks.

The block layout and Cartesian normal-mode convention are documented in
ORCA 6.1 §5.18.8 (isotope shifts):
https://www.faccts.de/docs/orca/6.1/manual/contents/spectroscopyproperties/vibrations.html
This reader uses the stored frequencies/vectors; it does not diagonalize or
mass-weight the Hessian again, or interpret a Cartesian Hessian as modes.
"""

from __future__ import annotations

import json
import math
import re
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
    if "$raman_spectrum" in blocks:
        raman_rows = [[_float(x) for x in line.split()]
                for line in _counted(blocks, "$raman_spectrum")]
        if len(raman_rows) != size or any(len(row) != 3 for row in raman_rows):
            raise ValueError("ORCA Raman spectrum requires one three-column row per mode")
        values = np.asarray(raman_rows)
        if not np.allclose(values[:, 0], frequencies, atol=0.005001, rtol=0):
            raise ValueError("ORCA Raman spectrum frequencies disagree with the normal modes")
        builder.add_json("orca_raman", "spectra.raman", "spectrum", {
            "frequencies": frequencies.tolist(), "intensities": values[:, 1].tolist(),
            "labels": [f"Mode {index}" for index in range(size)],
            "units": {"frequency": "cm^-1", "intensity": "angstrom^4/amu"},
            "orca": {"depolarization_ratios": values[:, 2].tolist()},
        }, section_extra={"label": "ORCA harmonic Raman activities", "spectrum_style": {
            "x_label": "Raman shift (cm⁻¹)", "x_unit_short": "cm⁻¹",
            "y_label": "Activity (Å⁴/amu)",
        }})


def add_vcd_output(builder: QVFArchiveBuilder, text: str) -> bool:
    """Read ORCA 6.1's printed VCD table only with matching Hessian modes.

    ORCA 6.1 §5.18.3 describes analytical VCD. The 6.1.1 output labels the
    strengths 1E-44 esu² cm²; neither property.txt nor .hess contains them.
    Require one completed calculation and its final geometry to avoid silently
    combining stale logs or multiple Compound steps with a single Hessian.
    """
    heading = "VCD SPECTRUM CALCULATION"
    if heading not in text:
        return False
    if text.count(heading) != 1 or text.count("ORCA TERMINATED NORMALLY") != 1:
        raise ValueError("VCD import requires one normally terminated ORCA calculation")
    section = next((s for s in builder.manifest["sections"]
                    if s["id"] == "orca_vibrations"), None)
    if section is None:
        raise ValueError("ORCA VCD output requires its matching .hess file")
    metadata = json.loads(dict(builder.members())[section["members"]["metadata"]["path"]])
    # Printed geometry has six decimal places and may differ by translation.
    geometries = re.findall(
        r"CARTESIAN COORDINATES \(ANGSTROEM\)\s*\n-+\s*\n(.*?)(?:\n\s*\n)",
        text, re.DOTALL,
    )
    atoms = metadata["atoms"]
    rows = [line.split() for line in geometries[-1].splitlines()] if geometries else []
    if (len(rows) != len(atoms) or any(len(row) != 4 for row in rows)
            or [row[0] for row in rows] != [a["symbol"] for a in atoms]):
        raise ValueError("ORCA VCD output geometry atom identities/order do not match Hessian")
    delta = np.asarray([[_float(x) for x in row[1:]] for row in rows]) - np.asarray(
        [a["position"] for a in atoms],
    )
    if not np.allclose(delta, delta[0], atol=1e-5, rtol=0):
        raise ValueError("ORCA VCD output geometry does not match Hessian up to translation")
    table = text.split(heading, 1)[1]
    match = re.search(
        r"Mode\s+Freq\s+VCD-Intensity\s*\n\s*\(1/cm\)\s*"
        r"\(1E-44\*esu\^2\*cm\^2\)\s*\n-+\s*\n(.*?)(?:\n\s*\n)",
        table, re.DOTALL,
    )
    if match is None:
        raise ValueError("Unsupported or incomplete ORCA VCD table or units")
    frequencies = np.asarray(metadata["frequencies"])
    indices: list[int] = []
    strengths: list[float] = []
    for line in match[1].splitlines():
        fields = line.split()
        if len(fields) != 3:
            raise ValueError("ORCA VCD rows require mode index, frequency and strength")
        index = int(fields[0])
        if index < 0 or index >= len(frequencies) or index in indices:
            raise ValueError("Invalid or duplicate ORCA VCD mode index")
        if not math.isclose(_float(fields[1]), frequencies[index], abs_tol=0.050001, rel_tol=0):
            raise ValueError("ORCA VCD frequencies disagree with the normal modes")
        indices.append(index)
        strengths.append(_float(fields[2]))
    if not indices:
        raise ValueError("Empty ORCA VCD table")
    builder.add_json("orca_vcd", "spectra.vcd", "spectrum", {
        "frequencies": frequencies[indices].tolist(), "intensities": strengths,
        "labels": [f"Mode {index}" for index in indices],
        "units": {"frequency": "cm^-1", "intensity": "1e-44 esu^2 cm^2"},
        "orca": {"mode_indices": indices, "source": "printed VCD output table"},
    }, section_extra={"label": "ORCA harmonic VCD rotatory strengths", "spectrum_style": {
        "x_label": "Wavenumber (cm⁻¹)", "x_unit_short": "cm⁻¹",
        "y_label": "Rotatory strength (10⁻⁴⁴ esu² cm²)",
    }})
    return True


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
