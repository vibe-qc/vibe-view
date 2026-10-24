"""vibe-view#51 row #39-35, generation 8: the CLI's last-resort catch and four root causes.

The gen 7 verification (note 35396) found content that the reader accepts and
that still ended a command in a traceback:

1. an unpaired UTF-16 surrogate escape (``"\\ud800"``) in a manifest or member
   string: ``click.echo`` raised UnicodeEncodeError;
2. a section of a schema-unknown kind without ``members``: ``merge`` raised
   KeyError;
3. an ``atomic_number`` of 2**63 or more: ``h-add`` raised OverflowError;
4. a periodic cell of magnitude 1e200, 1e-300 or -DBL_MAX: ``show`` and
   ``capture`` raised ZeroDivisionError in ``bonds._image_shells``.

Each is now refused where the archive is read. Independently, any exception
that still reaches the top-level command group becomes ``Error: <type>:
<message>`` with exit status 1, unless ``--debug`` or ``VIBEVIEW_DEBUG=1``
asks for the traceback.
"""

from __future__ import annotations

import errno
import json
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from click.testing import CliRunner

from tests._qvf_builders import write_archive
from tests.conftest import _water_structure_bytes, build_qvf
from vibeview import bonds, cli
from vibeview.qvf import ManifestValidationError, QVFError, QVFReader

