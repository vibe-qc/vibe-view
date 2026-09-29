"""Where vibe-view keeps its per-user files.

Every module that stores something for the user derives its location here,
so the cache and configuration directories are resolved once and agree
everywhere. The layout follows the XDG Base Directory Specification:
``$XDG_CACHE_HOME`` and ``$XDG_CONFIG_HOME`` win when set and non-empty, and
``~/.cache`` and ``~/.config`` are the fallbacks. ``docs/cli.md`` documents
the resulting files, and the Electron shell (``electron/main.js``) mirrors
the cache layout in JavaScript so both sides read the same ``recent.json``
and ``interpreter.json``.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIRNAME = "vibe-view"


def _xdg_base(variable: str, fallback: Path) -> Path:
    """Return an XDG base directory, treating an empty variable as unset.

    The specification says an unset *or empty* variable means the default.
    """
    value = os.environ.get(variable)
    return Path(value) if value else fallback


def cache_dir() -> Path:
    """``$XDG_CACHE_HOME/vibe-view``, or ``~/.cache/vibe-view``."""
    return _xdg_base("XDG_CACHE_HOME", Path.home() / ".cache") / APP_DIRNAME


def config_dir() -> Path:
    """``$XDG_CONFIG_HOME/vibe-view``, or ``~/.config/vibe-view``."""
    return _xdg_base("XDG_CONFIG_HOME", Path.home() / ".config") / APP_DIRNAME


def config_path() -> Path:
    """The ``vibe-view config`` TOML file."""
    return config_dir() / "config.toml"


def log_path() -> Path:
    """The rotating server log written by ``vibe-view open``, ``serve`` and ``desktop``."""
    return cache_dir() / "vibe-view.log"


def recent_path() -> Path:
    """The recent-files list shared with the desktop app's Open Recent menu."""
    return cache_dir() / "recent.json"


def settings_path() -> Path:
    """The browser viewer's persistent settings and colour overrides."""
    return cache_dir() / "settings.json"


def interpreter_record_path() -> Path:
    """The interpreter record the desktop app reads to relaunch the right venv."""
    return cache_dir() / "interpreter.json"


def auto_session_path() -> Path:
    """The auto-save session for a viewer opened without a file on disk."""
    return cache_dir() / "auto-session.json"
