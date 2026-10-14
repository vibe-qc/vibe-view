"""Reader correctness follow-ups to the #35 audit, tracked in #43.

Each test names the original #35 finding id so the verifier's matrix row
traces to it. Every finding has at least one test that failed on main at
0f77838; the remaining parametrizations guard cases that already worked.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from vibeview import bonds as bondsmod
from vibeview.qvf import Atom, QVFError, QVFReader, StructureData


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_member(path: str, data: bytes) -> dict:
    return {"path": path, "format": "json", "sha256": _sha(data)}


def _binary_member(path: str, arr: np.ndarray) -> dict:
    return {
        "path": path,
        "format": "binary",
        "sha256": _sha(arr.tobytes()),
        "dtype": str(arr.dtype),
        "shape": list(arr.shape),
    }


_WATER = json.dumps(
    {
        "atoms": [
            {"symbol": "O", "position": [0.0, 0.0, 0.1173], "atomic_number": 8},
            {"symbol": "H", "position": [0.0, 0.7572, -0.4692], "atomic_number": 1},
            {"symbol": "H", "position": [0.0, -0.7572, -0.4692], "atomic_number": 1},
        ],
        "pbc": [False, False, False],
    }
).encode()


def _archive(extra_sections=(), files=None) -> bytes:
    """A water archive plus ``extra_sections``; ``files`` maps zip path -> bytes."""
    manifest = {
        "qvf_version": 1,
        "source": {"program": "example", "version": "1", "calculation": "test"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {"structure": _json_member("s.json", _WATER)},
            },
            *extra_sections,
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", _WATER)
        for path, data in (files or {}).items():
            zf.writestr(path, data)
    return buf.getvalue()


def _volume_archive(n: int = 50) -> tuple[bytes, int]:
    grid = json.dumps(
        {"origin": [0, 0, 0], "voxel_vectors": np.eye(3).tolist(), "shape": [n, n, n]}
    ).encode()
    data = np.ones((n, n, n), dtype=np.float32)
    section = {
        "id": "rho",
        "kind": "volume.density",
        "members": {"grid": _json_member("g.json", grid), "data": _binary_member("d.bin", data)},
    }
    return _archive([section], {"g.json": grid, "d.bin": data.tobytes()}), data.nbytes










# ── #35-3: malformed wavefunction / reaction metadata raise QVFError ────────


def _wavefunction_archive(basis: object, meta: object) -> bytes:
    b = json.dumps(basis).encode()
    m = json.dumps(meta).encode()
    c = np.zeros((1, 1), dtype=np.float64)
    section = {
        "id": "wf",
        "kind": "wavefunction.gto",
        "members": {
            "basis": _json_member("b.json", b),
            "mo_metadata": _json_member("m.json", m),
            "mo_coefficients": _binary_member("c.bin", c),
        },
    }
    return _archive([section], {"b.json": b, "m.json": m, "c.bin": c.tobytes()})


@pytest.mark.parametrize(
    ("basis", "meta"),
    [
        ({"shells": [{"l": 0, "exponents": [1.0], "coefficients": [1.0]}]}, {}),  # no center
        ({"shells": []}, ["not", "an", "object"]),
        (["not", "an", "object"], {}),
        ({"shells": [], "n_ao": "many"}, {}),
        ({"shells": []}, {"energies": ["x"]}),
    ],
)
def test_malformed_wavefunction_payload_raises_qvf_error(basis: object, meta: object) -> None:
    """#35-3: previously KeyError / AttributeError / ValueError, nothing recorded."""
    reader = QVFReader(_wavefunction_archive(basis, meta))
    with pytest.raises(QVFError):
        reader.read_wavefunction_gto("wf")
    assert reader.section_error("wf") is not None