_STRUCTURE = "sections/structure.json"
DBL_MAX = sys.float_info.max


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(home / var.lower()))
    monkeypatch.delenv("VIBEVIEW_DEBUG", raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """Readers constructed during the test, and how often each was closed."""
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


@pytest.fixture
def last_resort(monkeypatch: pytest.MonkeyPatch) -> list[BaseException]:
    """Exceptions the CLI's last-resort catch reported during the test.

    A root-cause fix must keep its content away from the catch: the command
    reports the specific reason instead of ``Error: <type>: ...``.
    """
    seen: list[BaseException] = []
    real = getattr(cli, "_report_unexpected", None)

    def record(ctx: Any, exc: BaseException) -> None:
        seen.append(exc)
        if real is not None:
            real(ctx, exc)

    monkeypatch.setattr(cli, "_report_unexpected", record, raising=False)
    return seen


def _water(path: Path, structure: bytes | None = None) -> Path:
    payload = structure if structure is not None else _water_structure_bytes()
    members = {"structure": ("json", _STRUCTURE, payload)}
    return build_qvf(path, [("structure", "structure", members)])


@pytest.fixture
def water(tmp_path: Path) -> Path:
    return _water(tmp_path / "water.qvf")


def _clean(result: Any, spy: types.SimpleNamespace | None = None, label: str = "") -> None:
    exc = result.exception
    assert exc is None or isinstance(exc, SystemExit), (
        f"{label}: uncaught {type(exc).__name__}: {exc!r}"
    )
    assert "Traceback" not in (result.output or ""), f"{label}: traceback in output"
    assert result.exit_code in (0, 1, 2), f"{label}: exit {result.exit_code}"
    if spy is not None:
        assert all(n == 1 for n in spy.counts()), f"{label}: reader close counts {spy.counts()}"


# ── A. The last-resort catch ────────────────────────────────────────────────


def _boom(exc: BaseException):
    def raiser(*a: Any, **k: Any) -> Any:
        raise exc

    return raiser


def test_an_unexpected_error_is_one_line_and_exit_1(water, spy, monkeypatch):
    monkeypatch.setattr("vibeview.api.info", _boom(ZeroDivisionError("float division by zero")))
    result = CliRunner().invoke(cli.main, ["info", str(water)])
    _clean(result, spy, "info")
    assert result.exit_code == 1
    last = result.stderr.strip().splitlines()[-1]
    assert last == "Error: ZeroDivisionError: float division by zero"
    assert result.stdout == ""
    assert len(spy.made) == 1  # the reader was opened, and closed exactly once


def test_an_unexpected_error_without_a_message_names_the_type(water, spy, monkeypatch):
    monkeypatch.setattr("vibeview.api.info", _boom(KeyError()))
    result = CliRunner().invoke(cli.main, ["info", str(water)])
    _clean(result, spy, "info")
    assert result.exit_code == 1
    assert result.stderr.strip() == "Error: KeyError"


def test_json_mode_reports_an_unexpected_error_as_a_json_object(water, spy, monkeypatch):
    monkeypatch.setattr("vibeview.api.info", _boom(TypeError("unhashable type: 'list'")))
    result = CliRunner().invoke(cli.main, ["info", str(water), "--json"])
    _clean(result, spy, "info --json")
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {"error": "TypeError: unhashable type: 'list'"}


def test_batch_compare_json_reports_an_unexpected_error_as_a_json_object(
    tmp_path, water, monkeypatch
):
    # batch-compare --json reports every error as JSON on stdout, as info
    # --json and diff --json do (#67), so stdout is always JSON.
    monkeypatch.setattr(
        "vibeview.batch_compare.compare_batch", _boom(RuntimeError("no reference"))
    )
    other = _water(tmp_path / "other.qvf")
    result = CliRunner().invoke(cli.main, ["batch-compare", str(water), str(other), "--json"])
    _clean(result, label="batch-compare --json")
    assert result.exit_code == 1
    assert result.stderr == ""
    assert json.loads(result.stdout) == {"error": "RuntimeError: no reference"}


def test_the_message_cannot_break_the_report(water, monkeypatch):
    # A message that is not valid Unicode (a lone surrogate, as a file name
    # decoded with surrogateescape carries) is escaped, not re-raised.
    monkeypatch.setattr("vibeview.api.info", _boom(ValueError("bad \udcff name")))
    result = CliRunner().invoke(cli.main, ["info", str(water)])
    _clean(result, label="info")
    assert result.exit_code == 1
    assert "Error: ValueError: bad \\udcff name" in result.stderr


@pytest.mark.parametrize("how", ["flag", "env", "env-true"])
def test_debug_shows_the_traceback(water, spy, monkeypatch, how):
    monkeypatch.setattr("vibeview.api.info", _boom(ZeroDivisionError("float division by zero")))
    args = ["info", str(water)]
    env = {}
    if how == "flag":
        args = ["--debug", *args]
    else:
        env["VIBEVIEW_DEBUG"] = "1" if how == "env" else "true"
    result = CliRunner().invoke(cli.main, args, env=env)
    assert isinstance(result.exception, ZeroDivisionError)
    assert result.exit_code == 1
    assert "Error: ZeroDivisionError" not in result.output
    assert all(n == 1 for n in spy.counts())


def test_debug_env_zero_is_off(water, monkeypatch):
    monkeypatch.setattr("vibeview.api.info", _boom(ZeroDivisionError("x")))
    result = CliRunner().invoke(cli.main, ["info", str(water)], env={"VIBEVIEW_DEBUG": "0"})
    _clean(result, label="info")
    assert "Error: ZeroDivisionError: x" in result.stderr


def test_debug_is_a_documented_group_option():
    result = CliRunner().invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    assert "--debug" in result.output
    assert "VIBEVIEW_DEBUG" in result.output


def test_debug_also_shows_a_qvf_error_traceback(water, monkeypatch):
    monkeypatch.setattr("vibeview.api.info", _boom(QVFError("section 'x' is broken")))
    plain = CliRunner().invoke(cli.main, ["info", str(water)])
    assert plain.exit_code == 1
    assert plain.stderr.strip() == "Error: section 'x' is broken"
    debug = CliRunner().invoke(cli.main, ["--debug", "info", str(water)])
    assert isinstance(debug.exception, QVFError)


def test_keyboard_interrupt_keeps_clicks_abort(water, spy, monkeypatch):
    monkeypatch.setattr("vibeview.api.info", _boom(KeyboardInterrupt()))
    result = CliRunner().invoke(cli.main, ["info", str(water)])
    assert result.exit_code == 1
    assert "Aborted!" in result.output
    assert "Error:" not in result.output
    assert all(n == 1 for n in spy.counts())


@pytest.mark.parametrize("code", [0, 3, "a message"])
def test_system_exit_keeps_its_code(water, monkeypatch, code):
    monkeypatch.setattr("vibeview.api.info", _boom(SystemExit(code)))
    result = CliRunner().invoke(cli.main, ["info", str(water)])
    assert result.exception is None if code == 0 else isinstance(result.exception, SystemExit)
    assert result.exit_code == (1 if isinstance(code, str) else code)
    assert "Error:" not in result.output


def test_click_usage_errors_are_untouched(water):
    result = CliRunner().invoke(cli.main, ["info", str(water), "--no-such-flag"])
    assert result.exit_code == 2
    assert "No such option" in result.output
    assert "Error: NoSuchOption" not in result.output


def test_a_broken_pipe_stays_clicks(water, monkeypatch):
    monkeypatch.setattr("vibeview.api.info", _boom(BrokenPipeError(errno.EPIPE, "Broken pipe")))
    result = CliRunner().invoke(cli.main, ["info", str(water)])
    assert result.exit_code == 1
    assert "Error: BrokenPipeError" not in result.output


def test_python_dash_m_reaches_the_same_catch(water, monkeypatch):
    # ``python -m vibeview`` calls the same group, so it gets the same boundary.
    monkeypatch.setattr("vibeview.api.info", _boom(OverflowError("int too large")))
    result = CliRunner().invoke(cli.main, ["info", str(water)], prog_name="python -m vibeview")
    assert result.exit_code == 1
    assert "Error: OverflowError: int too large" in result.stderr


# ── B1. Unpaired surrogates are refused where they are read ─────────────────


def _manifest_text(water: Path) -> str:
    import zipfile

    with zipfile.ZipFile(water) as zf:
        return zf.read("manifest.json").decode()


def _with_manifest(src: Path, dest: Path, text: str) -> Path:
    import zipfile

    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w") as zout:
        for info in zin.infolist():
            data = text.encode() if info.filename == "manifest.json" else zin.read(info)
            zout.writestr(info.filename, data)
    return dest


@pytest.mark.parametrize(
    ("where", "edit"),
    [
        ("/source/program", lambda d: d["source"].__setitem__("program", "@S@vibe-qc")),
        ("/sections/0/label", lambda d: d["sections"][0].__setitem__("label", "x@S@")),
        ("/extensions", lambda d: d.__setitem__("extensions", {"@S@": {"v": 1}})),
    ],
)
@pytest.mark.parametrize("escape", ["\\ud800", "\\udfff", "\\uDC00\\uD800"])
def test_a_lone_surrogate_in_the_manifest_is_refused(tmp_path, water, where, edit, escape):
    doc = json.loads(_manifest_text(water))
    edit(doc)
    text = json.dumps(doc).replace("@S@", escape)
    bad = _with_manifest(water, tmp_path / "bad.qvf", text)
    with pytest.raises(ManifestValidationError, match="unpaired UTF-16 surrogate") as info:
        QVFReader(bad)
    assert where in str(info.value)


def test_a_paired_surrogate_escape_is_ordinary_text(tmp_path, water):
    doc = json.loads(_manifest_text(water))
    doc["source"]["program"] = "@S@"
    text = json.dumps(doc).replace("@S@", "\\ud83d\\ude00 vibe-qc")
    good = _with_manifest(water, tmp_path / "emoji.qvf", text)
    with QVFReader(good) as reader:
        assert reader.source.program == "\U0001f600 vibe-qc"
    result = CliRunner().invoke(cli.main, ["info", str(good)])
    assert result.exit_code == 0
    assert "\U0001f600 vibe-qc" in result.stdout


_MANIFEST_COMMANDS = {
    "info": lambda b, t: ["info", b],
    "info-short": lambda b, t: ["info", b, "--short"],
    "info-json": lambda b, t: ["info", b, "--json"],
    "show-info": lambda b, t: ["show", b, "--info"],
    "diff": lambda b, t: ["diff", b, str(t / "water.qvf")],
    "stats": lambda b, t: ["stats", str(Path(b).parent)],
}


@pytest.mark.parametrize("command", list(_MANIFEST_COMMANDS))
def test_commands_report_a_manifest_surrogate_cleanly(
    tmp_path, water, spy, last_resort, command
):
    folder = tmp_path / "bad"
    folder.mkdir()
    text = _manifest_text(water).replace('"vibe-qc"', '"\\ud800vibe-qc"', 1)
    bad = _with_manifest(water, folder / "bad.qvf", text)
    result = CliRunner().invoke(cli.main, _MANIFEST_COMMANDS[command](str(bad), tmp_path))
    _clean(result, spy, command)
    assert not last_resort, last_resort
    if command == "stats":
        assert "Unreadable: 1" in result.output
    else:
        assert result.exit_code == 1
        assert "unpaired UTF-16 surrogate" in result.output


def test_validate_reports_a_manifest_surrogate_as_invalid(tmp_path, water, spy, last_resort):
    text = _manifest_text(water).replace('"vibe-qc"', '"\\ud800vibe-qc"', 1)
    bad = _with_manifest(water, tmp_path / "bad.qvf", text)
    result = CliRunner().invoke(cli.main, ["validate", str(bad)])
    _clean(result, spy, "validate")
    assert result.exit_code == 1
    assert "INVALID" in result.output and "/source/program" in result.output


def _water_with_symbol(path: Path, symbol_escape: str) -> Path:
    text = _water_structure_bytes().decode().replace('"O"', f'"{symbol_escape}"', 1)
    return _water(path, text.encode())


def test_a_lone_surrogate_in_a_member_is_a_section_error(tmp_path):
    bad = _water_with_symbol(tmp_path / "bad.qvf", "\\ud800")
    with QVFReader(bad) as reader:
        with pytest.raises(QVFError, match="unpaired UTF-16 surrogate at /atoms/0/symbol"):
            reader.read_structure()
        assert "unpaired UTF-16 surrogate" in (reader.section_error("structure") or "")


def _with_atom_properties(path: Path, structure: bytes) -> Path:
    charges = np.array([-0.6, 0.3, 0.3], dtype="<f8").tobytes()
    return build_qvf(
        path,
        [
            ("structure", "structure", {"structure": ("json", _STRUCTURE, structure)}),
            (
                "charges",
                "atom_properties",
                {"mulliken_charge": ("binary", "sections/charges.bin", charges, "float64", [3])},
            ),
        ],
    )


_MEMBER_COMMANDS = {
    "table-atom-properties": lambda b, t: ["table", b, "--kind", "atom_properties"],
    "show-all": lambda b, t: ["show", b, "--all", "--plain", "--size", "40x20"],
    "show": lambda b, t: ["show", b, "--plain", "--size", "40x20"],
    "export-xyz": lambda b, t: ["export", b, "-f", "xyz", "-o", str(t / "o.xyz")],
    "h-add": lambda b, t: ["h-add", b, "-o", str(t / "h.qvf")],
}


@pytest.mark.parametrize("command", list(_MEMBER_COMMANDS))
def test_commands_report_a_member_surrogate_cleanly(tmp_path, spy, last_resort, command):
    text = _water_structure_bytes().decode().replace('"O"', '"\\ud800"', 1)
    bad = _with_atom_properties(tmp_path / "bad.qvf", text.encode())
    result = CliRunner().invoke(cli.main, _MEMBER_COMMANDS[command](str(bad), tmp_path))
    _clean(result, spy, command)
    assert not last_resort, last_resort
    if command == "table-atom-properties":
        # The charge table still prints; it leaves the element column blank
        # when the structure cannot be read, as for any other structure error.
        assert result.exit_code == 0
        assert "-0.6" in result.output
    else:
        assert "unpaired UTF-16 surrogate" in result.output


# ── B2. Every section carries ``members`` (QVF spec § 4.1) ───────────────────


def _unknown_kind_archive(path: Path, section: dict[str, Any]) -> Path:
    structure = _water_structure_bytes()
    from tests._qvf_builders import sha256

    return write_archive(
        [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {"path": _STRUCTURE, "format": "json", "sha256": sha256(structure)}
                },
            },
            section,
        ],
        {_STRUCTURE: structure},
        dest=path,
    )


