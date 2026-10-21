"""Reference validation, source ownership and optional installation boundaries."""

from __future__ import annotations

import io
import json
import sys
import zipfile

import pytest
from click.testing import CliRunner

from vibeview import validate
from vibeview.cli import main
from vibeview.qvf import QVFError, QVFOpenError, QVFReader


@pytest.mark.parametrize("kind", ["path", "str", "bytes", "bytearray", "memoryview", "stream"])
def test_source_types_and_stream_position(water_qvf, kind):
    raw = water_qvf.read_bytes()
    stream = io.BytesIO(raw)
    stream.seek(7)
    source = {"path": water_qvf, "str": str(water_qvf), "bytes": raw,
              "bytearray": bytearray(raw), "memoryview": memoryview(raw), "stream": stream}[kind]
    report = validate(source)
    assert report["valid"] and report["n_members"] > 0
    assert report["errors"] == []
    assert not stream.closed and stream.tell() == 7
    stream.close()


def test_stream_position_on_invalid_archive_and_bad_limits():
    stream = io.BytesIO(b"not a zip")
    stream.seek(2)
    with pytest.raises(QVFOpenError) as caught:
        validate(stream)
    report = caught.value.validation_report
    assert report is not None
    assert not report["valid"] and report["findings"][0]["code"] == "E_ZIP_UNREADABLE"
    assert stream.tell() == 2 and not stream.closed
    with pytest.raises(TypeError):
        validate(stream, limits={"misspelled_limit": 1})
    assert stream.tell() == 2 and not stream.closed


def test_existing_reader_validates_original_handle(water_qvf, tmp_path):
    path = tmp_path / "original.qvf"
    path.write_bytes(water_qvf.read_bytes())
    with QVFReader(path) as reader:
        replacement = tmp_path / "replacement.qvf"
        replacement.write_bytes(b"not a zip")
        replacement.replace(path)
        before = reader._zf.fp.tell()
        assert validate(reader)["valid"]
        assert reader._zf.fp.tell() == before
        assert reader.read_structure()
    with pytest.raises(QVFOpenError):
        validate(path)
    with pytest.raises(QVFError, match="closed reader"):
        validate(reader)


def test_limits_before_viewer_parsing(water_qvf, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("strict validation must not construct the viewer reader")

    monkeypatch.setattr(QVFReader, "__init__", forbidden)
    report = validate(water_qvf, limits={"max_member_bytes": 1})
    assert not report["valid"]
    assert "E_MEMBER_TOO_LARGE" in {f["code"] for f in report["findings"]}


@pytest.mark.parametrize("limit,code", [
    ("max_member_bytes", "E_MEMBER_TOO_LARGE"),
    ("max_json_bytes", "E_JSON_TOO_LARGE"),
    ("max_total_bytes", "E_TOTAL_TOO_LARGE"),
])
def test_resource_rejection_never_reopens_archive(water_qvf, monkeypatch, limit, code):
    import qvf_reader

    reference = qvf_reader.validate_qvf

    def forbidden(*args, **kwargs):
        pytest.fail("resource rejection must not reopen or decompress the archive")

    def checked(*args, **kwargs):
        report = reference(*args, **kwargs)
        monkeypatch.setattr(zipfile, "ZipFile", forbidden)
        return report

    monkeypatch.setattr(qvf_reader, "validate_qvf", checked)
    report = validate(water_qvf, limits={limit: 1})
    assert not report["valid"]
    assert code in {finding["code"] for finding in report["findings"]}


@pytest.mark.parametrize("kind", ["non_utf8", "crc", "deflate"])
def test_manifest_open_error_retains_findings_and_stream_ownership(kind):
    from tests.test_reader_open_errors import _bad_zip_bytes

    stream = io.BytesIO(_bad_zip_bytes(kind))
    stream.seek(3)
    with pytest.raises(QVFOpenError) as caught:
        validate(stream)
    report = caught.value.validation_report
    assert report is not None and not report["valid"]
    expected = "E_MANIFEST_JSON" if kind == "non_utf8" else "E_ZIP_UNREADABLE"
    assert {finding["code"] for finding in report["findings"]} == {expected}
    assert stream.tell() == 3 and not stream.closed


def test_warning_only_archive_is_valid_and_cli_shows_code(water_qvf, tmp_path):
    path = tmp_path / "orphan.qvf"
    path.write_bytes(water_qvf.read_bytes())
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("stale/unused.txt", b"unused")
    report = validate(path)
    assert report["valid"]
    assert "W_ORPHAN_MEMBER" in {f["code"] for f in report["findings"]}
    result = CliRunner().invoke(main, ["validate", str(path)])
    assert result.exit_code == 0
    assert "W_ORPHAN_MEMBER" in result.output


def test_payload_validation_not_only_hashes(water_qvf, tmp_path):
    import hashlib

    path = tmp_path / "bad-payload.qvf"
    with zipfile.ZipFile(water_qvf) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    section = next(s for s in manifest["sections"] if s["kind"] == "structure")
    member = section["members"]["structure"]
    files[member["path"]] = b'{"atoms": "not an atom array"}'
    member["sha256"] = hashlib.sha256(files[member["path"]]).hexdigest()
    files["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    report = validate(path)
    assert not report["valid"]
    assert "E_PAYLOAD_SCHEMA" in {f["code"] for f in report["findings"]}


def test_missing_reference_never_falls_back_to_partial_validation(water_qvf, monkeypatch):
    monkeypatch.setitem(sys.modules, "qvf_reader", None)
    with pytest.raises(QVFError, match="Strict validation requires"):
        validate(water_qvf)
    # Ordinary reading remains independent of the optional validator.
    with QVFReader(water_qvf) as reader:
        assert reader.read_structure()
    result = CliRunner().invoke(main, ["validate", str(water_qvf)])
    assert result.exit_code == 1
    assert "pip install" in result.output and "[validation]" in result.output
    assert "Traceback" not in result.output


def test_consumer_failure_is_separate_from_reference_findings():
    from tests._qvf_builders import DEFAULT_SOURCE, archive_bytes

    raw = archive_bytes([], source={**DEFAULT_SOURCE, "program": "\ud800viewer"})
    report = validate(raw)
    assert not report["valid"]
    assert report["reader_error"]
    assert report["findings"] == [] and report["errors"] == []
