"""qvf_writer is the one archive writer; everything it writes reads back."""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

import numpy as np
import pytest

import vibeview
from vibeview.qvf import QVFError, QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder, member_path, structure_archive, structure_payload

WATER = [
    {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.1173]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.7572, -0.4692]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, -0.7572, -0.4692]},
]


def test_member_paths_are_canonical() -> None:
    assert (
        member_path("structure", "structure", binary=False) == "sections/structure/structure.json"
    )
    assert member_path("vol_0", "data", binary=True) == "sections/vol_0/data.bin"


def test_structure_payload_derives_dimensionality() -> None:
    assert structure_payload(WATER, [False, False, False])["dimensionality"] == 0
    slab = structure_payload(WATER, (True, True, False), np.eye(3) * 5.0)
    assert slab["pbc"] == [True, True, False] and slab["dimensionality"] == 2
    assert slab["lattice_vectors"] == [[5.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0, 5.0]]
    assert "lattice_vectors" not in structure_payload(WATER, [False] * 3)


def test_structure_archive_round_trips_with_bonds_and_provenance() -> None:
    builder = structure_archive(
        WATER,
        [False, False, False],
        bonds=[{"i": 0, "j": 1, "order": 1.0}, {"i": 0, "j": 2, "order": 1.0}],
        secondary_structure=[{"type": "helix", "chain": "A", "start_seq": 1, "end_seq": 2}],
        calculation="xyz:water",
    )
    with QVFReader(builder.to_bytesio()) as reader:
        assert reader.source.program == "vibe-view"
        assert reader.source.version == vibeview.__version__
        assert reader.source.calculation == "xyz:water"
        data = reader.read_structure()
        assert [a.symbol for a in data.atoms] == ["O", "H", "H"]
        assert data.pbc == (False, False, False) and data.dim == 0
        assert data.bonds == [(0, 1, 1.0, (0, 0, 0)), (0, 2, 1.0, (0, 0, 0))]
        section = reader.get_section("structure")
        assert set(section.members) == {"structure", "bonds"}
        assert reader.manifest.sections[0].kind == "structure"
    names = zipfile.ZipFile(builder.to_bytesio()).namelist()
    assert names == [
        "manifest.json",
        "sections/structure/structure.json",
        "sections/structure/bonds.json",
    ]


def test_binary_members_carry_dtype_shape_and_sha() -> None:
    builder = QVFArchiveBuilder(calculation="cube:test")
    builder.add_json("structure", "structure", "structure", structure_payload(WATER, [False] * 3))
    grid = np.arange(24, dtype=np.float64).reshape(2, 3, 4)
    builder.add_binary("vol_0", "volume.density", "data", grid, "float32")
    builder.add_json(
        "vol_0",
        "volume.density",
        "grid",
        {"origin": [0, 0, 0], "voxel_vectors": np.eye(3).tolist(), "shape": [2, 3, 4]},
    )
    entry = builder.manifest["sections"][1]["members"]["data"]
    assert entry["dtype"] == "float32" and entry["shape"] == [2, 3, 4]
    with QVFReader(builder.to_bytesio()) as reader:
        section = reader.get_section("vol_0")
        assert section.kind == "volume.density"
        raw = reader._verify_and_read(section.members["data"])
        assert np.frombuffer(raw, dtype="<f4").reshape(2, 3, 4).tolist() == grid.tolist()


def test_provenance_and_root_fields() -> None:
    builder = structure_archive(WATER, [False] * 3, calculation="input.py")
    builder.set_provenance({"method": "HF", "basis": None, "charge": 0})
    builder.set_root("viewer_defaults", {"auto_open": ["structure"]})
    manifest = builder.manifest
    assert manifest["provenance"] == {"method": "HF", "charge": 0}
    assert manifest["viewer_defaults"] == {"auto_open": ["structure"]}
    assert list(manifest)[:2] == ["qvf_version", "source"] and manifest["qvf_version"] == 1
    with pytest.raises(ValueError):
        builder.set_root("sections", [])


def test_nan_is_refused_and_empty_archives_are_refused() -> None:
    builder = QVFArchiveBuilder()
    with pytest.raises(ValueError):
        builder.add_json("structure", "structure", "structure", {"x": math.nan})
    with pytest.raises(ValueError):
        builder.to_bytesio()


def test_a_section_keeps_one_kind() -> None:
    builder = QVFArchiveBuilder()
    builder.add_json("s", "structure", "m", {})
    with pytest.raises(ValueError):
        builder.add_json("s", "bonds", "n", {})
    with pytest.raises(ValueError):
        builder.add_json("s", "structure", "o", {}, section_extra={"members": {}})


def test_program_and_version_can_name_another_producer() -> None:
    builder = structure_archive(
        WATER, [False] * 3, program="TREXIO", version="2.5.0", calculation="h2o"
    )
    with QVFReader(builder.to_bytesio()) as reader:
        assert (reader.source.program, reader.source.version) == ("TREXIO", "2.5.0")


def test_write_is_atomic_and_verified(tmp_path: Path) -> None:
    out = tmp_path / "water.qvf"
    written = structure_archive(WATER, [False] * 3, calculation="t").write(out)
    assert written == out and out.exists()
    with QVFReader(out) as reader:
        assert len(reader.read_structure().atoms) == 3
    assert [p.name for p in tmp_path.iterdir()] == ["water.qvf"]
    manifest = json.loads(zipfile.ZipFile(out).read("manifest.json"))
    assert (
        manifest["sections"][0]["members"]["structure"]["path"]
        == "sections/structure/structure.json"
    )


def test_dimensionality_never_contradicts_pbc() -> None:
    """A molecule in a box stays a molecule; the reader would refuse a contradiction."""
    boxed = structure_archive(WATER, [False] * 3, np.eye(3) * 10.0, calculation="box")
    with QVFReader(boxed.to_bytesio()) as reader:
        data = reader.read_structure()
        assert data.dim == 0 and data.lattice_vectors is not None
    try:
        QVFReader(boxed.to_bytesio()).read_structure()
    except QVFError:  # pragma: no cover - the point of the test
        raise AssertionError("boxed molecule must not be self-contradictory") from None
