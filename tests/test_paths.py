"""vibeview.paths is the one place per-user file locations are derived."""

from __future__ import annotations

from pathlib import Path

import pytest

from vibeview import paths

CACHE_FILES = {
    paths.log_path: "vibe-view.log",
    paths.recent_path: "recent.json",
    paths.settings_path: "settings.json",
    paths.interpreter_record_path: "interpreter.json",
    paths.auto_session_path: "auto-session.json",
}


@pytest.mark.parametrize(("function", "name"), CACHE_FILES.items())
def test_cache_files_follow_xdg_cache_home(monkeypatch, tmp_path: Path, function, name) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert function() == tmp_path / "vibe-view" / name


def test_config_file_follows_xdg_config_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert paths.config_dir() == tmp_path / "vibe-view"
    assert paths.config_path() == tmp_path / "vibe-view" / "config.toml"


@pytest.mark.parametrize("variable", ["XDG_CACHE_HOME", "XDG_CONFIG_HOME"])
@pytest.mark.parametrize("state", ["unset", "empty"])
def test_home_fallback_when_xdg_variable_is_unset_or_empty(
    monkeypatch, tmp_path: Path, variable: str, state: str
) -> None:
    """The XDG specification treats an empty variable exactly like an unset one."""
    if state == "unset":
        monkeypatch.delenv(variable, raising=False)
    else:
        monkeypatch.setenv(variable, "")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    if variable == "XDG_CACHE_HOME":
        assert paths.cache_dir() == tmp_path / ".cache" / "vibe-view"
    else:
        assert paths.config_dir() == tmp_path / ".config" / "vibe-view"


def test_every_consumer_derives_from_the_shared_helpers(monkeypatch, tmp_path: Path) -> None:
    """Settings, recent files and the CLI log all land in the same cache tree."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    from vibeview import config, recent, settings

    settings.save_settings({"measure_unit": "bohr"})
    recent.add_recent(tmp_path / "missing.qvf")
    written = {
        p.relative_to(tmp_path / "cache") for p in (tmp_path / "cache").rglob("*") if p.is_file()
    }
    assert written == {Path("vibe-view/settings.json"), Path("vibe-view/recent.json")}
    assert config.init_config() == tmp_path / "config" / "vibe-view" / "config.toml"
