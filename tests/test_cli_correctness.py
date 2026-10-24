"""CLI and API correctness follow-ups to the #39 audit, tracked in #51.

Each test names the original #39 finding id so the verifier's matrix row
traces to it. Rendering is stubbed where the finding is about what the
command does with the result, not about the pixels.
"""

from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from click.testing import CliRunner

from tests.conftest import _water_structure_bytes, build_qvf
from vibeview import cli
from vibeview.cli import main


def _water(tmp_path: Path, name: str = "w.qvf", extra_sections=(), provenance=None) -> Path:
    sections = [
        (
            "structure",
            "structure",
            {"structure": ("json", "sections/structure/structure.json", _water_structure_bytes())},
        ),
        *extra_sections,
    ]
    path = build_qvf(tmp_path / name, sections)
    if provenance is not None:
        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            files = {n: zf.read(n) for n in zf.namelist() if n != "manifest.json"}
        manifest["provenance"] = provenance
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            for n, b in files.items():
                zf.writestr(n, b)
    return path


def _volume(sid: str, kind: str, values: np.ndarray) -> tuple[str, str, dict]:
    grid = json.dumps(
        {"origin": [-2.0, -2.0, -2.0], "voxel_vectors": (np.eye(3) * 0.5).tolist(),
         "shape": list(values.shape)}
    ).encode()
    return (
        sid,
        kind,
        {
            "grid": ("json", f"sections/{sid}/grid.json", grid),
            "data": ("binary", f"sections/{sid}/data.bin", values.astype(np.float32).tobytes(),
                     "float32", list(values.shape)),
        },
    )


# ── #39-3 / #39-4: animation outputs ────────────────────────────────────────


def _frame_dir(tmp_path: Path, n: int) -> Path:
    from PIL import Image

    d = tmp_path / "frames"
    d.mkdir()
    for i in range(n):
        Image.new("RGB", (4, 4), (i * 40, 0, 0)).save(d / f"frame_{i:04d}.png")
    return d


def test_frames_output_keeps_a_dotted_stem(tmp_path: Path) -> None:
    """#39-4: ``job.opt_trajectory`` lost ``.opt_trajectory`` to with_suffix("")."""
    from vibeview.animation import _encode_frames

    out = _encode_frames(_frame_dir(tmp_path, 2), tmp_path / "job.opt_trajectory", "frames", 5)
    assert out == tmp_path / "job.opt_trajectory"
    assert len(list(out.glob("frame_*.png"))) == 2


def test_gif_output_keeps_a_dotted_stem(tmp_path: Path) -> None:
    from vibeview.animation import _encode_frames

    out = _encode_frames(_frame_dir(tmp_path, 2), tmp_path / "job.opt_trajectory", "gif", 5)
    assert out == tmp_path / "job.opt_trajectory.gif"
    assert out.is_file()


@pytest.mark.parametrize("fmt", ["gif", "frames"])
def test_nothing_written_raises_instead_of_returning_a_missing_path(
    tmp_path: Path, fmt: str
) -> None:
    """#39-3: zero frames returned ``job.gif`` that did not exist."""
    from vibeview.animation import AnimationEncodeError, _encode_frames

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(AnimationEncodeError):
        _encode_frames(empty, tmp_path / "job.gif", fmt, 5)


def test_animate_reports_an_encode_failure_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#39-3: the CLI called ``.stat()`` on the missing path."""
    from vibeview import animation

    def fail(*_a: Any, **_k: Any) -> Path:
        raise animation.AnimationEncodeError("could not write x.gif")

    monkeypatch.setattr(animation, "render_turntable", fail)
    monkeypatch.setattr("vibeview.headless.add_structure", lambda *_a, **_k: True)
    monkeypatch.setattr("vibeview.headless.frame", lambda *_a, **_k: None)
    qvf = _water(tmp_path)
    result = CliRunner().invoke(main, ["animate", str(qvf), "-k", "turntable", "-f", "gif"])
    assert result.exit_code == 1
    assert "could not write" in result.output
    assert not isinstance(result.exception, FileNotFoundError)


# ── #39-9: animate kinds ────────────────────────────────────────────────────


def test_animate_auto_falls_back_to_a_turntable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#39-9: a structure-only file said "No animatable section found"."""
    calls: list[str] = []

    def fake(reader: Any, kind: str, out: Path, *_a: Any, **_kw: Any) -> Path:
        calls.append(kind)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr(cli, "_render_animation", fake)
    qvf = _water(tmp_path)
    result = CliRunner().invoke(main, ["animate", str(qvf), "-o", str(tmp_path / "t.gif")])
    assert result.exit_code == 0, result.output
    assert calls == ["turntable"]


