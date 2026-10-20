"""Packaging boundaries: verified downloads and isolated per-file viewers."""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_cached_runtime_download_is_always_hash_checked(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "desktop_builder", ROOT / "scripts/build_desktop_runtime.py"
    )
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    cached = tmp_path / "runtime.tar.gz"
    cached.write_bytes(b"verified input")
    item = {"filename": cached.name, "sha256": builder.digest(cached)}
    assert builder.fetch(item, tmp_path) == cached
    cached.write_bytes(b"changed after first download")
    with pytest.raises(ValueError, match="Checksum mismatch in cache"):
        builder.fetch(item, tmp_path)


@pytest.mark.parametrize("isolated", [False, True])
def test_file_viewers_preserve_parent_isolation(
    monkeypatch, tmp_path: Path, isolated: bool
) -> None:
    from vibeview import serve

    fake_sys = SimpleNamespace(
        executable="/application/python/bin/python3",
        flags=SimpleNamespace(isolated=isolated),
        dont_write_bytecode=isolated,
    )
    child = Mock(stderr=io.StringIO(""))
    spawn = Mock(return_value=child)
    monkeypatch.setattr(serve, "sys", fake_sys)
    monkeypatch.setattr(serve.subprocess, "Popen", spawn)
    monkeypatch.setattr(serve, "find_free_port", lambda *_: 9999)
    monkeypatch.setattr(serve, "_viewers", {})
    serve._ensure_viewer(tmp_path / "sample.xyz", 9998)
    argv = spawn.call_args.args[0]
    expected = [fake_sys.executable, *(["-I", "-B"] if isolated else []), "-m", "vibeview.cli"]
    assert argv[: len(expected)] == expected
    next(iter(serve._viewers.values()))["drain"].join(timeout=1)
