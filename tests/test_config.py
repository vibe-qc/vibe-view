"""Test the user configuration module."""

from __future__ import annotations

import tempfile
from unittest import mock


class TestConfig:
    def test_config_path(self) -> None:
        from vibeview.paths import config_path

        path = config_path()
        assert path.name == "config.toml"
        assert "vibe-view" in str(path)

    def test_init_creates_file(self) -> None:
        from vibeview.config import init_config

        with tempfile.TemporaryDirectory() as td:
            import os

            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": td}):
                path = init_config()
                assert path.exists()
                content = path.read_text()
                assert "port = 8080" in content
                assert "[server]" in content

    def test_read_empty(self) -> None:
        from vibeview.config import read_config

        with tempfile.TemporaryDirectory() as td:
            import os

            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": td}):
                config = read_config()
                assert config == {}

    def test_get_default(self) -> None:
        from vibeview.config import get

        assert get("nonexistent.key", "default") == "default"
        assert get("server.port", 8080) == 8080
