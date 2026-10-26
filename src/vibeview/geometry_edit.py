"""Exact internal-coordinate edits using rigid connected fragments.

Rotations use Rodrigues' axis-angle formula; coordinates remain in angstroms.
This is geometric preparation, not a force-field relaxation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from vibeview.bonds import structure_bonds
from vibeview.qvf import QVFReader, StructureData


def parameter_value(positions: np.ndarray, indices: Sequence[int]) -> float:
    """Distance in Å or angle/dihedral in degrees, in the picked atom order."""
    p = np.asarray(positions, dtype=float)[list(indices)]
    if len(p) == 2:
        return float(np.linalg.norm(p[1] - p[0]))
    if len(p) == 3:
        a, b = p[0] - p[1], p[2] - p[1]
        norm = float(np.linalg.norm(a) * np.linalg.norm(b))
        if norm < 1e-12:
            raise ValueError("Angle is undefined for coincident atoms")
        return float(np.degrees(np.arccos(np.clip(np.dot(a, b) / norm, -1, 1))))
    if len(p) == 4:
        a, b, c = np.diff(p, axis=0)
        n, m = np.cross(a, b), np.cross(b, c)
        if min(np.linalg.norm(n), np.linalg.norm(m)) < 1e-12:
            raise ValueError("Dihedral is undefined for collinear atoms")
        return float(
            np.degrees(np.arctan2(np.dot(np.cross(n, b / np.linalg.norm(b)), m), np.dot(n, m)))
        )
    raise ValueError("Select two, three or four distinct atoms")


def set_parameter(
    structure: StructureData,
    indices: Sequence[int],
    value: float,
    *,
    side: str = "smaller",
    frozen: Sequence[int] = (),
) -> np.ndarray:
    """Return edited positions without mutating the input.

    ``side`` is smaller (ties move the last side), other, or last (last atom
    only). A cyclic cut is refused even for last-atom edits: those would
    silently distort the ring. Frozen atoms may lie on the rotation axis,
    but any nonzero movement of a frozen atom refuses the whole operation.
    """
    ids = list(indices)
    n = len(structure.atoms)
    if len(ids) not in (2, 3, 4) or len(set(ids)) != len(ids) or any(i < 0 or i >= n for i in ids):
        raise ValueError("Select two, three or four distinct valid atoms")
    if (
        not np.isfinite(value)
        or (len(ids) == 2 and value <= 0)
        or (len(ids) == 3 and not 0 < value < 180)
    ):
        raise ValueError("Use a positive distance or an angle strictly between 0 and 180 degrees")
    if side not in {"smaller", "other", "last"}:
        raise ValueError("Moving side must be smaller, other or last")
    positions = np.array([a.position for a in structure.atoms], dtype=float)
    current = parameter_value(positions, ids)
    bonds = structure_bonds(structure)
    if any(any(b[3]) for b in bonds):
        raise ValueError("Exact fragment edits require a molecule without periodic image bonds")
    graph: dict[int, set[int]] = {i: set() for i in range(n)}
    for i, j, _, _ in bonds:
        graph[i].add(j)
        graph[j].add(i)

    def component(start: int, cut: tuple[int, int]) -> set[int]:
        seen, pending = {start}, [start]
        while pending:
            node = pending.pop()
            for neighbor in graph[node]:
                if {node, neighbor} == set(cut) or neighbor in seen:
                    continue
                seen.add(neighbor)
                pending.append(neighbor)
        return seen

    left_cut = (ids[0], ids[1])
    right_cut = (ids[-2], ids[-1]) if len(ids) < 4 else (ids[1], ids[2])
    if len(ids) == 4:
        left_cut = right_cut
    left = component(ids[0], left_cut)
    right = component(ids[-1], right_cut)
    if left & right:
        raise ValueError("Ring or connected alternate path: cannot edit without distortion")
    move_right = len(right) <= len(left)
    if side == "other":
        move_right = not move_right
    moving = {ids[-1]} if side == "last" else right if move_right else left
    if side == "last":
        move_right = True
    result = positions.copy()
    if len(ids) == 2:
        axis = positions[ids[1]] - positions[ids[0]]
        norm = np.linalg.norm(axis)
        if norm < 1e-12:
            raise ValueError("Distance direction is undefined for coincident atoms")
        result[list(moving)] += axis / norm * (value - current) * (1 if move_right else -1)
    else:
        center = positions[ids[1]]
        if len(ids) == 3:
            axis = np.cross(positions[ids[0]] - center, positions[ids[2]] - center)
        else:
            axis = positions[ids[2]] - center
        norm = np.linalg.norm(axis)
        if norm < 1e-12:
            raise ValueError("Rotation axis is undefined for collinear atoms")
        axis /= norm

        def rotated(degrees: float) -> np.ndarray:
            out = positions.copy()
            points = positions[list(moving)] - center
            radians = np.radians(degrees)
            out[list(moving)] = (
                center
                + points * np.cos(radians)
                + np.cross(axis, points) * np.sin(radians)
                + np.outer(points @ axis, axis) * (1 - np.cos(radians))
            )
            return out

        # Determine sign against the public measurement convention, including
        # the opposite fragment and the signed-dihedral branch cut.
        trial = parameter_value(rotated(0.01), ids)
        derivative = (trial - current + 180) % 360 - 180
        if abs(derivative) < 1e-8:
            raise ValueError("The chosen fragment cannot change this parameter")
        delta = value - current
        if len(ids) == 4:
            delta = (delta + 180) % 360 - 180
        result = rotated(delta * (1 if derivative > 0 else -1))
    held = [i for i in frozen if 0 <= i < n]
    if held and not np.allclose(result[held], positions[held], atol=1e-10, rtol=0):
        raise ValueError("The chosen movement would move frozen atoms; choose another side")
    return result


def edited_bonds(
    structure: StructureData, i: int, j: int, order: float
) -> list[tuple[int, int, float, tuple[int, int, int]]]:
    """Explicit connectivity after adding/changing a bond, or removing with zero."""
    if i == j or min(i, j) < 0 or max(i, j) >= len(structure.atoms):
        raise ValueError("Select two distinct valid atoms")
    if not np.isfinite(order) or order not in (0, 1, 1.5, 2, 3):
        raise ValueError("Bond order must be 0 (remove), 1, 1.5, 2 or 3")
    bonds = [b for b in structure_bonds(structure) if {b[0], b[1]} != {i, j} or any(b[3])]
    if order:
        bonds.append((min(i, j), max(i, j), float(order), (0, 0, 0)))
    return bonds


def _integer(value: Any) -> int | None:
    """A stated integer charge/multiplicity, or None when absent or not one."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else None


