"""Optional cclib result import, using its documented attribute units.

https://cclib.github.io/data.html and data_notes.html define the interchange:
positions in angstrom, SCF/MO/scan energies in eV, vibrational/excitation
frequencies in cm^-1. Missing properties are not reconstructed or guessed.
"""
from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Any

import numpy as np

from vibeview.elements import Z_TO_SYMBOL
from vibeview.install_hints import install_hint
from vibeview.qvf_writer import QVFArchiveBuilder, structure_payload
from vibeview.units import EV_TO_CM1, HARTREE_TO_EV, HARTREE_TO_KCAL_MOL

RESULT_EXTENSIONS = (".out", ".log")


def _array(data: Any, name: str) -> np.ndarray:
    value = np.asarray(getattr(data, name), dtype=float)
    if not np.isfinite(value).all():
        raise ValueError(f"Non-finite parsed {name}")
    return value


def result_to_qvf(path: str | Path) -> io.BytesIO:
    """Parse a supported output without launching the producing program."""
    try:
        from cclib.io.ccio import ccread
    except ImportError as exc:
        raise ValueError(
            f"Quantum-chemistry output import needs cclib. {install_hint('results')}"
        ) from exc
    data = ccread(str(path), loglevel=logging.ERROR)
    if data is None:
        raise ValueError("cclib did not recognize a supported quantum-chemistry output")
    return parsed_to_qvf(data)


def parsed_to_qvf(data: Any) -> io.BytesIO:
    """Map documented parsed attributes to existing QVF kinds."""
    if not hasattr(data, "atomcoords") or not hasattr(data, "atomnos"):
        raise ValueError("Parsed output contains no atomic geometry")
    coords = _array(data, "atomcoords")
    numbers = np.asarray(data.atomnos, dtype=int)
    if coords.ndim != 3 or coords.shape[1:] != (len(numbers), 3) or not len(coords):
        raise ValueError("Parsed geometry shape does not match atom identities")
    atoms = [{"atomic_number": int(z), "symbol": Z_TO_SYMBOL.get(int(z), "X"),
              "position": pos.tolist()} for z, pos in zip(numbers, coords[-1], strict=True)]
    builder = QVFArchiveBuilder(calculation="cclib result import")
    builder.add_json("structure", "structure", "structure", structure_payload(atoms, [False]*3))
    provenance: dict[str, Any] = {"parser": "cclib", "source_program": data.metadata.get("package")}
    for source, target in (("charge", "charge"), ("mult", "multiplicity")):
        if hasattr(data, source):
            provenance[target] = int(getattr(data, source))
    scf = _array(data, "scfenergies") / HARTREE_TO_EV if hasattr(data, "scfenergies") else None
    if scf is not None:
        provenance["scf_energies_hartree"] = scf.tolist()
    if len(coords) > 1:
        meta: dict[str, Any] = {"atoms": atoms, "coordinate_unit": "angstrom"}
        if scf is not None and len(scf) == len(coords):
            meta["energies"] = scf.tolist()
        builder.add_json("result_trajectory", "trajectory", "metadata", meta)
        builder.add_binary("result_trajectory", "trajectory", "coords", coords)
    # cclib scfvalues are convergence diagnostics, not iteration energies.
    if hasattr(data, "scfvalues"):
        rows: list[dict[str, Any]] = []
        for step, iterations in enumerate(data.scfvalues):
            for iteration, values in enumerate(iterations):
                values = np.asarray(values, dtype=float)
                if not np.isfinite(values).all():
                    continue
                rows.append({"iter": len(rows) + 1, "geometry_step": step,
                             "scf_iteration": iteration + 1, "convergence_values": values.tolist()})
        builder.add_json("result_scf", "scf_history", "iterations", {"iterations": rows})
    if hasattr(data, "vibfreqs"):
        frequencies = _array(data, "vibfreqs")
        if hasattr(data, "vibdisps"):
            displacements = _array(data, "vibdisps")
            if displacements.shape != (len(frequencies), len(atoms), 3):
                raise ValueError("Parsed normal modes do not match frequencies/atoms")
            builder.add_json("result_vibrations", "vibrations", "metadata",
                             {"atoms": atoms, "frequencies": frequencies.tolist()},
                             section_extra={"structure_ref": "structure"})
            builder.add_binary("result_vibrations", "vibrations", "displacements", displacements)
        provenance["vibrational_frequencies_cm-1"] = frequencies.tolist()
        for field, kind, unit in (("vibirs", "spectra.ir", "km/mol"),
                                  ("vibramans", "spectra.raman", "angstrom^4/amu")):
            if hasattr(data, field):
                _spectrum(builder, field, kind, frequencies, _array(data, field), "cm^-1", unit)
    if hasattr(data, "etenergies"):
        excitation = _array(data, "etenergies") / EV_TO_CM1
        provenance["excitation_energies_ev"] = excitation.tolist()
        if hasattr(data, "etoscs"):
            _spectrum(builder, "excitations", "spectra.uvvis", excitation,
                      _array(data, "etoscs"), "eV", "dimensionless")
    for method, values in getattr(data, "atomcharges", {}).items():
        charges = np.asarray(values, dtype=float)
        if charges.shape != (len(atoms),) or not np.isfinite(charges).all():
            raise ValueError("Parsed charges do not match atoms")
        name = re.sub(r"[^A-Za-z0-9_]", "_", method)
        section = f"result_charge_{name}"
        standard = {"mulliken": "mulliken_charge", "lowdin": "loewdin_charge",
                    "hirshfeld": "hirshfeld_charge", "cm5": "cm5_charge"}
        member = standard.get(method, f"custom.{name}_charge")
        builder.add_binary(section, "atom_properties", member, charges,
                           section_extra={"structure_ref": "structure", "label": method})
        if member.startswith("custom."):
            builder.add_json(section, "atom_properties", "meta", {member: {
                "units": "e", "description": f"cclib {method} atomic charge",
                "kind": "scalar"}})
    if hasattr(data, "moenergies"):
        provenance["mo_energies_ev"] = [
            np.asarray(spin, dtype=float).tolist() for spin in data.moenergies]
        for spin, values in enumerate(data.moenergies):
            values = np.asarray(values, dtype=float)
            _spectrum(builder, f"mo_{spin}", "spectra.generic", values,
                      np.ones(len(values)), "eV", "orbital marker")
        if hasattr(data, "homos"):
            provenance["homo_indices_zero_based"] = np.asarray(data.homos, dtype=int).tolist()
    thermo = {}
    for source, target, factor in (
        ("enthalpy", "enthalpy_eh", 1), ("freeenergy", "gibbs_free_energy_eh", 1),
        ("entropy", "entropy_cal_mol_k", HARTREE_TO_KCAL_MOL * 1000),
        ("zpve", "zpve_eh", 1), ("temperature", "temperature_k", 1),
        ("pressure", "pressure_atm", 1),
    ):
        if hasattr(data, source):
            thermo[target] = float(getattr(data, source)) * factor
    if thermo:
        builder.set_root("thermochemistry", thermo)
    _scan(builder, data, atoms)
    builder.set_provenance(provenance)
    return builder.to_bytesio()


