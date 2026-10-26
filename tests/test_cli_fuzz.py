"""vibe-view#51 row #39-35, generation 8: fuzzed archives through every CLI command.

Generation 7 made the reader leak nothing but ``QVFError``. Its verification
(note 35396) then found content the reader accepts that still ended commands
in a traceback one layer out: in ``click.echo``, in ``merge`` and in bond
inference. So this test drives the commands themselves, not the reader. It
mutates small synthesized archives (manifest, JSON members and binary
members) and runs each one through every command that reads an archive, with
click's ``CliRunner``. For every invocation it asserts:

* no traceback text and no uncaught exception;
* exit status 0, 1 or 2;
* every ``QVFReader`` the command constructed was closed exactly once;
* the CLI's last-resort catch did not fire: each failure is reported by the
  code that understands it, not as ``Error: <type>: ...``.

The generators follow the gen 7 verifier's probe (``fuzz_p51j.py``):
type-preserving edge values (1e200, 1e-300, ±DBL_MAX, 2**63, ...), retyped
values, deleted keys and list items, duplicated list entries, lone surrogate
and control escapes, number literals at the double-range edges, and binary
members truncated, grown, reshaped or retyped, always with a matching sha256.

Seeded, so a failure names its case and reproduces. About 3 s.
"""

from __future__ import annotations

import collections
import contextlib
import copy
import hashlib
import json
import random
import re
import sys
import types
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from click.testing import CliRunner

from tests.conftest import _water_structure_bytes, build_qvf
from vibeview import cli
from vibeview.qvf import QVFReader

SEED = 20260927
CASES = 50
# Rendering commands cost 40-200 ms each; they run on every HEAVY_EVERY-th case.
HEAVY_EVERY = 10
DBL_MAX = sys.float_info.max

# ── Seed archives, synthesized (no real archive is read or copied) ─────────


def _water_seed(path: Path) -> Path:
    vib_meta = {
        "atoms": json.loads(_water_structure_bytes())["atoms"],
        "frequencies": [1595.0, 3657.0, 3756.0],
    }
    disp = np.zeros((3, 3, 3), dtype="<f8")
    disp[:, 1, 1] = 0.5
    charges = np.array([-0.66, 0.33, 0.33], dtype="<f8")
    return build_qvf(
        path,
        [
            (
                "structure",
                "structure",
                {"structure": ("json", "sections/structure.json", _water_structure_bytes())},
            ),
            (
                "charges",
                "atom_properties",
                {
                    "mulliken_charge": (
                        "binary", "sections/mulliken.bin", charges.tobytes(), "float64", [3],
                    )
                },
            ),
            (
                "vib",
                "vibrations",
                {
                    "metadata": ("json", "sections/vib.json", json.dumps(vib_meta).encode()),
                    "displacements": (
                        "binary", "sections/vib.bin", disp.tobytes(), "float64", [3, 3, 3],
                    ),
                },
            ),
        ],
    )


def _crystal_seed(path: Path) -> Path:
    a = 5.64
    structure = {
        "atoms": [
            {"symbol": "Na", "position": [0.0, 0.0, 0.0], "atomic_number": 11},
            {"symbol": "Cl", "position": [a / 2, 0.0, 0.0], "atomic_number": 17},
        ],
        "pbc": [True, True, True],
        "lattice_vectors": [[0.0, a / 2, a / 2], [a / 2, 0.0, a / 2], [a / 2, a / 2, 0.0]],
    }
    kpath = {
        "n_kpoints": 4,
        "n_bands": 2,
        "fermi": 0.0,
        "segments": [
            {"label_start": "Γ", "label_end": "X", "start": 0, "end": 2},
            {"label_start": "X", "label_end": "L", "start": 2, "end": 3},
        ],
    }
    eig = np.array([[[-1.0, 1.0], [-0.8, 1.2], [-0.5, 1.5], [-0.7, 1.1]]], dtype="<f8")
    return build_qvf(
        path,
        [
            (
                "structure",
                "structure",
                {"structure": ("json", "sections/structure.json", json.dumps(structure).encode())},
            ),
            (
                "bands",
                "bands",
                {
                    "kpath": ("json", "sections/kpath.json", json.dumps(kpath).encode()),
                    "eigenvalues": (
                        "binary", "sections/eig.bin", eig.tobytes(), "float64", [1, 4, 2],
                    ),
                },
            ),
        ],
    )


# ── Value generators (after the gen 7 verifier's fuzz_p51j.py) ─────────────

