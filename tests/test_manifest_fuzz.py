"""vibe-view#51 row #39-35, generation 7: fuzzed manifests only ever fail as QVFError.

Six generations each fixed the malformed manifest the previous verification
found, and each time the next shape escaped: ``[]`` (AttributeError), deep
nesting (RecursionError), then ``"qvf_version": 1e400`` (OverflowError from
``int(inf)``) and a critical section whose ``kind`` is a list (TypeError from
an unhashable set lookup; note 35240). The reader now converts any exception
between opening the zip and building the model into a QVFError, and this file
checks that property directly instead of one counterexample at a time: random
JSON of every type in every position of a valid manifest, plus random section
entries, must open or raise a QVFError subclass, and never leave the zip open.

The generator is seeded, so a failure reproduces from its case number.
"""

from __future__ import annotations

import io
import json
import random
import zipfile
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests._qvf_builders import sha256
from tests.conftest import _water_structure_bytes
from vibeview import api, cli
from vibeview.qvf import (
    CriticalSectionUnsupportedError,
    ManifestValidationError,
    QVFError,
    QVFOpenError,
    QVFReader,
)

SEED = 20260926
CASES = 500
# The reader's documented bound (docs/qvf.md); the fuzzer stays at or below
# it so deep values reach the checks after parsing instead of the bound.
MAX_DEPTH = 256

_STRUCTURE = _water_structure_bytes()
_FILES = {"sections/structure.json": _STRUCTURE}


def _seed_manifest() -> dict[str, Any]:
    """A valid manifest that uses most root blocks the schema allows."""
    return {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.0.0-test", "calculation": "fuzz"},
        "provenance": {
            "method": "RHF",
            "basis": "sto-3g",
            "scf_energy": {"value": -75.0, "units": "hartree"},
            "run_status": "converged",
            "checkpoint": {"seq": 1},
        },
        "dipole_moment": {"total_debye": 1.8, "vector_debye": [0, 0, 1.8], "origin": [0, 0, 0]},
        "thermochemistry": {"zpve_eh": 0.02, "temperature_k": 298.15},
        "viewer_defaults": {"auto_open": ["structure"], "structure": {"opacity": 0.5}},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "sections/structure.json",
                        "format": "json",
                        "sha256": sha256(_STRUCTURE),
                    }
                },
            }
        ],
    }


# Numbers ``json.dumps`` cannot write as such; spliced in as raw text.
_RAW_NUMBERS = ["1e400", "-1e400", "1" * 5000, "-0.0", "1e-400"]
_KEYS = ["id", "kind", "members", "critical", "component", "trajectory_ref", "path",
         "format", "sha256", "dtype", "shape", "x", ""]


def _deep(rng: random.Random, depth: int) -> Any:
    value: Any = rng.choice([[], {}, None, 1])
    for _ in range(depth - 1):
        value = [value] if rng.random() < 0.5 else {rng.choice(_KEYS): value}
    return value


def _value(rng: random.Random, depth: int = 0) -> Any:
    """A random JSON value; raw-text numbers come back as a ``@RAW<n>@`` marker."""
    r = rng.random()
    if depth > 3 or r < 0.45:
        return rng.choice(
            [
                None, True, False, 0, -1, 1, 2, 1.5, -1e308, 10**400, -(10**30),
                float("inf"), float("-inf"), float("nan"), "", "structure", "x_v.k",
                "volume.density", "a" * 300, "\u0000", "퟿",
                f"@RAW{rng.randrange(len(_RAW_NUMBERS))}@",
            ]
        )
    if r < 0.65:
        return [_value(rng, depth + 1) for _ in range(rng.randrange(4))]
    if r < 0.9:
        return {rng.choice(_KEYS): _value(rng, depth + 1) for _ in range(rng.randrange(4))}
    return _deep(rng, rng.randrange(2, MAX_DEPTH - 2))


