"""Validated Cartesian table/XYZ operations; storage is always angstrom."""
from __future__ import annotations

from typing import Any

import numpy as np

from vibeview.elements import SYMBOL_TO_Z
from vibeview.qvf import StructureData
from vibeview.units import LENGTH_UNITS, length_unit


def table_rows(structure: StructureData, unit: str = "angstrom",
               frozen: list[int] | None = None) -> list[dict[str, Any]]:
    factor = LENGTH_UNITS[length_unit(unit)][1]
    return [{"element": a.symbol, "x": float(a.position[0]*factor),
             "y": float(a.position[1]*factor), "z": float(a.position[2]*factor),
             "frozen": i in (frozen or [])} for i, a in enumerate(structure.atoms)]


def parse_rows(rows: list[dict[str, Any]], unit: str = "angstrom"
               ) -> tuple[list[str], np.ndarray, list[int]]:
    if not rows:
        raise ValueError("A coordinate table must contain at least one atom")
    if unit not in LENGTH_UNITS:
        raise ValueError("Choose angstrom or bohr")
    symbols, positions, frozen = [], [], []
    for i, row in enumerate(rows):
        symbol = str(row.get("element", "")).strip().capitalize()
        if symbol not in SYMBOL_TO_Z:
            raise ValueError(f"Unknown element in row {i+1}: {symbol}")
        symbols.append(symbol)
        positions.append([float(row[k]) / LENGTH_UNITS[unit][1] for k in ("x", "y", "z")])
        flag = row.get("frozen", False)
        if not isinstance(flag, bool):
            raise ValueError("Frozen flags must be true or false")
        if flag:
            frozen.append(i)
    coords = np.asarray(positions, dtype=float)
    if not np.isfinite(coords).all():
        raise ValueError("Coordinates must be finite")
    return symbols, coords, frozen


def xyz_rows(text: str, unit: str = "angstrom") -> list[dict[str, Any]]:
    """Read exactly one standard XYZ frame in the explicitly selected length unit."""
    lines = text.strip().splitlines()
    try:
        count = int(lines[0])
    except (ValueError, IndexError) as exc:
        raise ValueError("XYZ requires an atom count, comment line and atom rows") from exc
    if count < 1 or len(lines) != count + 2:
        raise ValueError("Paste exactly one XYZ frame with its declared atom count")
    rows = []
    for line in lines[2:]:
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("XYZ rows require element x y z")
        rows.append(dict(zip(("element", "x", "y", "z"), fields, strict=True), frozen=False))
    parse_rows(rows, unit)
    return rows
