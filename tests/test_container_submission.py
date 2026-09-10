"""Container-mode vq submission: the viewer builds a *pending* QVF.

The Job Manager can submit a job as a single pending QVF container
(structure + ``job.spec``, ``run_status="pending"``) instead of a
generated Python script. vq recognizes the ``.qvf`` suffix and runs
``vibeqc run job.qvf``, which settles that same file in place -- one file
out, the settled one back.

Building a *pending* archive is a producer action, and the QVF producer
lives in vibeqc, which is not a vibe-view dependency. These tests skip
when vibeqc is absent rather than asserting a feature the environment
cannot offer.
"""

from __future__ import annotations

import json
import sys
import types
import zipfile
from pathlib import Path

import pytest

from vibeview.app import _vibeqc_importable, build_pending_container

requires_vibeqc = pytest.mark.skipif(
    not _vibeqc_importable(), reason="vibeqc (the QVF producer) not installed"
)

_H2O = [
    {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.1173]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.7572, -0.4692]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, -0.7572, -0.4692]},
]


class _Molecular:
    """Stand-in for a molecular StructureData."""

    lattice_vectors = None
    pbc = None


class _Slab:
    """Stand-in for a 2-D periodic StructureData (Å lattice rows)."""

    lattice_vectors = [[3.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 15.875]]
    pbc = [True, True, False]


def _params(**over):
    p = {
        "atoms": _H2O,
        "method": "rhf",
        "basis": "sto-3g",
        "functional": "",
        "charge": 0,
        "multiplicity": 1,
    }
    p.update(over)
    return p


def _manifest(path: Path) -> dict:
    with zipfile.ZipFile(path) as zf:
        return json.loads(zf.read("manifest.json"))


def _member(path: Path, kind: str, role: str):
    manifest = _manifest(path)
    section = next(s for s in manifest["sections"] if s["kind"] == kind)
    with zipfile.ZipFile(path) as zf:
        return json.loads(zf.read(section["members"][role]["path"]))


@requires_vibeqc
def test_builds_a_pending_container(tmp_path: Path) -> None:
    path = build_pending_container(_Molecular(), _params(), tmp_path / "job")
    manifest = _manifest(Path(path))

    assert manifest["provenance"]["run_status"] == "pending"
    kinds = {s["kind"] for s in manifest["sections"]}
    # A pending container is a *request*: structure + spec, no results.
    assert kinds == {"structure", "job.spec"}, kinds

    spec = _member(Path(path), "job.spec", "spec")
    assert spec["job_type"] == "molecular"
    assert spec["method"] == "rhf"
    assert spec["basis"] == "sto-3g"
    assert spec["charge"] == 0
    assert spec["multiplicity"] == 1


@requires_vibeqc
def test_angstrom_positions_round_trip(tmp_path: Path) -> None:
    """Å in, Å out.

    QVF stores Å; ``vibeqc.Atom`` takes bohr. Skipping that conversion
    shrinks every geometry by 0.529x, so pin the round trip rather than
    trusting the constant.
    """
    path = build_pending_container(_Molecular(), _params(), tmp_path / "job")
    payload = _member(Path(path), "structure", "structure")

    got = [round(c, 4) for c in payload["atoms"][0]["position"]]
    assert got == [0.0, 0.0, 0.1173], got
    o_h = payload["atoms"][1]["position"]
    assert round(o_h[1], 4) == 0.7572, o_h


@requires_vibeqc
def test_periodic_structure_keeps_its_pbc(tmp_path: Path) -> None:
    """A slab must stay a slab: pbc survives, the vacuum axis stays false."""
    path = build_pending_container(
        _Slab(),
        _params(method="uks", functional="pbe", charge=-1, multiplicity=2),
        tmp_path / "slab",
    )
    payload = _member(Path(path), "structure", "structure")
    manifest = _manifest(Path(path))

    assert payload["pbc"] == [True, True, False], payload["pbc"]
    assert payload["dimensionality"] == 2
    for actual, expected in zip(
        payload["lattice_vectors"], _Slab.lattice_vectors, strict=True
    ):
        assert actual == pytest.approx(expected)
    spec = _member(Path(path), "job.spec", "spec")
    assert spec["job_type"] == "periodic"
    assert spec["charge"] == -1
    assert spec["multiplicity"] == 2
    assert manifest["provenance"]["charge"] == -1
    assert manifest["provenance"]["multiplicity"] == 2


