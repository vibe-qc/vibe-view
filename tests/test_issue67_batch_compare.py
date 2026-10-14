"""vibe-view#67: ``batch-compare`` says why an RMSD or a file is missing.

The gaps #60 closed for ``diff`` were still open here:

1. The reference structure was read outside the per-file ``try``, so one
   unreadable reference structure aborted the whole batch, and ``--json``
   printed the error as plain text on stderr.
2. A reference with no atoms reported an RMSD of 0.0, and another 0-atom
   file compared against it got ``None`` with no reason.
3. An unreadable non-reference structure got ``None`` with no reason, and a
   file that did not open vanished from the report, so ``-r`` silently
   counted a different file.

``align.rmsd`` also raised ``AxisError`` for a ``(0,)`` array and returned
``nan`` with a warning for ``(0, 3)``; ``kabsch_fit`` did the same for
``(0, 3)``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import warnings
import zipfile
from pathlib import Path

import numpy as np
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


def _damaged_copy(source: Path, dest: Path, member: str) -> Path:
    """``source`` with ``member`` overwritten, so its SHA-256 no longer matches."""
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(dest, "w") as out:
        for name in src.namelist():
            data = src.read(name)
            if name == member:
                data = bytes(b ^ 0xFF for b in data)
            out.writestr(name, data)
    return dest


def _read_error(path: Path) -> str:
    """The message the reader raises for the damaged structure member."""
    from vibeview.qvf import QVFReader

    with QVFReader(path) as reader, pytest.raises(Exception) as excinfo:
        reader.read_structure()
    return str(excinfo.value)


def _invoke(args: list[str]):
    from vibeview.cli import main

    return CliRunner().invoke(main, args)


@pytest.fixture
def copy_qvf(sample_qvf, tmp_path) -> Path:
    return Path(shutil.copy(sample_qvf, tmp_path / "copy.qvf"))


@pytest.fixture
def zero_qvf(sample_qvf, tmp_path) -> Path:
    return _with_atoms(sample_qvf, tmp_path / "zero.qvf", 0)


@pytest.fixture
def one_qvf(sample_qvf, tmp_path) -> Path:
    return _with_atoms(sample_qvf, tmp_path / "one.qvf", 1)


@pytest.fixture
def bad_structure_qvf(sample_qvf, tmp_path) -> Path:
    return _damaged_copy(sample_qvf, tmp_path / "badstruct.qvf", "sections/structure.json")


@pytest.fixture
def not_a_qvf(tmp_path) -> Path:
    path = tmp_path / "broken.qvf"
    path.write_bytes(b"not a zip archive")
    return path


# ── 1. an unreadable reference structure ────────────────────────────────────


def test_unreadable_reference_structure_is_reported_on_every_row(sample_qvf, bad_structure_qvf):
    from vibeview.batch_compare import compare_batch

    reason = _read_error(bad_structure_qvf)
    result = compare_batch([str(bad_structure_qvf), str(sample_qvf)], reference_idx=0)

    assert result["files"] == [str(bad_structure_qvf), str(sample_qvf)]
    assert result["rmsd"] == [
        {
            "path": str(bad_structure_qvf),
            "rmsd_angstrom": None,
            "note": f"cannot read structure: {reason}",
        },
        {
            "path": str(sample_qvf),
            "rmsd_angstrom": None,
            "note": "reference structure cannot be read",
        },
    ]
    # The rest of the comparison stands.
    assert len(result["energies"]) == 2
    assert result["sections"]["structure"] == [True, True]
    assert f"badstruct.qvf: RMSD skipped: cannot read structure: {reason}" in result["summary"]
    assert (
        f"{sample_qvf.name}: RMSD skipped: reference structure cannot be read"
        in (result["summary"])
    )


def test_cli_unreadable_reference_structure_text(sample_qvf, bad_structure_qvf):
    reason = _read_error(bad_structure_qvf)
    result = _invoke(["batch-compare", str(bad_structure_qvf), str(sample_qvf)])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert f"RMSD skipped: cannot read structure: {reason}" in result.stdout
    assert "RMSD skipped: reference structure cannot be read" in result.stdout
    assert "| badstruct.qvf | " in result.stdout
    assert f"| — | cannot read structure: {reason} |" in result.stdout


def test_cli_unreadable_reference_structure_json(sample_qvf, bad_structure_qvf):
    reason = _read_error(bad_structure_qvf)
    result = _invoke(["batch-compare", "--json", str(bad_structure_qvf), str(sample_qvf)])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["rmsd"][0]["note"] == f"cannot read structure: {reason}"
    assert payload["rmsd"][1]["note"] == "reference structure cannot be read"
    assert payload["skipped"] == []


# ── 2. a reference with no atoms ────────────────────────────────────────────


def test_zero_atom_reference_has_no_rmsd_and_says_why(sample_qvf, zero_qvf, tmp_path):
    from vibeview.batch_compare import compare_batch

    other_zero = _with_atoms(sample_qvf, tmp_path / "zero2.qvf", 0)
    result = compare_batch([str(zero_qvf), str(other_zero), str(sample_qvf)], reference_idx=0)

    assert [r["rmsd_angstrom"] for r in result["rmsd"]] == [None, None, None]
    assert [r["note"] for r in result["rmsd"]] == [
        "no atoms",
        "no atoms (file: 0, reference: 0)",
        "no atoms (file: 3, reference: 0)",
    ]
    assert "zero.qvf: RMSD skipped: no atoms\n" in result["summary"]
    assert "zero2.qvf: RMSD skipped: no atoms (file: 0, reference: 0)" in result["summary"]


def test_zero_atom_file_against_a_normal_reference(sample_qvf, zero_qvf):
    from vibeview.batch_compare import compare_batch

    result = compare_batch([str(sample_qvf), str(zero_qvf)])
    assert result["rmsd"][0] == {"path": str(sample_qvf), "rmsd_angstrom": 0.0, "note": None}
    assert result["rmsd"][1] == {
        "path": str(zero_qvf),
        "rmsd_angstrom": None,
        "note": "no atoms (file: 0, reference: 3)",
    }


# ── 3. an unreadable non-reference structure, differing atom counts ─────────


def test_unreadable_structure_carries_the_reason(sample_qvf, bad_structure_qvf):
    from vibeview.batch_compare import compare_batch

    reason = _read_error(bad_structure_qvf)
    result = compare_batch([str(sample_qvf), str(bad_structure_qvf)])
    assert result["rmsd"][0]["rmsd_angstrom"] == 0.0
    assert result["rmsd"][1]["rmsd_angstrom"] is None
    assert result["rmsd"][1]["note"] == f"cannot read structure: {reason}"


def test_differing_atom_counts_carry_the_reason(sample_qvf, one_qvf):
    from vibeview.batch_compare import compare_batch

    result = compare_batch([str(sample_qvf), str(one_qvf)])
    assert result["rmsd"][1]["rmsd_angstrom"] is None
    assert result["rmsd"][1]["note"] == "atom counts differ (file: 1, reference: 3)"


# ── files that do not open ──────────────────────────────────────────────────


def test_unopenable_file_is_skipped_with_its_reason(sample_qvf, not_a_qvf):
    from vibeview.batch_compare import batch_compare_to_table, compare_batch

    result = compare_batch([str(sample_qvf), str(not_a_qvf)])
    assert result["files"] == [str(sample_qvf)]
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["path"] == str(not_a_qvf)
    assert "zip" in result["skipped"][0]["reason"]
    assert result["summary"].startswith("Batch comparison: 1 files (1 skipped)\n")
    assert "  broken.qvf: skipped: " in result["summary"]
    table = batch_compare_to_table(result)
    assert "| broken.qvf | — | — | skipped: " in table


def test_unopenable_reference_file_is_a_value_error(sample_qvf, not_a_qvf):
    from vibeview.batch_compare import compare_batch

    with pytest.raises(ValueError, match=r"reference file .*broken\.qvf cannot be read: .*zip"):
        compare_batch([str(not_a_qvf), str(sample_qvf)], reference_idx=0)


def test_cli_unopenable_reference_file_text(sample_qvf, not_a_qvf):
    result = _invoke(["batch-compare", str(not_a_qvf), str(sample_qvf)])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: reference file" in result.stderr
    assert "broken.qvf cannot be read: " in result.stderr


def test_cli_unopenable_reference_file_json(sample_qvf, not_a_qvf):
    result = _invoke(["batch-compare", "--json", str(not_a_qvf), str(sample_qvf)])
    assert result.exit_code == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert set(payload) == {"error"}
    assert "reference file" in payload["error"]
    assert "zip" in payload["error"]


def test_reference_index_counts_the_files_as_given(sample_qvf, not_a_qvf, one_qvf):
    """Before #67 the index counted only the files that opened, so ``-r 1``
    here silently made ``one.qvf`` the reference."""
    from vibeview.batch_compare import compare_batch

    result = compare_batch([str(not_a_qvf), str(sample_qvf), str(one_qvf)], reference_idx=1)
    assert result["files"] == [str(sample_qvf), str(one_qvf)]
    assert result["rmsd"][0]["rmsd_angstrom"] == 0.0
    assert result["rmsd"][1]["note"] == "atom counts differ (file: 1, reference: 3)"
    assert f"\nReference: {sample_qvf.name}\n" in result["summary"]


def test_reference_index_out_of_range_counts_the_files_as_given(sample_qvf, not_a_qvf):
    from vibeview.batch_compare import compare_batch

    with pytest.raises(ValueError, match=r"out of range \(0\.\.1 for 2 file\(s\)\)"):
        compare_batch([str(sample_qvf), str(not_a_qvf)], reference_idx=2)


def test_every_reader_is_closed_when_the_reference_does_not_open(
    sample_qvf, not_a_qvf, monkeypatch
):
    from vibeview import qvf
    from vibeview.batch_compare import compare_batch

    closes: list[int] = []
    original_close = qvf.QVFReader.close

    def counted_close(self):
        closes.append(id(self))
        original_close(self)

    monkeypatch.setattr(qvf.QVFReader, "close", counted_close)
    with pytest.raises(ValueError, match="reference file"):
        compare_batch([str(sample_qvf), str(not_a_qvf)], reference_idx=1)
    assert len(closes) == 1


# ── --json reports every error as JSON ──────────────────────────────────────


@pytest.mark.parametrize("args", [
    [],
    ["missing-reference.qvf", "missing-mobile.qvf"],
    ["-r", "invalid"],
    ["--unknown-option"],
    ["--reference"],
])
@pytest.mark.parametrize("json_first", [True, False])
def test_cli_json_argument_errors_are_json(args, json_first, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    options = ["--json", *args] if json_first else [*args, "--json"]
    result = _invoke(["batch-compare", *options])
    assert result.exit_code == 2
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert set(payload) == {"error"}
    assert payload["error"]
    assert "Usage:" not in result.stdout


def test_cli_json_filename_after_terminator_is_not_an_option(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _invoke(["batch-compare", "--", "--json"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Usage:" in result.stderr


def test_cli_json_out_of_range_reference_is_a_json_object(sample_qvf, copy_qvf):
    result = _invoke(["batch-compare", "--json", str(sample_qvf), str(copy_qvf), "-r", "5"])
    assert result.exit_code == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload == {"error": "reference index 5 out of range (0..1 for 2 file(s))"}


def test_cli_json_arity_error_is_a_json_object(sample_qvf):
    result = _invoke(["batch-compare", "--json", str(sample_qvf)])
    assert result.exit_code == 1
    assert result.stderr == ""
    assert json.loads(result.stdout) == {"error": "Need at least 2 .qvf files to compare."}


def test_cli_json_unexpected_error_is_a_json_object(sample_qvf, copy_qvf, monkeypatch):
    """Anything the last-resort catch reports is JSON on stdout under --json."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("synthetic failure inside batch-compare")

    monkeypatch.setattr("vibeview.batch_compare.compare_batch", boom)
    result = _invoke(["batch-compare", "--json", str(sample_qvf), str(copy_qvf)])
    assert result.exit_code == 1
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "error": "RuntimeError: synthetic failure inside batch-compare"
    }


