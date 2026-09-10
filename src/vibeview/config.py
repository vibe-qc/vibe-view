"""User configuration for vibe-view — reads/writes ``~/.config/vibe-view/config.toml``.

Used by ``vibe-view config`` CLI command.
"""

from __future__ import annotations

from pathlib import Path


def _config_path() -> Path:
    """Return the platform-appropriate config file path."""
    import os

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "vibe-view" / "config.toml"
    return Path.home() / ".config" / "vibe-view" / "config.toml"


_DEFAULT_CONFIG = """# vibe-view configuration
# Edit this file directly ("vibe-view config --path" shows its location).

[server]
# Default port for vibe-view open/serve/compare
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
    path = _config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_DEFAULT_CONFIG)
    return path


def read_config() -> dict:
    """Read the config file. Returns empty dict if it doesn't exist."""
    path = _config_path()
    if not path.exists():
        return {}
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def get(key: str, default=None):
    """Read a single config key (e.g. ``'server.port'``)."""
    config = read_config()
    parts = key.split(".")
    for part in parts:
        if isinstance(config, dict):
            config = config.get(part)
        else:
            return default
    return config if config is not None else default
