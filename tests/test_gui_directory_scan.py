"""Bounded and failure-tolerant GUI directory scanning."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


def _write_xyz(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("1\nfixture\nH 0.0 0.0 0.0\n", encoding="utf-8")


def test_gui_directory_scan_prunes_hidden_cache_and_virtualenvs(tmp_path: Path) -> None:
    from vibeview.app import _scan_gui_directory

    _write_xyz(tmp_path / "visible.xyz")
    _write_xyz(tmp_path / "results" / "nested.xyz")
    for relative in (
        ".hidden/hidden.xyz",
        "cache/cached.xyz",
        "venv/installed.xyz",
        "node_modules/package.xyz",
        "__pycache__/bytecode.xyz",
        "custom-python/environment.xyz",
    ):
        _write_xyz(tmp_path / relative)
    (tmp_path / "custom-python" / "pyvenv.cfg").write_text(
        "home = /python\n", encoding="utf-8"
    )

    result = _scan_gui_directory(
        tmp_path,
        recursive=True,
        max_entries=1_000,
        max_candidates=100,
    )

    assert {path.relative_to(tmp_path).as_posix() for path in result.candidates} == {
        "results/nested.xyz",
        "visible.xyz",
    }
    assert result.pruned_directories == 6
    assert result.traversal_error_count == 0
    assert result.truncated is False


def test_gui_directory_scan_keeps_results_when_subdirectory_is_unreadable(
    tmp_path: Path, monkeypatch
) -> None:
    from vibeview.app import _gui_directory_scan_notice, _scan_gui_directory

    visible = tmp_path / "visible.xyz"
    blocked = tmp_path / "blocked"
    _write_xyz(visible)
    _write_xyz(blocked / "secret.xyz")
    real_scandir = os.scandir

    def guarded_scandir(path):
        if Path(path) == blocked:
            raise PermissionError("test permission denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", guarded_scandir)
    result = _scan_gui_directory(tmp_path, recursive=True)

    assert result.candidates == (visible,)
    assert result.traversal_error_count == 1
    notice = _gui_directory_scan_notice(result)
    assert "1 unreadable path skipped" in notice
    assert "blocked" in notice
    assert "test permission denied" in notice


def test_gui_directory_scan_caps_synchronous_work(tmp_path: Path) -> None:
    from vibeview.app import _gui_directory_scan_notice, _scan_gui_directory

    for index in range(20):
        _write_xyz(tmp_path / f"structure-{index:02d}.xyz")

    result = _scan_gui_directory(
        tmp_path,
        recursive=True,
        max_entries=4,
        max_candidates=100,
    )

    assert result.scanned_entries == 4
    assert len(result.candidates) == 4
    assert result.truncated is True
    assert "scan limit reached after 4 entries" in _gui_directory_scan_notice(result)

    candidate_limited = _scan_gui_directory(
        tmp_path,
        recursive=True,
        max_entries=100,
        max_candidates=3,
    )
    assert len(candidate_limited.candidates) == 3
    assert candidate_limited.truncated is True


def test_gui_glob_caps_matches_and_reports_truncation(tmp_path: Path) -> None:
    from vibeview.app import _gui_directory_scan_notice, _scan_gui_glob

    for index in range(120):
        _write_xyz(tmp_path / f"structure-{index:03d}.xyz")

    result = _scan_gui_glob(
        str(tmp_path / "*.xyz"),
        recursive=False,
        max_entries=10_000,
        max_candidates=100,
    )

    assert len(result.candidates) == 100
    assert result.truncated is True
    assert "100 supported files" in _gui_directory_scan_notice(result)


def test_gui_glob_isolates_importer_probe_failures(
    tmp_path: Path, monkeypatch
) -> None:
    from vibeview import converters
    from vibeview.app import _scan_gui_glob

    broken = tmp_path / "broken.xyz"
    visible = tmp_path / "visible.xyz"
    _write_xyz(broken)
    _write_xyz(visible)
    real_is_supported = converters.is_supported_path

    def noisy_probe(path):
        if Path(path) == broken:
            raise RuntimeError("broken third-party importer")
        return real_is_supported(path)

    monkeypatch.setattr(converters, "is_supported_path", noisy_probe)
    result = _scan_gui_glob(str(tmp_path / "*.xyz"), recursive=False)

    assert result.candidates == (visible,)
    assert result.traversal_error_count == 1
    assert "broken third-party importer" in result.traversal_error_samples[0]


def test_open_path_rejects_recursive_double_star_glob_before_expansion(
    water_qvf: Path, tmp_path: Path, monkeypatch
) -> None:
    import glob

    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    monkeypatch.setattr(
        glob,
        "iglob",
        lambda *args, **kwargs: pytest.fail("recursive glob must not be expanded"),
    )
    reader = QVFReader(water_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.state.open_path = str(tmp_path / "**" / "*.qvf")
        server.state.open_recursive = True

        server.controller.open_path()

        assert "Recursive ** globs are not supported" in server.state.status_message
        assert "directory path" in server.state.status_message
    finally:
        reader.close()


def test_open_path_surfaces_directory_traversal_warning(
    water_qvf: Path, tmp_path: Path, monkeypatch
) -> None:
    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    visible = tmp_path / "visible.qvf"
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    shutil.copy2(water_qvf, visible)
    shutil.copy2(water_qvf, blocked / "secret.qvf")
    real_scandir = os.scandir

    def guarded_scandir(path):
        if Path(path) == blocked:
            raise PermissionError("test permission denied")
        return real_scandir(path)

    reader = QVFReader(water_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        monkeypatch.setattr(os, "scandir", guarded_scandir)
        server.state.open_path = str(tmp_path)
        server.state.open_recursive = True

        server.controller.open_path()

        assert "Opened 1 file(s)" in server.state.status_message
        assert "1 unreadable path skipped" in server.state.status_message
        assert "blocked" in server.state.status_message
    finally:
        reader.close()