def _spectrum(builder: QVFArchiveBuilder, name: str, kind: str,
              x: np.ndarray, y: np.ndarray, xunit: str, yunit: str) -> None:
    if x.ndim != 1 or x.shape != y.shape or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError(f"Invalid parsed {name} spectrum")
    builder.add_json("result_" + name, kind, "spectrum",
                     {"frequencies": x.tolist(), "intensities": y.tolist(),
                      "units": {"frequency": xunit, "intensity": yunit}},
                     section_extra={"label": name, "spectrum_style": {
                         "x_label": xunit, "x_unit_short": xunit, "y_label": yunit}})


def _scan(builder: QVFArchiveBuilder, data: Any, atoms: list[dict[str, Any]]) -> None:
    if not all(hasattr(data, name) for name in ("scanparm", "scanenergies", "scancoords")):
        return
    coords = _array(data, "scancoords")
    energies = _array(data, "scanenergies") / HARTREE_TO_EV
    parameters = _array(data, "scanparm")
    if coords.shape != (len(energies), len(atoms), 3) or parameters.ndim != 2:
        raise ValueError("Parsed scan geometries/energies/parameters disagree")
    if parameters.shape[1] != len(energies):
        raise ValueError("Parsed scan parameter count differs from energy count")
    names = getattr(data, "scannames", [f"Coordinate {i+1}" for i in range(len(parameters))])
    if len(parameters) == 1:
        builder.add_json("result_scan", "reaction.path", "metadata", {
            "atoms": atoms, "coordinate_unit": "angstrom", "energies": energies.tolist(),
            "reaction_coordinate": parameters[0].tolist(), "reaction_coordinate_label": names[0],
        })
        builder.add_binary("result_scan", "reaction.path", "coords", coords)
    elif len(parameters) == 2:
        a, b = (np.unique(row) for row in parameters)
        if len(a) * len(b) != len(energies):
            raise ValueError("Parsed two-dimensional scan is not a complete rectangular grid")
        grid = np.empty((len(a), len(b)))
        geometry = np.empty((len(a), len(b), len(atoms), 3))
        seen: set[tuple[int, int]] = set()
        for i, (x, y) in enumerate(parameters.T):
            key = (int(np.searchsorted(a, x)), int(np.searchsorted(b, y)))
            if key in seen:
                raise ValueError("Duplicate parsed scan point")
            seen.add(key)
            grid[key] = energies[i]
            geometry[key] = coords[i]
        builder.add_json("result_scan", "scan.surface", "metadata", {
            "atoms": atoms, "coordinate_a_label": names[0], "coordinate_b_label": names[1]})
        members = (("axis_a", a), ("axis_b", b), ("energies", grid), ("geometries", geometry))
        for name, value in members:
            builder.add_binary("result_scan", "scan.surface", name, value)
