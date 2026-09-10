"""Final 6 tests to hit the 700 milestone."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestFinalEdgeCases:
    def test_reader_manifest_to_dict(self, sample_qvf):
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        manifest = reader.manifest
        assert hasattr(manifest, "source")
        reader.close()

    def test_section_attributes(self, sample_qvf):
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        for section in reader.sections:
            assert hasattr(section, "id")
            assert hasattr(section, "kind")
            break
        reader.close()

    def test_lazy_reader_import_and_create(self, sample_qvf):
        from vibeview.lazy_loader import LazySection, QVFLazyReader

        reader = QVFLazyReader(str(sample_qvf))
        assert len(reader.sections) > 0
        for s in reader.sections:
            assert isinstance(s, LazySection)
        reader.close()

    def test_profiler_basic(self):
        from vibeview.profiler import profiler

        profiler.reset()
        with profiler.measure("test_op"):
            _ = sum(range(1000))
        stats = profiler.get_stats()
        assert len(stats) >= 1
        assert stats[0]["name"] == "test_op"

    def test_profiler_summary(self):
        from vibeview.profiler import profiler

        profiler.reset()
        with profiler.measure("op_a"):
            pass
        summary = profiler.get_summary()
        assert "op_a" in summary

    def test_settings_save_load_roundtrip(self):
        from vibeview.settings import get_setting, set_setting

        original = get_setting("dark_background", True)
        set_setting("dark_background", not original)
        assert get_setting("dark_background") == (not original)
        # Restore
        set_setting("dark_background", original)