EDGE_NUMBERS: list[Any] = [
    0, -0.0, 1, -1, 5e-324, 2.2250738585072014e-308, DBL_MAX, -DBL_MAX, 1e308, 1e200,
    1e-300, -1e200, 2**53 + 1, 2**63, -(2**63) - 1, 2**64 + 1, 10**100, 3, 118, 119,
    1_000_000, 0.5, 123456789.123,
]
ODD_STRINGS = [
    "", " ", "‮evil", "\U0001f600" * 5, "\x00", "a\nb", "../../../etc/passwd",
    "structure", "x_vendor.thing", "future.kind", "%s%n{0}", "<script>", "é" * 10,
    "﻿", "Ω" * 1000, "1e400", "NaN", "null",
]
# Spliced into JSON text, because json.dumps cannot write them.
RAW_NUMBERS = [
    "1.7976931348623157e308", "1.7976931348623159e308", "-1.7976931348623159e308",
    "4.9e-324", "1e-400", "-0", "9" * 309, "NaN", "-Infinity", "Infinity",
]
RAW_STRINGS = ['"\\ud800x"', '"\\udfff\\ud800"', '"\\u0000"', '"\\u202e"', '"\\ud83d\\ude00"']
KEYS = ["id", "kind", "members", "critical", "path", "format", "sha256", "dtype", "shape",
        "label", "value", "x", "", "é"]


def _rand_json(rng: random.Random, depth: int = 0) -> Any:
    r = rng.random()
    if depth > 3 or r < 0.5:
        return rng.choice(
            [None, True, False, rng.choice(EDGE_NUMBERS), rng.choice(ODD_STRINGS),
             rng.randint(-5, 5), rng.random() * 10 ** rng.randint(-10, 10)]
        )
    if r < 0.75:
        return [_rand_json(rng, depth + 1) for _ in range(rng.randrange(4))]
    return {rng.choice(KEYS): _rand_json(rng, depth + 1) for _ in range(rng.randrange(4))}


