"""Conformance tests for the public renderer plugin contract (#113).

Entry points are faked, as ``test_importer_plugins.py`` does for importers;
the end-to-end tests load the real sample package from
``docs/examples/renderer-plugin`` through such an entry point. The candidate
pipeline installs that package for real (``scripts/smoke_renderer_plugin.py``).
"""

from __future__ import annotations

import importlib
import json
import sys
import tomllib
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from vibeview import renderer_plugins
from vibeview.cli import main
from vibeview.kinds import classify_section
from vibeview.qvf import CriticalSectionUnsupportedError, QVFReader
from vibeview.renderer_plugins import RendererSpec

SAMPLE_ROOT = Path(__file__).resolve().parents[1] / "docs" / "examples" / "renderer-plugin"
KIND = "x_example.timings"


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


class _TableRenderer:
    def __init__(self, section: Any, reader: Any) -> None:
        self.section = section
        self.reader = reader

    def load(self) -> Any:
        return self.reader.read_json_member(self.section.id, "steps")

    def render_to_html(self) -> str:
        return f"<table><tr><td>{len(self.load())} steps</td></tr></table>"


def _spec(kind: str = KIND, **changes: Any) -> RendererSpec:
    values: dict[str, Any] = {"kind": kind, "title": "Step Timings", "renderer": _TableRenderer}
    values.update(changes)
    return RendererSpec(**values)


def _install(monkeypatch: pytest.MonkeyPatch, *entry_points: _FakeEntryPoint) -> None:
    monkeypatch.setattr(renderer_plugins, "_entry_points", lambda: tuple(entry_points))
    renderer_plugins.clear_renderer_cache()


@pytest.fixture(autouse=True)
def _fresh_renderer_cache():
    renderer_plugins.clear_renderer_cache()
    yield
    renderer_plugins.clear_renderer_cache()


@pytest.fixture
def sample_package(monkeypatch: pytest.MonkeyPatch):
    """The sample plugin's module, importable as if it were installed."""
    monkeypatch.syspath_prepend(str(SAMPLE_ROOT / "src"))
    sys.modules.pop("vibeview_example_renderer", None)
    module = importlib.import_module("vibeview_example_renderer")
    yield module
    sys.modules.pop("vibeview_example_renderer", None)


@pytest.fixture
def sample_archive(tmp_path: Path) -> Path:
    """The sample's archive, written by its own ``make_archive.py``."""
    spec = importlib.util.spec_from_file_location("make_archive", SAMPLE_ROOT / "make_archive.py")
    assert spec is not None and spec.loader is not None
    make_archive = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_archive)
    return Path(make_archive.build(tmp_path / "timings.qvf"))


def _critical_archive(path: Path) -> Path:
    """The sample archive with its vendor section marked ``critical``."""
    with zipfile.ZipFile(path) as source:
        files = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(files["manifest.json"])
    for section in manifest["sections"]:
        if section["kind"] == KIND:
            section["critical"] = True
    files["manifest.json"] = json.dumps(manifest).encode()
    out = path.with_name("critical.qvf")
    with zipfile.ZipFile(out, "w") as target:
        for name, data in files.items():
            target.writestr(name, data)
    return out


# ── discovery and validation ────────────────────────────────────────────


def test_plugin_kind_classifies_as_rendered(monkeypatch) -> None:
    assert classify_section(KIND) == ("skipped", "vendor namespace (example)")
    _install(monkeypatch, _FakeEntryPoint(KIND, _spec()))
    assert classify_section(KIND) == ("rendered", "via plugin")
    # Built-in classification is untouched by an installed plugin.
    assert classify_section("structure") == ("rendered", None)
    assert classify_section("x_other.thing") == ("skipped", "vendor namespace (other)")


def test_spec_object_and_factory_are_both_accepted(monkeypatch) -> None:
    _install(
        monkeypatch,
        _FakeEntryPoint(KIND, _spec()),
        _FakeEntryPoint("x_example.other", lambda: _spec("x_example.other")),
    )
    assert {spec.kind for spec in renderer_plugins.available_renderers()} == {
        KIND,
        "x_example.other",
    }
    assert renderer_plugins.get_renderer_plugin(KIND).icon == renderer_plugins.DEFAULT_PLUGIN_ICON


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (_spec(api_version=2), "unsupported renderer API 2"),
        (_spec(api_version=True), "api_version must be an integer"),
        (_spec("x_example.wrong"), "must match entry point name"),
        (_spec("structure"), "rendered by vibe-view itself"),
        (_spec("bonds"), "rendered by vibe-view itself"),
        (_spec(title="  "), "title must be non-empty"),
        (_spec(icon="timer"), "Material Design Icons name"),
        (_spec(icon="mdi-"), "Material Design Icons name"),
        (_spec(renderer=_TableRenderer(None, None)), "renderer must be a class"),
        (_spec(renderer=type("NoHtml", (), {"load": lambda self: None})), "render_to_html"),
        (_spec(renderer=type("NoLoad", (), {"render_to_html": lambda self: ""})), "load()"),
        ({"kind": KIND}, "must expose RendererSpec"),
        (_TableRenderer, "must expose RendererSpec"),
    ],
)
def test_contract_violations_are_isolated(monkeypatch, value: object, message: str) -> None:
    name = "structure" if getattr(value, "kind", None) == "structure" else KIND
    name = "bonds" if getattr(value, "kind", None) == "bonds" else name
    _install(
        monkeypatch,
        _FakeEntryPoint(name, value),
        _FakeEntryPoint("x_example.good", _spec("x_example.good")),
    )
    statuses = {status.entry_point: status for status in renderer_plugins.discover_renderers()}
    broken = statuses[name]
    assert not broken.available and broken.spec is None
    assert message in (broken.error or "")
    # The broken plugin renders nothing; the good one is unaffected.
    assert statuses["x_example.good"].available
    assert renderer_plugins.plugin_kinds() == frozenset({"x_example.good"})
    assert classify_section("structure") == ("rendered", None)


