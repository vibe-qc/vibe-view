"""Integrity failures must propagate and identify the affected lazy section."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import zipfile

import numpy as np
import pytest

from vibeview.banner import format_banner
from vibeview.qvf import (
    ManifestValidationError,
    QVFError,
    QVFOpenError,
    QVFReader,
    SHA256MismatchError,
)


def _archive(kind, members, *, damage=None, target=None, extra_sections=()):
    """Write a real archive, optionally damaging one member after describing it."""
    files = {}
    specs = {}
    for name, value in members.items():
        path = f"section/{name}"
        if isinstance(value, np.ndarray):
            data = value.tobytes()
            spec = {"format": "binary", "dtype": str(value.dtype), "shape": list(value.shape)}
        else:
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            opaque = kind == "citations" or (kind == "run.record" and name != "files")
            spec = {"format": "binary" if opaque else "json"}
        specs[name] = {**spec, "path": path, "sha256": hashlib.sha256(data).hexdigest()}
        files[path] = data
    section = {"id": "section", "kind": kind, "members": specs}
    if kind == "run.record":
        section["program"] = "example"
    if damage == "missing":
        files.pop(f"section/{target}")
    elif damage == "sha":
        specs[target]["sha256"] = "0" * 64
    elif damage == "shape":
        specs[target]["shape"] = [1, 1, 999]
    manifest = {
        "qvf_version": 1,
        "source": {"program": "example", "version": "1", "calculation": "test"},
        "sections": [section, *extra_sections],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, data in files.items():
            zf.writestr(path, data)
    data = buf.getvalue()
    if damage == "crc":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            offset = zf.getinfo(f"section/{target}").header_offset
        name_len, extra_len = struct.unpack_from("<HH", data, offset + 26)
        corrupt = bytearray(data)
        corrupt[offset + 30 + name_len + extra_len] ^= 1
        data = bytes(corrupt)
    return data


def _volume(**kwargs):
    return _archive(
        "volume.density",
        {
            "grid": {"origin": [0, 0, 0], "voxel_vectors": np.eye(3).tolist(), "shape": [1, 1, 2]},
            "data": np.array([1, 2], dtype=np.float32).reshape(1, 1, 2),
        },
        **kwargs,
    )


@pytest.mark.parametrize("member", ["grid", "data"])
@pytest.mark.parametrize("damage", ["missing", "sha", "crc"])
def test_lazy_member_failure_is_recorded_and_banner_classifies_it(member, damage):
    with QVFReader(_volume(damage=damage, target=member)) as reader:
        assert reader.section_error("section") is None
        assert reader.section_error_kind("section") is None
        read = reader.read_volume_grid if member == "grid" else reader.read_volume_data
        with pytest.raises(QVFError) as caught:
            read("section")
        assert not isinstance(caught.value, QVFOpenError)
        assert f"section/{member}" in str(caught.value)
        assert reader.section_error("section") == str(caught.value)
        expected = "sha256 mismatch" if damage == "sha" else "member read error"
        assert reader.section_error_kind("section") == expected
        assert f"error, {expected}" in format_banner(reader)
        assert "1 error(s)" in format_banner(reader)
        if damage == "sha":
            assert isinstance(caught.value, SHA256MismatchError)
        else:
            assert "sha256 mismatch" not in format_banner(reader)
        assert ("section", member) not in reader._binary_cache


@pytest.mark.parametrize("payload", [b"{invalid json", b"\xff"])
def test_json_decode_failure_is_recorded_with_member_context(payload):
    data = _archive("structure", {"structure": payload})
    with QVFReader(data) as reader:
        with pytest.raises(QVFError) as caught:
            reader.read_structure("section")
        assert "section/structure" in str(caught.value)
        assert "section 'section'" in str(caught.value)
        assert reader.section_error("section") == str(caught.value)
        assert reader.section_error_kind("section") == "invalid payload"
        assert "sha256 mismatch" not in format_banner(reader)


def test_binary_shape_failure_is_recorded():
    with QVFReader(_volume(damage="shape", target="data")) as reader:
        with pytest.raises(QVFError, match="shape"):
            reader.read_volume_data("section")
        assert reader.section_error_kind("section") == "invalid payload"
        assert "section/data" in reader.section_error("section")


def test_successful_read_does_not_erase_another_member_failure():
    with QVFReader(_volume(damage="sha", target="data")) as reader:
        with pytest.raises(SHA256MismatchError):
            reader.read_volume_data("section")
        error = reader.section_error("section")
        assert reader.read_volume_grid("section").shape == (1, 1, 2)
        assert reader.section_error("section") == error
        assert reader.section_error_kind("section") == "sha256 mismatch"


def test_successful_lazy_reads_have_no_section_error_and_keep_binary_cache():
    with QVFReader(_volume()) as reader:
        first = reader.read_volume_data("section")
        np.testing.assert_array_equal(first, [[[1, 2]]])
        assert first is reader.read_volume_data("section")
        assert reader.read_volume_grid("section").shape == (1, 1, 2)
        assert reader.section_error("section") is None
        assert reader.section_error_kind("section") is None
        assert "0 error(s)" in format_banner(reader)


@pytest.mark.parametrize("damage,payload", [("missing", {}), (None, b"not JSON")])
def test_phonon_dos_required_metadata_errors_propagate(damage, payload):
    data = _archive(
        "phonon_dos",
        {"frequencies": np.array([0., 1.]), "dos": np.array([2., 3.]), "meta": payload},
        damage=damage,
        target="meta",
    )
    with QVFReader(data) as reader:
        with pytest.raises(QVFError, match="section/meta"):
            reader.read_phonon_dos("section")
        assert reader.section_error("section") is not None


def test_phonon_dos_empty_metadata_is_valid():
    data = _archive(
        "phonon_dos",
        {"frequencies": np.array([0., 1.]), "dos": np.array([2., 3.]), "meta": {}},
    )
    with QVFReader(data) as reader:
        result = reader.read_phonon_dos("section")
        assert result.meta == {}
        np.testing.assert_array_equal(result.frequencies, [0, 1])
        np.testing.assert_array_equal(result.dos, [2, 3])


@pytest.mark.parametrize("damage,payload", [("missing", {}), (None, b"not JSON"), (None, [])])
def test_bond_order_errors_propagate_instead_of_drawing_default_orders(damage, payload):
    from vibeview.qvf import Atom, StructureData

    data = _archive("bond_orders", {"bond_orders": payload}, damage=damage, target="bond_orders")
    structure = StructureData(
        atoms=[Atom("H", np.array([0., 0., 0.]), 1), Atom("H", np.array([0.7, 0., 0.]), 1)],
        lattice_vectors=None, pbc=(False, False, False), dim=0, bonds=None,
    )
    with QVFReader(data) as reader:
        with pytest.raises(QVFError):
            reader.infer_bonds(structure)
        assert reader.section_error("section") is not None
        assert "sha256 mismatch" not in format_banner(reader)


@pytest.mark.parametrize("section", [1, {}, {"id": "future", "kind": 1, "members": {}},
                                    {"id": [], "kind": "future.kind", "members": {}},
                                    {"id": 17, "kind": "future.kind", "members": {}},
                                    {"id": "future", "kind": "future.kind", "members": {"x": {}}}])
def test_malformed_section_raises_qvf_validation_error_and_closes(section, monkeypatch):
    closed = []
    original = QVFReader.close

    def close(reader):
        original(reader)
        closed.append(reader)

    monkeypatch.setattr(QVFReader, "close", close)
    data = _volume(extra_sections=[section])
    with pytest.raises(ManifestValidationError):
        QVFReader(data)
    assert closed
    assert closed[0]._zf.fp is None


def test_future_kind_still_opens_and_leaves_known_sections_usable():
    data = _volume(extra_sections=[{"id": "future", "kind": "future.kind", "members": {}}])
    with QVFReader(data) as reader:
        assert reader.get_section("future").kind == "future.kind"
        np.testing.assert_array_equal(reader.read_volume_data("section"), [[[1, 2]]])
        assert "skipped, unsupported" in format_banner(reader)


@pytest.mark.parametrize("payload", [b"\xff", b"@article{test, title={Example}}"])
def test_citation_text_is_verified_and_decoded_through_shared_funnel(payload):
    with QVFReader(_archive("citations", {"references": payload})) as reader:
        if payload == b"\xff":
            with pytest.raises(QVFError, match="section/references.*not UTF-8"):
                reader.read_citations("section")
            assert reader.section_error_kind("section") == "invalid payload"
        else:
            assert reader.read_citations("section").bibtex == payload.decode()
            assert reader.section_error("section") is None


def test_run_record_keeps_lenient_text_and_arbitrary_attachment_contract():
    data = _archive("run.record", {"log": b"log\xff", "attachment.data": b"\xff\x00"})
    with QVFReader(data) as reader:
        record = reader.read_run_record("section")
        assert record.log_text == "log\ufffd"
        assert record.log_size == 4
        assert record.input_text is None
        assert reader.read_run_record_attachment("section", "attachment.data") is None
        assert reader.read_run_record_attachment_bytes("section", "attachment.data") == b"\xff\x00"
        assert reader.section_error("section") is None


@pytest.mark.parametrize("member", ["log", "attachment.data"])
def test_run_record_member_integrity_errors_are_recorded(member):
    data = _archive(
        "run.record", {"log": b"log", "attachment.data": b"data"}, damage="sha", target=member
    )
    with QVFReader(data) as reader:
        with pytest.raises(SHA256MismatchError):
            if member == "log":
                reader.read_run_record("section")
            else:
                reader.read_run_record_attachment_bytes("section", member)
        assert reader.section_error_kind("section") == "sha256 mismatch"
