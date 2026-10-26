"""Internal-coordinate geometry with explicit reference atoms and dummy anchors.

Conventions: FACCTs ORCA manual, Input of Coordinates (int/gzmt); MOPAC
manual, Internal Coordinate Definition and Specification of Gaussian Z-matrices.
Distances are angstrom and angles degrees. No expression evaluation is used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import permutations
from typing import Literal

import numpy as np

from vibeview.elements import SYMBOL_TO_Z


@dataclass(frozen=True)
class ZRow:
    symbol: str
    references: tuple[int, ...] = ()  # zero-based preceding rows, including X
    values: tuple[float, ...] = ()


@dataclass(frozen=True)
class ZMatrix:
    rows: tuple[ZRow, ...]

    def cartesian(self, *, include_dummies: bool = False) -> tuple[list[str], np.ndarray]:
        """Construct geometry; reject ambiguous collinear reference planes."""
        positions: list[np.ndarray] = []
        symbols: list[str] = []
        for i, row in enumerate(self.rows):
            n = min(i, 3)
            if len(row.references) != n or len(row.values) != n:
                raise ValueError(f"Z-matrix row {i+1} requires {n} references and values")
            if len(set(row.references)) != n or any(j < 0 or j >= i for j in row.references):
                raise ValueError("Z-matrix references must be distinct preceding rows")
            if not np.isfinite(row.values).all():
                raise ValueError("Z-matrix values must be finite")
            if row.symbol not in SYMBOL_TO_Z and row.symbol != "X":
                raise ValueError(f"Unknown Z-matrix element {row.symbol}")
            if not n:
                point = np.zeros(3)
            else:
                radius = row.values[0]
                if radius <= 0:
                    raise ValueError("Z-matrix distances must be positive")
                a = positions[row.references[0]]
                if n == 1:
                    point = a + np.array([0., 0., radius])
                else:
                    angle = row.values[1]
                    if not 0 <= angle <= 180:
                        raise ValueError("Z-matrix bond angles must lie between 0 and 180")
                    b = positions[row.references[1]]
                    axis = _unit(b-a)
                    if n == 2:
                        trial = np.eye(3)[int(np.argmin(np.abs(axis)))]
                        side = _unit(trial - np.dot(trial, axis)*axis)
                        phi = 0.
                    else:
                        c = positions[row.references[2]]
                        side = _unit(c-b - np.dot(c-b, axis)*axis)
                        phi = np.radians(row.values[2])
                    normal = np.cross(axis, side)
                    theta = np.radians(angle)
                    point = a + radius*(np.cos(theta)*axis + np.sin(theta)*(
                        np.cos(phi)*side + np.sin(phi)*normal))
            positions.append(point)
            symbols.append(row.symbol)
        keep = [i for i, symbol in enumerate(symbols) if include_dummies or symbol != "X"]
        return [symbols[i] for i in keep], np.asarray(positions)[keep].reshape(-1, 3)

    def text(self, style: Literal["gaussian", "orca", "mopac"] = "gaussian",
             *, variables: bool = False) -> str:
        """Export a coordinate block; Gaussian variables use explicit definitions."""
        if style not in {"gaussian", "orca", "mopac"}:
            raise ValueError("Choose gaussian, orca or mopac Z-matrix style")
        if variables and style != "gaussian":
            raise ValueError("Variable export uses Gaussian-style Z-matrices")
        lines, definitions = [], []
        for i, row in enumerate(self.rows):
            refs = [r+1 for r in row.references]
            values = [f"{v:.12g}" for v in row.values]
            if variables:
                for j, value in enumerate(values):
                    key = f"{'RAD'[j]}{i+1}"
                    definitions.append(f"{key}={value}")
                    values[j] = key
            if style == "gaussian":
                fields = [row.symbol]
                for ref, value in zip(refs, values, strict=True):
                    fields += [str(ref), value]
            else:
                refs += [0]*(3-len(refs))
                values += ["0"]*(3-len(values))
                if style == "orca":
                    fields = [row.symbol, *map(str, refs), *values]
                else:
                    fields = [row.symbol, values[0], "0", values[1], "0", values[2], "0",
                              *map(str, refs)]
            lines.append(" ".join(fields))
        return "\n".join(lines + (["", *definitions] if definitions else [])) + "\n"


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-10:
        raise ValueError("Degenerate Z-matrix reference plane; add a dummy atom")
    return vector/norm


def from_cartesian(symbols: list[str], positions: np.ndarray,
                   bonds: list[tuple[int, int]] | None = None) -> ZMatrix:
    """Generate connectivity-preferred references with three explicit X anchors.

    Anchors supply a noncollinear reference plane even for linear/disconnected
    fragments. Real atom ordering is retained; rings need no fictitious closure
    coordinate. Their non-tree bonds remain a property of the original structure.
    """
    coords = np.asarray(positions, dtype=float)
    if coords.shape != (len(symbols), 3) or not len(symbols) or not np.isfinite(coords).all():
        raise ValueError("Provide finite Cartesian coordinates for every atom")
    origin = coords.min(axis=0) - np.array([2., 3., 4.])
    all_coords = [origin, origin + [1, 0, 0], origin + [0, 1, 0], *coords]
    rows = [ZRow("X"), ZRow("X", (0,), (1.,)), ZRow("X", (0, 1), (1., 90.))]
    graph: dict[int, set[int]] = {i: set() for i in range(len(symbols))}
    for a, b in bonds or []:
        if a not in graph or b not in graph or a == b:
            raise ValueError("Invalid connectivity index")
        graph[a].add(b)
        graph[b].add(a)
    for atom, (symbol, point) in enumerate(zip(symbols, coords, strict=True)):
        i = atom + 3
        bonded = [j+3 for j in sorted(graph[atom]) if j < atom]
        candidates = bonded + [j for j in range(i-1, -1, -1) if j not in bonded]
        chosen = None
        for a in candidates:
            v = point-all_coords[a]
            radius = float(np.linalg.norm(v))
            if radius < 1e-10:
                continue
            for b, c in permutations((j for j in range(i) if j != a), 2):
                try:
                    axis = _unit(all_coords[b]-all_coords[a])
                    w = all_coords[c]-all_coords[b]
                    side = _unit(w-np.dot(w, axis)*axis)
                except ValueError:
                    continue
                theta = float(np.degrees(np.arccos(np.clip(np.dot(v, axis)/radius, -1, 1))))
                phi = float(np.degrees(np.arctan2(
                    np.dot(v, np.cross(axis, side)), np.dot(v, side))))
                chosen = ZRow(symbol, (a, b, c), (radius, theta, phi))
                break
            if chosen is not None:
                break
        if chosen is None:
            raise ValueError("Could not construct a nondegenerate reference plane")
        rows.append(chosen)
    result = ZMatrix(tuple(rows))
    result.cartesian()  # validate element identities and references before export
    return result


def parse_zmatrix(text: str, style: Literal["gaussian", "orca", "mopac"] = "gaussian") -> ZMatrix:
    """Read a coordinate block and signed numeric variables; no executable expressions."""
    lines = [line.split("!")[0].strip().replace(",", " ") for line in text.splitlines()]
    variables: dict[str, float] = {}
    geometry = []
    for line in lines:
        if not line or line.lower() in {"variables:", "constants:"}:
            continue
        match = re.fullmatch(r"([A-Za-z]\w*)\s*(?:=|\s)\s*([+-]?[\d.]+(?:[eEdD][+-]?\d+)?)", line)
        if match:
            variables[match[1]] = float(match[2].replace("D", "E").replace("d", "e"))
        else:
            geometry.append(line.split())
    def number(token: str) -> float:
        try:
            return float(token.replace("D", "E").replace("d", "e"))
        except ValueError:
            sign = -1 if token.startswith("-") else 1
            key = token.lstrip("+-")
            if key not in variables:
                raise ValueError(f"Undefined Z-matrix variable {token}") from None
            return sign * variables[key]
    rows = []
    labels: dict[str, int] = {}
    for i, fields in enumerate(geometry):
        count = min(i, 3)
        symbol_match = re.fullmatch(r"([A-Za-z]{1,2})\d*", fields[0])
        if symbol_match is None:
            raise ValueError("Invalid Z-matrix atom label")
        symbol = symbol_match[1].capitalize()
        if style == "gaussian":
            if len(fields) != 1 + 2*count:
                raise ValueError("Invalid Gaussian Z-matrix row")
            ref_tokens, value_tokens = fields[1::2], fields[2::2]
        elif style == "orca" and len(fields) == 7:
            ref_tokens, value_tokens = fields[1:1+count], fields[4:4+count]
        elif style == "mopac" and len(fields) == 10:
            ref_tokens, value_tokens = fields[7:7+count], fields[1:7:2][:count]
        else:
            raise ValueError("Invalid Z-matrix style or coordinate row")
        refs = tuple(labels[token] if token in labels else int(token)-1 for token in ref_tokens)
        rows.append(ZRow(symbol, refs, tuple(number(token) for token in value_tokens)))
        labels[fields[0]] = i
    if not rows:
        raise ValueError("Empty Z-matrix")
    result = ZMatrix(tuple(rows))
    result.cartesian()
    return result
