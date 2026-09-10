"""Performance benchmarks for vibe-view v2.x.

Not intended as part of the CI pass/fail gate, but as a reproducible
way to track performance regressions.  Run with:

    PYVISTA_OFF_SCREEN=True pytest vibe-view/tests/test_benchmarks.py -v
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest


class TestStructureRenderingBenchmarks:
    """Benchmark structure rendering performance."""

    def test_structure_read_speed(self, sample_qvf):
        """Structure read should complete under 2s."""
        from vibeview.qvf import QVFReader

        t0 = time.perf_counter()
        reader = QVFReader(sample_qvf)
        sdata = reader.read_structure()
        elapsed = time.perf_counter() - t0
        reader.close()

        assert len(sdata.atoms) > 0
        assert elapsed < 2.0, f"Structure read took {elapsed:.2f}s"

    def test_qvf_open_speed(self, sample_qvf):
        """QVF open should complete under 1s."""
        from vibeview.qvf import QVFReader

        t0 = time.perf_counter()
        reader = QVFReader(sample_qvf)
        elapsed = time.perf_counter() - t0
        reader.close()

        assert elapsed < 1.0, f"QVF open took {elapsed:.2f}s"

    def test_info_speed(self, sample_qvf):
        """info() call under 1s."""
        from vibeview import QVFReader, info

        reader = QVFReader(sample_qvf)
        t0 = time.perf_counter()
        result = info(reader)
        elapsed = time.perf_counter() - t0
        reader.close()

        assert isinstance(result, dict)
        assert elapsed < 1.0, f"info() took {elapsed:.2f}s"

    def test_sections_speed(self, sample_qvf):
        """sections() call under 0.5s."""
        from vibeview import QVFReader, sections

        reader = QVFReader(sample_qvf)
        t0 = time.perf_counter()
        result = sections(reader)
        elapsed = time.perf_counter() - t0
        reader.close()

        assert isinstance(result, list)
        assert elapsed < 0.5, f"sections() took {elapsed:.2f}s"

    def test_validate_speed(self, sample_qvf):
        """validate() call under 1s."""
        from vibeview import QVFReader, validate

        reader = QVFReader(sample_qvf)
        t0 = time.perf_counter()
        result = validate(reader)
        elapsed = time.perf_counter() - t0
        reader.close()

        assert "valid" in result
        assert elapsed < 1.0, f"validate() took {elapsed:.2f}s"

    def test_export_svg_speed(self, sample_qvf, tmp_path):
        """SVG export under 2s."""
        from vibeview.export_svg import export_svg
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        out = tmp_path / "bench.svg"
        t0 = time.perf_counter()
        export_svg(reader, str(out))
        elapsed = time.perf_counter() - t0
        reader.close()

        assert out.exists()
        assert elapsed < 2.0, f"SVG export took {elapsed:.2f}s"

    def test_batch_compare_speed(self, sample_qvf):
        """Batch compare of 2 identical files under 2s."""
        from vibeview.batch_compare import compare_batch

        t0 = time.perf_counter()
        result = compare_batch([str(sample_qvf), str(sample_qvf)])
        elapsed = time.perf_counter() - t0

        assert len(result["files"]) == 2
        assert elapsed < 2.0, f"Batch compare took {elapsed:.2f}s"

    def test_xyz_export_speed(self, sample_qvf):
        """XYZ export under 0.5s."""
        from vibeview import QVFReader, export_xyz

        reader = QVFReader(sample_qvf)
        t0 = time.perf_counter()
        xyz = export_xyz(reader)
        elapsed = time.perf_counter() - t0
        reader.close()

        assert isinstance(xyz, str)
        assert len(xyz) > 0
        assert elapsed < 0.5, f"XYZ export took {elapsed:.2f}s"

    def test_many_section_listing(self, sample_qvf):
        """Listing sections 100 times should be fast."""
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        t0 = time.perf_counter()
        for _ in range(100):
            _ = list(reader.sections)
        elapsed = time.perf_counter() - t0
        reader.close()

        # 100 iterations under 1s
        assert elapsed < 1.0, f"100x section list took {elapsed:.2f}s"