def geometry_archive(
    structure: StructureData, *, provenance: Mapping[str, Any] | None = None,
    source_reader: QVFReader | None = None
) -> bytes:
    """Save edited geometry and explicit bonds, without stale computed results.

    ``provenance`` is the source archive's: its charge and multiplicity
    describe the species, not a calculation, so they stay with the geometry
    (vibe-sketch and the calculation panel read them, #89). They used to be
    dropped, so a saved ion or radical came back unspecified (#137). Other
    provenance (method, basis, energies) is a stale result and is left out.
    """
    from vibeview.qvf_writer import structure_archive

    source = provenance or {}

    bonds = (
        None
        if structure.bonds is None
        else [
            {"i": i, "j": j, "order": order, "image": list(image)}
            for i, j, order, image in structure.bonds
        ]
    )
    builder = structure_archive(
        [
            {
                "symbol": a.symbol,
                "atomic_number": a.atomic_number,
                "position": a.position.tolist(),
            }
            for a in structure.atoms
        ],
        structure.pbc,
        structure.lattice_vectors,
        bonds=bonds,
        calculation="Edited geometry",
        charge=_integer(source.get("charge")),
        multiplicity=_integer(source.get("multiplicity")),
    )
    if source_reader is not None:
        from vibeview.sketch_return import preserve_origin

        preserve_origin(builder, source_reader)
    return builder.to_bytesio().getvalue()
