"""Recent files history for vibe-view.

Tracks recently opened QVF files in ``~/.cache/vibe-view/recent.json``
(``$XDG_CACHE_HOME`` honored).  Used by the ``vibe-view recent`` CLI
command and shared with the Electron desktop app's File → Open Recent
submenu — both sides read and write the same JSON list, most recent
first.
"""

from __future__ import annotations

import json
from pathlib import Path

from vibeview.paths import config_dir, recent_path


def _read_recent() -> list[str]:
    """Return list of recently opened QVF paths (most recent first)."""
    path = recent_path()
    if not path.exists():
        # One-time migration from the pre-desktop-app config-dir location.
        legacy = config_dir() / "recent.json"
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
    p = recent_path()
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
