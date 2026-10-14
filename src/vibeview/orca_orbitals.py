"""Read real molecular orbitals from the ORCA 6 ``orca_2json`` format.

The ORCA manual defines the JSON and its solid harmonics in sections 9.3.1
and 9.3.3: https://www.faccts.de/docs/orca/6.1/manual/contents/
utilitiesvisualization/orca_2json.html. In particular, ORCA's f(+/-3)
functions have the opposite phase to QVF's real spherical harmonics.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from vibeview.elements import Z_TO_SYMBOL
from vibeview.qvf_writer import QVFArchiveBuilder
from vibeview.units import BOHR_TO_ANGSTROM, HARTREE_TO_EV


def _array(value: Any, shape: tuple[int, ...], field: str) -> np.ndarray:
    try:
        array = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ORCA {field} must contain real numbers") from exc
    if array.shape != shape or not np.all(np.isfinite(array)):
        raise ValueError(f"ORCA {field} must contain finite values with shape {shape}")
    return array


def _basis(atoms: list[dict[str, Any]]) -> tuple[dict[str, Any], list[int], list[int]]:
    shells = []
    order: list[int] = []
    phases: list[int] = []
    for center, atom in enumerate(atoms):
        if atom.get("Idx", center) != center:
            raise ValueError("ORCA atom Idx must follow the Atoms array order for orbital import")
        atomic_basis = atom.get("Basis")
        if not isinstance(atomic_basis, list) or not atomic_basis:
            raise ValueError(
                "ORCA orbitals require Basis on every atom; export with "
                'orca_2json and {"Basisset": true, "MOCoefficients": true}'
            )
        for shell in atomic_basis:
            label = str(shell.get("Shell", "")).lower() if isinstance(shell, dict) else ""
            if label not in ("s", "p", "d", "f"):
                raise ValueError(
                    "ORCA orbital rendering supports spherical s, p, d and f shells; "
                    "export a cube volume for other shells"
                )
            ell = "spdf".index(label)
            exponents = shell.get("Exponents")
            if not isinstance(exponents, list) or not exponents:
                raise ValueError("ORCA Basis Exponents must be a nonempty array")
            alpha = _array(exponents, (len(exponents),), "Basis Exponents")
            coefficients = _array(shell.get("Coefficients"), alpha.shape, "Basis Coefficients")
            if np.any(alpha <= 0):
                raise ValueError("ORCA Basis Exponents must be positive")
            # orca_2json exports coefficients of normalized primitives, with
            # contraction normalization already included (manual 9.3.1.4).
            # QVF A.1 applies that same primitive norm at evaluation time.
            shells.append(
                {
                    "center": center,
                    "l": ell,
                    "pure": True,
                    "exponents": alpha.tolist(),
                    "coefficients": coefficients.tolist(),
                }
            )
            offset = len(order)
            # ORCA: 0,+1,-1,...; QVF: -l,...,+l. Manual 9.3.3 also
            # specifies the negative phases of ORCA's two f(|m|=3) AOs.
            for m in range(-ell, ell + 1):
                order.append(offset + 2 * abs(m) - (1 if m > 0 else 0))
                phases.append(-1 if ell == 3 and abs(m) == 3 else 1)
    return (
        {"structure_ref": "structure", "pure": True, "n_ao": len(order), "shells": shells},
        order,
        phases,
    )


def _orbital_data(
    molecule: dict[str, Any], atoms: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, np.ndarray]] | None:
    raw = molecule.get("MolecularOrbitals")
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("MOs"), list) or not raw["MOs"]:
        raise ValueError("ORCA MolecularOrbitals must contain a nonempty MOs array")
    rows = raw["MOs"]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("ORCA MOs must contain orbital objects")
    if not any("MOCoefficients" in row for row in rows):
        return None
    hf_type = str(molecule.get("HFTyp", "")).upper()
    if hf_type not in ("RHF", "ROHF", "UHF", "CASSCF"):
        raise ValueError(f"ORCA HFTyp {hf_type!r} is unsupported for real molecular orbitals")
    basis, order, phases = _basis(atoms)
    n_ao = len(order)
    n_mo = len(rows)
    coefficients = _array(
        [row.get("MOCoefficients") for row in rows], (n_mo, n_ao), "MOCoefficients"
    )[:, order] * np.asarray(phases)
    # FACCTs OPI Output.get_mos uses this same alpha-then-beta split:
    # https://github.com/faccts/opi/blob/main/src/opi/output/core.py
    unrestricted = hf_type == "UHF"
    if unrestricted and n_mo % 2:
        raise ValueError("ORCA UHF MOs must contain equal alpha and beta blocks")
    if n_mo > n_ao * (2 if unrestricted else 1):
        raise ValueError("ORCA MO count exceeds the number of basis functions per spin")
    metadata: dict[str, Any] = {
        "n_ao": n_ao,
        "spin": "unrestricted" if unrestricted else "restricted",
    }
    # CASSCF carries one spatial-orbital block and fractional occupations.
    # Do not infer "natural": ORCA permits other active-space orbital choices
    # (manual 3.13.3.4), and this JSON does not report the canonicalization.
    if hf_type == "CASSCF":
        metadata["source_wavefunction"] = "CASSCF"
        metadata["density_unavailable_reason"] = (
            "ORCA CASSCF JSON does not identify the active-orbital canonicalization; "
            "occupations alone cannot reconstruct the correlated density. "
            "Open a density cube exported by ORCA."
        )
    values: dict[str, Any] = {}
    for field, key in (("Occupancy", "occupations"), ("OrbitalEnergy", "energies")):
        present = [field in row for row in rows]
        if not any(present):
            continue
        if not all(present):
            raise ValueError(f"ORCA {field} must be present on every MO or omitted on all")
        array = _array([row[field] for row in rows], (n_mo,), field)
        if field == "Occupancy":
            maximum = 1 if unrestricted else 2
            if np.any(array < 0) or np.any(array > maximum + 1e-8):
                raise ValueError(f"ORCA {hf_type} occupations must lie between 0 and {maximum}")
        else:
            unit = str(raw.get("EnergyUnit", "")).lower()
            if unit == "ev":
                array /= HARTREE_TO_EV
            elif unit not in ("eh", "hartree", "au", "a.u."):
                raise ValueError(f"Unsupported ORCA orbital EnergyUnit {unit!r}")
        values[key] = array.tolist()
    if any("OrbitalSymLabel" in row for row in rows):
        if not all(isinstance(row.get("OrbitalSymLabel"), str) for row in rows):
            raise ValueError("ORCA OrbitalSymLabel must be a string on every MO")
        values["symmetry_labels"] = [row["OrbitalSymLabel"] for row in rows]
    matrices = {}
    if unrestricted:
        midpoint = n_mo // 2
        for label, start, end in (("alpha", 0, midpoint), ("beta", midpoint, n_mo)):
            metadata[label] = {key: value[start:end] for key, value in values.items()}
            matrices[f"mo_coefficients_{label}"] = coefficients[start:end]
    else:
        metadata.update(values)
        matrices["mo_coefficients"] = coefficients
    return basis, metadata, matrices


def add_molecule(builder: QVFArchiveBuilder, data: dict[str, Any]) -> None:
    """Add structure and any supported real s–f wavefunction from ORCA JSON.

    ``data`` is the complete JSON object, containing ``Molecule``. Geometry
    needs no orbital/basis information. When MO coefficients are present,
    unknown spin, units or shells raise instead of displaying partial MOs.
    The supplied dictionary is never changed.
    """
    molecule = data.get("Molecule")
    if not isinstance(molecule, dict):
        raise ValueError("ORCA JSON must contain a Molecule object")
    atoms = molecule.get("Atoms")
    if not isinstance(atoms, list) or not atoms or not all(isinstance(a, dict) for a in atoms):
        raise ValueError("ORCA Molecule must contain a nonempty Atoms array")
    unit = str(molecule.get("CoordinateUnits", "")).lower()
    if unit in ("angs", "angstrom", "angstroms"):
        scale = 1.0
    elif unit in ("bohr", "bohrs", "au", "a.u."):
        scale = BOHR_TO_ANGSTROM
    else:
        raise ValueError(f"Unsupported ORCA CoordinateUnits {unit!r}")
    converted = []
    for atom in atoms:
        number = atom.get("ElementNumber")
        if isinstance(number, bool) or not isinstance(number, int) or number not in Z_TO_SYMBOL:
            raise ValueError("ORCA ElementNumber must identify an element")
        position = _array(atom.get("Coords"), (3,), "Coords") * scale
        converted.append(
            {"atomic_number": number, "symbol": Z_TO_SYMBOL[number], "position": position.tolist()}
        )
    wavefunction = _orbital_data(molecule, atoms)
    builder.add_json(
        "structure",
        "structure",
        "structure",
        {"atoms": converted, "pbc": [False] * 3, "dimensionality": 0},
    )
    if wavefunction is not None:
        basis, metadata, matrices = wavefunction
        builder.add_json("wavefunction", "wavefunction.gto", "basis", basis)
        builder.add_json("wavefunction", "wavefunction.gto", "mo_metadata", metadata)
        for name, matrix in matrices.items():
            builder.add_binary("wavefunction", "wavefunction.gto", name, matrix)
    provenance = dict(builder.manifest.get("provenance", {}))
    for source, target in (
        ("Charge", "charge"),
        ("Multiplicity", "multiplicity"),
        ("PointGroup", "point_group"),
    ):
        if source in molecule:
            provenance[target] = molecule[source]
    builder.set_provenance(provenance)
