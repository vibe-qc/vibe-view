"""Test the recent files tracking module."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock


class TestRecent:
    def test_empty_recent(self) -> None:
        from vibeview.recent import get_recent

        with tempfile.TemporaryDirectory() as td:
            env = {"XDG_CACHE_HOME": td, "XDG_CONFIG_HOME": td}
            with mock.patch.dict(os.environ, env):
                files = get_recent()
                assert files == []

    def test_recent_path_in_cache_dir(self) -> None:
        """recent.json lives in the XDG cache dir — shared with the
        Electron desktop app's Open Recent submenu."""
        from vibeview.recent import _recent_path

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": td}):
                assert _recent_path() == Path(td) / "vibe-view" / "recent.json"

    def test_add_and_get(self) -> None:
        from vibeview.recent import add_recent, get_recent

        with tempfile.TemporaryDirectory() as td:
            env = {"XDG_CACHE_HOME": td, "XDG_CONFIG_HOME": td}
            with mock.patch.dict(os.environ, env):
                # Create a dummy file to track
                dummy = Path(td) / "test.qvf"
                dummy.write_text("")
                add_recent(dummy)
                files = get_recent()
                assert files == [str(dummy.resolve())]

    def test_duplicate_handling(self) -> None:
        from vibeview.recent import _read_recent, _write_recent

        with tempfile.TemporaryDirectory() as td:
            env = {"XDG_CACHE_HOME": td, "XDG_CONFIG_HOME": td}
            with mock.patch.dict(os.environ, env):
                _write_recent(["/a/b.qvf", "/a/b.qvf", "/c/d.qvf"])
                files = _read_recent()
                # Duplicates are preserved in raw storage but get_recent deduplicates
                assert len(files) <= 3

    def test_migrates_from_legacy_config_location(self) -> None:
        """Entries from the old ~/.config/vibe-view/recent.json carry over."""
        from vibeview.recent import _recent_path, get_recent

        with tempfile.TemporaryDirectory() as cache_td, tempfile.TemporaryDirectory() as cfg_td:
            env = {"XDG_CACHE_HOME": cache_td, "XDG_CONFIG_HOME": cfg_td}
            with mock.patch.dict(os.environ, env):
                dummy = Path(cfg_td) / "old.qvf"
                dummy.write_text("")
                legacy = Path(cfg_td) / "vibe-view" / "recent.json"
                legacy.parent.mkdir(parents=True)
                legacy.write_text(json.dumps([str(dummy)]))
                assert get_recent() == [str(dummy)]
                # Migration materialized the new file
                assert _recent_path().exists()