def _slots(doc: Any, path: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    out = [path]
    if isinstance(doc, dict):
        for key, child in doc.items():
            out += _slots(child, (*path, key))
    elif isinstance(doc, list):
        for i, child in enumerate(doc):
            out += _slots(child, (*path, i))
    return out


def _get(doc: Any, path: tuple[Any, ...]) -> Any:
    for key in path:
        doc = doc[key]
    return doc


def _put(doc: Any, path: tuple[Any, ...], value: Any) -> Any:
    if not path:
        return value
    _get(doc, path[:-1])[path[-1]] = value
    return doc


def _preserve(rng: random.Random, value: Any) -> Any:
    """A value of the same JSON type, at an edge."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return rng.choice([n for n in EDGE_NUMBERS if isinstance(n, int)] + [-value, 0])
    if isinstance(value, float):
        return rng.choice(
            [n for n in EDGE_NUMBERS if isinstance(n, float)] + [-value, value * 1e300]
        )
    if isinstance(value, str):
        return rng.choice([*ODD_STRINGS, value * 100, value.upper(), value[::-1]])
    if isinstance(value, list):
        c = rng.random()
        if c < 0.3 or not value:
            return []
        if c < 0.6:
            return value[: rng.randrange(len(value))]
        if c < 0.8:
            return value + [copy.deepcopy(rng.choice(value)) for _ in range(rng.randrange(1, 3))]
        return [_preserve(rng, v) for v in value]
    if isinstance(value, dict):
        value = dict(value)
        if value and rng.random() < 0.6:
            del value[rng.choice(list(value))]
        else:
            value[rng.choice(["x_extra", "é", "", "critical", "members"])] = _rand_json(rng)
        return value
    return _rand_json(rng)


def _mutate_doc(rng: random.Random, doc: Any) -> tuple[Any, str]:
    ops = []
    for _ in range(rng.choice([1, 1, 2, 3])):
        path = rng.choice(_slots(doc))
        op = rng.random()
        if op < 0.45:
            doc = _put(doc, path, _preserve(rng, _get(doc, path)))
            ops.append("preserve")
        elif op < 0.7:
            doc = _put(doc, path, _rand_json(rng))
            ops.append("retype")
        elif op < 0.9 and path:
            parent = _get(doc, path[:-1])
            del parent[path[-1]]
            ops.append("delete")
        else:
            target = _get(doc, path)
            if isinstance(target, list) and target:
                target.append(copy.deepcopy(target[0]))
                ops.append("duplicate")
    return doc, "+".join(ops) or "noop"


def _raw_mutate(rng: random.Random, text: str) -> tuple[str, str]:
    c = rng.random()
    if c < 0.4:
        numbers = list(re.finditer(r"(?<![\w.\"])-?\d+(\.\d+)?([eE][-+]?\d+)?(?![\w.\"])", text))
        if numbers:
            m = rng.choice(numbers)
            literal = rng.choice(RAW_NUMBERS)
            return text[: m.start()] + literal + text[m.end():], f"number:{literal[:12]}"
    if c < 0.85:
        strings = list(re.finditer(r'"[^"\\]{1,40}"', text))
        if strings:
            m = rng.choice(strings)
            literal = rng.choice(RAW_STRINGS)
            return text[: m.start()] + literal + text[m.end():], f"string:{literal}"
    return text.replace('"structure"', '"future.kind"', 1), "unknown-kind"


def _unknown_kind_without_members(doc: Any) -> Any:
    doc["sections"].append({"id": "fut", "kind": "future.kind"})
    return doc


# ── Archive IO ──────────────────────────────────────────────────────────────


def _entries(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _write(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def _encode(text: str) -> bytes:
    return text.encode("utf-8", "surrogatepass")


def _fuzzed_archive(rng: random.Random, seed: Path, dest: Path) -> tuple[Path, str]:
    entries = _entries(seed)
    manifest = json.loads(entries["manifest.json"])
    target = rng.random()
    if target < 0.4:
        if rng.random() < 0.08:
            doc, op = _unknown_kind_without_members(manifest), "section-without-members"
        elif rng.random() < 0.7:
            doc, op = _mutate_doc(rng, manifest)
        else:
            text, op = _raw_mutate(rng, json.dumps(manifest, ensure_ascii=False))
            entries["manifest.json"] = _encode(text)
            return _write(dest, entries), f"manifest-raw:{op}"
        with contextlib.suppress(ValueError, OverflowError):  # dumps of a fuzzed value
            entries["manifest.json"] = _encode(json.dumps(doc))
        return _write(dest, entries), f"manifest:{op}"
    members = [
        (section, role, spec)
        for section in manifest["sections"]
        for role, spec in section["members"].items()
    ]
    section, role, spec = rng.choice(members)
    body = entries[spec["path"]]
    if spec["format"] == "json":
        if rng.random() < 0.7:
            doc, op = _mutate_doc(rng, json.loads(body))
            with contextlib.suppress(ValueError, OverflowError):
                body = _encode(json.dumps(doc))
        else:
            text, op = _raw_mutate(rng, body.decode())
            body = _encode(text)
        op = f"json:{section['kind']}/{role}:{op}"
    else:
        c = rng.random()
        if c < 0.3:
            body = body[: max(0, len(body) - rng.choice([1, 8, len(body)]))]
            op = "truncate"
        elif c < 0.45:
            body = body + b"\x00" * rng.choice([1, 8, 4096])
            op = "grow"
        elif c < 0.7:
            spec["shape"] = rng.choice(
                [[0], [], [2**31, 2**31], [-1], [1] * 40, [*spec["shape"], 1]]
            )
            op = f"shape={spec['shape']}"
        elif c < 0.85:
            spec["dtype"] = rng.choice(["float32", "int32", "uint8", "complex128", "object", ""])
            op = f"dtype={spec['dtype']}"
        else:
            values = np.frombuffer(body, dtype="<f8").copy()
            values[rng.randrange(len(values))] = rng.choice(
                [np.nan, np.inf, -np.inf, 1e200, -DBL_MAX, 5e-324]
            )
            body = values.tobytes()
            op = "values"
        op = f"binary:{section['kind']}/{role}:{op}"
    spec["sha256"] = hashlib.sha256(body).hexdigest()
    entries[spec["path"]] = body
    entries["manifest.json"] = json.dumps(manifest).encode()
    return _write(dest, entries), op


# ── The command matrix ──────────────────────────────────────────────────────

Args = list[str]


def _light(bad: str, good: str, out: Path) -> dict[str, Args]:
    return {
        "spectrum": ["spectrum", bad, "--section", "ir", "-o", str(out / "spectrum.csv")],
        "info": ["info", bad],
        "info-json": ["info", bad, "--json"],
        "info-short": ["info", bad, "--short"],
        "validate": ["validate", bad],
        "table": ["table", bad],
        "table-atom-properties": ["table", bad, "--kind", "atom_properties"],
        "table-vibrations-json": ["table", bad, "--kind", "vibrations", "--format", "json"],
        "show-info": ["show", bad, "--info"],
        "show-all": ["show", bad, "--all", "--plain", "--size", "30x12"],
        "show": ["show", bad, "--plain", "--size", "30x12"],
        "export-xyz": ["export", bad, "-f", "xyz", "-o", str(out / "o.xyz")],
        "export-json": ["export", bad, "-f", "json", "-o", str(out / "o.json")],
        "export-cif": ["export", bad, "-f", "cif", "-o", str(out / "o.cif")],
        "diff": ["diff", bad, good],
        "diff-json": ["diff", good, bad, "--json"],
        "slice": ["slice", bad, "-k", "structure", "-o", str(out / "s.qvf")],
        "merge": ["merge", good, bad, "-o", str(out / "m.qvf")],
        "h-add": ["h-add", bad, "-o", str(out / "h.qvf")],
        "supercell": ["supercell", bad, "-o", str(out / "sc.qvf")],
        "batch-compare-json": ["batch-compare", good, bad, "--json"],
        "stats": ["stats", str(Path(bad).parent)],
        "open": ["open", bad, "--no-browser"],
        "compare": ["compare", good, bad, "--no-browser"],
        "from-vq": ["from-vq", "job1", "-o", str(out / "fetched"), "--no-browser"],
    }


def _heavy(bad: str, out: Path) -> dict[str, Args]:
    return {
        "capture": ["capture", bad, "-o", str(out / "c.png"), "--size", "80x60"],
        "animate": ["animate", bad, "-f", "frames", "-o", str(out / "anim")],
        "batch": ["batch", bad, "-o", str(out / "gallery")],
        "dashboard": ["dashboard", bad, "-o", str(out / "d.png")],
    }


# Commands that never read an archive the user names, or that only run
# interactively; the bad-archive matrix covers ``tui`` for archives that fail
# to open.
NOT_ARCHIVE_READERS = {
    "capture-selftest": "renders a built-in scene",
    "config": "reads the config file only",
    "demo": "writes its own example archive",
    "desktop": "launches the Electron shell",
    "doctor": "checks the environment",
    "examples": "copies packaged examples",
    "formats": "lists formats",
    "import": "reads loose formats, not QVF",
    "quickstart": "opens a packaged example in the browser",
    "recent": "reads the recent-files list",
    "serve": "starts the file-browser server",
    "tui": "interactive once the archive opens",
    "vq-features": "queries vibe-queue",
}


def test_every_command_is_fuzzed_or_excused(tmp_path):
    fuzzed = {args[0] for args in _light("a", "b", tmp_path).values()}
    fuzzed |= {args[0] for args in _heavy("a", tmp_path).values()}
    assert fuzzed.isdisjoint(NOT_ARCHIVE_READERS)
    assert fuzzed | set(NOT_ARCHIVE_READERS) == set(cli.main.commands)


# ── The test ────────────────────────────────────────────────────────────────


def install_spy(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Close count per constructed reader, in construction order."""
    closes: list[int] = []
    real_init, real_close = QVFReader.__init__, QVFReader.close

    def init(self: QVFReader, *a: Any, **k: Any) -> None:
        real_init(self, *a, **k)
        self.__dict__["_fuzz_serial"] = len(closes)
        closes.append(0)

    def close(self: QVFReader) -> None:
        serial = self.__dict__.get("_fuzz_serial")
        if serial is not None:
            closes[serial] += 1
        real_close(self)

    monkeypatch.setattr(QVFReader, "__init__", init)
    monkeypatch.setattr(QVFReader, "close", close)
    return closes


def prepare_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """An isolated HOME, a stubbed browser viewer and vibe-queue, and a
    recorder on the CLI's last-resort catch."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(home / var.lower()))
    monkeypatch.delenv("VIBEVIEW_DEBUG", raising=False)
    monkeypatch.chdir(tmp_path)
    # The browser viewer is stubbed: open, compare and from-vq still open,
    # check and close their archives, but no server starts.
    monkeypatch.setattr(cli, "_configure_logging", lambda p: None)
    monkeypatch.setattr(cli, "_announce_when_bound", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_abort_if_port_in_use", lambda *a, **k: None)
    monkeypatch.setattr(
        cli, "_load_app", lambda: (lambda readers, *a, **k: MagicMock(), lambda app, **k: None)
    )
    workspace = tmp_path / "job1"
    vq = types.ModuleType("vq")
    vq_fetch = types.ModuleType("vq.fetch")
    vq_fetch.fetch_local = lambda job_id, out: workspace  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vq", vq)
    monkeypatch.setitem(sys.modules, "vq.fetch", vq_fetch)
    unexpected: list[BaseException] = []
    real = getattr(cli, "_report_unexpected", None)

    def record(ctx: Any, exc: BaseException) -> None:
        unexpected.append(exc)
        if real is not None:
            real(ctx, exc)

    monkeypatch.setattr(cli, "_report_unexpected", record, raising=False)
    return types.SimpleNamespace(workspace=workspace, unexpected=unexpected)


def run_fuzz(
    tmp_path: Path,
    spy: list[int],
    environment: types.SimpleNamespace,
    *,
    seed: int = SEED,
    cases: int = CASES,
    heavy_every: int = HEAVY_EVERY,
) -> tuple[list[str], dict[str, Any]]:
    """Fuzz ``cases`` archives through the command matrix; problems and statistics."""
    seeds = {
        "water": _water_seed(tmp_path / "water.qvf"),
        "crystal": _crystal_seed(tmp_path / "crystal.qvf"),
    }
    rng = random.Random(seed)
    runner = CliRunner()
    problems: list[str] = []
    exits: collections.Counter[int] = collections.Counter()
    outcomes: collections.Counter[str] = collections.Counter()
    targets: collections.Counter[str] = collections.Counter()
    invocations = 0
    for case in range(cases):
        name = rng.choice(sorted(seeds))
        case_dir = tmp_path / f"c{case:04d}"
        (case_dir / "in").mkdir(parents=True)
        (case_dir / "out").mkdir()
        bad, op = _fuzzed_archive(rng, seeds[name], case_dir / "in" / "bad.qvf")
        targets[op.split(":")[0]] += 1
        environment.workspace.mkdir(exist_ok=True)
        for old in environment.workspace.iterdir():
            old.unlink()
        (environment.workspace / "bad.qvf").write_bytes(bad.read_bytes())
        commands = _light(str(bad), str(seeds[name]), case_dir / "out")
        if case % heavy_every == 0:
            commands.update(_heavy(str(bad), case_dir / "out"))
        for command, args in commands.items():
            start, seen = len(spy), len(environment.unexpected)
            result = runner.invoke(cli.main, args)
            invocations += 1
            exits[result.exit_code] += 1
            label = f"case {case} (seed {seed}, {name}, {op}) {command}"
            exc = result.exception
            if exc is not None and not isinstance(exc, SystemExit):
                problems.append(f"{label}: uncaught {type(exc).__name__}: {exc!s:.160}")
            if "Traceback" in (result.output or ""):
                problems.append(f"{label}: traceback in output")
            if result.exit_code not in (0, 1, 2):
                problems.append(f"{label}: exit {result.exit_code}")
            if any(n != 1 for n in spy[start:]):
                problems.append(f"{label}: reader close counts {spy[start:]}")
            for caught in environment.unexpected[seen:]:
                problems.append(
                    f"{label}: last-resort catch: {type(caught).__name__}: {caught!s:.160}"
                )
        start = len(spy)
        try:
            with QVFReader(bad):
                outcomes["opens"] += 1
        except Exception as error:  # noqa: BLE001 - classify only
            outcomes[type(error).__name__] += 1
        del spy[start:]  # this classification is not a command
    stats = {
        "seed": seed,
        "cases": cases,
        "invocations": invocations,
        "readers": len(spy),
        "readers_closed_not_once": sum(1 for n in spy if n != 1),
        "exits": dict(sorted(exits.items())),
        "open_outcomes": dict(outcomes),
        "mutation_targets": dict(targets),
    }
    return problems, stats


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    return install_spy(monkeypatch)


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    return prepare_environment(tmp_path, monkeypatch)


def test_fuzzed_archives_through_every_command(tmp_path, spy, environment):
    problems, stats = run_fuzz(tmp_path, spy, environment)
    assert not problems, "\n".join(problems[:40])
    outcomes, exits = stats["open_outcomes"], stats["exits"]
    # The fuzzer must reach both sides: archives that open and reach the
    # commands' own code, and archives each command refuses.
    assert outcomes.get("opens", 0) > CASES // 3, stats
    assert sum(v for k, v in outcomes.items() if k != "opens") > CASES // 10, stats
    assert exits.get(0, 0) > stats["invocations"] // 4, stats
    assert exits.get(1, 0) > stats["invocations"] // 10, stats
    assert stats["readers"] > stats["invocations"] // 2, stats
    assert stats["readers_closed_not_once"] == 0, stats
