"""Persistent viewer settings."""

from __future__ import annotations


class TestSettings:
    def test_import(self):
        from vibeview.settings import (
            DEFAULTS,
        )

        assert isinstance(DEFAULTS, dict)

    def test_defaults(self):
        from vibeview.settings import DEFAULTS

        assert "dark_background" in DEFAULTS
        assert "material_preset" in DEFAULTS

    def test_get_setting_default(self):
        from vibeview.settings import get_setting

        val = get_setting("nonexistent_key", "fallback")
        assert val == "fallback"

    def test_get_setting_exists(self):
        from vibeview.settings import get_setting

        val = get_setting("dark_background")
        assert isinstance(val, bool)

    def test_load_settings(self):
        from vibeview.settings import load_settings

        settings = load_settings()
        assert isinstance(settings, dict)
        assert "dark_background" in settings

    def test_add_recent_file(self):
        from vibeview.settings import add_recent_file, get_recent_files

        add_recent_file("/tmp/test_recent.qvf")
        recent = get_recent_files()
        assert "/tmp/test_recent.qvf" in recent

    def test_settings_save_load_roundtrip(self):
        from vibeview.settings import get_setting, set_setting

        original = get_setting("dark_background", True)
        set_setting("dark_background", not original)
        assert get_setting("dark_background") == (not original)
        # Restore
        set_setting("dark_background", original)