@pytest.mark.parametrize(
    "section",
    [
        {"id": "fut", "kind": "future.kind"},
        {"id": "fut", "kind": "future.kind", "members": None},
        {"id": "fut", "kind": "future.kind", "members": []},
    ],
)
def test_a_section_without_members_is_refused_at_open(tmp_path, section):
    bad = _unknown_kind_archive(tmp_path / "bad.qvf", section)
    with pytest.raises(ManifestValidationError, match="members"):
        QVFReader(bad)


def test_an_unknown_kind_with_members_still_opens(tmp_path):
    good = _unknown_kind_archive(
        tmp_path / "ok.qvf", {"id": "fut", "kind": "future.kind", "members": {}}
    )
    with QVFReader(good) as reader:
        assert reader.has_section("fut")


def test_validate_and_merge_report_a_section_without_members(tmp_path, water, spy, last_resort):
    bad = _unknown_kind_archive(tmp_path / "bad.qvf", {"id": "fut", "kind": "future.kind"})
    validated = CliRunner().invoke(cli.main, ["validate", str(bad)])
    _clean(validated, spy, "validate")
    assert validated.exit_code == 1
    assert "INVALID" in validated.output and "'fut'" in validated.output
    for pair in ([water, bad], [bad, water]):
        out = tmp_path / "m.qvf"
        merged = CliRunner().invoke(cli.main, ["merge", *map(str, pair), "-o", str(out)])
        _clean(merged, spy, "merge")
        assert merged.exit_code == 1
        assert "members" in merged.output
        assert not out.exists()
    assert not last_resort, last_resort


