"""vibe-view#51 row #39-18: every CLI path that opens a QVFReader closes it.

The verifier's counterexamples at d7d1988: ``open`` and ``quickstart`` left
the reader open after ``serve()`` returned, and ``export`` left it open (with
a raw traceback) when the exporter or the output write failed.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.conftest import _water_structure_bytes, build_qvf
from vibeview import cli, recent
from vibeview.cli import main
from vibeview.qvf import QVFReader


def _water(tmp_path: Path, name: str = "w.qvf") -> Path:
    return build_qvf(
        tmp_path / name,
        [(
            "structure",
            "structure",
            {"structure": ("json", "sections/structure/structure.json", _water_structure_bytes())},
        )],
    )


@pytest.fixture
def closes(monkeypatch: pytest.MonkeyPatch) -> dict[int, int]:
    """Count close() calls per reader object (the verifier's spy)."""
    counts: dict[int, int] = {}
    real_close = QVFReader.close

    def spy(self: QVFReader) -> None:
        counts[id(self)] = counts.get(id(self), 0) + 1
        real_close(self)

    monkeypatch.setattr(QVFReader, "close", spy)
    return counts


@pytest.fixture
def fake_viewer(monkeypatch: pytest.MonkeyPatch):
    """Stub the trame app so ``open``/``quickstart`` reach serve() headless."""
    opened: list[Any] = []
    serve_calls: list[Any] = []
    behaviour: dict[str, BaseException | None] = {"raise": None}

    class _App:
        state = types.SimpleNamespace()
        controller = types.SimpleNamespace(
            toggle_compare_mode=lambda on: None, activate_section=lambda sid: None
        )

    def create_app(readers: list[Any]) -> _App:
        opened.extend(readers)
        return _App()

    def serve(app: Any, **kwargs: Any) -> None:
        serve_calls.append(kwargs)
        if behaviour["raise"] is not None:
            raise behaviour["raise"]

    monkeypatch.setattr(recent, "add_recent", lambda p: None)
    monkeypatch.setattr(cli, "_load_app", lambda: (create_app, serve))
    monkeypatch.setattr(cli, "_abort_if_port_in_use", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_announce_when_bound", lambda *a, **k: None)
    return types.SimpleNamespace(opened=opened, serve_calls=serve_calls, behaviour=behaviour)


def test_open_closes_its_reader_after_serve(tmp_path, closes, fake_viewer):
    qvf = _water(tmp_path)
    result = CliRunner().invoke(
        main, ["open", str(qvf), "--no-browser", "--log-file", str(tmp_path / "v.log")]
    )
    assert result.exit_code == 0, result.output
    assert fake_viewer.serve_calls, "serve() was never reached"
    assert len(fake_viewer.opened) == 1
    assert closes.get(id(fake_viewer.opened[0]), 0) == 1


def test_open_closes_its_readers_when_serve_raises(tmp_path, closes, fake_viewer):
    fake_viewer.behaviour["raise"] = KeyboardInterrupt()
    a, b = _water(tmp_path, "a.qvf"), _water(tmp_path, "b.qvf")
    CliRunner().invoke(
        main, ["open", str(a), str(b), "--no-browser", "--log-file", str(tmp_path / "v.log")]
    )
    assert len(fake_viewer.opened) == 2
    assert [closes.get(id(r), 0) for r in fake_viewer.opened] == [1, 1]


def test_quickstart_closes_the_demo_reader(tmp_path, monkeypatch, closes, fake_viewer):
    demo = _water(tmp_path, "template.qvf")

    def run_job(mol: Any, *, output: str, **kwargs: Any) -> None:
        Path(f"{output}.qvf").write_bytes(demo.read_bytes())

    fake = types.ModuleType("vibeqc")
    fake.Atom = lambda z, pos: (z, pos)  # type: ignore[attr-defined]
    fake.Molecule = lambda atoms: atoms  # type: ignore[attr-defined]
    fake.run_job = run_job  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vibeqc", fake)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    result = CliRunner().invoke(main, ["quickstart"])
    assert result.exit_code == 0, result.output
    assert fake_viewer.serve_calls
    assert len(fake_viewer.opened) == 1
    assert closes.get(id(fake_viewer.opened[0]), 0) == 1


def _export_reader_closes(closes: dict[int, int], monkeypatch) -> list[QVFReader]:
    """Record every reader the export command opens."""
    made: list[QVFReader] = []
    real_init = QVFReader.__init__

    def init(self: QVFReader, *a: Any, **k: Any) -> None:
        real_init(self, *a, **k)
        made.append(self)

    monkeypatch.setattr(QVFReader, "__init__", init)
    return made


def test_export_exporter_failure_is_clean_and_closes(tmp_path, monkeypatch, closes):
    import vibeview.export_cml as export_cml_mod

    def boom(reader: Any, out: str) -> None:
        raise RuntimeError("cml writer exploded")

    monkeypatch.setattr(export_cml_mod, "export_cml", boom)
    made = _export_reader_closes(closes, monkeypatch)
    qvf = _water(tmp_path)
    result = CliRunner().invoke(
        main, ["export", str(qvf), "-f", "cml", "-o", str(tmp_path / "w.cml")]
    )
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception
    assert "Traceback" not in result.output
    assert "cml writer exploded" in result.output
    assert len(made) == 1 and closes.get(id(made[0]), 0) == 1


def test_export_to_a_missing_directory_is_clean_and_closes(tmp_path, monkeypatch, closes):
    made = _export_reader_closes(closes, monkeypatch)
    qvf = _water(tmp_path)
    target = tmp_path / "missing" / "x.xyz"
    result = CliRunner().invoke(main, ["export", str(qvf), "-f", "xyz", "-o", str(target)])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception
    assert "Traceback" not in result.output
    assert "missing" in result.output
    assert not target.exists()
    assert len(made) == 1 and closes.get(id(made[0]), 0) == 1


def test_export_success_still_closes_once(tmp_path, monkeypatch, closes):
    made = _export_reader_closes(closes, monkeypatch)
    qvf = _water(tmp_path)
    out = tmp_path / "w.xyz"
    result = CliRunner().invoke(main, ["export", str(qvf), "-f", "xyz", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text().splitlines()[0].strip() == "3"
    assert len(made) == 1 and closes.get(id(made[0]), 0) == 1


# ── from-vq (re-verification FAIL at 69493b5, verify-vv-51c) ────────────────


@pytest.fixture
def fake_vq(tmp_path, monkeypatch):
    """A stand-in ``vq.fetch.fetch_local`` that 'fetches' a water archive."""
    source = _water(tmp_path, "src.qvf")

    def fetch_local(jid: str, output_dir: Path) -> Path:
        dst = Path(output_dir) / jid
        dst.mkdir(parents=True)
        (dst / f"{jid}.qvf").write_bytes(source.read_bytes())
        return dst

    vq_pkg = types.ModuleType("vq")
    vq_fetch = types.ModuleType("vq.fetch")
    vq_fetch.fetch_local = fetch_local  # type: ignore[attr-defined]
    vq_pkg.fetch = vq_fetch  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vq", vq_pkg)
    monkeypatch.setitem(sys.modules, "vq.fetch", vq_fetch)
    return tmp_path / "fetched"


def _from_vq(tmp_path: Path, out: Path):
    return CliRunner().invoke(
        main,
        ["from-vq", "job1", "-o", str(out), "--no-browser",
         "--log-file", str(tmp_path / "v.log")],
    )


@pytest.mark.parametrize("raised", [None, KeyboardInterrupt(), RuntimeError("boom")])
def test_from_vq_closes_its_readers_after_serve(tmp_path, monkeypatch, closes,
                                                fake_viewer, fake_vq, raised):
    made = _export_reader_closes(closes, monkeypatch)
    fake_viewer.behaviour["raise"] = raised
    _from_vq(tmp_path, fake_vq)
    assert fake_viewer.serve_calls, "serve() was never reached"
    assert len(made) == 1
    assert closes.get(id(made[0]), 0) == 1


def test_from_vq_closes_its_readers_when_the_port_is_busy(tmp_path, monkeypatch, closes,
                                                         fake_viewer, fake_vq):
    made = _export_reader_closes(closes, monkeypatch)

    def busy(*a: Any, **k: Any) -> None:
        raise SystemExit(1)

    monkeypatch.setattr(cli, "_abort_if_port_in_use", busy)
    result = _from_vq(tmp_path, fake_vq)
    assert result.exit_code == 1
    assert not fake_viewer.serve_calls
    assert len(made) == 1
    assert closes.get(id(made[0]), 0) == 1


# ── batch-compare and the export_from_qvf helpers (verify-vv-49-51d) ────────


def test_batch_compare_out_of_range_reference_opens_no_reader(tmp_path, monkeypatch, closes):
    # The index is checked against the files as given, before any opens (#67).
    made = _export_reader_closes(closes, monkeypatch)
    a, b = _water(tmp_path, "a.qvf"), _water(tmp_path, "b.qvf")
    result = CliRunner().invoke(main, ["batch-compare", str(a), str(b), "-r", "5"])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception
    assert made == []


def test_batch_compare_unopenable_reference_closes_every_reader(tmp_path, monkeypatch, closes):
    # The reference that does not open is found after a.qvf was opened (#67).
    made = _export_reader_closes(closes, monkeypatch)
    a = _water(tmp_path, "a.qvf")
    broken = tmp_path / "broken.qvf"
    broken.write_bytes(b"not a zip archive")
    result = CliRunner().invoke(main, ["batch-compare", str(a), str(broken), "-r", "1"])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception
    assert "Error: reference file" in result.stderr
    assert len(made) == 1
    assert [closes.get(id(r), 0) for r in made] == [1]


def test_compare_batch_body_exception_closes_every_reader(tmp_path, monkeypatch, closes):
    from vibeview.batch_compare import compare_batch

    made = _export_reader_closes(closes, monkeypatch)

    def broken(self: QVFReader) -> Any:
        raise RuntimeError("section table exploded")

    monkeypatch.setattr(QVFReader, "sections", property(broken))
    a, b = _water(tmp_path, "a.qvf"), _water(tmp_path, "b.qvf")
    with pytest.raises(RuntimeError, match="section table exploded"):
        compare_batch([str(a), str(b)])
    assert len(made) == 2
    assert [closes.get(id(r), 0) for r in made] == [1, 1]


@pytest.mark.parametrize(
    ("module", "exporter"),
    [("vibeview.blender_export", "export_blender_script"),
     ("vibeview.povray_export", "export_povray")],
)
@pytest.mark.parametrize("fails", [False, True])
def test_export_from_qvf_closes_its_reader(tmp_path, monkeypatch, closes, module, exporter, fails):
    import importlib

    mod = importlib.import_module(module)
    made = _export_reader_closes(closes, monkeypatch)
    if fails:
        def boom(*a: Any, **k: Any) -> None:
            raise RuntimeError("exporter exploded")

        monkeypatch.setattr(mod, exporter, boom)
    qvf = _water(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    if fails:
        with pytest.raises(RuntimeError, match="exporter exploded"):
            mod.export_from_qvf(str(qvf), output_dir=str(out_dir))
    else:
        assert Path(mod.export_from_qvf(str(qvf), output_dir=str(out_dir))).is_file()
    assert len(made) == 1
    assert closes.get(id(made[0]), 0) == 1
