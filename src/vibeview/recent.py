"""Recent files history for vibe-view.

Tracks recently opened QVF files in ``~/.cache/vibe-view/recent.json``
(``$XDG_CACHE_HOME`` honored).  Used by the ``vibe-view recent`` CLI
command and shared with the Electron desktop app's File → Open Recent
submenu — both sides read and write the same JSON list, most recent
first.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _recent_path() -> Path:
    """Path to the recent-files JSON file (in the XDG cache dir)."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "vibe-view" / "recent.json"


def _legacy_recent_path() -> Path:
    """Pre-desktop-app location (``~/.config/vibe-view/recent.json``)."""
    from vibeview.config import _config_path

    return _config_path().parent / "recent.json"


def _read_recent() -> list[str]:
    """Return list of recently opened QVF paths (most recent first)."""
    path = _recent_path()
    if not path.exists():
        # One-time migration from the old config-dir location.
        legacy = _legacy_recent_path()
        if legacy.exists():
            try:
                _write_recent(json.loads(legacy.read_text()))
            except Exception:
                return []
        else:
            return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return [p for p in data if isinstance(p, str) and Path(p).exists()]
    except Exception:
        pass
    return []


def _write_recent(paths: list[str]) -> None:
    """Save recent paths (max 50)."""
    p = _recent_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(paths[:50], indent=2))


def add_recent(qvf_path: str | Path) -> None:
    """Add a file to the recent list."""
    path_str = str(Path(qvf_path).resolve())
    recent = _read_recent()
    if path_str in recent:
        recent.remove(path_str)
    recent.insert(0, path_str)
    _write_recent(recent)


def get_recent(limit: int = 10) -> list[str]:
    """Return the most recent existing file paths."""
    return _read_recent()[:limit]
