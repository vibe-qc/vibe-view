"""Reading vibe-view's own source in the tests that must (#37-R6).

``vibeview.app`` is a package, so a test that read ``app.py`` by name reads
all of its modules here instead: :func:`read_source` concatenates them and
:func:`source_files` lists them, so a guard keeps covering code that moved
between modules.
"""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "vibeview"


def source_files(name: str) -> list[Path]:
    """The files behind ``name``: a module path such as ``cli.py``, or
    ``app.py`` for the modules of the ``vibeview.app`` package."""
    path = SRC / name
    if path.is_file():
        return [path]
    package = SRC / name.removesuffix(".py")
    if package.is_dir():
        return sorted(package.glob("*.py"))
    raise FileNotFoundError(path)


def read_source(name: str) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in source_files(name))


def app_source() -> str:
    """Every module of the browser viewer, as one text."""
    return read_source("app.py")
