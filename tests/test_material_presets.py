"""Tests for material_presets module."""

from __future__ import annotations

import pytest


class TestMaterialPresets:
    def test_import(self):
        from vibeview.material_presets import (
            MATERIAL_PRESETS,
            get_preset,
            list_presets,
        )

        assert len(MATERIAL_PRESETS) == 6
        assert callable(get_preset)
        assert callable(list_presets)

    def test_get_preset_valid(self):
        from vibeview.material_presets import get_preset

        p = get_preset("cpk_glossy")
        assert p.name == "CPK Glossy (default)"
        assert p.atom_roughness == 0.25

    def test_get_preset_fallback(self):
        from vibeview.material_presets import get_preset

        p = get_preset("nonexistent")
        assert p.name == "CPK Glossy (default)"

    def test_list_presets(self):
        from vibeview.material_presets import list_presets

        options = list_presets()
        assert len(options) == 6
        for opt in options:
            assert "value" in opt
            assert "title" in opt

    def test_all_presets_valid(self):
        from vibeview.material_presets import MATERIAL_PRESETS

        for name, preset in MATERIAL_PRESETS.items():
            assert preset.name
            assert 0 <= preset.atom_roughness <= 1
            assert 0 <= preset.atom_metallic <= 1
            assert len(preset.background_color) == 3
