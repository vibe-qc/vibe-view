"""Additional CLI edge-case tests for vibe-view."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


class TestCLIInfo:
    def test_info_json(self, runner, sample_qvf):
        from vibeview.cli import main

        result = runner.invoke(main, ["info", str(sample_qvf), "--json"])
        assert result.exit_code == 0

    def test_info_short(self, runner, sample_qvf):
        from vibeview.cli import main

        result = runner.invoke(main, ["info", str(sample_qvf), "--short"])
        assert result.exit_code == 0

    def test_info_nonexistent(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["info", "/nonexistent.qvf"])
        assert result.exit_code != 0


class TestCLIDiff:
    def test_diff_json(self, runner, sample_qvf):
        from vibeview.cli import main

        result = runner.invoke(main, ["diff", str(sample_qvf), str(sample_qvf), "--json"])
        assert result.exit_code == 0

    def test_diff_nonexistent(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["diff", "/a.qvf", "/b.qvf"])
        assert result.exit_code != 0


class TestCLIValidate:
    def test_validate_single(self, runner, sample_qvf):
        from vibeview.cli import main

        result = runner.invoke(main, ["validate", str(sample_qvf)])
        assert result.exit_code in (0, 1)

    def test_validate_multiple(self, runner, sample_qvf):
        from vibeview.cli import main

        result = runner.invoke(main, ["validate", str(sample_qvf), str(sample_qvf)])
        assert result.exit_code in (0, 1)

    def test_validate_nonexistent(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["validate", "/nonexistent.qvf"])
        assert result.exit_code != 0


class TestCLITable:
    def test_table_list(self, runner, sample_qvf):
        from vibeview.cli import main

        result = runner.invoke(main, ["table", str(sample_qvf)])
        assert result.exit_code in (0, 1)

    def test_table_csv(self, runner, sample_qvf):
        from vibeview.cli import main

        result = runner.invoke(main, ["table", str(sample_qvf), "--format", "csv"])
        assert result.exit_code in (0, 1)

    def test_table_json_format(self, runner, sample_qvf):
        from vibeview.cli import main

        result = runner.invoke(main, ["table", str(sample_qvf), "--format", "json"])
        assert result.exit_code in (0, 1)

    def test_table_nonexistent(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["table", "/nonexistent.qvf"])
        assert result.exit_code != 0


class TestCLIExport:
    def test_export_xyz(self, runner, sample_qvf, tmp_path):
        from vibeview.cli import main

        out = tmp_path / "test.xyz"
        result = runner.invoke(main, ["export", str(sample_qvf), "-f", "xyz", "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()

    def test_export_json(self, runner, sample_qvf, tmp_path):
        from vibeview.cli import main

        out = tmp_path / "test.json"
        result = runner.invoke(main, ["export", str(sample_qvf), "-f", "json", "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()

    def test_export_cif(self, runner, sample_qvf, tmp_path):
        from vibeview.cli import main

        out = tmp_path / "test.cif"
        result = runner.invoke(main, ["export", str(sample_qvf), "-f", "cif", "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()

    def test_export_pov(self, runner, sample_qvf, tmp_path):
        from vibeview.cli import main

        out = tmp_path / "test.pov"
        result = runner.invoke(main, ["export", str(sample_qvf), "-f", "pov", "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()

    def test_export_blend(self, runner, sample_qvf, tmp_path):
        from vibeview.cli import main

        out = tmp_path / "test.py"
        result = runner.invoke(main, ["export", str(sample_qvf), "-f", "blend", "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()

    def test_export_svg(self, runner, sample_qvf, tmp_path):
        from vibeview.cli import main

        out = tmp_path / "test.svg"
        result = runner.invoke(main, ["export", str(sample_qvf), "-f", "svg", "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()

    def test_export_default_filename(self, runner, sample_qvf, tmp_path):
        from vibeview.cli import main

        cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            result = runner.invoke(main, ["export", str(sample_qvf), "-f", "xyz"])
            assert result.exit_code == 0
        finally:
            os.chdir(cwd)


class TestCLIVQFeatures:
    def test_vq_features_empty(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["vq-features"])
        assert result.exit_code == 0

    def test_vq_features_specific(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["vq-features", "spin_density"])
        assert result.exit_code == 0

    def test_vq_features_unknown(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["vq-features", "nonexistent_feature"])
        assert result.exit_code == 0


class TestCLICapture:
    def test_capture_help(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["capture", "--help"])
        assert result.exit_code == 0
        assert "structure" in result.output


class TestCLISlice:
    def test_slice_help(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["slice", "--help"])
        assert result.exit_code == 0

    def test_slice_no_args(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["slice"])
        assert result.exit_code != 0  # Requires a file


class TestCLIMerge:
    def test_merge_help(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["merge", "--help"])
        assert result.exit_code == 0

    def test_merge_preserves_section_peer_fields(self, runner, tmp_path):
        """Regression: merge used to flatten sections to id/kind/members/
        label, silently dropping kind-specific peer fields — run.record's
        REQUIRED `program`, reaction.waypoints' `trajectory_ref`,
        volume.difference's operand links — producing schema-invalid
        archives."""
        import hashlib
        import json
        import zipfile

        from vibeview.cli import main

        def _write(path, sections, blobs):
            manifest = {
                "qvf_version": 1,
                "source": {"program": "t", "version": "1", "calculation": "m"},
                "sections": sections,
            }
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("manifest.json", json.dumps(manifest))
                for zpath, data in blobs.items():
                    zf.writestr(zpath, data)

        log = b"SCF converged\n"
        a = tmp_path / "a.qvf"
        _write(
            a,
            [
                {
                    "id": "run_record0",
                    "kind": "run.record",
                    "program": "orca",
                    "program_version": "6.0.1",
                    "members": {
                        "log": {
                            "path": "r/log.txt",
                            "format": "binary",
                            "sha256": hashlib.sha256(log).hexdigest(),
                        }
                    },
                }
            ],
            {"r/log.txt": log},
        )
        bib = b"@misc{x}\n"
        b = tmp_path / "b.qvf"
        _write(
            b,
            [
                {
                    "id": "cites",
                    "kind": "citations",
                    "members": {
                        "references": {
                            "path": "c/refs.bib",
                            "format": "binary",
                            "sha256": hashlib.sha256(bib).hexdigest(),
                        }
                    },
                }
            ],
            {"c/refs.bib": bib},
        )
        out = tmp_path / "merged.qvf"
        result = runner.invoke(main, ["merge", str(a), str(b), "-o", str(out)])
        assert result.exit_code == 0, result.output
        with zipfile.ZipFile(out) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        rr = [s for s in manifest["sections"] if s["kind"] == "run.record"][0]
        assert rr["program"] == "orca"
        assert rr["program_version"] == "6.0.1"


class TestCLIExamples:
    def test_examples(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["examples"])
        assert result.exit_code == 0


class TestCLIQuickstart:
    def test_quickstart_help(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["quickstart", "--help"])
        assert result.exit_code == 0


class TestCLIRecent:
    def test_recent(self, runner):
        from vibeview.cli import main

        result = runner.invoke(main, ["recent"])
        assert result.exit_code == 0