@pytest.mark.parametrize(
    "extra",
    [
        {"dim": "abc"},
        {"volume_isovalue": "high"},
        {"dim_per_frame": ["x", "y"]},
    ],
)
def test_malformed_reaction_metadata_raises_qvf_error(extra: dict) -> None:
    """#35-3: ``int(meta["dim"])`` etc. raised bare ValueError."""
    coords = np.zeros((2, 1, 3), dtype=np.float64)
    meta = json.dumps(
        {
            "atoms": [{"symbol": "H", "atomic_number": 1}],
            "waypoints": [],
            **extra,
        }
    ).encode()
    section = {
        "id": "rp",
        "kind": "reaction.path",
        "members": {
            "metadata": _json_member("rm.json", meta),
            "coords": _binary_member("rc.bin", coords),
        },
    }
    reader = QVFReader(
        _archive(
            [section],
            {"rm.json": meta, "rc.bin": coords.tobytes()},
        )
    )
    with pytest.raises(QVFError):
        reader.read_reaction_path("rp")
    assert reader.section_error("rp") is not None


# ── #35-9: thin periodic cells need more than one image shell ──────────────


def _brute_force_image_bonds(cell: float, radius: float) -> set[tuple[int, int, int]]:
    """Every lattice image of a lone atom inside the covalent cutoff, folded to +/-."""
    cutoff = 2 * radius + bondsmod.BOND_TOLERANCE
    reach = int(np.ceil(cutoff / cell)) + 1
    out = set()
    for image in itertools.product(range(-reach, reach + 1), repeat=3):
        if not any(image):
            continue
        if np.linalg.norm(np.asarray(image) * cell) < cutoff:
            neg = tuple(-v for v in image)
            out.add(max(image, neg))
    return out


@pytest.mark.parametrize("cell", [0.9, 1.6])
def test_single_atom_in_thin_cell_bonds_to_its_images(cell: float) -> None:
    """#35-9: one C in a 0.9 A or 1.6 A cube returned [] although the
    C-C cutoff 2*0.76+0.45 = 1.97 A exceeds both edges."""
    sd = StructureData(
        atoms=[Atom("C", np.zeros(3), 6)],
        pbc=(True, True, True),
        lattice_vectors=np.eye(3) * cell,
        bonds=None,
    )
    got = {bond[3] for bond in bondsmod.structure_bonds(sd)}
    expected = _brute_force_image_bonds(cell, float(bondsmod.covalent_radii([6])[0]))
    assert expected, "the fixture must have image bonds"
    assert got == expected


def test_third_image_shell_bond_in_a_two_atom_thin_cell() -> None:
    """#35-9: a bond reached only through the +/-3 image along a thin axis."""
    # H-H cutoff 2*0.31+0.45 = 1.07 A; cell edge 0.3 A along x, a second H
    # at x=0.1. Through image (-3,0,0) the pair is 0.8 A apart, a bond one
    # image shell (plus the ghost-ghost pairs it implies) cannot reach.
    lattice = np.diag([0.3, 20.0, 20.0])
    positions = np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]])
    got = {(b[0], b[1], b[3]) for b in bondsmod.infer_bonds(
        positions, [1, 1], lattice=lattice, pbc=(True, False, False))}
    cutoff = 2 * float(bondsmod.covalent_radii([1])[0]) + bondsmod.BOND_TOLERANCE
    expected = set()
    for i, j in ((0, 0), (0, 1), (1, 1)):
        for k in range(-8, 9):
            if i == j and k <= 0:
                continue
            d = np.linalg.norm(positions[j] + k * lattice[0] - positions[i])
            if d < cutoff:
                expected.add((i, j, (k, 0, 0)))
    assert (0, 1, (-3, 0, 0)) in expected
    assert got == expected


# ── #35-12: memory accounting and unload ────────────────────────────────────


def test_reader_reports_and_drops_cached_bytes() -> None:
    """#35-12: the cache is measurable and releasable per section."""
    data, nbytes = _volume_archive()
    reader = QVFReader(data)
    reader.read_volume_data("rho")
    assert reader.cached_bytes() == {"rho": nbytes}
    assert reader.drop_cached("rho") == nbytes
    assert reader.cached_bytes() == {}




# ── #35-13: edit overlays refuse unknown symbols and bad positions ─────────


def test_edit_overlay_rejects_unknown_symbol() -> None:
    """#35-13: ``Xx`` became Z=0, which the bond rule treats as a metal."""
    reader = QVFReader(_archive())
    with pytest.raises(ValueError, match="Xx"):
        reader.set_edit_overlay([[0, 0, 0]], ["Xx"])
    assert not reader.has_edit_overlay


