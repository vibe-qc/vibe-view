"""User configuration for vibe-view — reads/writes ``~/.config/vibe-view/config.toml``.

Used by ``vibe-view config`` CLI command. The file location comes from
:mod:`vibeview.paths`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from vibeview.paths import config_path

_DEFAULT_CONFIG = """# vibe-view configuration
# Edit this file directly ("vibe-view config --path" shows its location).

[server]
# Default port for open, compare, serve, from-vq and demo --open
port = 8080

# Default bind address (127.0.0.1 = localhost only)
host = "127.0.0.1"

# Auto-open browser (true) or start headless (false)
open_browser = true

[display]
# Default image size for vibe-view batch/capture
size = "900x600"

# Default structure representation (ball_and_stick, space_filling, sticks_only, wireframe)
representation = "ball_and_stick"
"""


def init_config() -> Path:
    """Create a default config file if it doesn't exist. Returns path."""
    path = config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_DEFAULT_CONFIG)
    return path


def read_config() -> dict[str, Any]:
    """Read the config file. Returns empty dict if it doesn't exist."""
    path = config_path()
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def get(key: str, default: Any = None) -> Any:
    """Read a single config key (e.g. ``'server.port'``)."""
    node: Any = read_config()
    parts = key.split(".")
    for part in parts:
        if isinstance(node, dict):
            node = node.get(part)
        else:
            return default
    return node if node is not None else default
