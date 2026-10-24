"""Supplied fchk observables and density-derived natural-orbital representations."""
from __future__ import annotations

import re
from typing import Any

import numpy as np

from vibeview.qvf_writer import QVFArchiveBuilder


def unpack(values: Any, size: int) -> np.ndarray:
    packed = np.asarray(values, dtype=float)
    if packed.shape != (size*(size+1)//2,) or not np.isfinite(packed).all():
        raise ValueError("Packed symmetric matrix has wrong length or nonfinite entries")
    result = np.zeros((size, size))
    lower = np.tril_indices(size)
    result[lower] = packed
    result[(lower[1], lower[0])] = packed
    return result


def density_orbitals(density: np.ndarray, canonical: np.ndarray
                     ) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    """Transform AO density into the supplied orthonormal canonical-MO space.

    P_AO = C.T D_MO C. The pseudoinverse permits a complete independent orbital
    space smaller than the AO count; a reconstruction check rejects missing
    occupied subspaces. Symmetric diagonalization produces the natural orbitals.
    """
    inverse = np.linalg.pinv(canonical, rcond=1e-12)
    transformed = inverse.T @ density @ inverse
    transformed = (transformed+transformed.T)/2
    residual = float(np.linalg.norm(canonical.T@transformed@canonical-density) /
                     max(np.linalg.norm(density), 1e-30))
    if residual > 1e-6:
        raise ValueError("Supplied MO space cannot represent the fchk AO density")
    occupations, vectors = np.linalg.eigh(transformed)
    # Eight-digit fchk coefficients/density records incur small negative zeros.
    if np.min(occupations) < -1e-6 or np.max(occupations) > 2+1e-6:
        raise ValueError("Supplied density is not a positive electron one-particle density")
    corrected = np.clip(occupations, 0, 2)
    adjustment = float(np.max(np.abs(corrected-occupations)))
    order = np.argsort(corrected)[::-1]
    return vectors[:, order].T @ canonical, corrected[order], {
        "relative_ao_reconstruction_residual": residual,
        "maximum_occupation_roundoff_adjustment": adjustment,
        "source_precision_occupation_tolerance": 1e-6,
    }


def add_properties(builder: QVFArchiveBuilder, data: dict[str, Any],
                   atoms: list[dict[str, Any]], basis: dict[str, Any],
                   order: list[int], scales: np.ndarray, canonical: np.ndarray) -> None:
    n = len(atoms)
    for source in data:
        if not source.endswith(" Charges"):
            continue
        member = ("mulliken_charge" if source == "Mulliken Charges" else
                  "custom."+re.sub('[^a-z0-9]+', '_', source.lower()).strip('_'))
        values = np.asarray(data[source], dtype=float)
        if values.shape != (n,) or not np.isfinite(values).all():
            raise ValueError(f"Invalid fchk {source}")
        sid = 'fchk_'+member.replace('.', '_')
        builder.add_binary(sid, "atom_properties", member, values,
                           section_extra={"structure_ref": "structure", "label": source})
        if member.startswith('custom.'):
            builder.add_json(sid, "atom_properties", "meta", {member: {
                "units": "e", "description": source, "kind": "scalar"}})
    if "Dipole Moment" in data:
        vector = np.asarray(data["Dipole Moment"], dtype=float)
        if vector.shape != (3,) or not np.isfinite(vector).all():
            raise ValueError("Invalid fchk dipole vector")
        vector *= 2.541746473  # same atomic-unit/debye convention as ORCA importer
        builder.set_root("dipole_moment", {"vector_debye": vector.tolist(),
                                          "total_debye": float(np.linalg.norm(vector)),
                                          "origin": [0., 0., 0.]})
    for name, packed in data.items():
        if not (name.startswith("Total ") and name.endswith(" Density")):
            continue
        density = unpack(packed, len(order))[np.ix_(order, order)] * np.outer(scales, scales)
        coeff, occupations, diagnostics = density_orbitals(density, canonical)
        sid = 'fchk_density_'+re.sub('[^a-z0-9]+', '_', name.lower()).strip('_')
        builder.add_json(sid, "wavefunction.gto", "basis", basis,
                         section_extra={"label": f"{name}: derived natural orbitals"})
        builder.add_json(sid, "wavefunction.gto", "mo_metadata", {
            "n_ao": len(order), "spin": "restricted", "orbital_kind": "natural",
            "occupation_semantics": "electron_occupation", "occupations": occupations.tolist(),
            "density_source": {"record": name, "derivation": "canonical-MO density diagonalization",
                               "packed_source_ao_density": packed, **diagnostics},
        })
        builder.add_binary(sid, "wavefunction.gto", "mo_coefficients", coeff)
        spin_name = name.replace("Total ", "Spin ", 1)
        if spin_name in data:
            spin = unpack(data[spin_name], len(order))[np.ix_(order, order)]
            spin *= np.outer(scales, scales)
            spin_sid = sid+"_spin"
            spin_metadata: dict[str, Any] = {
                "n_ao": len(order), "spin": "unrestricted", "orbital_kind": "natural",
                "occupation_semantics": "electron_occupation",
                "density_source": {"total_record": name, "spin_record": spin_name,
                                   "packed_source_spin_density": data[spin_name]},
            }
            builder.add_json(spin_sid, "wavefunction.gto", "basis", basis,
                             section_extra={"label": f"{name} + {spin_name}"})
            for channel, sign in (("alpha", 1), ("beta", -1)):
                orbitals, counts, check = density_orbitals((density+sign*spin)/2, canonical)
                if np.max(counts) > 1+1e-6:
                    raise ValueError("Supplied spin density exceeds one electron per spin orbital")
                spin_metadata[channel] = {"occupations": counts.tolist(), "density_check": check}
                builder.add_binary(spin_sid, "wavefunction.gto", "mo_coefficients_"+channel,
                                   orbitals)
            builder.add_json(spin_sid, "wavefunction.gto", "mo_metadata", spin_metadata)
    hessian = None
    if "Cartesian Force Constants" in data:
        hessian = unpack(data["Cartesian Force Constants"], 3*n)
    if "Vib-Modes" in data or "Vib-E2" in data:
        if not all(key in data for key in ("Vib-Modes", "Vib-E2", "Number of Normal Modes")):
            raise ValueError("Incomplete formatted-checkpoint normal mode records")
        count = int(data["Number of Normal Modes"])
        values = np.asarray(data["Vib-E2"], dtype=float)
        modes = np.asarray(data["Vib-Modes"], dtype=float)
        if count < 1 or values.size < 4*count or modes.size != 3*n*count:
            raise ValueError("Formatted-checkpoint vibration dimensions disagree")
        frequencies = values[:count]
        displacements = modes.reshape(count, n, 3)
        origin = "stored fchk Cartesian normal modes"
    elif hessian is not None:
        masses = np.asarray(data.get("Real atomic weights", []), dtype=float)
        if masses.shape != (n,) or np.any(masses <= 0):
            raise ValueError("Hessian mode derivation needs positive real atomic weights")
        # CODATA atomic mass/electron mass and Hartree/hc; all 3N modes are
        # retained, including near-zero rotations/translations and imaginary modes.
        weights = np.repeat(np.sqrt(masses*1822.888486209), 3)
        eigenvalues, vectors = np.linalg.eigh(hessian / np.outer(weights, weights))
        frequencies = np.sign(eigenvalues)*np.sqrt(np.abs(eigenvalues))*219474.6313632
        displacements = (vectors.T/weights).reshape(3*n, n, 3)
        origin = "mass-weighted Cartesian Hessian eigensystem; all 3N modes"
    else:
        return
    metadata: dict[str, Any] = {"atoms": atoms, "frequencies": frequencies.tolist(),
                                "fchk": {"displacement_convention": origin}}
    if hessian is not None:
        metadata["fchk"]["cartesian_hessian_hartree_per_bohr2"] = hessian.tolist()
    builder.add_json("fchk_vibrations", "vibrations", "metadata", metadata,
                     section_extra={"structure_ref": "structure", "label": "FCHK vibrations"})
    builder.add_binary("fchk_vibrations", "vibrations", "displacements", displacements)