def test_animate_unknown_kind_message(tmp_path: Path) -> None:
    """#39-9: the "Unknown kind" message omitted ``reaction``."""
    result = CliRunner().invoke(main, ["animate", str(_water(tmp_path)), "-k", "bogus"])
    assert result.exit_code == 1
    for kind in ("trajectory", "reaction", "vibration", "orbital", "turntable"):
        assert kind in result.output


# ── #39-6 / #39-25: volume defaults and one volume render ───────────────────


@pytest.mark.parametrize(
    ("kind", "isovalue", "colormap"),
    [("volume.spin", 0.005, "RdBu"), ("volume.elf", 0.5, "plasma"),
     ("volume.density", 0.05, "viridis")],
)
def test_capture_uses_the_viewer_defaults_for_the_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, isovalue: float, colormap: str
) -> None:
    """#39-6: spin captured at 0.05/coolwarm, elf at 0.05/viridis."""
    seen: dict[str, Any] = {}

    def fake(reader: Any, section: str, out: Path, **kw: Any) -> bool:
        seen.update(kw)
        return True

    monkeypatch.setattr("vibeview.capture.capture_volume", fake)
    qvf = _water(tmp_path, extra_sections=[_volume("v", kind, np.zeros((3, 3, 3)))])
    result = CliRunner().invoke(
        main, ["capture", str(qvf), "-s", "v", "-o", str(tmp_path / "v.png")]
    )
    assert result.exit_code == 0, result.output
    assert (seen["isovalue"], seen["colormap"]) == (isovalue, colormap)


def test_capture_flags_still_override_the_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        "vibeview.capture.capture_volume", lambda *_a, **kw: seen.update(kw) or True
    )
    qvf = _water(tmp_path, extra_sections=[_volume("v", "volume.spin", np.zeros((3, 3, 3)))])
    result = CliRunner().invoke(
        main,
        ["capture", str(qvf), "-s", "v", "--isovalue", "0.2", "--colormap", "gray",
         "-o", str(tmp_path / "v.png")],
    )
    assert result.exit_code == 0, result.output
    assert (seen["isovalue"], seen["colormap"]) == (0.2, "gray")


def test_batch_volume_render_is_the_capture_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#39-6 / #39-25: batch kept a third defaults table and its own
    renderer with no signed lobes, replication or PBC clamp."""
    from vibeview import batch

    seen: list[tuple[str, dict]] = []

    def fake(reader: Any, sid: str, path: Any, **kw: Any) -> bool:
        seen.append((sid, kw))
        return True

    monkeypatch.setattr("vibeview.capture.capture_volume", fake)
    monkeypatch.setattr(batch, "render_structure_png", lambda *_a, **_k: True)
    qvf = _water(tmp_path, extra_sections=[_volume("v", "volume.orbital", np.zeros((3, 3, 3)))])
    from vibeview.qvf import QVFReader

    with QVFReader(qvf) as reader:
        batch.render_all_png(reader, tmp_path, include_volumes=True)
    assert seen and seen[0][0] == "v"
    assert (seen[0][1]["isovalue"], seen[0][1]["colormap"]) == (0.05, "coolwarm")


# ── #39-7: batch-compare errors ─────────────────────────────────────────────


def test_batch_compare_out_of_range_reference_is_a_clean_error(tmp_path: Path) -> None:
    """#39-7: ``-r 5`` escaped as an uncaught ValueError."""
    a, b = _water(tmp_path, "a.qvf"), _water(tmp_path, "b.qvf")
    result = CliRunner().invoke(main, ["batch-compare", str(a), str(b), "-r", "5"])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "out of range" in result.output


def test_batch_compare_arity_error_goes_to_stderr(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["batch-compare", str(_water(tmp_path))])
    assert result.exit_code == 1
    assert "Need at least 2" in result.stderr
    assert "Need at least 2" not in result.stdout


# ── #39-11: [display] config is read ────────────────────────────────────────


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    cfg = tmp_path / "xdg"
    (cfg / "vibe-view").mkdir(parents=True)
    (cfg / "vibe-view" / "config.toml").write_text(text)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))


