"""Small text formatting helpers shared by the CLI, the file browser and panels."""

from __future__ import annotations

_SIZE_UNITS = ("KB", "MB", "GB", "TB")


def human_size(size_bytes: int | float) -> str:
    """Format a byte count for people: whole bytes, then one decimal per 1024 step.

    ``0`` → ``"0 B"``, ``1536`` → ``"1.5 KB"``, ``3 * 1024**3`` → ``"3.0 GB"``.
    Steps are binary (1024) with the short labels the viewer has always shown.
    """
    size = float(size_bytes)
    if abs(size) < 1024.0:
        return f"{int(size)} B"
    for unit in _SIZE_UNITS:
        size /= 1024.0
        if abs(size) < 1024.0 or unit == _SIZE_UNITS[-1]:
            return f"{size:.1f} {unit}"
    raise AssertionError("unreachable")
