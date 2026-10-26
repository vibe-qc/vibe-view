"""vibe-view#51 row #39-35, archive errors at reader and command boundaries.

The gen 5 verification (note 34913) found three paths that still ended in a
traceback: ``validate`` on a manifest that is not an object or fails the
schema (it caught ``QVFOpenError`` only), a deeply nested ``manifest.json``
(``json.loads`` raised ``RecursionError`` through ``QVFReader``) and a
section member whose CRC fails (``capture``, ``h-add``, ``supercell`` and
``animate`` let ``QVFError`` escape, ``slice`` a raw ``BadZipFile``).
Every archive kind still runs through the API, watcher, server and multi-file
paths. Single-file commands cross representative open, validation and member
errors; table and validate retain all kinds. This avoids repeating every
low-level ZIP variant through every CLI formatting option while preserving
clean-exit and exactly-once reader-close assertions.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import sys
import types
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from tests.conftest import _water_structure_bytes, build_qvf
from vibeview import api, cli
from vibeview.qvf import QVFError, QVFOpenError, QVFReader

_STRUCTURE = "sections/structure.json"
_CAN_CHMOD = hasattr(os, "geteuid") and os.geteuid() != 0


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(home / var.lower()))
    monkeypatch.chdir(tmp_path)
    yield
    for p in tmp_path.rglob("*"):
        if p.is_file() and not os.access(p, os.R_OK):
            p.chmod(0o644)


def _water(path: Path) -> Path:
    return build_qvf(
        path,
        [("structure", "structure", {"structure": ("json", _STRUCTURE, _water_structure_bytes())})],
    )


@pytest.fixture
def water(tmp_path: Path) -> Path:
    return _water(tmp_path / "water.qvf")


# ── Bad-archive builders (everything under tmp_path) ─────────────────────────


def _entries(good: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(good) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _rewrite(path: Path, entries: dict[str, bytes], method: int) -> None:
    with zipfile.ZipFile(path, "w", compression=method) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _data_start(raw: bytes | bytearray, path: Path, name: str) -> tuple[int, int]:
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo(name)
    name_len, extra_len = struct.unpack_from("<HH", raw, info.header_offset + 26)
    return info.header_offset + 30 + name_len + extra_len, info.compress_size


def _corrupt(path: Path, good: Path, name: str, how: str) -> None:
    """Rewrite ``good`` into ``path`` with entry ``name`` damaged as ``how``."""
    method = zipfile.ZIP_STORED if how == "crc" else zipfile.ZIP_DEFLATED
    _rewrite(path, _entries(good), method)
    raw = bytearray(path.read_bytes())
    if how in ("crc", "zlib"):
        start, size = _data_start(raw, path, name)
        if how == "crc":
            raw[start + size // 2] ^= 0x01  # same length, wrong CRC-32
        else:
            raw[start : start + min(8, size)] = b"\xff" * min(8, size)  # invalid deflate block
    else:
        i = 0
        while True:
            i = raw.find(b"PK\x01\x02", i)
            assert i >= 0, f"no central entry for {name}"
            name_len = struct.unpack_from("<H", raw, i + 28)[0]
            if bytes(raw[i + 46 : i + 46 + name_len]) == name.encode():
                break
            i += 4
        if how == "encrypted":
            flags = struct.unpack_from("<H", raw, i + 8)[0]
            struct.pack_into("<H", raw, i + 8, flags | 0x1)
        else:  # compression method 99
            struct.pack_into("<H", raw, i + 10, 99)
    path.write_bytes(bytes(raw))


def _deep(depth: int) -> Any:
    value: Any = []
    for _ in range(depth - 1):
        value = [value]
    return value


def _deep_member(path: Path, good: Path, depth: int) -> None:
    """``good`` with the structure member replaced by ``depth`` nested lists."""
    entries = _entries(good)
    body = ("[" * depth + "]" * depth).encode()
    doc = json.loads(entries["manifest.json"])
    doc["sections"][0]["members"]["structure"]["sha256"] = hashlib.sha256(body).hexdigest()
    entries["manifest.json"] = json.dumps(doc).encode()
    entries[_STRUCTURE] = body
    _rewrite(path, entries, zipfile.ZIP_DEFLATED)


def make_bad(kind: str, path: Path, good: Path) -> Path:
    manifest = _entries(good)["manifest.json"]
    if kind == "non_utf8":
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", b'{"qvf_version": 1, "x": "\xff\xfe"}')
    elif kind == "crc_manifest":
        _corrupt(path, good, "manifest.json", "crc")
    elif kind == "zlib_manifest":
        _corrupt(path, good, "manifest.json", "zlib")
    elif kind == "encrypted_manifest":
        _corrupt(path, good, "manifest.json", "encrypted")
    elif kind == "method99_manifest":
        _corrupt(path, good, "manifest.json", "method99")
    elif kind == "crc_member":
        _corrupt(path, good, _STRUCTURE, "crc")
    elif kind == "zlib_member":
        _corrupt(path, good, _STRUCTURE, "zlib")
    elif kind == "encrypted_member":
        _corrupt(path, good, _STRUCTURE, "encrypted")
    elif kind == "method99_member":
        _corrupt(path, good, _STRUCTURE, "method99")
    elif kind == "directory":
        path.mkdir()
        (path / "manifest.json").write_bytes(manifest)
    elif kind == "truncated":
        data = good.read_bytes()
        path.write_bytes(data[: len(data) // 2])
    elif kind == "deep_json":
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", "[" * 200_000 + "]" * 200_000)
    elif kind == "deep_json_object":
        # Deep nesting inside an otherwise valid object manifest.
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "manifest.json",
                '{"qvf_version": 1, "sections": ' + "[" * 200_000 + "]" * 200_000 + "}",
            )
    elif kind == "deep_json_value":
        # Parses on every supported Python and sits where the schema allows
        # any JSON value, but nests past the reader's 256-level bound: at a
        # few thousand levels json.dumps and pprint run out of recursion.
        entries = _entries(good)
        entries["manifest.json"] = json.dumps(
            {**json.loads(manifest), "provenance": {"deep": _deep(300)}}
        ).encode()
        _rewrite(path, entries, zipfile.ZIP_DEFLATED)
    elif kind == "deep_json_member":
        _deep_member(path, good, 50_000)
    elif kind == "list_manifest":
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", "[]")
    elif kind == "schema_invalid":
        entries = _entries(good)
        entries["manifest.json"] = json.dumps(
            {**json.loads(manifest), "not_a_manifest_key": 1}
        ).encode()
        _rewrite(path, entries, zipfile.ZIP_DEFLATED)
    elif kind == "version_inf":
        # Gen 6 counterexample (note 35240): ``1e400`` parses as inf, and
        # ``int(inf)`` raised OverflowError out of the reader.
        entries = _entries(good)
        text = json.dumps({**json.loads(manifest), "qvf_version": "@V@"})
        entries["manifest.json"] = text.replace('"@V@"', "1e400").encode()
        _rewrite(path, entries, zipfile.ZIP_DEFLATED)
    elif kind == "critical_list_kind":
        # Gen 6 counterexample (note 35240): an unhashable kind on a critical
        # section raised TypeError from the set lookup before the schema ran.
        entries = _entries(good)
        doc = json.loads(manifest)
        doc["sections"].append({"id": "x", "kind": ["weird"], "critical": True, "members": {}})
        entries["manifest.json"] = json.dumps(doc).encode()
        _rewrite(path, entries, zipfile.ZIP_DEFLATED)
    elif kind == "non_zip":
        path.write_bytes(b"not a zip archive at all")
    elif kind == "chmod000":
        path.write_bytes(good.read_bytes())
        path.chmod(0)
    else:
        raise AssertionError(kind)
    return path


# The archives whose manifest (or container) is bad: no reader can open them.
OPEN_KINDS = [
    "non_utf8",
    "crc_manifest",
    "zlib_manifest",
    "directory",
    "truncated",
    "encrypted_manifest",
    "method99_manifest",
    "deep_json",
    "deep_json_object",
    "deep_json_value",
    "list_manifest",
    "schema_invalid",
    "version_inf",
    "critical_list_kind",
    "non_zip",
    "chmod000",
]
# The archives that open, but one section member cannot be read.
ZIP_MEMBER_KINDS = ["crc_member", "zlib_member", "encrypted_member", "method99_member"]
MEMBER_KINDS = [*ZIP_MEMBER_KINDS, "deep_json_member"]
ALL_KINDS = OPEN_KINDS + MEMBER_KINDS
# Distinct boundaries: container, schema, unsupported critical section,
# filesystem, binary member and decoded JSON member. Low-level variants
# remain covered in full by table/validate and the API/reader tests.
COMMAND_KINDS = [
    "non_zip", "schema_invalid", "critical_list_kind", "directory",
    "chmod000", "crc_member", "deep_json_member",
]


def _skip_unless_possible(kind: str) -> None:
    if kind == "chmod000" and not _CAN_CHMOD:
        pytest.skip("chmod 000 does not deny reads to root")


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """Readers fully constructed during the test and how often each was closed."""
    made: list[QVFReader] = []
    closes: dict[int, int] = {}
    real_init, real_close = QVFReader.__init__, QVFReader.close

    def init(self: QVFReader, *a: Any, **k: Any) -> None:
        real_init(self, *a, **k)
        made.append(self)

    def close(self: QVFReader) -> None:
        if any(self is r for r in made):
            closes[id(self)] = closes.get(id(self), 0) + 1
        real_close(self)

    monkeypatch.setattr(QVFReader, "__init__", init)
    monkeypatch.setattr(QVFReader, "close", close)
    return types.SimpleNamespace(made=made, counts=lambda: [closes.get(id(r), 0) for r in made])


def _assert_clean(result: Any, spy: types.SimpleNamespace, label: str) -> None:
    exc = result.exception
    assert exc is None or isinstance(exc, SystemExit), (
        f"{label}: uncaught {type(exc).__name__}: {exc!r}"
    )
    assert "Traceback" not in (result.output or ""), f"{label}: traceback in output"
    assert all(n == 1 for n in spy.counts()), f"{label}: reader close counts {spy.counts()}"


def _stub_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_configure_logging", lambda p: None)
    monkeypatch.setattr(cli, "_announce_when_bound", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_abort_if_port_in_use", lambda *a, **k: None)
    monkeypatch.setattr(
        cli, "_load_app", lambda: (lambda readers, *a, **k: MagicMock(), lambda app, **k: None)
    )


# ── The reader ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["deep_json", "deep_json_object"])
@pytest.mark.parametrize("source", ["path", "bytes", "stream"])
def test_deep_manifest_is_an_open_error(tmp_path, water, kind, source):
    bad = make_bad(kind, tmp_path / "bad.qvf", water)
    arg: Any = {"path": bad, "bytes": bad.read_bytes(), "stream": io.BytesIO(bad.read_bytes())}[
        source
    ]
    with pytest.raises(QVFOpenError, match="manifest.json"):
        QVFReader(arg)


@pytest.mark.parametrize("error", [RecursionError, MemoryError, ValueError])
def test_manifest_parse_failures_are_open_errors_and_close_the_zip(
    tmp_path, water, monkeypatch, error
):
    import vibeview.qvf as qvf

    closed: list[bool] = []
    real_close = zipfile.ZipFile.close

    def close(self: zipfile.ZipFile) -> None:
        closed.append(True)
        real_close(self)

    def boom(*a: Any, **k: Any) -> Any:
        raise error("parse failed")

    monkeypatch.setattr(zipfile.ZipFile, "close", close)
    monkeypatch.setattr(qvf.json, "loads", boom)
    with pytest.raises(QVFOpenError, match="manifest.json"):
        QVFReader(water)
    assert closed


@pytest.mark.parametrize("kind", ZIP_MEMBER_KINDS)
def test_member_read_failure_is_a_qvf_error_naming_the_member(tmp_path, water, kind):
    bad = make_bad(kind, tmp_path / "bad.qvf", water)
    with QVFReader(bad) as reader:
        with pytest.raises(QVFError, match="cannot read zip member 'sections/structure.json'"):
            reader.read_structure()
        with pytest.raises(QVFError, match="cannot read zip member"):
            reader.read_member_bytes(_STRUCTURE)


@pytest.mark.parametrize("kind", ["deep_json_value", "deep_json_object"])
def test_parseable_deep_manifest_is_refused_at_open(tmp_path, water, kind):
    bad = make_bad(kind, tmp_path / "bad.qvf", water)
    if kind == "deep_json_object":
        # Shallow enough to parse: the depth bound refuses it, not the parser.
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("manifest.json", '{"sections": ' + json.dumps(_deep(300)) + "}")
    with pytest.raises(QVFOpenError, match="nests deeper than 256 levels"):
        QVFReader(bad)


@pytest.mark.parametrize(
    ("depth", "reason"),
    [
        # Parses everywhere; the reader's bound refuses it.
        (300, "nests deeper than 256 levels"),
        # Beyond the parser: RecursionError, at a depth that varies by Python.
        (200_000, "is not valid JSON"),
    ],
)
def test_a_deep_json_member_is_a_section_error(tmp_path, water, depth, reason):
    bad = tmp_path / "bad.qvf"
    _deep_member(bad, water, depth)
    with QVFReader(bad) as reader:
        with pytest.raises(QVFError, match=reason):
            reader._read_json_member("structure", "structure")
        assert reader.section_error("structure") is not None


def test_a_schema_failure_on_a_deep_value_still_has_a_message(tmp_path, water):
    import vibeview.qvf as qvf

    class Deep(Exception):
        absolute_path = ("source",)

        def __str__(self) -> str:
            raise RecursionError

    assert "at /source" in qvf._validation_message(Deep())


# ── Single-archive commands ─────────────────────────────────────────────────

SINGLE: dict[str, Any] = {
    "info": lambda b, t: ["info", b],
    "info-json": lambda b, t: ["info", b, "--json"],
    "info-short": lambda b, t: ["info", b, "--short"],
    "table": lambda b, t: ["table", b],
    "export": lambda b, t: ["export", b, "-f", "xyz", "-o", str(t / "o.xyz")],
    "validate": lambda b, t: ["validate", b],
    "capture": lambda b, t: ["capture", b, "-s", "structure", "-o", str(t / "o.png")],
    "show": lambda b, t: ["show", b, "--plain", "--size", "40x20"],
    "show-all": lambda b, t: ["show", b, "--all", "--plain", "--size", "40x20"],
    "show-info": lambda b, t: ["show", b, "--info"],
    "tui": lambda b, t: ["tui", b],
    "slice": lambda b, t: ["slice", b, "-k", "structure", "-o", str(t / "s.qvf")],
    "animate": lambda b, t: ["animate", b, "-f", "frames", "-o", str(t / "anim")],
    "h-add": lambda b, t: ["h-add", b, "-o", str(t / "h.qvf")],
    "supercell": lambda b, t: ["supercell", b, "-o", str(t / "sc.qvf")],
    "batch": lambda b, t: ["batch", b, "-o", str(t / "gallery")],
    "dashboard": lambda b, t: ["dashboard", b, "-o", str(t / "d.png")],
    "dashboard-html": lambda b, t: ["dashboard", b, "--html", "-o", str(t / "d.html")],
}
# These report a bad file inline and go on with the rest (exit 0).
_SKIPS_BAD_FILES = {"batch", "dashboard", "dashboard-html"}
# These never read the structure member, or report it inline.
_MEMBER_TOLERANT = {"info", "info-json", "info-short", "table", "show-all", "show-info", "tui"}
# The member-read failures the gen 5 verifier found as tracebacks (note 34913).
_MEMBER_READERS = {"capture", "h-add", "supercell", "animate", "slice"}


@pytest.mark.parametrize(
    ("command", "kind"),
    [(command, kind) for command in SINGLE
     for kind in (ALL_KINDS if command in {"table", "validate"} else COMMAND_KINDS)],
)
def test_single_archive_command(tmp_path, water, spy, command, kind):
    _skip_unless_possible(kind)
    if command == "tui" and kind in MEMBER_KINDS:
        pytest.skip("the TUI renders interactively once the archive opens")
    bad = make_bad(kind, tmp_path / "bad.qvf", water)
    result = CliRunner().invoke(cli.main, SINGLE[command](str(bad), tmp_path))
    _assert_clean(result, spy, f"{command} {kind}")
    if kind in OPEN_KINDS and command not in _SKIPS_BAD_FILES:
        assert result.exit_code in (1, 2), f"{command} {kind}: exit {result.exit_code}"
    if kind in ZIP_MEMBER_KINDS and command in _MEMBER_READERS:
        assert result.exit_code == 1, f"{command} {kind}: exit {result.exit_code}"
        assert "Error: cannot read zip member" in result.output, result.output
    # slice copies intact bytes; strict validation also checks JSON depth.
    copies_bytes = kind == "deep_json_member" and command == "slice"
    fails_on_member = command not in _MEMBER_TOLERANT | _SKIPS_BAD_FILES and not copies_bytes
    if kind in MEMBER_KINDS and fails_on_member:
        assert result.exit_code == 1, f"{command} {kind}: exit {result.exit_code}"


@pytest.mark.parametrize(
    "kind",
    [
        "list_manifest",
        "schema_invalid",
        "deep_json",
        "deep_json_value",
        "version_inf",
        "critical_list_kind",
        *ZIP_MEMBER_KINDS,
    ],
)
def test_validate_reports_invalid(tmp_path, water, spy, kind):
    bad = make_bad(kind, tmp_path / "bad.qvf", water)
    result = CliRunner().invoke(cli.main, ["validate", str(bad), str(water)])
    _assert_clean(result, spy, f"validate {kind}")
    assert result.exit_code == 1
    assert "bad.qvf: INVALID: " in result.output
    assert "water.qvf: OK" in result.output
    assert "1 OK, 1 FAILED" in result.output


# ── Multi-archive commands: a good archive opened first ─────────────────────


@pytest.mark.parametrize(
    ("kind", "order", "as_json"),
    [(kind, order, as_json)
     for order in ("good_bad", "bad_good") for as_json in (False, True)
     for kind in (ALL_KINDS if order == "good_bad" and not as_json else COMMAND_KINDS)],
)
def test_diff(tmp_path, water, spy, kind, order, as_json):
    _skip_unless_possible(kind)
    bad = make_bad(kind, tmp_path / "bad.qvf", water)
    pair = [str(water), str(bad)] if order == "good_bad" else [str(bad), str(water)]
    result = CliRunner().invoke(cli.main, ["diff", *pair, *(["--json"] if as_json else [])])
    _assert_clean(result, spy, f"diff {order} {kind}")
    if kind in OPEN_KINDS:
        assert result.exit_code in (1, 2)


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("command", ["open", "compare"])
def test_open_and_compare(tmp_path, water, spy, monkeypatch, command, kind):
    _skip_unless_possible(kind)
    _stub_viewer(monkeypatch)
    bad = make_bad(kind, tmp_path / "bad.qvf", water)
    result = CliRunner().invoke(cli.main, [command, str(water), str(bad), "--no-browser"])
    _assert_clean(result, spy, f"{command} {kind}")
    if kind in OPEN_KINDS:
        assert result.exit_code in (1, 2)


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("with_good", [False, True])
def test_from_vq(tmp_path, water, spy, monkeypatch, kind, with_good):
    _skip_unless_possible(kind)
    workspace = tmp_path / "job1"
    workspace.mkdir()
    if with_good:
        (workspace / "a_good.qvf").write_bytes(water.read_bytes())
    make_bad(kind, workspace / "b_bad.qvf", water)
    vq = types.ModuleType("vq")
    vq_fetch = types.ModuleType("vq.fetch")
    vq_fetch.fetch_local = lambda job_id, out: workspace  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vq", vq)
    monkeypatch.setitem(sys.modules, "vq.fetch", vq_fetch)
    _stub_viewer(monkeypatch)
    result = CliRunner().invoke(
        cli.main, ["from-vq", "job1", "-o", str(tmp_path / "fetched"), "--no-browser"]
    )
    _assert_clean(result, spy, f"from-vq {kind}")
    if kind in OPEN_KINDS and not with_good:
        assert result.exit_code == 1


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_batch_compare(tmp_path, water, spy, kind):
    _skip_unless_possible(kind)
    other = _water(tmp_path / "water2.qvf")
    bad = make_bad(kind, tmp_path / "bad.qvf", water)
    result = CliRunner().invoke(cli.main, ["batch-compare", str(water), str(bad), str(other)])
    _assert_clean(result, spy, f"batch-compare {kind}")


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_merge(tmp_path, water, spy, kind):
    _skip_unless_possible(kind)
    bad = make_bad(kind, tmp_path / "bad.qvf", water)
    result = CliRunner().invoke(
        cli.main, ["merge", str(water), str(bad), "-o", str(tmp_path / "m.qvf")]
    )
    _assert_clean(result, spy, f"merge {kind}")
    if kind == "deep_json_member":  # member bytes are copied, not decoded
        return
    assert result.exit_code in (1, 2)
    if kind in ZIP_MEMBER_KINDS:
        assert "cannot read zip member" in result.output
    assert not (tmp_path / "m.qvf").exists()


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_stats(tmp_path, water, spy, kind):
    _skip_unless_possible(kind)
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "good.qvf").write_bytes(water.read_bytes())
    make_bad(kind, folder / "bad.qvf", water)
    result = CliRunner().invoke(cli.main, ["stats", str(folder)])
    _assert_clean(result, spy, f"stats {kind}")
    assert result.exit_code == 0
    if kind in OPEN_KINDS:
        assert "Unreadable: 1" in result.output


# ── The Python API keeps its QVFError contract ──────────────────────────────


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_api_raises_only_qvf_errors(tmp_path, water, spy, kind):
    _skip_unless_possible(kind)
    bad = make_bad(kind, tmp_path / "bad.qvf", water)
    for name, call in (
        ("info", lambda: api.info(bad)),
        ("validate", lambda: api.validate(bad)),
        ("diff", lambda: api.diff(water, bad)),
        ("slice", lambda: api.slice_qvf(bad, tmp_path / "s.qvf", keep=["structure"])),
    ):
        try:
            result = call()
        except (QVFError, ValueError):
            continue
        if name == "validate":
            assert result["valid"] is False, (kind, result)
    assert all(n == 1 for n in spy.counts()), spy.counts()


# ── The hot-reload watcher fingerprints a bad archive as raw bytes ──────────


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_watcher_fingerprint_never_raises(tmp_path, water, kind):
    from vibeview.file_watcher import _fingerprint_file

    _skip_unless_possible(kind)
    bad = make_bad(kind, tmp_path / "bad.qvf", water)
    fingerprint = _fingerprint_file(bad)
    if kind in ("directory", "chmod000"):
        assert fingerprint is None
    elif kind in ("non_utf8", "crc_manifest", "zlib_manifest", "truncated", "non_zip",
                  "encrypted_manifest", "method99_manifest", "deep_json", "deep_json_object"):
        assert fingerprint is not None and fingerprint[0] == "raw", (kind, fingerprint)


# ── The directory server's pages ────────────────────────────────────────────


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_serve_pages(tmp_path, water, spy, kind):
    from vibeview import serve

    _skip_unless_possible(kind)
    folder = tmp_path / "served"
    folder.mkdir()
    (folder / "good.qvf").write_bytes(water.read_bytes())
    make_bad(kind, folder / "bad.qvf", water)
    assert "bad.qvf" in serve._browse_dir(folder)
    assert "<html" in serve._view_page("bad.qvf", folder)
    assert all(n == 1 for n in spy.counts()), spy.counts()


# ── The TUI draws a member failure in place instead of crashing ─────────────


@pytest.mark.parametrize("kind", MEMBER_KINDS)
def test_tui_reports_an_unreadable_structure_in_the_viewport(tmp_path, water, kind):
    pytest.importorskip("textual")
    import asyncio

    from vibeview.tui.app import VibeViewTUI

    bad = make_bad(kind, tmp_path / "bad.qvf", water)

    async def body() -> str:
        app = VibeViewTUI(bad)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            grid = app._build_grid(60, 20)
            assert app.return_code in (None, 0)
            assert app._exception is None, app._exception
            return "".join(ch for row in grid.chars for ch in row) if grid else ""

    text = asyncio.run(body())
    assert "structure:" in text
