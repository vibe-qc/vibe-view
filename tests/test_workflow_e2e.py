"""End-to-end workflow integration tests.

Tests that span multiple SDK calls in sequence, mimicking real usage
patterns: open -> inspect -> extract -> export -> validate -> slice.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestE2EWorkflow:
    def test_open_inspect_close(self, sample_qvf):
        from vibeview import QVFReader, info, sections

        reader = QVFReader(sample_qvf)
        assert info(reader) is not None
        assert len(sections(reader)) > 0
        reader.close()

    def test_structure_to_xyz_roundtrip(self, sample_qvf, tmp_path):
        from vibeview import QVFReader, export_xyz, get_structure

        reader = QVFReader(sample_qvf)
        sdata = get_structure(reader)
        xyz = export_xyz(reader)
        reader.close()

        assert len(xyz) > 0
        # XYZ should contain all atom symbols from the structure
        symbols_in_structure = {a["symbol"] for a in sdata["atoms"]}
        for sym in symbols_in_structure:
            assert sym in xyz

    def test_validate_and_slice(self, sample_qvf, tmp_path):
        from vibeview import QVFReader, slice_qvf, validate

        reader = QVFReader(sample_qvf)
        v = validate(reader)
        assert isinstance(v, dict)
        assert v["valid"] is True
        reader.close()

        out = tmp_path / "sliced.qvf"
        result = slice_qvf(str(sample_qvf), str(out), keep=["structure"])
        assert result.exists()

    def test_diff_workflow(self, sample_qvf):
        from vibeview import diff

        d = diff(str(sample_qvf), str(sample_qvf))
        assert isinstance(d, dict)
        assert "delta_e_eh" in d

    def test_batch_compare_workflow(self, sample_qvf):
        from vibeview.batch_compare import compare_batch

        result = compare_batch([str(sample_qvf)])
        assert len(result["files"]) == 1
        assert "summary" in result

    def test_full_pipeline_read_only(self, sample_qvf, tmp_path):
        """Exercise every read-only SDK function on the sample."""
        from vibeview import (
            QVFReader,
            diff,
            export_xyz,
            get_structure,
            get_table,
            get_volume,
            has_section,
            info,
            sections,
            validate,
        )

        # -- info
        inf = info(sample_qvf)
        assert inf["program"] == "vibe-qc"

        # -- sections & has_section
        secs = sections(sample_qvf)
        kinds = {s["kind"] for s in secs}
        assert has_section(sample_qvf, "structure")
        assert has_section(sample_qvf, "density")

        # -- get_structure
        sdata = get_structure(sample_qvf)
        assert len(sdata["atoms"]) == 3

        # -- export_xyz
        xyz = export_xyz(sample_qvf)
        assert len(xyz.strip().split("\n")) >= 2

        # -- get_volume
        if "volume.density" in kinds:
            vol = get_volume(sample_qvf, "density")
            assert vol is not None

        # -- validate
        v = validate(sample_qvf)
        assert v["valid"] is True

        # -- diff
        d = diff(sample_qvf, sample_qvf)
        assert d["geo_rmsd_a"] == pytest.approx(0.0, abs=0.01)

        # -- get_table (expect ValueError for non-tabulatable kind)
        with pytest.raises(ValueError):
            get_table(sample_qvf, "vibrations")

    def test_slice_and_reopen(self, sample_qvf, tmp_path):
        """Slice out the structure section, then open and validate the new QVF."""
        from vibeview import QVFReader, get_structure, slice_qvf

        out = tmp_path / "sliced_reopen.qvf"
        slice_qvf(str(sample_qvf), str(out), keep=["structure"])

        # Re-open the sliced QVF
        reader = QVFReader(out)
        sdata = get_structure(reader)
        assert len(sdata["atoms"]) == 3
        # Should not have density
        assert not reader.has_section("density")
        reader.close()

    def test_batch_compare_identical(self, sample_qvf):
        """Batch-compare a single file against itself (should have zero RMSD)."""
        from vibeview.batch_compare import compare_batch

        result = compare_batch([str(sample_qvf), str(sample_qvf)])
        assert len(result["files"]) == 2
        if result.get("rmsd"):
            assert result["rmsd"][1]["rmsd_angstrom"] == pytest.approx(0.0, abs=0.01)

    def test_sections_all_have_required_fields(self, sample_qvf):
        from vibeview import sections

        for s in sections(sample_qvf):
            assert isinstance(s["id"], str)
            assert isinstance(s["kind"], str)
            assert len(s["id"]) > 0
            assert len(s["kind"]) > 0
