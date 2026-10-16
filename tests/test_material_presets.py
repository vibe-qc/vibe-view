"""Tests for material_presets module."""

from __future__ import annotations


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

        for _name, preset in MATERIAL_PRESETS.items():
            assert preset.name
            assert 0 <= preset.atom_roughness <= 1
            assert len(preset.background_color) == 3


class TestMaterialPresetsEdgeCases:
    def test_preset_background_valid_rgb(self):
        from vibeview.material_presets import MATERIAL_PRESETS

        for name, p in MATERIAL_PRESETS.items():
            assert len(p.background_color) == 3
            for c in p.background_color:
                assert 0.0 <= c <= 1.0, f"{name} background has out-of-range value {c}"

    def test_preset_roughness_range(self):
        from vibeview.material_presets import MATERIAL_PRESETS

        for name, p in MATERIAL_PRESETS.items():
            assert 0.0 <= p.atom_roughness <= 1.0, f"{name} roughness out of range"


class TestMaterialPresetsIntegration:
    """Test material presets apply correctly."""

    def test_all_presets_listed(self):
        from vibeview.material_presets import list_presets

        options = list_presets()
        names = {o["value"] for o in options}
        assert "cpk_glossy" in names
        assert "scientific" in names
        assert "toon" in names

    def test_preset_fields(self):
        from vibeview.material_presets import get_preset

        for name in ["cpk_glossy", "matte", "glass", "metallic", "toon", "scientific"]:
            p = get_preset(name)
            assert p.name
            assert len(p.background_color) == 3
            # All values in valid range
            for c in p.background_color:
                assert 0.0 <= c <= 1.0