def test_kind_with_whitespace_is_rejected(monkeypatch) -> None:
    _install(monkeypatch, _FakeEntryPoint("x_example bad", _spec("x_example bad")))
    (status,) = renderer_plugins.discover_renderers()
    assert "without whitespace" in (status.error or "")


def test_load_failures_are_isolated(monkeypatch) -> None:
    _install(
        monkeypatch,
        _FakeEntryPoint("x_example.broken", ImportError("no module named acme")),
        _FakeEntryPoint("x_example.exits", SystemExit(3)),
        _FakeEntryPoint(KIND, _spec()),
    )
    statuses = {status.entry_point: status for status in renderer_plugins.discover_renderers()}
    assert statuses["x_example.broken"].error == "ImportError: no module named acme"
    assert statuses["x_example.exits"].error == "SystemExit: 3"
    assert renderer_plugins.plugin_kinds() == frozenset({KIND})


def test_keyboard_interrupt_during_load_is_preserved(monkeypatch) -> None:
    _install(monkeypatch, _FakeEntryPoint(KIND, KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        renderer_plugins.discover_renderers()


def test_noisy_plugin_cannot_corrupt_info_json(monkeypatch, sample_archive) -> None:
    def noisy() -> RendererSpec:
        print("plugin chatter on stdout")
        print("plugin chatter on stderr", file=sys.stderr)
        return _spec()

    _install(monkeypatch, _FakeEntryPoint(KIND, noisy))
    result = CliRunner().invoke(main, ["info", str(sample_archive), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "chatter" not in result.output
    assert {s["id"]: s["status"] for s in payload["sections"]}["timings"] == "rendered"


def test_duplicate_kind_claims_fail_closed_without_loading(monkeypatch) -> None:
    calls: list[str] = []
    _install(
        monkeypatch,
        _FakeEntryPoint(KIND, _spec(), _FakeDistribution("acme-a"), calls),
        _FakeEntryPoint(KIND, _spec(), _FakeDistribution("acme-b"), calls),
    )
    statuses = renderer_plugins.discover_renderers()
    assert [status.distribution for status in statuses] == ["acme-a", "acme-b"]
    assert all(not status.available for status in statuses)
    assert all("duplicate entry point name" in (status.error or "") for status in statuses)
    assert calls == []
    assert classify_section(KIND)[0] == "skipped"


def test_discovery_is_cached_until_cleared(monkeypatch) -> None:
    calls: list[str] = []
    _install(monkeypatch, _FakeEntryPoint(KIND, _spec(), load_calls=calls))
    renderer_plugins.plugin_kinds()
    renderer_plugins.plugin_kinds()
    assert calls == [KIND]
    renderer_plugins.clear_renderer_cache()
    renderer_plugins.plugin_kinds()
    assert calls == [KIND, KIND]


# ── the reader ──────────────────────────────────────────────────────────


def test_critical_plugin_section_opens_only_with_the_plugin(monkeypatch, sample_archive) -> None:
    critical = _critical_archive(sample_archive)
    with pytest.raises(CriticalSectionUnsupportedError, match="marked critical"):
        QVFReader(critical)
    _install(monkeypatch, _FakeEntryPoint(KIND, _spec()))
    with QVFReader(critical) as reader:
        assert reader.get_section("timings").kind == KIND


def test_public_member_readers_verify_and_decode(sample_archive, sample_qvf) -> None:
    with QVFReader(sample_archive) as reader:
        steps = reader.read_json_member("timings", "steps")
        assert json.loads(reader.read_text_member("timings", "steps")) == steps
    with QVFReader(sample_qvf) as reader:
        section = next(s for s in reader.sections if s.kind == "volume.density")
        name = next(n for n, m in section.members.items() if m.format == "binary")
        values = reader.read_binary_member(section.id, name)
        assert values.size > 0


# ── every surface, with the real sample package ─────────────────────────


def test_sample_package_matches_its_entry_point(sample_package) -> None:
    config = tomllib.loads((SAMPLE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    entry_points = config["project"]["entry-points"]["vibeview.renderers"]
    spec = sample_package.renderer()
    assert entry_points == {spec.kind: "vibeview_example_renderer:renderer"}
    assert spec.api_version == renderer_plugins.RENDERER_API_VERSION


def test_info_lists_the_sample_kind_as_rendered(monkeypatch, sample_package, sample_archive):
    _install(monkeypatch, _FakeEntryPoint(KIND, sample_package.renderer))
    payload = json.loads(
        CliRunner().invoke(main, ["info", str(sample_archive), "--json"]).output
    )
    rows = {s["id"]: s for s in payload["sections"]}
    assert (rows["timings"]["status"], rows["timings"]["status_detail"]) == (
        "rendered",
        "via plugin",
    )
    assert (rows["structure"]["status"], rows["structure"]["status_detail"]) == ("rendered", None)
    text = CliRunner().invoke(main, ["info", str(sample_archive)]).output
    line = next(line for line in text.splitlines() if line.startswith("timings"))
    assert "rendered" in line and "via plugin" in line


def test_info_reports_an_unclaimed_vendor_kind_as_skipped(sample_archive) -> None:
    payload = json.loads(
        CliRunner().invoke(main, ["info", str(sample_archive), "--json"]).output
    )
    timings = {s["id"]: s for s in payload["sections"]}["timings"]
    assert (timings["status"], timings["status_detail"]) == (
        "skipped",
        "vendor namespace (example)",
    )


def _app(archive: Path) -> Any:
    from vibeview.app import create_app

    return create_app(QVFReader(archive))


def test_browser_lists_and_opens_the_sample_panel(monkeypatch, sample_package, sample_archive):
    _install(monkeypatch, _FakeEntryPoint(KIND, sample_package.renderer))
    app = _app(sample_archive)
    entry = next(e for e in app.state.sidebar_entries if e.get("id") == "timings")
    assert entry["kind_icon"] == "mdi-timer-outline"
    assert entry["icon"] == "mdi-check-circle"
    row = next(r for r in app.state.section_list if r["id"] == "timings")
    assert row["supported"] and row["status"] == "rendered"

    app.controller.activate_section("timings")
    assert app.state.properties_title == "Step Timings"
    html = app.state.properties_html
    assert "<table>" in html and "SCF iterations" in html and "10.540" in html
    assert app.state.status_message == "Loaded Step Timings: timings"


def test_a_failing_plugin_panel_is_contained(monkeypatch, sample_archive) -> None:
    class Exploding(_TableRenderer):
        def render_to_html(self) -> str:
            raise RuntimeError("plugin bug")

    _install(monkeypatch, _FakeEntryPoint(KIND, _spec(renderer=Exploding)))
    app = _app(sample_archive)
    app.controller.activate_section("timings")
    assert app.state.status_message == (
        f"Renderer plugin for {KIND} failed: RuntimeError: plugin bug"
    )
    assert not app.state.properties_html


def test_a_plugin_returning_non_text_is_contained(monkeypatch, sample_archive) -> None:
    class NotText(_TableRenderer):
        def render_to_html(self) -> Any:
            return b"<table/>"

    _install(monkeypatch, _FakeEntryPoint(KIND, _spec(renderer=NotText)))
    app = _app(sample_archive)
    app.controller.activate_section("timings")
    assert "returned bytes, not str" in app.state.status_message
    assert not app.state.properties_html


def test_terminal_shows_the_generic_pane(monkeypatch, sample_package, sample_archive) -> None:
    pytest.importorskip("textual")
    from vibeview.tui.app import VibeViewTUI

    _install(monkeypatch, _FakeEntryPoint(KIND, sample_package.renderer))
    app = VibeViewTUI(sample_archive)
    try:
        app.selected = next(i for i, entry in enumerate(app.entries) if entry[0] == "timings")
        # The terminal cannot show a plugin's HTML, so by its own rule (#38-39)
        # the list says skipped and the pane is the generic manifest dump,
        # which still names the browser's status.
        assert app.current == ("timings", KIND, "skipped")
        text = app._text_for_current()
    finally:
        app.reader.close()
    assert KIND in text and "rendered (via plugin)" in text


def test_doctor_lists_renderer_plugins(monkeypatch) -> None:
    from vibeview.onboarding import doctor_report

    _install(
        monkeypatch,
        _FakeEntryPoint(KIND, _spec(), _FakeDistribution("vibeview-example-renderer")),
        _FakeEntryPoint("x_example.broken", ImportError("gone")),
    )
    rows = {row["kind"]: row for row in doctor_report()["renderer_plugins"]}
    assert rows[KIND]["available"] and rows[KIND]["title"] == "Step Timings"
    assert rows["x_example.broken"]["error"] == "ImportError: gone"
    text = CliRunner().invoke(main, ["doctor"]).output
    assert "Renderer plugins:" in text
    assert f"{KIND}  ready  (vibeview-example-renderer)" in text
    assert "error: ImportError: gone" in text


def test_spec_is_frozen() -> None:
    spec = _spec()
    with pytest.raises(AttributeError):
        spec.kind = "x_other.kind"  # type: ignore[misc]
    assert replace(spec, title="Other").title == "Other"
