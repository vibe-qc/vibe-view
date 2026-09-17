"""Persistent user settings for vibe-view.

Settings are stored in a JSON file at ~/.cache/vibe-view/settings.json
and are loaded at startup.  They control default viewer behaviour:
background, material preset, measurement units, etc.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _settings_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "vibe-view" / "settings.json"


DEFAULTS: dict[str, Any] = {
    "dark_background": True,
    "material_preset": "cpk_glossy",
    "representation_style": "ball_and_stick",
    "default_isovalue": 0.05,
    "default_colormap": "viridis",
    "default_opacity": 0.65,
    "show_atom_labels": False,
    "measure_unit": "angstrom",
    "auto_save_interval": 60,
    "recent_files": [],
    "max_recent_files": 20,
    "relocalize_backend_python": "",
}


def load_settings() -> dict[str, Any]:
    """Load user settings from disk, merging with defaults."""
    path = _settings_path()
    settings = dict(DEFAULTS)
    if path.exists():
        try:
            stored = json.loads(path.read_text())
            settings.update(stored)
        except (json.JSONDecodeError, OSError):
            pass
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    """Persist user settings to disk."""
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2))


def get_setting(key: str, default: Any = None) -> Any:
    """Get a single setting value."""
    settings = load_settings()
    return settings.get(key, default)


def set_setting(key: str, value: Any) -> None:
    """Set and persist a single setting."""
    settings = load_settings()
    settings[key] = value
    save_settings(settings)


def add_recent_file(filepath: str) -> None:
    """Add a file to the recent-files list."""
    settings = load_settings()
    recent = settings.get("recent_files", [])
    # Remove if already present (move to top)
    if filepath in recent:
        recent.remove(filepath)
    recent.insert(0, filepath)
    # Trim
    max_files = settings.get("max_recent_files", 20)
    settings["recent_files"] = recent[:max_files]
    save_settings(settings)


def get_recent_files() -> list[str]:
    """Return the list of recently opened files."""
    return load_settings().get("recent_files", [])
