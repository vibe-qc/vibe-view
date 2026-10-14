"""Persistent viewer settings."""

from __future__ import annotations

import json

import pytest

from vibeview import settings


@pytest.fixture(autouse=True)
def _own_settings_file(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


_INVALID_SETTINGS = [
    pytest.param(b"null", id="null"),
    pytest.param(b"true", id="boolean"),
    pytest.param(b"42", id="integer"),
    pytest.param(b"0.5", id="float"),
    pytest.param(b'"settings"', id="string"),
    pytest.param(b"[]", id="empty-array"),
    pytest.param(b"[1]", id="array"),
    pytest.param(b'[["measure_unit", "bohr"]]', id="pair-array"),
    pytest.param(b"{", id="invalid-json"),
    pytest.param(b"\xff", id="invalid-utf8"),
]


def _write_settings(payload: bytes) -> None:
    path = settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


@pytest.mark.parametrize("payload", _INVALID_SETTINGS)
def test_invalid_settings_fall_back_to_defaults(payload):
    _write_settings(payload)

    assert settings.load_settings() == settings.DEFAULTS
    assert settings.settings_path().read_bytes() == payload


@pytest.mark.parametrize("payload", _INVALID_SETTINGS)
def test_setting_can_be_saved_after_invalid_file(payload):
    _write_settings(payload)

    settings.set_setting("measure_unit", "bohr")

    stored = json.loads(settings.settings_path().read_text(encoding="utf-8"))
    assert stored == {**settings.DEFAULTS, "measure_unit": "bohr"}
    assert settings.get_setting("measure_unit") == "bohr"


def test_valid_settings_merge_defaults_and_preserve_unknown_keys():
    stored = {"measure_unit": "bohr", "future_setting": "Å"}
    _write_settings(json.dumps(stored, ensure_ascii=False).encode("utf-8"))

    assert settings.load_settings() == {**settings.DEFAULTS, **stored}

    settings.set_setting("dark_background", False)
    assert settings.get_setting("future_setting") == "Å"
    assert settings.get_setting("measure_unit") == "bohr"


def test_unreadable_settings_fall_back_to_defaults():
    settings.settings_path().mkdir(parents=True)

    assert settings.load_settings() == settings.DEFAULTS


@pytest.mark.parametrize("command", ["diff", "batch-compare"])
@pytest.mark.parametrize("payload", _INVALID_SETTINGS)
def test_comparison_uses_defaults_after_invalid_settings(command, payload, sample_qvf):
    from click.testing import CliRunner

    from vibeview.cli import main

    _write_settings(payload)

    result = CliRunner().invoke(main, [command, str(sample_qvf), str(sample_qvf)])

    assert result.exit_code == 0, result.output
    assert "0.0000 Å" in result.output
    assert "bohr" not in result.output


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