def test_capture_reads_display_size_and_representation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#39-11: ``[display]`` was written by ``config --init``, documented,
    and never read."""
    _config(tmp_path, monkeypatch,
            '[display]\nsize = "320x200"\nrepresentation = "space_filling"\n')
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        "vibeview.capture.capture_structure", lambda *_a, **kw: seen.update(kw) or True
    )
    result = CliRunner().invoke(main, ["capture", str(_water(tmp_path)),
                                       "-o", str(tmp_path / "s.png")])
    assert result.exit_code == 0, result.output
    assert seen == {"size": (320, 200), "representation": "space_filling", "publication": False}


def test_explicit_size_beats_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config(tmp_path, monkeypatch, '[display]\nsize = "320x200"\n')
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        "vibeview.capture.capture_structure", lambda *_a, **kw: seen.update(kw) or True
    )
    result = CliRunner().invoke(main, ["capture", str(_water(tmp_path)), "--size", "64x48",
                                       "-o", str(tmp_path / "s.png")])
    assert result.exit_code == 0, result.output
    assert seen["size"] == (64, 48)


def test_batch_reads_display_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _config(tmp_path, monkeypatch, '[display]\nsize = "320x200"\n')
    seen: list[dict] = []
    monkeypatch.setattr(
        "vibeview.batch.render_structure_png", lambda *_a, **kw: seen.append(kw) or True
    )
    result = CliRunner().invoke(main, ["batch", str(_water(tmp_path)),
                                       "-o", str(tmp_path / "g")])
    assert result.exit_code == 0, result.output
    assert seen[0]["size"] == (320, 200)


# ── #39-14: geometry edits say what they leave behind ──────────────────────


def test_h_add_names_the_sections_and_provenance_it_drops(tmp_path: Path) -> None:
    """#39-14: h-add silently dropped every other section and the provenance."""
    notes = ("notes", "x_demo.notes", {"payload": ("json", "sections/notes/n.json", b"{}")})
    qvf = _water(tmp_path, extra_sections=[notes],
                 provenance={"method": "rhf", "basis": "sto-3g"})
    result = CliRunner().invoke(main, ["h-add", str(qvf), "-o", str(tmp_path / "h.qvf")])
    assert result.exit_code == 0, result.output
    assert "notes" in result.stderr and "provenance" in result.stderr


# ── #39-15: HTTP caching patch ──────────────────────────────────────────────


def test_disable_http_caching_is_idempotent_and_not_in_the_cli() -> None:
    """#39-15: each call wrapped WebAppServer.__init__ again, and app.py
    imported the helper from the CLI module."""
    pytest.importorskip("wslink")
    import wslink.backends.aiohttp as backend

    from vibeview.netutil import disable_http_caching

    original = backend.WebAppServer.__init__
    try:
        disable_http_caching()
        first = backend.WebAppServer.__init__
        disable_http_caching()
        assert backend.WebAppServer.__init__ is first
    finally:
        backend.WebAppServer.__init__ = original
    assert not hasattr(cli, "_disable_http_caching")
    from tests._sources import app_source as _app_source

    app_source = _app_source()
    assert "from vibeview.cli import" not in app_source


# ── #39-16: stats ───────────────────────────────────────────────────────────


def test_stats_spacing_and_denominator(tmp_path: Path) -> None:
    """#39-16: "Total size:1005 B" and "Converged: 1/2" with one unreadable file."""
    d = tmp_path / "runs"
    d.mkdir()
    _water(d, "ok.qvf", provenance={"method": "rhf", "scf_converged": True})
    (d / "bad.qvf").write_bytes(b"not a zip")
    result = CliRunner().invoke(main, ["stats", str(d)])
    assert result.exit_code == 0, result.output
    assert "Total size: " in result.output
    assert "Converged:     1/1" in result.output
    assert "Unreadable: 1 (bad.qvf)" in result.output


# ── #39-18: readers are closed ──────────────────────────────────────────────


