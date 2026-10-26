"""Shared finite input-cell selections for molecular inspection and scripting."""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence

import numpy as np

from vibeview.qvf import StructureData


def positions(structure: StructureData) -> np.ndarray:
    points = np.asarray([a.position for a in structure.atoms], dtype=float).reshape(-1, 3)
    if not np.isfinite(points).all():
        raise ValueError("Atom coordinates must be finite")
    return points


def within(structure: StructureData, indices: set[int], radius: float) -> set[int]:
    """Atoms within radius Å, inclusive, including the seed; no periodic images."""
    if not np.isfinite(radius) or radius < 0:
        raise ValueError("Selection radius must be finite and non-negative")
    points = positions(structure)
    if any(i < 0 or i >= len(points) for i in indices):
        raise ValueError("Selection atom index is out of range")
    result: set[int] = set()
    # Chunking bounds memory even for large seed selections, without scipy.
    for i in sorted(indices):
        result.update(
            int(j) for j in np.flatnonzero(np.linalg.norm(points - points[i], axis=1) <= radius)
        )
    return result


def select(
    structure: StructureData,
    expression: str = "all",
    *,
    groups: Mapping[str, set[int]] | None = None,
    model_ids: Sequence[str] | None = None,
) -> set[int]:
    """Evaluate the documented selection grammar, returning zero-based indices.

    Boolean precedence is not, and, or. Indices in expressions are one-based;
    named groups contain zero-based indices. Models default to the single model
    "1"; a scene passes its explicit per-atom model identifiers. No eval is used.
    """
    count = len(structure.atoms)
    universe = set(range(count))
    models = ["1"] * count if model_ids is None else list(model_ids)
    if len(models) != count:
        raise ValueError("Model identifiers must have one entry per atom")
    lexer = shlex.shlex(expression, posix=True, punctuation_chars="()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = [part for token in lexer for part in
              (list(token) if token and set(token) <= {"(", ")"} else [token])]
    if not tokens:
        return set()
    cursor = 0

    def pop() -> str:
        nonlocal cursor
        if cursor >= len(tokens):
            raise ValueError("Incomplete selection expression")
        value = tokens[cursor]
        cursor += 1
        return value

    def expect(token: str) -> None:
        if pop() != token:
            raise ValueError(f"Expected {token!r} in selection")

    def checked(indices: set[int]) -> set[int]:
        if any(not isinstance(i, int) or i < 0 or i >= count for i in indices):
            raise ValueError("Selection atom index is out of range")
        return set(indices)

    def primary() -> set[int]:
        kind = pop()
        if kind == "(":
            selected = disjunction()
            expect(")")
            return selected
        if kind == "all":
            return universe.copy()
        if kind == "none":
            return set()
        if kind == "within":
            try:
                radius = float(pop())
            except ValueError as exc:
                raise ValueError("Selection radius must be numeric") from exc
            expect("of")
            expect("(")
            seed = disjunction()
            expect(")")
            return within(structure, seed, radius)
        if kind not in {"index", "element", "chain", "residue", "model", "group"}:
            raise ValueError(f"Unknown selection predicate {kind!r}")
        value = pop()
        if value in {"(", ")", "and", "or", "not"}:
            raise ValueError(f"Missing value for {kind}")
        if kind == "index":
            while cursor < len(tokens) and (value.endswith(",") or tokens[cursor].startswith(",")):
                value += pop()
            result: set[int] = set()
            for term in value.split(","):
                match = re.fullmatch(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?", term)
                if match is None:
                    raise ValueError("Use one-based indices/ranges, for example index 1,3-5")
                first = int(match[1])
                last = int(match[2] or match[1])
                if first > last:
                    raise ValueError("Selection atom index range is reversed")
                if last > count:
                    raise ValueError("Selection atom index is out of range")
                result.update(range(first - 1, last))
            return result
        if kind == "group":
            if groups is None or value not in groups:
                raise ValueError(f"Unknown named group {value!r}")
            return checked(groups[value])
        if kind == "model":
            return {i for i, model in enumerate(models) if model == value}
        if kind == "element":
            return {i for i, atom in enumerate(structure.atoms)
                    if atom.symbol.casefold() == value.casefold()}
        if kind == "chain":
            return {i for i, atom in enumerate(structure.atoms) if atom.chain_id == value}
        if "/" not in value:
            raise ValueError("Residue selection requires chain/sequence, for example A/24")
        chain, sequence = value.split("/", 1)
        return {i for i, atom in enumerate(structure.atoms)
                if atom.chain_id == chain and str(atom.residue_seq) == sequence}

    def negation() -> set[int]:
        if cursor < len(tokens) and tokens[cursor] == "not":
            pop()
            return universe - negation()
        return primary()

    def conjunction() -> set[int]:
        selected = negation()
        while cursor < len(tokens) and tokens[cursor] == "and":
            pop()
            selected &= negation()
        return selected

    def disjunction() -> set[int]:
        selected = conjunction()
        while cursor < len(tokens) and tokens[cursor] == "or":
            pop()
            selected |= conjunction()
        return selected

    selected = disjunction()
    if cursor != len(tokens):
        raise ValueError(f"Unexpected selection token {tokens[cursor]!r}")
    return selected


def neighbour_pairs(points: np.ndarray, indices: set[int], cutoff: float) -> list[tuple[int, int]]:
    """Finite-cell candidates in deterministic order without an N² matrix."""
    from collections import defaultdict
    from itertools import product

    cells: dict[tuple[int, ...], list[int]] = defaultdict(list)
    for i in sorted(indices):
        cells[tuple(np.floor(points[i] / cutoff).astype(int))].append(i)
    pairs = []
    for i in sorted(indices):
        cell = np.floor(points[i] / cutoff).astype(int)
        for shift in product((-1, 0, 1), repeat=3):
            for j in cells.get(tuple(cell + shift), ()):
                if i < j and np.linalg.norm(points[i] - points[j]) <= cutoff:
                    pairs.append((i, j))
    return sorted(pairs)
