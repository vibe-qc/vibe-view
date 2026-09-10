"""Tests for desktop packaging module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestDesktop:
    def test_import(self):
        from vibeview.desktop import (
            generate_electron_main_js,
            generate_electron_package_json,
            generate_electron_preload_js,
            get_system_info,
        )

        assert callable(generate_electron_package_json)

    def test_generate_package_json(self, tmp_path):
        from vibeview.desktop import generate_electron_package_json

        out = str(tmp_path / "electron")
        pkg_path = generate_electron_package_json(out)
        assert Path(pkg_path).exists()
        data = json.loads(Path(pkg_path).read_text())
        assert data["name"] == "vibe-view"
        assert "electron" in data["devDependencies"]

    def test_generate_main_js(self, tmp_path):
        from vibeview.desktop import generate_electron_main_js

        out = str(tmp_path / "electron")
        main_path = generate_electron_main_js(out)
        assert Path(main_path).exists()
        content = Path(main_path).read_text()
        assert "BrowserWindow" in content
        assert "app.whenReady" in content

    def test_generate_preload_js(self, tmp_path):
        from vibeview.desktop import generate_electron_preload_js

        out = str(tmp_path / "electron")
        preload_path = generate_electron_preload_js(out)
        assert Path(preload_path).exists()
        content = Path(preload_path).read_text()
        assert "contextBridge" in content

    def test_get_system_info(self):
        from vibeview.desktop import get_system_info

        info = get_system_info()
        assert "os" in info
        assert "python_version" in info

    def test_check_for_updates(self):
        from vibeview.desktop import check_for_updates

        result = check_for_updates("1.0.0")
        assert "update_available" in result
        assert "current_version" in result