def test_periodic_constructor_receives_requested_charge_and_multiplicity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin the viewer-to-producer boundary even without vibeqc installed."""
    seen: dict[str, object] = {}

    class FakeAtom:
        def __init__(self, atomic_number, position):
            self.atomic_number = atomic_number
            self.position = position

    class FakePeriodicSystem:
        def __init__(
            self, dim, lattice, atoms, *, charge=0, multiplicity=1
        ) -> None:
            seen.update(
                dim=dim,
                lattice=lattice,
                atoms=atoms,
                charge=charge,
                multiplicity=multiplicity,
            )

    fake_vibeqc = types.ModuleType("vibeqc")
    fake_vibeqc.Atom = FakeAtom
    fake_vibeqc.PeriodicSystem = FakePeriodicSystem
    fake_vibeqc.Molecule = object

    def fake_write_pending_qvf(system, stem, **kwargs):
        seen.update(system=system, stem=stem, writer_kwargs=kwargs)
        return Path(stem).with_suffix(".qvf")

    fake_vibeqc.write_pending_qvf = fake_write_pending_qvf
    monkeypatch.setitem(sys.modules, "vibeqc", fake_vibeqc)

    result = build_pending_container(
        _Slab(),
        _params(method="uks", functional="pbe", charge=-1, multiplicity=2),
        tmp_path / "charged-slab",
    )

    assert result == tmp_path / "charged-slab.qvf"
    assert seen["dim"] == 2
    assert seen["charge"] == -1
    assert seen["multiplicity"] == 2
    assert seen["writer_kwargs"] == {
        "method": "uks",
        "basis": "sto-3g",
        "functional": "pbe",
    }


@requires_vibeqc
def test_empty_functional_is_omitted_not_empty_string(tmp_path: Path) -> None:
    """The viewer sends "" for "no functional"; the spec must omit it."""
    path = build_pending_container(_Molecular(), _params(), tmp_path / "job")
    spec = _member(Path(path), "job.spec", "spec")
    assert spec.get("functional") in (None, ""), spec.get("functional")
    assert spec.get("functional") != "none"


def test_container_availability_flag_matches_import() -> None:
    """The UI switch is gated on the same probe the submit path uses."""
    import importlib.util

    assert _vibeqc_importable() == (
        importlib.util.find_spec("vibeqc") is not None
    )


def test_availability_probe_survives_a_stubbed_vibeqc(monkeypatch) -> None:
    """A stub in sys.modules must not blow up the probe.

    ``importlib.util.find_spec`` raises ValueError for a sys.modules entry
    whose ``__spec__`` is None -- which is what a bare types.ModuleType is,
    and what this very file injects a few tests above. _vibeqc_importable is
    called twice inside create_app, so an unguarded probe turns "vibeqc is
    stubbed" into a ValueError out of app construction.

    A stub answers True: `from vibeqc import ...` genuinely succeeds against
    it, which is the question the submit path is really asking.
    """
    import sys
    import types

    stub = types.ModuleType("vibeqc")
    assert stub.__spec__ is None, "premise: a bare ModuleType has no __spec__"
    monkeypatch.setitem(sys.modules, "vibeqc", stub)

    assert _vibeqc_importable() is True


def test_availability_probe_survives_a_raising_finder(monkeypatch) -> None:
    """A meta-path finder that raises means "not available", not a crash.

    find_spec propagates whatever a finder raises. The viewer must degrade to
    "no container submission" rather than take create_app down with it.
    """
    import importlib.util
    import sys

    monkeypatch.delitem(sys.modules, "vibeqc", raising=False)

    def _boom(name, *args, **kwargs):
        raise ImportError(f"finder refused {name}")

    monkeypatch.setattr(importlib.util, "find_spec", _boom)
    assert _vibeqc_importable() is False
