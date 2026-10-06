"""Archive merge regressions with independently specified references and bytes."""

import hashlib
import json
import zipfile

import numpy as np
import pytest
from click.testing import CliRunner

from vibeview.cli import main
from vibeview.qvf import QVFReader


def _archive(path, tag, *, ids=("trajectory",), refs=True, bad_hash=False, member_path=None):
    sections = []
    blobs = {}
    for index, sid in enumerate(ids):
        raw = json.dumps(
            {
                "tag": tag,
                "index": index,
                "atoms": [{"symbol": "H", "atomic_number": 1}],
                "energies": [-1.0],
            }
        ).encode()
        mp = member_path or f"{sid}.json"
        blobs[mp] = raw
        sections.append(
            {
                "id": sid,
                "kind": "x_example.data",
                "label": tag,
                "members": {
                    "data": {
                        "path": mp,
                        "format": "json",
                        "sha256": "0" * 64 if bad_hash else hashlib.sha256(raw).hexdigest(),
                    }
                },
                "x_note": "retain section metadata",
            }
        )
    if refs:
        first = sections[0]
        first["kind"] = "trajectory"
        first["members"]["metadata"] = first["members"].pop("data")
        coords = np.array([[[0.0, 0.0, 0.0]]], dtype="<f8").tobytes()
        blobs["coords.dat"] = coords
        first["members"]["coords"] = {
            "path": "coords.dat",
            "format": "binary",
            "dtype": "float64",
            "shape": [1, 1, 3],
            "sha256": hashlib.sha256(coords).hexdigest(),
        }
        raw = b'{"waypoints": []}'
        blobs["waypoints.json"] = raw
        sections.append(
            {
                "id": "path",
                "kind": "reaction.waypoints",
                "trajectory_ref": ids[0],
                "members": {
                    "waypoints": {
                        "path": "waypoints.json",
                        "format": "json",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                },
            }
        )
    manifest = {
        "qvf_version": 1,
        "source": {"program": "example", "version": "1", "calculation": tag},
        "sections": sections,
        "provenance": {"method": "example"},
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, raw in blobs.items():
            zf.writestr(name, raw)
    return manifest


def _merge(a, b, out):
    return CliRunner().invoke(main, ["merge", str(a), str(b), "-o", str(out)])


def test_merge_rewrites_each_sources_references(tmp_path):
    a, b, out = (tmp_path / name for name in ("a.qvf", "b.qvf", "out.qvf"))
    _archive(a, "first")
    _archive(b, "second")
    result = _merge(a, b, out)
    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        sections = {s["id"]: s for s in manifest["sections"]}
        assert sections["path"]["trajectory_ref"] == "trajectory"
        assert sections["path_1"]["trajectory_ref"] == "trajectory_1"
        target = sections[sections["path_1"]["trajectory_ref"]]
        assert json.loads(zf.read(target["members"]["metadata"]["path"]))["tag"] == "second"
        assert manifest["provenance"]["method"] == "example"
        assert target["x_note"] == "retain section metadata"


def test_generated_ids_do_not_collide_with_source_ids(tmp_path):
    a, b, out = (tmp_path / name for name in ("a.qvf", "b.qvf", "out.qvf"))
    _archive(a, "first", ids=("data", "data_1"), refs=False)
    _archive(b, "second", ids=("data", "data_2"), refs=False)
    result = _merge(a, b, out)
    assert result.exit_code == 0, result.output
    with QVFReader(out) as reader:
        assert [s.id for s in reader.sections] == ["data", "data_1", "data_3", "data_2"]


def test_bad_member_does_not_replace_existing_output(tmp_path):
    a, b, out = (tmp_path / name for name in ("a.qvf", "b.qvf", "out.qvf"))
    _archive(a, "first", refs=False)
    _archive(b, "second", refs=False, bad_hash=True)
    out.write_bytes(b"existing output")
    result = _merge(a, b, out)
    assert result.exit_code != 0
    assert "Error:" in result.output
    assert out.read_bytes() == b"existing output"


def test_merge_onto_input_is_safe(tmp_path):
    a, b = (tmp_path / name for name in ("a.qvf", "b.qvf"))
    _archive(a, "first", refs=False)
    _archive(b, "second", refs=False)
    result = _merge(a, b, a)
    assert result.exit_code == 0, result.output
    with QVFReader(a) as reader:
        assert len(reader.sections) == 2
        assert reader._read_json_member("trajectory", "data")["tag"] == "first"
        assert reader._read_json_member("trajectory_1", "data")["tag"] == "second"


def test_prefixed_member_path_cannot_shadow_first_archive(tmp_path):
    a, b, out = (tmp_path / name for name in ("a.qvf", "b.qvf", "out.qvf"))
    _archive(a, "first", refs=False, member_path="1/data.json")
    _archive(b, "second", refs=False, member_path="data.json")
    result = _merge(a, b, out)
    assert result.exit_code == 0, result.output
    with QVFReader(out) as reader:
        assert reader._read_json_member("trajectory", "data")["tag"] == "first"
        assert reader._read_json_member("trajectory_1", "data")["tag"] == "second"


def _rewrite(path, mutate):
    with zipfile.ZipFile(path) as zf:
        blobs = {name: zf.read(name) for name in zf.namelist() if name != "manifest.json"}
        manifest = json.loads(zf.read("manifest.json"))
    mutate(manifest, blobs)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, data in blobs.items():
            zf.writestr(name, data)


def test_dangling_reference_is_rejected_before_output_replacement(tmp_path):
    a, b, out = (tmp_path / name for name in ("a.qvf", "b.qvf", "out.qvf"))
    _archive(a, "first")
    _archive(b, "second")
    _rewrite(b, lambda manifest, blobs: manifest["sections"][1].update(trajectory_ref="missing"))
    out.write_bytes(b"previous output")
    result = _merge(a, b, out)
    assert result.exit_code != 0
    assert "missing" in result.output
    assert out.read_bytes() == b"previous output"


@pytest.mark.parametrize("explicit", [True, False])
def test_wavefunction_basis_reference_and_hash_follow_renamed_structure(tmp_path, explicit):
    a, b, out = (tmp_path / name for name in ("a.qvf", "b.qvf", "out.qvf"))
    for path, tag in ((a, "first"), (b, "second")):
        _archive(path, tag, ids=("structure",), refs=False)

        def add_wavefunction(manifest, blobs):
            structure = manifest["sections"][0]
            structure["kind"] = "structure"
            structure["members"]["structure"] = structure["members"].pop("data")
            member = structure["members"]["structure"]
            raw_structure = json.dumps(
                {
                    "atoms": [{"symbol": "H", "atomic_number": 1, "position": [0.0, 0.0, 0.0]}],
                    "pbc": [False, False, False],
                }
            ).encode()
            blobs[member["path"]] = raw_structure
            member["sha256"] = hashlib.sha256(raw_structure).hexdigest()
            basis = {
                "shells": [{"center": 0, "l": 0, "exponents": [1.0], "coefficients": [1.0]}],
                "n_ao": 1,
            }
            if explicit:
                basis["structure_ref"] = "structure"
            members = {}
            for name, value in (("basis", basis), ("mo_metadata", {"spin": "restricted"})):
                raw = json.dumps(value).encode()
                blobs[f"{name}.json"] = raw
                members[name] = {
                    "path": f"{name}.json",
                    "format": "json",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            raw = np.array([[1.0]], dtype="<f8").tobytes()
            blobs["coeff.dat"] = raw
            members["mo_coefficients"] = {
                "path": "coeff.dat",
                "format": "binary",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "dtype": "float64",
                "shape": [1, 1],
            }
            manifest["sections"].append(
                {"id": "wavefunction", "kind": "wavefunction.gto", "members": members}
            )

        _rewrite(path, add_wavefunction)
    result = _merge(a, b, out)
    assert result.exit_code == 0, result.output
    with QVFReader(out) as reader:
        assert reader.read_wavefunction_gto("wavefunction_1").structure_ref == "structure_1"
        member = reader.get_section("wavefunction_1").members["basis"]
        assert hashlib.sha256(reader._zf.read(member.path)).hexdigest() == member.sha256
    from vibeview.api import slice_qvf

    sliced = tmp_path / "slice.qvf"
    with pytest.raises(ValueError, match="structure_1"):
        slice_qvf(out, sliced, drop=["structure_1"])
    assert not sliced.exists()


def test_second_input_open_failure_closes_first_reader(tmp_path, monkeypatch):
    import vibeview.archive as archive

    a, b, out = (tmp_path / name for name in ("a.qvf", "b.qvf", "out.qvf"))
    _archive(a, "first", refs=False)
    b.write_bytes(b"invalid ZIP")
    opened = []
    real_reader = archive.QVFReader

    def track(path):
        reader = real_reader(path)
        opened.append(reader)
        return reader

    monkeypatch.setattr(archive, "QVFReader", track)
    result = _merge(a, b, out)
    assert result.exit_code != 0
    assert opened[0]._zf.fp is None


@pytest.mark.parametrize("version_a,version_b", [(1, 2), (2, 1), (2, 2)])
def test_legacy_inputs_emit_current_qvf_version(tmp_path, version_a, version_b):
    from vibeview.api import slice_qvf

    a, b, out = (tmp_path / name for name in ("a.qvf", "b.qvf", "out.qvf"))
    for path, version in ((a, version_a), (b, version_b)):
        _archive(path, "legacy", refs=False)
        _rewrite(
            path, lambda manifest, blobs, version=version: manifest.update(qvf_version=version)
        )
    result = _merge(a, b, out)
    assert result.exit_code == 0, result.output
    with QVFReader(out) as reader:
        assert reader.manifest.qvf_version == 1
    sliced = slice_qvf(a, tmp_path / "sliced.qvf")
    with QVFReader(sliced) as reader:
        assert reader.manifest.qvf_version == 1


def test_inputs_are_opened_once_independent_of_member_count(tmp_path, monkeypatch):
    a, b, out = (tmp_path / name for name in ("a.qvf", "b.qvf", "out.qvf"))
    _archive(a, "first", ids=tuple(f"a{i}" for i in range(20)), refs=False)
    _archive(b, "second", ids=tuple(f"b{i}" for i in range(20)), refs=False)
    real_zip = zipfile.ZipFile
    opened = []

    def tracked(path, *args, **kwargs):
        if path in (a, b):
            opened.append(path)
        return real_zip(path, *args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", tracked)
    result = _merge(a, b, out)
    assert result.exit_code == 0, result.output
    assert opened == [a, b]
    with QVFReader(out) as reader:
        assert len(reader.sections) == 40
        assert reader._read_json_member("a19", "data")["tag"] == "first"
        assert reader._read_json_member("b19", "data")["tag"] == "second"
