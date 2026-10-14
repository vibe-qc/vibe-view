"""vibe-view#60 follow-up: ``diff`` of a structure with no atoms.

!105 moved the RMSD out of its ``try`` so that an unreadable structure is
reported instead of dropped. A readable structure with zero atoms then
reached ``align.rmsd`` with a ``(0,)`` array and raised ``AxisError``:
``diff`` exited 1 through the last-resort catch, ``api.diff`` raised, and
``diff --json`` printed the error as plain text on stderr. Before !105 the
same diff succeeded without an RMSD.

Now the RMSD is skipped with a reason, and ``diff --json`` reports every
error as a ``{"error": ...}`` object on stdout, as ``info --json`` does.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner


def _with_atoms(source: Path, dest: Path, n_keep: int) -> Path:
    """``source`` with only its first ``n_keep`` atoms; the member SHA-256 is fixed."""
    with zipfile.ZipFile(source) as src:
        items = {name: src.read(name) for name in src.namelist()}
    manifest = json.loads(items["manifest.json"])
    section = next(s for s in manifest["sections"] if s["id"] == "structure")
    member = section["members"]["structure"]
    structure = json.loads(items[member["path"]])
    structure["atoms"] = structure["atoms"][:n_keep]
    items[member["path"]] = json.dumps(structure).encode()
    member["sha256"] = hashlib.sha256(items[member["path"]]).hexdigest()
    items["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(dest, "w") as out:
        for name, data in items.items():
            out.writestr(name, data)
    return dest


@pytest.fixture
def zero_qvf(sample_qvf, tmp_path) -> Path:
    return _with_atoms(sample_qvf, tmp_path / "zero.qvf", 0)


def _invoke(args: list[str]):
    from vibeview.cli import main

    return CliRunner().invoke(main, args)


# ── api.diff ────────────────────────────────────────────────────────────────


def test_api_diff_zero_vs_zero_skips_the_rmsd(zero_qvf) -> None:
    from vibeview.api import diff
    from vibeview.qvf import QVFReader

    with QVFReader(zero_qvf) as reader:
        assert reader.read_structure().atoms == []

    result = diff(zero_qvf, zero_qvf)
    assert result["geo_rmsd_a"] is None
    assert result["geo_rmsd_skipped"] == "no atoms (A: 0, B: 0)"
    # The rest of the report stands.
    assert result["n_sections_a"] == result["n_sections_b"]


@pytest.mark.parametrize("zero_side", ["A", "B"])
def test_api_diff_zero_vs_normal_skips_the_rmsd(sample_qvf, zero_qvf, zero_side) -> None:
    from vibeview.api import diff

    a, b = (zero_qvf, sample_qvf) if zero_side == "A" else (sample_qvf, zero_qvf)
    result = diff(a, b)
    assert result["geo_rmsd_a"] is None
    expected = "no atoms (A: 0, B: 3)" if zero_side == "A" else "no atoms (A: 3, B: 0)"
    assert result["geo_rmsd_skipped"] == expected


# ── CLI text mode ───────────────────────────────────────────────────────────


def test_cli_diff_zero_vs_zero_text(zero_qvf) -> None:
    result = _invoke(["diff", str(zero_qvf), str(zero_qvf)])
    assert result.exit_code == 0, result.output
    assert "Geom. RMSD" in result.stdout
    assert "skipped: no atoms (A: 0, B: 0)" in result.stdout
    assert "AxisError" not in result.output
    assert result.stderr == ""


def test_cli_diff_zero_vs_normal_text(sample_qvf, zero_qvf) -> None:
    result = _invoke(["diff", str(sample_qvf), str(zero_qvf)])
    assert result.exit_code == 0, result.output
    assert "skipped: no atoms (A: 3, B: 0)" in result.stdout
    assert result.stderr == ""


# ── CLI --json mode ─────────────────────────────────────────────────────────


def test_cli_diff_json_zero_vs_zero(zero_qvf) -> None:
    result = _invoke(["diff", "--json", str(zero_qvf), str(zero_qvf)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["geo_rmsd_a"] is None
    assert payload["geo_rmsd_skipped"] == "no atoms (A: 0, B: 0)"
    assert result.stderr == ""


def test_cli_diff_json_zero_vs_normal(sample_qvf, zero_qvf) -> None:
    result = _invoke(["diff", "--json", str(zero_qvf), str(sample_qvf)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["geo_rmsd_a"] is None
    assert payload["geo_rmsd_skipped"] == "no atoms (A: 0, B: 3)"
    assert result.stderr == ""


# ── diff --json reports every error as JSON ─────────────────────────────────


@pytest.mark.parametrize("broken_side", ["A", "B"])
def test_cli_diff_json_open_error_is_a_json_object(sample_qvf, tmp_path, broken_side) -> None:
    broken = tmp_path / "broken.qvf"
    broken.write_bytes(b"not a zip archive")
    a, b = (broken, sample_qvf) if broken_side == "A" else (sample_qvf, broken)

    result = _invoke(["diff", "--json", str(a), str(b)])
    assert result.exit_code != 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert "zip" in payload["error"]
    assert payload["file"] == broken_side
    assert payload["path"] == "broken.qvf"


def test_cli_diff_json_unexpected_error_is_a_json_object(sample_qvf, monkeypatch) -> None:
    """Anything the last-resort catch reports is JSON on stdout under --json."""
    import vibeview.api

    def boom(*_args, **_kwargs):
        raise RuntimeError("synthetic failure inside diff")

    monkeypatch.setattr(vibeview.api, "diff", boom)
    result = _invoke(["diff", "--json", str(sample_qvf), str(sample_qvf)])
    assert result.exit_code != 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload == {"error": "RuntimeError: synthetic failure inside diff"}


def test_cli_diff_json_qvf_error_is_a_json_object(sample_qvf, monkeypatch) -> None:
    """A ``QVFError`` raised after open is JSON on stdout under --json."""
    import vibeview.api
    from vibeview.qvf import QVFError

    def boom(*_args, **_kwargs):
        raise QVFError("synthetic archive failure")

    monkeypatch.setattr(vibeview.api, "diff", boom)
    result = _invoke(["diff", "--json", str(sample_qvf), str(sample_qvf)])
    assert result.exit_code != 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {"error": "synthetic archive failure"}


def test_cli_diff_text_unexpected_error_stays_on_stderr(sample_qvf, monkeypatch) -> None:
    """Text mode is unchanged: the last-resort line goes to stderr."""
    import vibeview.api

    def boom(*_args, **_kwargs):
        raise RuntimeError("synthetic failure inside diff")

    monkeypatch.setattr(vibeview.api, "diff", boom)
    result = _invoke(["diff", str(sample_qvf), str(sample_qvf)])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: RuntimeError: synthetic failure inside diff" in result.stderr


# ── still works (guards) ────────────────────────────────────────────────────


def test_diff_identical_normal_files_still_take_the_rmsd(sample_qvf) -> None:
    result = _invoke(["diff", "--json", str(sample_qvf), str(sample_qvf)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["geo_rmsd_a"] == pytest.approx(0.0)
    assert payload["geo_rmsd_skipped"] is None


def test_diff_differing_nonzero_counts_keep_their_reason(sample_qvf, tmp_path) -> None:
    from vibeview.api import diff

    one = _with_atoms(sample_qvf, tmp_path / "one.qvf", 1)
    result = diff(sample_qvf, one)
    assert result["geo_rmsd_a"] is None
    assert result["geo_rmsd_skipped"] == "atom counts differ (3 vs 1)"