# ── B3. atomic_number is an element number ──────────────────────────────────


def _water_with_z(path: Path, z: int) -> Path:
    doc = json.loads(_water_structure_bytes())
    doc["atoms"][0]["atomic_number"] = z
    return _water(path, json.dumps(doc).encode())


@pytest.mark.parametrize("z", [2**63, 2**64 + 1, 10**30, 10**300])
def test_an_atomic_number_beyond_int64_is_a_section_error(tmp_path, z):
    bad = _water_with_z(tmp_path / "bad.qvf", z)
    with QVFReader(bad) as reader:
        with pytest.raises(QVFError, match=r"atomic_number is .*outside 0\.\.9223372036854775807"):
            reader.read_structure()
        assert "atomic_number" in (reader.section_error("structure") or "")


@pytest.mark.parametrize("z", [0, 1, 118, 119, 999, 2**63 - 1])
def test_atomic_numbers_up_to_int64_still_read_and_bond(tmp_path, spy, last_resort, z):
    # Numbers beyond the element table are kept on purpose ("Z999" labels).
    good = _water_with_z(tmp_path / "ok.qvf", z)
    with QVFReader(good) as reader:
        assert reader.read_structure().atoms[0].atomic_number == z
    result = CliRunner().invoke(cli.main, ["h-add", str(good), "-o", str(tmp_path / "h.qvf")])
    _clean(result, spy, "h-add")
    assert not last_resort, last_resort
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("command", ["h-add", "show", "export-xyz"])
def test_commands_report_a_huge_atomic_number(tmp_path, spy, last_resort, command):
    bad = _water_with_z(tmp_path / "bad.qvf", 2**63)
    result = CliRunner().invoke(cli.main, _MEMBER_COMMANDS[command](str(bad), tmp_path))
    _clean(result, spy, command)
    assert not last_resort, last_resort
    assert result.exit_code == 1
    assert "atomic_number" in result.output


