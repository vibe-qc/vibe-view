"""Tests for the vibe-view Python SDK API.

Covers the programmatic API surface: info, sections, has_section,
get_structure, export_xyz, diff, get_volume, get_table, validate,
capture_structure, slice_qvf, and error-handling paths.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

# ── Info / inspection ────────────────────────────────────────────────────


class TestSDKInfo:
    def test_info_returns_dict(self, sample_qvf):
        from vibeview import QVFReader, info

        reader = QVFReader(sample_qvf)
        result = info(reader)
        assert isinstance(result, dict)
        reader.close()

    def test_info_has_basic_keys(self, sample_qvf):
        from vibeview import info

        result = info(sample_qvf)
        assert "program" in result

    def test_info_reports_program(self, sample_qvf):
        from vibeview import info

        result = info(sample_qvf)
        assert result["program"] == "vibe-qc"

    def test_info_reports_calculation(self, sample_qvf):
        from vibeview import info

        result = info(sample_qvf)
        assert result["calculation"] == "h2o_sample"

    def test_info_reports_qvf_version(self, sample_qvf):
        from vibeview import info

        result = info(sample_qvf)
        assert result["qvf_version"] == 1

    def test_info_reports_n_sections(self, sample_qvf):
        from vibeview import info

        result = info(sample_qvf)
        assert result["n_sections"] >= 1
        assert isinstance(result["sections"], list)

    def test_info_from_path_string(self, sample_qvf):
        from vibeview import info

        result = info(str(sample_qvf))
        assert result["program"] == "vibe-qc"

    def test_sections_returns_list(self, sample_qvf):
        from vibeview import sections

        result = sections(sample_qvf)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_sections_has_id_and_kind(self, sample_qvf):
        from vibeview import sections

        for s in sections(sample_qvf):
            assert "id" in s
            assert "kind" in s

    def test_has_section_true(self, sample_qvf):
        from vibeview import has_section

        assert has_section(sample_qvf, "structure")

    def test_has_section_false(self, sample_qvf):
        from vibeview import has_section

        assert not has_section(sample_qvf, "nonexistent_section_id")


# ── Structure ────────────────────────────────────────────────────────────


class TestSDKStructure:
    def test_get_structure(self, sample_qvf):
        from vibeview import get_structure

        sdata = get_structure(sample_qvf)
        assert "atoms" in sdata
        assert len(sdata["atoms"]) > 0

    def test_get_structure_atoms_have_symbol(self, sample_qvf):
        from vibeview import get_structure

        sdata = get_structure(sample_qvf)
        for atom in sdata["atoms"]:
            assert "symbol" in atom
            assert "position" in atom
            assert len(atom["position"]) == 3

    def test_get_structure_has_pbc(self, sample_qvf):
        from vibeview import get_structure

        sdata = get_structure(sample_qvf)
        assert "pbc" in sdata
        assert sdata["pbc"] == [False, False, False]

    def test_export_xyz(self, sample_qvf):
        from vibeview import export_xyz

        xyz = export_xyz(sample_qvf)
        assert isinstance(xyz, str)
        lines = xyz.strip().split("\n")
        assert len(lines) >= 2
        # First line is atom count
        assert int(lines[0]) == 3


# ── Comparison ───────────────────────────────────────────────────────────


class TestSDKDiff:
    def test_diff_returns_dict(self, sample_qvf):
        from vibeview import diff

        result = diff(str(sample_qvf), str(sample_qvf))
        assert isinstance(result, dict)

    def test_diff_identical_has_common_sections(self, sample_qvf):
        from vibeview import diff

        result = diff(str(sample_qvf), str(sample_qvf))
        assert "structure" in result.get("kinds_common", [])

    def test_diff_identical_empty_diffs(self, sample_qvf):
        from vibeview import diff

        result = diff(str(sample_qvf), str(sample_qvf))
        assert result["kinds_only_a"] == []
        assert result["kinds_only_b"] == []

    def test_diff_identical_zero_rmsd(self, sample_qvf):
        from vibeview import diff

        result = diff(str(sample_qvf), str(sample_qvf))
        assert result["geo_rmsd_a"] == pytest.approx(0.0, abs=0.01)


# ── Volume ───────────────────────────────────────────────────────────────


class TestSDKVolume:
    def test_get_volume_for_density(self, sample_qvf):
        from vibeview import QVFReader, get_volume

        reader = QVFReader(sample_qvf)
        result = get_volume(reader, "density")
        assert result is not None
        assert "origin" in result
        reader.close()

    def test_get_volume_returns_none_for_nonexistent(self, sample_qvf):
        from vibeview import get_volume

        result = get_volume(sample_qvf, "nonexistent_id")
        assert result is None

    def test_get_volume_has_shape(self, sample_qvf):
        from vibeview import get_volume

        result = get_volume(sample_qvf, "density")
        assert result is not None
        assert "shape" in result
        assert result["shape"] == [40, 40, 40]


# ── Capture ──────────────────────────────────────────────────────────────


class TestSDKCapture:
    def test_capture_structure(self, sample_qvf, tmp_path):
        import os

        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
        from vibeview import capture_structure

        png_path = tmp_path / "structure.png"
        ok = capture_structure(sample_qvf, png_path, size=(200, 150))
        assert ok is True
        assert png_path.exists()
        data = png_path.read_bytes()
        assert data[:4] == b"\x89PNG"
        assert len(data) > 100

    def test_capture_structure_custom_size(self, sample_qvf, tmp_path):
        import os

        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
        from vibeview import capture_structure

        png_path = tmp_path / "large.png"
        ok = capture_structure(sample_qvf, png_path, size=(400, 300))
        assert ok is True
        assert png_path.exists()


# ── Validate ─────────────────────────────────────────────────────────────


class TestSDKValidate:
    def test_validate_sample(self, sample_qvf):
        from vibeview import validate

        result = validate(sample_qvf)
        assert "valid" in result
        assert result["valid"] is True
        assert result["n_sections"] >= 1

    def test_validate_from_path(self, sample_qvf):
        from vibeview import validate

        result = validate(str(sample_qvf))
        assert result["valid"] is True

    def test_validate_reader(self, sample_qvf):
        from vibeview import QVFReader, validate

        reader = QVFReader(sample_qvf)
        try:
            result = validate(reader)
            assert result["valid"] is True
        finally:
            reader.close()


# ── Slice ────────────────────────────────────────────────────────────────


class TestSDKSlice:
    def test_slice_keep_list(self, sample_qvf, tmp_path):
        from vibeview import slice_qvf

        out = tmp_path / "sliced.qvf"
        result = slice_qvf(str(sample_qvf), str(out), keep=["structure"])
        assert result.exists()
        assert result.suffix == ".qvf"

    def test_slice_keep_on_closed_reader(self, sample_qvf, tmp_path):
        from vibeview import QVFReader, slice_qvf

        reader = QVFReader(sample_qvf)
        reader.close()
        out = tmp_path / "sliced_closed.qvf"
        # slice_qvf opens its own reader from the path
        result = slice_qvf(str(sample_qvf), str(out), keep=["structure"])
        assert result.exists()


# ── Error handling ───────────────────────────────────────────────────────


class TestSDKErrorHandling:
    def test_qvf_open_nonexistent(self):
        from vibeview.qvf import QVFOpenError

        with pytest.raises(QVFOpenError):
            from vibeview import QVFReader

            QVFReader("/nonexistent/path_12345.qvf")

    def test_info_on_nonexistent_path(self):
        from vibeview.qvf import QVFOpenError

        with pytest.raises(QVFOpenError):
            from vibeview import info

            info("/nonexistent/path_12345.qvf")

    def test_sections_on_nonexistent_path(self):
        from vibeview.qvf import QVFOpenError

        with pytest.raises(QVFOpenError):
            from vibeview import sections

            sections("/nonexistent/path_12345.qvf")

    def test_validate_on_nonexistent_path(self):
        from vibeview.qvf import QVFOpenError

        with pytest.raises(QVFOpenError):
            from vibeview import validate

            validate("/nonexistent/path_12345.qvf")


# ── get_table (tabulatable kinds) ───────────────────────────────────────


class TestSDKGetTable:
    def test_get_table_raises_on_unsupported_kind(self, sample_qvf):
        from vibeview import get_table

        with pytest.raises(ValueError, match="not tabulatable"):
            get_table(sample_qvf, "structure")

    def test_get_table_raises_on_absent_kind(self, sample_qvf):
        from vibeview import get_table

        with pytest.raises(ValueError, match="no .* section"):
            get_table(sample_qvf, "vibrations")

    def test_atom_properties_table_smoke(self, sample_qvf):
        """atom_properties kind is tabulatable; should raise
        ValueError (no section) rather than an unexpected error."""
        from vibeview import get_table

        with pytest.raises(ValueError):
            get_table(sample_qvf, "atom_properties")
