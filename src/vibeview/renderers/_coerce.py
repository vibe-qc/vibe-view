"""Numbers and vectors out of schema-free JSON, without raising.

Producer metadata is untyped: a pair field can be a number, a numeric
string, ``null`` or garbage. Six renderers and terminal panes each wrote
their own guard with a different fallback. These are the guards, with the
fallback the caller chooses: ``None`` when it is omitted, so a missing
value is distinguishable from zero, or a number for display code that
would rather print a placeholder than branch.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import overload


def _is_scalar(value: object) -> bool:
    # bool is an int subclass, but True in a numeric field is a producer
    # mistake, not 1.0. Containers never coerce.
    return value is not None and not isinstance(value, bool | list | tuple | dict | set)


@overload
def as_float(value: object) -> float | None: ...
@overload
def as_float(value: object, default: float) -> float: ...
def as_float(value: object, default: float | None = None) -> float | None:
    """``value`` as a float, or ``default`` when absent or not numeric.

    Anything ``float()`` accepts counts, so NumPy scalars and numeric
    strings pass; ``"nan"`` yields NaN, which a caller that wants a gap in
    a plot can rely on.
    """
    if not _is_scalar(value):
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@overload
def as_int(value: object) -> int | None: ...
@overload
def as_int(value: object, default: int) -> int: ...
def as_int(value: object, default: int | None = None) -> int | None:
    """``value`` as an int, or ``default``; a float or numeric string is truncated."""
    number = as_float(value)
    if number is None or number != number or number in (float("inf"), float("-inf")):
        return default
    return int(number)


def as_vector(value: object) -> list[float] | None:
    """A numeric sequence (position, pair), or None when any entry is not."""
    if not isinstance(value, list | tuple):
        return None
    out: list[float] = []
    for item in value:
        number = as_float(item)
        if number is None:
            return None
        out.append(number)
    return out


def as_str_list(value: object) -> list[str]:
    """A list of strings; a scalar becomes one entry, anything else is empty."""
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    return [str(value)]