def test_vibration_atoms_are_bounded_too(tmp_path):
    meta = {
        "atoms": [{"symbol": "H", "position": [0, 0, 0], "atomic_number": 2**63}],
        "frequencies": [100.0],
    }
    disp = np.zeros((1, 1, 3), dtype="<f8").tobytes()
    path = build_qvf(
        tmp_path / "vib.qvf",
        [
            (
                "structure",
                "structure",
                {"structure": ("json", _STRUCTURE, _water_structure_bytes())},
            ),
            (
                "vib",
                "vibrations",
                {
                    "metadata": ("json", "sections/vib.json", json.dumps(meta).encode()),
                    "displacements": ("binary", "sections/vib.bin", disp, "float64", [1, 1, 3]),
                },
            ),
        ],
    )
    with QVFReader(path) as reader, pytest.raises(QVFError, match="atomic_number"):
        reader.read_vibrations("vib")


def _vibrations(path: Path, meta: dict[str, Any], shape: tuple[int, ...] = (3, 3, 3)) -> Path:
    disp = np.zeros(shape, dtype="<f8").tobytes()
    return build_qvf(
        path,
        [
            (
                "structure",
                "structure",
                {"structure": ("json", _STRUCTURE, _water_structure_bytes())},
            ),
            (
                "vib",
                "vibrations",
                {
                    "metadata": ("json", "sections/vib.json", json.dumps(meta).encode()),
                    "displacements": ("binary", "sections/vib.bin", disp, "float64", list(shape)),
                },
            ),
        ],
    )


