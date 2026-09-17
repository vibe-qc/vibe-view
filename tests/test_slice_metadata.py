"""Slicing preserves scientific metadata and refuses invalid output (#24)."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from click.testing import CliRunner

from vibeview.api import slice_qvf, validate
from vibeview.cli import main


def archive():
    payload = b'{"atoms": [{"symbol": "He", "atomic_number": 2, "position": [0,0,0]}]}'
    log = b"Completed.\n"

    def member(path, data, fmt):
        return {"path": path, "format": fmt, "sha256": hashlib.sha256(data).hexdigest()}

    manifest = {
        "qvf_version": 1,
        "source": {"program": "example", "version": "1", "calculation": "test"},
        "extensions": {"x_example": {"version": "1", "critical": False}},
        "thermochemistry": {"temperature_k": 298.15, "zpve_eh": 0.01},
        "viewer_defaults": {"auto_open": ["structure", "record"], "record": {"visible": True}},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "b_factors": [5],
                "members": {"structure": member("s.json", payload, "json")},
            },
            {
                "id": "record",
                "kind": "run.record",
                "program": "example",
                "program_version": "1",
                "members": {"log": member("log.txt", log, "binary")},
            },
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", payload)
        zf.writestr("log.txt", log)
    return buffer.getvalue(), manifest


@pytest.mark.parametrize("cli", [False, True])
def test_slice_preserves_root_source_section_and_member_metadata(tmp_path, cli):
    raw, original = archive()
    output = tmp_path / "slice.qvf"
    if cli:
        source = tmp_path / "source.qvf"
        source.write_bytes(raw)
        result = CliRunner().invoke(main, ["slice", str(source), "-o", str(output)])
        assert result.exit_code == 0, result.output
    else:
        slice_qvf(raw, output)
    assert validate(output)["valid"]
    with zipfile.ZipFile(output) as zf:
        assert json.loads(zf.read("manifest.json")) == original
        assert zf.getinfo("manifest.json").compress_type == zipfile.ZIP_STORED
        assert zf.read("log.txt") == b"Completed.\n"


def test_removed_section_hints_are_pruned(tmp_path):
    raw, original = archive()
    output = slice_qvf(raw, tmp_path / "slice.qvf", drop=["run.record"])
    with zipfile.ZipFile(output) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["sections"] == original["sections"][:1]
        assert manifest["viewer_defaults"] == {"auto_open": ["structure"]}
        assert "log.txt" not in zf.namelist()


def test_conflicting_selectors_refused_without_replacing_output(tmp_path):
    raw, _ = archive()
    output = tmp_path / "slice.qvf"
    output.write_bytes(b"keep this")
    with pytest.raises(ValueError, match="mutually exclusive"):
        slice_qvf(raw, output, keep=["structure"], drop=["record"])
    assert output.read_bytes() == b"keep this"


def test_can_slice_safely_onto_input_path(tmp_path):
    raw, _ = archive()
    output = tmp_path / "same.qvf"
    output.write_bytes(raw)
    slice_qvf(output, output, keep=["structure"])
    assert validate(output)["valid"]


def test_dangling_reference_refused_without_output(tmp_path):
    raw, manifest = archive()
    manifest["sections"][0]["structure_ref"] = "record"
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(buffer, "w") as target:
        for name in source.namelist():
            target.writestr(
                name, json.dumps(manifest) if name == "manifest.json" else source.read(name)
            )
    output = tmp_path / "invalid.qvf"
    with pytest.raises(ValueError, match="still references"):
        slice_qvf(buffer.getvalue(), output, drop=["record"])
    assert not output.exists()


def test_corrupt_payload_does_not_replace_output(tmp_path):
    raw, _ = archive()
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(buffer, "w") as target:
        for name in source.namelist():
            target.writestr(name, b"corrupt" if name == "log.txt" else source.read(name))
    output = tmp_path / "existing.qvf"
    output.write_bytes(b"keep this")
    with pytest.raises(ValueError, match="Invalid slice"):
        slice_qvf(buffer.getvalue(), output)
    assert output.read_bytes() == b"keep this"