def test_cli_text_errors_stay_on_stderr(sample_qvf, copy_qvf):
    result = _invoke(["batch-compare", str(sample_qvf), str(copy_qvf), "-r", "5"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: reference index 5 out of range (0..1 for 2 file(s))" in result.stderr


# ── text output ─────────────────────────────────────────────────────────────


def test_table_carries_the_reason_in_a_note_column(sample_qvf, one_qvf):
    from vibeview.batch_compare import batch_compare_to_table, compare_batch

    table = batch_compare_to_table(compare_batch([str(sample_qvf), str(one_qvf)]))
    lines = table.splitlines()
    assert lines[0] == "| File | Energy (Eh) | RMSD (Å) | Note |"
    assert lines[2].startswith(f"| {sample_qvf.name} | ")
    assert lines[2].endswith("| 0.0000 |  |")
    assert lines[3].startswith("| one.qvf | ")
    assert lines[3].endswith("| — | atom counts differ (file: 1, reference: 3) |")


def test_table_cells_keep_the_table_intact():
    from vibeview.batch_compare import batch_compare_to_table

    results = {
        "files": ["a.qvf"],
        "energies": [{"path": "a.qvf", "scf_energy_eh": None}],
        "rmsd": [{"path": "a.qvf", "rmsd_angstrom": None, "note": "line one\nline | two"}],
        "skipped": [{"path": "b.qvf", "reason": "bad | thing"}],
    }
    lines = batch_compare_to_table(results).splitlines()
    assert lines[2] == "| a.qvf | — | — | line one line \\| two |"
    assert lines[3] == "| b.qvf | — | — | skipped: bad \\| thing |"


def test_summary_names_each_file_and_the_reference(sample_qvf, copy_qvf):
    from vibeview.batch_compare import compare_batch

    result = compare_batch([str(sample_qvf), str(copy_qvf)])
    lines = result["summary"].splitlines()
    assert lines[0] == "Batch comparison: 2 files"
    assert lines[1] == f"Reference: {sample_qvf.name}"
    assert "  copy.qvf: RMSD to ref = 0.0000 Å" in lines
    # The reference is not compared with itself.
    assert not any(line.startswith(f"  {sample_qvf.name}: RMSD") for line in lines)


# ── align refuses empty input ───────────────────────────────────────────────


@pytest.mark.parametrize("shape", [(0,), (0, 3)])
def test_rmsd_refuses_empty_input(shape):
    from vibeview.align import rmsd

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(ValueError, match="^rmsd: "):
            rmsd(np.zeros(shape), np.zeros(shape))


def test_kabsch_fit_refuses_empty_input():
    from vibeview.align import kabsch_fit

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(ValueError, match="^kabsch_fit: need at least one point"):
            kabsch_fit(np.zeros((0, 3)), np.zeros((0, 3)))


def test_rmsd_and_kabsch_fit_still_work():
    from vibeview.align import kabsch_fit, rmsd

    pos = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    assert rmsd(pos, pos) == pytest.approx(0.0)
    _aligned, r = kabsch_fit(pos, pos)
    assert r == pytest.approx(0.0)