_WATER_ATOMS = json.loads(_water_structure_bytes())["atoms"]
_FREQUENCIES = [1595.0, 3657.0, 3756.0]


@pytest.mark.parametrize(
    ("meta", "shape", "reason"),
    [
        # Found by test_cli_fuzz's wide runs (fix-vv-51k/probes): seed 51270004
        # case 505, and seed 51270006 case 205.
        (
            {"atoms": [*_WATER_ATOMS[:1], {**_WATER_ATOMS[1], "position": [0.0, -0.4692]},
                       _WATER_ATOMS[2]], "frequencies": _FREQUENCIES},
            (3, 3, 3),
            "three finite numbers",
        ),
        ({"atoms": _WATER_ATOMS, "frequencies": 1595.0}, (1, 3, 3), "flat list"),
        ({"atoms": _WATER_ATOMS, "frequencies": _FREQUENCIES}, (3, 2, 3), "expected"),
        ({"atoms": _WATER_ATOMS, "frequencies": _FREQUENCIES}, (2, 3, 3), "expected"),
    ],
)
def test_vibrations_have_the_shape_the_commands_use(
    tmp_path, spy, last_resort, meta, shape, reason
):
    path = _vibrations(tmp_path / "vib.qvf", meta, shape)
    with QVFReader(path) as reader, pytest.raises(QVFError, match=reason):
        reader.read_vibrations("vib")
    for args in (
        ["animate", str(path), "-f", "frames", "-o", str(tmp_path / "anim")],
        ["table", str(path), "--kind", "vibrations", "--format", "json"],
    ):
        result = CliRunner().invoke(cli.main, args)
        _clean(result, spy, args[0])
        assert not last_resort, last_resort
        assert result.exit_code == 1, (args, result.output)


# ── B4. A degenerate periodic cell ──────────────────────────────────────────


def _crystal(path: Path, scale: float, positions: list[list[float]] | None = None) -> Path:
    """Rocksalt-like NaCl in a cubic cell of edge ``scale``."""
    if positions is None:
        positions = [[0.0, 0.0, 0.0], [0.5 * scale, 0.5 * scale, 0.5 * scale]]
    doc = {
        "atoms": [
            {"symbol": "Na", "position": positions[0], "atomic_number": 11},
            {"symbol": "Cl", "position": positions[1], "atomic_number": 17},
        ],
        "pbc": [True, True, True],
        "lattice_vectors": [[scale, 0.0, 0.0], [0.0, scale, 0.0], [0.0, 0.0, scale]],
    }
    return _water(path, json.dumps(doc).encode())


@pytest.mark.parametrize("scale", [1e200, 1e-300, -DBL_MAX, DBL_MAX])
def test_a_degenerate_periodic_cell_is_a_section_error(tmp_path, scale):
    bad = _crystal(tmp_path / "bad.qvf", scale)
    with QVFReader(bad) as reader, pytest.raises(QVFError, match="degenerate lattice"):
        reader.read_structure()


