"""Native real-GTO Molden reader, following the published Molden format.

https://www.theochem.ru.nl/molden/molden_format.html
Source-specific normalization deviations must be explicit, never guessed from
whether the resulting picture looks plausible.
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np

from vibeview.elements import Z_TO_SYMBOL
from vibeview.exchange_basis import ao_transform, validate_shell
from vibeview.qvf_writer import QVFArchiveBuilder
from vibeview.units import BOHR_TO_ANGSTROM


def _number(token: str) -> float:
    value = float(token.replace("D", "E").replace("d", "e"))
    if not np.isfinite(value):
        raise ValueError("Molden numeric values must be finite")
    return value


def add_molden(builder: QVFArchiveBuilder, text: str, *, natural: bool = False) -> None:
    """Import a conventional Molden file; explicit natural confirms Occup semantics."""
    sections: dict[str, tuple[str, list[str]]] = {}
    current: list[str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        match = re.match(r"^\[([^]]+)\](.*)$", line)
        if match:
            key = match[1].lower()
            if key in sections:
                raise ValueError(f"Duplicate Molden section [{key}]")
            current = []
            sections[key] = (match[2].strip(), current)
        elif current is not None and line:
            current.append(line)
    if "molden format" not in sections or not all(k in sections for k in ("atoms", "gto", "mo")):
        raise ValueError("Molden orbitals require [Molden Format], [Atoms], [GTO] and [MO]")
    unit = sections['atoms'][0].lower()
    if unit not in ("angs", "au", "(angs)", "(au)"):
        raise ValueError("Molden [Atoms] must explicitly specify Angs or AU")
    factor = BOHR_TO_ANGSTROM if "au" in unit else 1.
    atoms = []
    for expected, line in enumerate(sections['atoms'][1], 1):
        fields = line.split()
        if len(fields) != 6 or int(fields[1]) != expected:
            raise ValueError("Molden atom rows need consecutive indices and six fields")
        number = int(fields[2])
        if number not in Z_TO_SYMBOL:
            raise ValueError("Unsupported Molden ghost or unknown atom")
        atoms.append({"symbol": Z_TO_SYMBOL[number], "atomic_number": number,
                      "position": [_number(v)*factor for v in fields[3:]]})
    if not atoms:
        raise ValueError("Molden file has no atoms")
    orca = any("orca_2mkl" in line.lower() for line in sections.get('title', ('', []))[1])
    pure_d = any(key in sections for key in ("5d", "5d7f", "5d10f"))
    pure_f = any(key in sections for key in ("5d", "5d7f", "7f"))
    lines = sections['gto'][1]
    shells: list[dict[str, Any]] = []
    center, i = -1, 0
    while i < len(lines):
        fields = lines[i].split()
        i += 1
        if fields[0].isdigit():
            center = int(fields[0])-1
            if len(fields) > 2:
                raise ValueError("Malformed Molden atom block header")
            continue
        label = fields[0].lower()
        if label not in ('s', 'p', 'd', 'f', 'sp'):
            raise ValueError("Molden reader supports s, p, d, f and sp shells; "
                             "export higher shells as cubes")
        if len(fields) not in (2, 3):
            raise ValueError("Malformed Molden shell header")
        count = int(fields[1])
        if count < 1 or i+count > len(lines):
            raise ValueError("Truncated Molden primitive shell")
        primitives = [[_number(v) for v in row.split()] for row in lines[i:i+count]]
        if any(len(row) != (3 if label == 'sp' else 2) for row in primitives):
            raise ValueError("Molden primitive row has wrong coefficient count")
        i += count
        for col, component in enumerate(('s', 'p') if label == 'sp' else (label,), 1):
            angular = 'spdf'.index(component)
            pure = pure_d if angular == 2 else pure_f if angular == 3 else False
            alpha = np.array([p[0] for p in primitives])
            contraction = np.array([p[col] for p in primitives])
            if orca:
                from vibeview.renderers.wavefunction import _primitive_norm

                # ORCA's identified writer embeds axial primitive normalization
                # in GTO coefficients. Restore normalized-primitive coefficients.
                contraction /= np.array([_primitive_norm(angular, a) for a in alpha])
            # Molden writers may retain basis-library contraction coefficients;
            # AOs are normalized contractions of normalized primitives.
            overlaps = (2*np.sqrt(alpha[:, None]*alpha[None, :]) /
                        (alpha[:, None]+alpha[None, :]))**(angular+1.5)
            norm = float(contraction @ overlaps @ contraction)
            if norm <= 0 or not np.isfinite(norm):
                raise ValueError("Molden contraction has zero/nonfinite norm")
            contraction /= np.sqrt(norm)
            shells.append(validate_shell(center, angular, pure, alpha.tolist(),
                                         contraction.tolist(), len(atoms)))
    order, scales = ao_transform(shells, normalized_components=False)
    if orca:
        # ORCA uses opposite phases for the two spherical f(|m|=3) AOs,
        # also present in its native Molden export (ORCA manual 9.3.3).
        offset = 0
        for shell in shells:
            angular = shell['l']
            width = 2*angular+1 if shell['pure'] else (angular+1)*(angular+2)//2
            if angular == 3 and shell['pure']:
                scales[offset] *= -1
                scales[offset+width-1] *= -1
            offset += width
    nao = len(order)
    if not nao:
        raise ValueError("Molden basis has no functions")
    orbitals: list[dict[str, Any]] = []
    orbital: dict[str, Any] = {}
    coefficients: dict[int, float] = {}
    def finish() -> None:
        if not orbital and not coefficients:
            return
        if set(coefficients) != set(range(nao)):
            raise ValueError("Molden MO must contain each AO coefficient exactly once")
        if not all(key in orbital for key in ('ene', 'spin', 'occup')):
            raise ValueError("Molden MO requires Ene, Spin and Occup")
        orbitals.append({**orbital, 'coefficients': [coefficients[j] for j in range(nao)]})
    for line in sections['mo'][1]:
        if '=' in line:
            key, value = (part.strip() for part in line.split('=', 1))
            key = key.lower()
            if coefficients:
                finish()
                orbital, coefficients = {}, {}
            if key in orbital:
                raise ValueError(f"Duplicate Molden MO field {key}")
            orbital[key] = value
        else:
            fields = line.split()
            if len(fields) != 2:
                raise ValueError("Molden MO coefficient needs index and value")
            index = int(fields[0])-1
            if index in coefficients or index < 0 or index >= nao:
                raise ValueError("Invalid/duplicate Molden MO AO index")
            coefficients[index] = _number(fields[1])
    finish()
    if not orbitals:
        raise ValueError("Molden file has no molecular orbitals")
    spins = {str(row['spin']).lower() for row in orbitals}
    if not spins <= {'alpha', 'beta'} or 'alpha' not in spins:
        raise ValueError("Molden MOs require Alpha and optional Beta spin blocks")
    unrestricted = 'beta' in spins
    metadata: dict[str, Any] = {"n_ao": nao,
                               "spin": "unrestricted" if unrestricted else "restricted"}
    if natural:
        metadata.update({"orbital_kind": "natural", "occupation_semantics": "electron_occupation"})
    matrices = {}
    for spin in ('alpha', 'beta') if unrestricted else ('alpha',):
        rows = [r for r in orbitals if str(r['spin']).lower() == spin]
        if len(rows) > nao:
            raise ValueError("Molden MO count exceeds basis dimension")
        occupations = [_number(r['occup']) for r in rows]
        if any(n < 0 or n > (1 if unrestricted else 2)+1e-8 for n in occupations):
            raise ValueError("Molden orbital occupations exceed spin capacity")
        block = {"energies": [_number(r['ene']) for r in rows], "occupations": occupations,
                 "symmetry_labels": [r.get('sym', '') for r in rows]}
        if unrestricted:
            metadata[spin] = block
        else:
            metadata.update(block)
        key = f"mo_coefficients_{spin}" if unrestricted else "mo_coefficients"
        matrices[key] = np.asarray([r['coefficients'] for r in rows])[:, order]*scales
    builder.add_json("structure", "structure", "structure",
                     {"atoms": atoms, "pbc": [False]*3, "dimensionality": 0})
    builder.add_json("wavefunction", "wavefunction.gto", "basis",
                     {"structure_ref": "structure", "n_ao": nao, "shells": shells})
    builder.add_json("wavefunction", "wavefunction.gto", "mo_metadata", metadata)
    for key, matrix in matrices.items():
        builder.add_binary("wavefunction", "wavefunction.gto", key, matrix)
    builder.set_provenance({"molden": {"normalization": "Molden shell-wide Cartesian primitives",
                                       "natural_occupations_confirmed": natural}})