@pytest.mark.parametrize("positions", [[[0.0, 0.0]], [[0.0, 0.0, 0.0, 1.0]], [[0.0, np.nan, 0.0]]])
def test_edit_overlay_rejects_malformed_positions(positions: list) -> None:
    """#35-13: a 2-component position was accepted."""
    reader = QVFReader(_archive())
    with pytest.raises(ValueError):
        reader.set_edit_overlay(positions, ["C"])
    assert not reader.has_edit_overlay


# ── #35-16: dim cannot contradict pbc ───────────────────────────────────────


def test_structure_data_rejects_dim_contradicting_pbc() -> None:
    """#35-16: ``pbc=(T,F,F), dim=3`` was accepted."""
    with pytest.raises(ValueError, match="dim"):
        StructureData(atoms=[], pbc=(True, False, False), lattice_vectors=np.eye(3),
                      bonds=None, dim=3)


@pytest.mark.parametrize(
    ("pbc", "dim"),
    [((False, False, False), 0), ((True, False, True), 2), ((True, True, True), 3)],
)
def test_structure_data_derives_dim_from_pbc(pbc: tuple, dim: int) -> None:
    """#35-16: omitted ``dim`` is ``sum(pbc)``, not a blanket 3."""
    sd = StructureData(atoms=[], pbc=pbc, lattice_vectors=np.eye(3), bonds=None)
    assert sd.dim == dim


# ── #35-26: read_dos_coop refuses a non-object meta ─────────────────────────


def test_dos_coop_rejects_non_object_meta() -> None:
    """#35-26: a list ``meta`` was silently replaced by ``{}``."""
    en = np.zeros(2, dtype=np.float64)
    meta = b"[1, 2]"
    section = {
        "id": "coop",
        "kind": "dos.coop",
        "members": {
            "energies": _binary_member("e.bin", en),
            "projections": _binary_member("e.bin", en),
            "integrated": _binary_member("e.bin", en),
            "meta": _json_member("m.json", meta),
        },
    }
    reader = QVFReader(_archive([section], {"e.bin": en.tobytes(), "m.json": meta}))
    with pytest.raises(QVFError, match="meta"):
        reader.read_dos_coop("coop")
    assert reader.section_error("coop") is not None


# ── #35-27: a member's declared format is checked ───────────────────────────


def test_json_reader_refuses_a_member_declared_binary() -> None:
    """#35-27: a vendor member declared ``binary`` was JSON-decoded anyway."""
    payload = json.dumps({"a": 1}).encode()
    member = {
        "path": "p.bin",
        "format": "binary",
        "sha256": _sha(payload),
        "dtype": "uint8",
        "shape": [len(payload)],
    }
    section = {"id": "x", "kind": "x_demo.custom", "members": {"payload": member}}
    reader = QVFReader(_archive([section], {"p.bin": payload}))
    with pytest.raises(QVFError, match="format"):
        reader._read_json_member("x", "payload")
    assert reader.section_error("x") is not None


def test_binary_reader_refuses_a_member_declared_json() -> None:
    """#35-27: and a member declared ``json`` was decoded as an array."""
    payload = np.zeros(4, dtype=np.float64).tobytes()
    member = {"path": "p.json", "format": "json", "sha256": _sha(payload)}
    section = {"id": "x", "kind": "x_demo.custom", "members": {"payload": member}}
    reader = QVFReader(_archive([section], {"p.json": payload}))
    with pytest.raises(QVFError, match="format"):
        reader._read_binary_member("x", "payload")


# ── #35-36: the suffix check is case-insensitive ────────────────────────────


@pytest.mark.parametrize("suffix", [".QVF", ".Qvf"])
def test_reader_opens_upper_case_suffix(tmp_path: Path, suffix: str) -> None:
    """#35-36: ``.QVF`` raised "expected .qvf extension"."""
    path = tmp_path / f"water{suffix}"
    path.write_bytes(_archive())
    with QVFReader(path) as reader:
        assert len(reader.read_structure().atoms) == 3
