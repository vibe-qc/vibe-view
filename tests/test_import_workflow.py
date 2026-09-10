"""Tests for persistent loose-file imports."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

import vibeview.import_workflow as import_workflow
from vibeview.cli import main
from vibeview.import_workflow import (
    ImportWorkflowError,
    execute_import,
    plan_imports,
)
from vibeview.qvf import QVFReader


def _write_xyz(path: Path, symbol: str = "He") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"1\nfixture\n{symbol} 0 0 0\n", encoding="utf-8")


def test_single_import_defaults_to_sibling_qvf(tmp_path: Path) -> None:
    source = tmp_path / "calculation.xyz"
    _write_xyz(source)

    plan = plan_imports((source,))[0]
    assert plan.destination == tmp_path / "calculation.qvf"
    result = execute_import(plan)

    assert result.destination == plan.destination
    assert result.detected_format == "xyz"
    assert result.section_kinds == ("structure",)
    assert len(result.sha256) == 64
    reader = QVFReader(result.destination)
    reader.close()


def test_directory_import_scans_recursively_and_skips_hidden(tmp_path: Path) -> None:
    source_dir = tmp_path / "job"
    _write_xyz(source_dir / "a.xyz", "H")
    _write_xyz(source_dir / "nested" / "b.xyz", "Ne")
    _write_xyz(source_dir / ".cache" / "hidden.xyz", "Ar")
    (source_dir / "notes.txt").write_text("ignore", encoding="utf-8")
    output_dir = tmp_path / "converted"

    plans = plan_imports((source_dir,), output=output_dir)
    assert [plan.destination.name for plan in plans] == ["a.qvf", "b.qvf"]
    for plan in plans:
        execute_import(plan)
    assert sorted(path.name for path in output_dir.iterdir()) == ["a.qvf", "b.qvf"]


def test_directory_rerun_does_not_reimport_its_output_tree(tmp_path: Path) -> None:
    source_dir = tmp_path / "job"
    _write_xyz(source_dir / "a.xyz", "H")
    output_dir = source_dir / "vibe-view-imports"

    first = plan_imports((source_dir,), output=output_dir)
    execute_import(first[0])
    second = plan_imports((source_dir,), output=output_dir)

    assert [plan.source.name for plan in second] == ["a.xyz"]
    assert [plan.destination.name for plan in second] == ["a.qvf"]


def test_multiple_inputs_get_collision_safe_names(tmp_path: Path) -> None:
    first = tmp_path / "one" / "same.xyz"
    second = tmp_path / "two" / "same.xyz"
    _write_xyz(first)
    _write_xyz(second)

    plans = plan_imports((first, second), output=tmp_path / "out")
    assert [plan.destination.name for plan in plans] == ["same.qvf", "same_2.qvf"]


def test_existing_output_requires_force(tmp_path: Path) -> None:
    source = tmp_path / "molecule.xyz"
    _write_xyz(source)
    plan = plan_imports((source,))[0]
    plan.destination.write_bytes(b"keep me")

    with pytest.raises(ImportWorkflowError, match="--force"):
        execute_import(plan)
    assert plan.destination.read_bytes() == b"keep me"

    execute_import(plan, force=True)
    reader = QVFReader(plan.destination)
    reader.close()


def test_racing_writer_is_not_overwritten(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "molecule.xyz"
    _write_xyz(source)
    plan = plan_imports((source,))[0]
    convert = import_workflow.convert_to_qvf

    def create_racing_output(*args, **kwargs):
        payload = convert(*args, **kwargs)
        plan.destination.write_bytes(b"created by another process")
        return payload

    monkeypatch.setattr(import_workflow, "convert_to_qvf", create_racing_output)

    with pytest.raises(ImportWorkflowError, match="--force"):
        execute_import(plan)
    assert plan.destination.read_bytes() == b"created by another process"


def test_published_qvf_respects_a_restrictive_umask(tmp_path: Path) -> None:
    source = tmp_path / "molecule.xyz"
    _write_xyz(source)
    destination = plan_imports((source,))[0].destination

    previous_umask = os.umask(0o077)
    try:
        execute_import(plan_imports((source,))[0])
    finally:
        os.umask(previous_umask)

    assert destination.stat().st_mode & 0o077 == 0


def test_publication_errors_are_actionable(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "molecule.xyz"
    _write_xyz(source)
    plan = plan_imports((source,))[0]

    def refuse_link(*_args, **_kwargs):
        raise PermissionError("read-only destination")

    monkeypatch.setattr(import_workflow.os, "link", refuse_link)

    with pytest.raises(ImportWorkflowError, match="could not write.*read-only destination"):
        execute_import(plan)
    assert not plan.destination.exists()


def test_qvf_cannot_overwrite_itself_by_default(tmp_path: Path) -> None:
    source = tmp_path / "already.qvf"
    source.write_bytes(b"placeholder")
    with pytest.raises(ImportWorkflowError, match="already a QVF"):
        plan_imports((source,))


def test_unsupported_explicit_file_has_actionable_error(tmp_path: Path) -> None:
    source = tmp_path / "calculation.out"
    source.write_text("unknown output", encoding="utf-8")
    with pytest.raises(ImportWorkflowError, match="vibe-view formats"):
        plan_imports((source,))


def test_import_cli_persists_and_reports_qvf(tmp_path: Path) -> None:
    source = tmp_path / "molecule.xyz"
    destination = tmp_path / "result.qvf"
    _write_xyz(source)

    result = CliRunner().invoke(
        main,
        ["import", str(source), "--output", str(destination)],
    )
    assert result.exit_code == 0, result.output
    assert destination.is_file()
    assert "Imported" in result.output
    assert "source: vibe-view" in result.output
    assert "sha256:" in result.output


def test_import_cli_directory_preflights_existing_outputs(tmp_path: Path) -> None:
    source_dir = tmp_path / "jobs"
    _write_xyz(source_dir / "one.xyz", "H")
    _write_xyz(source_dir / "two.xyz", "He")
    output_dir = tmp_path / "qvf"
    output_dir.mkdir()
    sentinel = output_dir / "two.qvf"
    sentinel.write_bytes(b"user data")

    result = CliRunner().invoke(
        main,
        ["import", str(source_dir), "--output", str(output_dir)],
    )
    assert result.exit_code != 0
    assert "--force" in result.output
    assert sentinel.read_bytes() == b"user data"
    assert not (output_dir / "one.qvf").exists()


def test_import_cli_forced_unknown_format_is_actionable(tmp_path: Path) -> None:
    source = tmp_path / "calculation.out"
    source.write_text("external output", encoding="utf-8")

    result = CliRunner().invoke(main, ["import", str(source), "--from", "missing"])
    assert result.exit_code != 0
    assert "vibe-view formats" in result.output


def test_malformed_python_input_is_a_click_error(tmp_path: Path) -> None:
    source = tmp_path / "broken.py"
    source.write_text("not a valid Python input )\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["import", str(source)])

    assert result.exit_code != 0
    assert "Error:" in result.output
    assert "broken.py" in result.output
    assert not source.with_suffix(".qvf").exists()


def test_malformed_cube_input_is_a_click_error(tmp_path: Path) -> None:
    source = tmp_path / "broken.cube"
    source.write_text(
        "comment\ncomment\n0\n1 1 0 0\n1 0 1 0\n1 0 0 1\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["import", str(source)])

    assert result.exit_code != 0
    assert "Error:" in result.output
    assert "broken.cube" in result.output
    assert not source.with_suffix(".qvf").exists()
