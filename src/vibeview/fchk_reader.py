"""Native Gaussian formatted-checkpoint records and real molecular orbitals.

Source contract: Gaussian formchk version 3 documentation (A40 labels, typed
scalar/array records, normalized primitive contractions and Gaussian AO order).
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np

from vibeview.elements import Z_TO_SYMBOL
from vibeview.exchange_basis import ao_transform, validate_shell
from vibeview.qvf_writer import QVFArchiveBuilder
from vibeview.units import BOHR_TO_ANGSTROM


def records(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    if len(lines) < 3:
        raise ValueError("Incomplete formatted checkpoint")
    result: dict[str, Any] = {"title": lines[0], "method_header": lines[1]}
    i = 2
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip():
            continue
        match = re.match(r"^(.{40})\s+([IRCLH])\s+(.*)$", line)
        if not match:
            raise ValueError(f"Invalid formatted-checkpoint record header at line {i}")
        label, kind, tail = match[1].strip(), match[2], match[3].strip()
        if label.lower() == "current cartesian coordinates":
            label = "Current Cartesian coordinates"
        if label in result:
            raise ValueError(f"Duplicate formatted-checkpoint record {label}")
        if tail.startswith("N="):
            count = int(tail[2:].strip())
            if count < 0 or count > 50_000_000:
                raise ValueError("Invalid formatted-checkpoint array length")
            values: list[Any] = []
            while len(values) < count:
                if i >= len(lines):
                    raise ValueError(f"Truncated formatted-checkpoint array {label}")
                payload = lines[i]
                i += 1
                if kind in ("C", "H"):
                    width = 12 if kind == "C" else 8
                    values.extend(payload[j:j+width] for j in range(0, len(payload), width))
                else:
                    values.extend(payload.split())
            if len(values) != count:
                raise ValueError(f"Array {label} disagrees with its declared length")
        else:
            values = [tail]
        try:
            converted = ([int(x) for x in values] if kind == "I" else
                         [float(x.replace("D", "E")) for x in values] if kind == "R" else values)
        except ValueError as exc:
            raise ValueError(f"Invalid numeric record {label}") from exc
        if kind == "R" and not np.isfinite(converted).all():
            raise ValueError(f"Nonfinite formatted-checkpoint record {label}")
        result[label] = converted if tail.startswith("N=") else converted[0]
    return result


def _required(data: dict[str, Any], name: str, size: int) -> np.ndarray:
    if name not in data:
        raise ValueError(f"Formatted checkpoint is missing {name}")
    array = np.asarray(data[name], dtype=float)
    if array.shape != (size,) or not np.isfinite(array).all():
        raise ValueError(f"Formatted checkpoint {name} must contain {size} finite values")
    return array


def add_fchk(builder: QVFArchiveBuilder, text: str) -> None:
    data = records(text)
    nat = int(data.get("Number of atoms", 0))
    if nat < 1:
        raise ValueError("Formatted checkpoint has no atoms")
    numbers = _required(data, "Atomic numbers", nat).astype(int)
    positions = _required(data, "Current Cartesian coordinates", 3*nat).reshape(nat, 3)
    if any(int(z) not in Z_TO_SYMBOL for z in numbers):
        raise ValueError("Unsupported ghost or unknown atom in formatted checkpoint")
    atoms = [{"symbol": Z_TO_SYMBOL[int(z)], "atomic_number": int(z),
              "position": (p*BOHR_TO_ANGSTROM).tolist()}
             for z, p in zip(numbers, positions, strict=True)]
    n_shell = int(data.get("Number of contracted shells", 0))
    types = _required(data, "Shell types", n_shell).astype(int)
    counts = _required(data, "Number of primitives per shell", n_shell).astype(int)
    centers = _required(data, "Shell to atom map", n_shell).astype(int)-1
    total = int(counts.sum())
    exponents = _required(data, "Primitive exponents", total)
    coefficients = _required(data, "Contraction coefficients", total)
    sp = _required(data, "P(S=P) Contraction coefficients", total) if -1 in types else None
    shells, start = [], 0
    for kind, count, center in zip(types, counts, centers, strict=True):
        if count < 1:
            raise ValueError("Each shell needs at least one primitive")
        end = start+int(count)
        angular = 0 if kind == -1 else abs(int(kind))
        shells.append(validate_shell(int(center), angular, kind < -1,
                                     exponents[start:end].tolist(),
                                     coefficients[start:end].tolist(), nat))
        if kind == -1:
            assert sp is not None
            shells.append(validate_shell(int(center), 1, False, exponents[start:end].tolist(),
                                         sp[start:end].tolist(), nat))
        start = end
    order, scales = ao_transform(shells)
    nao = len(order)
    if nao != int(data.get("Number of basis functions", 0)):
        raise ValueError("Formatted checkpoint basis function count does not match shells")
    unrestricted = "Beta MO coefficients" in data
    alpha = int(data.get("Number of alpha electrons", -1))
    beta = int(data.get("Number of beta electrons", -1))
    if min(alpha, beta) < 0:
        raise ValueError("Missing alpha/beta electron counts")
    metadata: dict[str, Any] = {"n_ao": nao,
                               "spin": "unrestricted" if unrestricted else "restricted"}
    matrices = {}
    for spin, count in (("Alpha", alpha), ("Beta", beta)) if unrestricted else (("Alpha", alpha),):
        energies = np.asarray(data.get(f"{spin} Orbital Energies", []), dtype=float)
        nmo = len(energies)
        if nmo < count or nmo > nao or (not unrestricted and nmo < beta):
            raise ValueError("Invalid formatted-checkpoint orbital/electron count")
        matrix = _required(data, f"{spin} MO coefficients", nmo*nao).reshape(nmo, nao)
        occupations = [int(i < count) + (int(i < beta) if not unrestricted else 0)
                       for i in range(nmo)]
        block = {"energies": energies.tolist(), "occupations": occupations}
        if unrestricted:
            metadata[spin.lower()] = block
        else:
            metadata.update(block)
        key = f"mo_coefficients_{spin.lower()}" if unrestricted else "mo_coefficients"
        matrices[key] = matrix[:, order] * scales
    builder.add_json("structure", "structure", "structure",
                     {"atoms": atoms, "pbc": [False]*3, "dimensionality": 0})
    builder.add_json("wavefunction", "wavefunction.gto", "basis",
                     {"structure_ref": "structure", "n_ao": nao, "shells": shells})
    builder.add_json("wavefunction", "wavefunction.gto", "mo_metadata", metadata)
    for key, matrix in matrices.items():
        builder.add_binary("wavefunction", "wavefunction.gto", key, matrix)
    from vibeview.fchk_properties import add_properties

    basis = {"structure_ref": "structure", "n_ao": nao, "shells": shells}
    canonical = matrices["mo_coefficients_alpha" if unrestricted else "mo_coefficients"]
    add_properties(builder, data, atoms, basis, order, scales, canonical)
    builder.set_provenance({"n_electrons": alpha+beta, "charge": int(data.get("Charge", 0)),
                            "multiplicity": int(data.get("Multiplicity", 1)),
                            "fchk": {"title": data["title"], "method": data["method_header"]}})
