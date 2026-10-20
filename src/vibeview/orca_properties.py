"""Translate ORCA 6.1 property data into the viewer's QVF section contracts.

The property-file vocabulary and its JSON export are documented at
https://www.faccts.de/docs/orca/6.1/manual/contents/utilitiesvisualization/property_file.html
The ORCA electronic-spectrum tables carry eV, cm^-1, nm and then strength;
ECD strength is R in 10^-40 cgs, not a molar extinction coefficient.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from vibeview.elements import SYMBOL_TO_Z
from vibeview.qvf_writer import QVFArchiveBuilder, structure_payload
from vibeview.units import BOHR_TO_ANGSTROM

# CODATA 2018 e*a0, converted using 1 D = 10^-21 / c C m.
_AU_TO_DEBYE = 2.541746473
_STATE_FIELDS = ("Method", "Level", "State", "Mult", "Irrep")


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"ORCA {name} must be an object")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"ORCA {name} must be an array")
    return value


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"ORCA {name} must be a finite number")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError(f"ORCA {name} must be a finite number") from None
    if not math.isfinite(result):
        raise ValueError(f"ORCA {name} must be a finite number")
    return result


def _integer(value: Any, name: str) -> int:
    number = _number(value, name)
    if not number.is_integer():
        raise ValueError(f"ORCA {name} must be an integer")
    return int(number)


def _vector(value: Any, name: str, length: int | None = None) -> list[float]:
    values = _list(value, name)
    result = []
    for entry in values:
        # ORCA serializes column vectors as [[x], [y], ...].
        if isinstance(entry, list) and len(entry) == 1:
            entry = entry[0]
        result.append(_number(entry, name))
    if length is not None and len(result) != length:
        raise ValueError(f"ORCA {name} must contain {length} values")
    return result


def _blocks(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name, value in data.items():
        if name != key and not (name.startswith(key + "_") and name[len(key) + 1 :].isdigit()):
            continue
        entries = value if isinstance(value, list) else [value]
        result.extend(_object(entry, name) for entry in entries)
    return result


def _atoms(geometry: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _object(geometry.get("Geometry"), "Geometry")
    coords = _object(raw.get("Coordinates"), "Geometry.Coordinates")
    unit = str(coords.get("Units", "")).lower()
    if unit in ("a.u.", "au", "bohr"):
        factor = BOHR_TO_ANGSTROM
    elif unit in ("angstrom", "angstroms", "angstroem", "angstroems", "å"):
        factor = 1.0
    else:
        raise ValueError(f"Unsupported ORCA coordinate units: {unit!r}")
    atoms = []
    for index, row in enumerate(_list(coords.get("Cartesians"), "Cartesians")):
        if not isinstance(row, list) or len(row) != 4 or not isinstance(row[0], str):
            raise ValueError(f"ORCA Cartesian atom {index} must contain symbol, x, y, z")
        symbol = row[0].strip().capitalize()
        if not symbol:
            raise ValueError(f"ORCA Cartesian atom {index} has an empty symbol")
        atoms.append(
            {
                "symbol": symbol,
                "atomic_number": SYMBOL_TO_Z.get(symbol, 0),
                "position": [_number(x, f"Cartesian atom {index}") * factor for x in row[1:]],
            }
        )
    if not atoms:
        raise ValueError("ORCA Geometry contains no atoms")
    if "NAtoms" in raw and _integer(raw["NAtoms"], "NAtoms") != len(atoms):
        raise ValueError("ORCA NAtoms does not match the Cartesian coordinates")
    return atoms


def _state_label(block: dict[str, Any]) -> str:
    return ", ".join(f"{key} {block[key]}" for key in _STATE_FIELDS if key in block)


def _spectra(builder: QVFArchiveBuilder, geometry: dict[str, Any], index: int) -> None:
    for key, kind, title, unit in (
        ("Absorption_Spectrum", "spectra.uvvis", "Absorption", "dimensionless"),
        ("ECD_Spectrum", "spectra.ecd", "ECD", "10^-40 cgs"),
    ):
        for ordinal, block in enumerate(_blocks(geometry, key), 1):
            rows = _list(block.get("ExcitationEnergies"), f"{key}.ExcitationEnergies")
            values = []
            for row in rows:
                numbers = _vector(row, f"{key}.ExcitationEnergies row")
                if len(numbers) < 4:
                    raise ValueError(f"ORCA {key} needs energy, wavenumber, wavelength, strength")
                values.append(numbers)
            if "NTrans" in block and _integer(block["NTrans"], f"{key}.NTrans") != len(rows):
                raise ValueError(f"ORCA {key}.NTrans does not match its transition rows")
            labels = []
            states = block.get("States")
            multiplicities = block.get("Multiplicities")
            if states is not None:
                states = _list(states, f"{key}.States")
                if len(states) != len(rows):
                    raise ValueError(f"ORCA {key}.States must have one row per transition")
            if multiplicities is not None:
                multiplicities = _list(multiplicities, f"{key}.Multiplicities")
                if len(multiplicities) != len(rows):
                    raise ValueError(f"ORCA {key}.Multiplicities must have one row per transition")
            for i in range(len(rows)):
                label = f"Transition {i + 1}"
                if states is not None:
                    state = _vector(states[i], f"{key}.States", 4)
                    if any(not value.is_integer() for value in state):
                        raise ValueError(f"ORCA {key}.States must contain integers")
                    label = (
                        f"State {int(state[0])} (irrep {int(state[1])}) → "
                        f"{int(state[2])} (irrep {int(state[3])})"
                    )
                if multiplicities is not None:
                    mult = _vector(multiplicities[i], f"{key}.Multiplicities", 2)
                    label += f"; multiplicity {mult[0]:g} → {mult[1]:g}"
                labels.append(label)
            descriptor = ", ".join(
                str(block[k]) for k in ("Density_name", "Representation") if k in block
            )
            label = f"ORCA {title} {ordinal} (geometry {index})"
            if descriptor:
                label += f" — {descriptor}"
            y_label = (
                "Oscillator strength"
                if kind == "spectra.uvvis"
                else "Rotatory strength (10^-40 cgs)"
            )
            payload = {
                "frequencies": [row[0] for row in values],
                "intensities": [row[3] for row in values],
                "units": {"frequency": "eV", "intensity": unit},
                "labels": labels,
                "orca": block,
            }
            builder.add_json(
                f"orca_g{index}_{kind.split('.')[-1]}_{ordinal}",
                kind,
                "spectrum",
                payload,
                section_extra={
                    "label": label,
                    "geometry_index": index,
                    "spectrum_style": {
                        "x_label": "Energy (eV)",
                        "x_unit_short": "eV",
                        "y_label": y_label,
                    },
                },
            )


def _populations(builder: QVFArchiveBuilder, geometry: dict[str, Any], n_atoms: int) -> None:
    for key, member, title in (
        ("Mulliken_Population_Analysis", "mulliken_charge", "Mulliken charges"),
        ("Loewdin_Population_Analysis", "loewdin_charge", "Löwdin charges"),
        ("Hirshfeld_Population_Analysis", "hirshfeld_charge", "Hirshfeld charges"),
    ):
        for ordinal, block in enumerate(_blocks(geometry, key), 1):
            if "AtomicCharges" not in block:
                continue
            charges = _vector(block["AtomicCharges"], f"{key}.AtomicCharges", n_atoms)
            extra = {
                "label": f"ORCA {title} {ordinal}: {_state_label(block)}",
                "orca": {field: block[field] for field in _STATE_FIELDS if field in block},
            }
            section_id = f"orca_{member}_{ordinal}"
            builder.add_binary(
                section_id, "atom_properties", member, np.asarray(charges), section_extra=extra
            )
            if "SpinPopulations" in block:
                spin = _vector(block["SpinPopulations"], f"{key}.SpinPopulations", n_atoms)
                builder.add_binary(
                    section_id, "atom_properties", "spin_population", np.asarray(spin)
                )
    for ordinal, block in enumerate(_blocks(geometry, "Mayer_Population_Analysis"), 1):
        if "BondOrders" not in block:
            continue
        orders = _vector(block["BondOrders"], "Mayer.BondOrders")
        components = _list(block.get("components"), "Mayer.components")
        if len(orders) != len(components):
            raise ValueError("ORCA Mayer BondOrders and components must have the same length")
        pairs = []
        for row, order in zip(components, orders, strict=True):
            if not isinstance(row, list) or len(row) != 4:
                raise ValueError("ORCA Mayer components need atom index, Z, atom index, Z")
            i, j = _integer(row[0], "Mayer atom index"), _integer(row[2], "Mayer atom index")
            if not (0 <= i < n_atoms and 0 <= j < n_atoms) or i == j:
                raise ValueError("ORCA Mayer bond indices must identify two different atoms")
            pairs.append({"i": i, "j": j, "order": order})
        builder.add_json(
            f"orca_mayer_{ordinal}",
            "bond_orders",
            "bond_orders",
            {"method": "Mayer", "pairs": pairs, "x_orca": block},
            section_extra={"label": f"ORCA Mayer {ordinal}: {_state_label(block)}"},
        )


def _ground_state_metadata(
    builder: QVFArchiveBuilder, data: dict[str, Any], geometry: dict[str, Any]
) -> None:
    provenance = dict(builder.manifest.get("provenance", {}))
    info = _object(data.get("Calculation_Info", {}), "Calculation_Info")
    provenance["orca"] = {
        key: data[key] for key in ("Calculation_Info", "Calculation_Status", "Calculation_Timings")
        if key in data
    }
    for source, destination in (
        ("Charge", "charge"),
        ("Mult", "multiplicity"),
        ("NumOfElectrons", "n_electrons"),
    ):
        if source in info:
            provenance[destination] = _integer(info[source], source)
    point = _object(geometry.get("Single_Point_Data", {}), "Single_Point_Data")
    if isinstance(point.get("Converged"), bool):
        provenance["scf_converged"] = point["Converged"]
    for block in _blocks(geometry, "Energy"):
        if block.get("Method") == "SCF" and "totalEnergy" in block:
            energy = _vector(block["totalEnergy"], "SCF.totalEnergy", 1)[0]
            provenance["scf_energy"] = {"value": energy, "units": "hartree"}
            break
    builder.set_provenance(provenance)
    for block in _blocks(geometry, "Dipole_Moment"):
        if block.get("Method") != "SCF" or block.get("State") != -1:
            continue
        if "dipoleTotal" not in block:
            continue
        vector = _vector(block["dipoleTotal"], "dipoleTotal", 3)
        builder.set_root(
            "dipole_moment",
            {
                "vector_debye": [x * _AU_TO_DEBYE for x in vector],
                "total_debye": math.hypot(*vector) * _AU_TO_DEBYE,
            },
        )
        break


def add_properties(builder: QVFArchiveBuilder, data: dict[str, Any]) -> None:
    """Add structures, spectra and properties from an ORCA property JSON object.

    The last geometry supplies the default structure and atom analyses. Earlier
    geometries become a trajectory when atom identities agree. Remaining
    property blocks are preserved as explicitly unrendered vendor data, with
    their original method/state metadata; they are never relabelled as SCF.
    """
    geometries = [
        _object(value, "Geometries entry") for value in _list(data.get("Geometries"), "Geometries")
    ]
    if not geometries:
        raise ValueError("ORCA property file contains no geometries")
    atoms = [_atoms(geometry) for geometry in geometries]
    builder.add_json(
        "structure",
        "structure",
        "structure",
        structure_payload(atoms[-1], [False] * 3),
        section_extra={"label": f"ORCA geometry {len(geometries)}"},
    )
    if len(atoms) > 1:
        identities = [[atom["symbol"] for atom in frame] for frame in atoms]
        if all(identity == identities[-1] for identity in identities):
            builder.add_json(
                "orca_geometries",
                "trajectory",
                "metadata",
                {
                    "atoms": atoms[-1],
                    "coordinate_unit": "angstrom",
                },
                section_extra={"label": "ORCA geometries"},
            )
            builder.add_binary(
                "orca_geometries",
                "trajectory",
                "coords",
                np.asarray([[a["position"] for a in frame] for frame in atoms]),
            )
        else:
            for index, frame in enumerate(atoms[:-1], 1):
                builder.add_json(
                    f"orca_structure_{index}",
                    "structure",
                    "structure",
                    structure_payload(frame, [False] * 3),
                    section_extra={"label": f"ORCA geometry {index}"},
                )
    _populations(builder, geometries[-1], len(atoms[-1]))
    _ground_state_metadata(builder, data, geometries[-1])
    for index, geometry in enumerate(geometries, 1):
        _spectra(builder, geometry, index)
        retained = {
            key: value
            for key, value in geometry.items()
            if key not in ("Geometry", "Absorption_Spectrum", "ECD_Spectrum")
        }
        if retained:
            builder.add_json(
                f"orca_properties_{index}",
                "x_orca.properties",
                "properties",
                {
                    "geometry_index": index,
                    "energy_unit": "hartree",
                    "dipole_unit": "e a0",
                    "properties": retained,
                },
                section_extra={"label": f"ORCA property metadata (geometry {index})"},
            )
