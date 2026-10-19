"""vibe-view#60: damaged archives report the underlying error.

Three gaps found while verifying #51:

1. ``%vibeview --capture`` (``jupyter._capture_section``) said only "could
   not render section 'density'" for a damaged volume member.
2. ``diff`` dropped the geometry RMSD silently when a structure could not
   be read.
3. ``diff --json`` reported an open error as plain text on stderr.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner


def _damaged_copy(source: Path, dest: Path, member: str) -> Path:
    """``source`` with ``member`` overwritten, so its SHA-256 no longer matches."""
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(dest, "w") as out:
        for name in src.namelist():
            data = src.read(name)
            if name == member:
                data = bytes(b ^ 0xFF for b in data)
            out.writestr(name, data)
    return dest


def _read_error(path: Path, read) -> str:
    """The message the reader raises for the damaged member."""
    from vibeview.qvf import QVFReader

    with QVFReader(path) as reader, pytest.raises(Exception) as excinfo:
        read(reader)
    return str(excinfo.value)


# ── 1. capture keeps the read error ─────────────────────────────────────────


def test_jupyter_capture_names_the_read_error(sample_qvf, tmp_path) -> None:
    from vibeview.jupyter import _capture_section

    bad = _damaged_copy(sample_qvf, tmp_path / "bad.qvf", "sections/density.dat")
    reason = _read_error(bad, lambda r: r.read_volume_data("density"))

    with pytest.raises(RuntimeError) as excinfo:
        _capture_section(bad, "volume.density")
    assert str(excinfo.value) == f"could not render section 'density': {reason}"


def test_render_volume_raises_with_the_cause_and_capture_volume_stays_boolean(
    sample_qvf, tmp_path
) -> None:
    from vibeview.capture import CaptureError, capture_volume, render_volume
    from vibeview.qvf import QVFReader

    bad = _damaged_copy(sample_qvf, tmp_path / "bad.qvf", "sections/density.dat")
    reason = _read_error(bad, lambda r: r.read_volume_data("density"))

    with QVFReader(bad) as reader:
        with pytest.raises(CaptureError) as excinfo:
            render_volume(reader, "density", tmp_path / "out.png")
        assert reason in str(excinfo.value)
        assert excinfo.value.__cause__ is not None
        # The public bool contract is unchanged.
        assert capture_volume(reader, "density", tmp_path / "out.png") is False


# ── 2. diff says why the RMSD is missing ────────────────────────────────────


def test_diff_reports_an_unreadable_structure(sample_qvf, tmp_path) -> None:
    from vibeview.api import diff
    from vibeview.cli import main

    bad = _damaged_copy(sample_qvf, tmp_path / "bad.qvf", "sections/structure.json")
    reason = _read_error(bad, lambda r: r.read_structure())

    result = diff(sample_qvf, bad)
    assert result["geo_rmsd_a"] is None
    assert result["geo_rmsd_skipped"] == f"structure B: {reason}"

    text = CliRunner().invoke(main, ["diff", str(sample_qvf), str(bad)])
    assert text.exit_code == 0, text.output
    assert f"skipped: structure B: {reason}" in text.output


def test_diff_reports_differing_atom_counts(sample_qvf, tmp_path) -> None:
    from vibeview.api import diff
    from vibeview.qvf import QVFReader, StructureData

    real = QVFReader.read_structure

    def one_atom_less(self: QVFReader) -> StructureData:
        data = real(self)
        if Path(self.path).name == "fewer.qvf":
            data.atoms = data.atoms[:-1]
        return data

    fewer = tmp_path / "fewer.qvf"
    fewer.write_bytes(sample_qvf.read_bytes())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(QVFReader, "read_structure", one_atom_less)
        result = diff(sample_qvf, fewer)
    assert result["geo_rmsd_a"] is None
    assert result["geo_rmsd_skipped"] == "atom counts differ (3 vs 2)"


def test_diff_of_identical_files_takes_the_rmsd(sample_qvf) -> None:
    from vibeview.api import diff

    result = diff(sample_qvf, sample_qvf)
    assert result["geo_rmsd_a"] == pytest.approx(0.0)
    assert result["geo_rmsd_skipped"] is None


# ── 3. diff --json reports open errors as JSON ──────────────────────────────


@pytest.mark.parametrize("json_flag", [True, False])
def test_diff_open_error_follows_the_output_mode(sample_qvf, tmp_path, json_flag) -> None:
    from vibeview.cli import main

    broken = tmp_path / "broken.qvf"
    broken.write_bytes(b"not a zip archive")
    args = ["diff", str(sample_qvf), str(broken)] + (["--json"] if json_flag else [])

    result = CliRunner().invoke(main, args)
    assert result.exit_code == 1
    if json_flag:
        payload = json.loads(result.stdout)
        assert payload["file"] == "B"
        assert payload["path"] == "broken.qvf"
        assert "zip" in payload["error"]
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        assert "Error opening B (broken.qvf):" in result.stderr
