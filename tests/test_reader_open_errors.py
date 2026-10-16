"""vibe-view#51 rows #39-18 and #39-35: archives that fail to open fail cleanly.

The verifier's counterexamples at 9493d75 (note 34595): a non-UTF-8 manifest,
a CRC-corrupt manifest entry, a directory named ``*.qvf`` and an unreadable
archive made ``QVFReader`` raise UnicodeDecodeError, BadZipFile,
IsADirectoryError or PermissionError instead of ``QVFOpenError``. Every
caller that catches ``QVFError`` then let the failure escape: ``from-vq`` and
``diff`` leaked the reader they had already opened, and ``info``, ``table``,
``export``, ``validate`` and ``capture`` exited 1 with a traceback.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import zipfile
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.conftest import _water_structure_bytes, build_qvf
from vibeview import cli, recent
from vibeview.cli import main
from vibeview.qvf import QVFOpenError, QVFReader

_MANIFEST = json.dumps(
    {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "bad"},
        "sections": [],
    }
).encode()

BAD_KINDS = ["non_utf8", "crc", "deflate", "directory", "unreadable"]
# The archives a bytes or file-like source can also carry.
BAD_ZIP_KINDS = ["non_utf8", "crc", "deflate"]

_CAN_CHMOD = hasattr(os, "geteuid") and os.geteuid() != 0


def _water(tmp_path: Path, name: str = "w.qvf") -> Path:
    return build_qvf(
        tmp_path / name,
        [(
            "structure",
            "structure",
            {"structure": ("json", "sections/structure/structure.json", _water_structure_bytes())},
        )],
    )


def _bad_zip_bytes(kind: str) -> bytes:
    """A zip whose manifest entry fails on read in the way ``kind`` names."""
    import io

    buf = io.BytesIO()
    if kind == "non_utf8":
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", b'{"qvf_version": 1, "x": "\xff\xfe"}')
        return buf.getvalue()

    method = zipfile.ZIP_STORED if kind == "crc" else zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(buf, "w", compression=method) as zf:
        zf.writestr("manifest.json", _MANIFEST)
    data = bytearray(buf.getvalue())
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
        info = zf.getinfo("manifest.json")
    name_len = int.from_bytes(data[info.header_offset + 26 : info.header_offset + 28], "little")
    extra_len = int.from_bytes(data[info.header_offset + 28 : info.header_offset + 30], "little")
    start = info.header_offset + 30 + name_len + extra_len
    if kind == "crc":
        # Same length, different bytes: the stored payload fails its CRC-32.
        data[start + 5] ^= 0x01
    else:
        # BFINAL=1, BTYPE=11: an invalid deflate block, so zlib.error.
        data[start] = 0x07
    return bytes(data)


def _make_bad(path: Path, kind: str) -> Path:
    if kind == "directory":
        path.mkdir()
        (path / "manifest.json").write_bytes(_MANIFEST)
        return path
    if kind == "unreadable":
        with tempfile.TemporaryDirectory() as src:
            path.write_bytes(_water(Path(src)).read_bytes())
        path.chmod(0)
        return path
    path.write_bytes(_bad_zip_bytes(kind))
    return path


def _skip_unless_possible(kind: str) -> None:
    if kind == "unreadable" and not _CAN_CHMOD:
        pytest.skip("chmod 000 does not deny reads to root")


@pytest.fixture(autouse=True)
def _restore_permissions(tmp_path: Path):
    yield
    for p in tmp_path.rglob("*"):
        if p.is_file() and not os.access(p, os.R_OK):
            p.chmod(0o644)


@pytest.fixture
def zipfiles(monkeypatch: pytest.MonkeyPatch) -> list[zipfile.ZipFile]:
    """Every ZipFile constructed successfully during the test."""
    made: list[zipfile.ZipFile] = []
    real_init = zipfile.ZipFile.__init__

    def init(self: zipfile.ZipFile, *a: Any, **k: Any) -> None:
        real_init(self, *a, **k)
        made.append(self)

    monkeypatch.setattr(zipfile.ZipFile, "__init__", init)
    return made


@pytest.fixture
def readers(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """Readers constructed during the test, and close() calls per reader."""
    made: list[QVFReader] = []
    closes: dict[int, int] = {}
    real_init = QVFReader.__init__
    real_close = QVFReader.close

    def init(self: QVFReader, *a: Any, **k: Any) -> None:
        real_init(self, *a, **k)
        made.append(self)

    def close(self: QVFReader) -> None:
        closes[id(self)] = closes.get(id(self), 0) + 1
        real_close(self)

    monkeypatch.setattr(QVFReader, "__init__", init)
    monkeypatch.setattr(QVFReader, "close", close)
    return types.SimpleNamespace(
        made=made, closes=closes, counts=lambda: [closes.get(id(r), 0) for r in made]
    )


# ── The constructor ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", BAD_KINDS)
def test_reader_path_open_error_is_qvf_open_error_and_leaves_no_zip_open(
    tmp_path, zipfiles, kind
):
    _skip_unless_possible(kind)
    bad = _make_bad(tmp_path / "bad.qvf", kind)
    with pytest.raises(QVFOpenError):
        QVFReader(bad)
    assert all(zf.fp is None for zf in zipfiles), "a ZipFile was left open"


@pytest.mark.parametrize("as_stream", [False, True])
@pytest.mark.parametrize("kind", BAD_ZIP_KINDS)
def test_reader_memory_open_error_is_qvf_open_error_and_leaves_no_zip_open(
    zipfiles, kind, as_stream
):
    import io

    raw = _bad_zip_bytes(kind)
    source: Any = io.BytesIO(raw) if as_stream else raw
    with pytest.raises(QVFOpenError):
        QVFReader(source)
    assert zipfiles, "the archive itself should have opened"
    assert all(zf.fp is None for zf in zipfiles), "a ZipFile was left open"


# ── Multi-reader commands close what they already opened ───────────────────


@pytest.fixture
def fake_viewer(monkeypatch: pytest.MonkeyPatch):
    served: list[Any] = []

    class _App:
        state = types.SimpleNamespace()
        controller = types.SimpleNamespace(
            toggle_compare_mode=lambda on: None, activate_section=lambda sid: None
        )

    def create_app(rs: list[Any]) -> _App:
        served.extend(rs)
        return _App()

    monkeypatch.setattr(recent, "add_recent", lambda p: None)
    monkeypatch.setattr(cli, "_load_app", lambda: (create_app, lambda app, **k: None))
    monkeypatch.setattr(cli, "_abort_if_port_in_use", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_announce_when_bound", lambda *a, **k: None)
    return served


def _fake_vq(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str) -> Path:
    """``vq.fetch.fetch_local`` 'fetches' a good archive and then a bad one."""
    good = _water(tmp_path, "src.qvf").read_bytes()

    def fetch_local(jid: str, output_dir: Path) -> Path:
        dst = Path(output_dir) / jid
        dst.mkdir(parents=True)
        (dst / "a_good.qvf").write_bytes(good)
        _make_bad(dst / "b_bad.qvf", kind)
        return dst

    vq_pkg = types.ModuleType("vq")
    vq_fetch = types.ModuleType("vq.fetch")
    vq_fetch.fetch_local = fetch_local  # type: ignore[attr-defined]
    vq_pkg.fetch = vq_fetch  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vq", vq_pkg)
    monkeypatch.setitem(sys.modules, "vq.fetch", vq_fetch)
    return tmp_path / "fetched"


@pytest.mark.parametrize("kind", BAD_KINDS)
def test_from_vq_skips_a_bad_archive_and_closes_the_good_reader(
    tmp_path, monkeypatch, readers, fake_viewer, kind
):
    _skip_unless_possible(kind)
    out = _fake_vq(monkeypatch, tmp_path, kind)
    result = CliRunner().invoke(
        main,
        ["from-vq", "job1", "-o", str(out), "--no-browser", "--log-file", str(tmp_path / "v.log")],
    )
    assert result.exception is None, repr(result.exception)
    assert result.exit_code == 0, result.output
    assert "Error opening" in result.output and "b_bad.qvf" in result.output
    assert len(readers.made) == 1 and fake_viewer == readers.made
    assert readers.counts() == [1]


@pytest.mark.parametrize("kind", BAD_KINDS)
def test_diff_with_a_bad_second_archive_exits_cleanly_and_closes_the_first(
    tmp_path, readers, kind
):
    _skip_unless_possible(kind)
    good = _water(tmp_path, "good.qvf")
    bad = _make_bad(tmp_path / "b.qvf", kind)
    result = CliRunner().invoke(main, ["diff", str(good), str(bad)])
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    if kind == "unreadable":
        # click's ``Path(readable)`` check refuses it before any reader opens.
        assert result.exit_code == 2, result.output
        assert readers.made == []
        return
    assert result.exit_code == 1, result.output
    assert "Error opening B" in result.output
    assert len(readers.made) == 1
    assert readers.counts() == [1]


def _interrupt_second_open(monkeypatch: pytest.MonkeyPatch, made: list[QVFReader]) -> None:
    """The second QVFReader construction raises KeyboardInterrupt."""
    counting_init = QVFReader.__init__

    def init(self: QVFReader, *a: Any, **k: Any) -> None:
        if len(made) >= 1:
            raise KeyboardInterrupt
        counting_init(self, *a, **k)

    monkeypatch.setattr(QVFReader, "__init__", init)


@pytest.mark.parametrize("command", ["diff", "open", "from-vq"])
def test_multi_reader_commands_close_on_any_exception_while_opening(
    tmp_path, monkeypatch, readers, fake_viewer, command
):
    a, b = _water(tmp_path, "a.qvf"), _water(tmp_path, "b.qvf")
    if command == "from-vq":
        out = _fake_vq(monkeypatch, tmp_path, "non_utf8")
        args = ["from-vq", "job1", "-o", str(out), "--no-browser",
                "--log-file", str(tmp_path / "v.log")]
    elif command == "open":
        args = ["open", str(a), str(b), "--no-browser", "--log-file", str(tmp_path / "v.log")]
    else:
        args = ["diff", str(a), str(b)]
    _interrupt_second_open(monkeypatch, readers.made)
    CliRunner().invoke(main, args)
    assert not fake_viewer, "nothing should be served after an interrupted open"
    assert len(readers.made) == 1
    assert readers.counts() == [1]


def test_compare_batch_closes_on_any_exception_while_opening(tmp_path, monkeypatch, readers):
    from vibeview.batch_compare import compare_batch

    a, b = _water(tmp_path, "a.qvf"), _water(tmp_path, "b.qvf")
    _interrupt_second_open(monkeypatch, readers.made)
    with pytest.raises(KeyboardInterrupt):
        compare_batch([str(a), str(b)])
    assert len(readers.made) == 1
    assert readers.counts() == [1]


# ── Single-reader commands exit 1 cleanly (#39-35) ──────────────────────────


@pytest.mark.parametrize(
    "command",
    [["info"], ["table"], ["export", "-f", "xyz"], ["validate"], ["capture", "-s", "structure"]],
    ids=["info", "table", "export", "validate", "capture"],
)
@pytest.mark.parametrize("kind", BAD_KINDS)
def test_single_reader_commands_fail_cleanly_on_a_bad_archive(tmp_path, readers, command, kind):
    _skip_unless_possible(kind)
    bad = _make_bad(tmp_path / "bad.qvf", kind)
    args = [*command, str(bad)]
    if command[0] in ("export", "capture"):
        args += ["-o", str(tmp_path / ("out.xyz" if command[0] == "export" else "out.png"))]
    result = CliRunner().invoke(main, args)
    # click's own ``Path(readable)`` check already refuses the unreadable
    # archive with a usage error (exit 2) before the reader is built.
    assert result.exit_code == (2 if kind == "unreadable" else 1), result.output
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert "bad.qvf" in result.output or "Error" in result.output or "INVALID" in result.output
    assert readers.made == []


@pytest.mark.parametrize("kind", BAD_ZIP_KINDS)
def test_api_validate_raises_qvf_open_error_for_an_unopenable_archive(tmp_path, kind):
    from vibeview.api import validate

    # The API contract (tests/test_reader_ownership.py, test_sdk_api.py) is to
    # raise QVFOpenError when the source cannot be opened; the CLI reports it.
    with pytest.raises(QVFOpenError):
        validate(_make_bad(tmp_path / "bad.qvf", kind))


# ── Further paths from the repo-wide audit ──────────────────────────────────


@pytest.mark.parametrize("command", [["show"], ["show", "--all"], ["tui"]],
                         ids=["show", "show-all", "tui"])
@pytest.mark.parametrize("kind", ["non_utf8", "crc", "directory"])
def test_terminal_commands_fail_cleanly_on_a_bad_archive(tmp_path, command, kind):
    if command == ["tui"]:
        pytest.importorskip("textual")
    bad = _make_bad(tmp_path / "bad.qvf", kind)
    result = CliRunner().invoke(main, [*command, str(bad)])
    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert "Error:" in result.output


def test_stats_counts_damaged_archives_and_directories_as_unreadable(tmp_path):
    _water(tmp_path, "good.qvf")
    _make_bad(tmp_path / "b_dir.qvf", "directory")
    _make_bad(tmp_path / "c_utf8.qvf", "non_utf8")
    _make_bad(tmp_path / "d_crc.qvf", "crc")
    result = CliRunner().invoke(main, ["stats", str(tmp_path)])
    assert result.exception is None, repr(result.exception)
    assert result.exit_code == 0, result.output
    assert "Unreadable: 3" in result.output



def test_tui_closes_its_reader_when_the_app_fails_before_mounting(tmp_path, monkeypatch, readers):
    tui_app = pytest.importorskip("vibeview.tui.app")

    def broken_run(self: Any, *a: Any, **k: Any) -> None:
        raise RuntimeError("no terminal")

    monkeypatch.setattr(tui_app.VibeViewTUI, "run", broken_run)
    with pytest.raises(RuntimeError, match="no terminal"):
        tui_app.run(_water(tmp_path))
    assert len(readers.made) == 1
    assert readers.counts() == [1]


def test_tui_constructor_failure_closes_its_reader(tmp_path, monkeypatch, readers):
    tui_app = pytest.importorskip("vibeview.tui.app")

    def broken(self: Any, section: Any) -> str:
        raise RuntimeError("status exploded")

    monkeypatch.setattr(tui_app.VibeViewTUI, "_status_of", broken)
    with pytest.raises(RuntimeError, match="status exploded"):
        tui_app.VibeViewTUI(_water(tmp_path))
    assert len(readers.made) == 1
    assert readers.counts() == [1]
