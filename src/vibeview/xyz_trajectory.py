"""Native XYZ frame reading, including the libAtoms extended-XYZ layout.

Extended fields follow https://github.com/libAtoms/extxyz : Properties column
triples, row lattice vectors, and energy in eV unless explicitly labelled.
Unlabelled free-text comments are retained, never guessed to be energies.
"""
from __future__ import annotations

import io
import re
from typing import Any

import numpy as np

from vibeview.elements import SYMBOL_TO_Z
from vibeview.qvf_writer import QVFArchiveBuilder, structure_payload
from vibeview.units import HARTREE_TO_EV

_FIELDS = re.compile(r'''(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+))''')


def xyz_frames(text: str, name: str = "xyz") -> io.BytesIO:
    """Read complete frames without silently discarding malformed later data."""
    lines = text.splitlines()
    frames: list[list[dict[str, Any]]] = []
    comments: list[str] = []
    cells: list[list[list[float]] | None] = []
    flags: list[list[bool]] = []
    energies: list[float | None] = []
    charges: list[dict[str, int]] = []
    cursor = 0
    while cursor < len(lines):
        if cursor and not lines[cursor].strip():
            cursor += 1
            continue
        try:
            count = int(lines[cursor])
        except ValueError as exc:
            raise ValueError(f"invalid XYZ atom count at line {cursor + 1}") from exc
        if count <= 0:
            raise ValueError("no atoms in XYZ frame: atom count must be positive")
        if cursor + count + 2 > len(lines):
            raise ValueError("truncated XYZ frame or missing comment line")
        comment = lines[cursor + 1]
        fields = {m[0].lower(): next((v for v in m[1:] if v), "") for m in _FIELDS.findall(comment)}
        columns = fields.get("properties", "species:S:1:pos:R:3").split(":")
        layout: dict[str, tuple[int, int]] = {}
        offset = 0
        try:
            if len(columns) % 3:
                raise ValueError("incomplete property triple")
            for i in range(0, len(columns), 3):
                kind = columns[i + 1]
                if columns[i].lower() in {"species", "pos"} and kind != (
                    "S" if columns[i].lower() == "species" else "R"
                ):
                    raise ValueError("invalid species/pos property type")
                width = int(columns[i + 2])
                if width < 1:
                    raise ValueError("invalid property width")
                layout[columns[i].lower()] = offset, width
                offset += width
            si, sw = layout["species"]
            pi, pw = layout["pos"]
            if sw != 1 or pw != 3:
                raise ValueError("species/pos width")
        except (ValueError, KeyError) as exc:
            raise ValueError("invalid XYZ Properties: require species:S:1 and pos:R:3") from exc
        atoms: list[dict[str, Any]] = []
        for line in lines[cursor + 2:cursor + count + 2]:
            row = line.split()
            try:
                if len(row) < offset:
                    raise ValueError("missing columns")
                symbol = row[si].capitalize()
                position = [float(v) for v in row[pi:pi + 3]]
                if not np.isfinite(position).all():
                    raise ValueError("non-finite position")
            except (ValueError, IndexError) as exc:
                raise ValueError("invalid XYZ atom record or coordinates") from exc
            atoms.append({"symbol": symbol, "atomic_number": SYMBOL_TO_Z.get(symbol, 0),
                          "position": position})
        cell = None
        pbc = [False] * 3
        try:
            if "lattice" in fields:
                array = np.asarray([float(v) for v in fields["lattice"].split()]).reshape(3, 3)
                if not np.isfinite(array).all() or abs(float(np.linalg.det(array))) < 1e-12:
                    raise ValueError("invalid lattice")
                cell = array.tolist()
                pbc = [True] * 3
            if "pbc" in fields:
                values = fields["pbc"].lower().split()
                allowed = {"t", "f", "true", "false", "1", "0"}
                if len(values) != 3 or any(v not in allowed for v in values):
                    raise ValueError("invalid pbc")
                pbc = [v in {"t", "true", "1"} for v in values]
            if any(pbc) and cell is None:
                raise ValueError("periodicity requires Lattice")
            energy = None
            if "energy" in fields:
                energy = float(fields["energy"])
                unit = fields.get("energy_unit", "ev").lower()
                if unit == "ev":
                    energy /= HARTREE_TO_EV
                elif unit not in {"hartree", "ha", "au"}:
                    raise ValueError("unsupported energy_unit")
                if not np.isfinite(energy):
                    raise ValueError("non-finite energy")
            charge = {k: int(fields[k]) for k in ("charge", "multiplicity") if k in fields}
            if charge.get("multiplicity", 1) < 1:
                raise ValueError("multiplicity must be positive")
        except ValueError as exc:
            raise ValueError(f"invalid XYZ comment metadata: {exc}") from exc
        frames.append(atoms)
        comments.append(comment)
        cells.append(cell)
        flags.append(pbc)
        energies.append(energy)
        charges.append(charge)
        cursor += count + 2
    if not frames:
        raise ValueError("empty XYZ file")
    builder = QVFArchiveBuilder(calculation=f"xyz:{name}")
    # Preserve the existing default first-structure choice. Every later geometry
    # remains independently addressable, including changed identities or cells.
    for i, atoms in enumerate(frames):
        builder.add_json("structure" if i == 0 else f"xyz_frame_{i}", "structure", "structure",
                         structure_payload(atoms, flags[i], cells[i]),
                         section_extra={"label": f"XYZ frame {i + 1}"})
    builder.set_provenance({**charges[0], "xyz_comments": comments,
                            "xyz_frame_states": charges, "xyz_energies_hartree": energies})
    identities = [[a["symbol"] for a in atoms] for atoms in frames]
    if len(frames) > 1 and all(row == identities[0] for row in identities):
        metadata: dict[str, Any] = {"atoms": frames[0], "coordinate_unit": "angstrom"}
        if all(value is not None for value in energies):
            metadata["energies"] = energies
        builder.add_json("xyz_trajectory", "trajectory", "metadata", metadata)
        builder.add_binary("xyz_trajectory", "trajectory", "coords",
                           np.asarray([[a["position"] for a in atoms] for atoms in frames]))
    return builder.to_bytesio()