@pytest.fixture
def tracked_readers(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from vibeview import qvf

    opened: list[Any] = []
    real_init = qvf.QVFReader.__init__

    def init(self: Any, *a: Any, **k: Any) -> None:
        real_init(self, *a, **k)
        opened.append(self)

    monkeypatch.setattr(qvf.QVFReader, "__init__", init)
    return opened


def _all_closed(readers: list[Any]) -> bool:
    return all(r._zf.fp is None for r in readers)


def test_tui_show_closes_its_reader(tmp_path: Path, tracked_readers: list[Any]) -> None:
    """#39-18: ``tui.show.show()`` and ``show_all()`` never closed."""
    from vibeview.tui.show import show, show_all

    qvf = _water(tmp_path)
    show(qvf, size=(40, 12), plain=True)
    show_all(qvf, size=(40, 12), plain=True)
    assert len(tracked_readers) == 2 and _all_closed(tracked_readers)


def test_table_closes_on_its_early_return(tmp_path: Path, tracked_readers: list[Any]) -> None:
    result = CliRunner().invoke(main, ["table", str(_water(tmp_path))])
    assert result.exit_code == 0, result.output
    assert tracked_readers and _all_closed(tracked_readers)


def test_serve_detail_page_closes_on_error(
    tmp_path: Path, tracked_readers: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#39-18: the file-browser pages closed only on success."""
    from vibeview import serve
    from vibeview.qvf import QVFReader

    monkeypatch.setattr(QVFReader, "sections", property(lambda self: 1 / 0))
    qvf = _water(tmp_path)
    serve._view_page(qvf.name, qvf.parent)
    assert tracked_readers and _all_closed(tracked_readers)


# ── #39-20: a killed optimisation worker is reaped ──────────────────────────


def test_live_opt_reaps_the_worker_it_kills() -> None:
    """#39-20: ``proc.kill()`` without a wait left a zombie."""
    import sys

    from vibeview.live_opt import LiveOptService

    async def run() -> int | None:
        controller = LiveOptService.__new__(LiveOptService)
        controller._task = None
        controller._reaper = None
        controller._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(30)"
        )
        proc = controller._proc
        controller._kill_proc()
        assert controller._reaper is not None
        await asyncio.wait_for(controller._reaper, 10)
        return proc.returncode

    assert asyncio.run(run()) is not None


# ── #39-35: every command reports a malformed manifest cleanly ─────────────


@pytest.mark.parametrize("command", ["info", "table", "export"])
def test_manifest_that_is_not_an_object_is_a_clean_error(tmp_path: Path, command: str) -> None:
    """#39-35: a manifest ``[]`` escaped as AttributeError from info/table/export."""
    path = tmp_path / "bad.qvf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", "[]")
    result = CliRunner().invoke(main, [command, str(path)])
    assert result.exit_code == 1
    assert not isinstance(result.exception, AttributeError)
    assert "manifest.json must be a JSON object" in result.output


# ── #39-36 / #39-46: compare is open --auto-compare ────────────────────────


def test_compare_is_open_with_auto_compare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#39-36 / #39-46: compare skipped the recent list and had no ``-s``."""
    import vibeview.recent as recent

    added: list[Path] = []
    opened: list[Any] = []

    class _State:
        compare_mode = False
        selected_section = None
        status_message = ""

    class _Controller:
        # open drives the controllers the UI would (#37-24), not bare flags.
        def toggle_compare_mode(self, on: bool) -> None:
            _App.state.compare_mode = on

        def activate_section(self, section_id: str) -> None:
            _App.state.selected_section = section_id

        def open_view(self, section: str | None = None, **_: Any) -> list[str]:
            if section is not None:
                self.activate_section(section)
            return []

    class _App:
        state = _State()
        controller = _Controller()

    def create_app(readers: list[Any]) -> _App:
        opened.extend(readers)
        return _App()

    monkeypatch.setattr(recent, "add_recent", lambda p: added.append(Path(p)))
    monkeypatch.setattr(cli, "_load_app", lambda: (create_app, lambda *a, **k: None))
    monkeypatch.setattr(cli, "_abort_if_port_in_use", lambda *a: None)
    monkeypatch.setattr(cli, "_announce_when_bound", lambda *a, **k: None)
    a, b = _water(tmp_path, "a.qvf"), _water(tmp_path, "b.qvf")
    try:
        result = CliRunner().invoke(
            main,
            ["compare", str(a), str(b), "-s", "structure", "--no-browser",
             "--log-file", str(tmp_path / "v.log")],
        )
        assert result.exit_code == 0, result.output
        assert [p.name for p in added] == ["a.qvf", "b.qvf"]
        assert _App.state.compare_mode is True
        assert _App.state.selected_section == "structure"
    finally:
        for r in opened:
            r.close()


# ── #39-45: capture --help names its defaults ───────────────────────────────


def test_capture_help_names_the_defaults() -> None:
    """#39-45: ``--isovalue`` and ``--colormap`` showed no default."""
    text = " ".join(CliRunner().invoke(main, ["capture", "--help"]).output.split())
    assert "default: <stem>_<section>.png" in text
    assert "Isovalue for volume sections (default: the section's viewer default" in text
    assert "Colormap for volume sections (default: the section's viewer default)" in text