def test_an_ordinary_cell_still_bonds(tmp_path):
    good = _crystal(tmp_path / "ok.qvf", 5.64 / 2)
    with QVFReader(good) as reader:
        structure = reader.read_structure()
        assert reader.connectivity(structure)


def test_atoms_that_span_too_many_images_are_refused(tmp_path):
    # Finite, but a bond search would have to enumerate ~10**18 cell images.
    bad = _crystal(tmp_path / "far.qvf", 4.0, [[0.0, 0.0, 0.0], [4.0e6, 4.0e6, 4.0e6]])
    with QVFReader(bad) as reader, pytest.raises(QVFError, match="degenerate lattice"):
        reader.read_structure()


@pytest.mark.parametrize("command", ["show", "capture", "h-add"])
@pytest.mark.parametrize("scale", [1e200, 1e-300, -DBL_MAX])
def test_show_and_capture_report_a_degenerate_cell(tmp_path, spy, last_resort, command, scale):
    bad = _crystal(tmp_path / "bad.qvf", scale)
    args = {
        "show": ["show", str(bad), "--plain", "--size", "40x20"],
        "capture": ["capture", str(bad), "-s", "structure", "-o", str(tmp_path / "c.png")],
        "h-add": ["h-add", str(bad), "-o", str(tmp_path / "h.qvf")],
    }[command]
    result = CliRunner().invoke(cli.main, args)
    _clean(result, spy, command)
    assert not last_resort, last_resort
    assert result.exit_code == 1
    assert "degenerate lattice" in result.output


def test_one_huge_cell_component_is_degenerate(tmp_path, spy, last_resort):
    # Found by test_cli_fuzz's wide runs (seed 51270010, case 5): one
    # component of DBL_MAX left every spacing finite, and the cell's length
    # overflowed in the renderer's transform (capture, animate).
    a = 2.82
    doc = {
        "atoms": [
            {"symbol": "Na", "position": [0.0, 0.0, 0.0], "atomic_number": 11},
            {"symbol": "Cl", "position": [a, 0.0, 0.0], "atomic_number": 17},
        ],
        "pbc": [True, True, True],
        "lattice_vectors": [[0.0, a, a], [a, DBL_MAX, a], [a, a, 0.0]],
    }
    bad = _water(tmp_path / "bad.qvf", json.dumps(doc).encode())
    with QVFReader(bad) as reader, pytest.raises(QVFError, match="degenerate lattice"):
        reader.read_structure()
    result = CliRunner().invoke(
        cli.main, ["capture", str(bad), "-s", "structure", "-o", str(tmp_path / "c.png")]
    )
    _clean(result, spy, "capture")
    assert not last_resort, last_resort
    assert result.exit_code == 1
    assert "degenerate lattice" in result.output


@pytest.mark.parametrize("scale", [1e200, 1e-300, -DBL_MAX])
def test_image_shells_reports_a_degenerate_lattice(scale):
    lattice = np.eye(3) * scale
    with pytest.raises(ValueError, match="degenerate lattice"):
        bonds.infer_bonds(
            [[0.0, 0.0, 0.0], [0.1, 0.1, 0.1]], [6, 6], lattice=lattice, pbc=(True, True, True)
        )


def test_image_shells_keeps_the_singular_cell_fallback():
    # A singular cell already fell back to a single shell; that stays.
    lattice = np.array([[1.0, 0, 0], [2.0, 0, 0], [0, 0, 1.0]])
    assert bonds._image_shells(lattice, (True, True, True), np.zeros((1, 3)), 2.0) == [1, 1, 1]


def test_edit_overlay_refuses_a_degenerate_lattice(tmp_path):
    good = _crystal(tmp_path / "ok.qvf", 2.82)
    with QVFReader(good) as reader:
        with pytest.raises(ValueError, match="degenerate lattice"):
            reader.set_edit_overlay(
                [[0, 0, 0]], ["Na"], lattice_vectors=np.eye(3) * 1e-300
            )
        assert not reader.has_edit_overlay