def _slots(doc: Any, path: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Every position in ``doc``: the root, each dict value and list item."""
    out = [path]
    if isinstance(doc, dict):
        for key, child in doc.items():
            out += _slots(child, (*path, key))
    elif isinstance(doc, list):
        for i, child in enumerate(doc):
            out += _slots(child, (*path, i))
    return out


def _mutate(rng: random.Random) -> str:
    """One fuzzed manifest.json text."""
    doc: Any = _seed_manifest()
    for _ in range(rng.choice([1, 1, 1, 2, 3])):
        op = rng.random()
        if op < 0.55:
            path = rng.choice(_slots(doc))
            if not path:
                doc = _value(rng)
                continue
            parent = doc
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = _value(rng)
        elif op < 0.75:
            dicts = [p for p in _slots(doc) if isinstance(_get(doc, p), dict)]
            if dicts:
                _get(doc, rng.choice(dicts))[rng.choice([*_KEYS, "provenance"])] = _value(rng)
        elif isinstance(doc, dict) and isinstance(doc.get("sections"), list):
            entry = {k: _value(rng) for k in rng.sample(_KEYS[:6], rng.randrange(1, 6))}
            if rng.random() < 0.6:
                entry["critical"] = rng.choice([True, True, False, 1])
            doc["sections"].append(entry)
    text = json.dumps(doc)
    for i, raw in enumerate(_RAW_NUMBERS):
        text = text.replace(f'"@RAW{i}@"', raw)
    return text


def _get(doc: Any, path: tuple[Any, ...]) -> Any:
    for key in path:
        doc = doc[key]
    return doc


def _archive(manifest_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", manifest_text)
        for name, data in _FILES.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def opened_zips(monkeypatch: pytest.MonkeyPatch) -> list[zipfile.ZipFile]:
    """Every ZipFile a QVFReader opened during the test."""
    made: list[zipfile.ZipFile] = []
    real = QVFReader._open_zip

    def spy(target: Any) -> zipfile.ZipFile:
        zf = real(target)
        made.append(zf)
        return zf

    monkeypatch.setattr(QVFReader, "_open_zip", staticmethod(spy))
    return made


def _exercise(reader: QVFReader) -> None:
    """The accessors every command reads straight after opening."""
    _ = reader.provenance, reader.run_status, reader.checkpoint_info
    _ = reader.dipole_moment, reader.thermochemistry, reader.viewer_defaults
    reader.scf_energy_eh()
    reader.lifecycle_warnings()
    reader.manifest_json()
    for section in reader.sections:
        reader.section_is_partial(section.id)
    # What ``info`` (and ``info --json``, ``diff``, ``stats``) print; the
    # gen 7 probe found float() of a schema-valid 10**400 energy here.
    json.dumps(api.info(reader))


def test_fuzzed_manifests_open_or_raise_a_qvf_error(opened_zips):
    rng = random.Random(SEED)
    outcomes: dict[str, int] = {}
    for case in range(CASES):
        text = _mutate(rng)
        try:
            reader = QVFReader(_archive(text))
        except QVFError as error:
            outcomes[type(error).__name__] = outcomes.get(type(error).__name__, 0) + 1
        except Exception as error:  # the property under test
            pytest.fail(
                f"case {case} (seed {SEED}): {type(error).__name__}: {error!s:.200}\n"
                f"manifest: {text[:500]}"
            )
        else:
            outcomes["opened"] = outcomes.get("opened", 0) + 1
            try:
                _exercise(reader)
            except QVFError:
                pass
            except Exception as error:
                pytest.fail(f"case {case} (seed {SEED}), after open: {error!r:.200}")
            finally:
                reader.close()
        assert opened_zips[-1].fp is None, f"case {case}: zip left open"
    # The fuzzer must reach both sides of the property, not just the parser.
    assert outcomes.get("opened", 0) > CASES // 20, outcomes
    assert outcomes.get("ManifestValidationError", 0) > CASES // 4, outcomes
    assert len(opened_zips) == CASES


# ── The two gen 6 counterexamples, and the structural catch-all ─────────────


def _manifest_with(**changes: Any) -> dict[str, Any]:
    doc = _seed_manifest()
    doc.update(changes)
    return doc


VERSION_INF = json.dumps(_manifest_with(qvf_version="@V@")).replace('"@V@"', "1e400")
CRITICAL_LIST_KIND = json.dumps(
    _manifest_with(
        sections=[
            *_seed_manifest()["sections"],
            {"id": "x", "kind": ["weird"], "critical": True, "members": {}},
        ]
    )
)


def test_version_inf_is_a_manifest_validation_error(opened_zips):
    with pytest.raises(ManifestValidationError, match="qvf_version"):
        QVFReader(_archive(VERSION_INF))
    assert opened_zips[-1].fp is None


@pytest.mark.parametrize("kind", [["weird"], {"a": 1}, 7, None])
def test_a_critical_section_with_a_non_string_kind_is_unsupported(opened_zips, kind):
    doc = _seed_manifest()
    doc["sections"].append({"id": "x", "kind": kind, "critical": True, "members": {}})
    with pytest.raises(CriticalSectionUnsupportedError, match="'x' is marked critical"):
        QVFReader(_archive(json.dumps(doc)))
    assert opened_zips[-1].fp is None


@pytest.mark.parametrize("error", [TypeError, OverflowError, KeyError, ZeroDivisionError])
def test_any_unexpected_error_while_loading_becomes_a_qvf_error(
    opened_zips, monkeypatch, error
):
    import vibeview.qvf as qvf

    def boom(*a: Any, **k: Any) -> Any:
        raise error("unexpected shape")

    monkeypatch.setattr(qvf, "_consumer_usable_kinds", boom)
    with pytest.raises(ManifestValidationError, match=error.__name__) as info:
        QVFReader(_archive(json.dumps(_seed_manifest())))
    assert isinstance(info.value.__cause__, error)
    assert opened_zips[-1].fp is None


def test_an_unexpected_error_before_the_manifest_is_parsed_is_an_open_error(
    opened_zips, monkeypatch
):
    import vibeview.qvf as qvf

    def boom(*a: Any, **k: Any) -> Any:
        raise TypeError("odd read")

    monkeypatch.setattr(qvf.json, "loads", boom)
    with pytest.raises(QVFOpenError, match="manifest.json") as info:
        QVFReader(_archive(json.dumps(_seed_manifest())))
    assert isinstance(info.value.__cause__, TypeError)
    assert opened_zips[-1].fp is None


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_interrupts_are_not_swallowed_and_still_close_the_zip(opened_zips, monkeypatch, error):
    import vibeview.qvf as qvf

    def boom(*a: Any, **k: Any) -> Any:
        raise error()

    monkeypatch.setattr(qvf, "_consumer_usable_kinds", boom)
    with pytest.raises(error):
        QVFReader(_archive(json.dumps(_seed_manifest())))
    assert opened_zips[-1].fp is None


def test_a_misbehaving_stream_is_an_open_error():
    class Stream(io.RawIOBase):
        def read(self, *a: Any) -> bytes:  # noqa: ARG002
            raise TypeError("broken stream")

        def seek(self, *a: Any) -> int:
            raise TypeError("broken stream")

    with pytest.raises(QVFOpenError, match="cannot open archive") as info:
        QVFReader(Stream())
    assert isinstance(info.value.__cause__, TypeError)


@pytest.mark.parametrize(
    ("where", "raw"),
    [
        ("provenance/scf_energy/value", "1" + "0" * 400),  # schema-valid integer
        ("provenance/scf_energy/value", "1e400"),
        ("provenance/wall_seconds", "Infinity"),
        ("dipole_moment/total_debye", "-Infinity"),
        ("viewer_defaults/structure/opacity", "NaN"),  # NaN passes min/max checks
    ],
    ids=["huge-int", "1e400", "Infinity", "-Infinity", "NaN"],
)
def test_numbers_no_double_can_hold_are_refused_at_open(opened_zips, where, raw):
    doc = _seed_manifest()
    *parents, leaf = where.split("/")
    node = doc
    for key in parents:
        node = node.setdefault(key, {})
    node[leaf] = "@N@"
    with pytest.raises(ManifestValidationError, match=f"not a finite double at /{where}"):
        QVFReader(_archive(json.dumps(doc).replace('"@N@"', raw)))
    assert opened_zips[-1].fp is None


# ── The CLI on the two counterexamples ───────────────────────────────────────


@pytest.fixture
def counterexamples(tmp_path: Path) -> dict[str, Path]:
    out = {}
    for name, text in (("version_inf", VERSION_INF), ("critical_list_kind", CRITICAL_LIST_KIND)):
        path = tmp_path / f"{name}.qvf"
        path.write_bytes(_archive(text))
        out[name] = path
    return out


@pytest.mark.parametrize("name", ["version_inf", "critical_list_kind"])
def test_validate_reports_the_counterexample_invalid(counterexamples, name):
    result = CliRunner().invoke(cli.main, ["validate", str(counterexamples[name])])
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    assert result.exit_code == 1
    assert f"{name}.qvf: INVALID: " in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("name", ["version_inf", "critical_list_kind"])
@pytest.mark.parametrize(
    "args",
    [["info"], ["info", "--json"], ["table"], ["export", "-f", "xyz"]],
    ids=["info", "info-json", "table", "export"],
)
def test_commands_exit_1_cleanly_on_the_counterexample(tmp_path, counterexamples, name, args):
    target = str(counterexamples[name])
    extra = ["-o", str(tmp_path / "o.xyz")] if args[0] == "export" else []
    result = CliRunner().invoke(cli.main, [*args, target, *extra])
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    if args == ["info", "--json"]:
        assert "error" in json.loads(result.output)
    else:
        assert "rror" in result.output


# ── Section reads: valid JSON of the wrong shape (maintainer point 2) ───────


def test_every_public_section_reader_is_wrapped():
    import vibeview.qvf as qvf

    public = {
        name
        for name in vars(QVFReader)
        if name.startswith("read_") and name != "read_member_bytes"
    }
    assert public <= set(qvf._SECTION_READERS)
    for name in qvf._SECTION_READERS:
        assert hasattr(getattr(QVFReader, name), "__wrapped__"), name


_SPECTRUM = json.dumps({"frequencies": [1000.0, 1600.0], "intensities": [1.0, 0.5]}).encode()
_GRID = json.dumps(
    {"origin": [0, 0, 0], "voxel_vectors": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "shape": [2, 2, 2]}
).encode()
_DAT = bytes(range(32))  # 8 float32 values
_MEMBERS: dict[str, tuple[str, str, str, bytes]] = {
    # section id, kind, member name, zip path, payload
    "structure": ("structure", "structure", "sections/structure.json", _STRUCTURE),
    "ir": ("spectra.ir", "spectrum", "sections/ir.json", _SPECTRUM),
    "density": ("volume.density", "grid", "sections/density.grid.json", _GRID),
}
_SECTION_READS = {
    "structure": ["read_structure", "read_bonds"],
    "ir": ["read_spectra"],
    "density": ["read_volume_grid", "read_volume_data"],
}


def _sections_archive(payloads: dict[str, bytes]) -> bytes:
    sections = []
    files = {"sections/density.dat": _DAT}
    for sid, (kind, member, path, _default) in _MEMBERS.items():
        body = payloads[sid]
        files[path] = body
        members = {member: {"path": path, "format": "json", "sha256": sha256(body)}}
        if sid == "density":
            members["data"] = {
                "path": "sections/density.dat",
                "format": "binary",
                "dtype": "float32",
                "shape": [2, 2, 2],
                "sha256": sha256(_DAT),
            }
        sections.append({"id": sid, "kind": kind, "members": members})
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.0.0-test", "calculation": "fuzz"},
        "sections": sections,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _defaults() -> dict[str, bytes]:
    return {sid: spec[3] for sid, spec in _MEMBERS.items()}


def test_a_wrong_shape_member_is_a_recorded_qvf_error():
    # Valid JSON, wrong shape. read_spectra used to subscript the int and the
    # generic guard reported the TypeError; the reader now names it (#66).
    with QVFReader(_sections_archive({**_defaults(), "ir": b"5"})) as reader:
        with pytest.raises(
            QVFError, match="spectrum member in section 'ir': payload is not a JSON object"
        ):
            reader.read_spectra("ir")
        assert reader.section_error_kind("ir") == "invalid payload"
        reader.read_structure()  # the other sections still read


def test_a_section_reader_passes_interrupts_through(monkeypatch):
    def boom(self: QVFReader, section_id: str) -> Any:
        raise KeyboardInterrupt

    import vibeview.qvf as qvf

    monkeypatch.setattr(QVFReader, "read_spectra", qvf._section_read(boom))
    with QVFReader(_sections_archive(_defaults())) as reader, pytest.raises(KeyboardInterrupt):
        reader.read_spectra("ir")


MEMBER_CASES = 300


def test_fuzzed_members_read_or_raise_a_qvf_error():
    rng = random.Random(SEED + 1)
    outcomes = {"read": 0, "QVFError": 0}
    for case in range(MEMBER_CASES):
        sid = rng.choice(list(_MEMBERS))
        doc: Any = json.loads(_MEMBERS[sid][3])
        path = rng.choice(_slots(doc))
        if path:
            _get(doc, path[:-1])[path[-1]] = _value(rng)
        else:
            doc = _value(rng)
        text = json.dumps(doc)
        for i, raw in enumerate(_RAW_NUMBERS):
            text = text.replace(f'"@RAW{i}@"', raw)
        with QVFReader(_sections_archive({**_defaults(), sid: text.encode()})) as reader:
            for method in _SECTION_READS[sid]:
                try:
                    getattr(reader, method)(sid)
                except QVFError:
                    outcomes["QVFError"] += 1
                except Exception as error:
                    pytest.fail(
                        f"case {case} (seed {SEED + 1}) {method}: {error!r:.200}\n{text:.300}"
                    )
                else:
                    outcomes["read"] += 1
    assert outcomes["QVFError"] > MEMBER_CASES // 4, outcomes
    assert outcomes["read"] > 0, outcomes


@pytest.mark.parametrize("key", ["energies", "occupations"])
def test_scalar_mo_energies_are_a_section_error_not_a_table_traceback(
    tmp_path, tables_qvf, key
):
    # The gen 7 member probe: a bare number parsed as a 0-d array, the reader
    # returned it, and ``table --kind wavefunction.gto`` failed on len().
    with zipfile.ZipFile(tables_qvf) as zf:
        entries = {name: zf.read(name) for name in zf.namelist()}
    manifest = json.loads(entries["manifest.json"])
    section = next(s for s in manifest["sections"] if s["kind"] == "wavefunction.gto")
    member = section["members"]["mo_metadata"]
    meta = json.loads(entries[member["path"]])
    meta[key] = 1.0
    entries[member["path"]] = json.dumps(meta).encode()
    member["sha256"] = sha256(entries[member["path"]])
    entries["manifest.json"] = json.dumps(manifest).encode()
    bad = tmp_path / "bad.qvf"
    with zipfile.ZipFile(bad, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)

    with QVFReader(bad) as reader, pytest.raises(QVFError, match=f"{key} must be a flat list"):
        reader.read_wavefunction_gto(section["id"])
    args = ["table", str(bad), "--kind", "wavefunction.gto", "--format", "json"]
    result = CliRunner().invoke(cli.main, args)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    assert result.exit_code == 1
    assert "Traceback" not in result.output
