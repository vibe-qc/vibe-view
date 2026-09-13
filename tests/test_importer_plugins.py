"""Conformance tests for the public third-party importer contract."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from vibeview import converters, importers
from vibeview.cli import main
from vibeview.converters import convert_to_qvf, detect_format, xyz_to_qvf
from vibeview.import_workflow import plan_imports
from vibeview.qvf import QVFReader


@dataclass
class _FakeEntryPoint:
    name: str
    value: object
    dist: object | None = None
    load_calls: list[str] | None = None

    def load(self) -> object:
        if self.load_calls is not None:
            self.load_calls.append(self.name)
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


@dataclass(frozen=True)
class _FakeDistribution:
    name: str

    @property
    def metadata(self) -> dict[str, str]:
        return {"Name": self.name}

    def __str__(self) -> str:
        return self.name


@pytest.fixture(autouse=True)
def _fresh_importer_cache() -> None:
    importers.clear_importer_cache()
    yield
    importers.clear_importer_cache()


def _xyz_qvf(_: Path) -> io.BytesIO:
    return xyz_to_qvf(b"2\nplugin fixture\nH 0 0 0\nH 0 0 0.74\n")


def _plugin_spec(format_name: str = "example", **changes: Any) -> importers.ImporterSpec:
    values: dict[str, Any] = {
        "format_name": format_name,
        "description": "Example importer",
        "extensions": (f".{format_name}",),
        "convert": _xyz_qvf,
    }
    values.update(changes)
    return importers.ImporterSpec(**values)


def _rewrite_qvf(
    payload: io.BytesIO,
    *,
    omit_declared_member: bool = False,
    corrupt_declared_member: bool = False,
) -> bytes:
    """Return a structurally valid archive with broken member integrity."""
    payload.seek(0)
    output = io.BytesIO()
    with zipfile.ZipFile(payload) as source, zipfile.ZipFile(output, "w") as target:
        manifest = json.loads(source.read("manifest.json"))
        member_path = next(iter(manifest["sections"][0]["members"].values()))["path"]
        for name in source.namelist():
            if omit_declared_member and name == member_path:
                continue
            data = source.read(name)
            if corrupt_declared_member and name == member_path:
                data += b"corrupt"
            target.writestr(name, data)
    return output.getvalue()


def test_plugin_extension_is_detected_and_converted(monkeypatch, tmp_path: Path) -> None:
    spec = importers.ImporterSpec(
        format_name="example",
        description="Example chemistry-code output",
        extensions=(".example",),
        convert=_xyz_qvf,
        data_kinds=("structure",),
    )
    monkeypatch.setattr(importers, "_entry_points", lambda: (_FakeEntryPoint("example", spec),))

    source = tmp_path / "calculation.example"
    source.write_text("external output", encoding="utf-8")
    assert detect_format(source) == "plugin:example"

    payload = convert_to_qvf(source)
    reader = QVFReader(payload)
    try:
        assert reader.source.program == "vibe-view"
        structure = reader.read_structure()
        assert len(structure.atoms) == 2
    finally:
        reader.close()


def test_builtin_format_wins_plugin_extension_conflict(monkeypatch, tmp_path: Path) -> None:
    spec = importers.ImporterSpec(
        format_name="xyz-shadow",
        description="Must not replace the built-in XYZ parser",
        extensions=(".xyz",),
        convert=_xyz_qvf,
    )
    monkeypatch.setattr(
        importers,
        "_entry_points",
        lambda: (_FakeEntryPoint("xyz-shadow", spec),),
    )

    source = tmp_path / "molecule.xyz"
    source.write_text("1\nH\nH 0 0 0\n", encoding="utf-8")
    assert detect_format(source) == "xyz"


def test_explicit_plugin_name_ignores_input_suffix(monkeypatch, tmp_path: Path) -> None:
    spec = importers.ImporterSpec(
        format_name="example",
        description="Example",
        extensions=(".example",),
        convert=_xyz_qvf,
    )
    monkeypatch.setattr(importers, "_entry_points", lambda: (_FakeEntryPoint("example", spec),))
    source = tmp_path / "calculation.unknown"
    source.write_text("external output", encoding="utf-8")

    reader = QVFReader(convert_to_qvf(source, format_name="example"))
    reader.close()


def test_explicit_plugin_directory_uses_the_requested_importer_claim(
    monkeypatch, tmp_path: Path
) -> None:
    spec = importers.ImporterSpec(
        format_name="xyz-shadow",
        description="Explicit alternative parser for XYZ files",
        extensions=(".xyz",),
        convert=_xyz_qvf,
    )
    monkeypatch.setattr(
        importers,
        "_entry_points",
        lambda: (_FakeEntryPoint("xyz-shadow", spec),),
    )
    source = tmp_path / "jobs" / "molecule.xyz"
    source.parent.mkdir()
    source.write_text("vendor-specific XYZ", encoding="utf-8")

    plans = plan_imports(
        (source.parent,),
        output=tmp_path / "converted",
        format_name="xyz-shadow",
    )

    assert [plan.source for plan in plans] == [source]


def test_broken_plugin_is_isolated(monkeypatch) -> None:
    monkeypatch.setattr(
        importers,
        "_entry_points",
        lambda: (_FakeEntryPoint("broken", RuntimeError("cannot import dependency")),),
    )
    statuses = importers.discover_importers()
    assert len(statuses) == 1
    assert statuses[0].available is False
    assert "cannot import dependency" in (statuses[0].error or "")
    assert detect_format("ordinary.unknown") is None


def test_api_mismatch_is_reported_without_loading(monkeypatch) -> None:
    spec = importers.ImporterSpec(
        format_name="future",
        description="Future API",
        extensions=(".future",),
        convert=_xyz_qvf,
        api_version=99,
    )
    monkeypatch.setattr(importers, "_entry_points", lambda: (_FakeEntryPoint("future", spec),))
    status = importers.discover_importers()[0]
    assert not status.available
    assert "unsupported importer API 99" in (status.error or "")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"api_version": "1"}, "api_version must be an integer"),
        ({"api_version": True}, "api_version must be an integer"),
        ({"description": 7}, "description must be a string"),
        ({"description": "  "}, "description must be non-empty"),
        ({"extensions": [".malformed"]}, "extensions must be a tuple"),
        ({"extensions": (7,)}, "extensions must contain only strings"),
        ({"extensions": ("malformed",)}, "must start with '.'"),
        (
            {"extensions": (".malformed", ".MALFORMED")},
            "duplicate suffixes",
        ),
        ({"stems": ["INPUT"]}, "stems must be a tuple"),
        ({"stems": (7,)}, "stems must contain only strings"),
        ({"probe": 7}, "probe must be callable or None"),
        ({"data_kinds": ["structure"]}, "data_kinds must be a tuple"),
        ({"data_kinds": (7,)}, "data_kinds must contain only strings"),
        ({"data_kinds": ()}, "must declare at least one QVF data kind"),
        ({"convert": 7}, "convert must be callable"),
    ],
)
def test_malformed_spec_fields_are_isolated(
    monkeypatch,
    changes: dict[str, object],
    message: str,
) -> None:
    spec = _plugin_spec("malformed", **changes)
    monkeypatch.setattr(
        importers,
        "_entry_points",
        lambda: (_FakeEntryPoint("malformed", spec),),
    )

    status = importers.discover_importers()[0]
    assert not status.available
    assert message in (status.error or "")


def test_non_string_format_name_is_isolated(monkeypatch) -> None:
    spec = _plugin_spec(format_name=7)  # type: ignore[arg-type]
    monkeypatch.setattr(
        importers,
        "_entry_points",
        lambda: (_FakeEntryPoint("malformed", spec),),
    )

    status = importers.discover_importers()[0]
    assert not status.available
    assert "format_name must be a string" in (status.error or "")


def test_reserved_names_track_built_in_and_optional_formats() -> None:
    built_in_names = {capability.format_name for capability in converters._BUILTIN_FORMATS}
    assert built_in_names | {"ase", "trexio"} == importers.RESERVED_FORMAT_NAMES


@pytest.mark.parametrize("format_name", sorted(importers.RESERVED_FORMAT_NAMES))
def test_plugin_cannot_claim_reserved_format_name(monkeypatch, format_name: str) -> None:
    spec = _plugin_spec(format_name, extensions=(".vendor",))
    monkeypatch.setattr(
        importers,
        "_entry_points",
        lambda: (_FakeEntryPoint(format_name, spec),),
    )

    status = importers.discover_importers()[0]
    assert not status.available
    assert "reserved by a built-in importer" in (status.error or "")


def test_duplicate_entry_point_names_fail_closed_without_loading(monkeypatch) -> None:
    load_calls: list[str] = []
    entries = (
        _FakeEntryPoint(
            "duplicate",
            _plugin_spec("duplicate", extensions=(".first",)),
            _FakeDistribution("zeta-package"),
            load_calls,
        ),
        _FakeEntryPoint(
            "duplicate",
            _plugin_spec("duplicate", extensions=(".second",)),
            _FakeDistribution("alpha-package"),
            load_calls,
        ),
    )

    def snapshot(order: tuple[_FakeEntryPoint, ...]) -> list[tuple[str | None, str | None]]:
        monkeypatch.setattr(importers, "_entry_points", lambda: order)
        importers.clear_importer_cache()
        statuses = importers.discover_importers()
        assert all(not status.available for status in statuses)
        return [(status.distribution, status.error) for status in statuses]

    forward = snapshot(entries)
    reverse = snapshot(tuple(reversed(entries)))
    assert forward == reverse
    assert [distribution for distribution, _ in forward] == [
        "alpha-package",
        "zeta-package",
    ]
    assert all("duplicate entry point name 'duplicate'" in (error or "") for _, error in forward)
    assert load_calls == []


def test_duplicate_plugin_claims_fail_deterministically(monkeypatch) -> None:
    alpha = _FakeEntryPoint(
        "alpha",
        _plugin_spec("alpha", extensions=(".SHARED",), stems=("CONTROL",)),
    )
    beta = _FakeEntryPoint(
        "beta",
        _plugin_spec("beta", extensions=(".shared",), stems=("CONTROL",)),
    )
    gamma = _FakeEntryPoint("gamma", _plugin_spec("gamma"))

    def snapshot(order: tuple[_FakeEntryPoint, ...]) -> list[tuple[str, bool, str | None]]:
        monkeypatch.setattr(importers, "_entry_points", lambda: order)
        importers.clear_importer_cache()
        return [
            (status.entry_point, status.available, status.error)
            for status in importers.discover_importers()
        ]

    forward = snapshot((alpha, beta, gamma))
    reverse = snapshot((gamma, beta, alpha))
    assert forward == reverse
    assert forward[2] == ("gamma", True, None)
    for entry_point, available, error in forward[:2]:
        assert entry_point in {"alpha", "beta"}
        assert not available
        assert "extension '.shared'" in (error or "")
        assert "stem 'CONTROL'" in (error or "")


def test_system_exit_during_plugin_load_is_isolated(monkeypatch) -> None:
    monkeypatch.setattr(
        importers,
        "_entry_points",
        lambda: (_FakeEntryPoint("exits", SystemExit(7)),),
    )

    status = importers.discover_importers()[0]
    assert not status.available
    assert status.error == "SystemExit: 7"


def test_noisy_plugin_load_cannot_corrupt_formats_json(monkeypatch) -> None:
    spec = _plugin_spec("noisy")

    class _NoisyEntryPoint(_FakeEntryPoint):
        def load(self) -> object:
            print("plugin stdout noise")

            def factory() -> importers.ImporterSpec:
                print("plugin factory noise")
                return spec

            return factory

    monkeypatch.setattr(
        importers,
        "_entry_points",
        lambda: (_NoisyEntryPoint("noisy", spec),),
    )

    result = CliRunner().invoke(main, ["formats", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["formats"][-1]["format_name"] == "noisy"


def test_keyboard_interrupt_during_plugin_load_is_preserved(monkeypatch) -> None:
    monkeypatch.setattr(
        importers,
        "_entry_points",
        lambda: (_FakeEntryPoint("interrupts", KeyboardInterrupt()),),
    )

    with pytest.raises(KeyboardInterrupt):
        importers.discover_importers()


def test_plugin_must_return_valid_qvf(monkeypatch, tmp_path: Path) -> None:
    spec = importers.ImporterSpec(
        format_name="invalid",
        description="Invalid fixture",
        extensions=(".invalid",),
        convert=lambda _: b"not a zip archive",
    )
    monkeypatch.setattr(importers, "_entry_points", lambda: (_FakeEntryPoint("invalid", spec),))
    source = tmp_path / "bad.invalid"
    source.write_bytes(b"source")

    with pytest.raises(importers.ImporterError, match="produced an invalid QVF"):
        convert_to_qvf(source)


@pytest.mark.parametrize("failure", ["missing", "hash"])
def test_plugin_qvf_members_are_fully_validated(
    monkeypatch,
    tmp_path: Path,
    failure: str,
) -> None:
    broken = _rewrite_qvf(
        _xyz_qvf(Path("unused")),
        omit_declared_member=failure == "missing",
        corrupt_declared_member=failure == "hash",
    )
    spec = importers.ImporterSpec(
        format_name="broken-member",
        description="Broken archive fixture",
        extensions=(".broken",),
        convert=lambda _: broken,
    )
    monkeypatch.setattr(
        importers,
        "_entry_points",
        lambda: (_FakeEntryPoint("broken-member", spec),),
    )
    source = tmp_path / "calculation.broken"
    source.write_bytes(b"source")

    with pytest.raises(importers.ImporterError, match="produced an invalid QVF"):
        convert_to_qvf(source)


def test_file_like_plugin_output_is_rewound_and_closed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    returned: list[io.BytesIO] = []

    def convert(_: Path) -> io.BytesIO:
        payload = _xyz_qvf(Path("unused"))
        payload.seek(5)
        returned.append(payload)
        return payload

    spec = importers.ImporterSpec(
        format_name="rewind",
        description="Rewind fixture",
        extensions=(".rewind",),
        convert=convert,
    )
    monkeypatch.setattr(importers, "_entry_points", lambda: (_FakeEntryPoint("rewind", spec),))
    source = tmp_path / "molecule.rewind"
    source.write_bytes(b"source")

    reader = QVFReader(convert_to_qvf(source))
    reader.close()
    assert returned[0].closed


def test_non_binary_plugin_stream_is_closed_on_failure(monkeypatch, tmp_path: Path) -> None:
    returned = io.StringIO("not binary")
    spec = importers.ImporterSpec(
        format_name="text-stream",
        description="Invalid text stream fixture",
        extensions=(".text-stream",),
        convert=lambda _: returned,  # type: ignore[arg-type,return-value]
    )
    monkeypatch.setattr(
        importers,
        "_entry_points",
        lambda: (_FakeEntryPoint("text-stream", spec),),
    )
    source = tmp_path / "molecule.text-stream"
    source.write_bytes(b"source")

    with pytest.raises(importers.ImporterError, match="returned non-binary data"):
        convert_to_qvf(source)
    assert returned.closed
