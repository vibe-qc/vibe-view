"""The Sketch entry point and the vibe-sketch hand-off contract (#77).

vibe-sketch is not installed in the test environment, and it would not help:
its *Send to vibe-view* is the other half of the contract. A stand-in
``vibesketch`` package, put on ``PYTHONPATH``, plays the sketcher: it records
its command line, writes a structure and a hand-off record the way the
contract says, and exits when the test releases it. Everything on the
viewer's side runs for real, including the child process.
"""

from __future__ import annotations

import json
import signal
import sys
import textwrap
import time
from pathlib import Path

import pytest

import vibeview.sketch as sketch
from vibeview.converters import xyz_to_qvf

WATER_XYZ = "3\nwater\nO 0 0 0\nH 0 0.757 0.587\nH 0 -0.757 0.587\n"

_FAKE_MAIN = textwrap.dedent(
    """
    import json, os, sys, time
    from pathlib import Path

    out = Path(os.environ["FAKE_SKETCH_OUT"])
    (out / "argv.json").write_text(json.dumps(sys.argv[1:]))
    handoff = Path(os.environ["VIBEVIEW_SKETCH_HANDOFF"])
    (out / "handoff_dir").write_text(str(handoff))
    (out / "return_dir").write_text(os.environ["VIBEVIEW_SKETCH_RETURN"])
    if os.environ.get("FAKE_SKETCH_SEND") == "1":
        structure = out / "water.xyz"
        structure.write_text(os.environ["FAKE_SKETCH_XYZ"])
        record = {"schema": "vibe-view-sketch-handoff/1", "path": str(structure)}
        partial = handoff / "0001.json.partial"
        partial.write_text(json.dumps(record))
        os.replace(partial, handoff / "0001.json")
    (out / "ready").touch()
    deadline = time.monotonic() + 60
    while not (out / "release").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    print("fake sketcher: the last line of its log")
    sys.exit(int(os.environ.get("FAKE_SKETCH_EXIT", "0")))
    """
)


def _wait_for(path: Path, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"the fake sketcher never wrote {path.name}"
        time.sleep(0.02)


def _wait_for_exit(session: sketch.SketchSession, timeout: float = 30.0) -> None:
    session.process.wait(timeout=timeout)


@pytest.fixture(autouse=True)
def _no_process_wide_hooks(monkeypatch):
    """Keep the SIGTERM and atexit hook out of the test process; one test
    exercises it directly. Stop any sketcher a failing test left running."""
    original = sketch._install_exit_hook
    monkeypatch.setattr(sketch, "_install_exit_hook", lambda: None)
    yield original
    sketch._close_live_sessions()


@pytest.fixture
def fake_sketcher(tmp_path, monkeypatch):
    """A ``vibesketch`` package that importlib and the child both find."""
    site = tmp_path / "fake-site"
    package = site / "vibesketch"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text(_FAKE_MAIN)
    out = tmp_path / "sketcher-out"
    out.mkdir()
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setenv("PYTHONPATH", str(site))
    monkeypatch.setenv("FAKE_SKETCH_OUT", str(out))
    monkeypatch.setenv("FAKE_SKETCH_XYZ", WATER_XYZ)
    monkeypatch.setenv("FAKE_SKETCH_SEND", "1")
    return out


def _record(directory: Path, name: str, **fields) -> Path:
    record = directory / name
    record.write_text(json.dumps(fields))
    return record


# ── the record ────────────────────────────────────────────────────────────


class TestHandoffRecord:
    def test_names_an_xyz_file(self, tmp_path):
        structure = tmp_path / "water.xyz"
        structure.write_text(WATER_XYZ)
        record = _record(tmp_path, "a.json", schema=sketch.HANDOFF_SCHEMA, path=str(structure))
        assert sketch.read_handoff_record(record) == structure

    def test_names_a_qvf_file(self, tmp_path):
        structure = tmp_path / "water.qvf"
        structure.write_bytes(xyz_to_qvf(WATER_XYZ.encode()).getvalue())
        record = _record(tmp_path, "a.json", schema=sketch.HANDOFF_SCHEMA, path=str(structure))
        assert sketch.read_handoff_record(record) == structure

    @pytest.mark.parametrize(
        ("fields", "reason"),
        [
            ({"schema": "vibe-view-sketch-handoff/2", "path": "{xyz}"}, "unsupported hand-off"),
            ({"path": "{xyz}"}, "unsupported hand-off"),
            ({"schema": sketch.HANDOFF_SCHEMA}, "names no structure path"),
            ({"schema": sketch.HANDOFF_SCHEMA, "path": ""}, "names no structure path"),
            ({"schema": sketch.HANDOFF_SCHEMA, "path": 7}, "names no structure path"),
            ({"schema": sketch.HANDOFF_SCHEMA, "path": "water.xyz"}, "not absolute"),
            ({"schema": sketch.HANDOFF_SCHEMA, "path": "{sdf}"}, "neither a QVF nor an XYZ"),
            ({"schema": sketch.HANDOFF_SCHEMA, "path": "{missing}"}, "does not exist"),
        ],
    )
    def test_refuses_what_breaks_the_contract(self, tmp_path, fields, reason):
        (tmp_path / "water.xyz").write_text(WATER_XYZ)
        (tmp_path / "water.sdf").write_text("")
        paths = {
            "xyz": tmp_path / "water.xyz",
            "sdf": tmp_path / "water.sdf",
            "missing": tmp_path / "gone.xyz",
        }
        fields = {
            key: value.format(**paths) if isinstance(value, str) else value
            for key, value in fields.items()
        }
        record = _record(tmp_path, "bad.json", **fields)
        with pytest.raises(sketch.SketchHandoffError, match=reason) as caught:
            sketch.read_handoff_record(record)
        assert "bad.json" in str(caught.value)

    @pytest.mark.parametrize("text", ["{not json", "[1, 2]", ""])
    def test_refuses_a_record_that_is_not_a_json_object(self, tmp_path, text):
        record = tmp_path / "bad.json"
        record.write_text(text)
        with pytest.raises(sketch.SketchHandoffError, match="bad.json"):
            sketch.read_handoff_record(record)


class TestLoadSketchedStructure:
    def test_xyz_becomes_a_reader_named_after_the_file(self, tmp_path):
        structure = tmp_path / "water.xyz"
        structure.write_text(WATER_XYZ)
        reader = sketch.load_sketched_structure(structure)
        assert [a.symbol for a in reader.read_structure().atoms] == ["O", "H", "H"]
        assert reader.source_path == structure.resolve()

    def test_qvf_is_read_into_memory(self, tmp_path):
        """A later send to the same path must not change what is open."""
        structure = tmp_path / "sketch.qvf"
        structure.write_bytes(xyz_to_qvf(WATER_XYZ.encode()).getvalue())
        reader = sketch.load_sketched_structure(structure)
        assert reader.path is None
        structure.write_bytes(xyz_to_qvf(b"1\nhelium\nHe 0 0 0\n").getvalue())
        assert len(reader.read_structure().atoms) == 3
        assert reader.source_path == structure.resolve()


# ── the session ───────────────────────────────────────────────────────────


class TestSketchSession:
    def test_launch_hands_the_sketcher_its_directory_and_port(self, fake_sketcher):
        session = sketch.SketchSession.launch()
        try:
            _wait_for(fake_sketcher / "ready")
            argv = json.loads((fake_sketcher / "argv.json").read_text())
            port = session.url.rstrip("/").rsplit(":", 1)[1]
            assert argv == ["serve", "--host", "127.0.0.1", "--port", port, "--open"]
            assert session.url == f"http://127.0.0.1:{port}/"
            handoff = Path((fake_sketcher / "handoff_dir").read_text())
            assert handoff == session.handoff_dir
            returned = Path((fake_sketcher / "return_dir").read_text())
            assert returned == handoff.parent / "return"
            assert returned.is_dir()
            assert session.running
        finally:
            session.close()

    def test_open_browser_false_leaves_out_the_flag(self, fake_sketcher):
        session = sketch.SketchSession.launch(open_browser=False)
        try:
            _wait_for(fake_sketcher / "ready")
            assert "--open" not in json.loads((fake_sketcher / "argv.json").read_text())
        finally:
            session.close()

    def test_receive_returns_the_path_and_deletes_the_record(self, fake_sketcher):
        session = sketch.SketchSession.launch()
        try:
            _wait_for(fake_sketcher / "ready")
            assert session.receive() == [fake_sketcher / "water.xyz"]
            assert session.pending_records() == []
            assert session.receive() == []
        finally:
            session.close()

    def test_a_refused_record_is_reported_once(self, fake_sketcher, monkeypatch):
        monkeypatch.setenv("FAKE_SKETCH_SEND", "0")
        session = sketch.SketchSession.launch()
        try:
            _wait_for(fake_sketcher / "ready")
            _record(session.handoff_dir, "0001.json", schema="other/1", path="/x.xyz")
            (received,) = session.receive()
            assert isinstance(received, sketch.SketchHandoffError)
            assert session.receive() == []
        finally:
            session.close()

    def test_a_record_still_being_written_is_not_read(self, fake_sketcher, monkeypatch):
        monkeypatch.setenv("FAKE_SKETCH_SEND", "0")
        session = sketch.SketchSession.launch()
        try:
            _wait_for(fake_sketcher / "ready")
            (session.handoff_dir / "0001.json.partial").write_text("{")
            assert session.receive() == []
        finally:
            session.close()

    def test_close_stops_the_sketcher_and_removes_the_directory(self, fake_sketcher):
        session = sketch.SketchSession.launch()
        _wait_for(fake_sketcher / "ready")
        handoff = session.handoff_dir
        session.close()
        assert session.process.poll() is not None
        assert not handoff.exists()
        assert not session.running
        session.close()  # idempotent

    def test_a_clean_exit_reads_as_closed(self, fake_sketcher):
        session = sketch.SketchSession.launch()
        try:
            _wait_for(fake_sketcher / "ready")
            (fake_sketcher / "release").touch()
            _wait_for_exit(session)
            assert not session.running
            assert session.exit_message() == "vibe-sketch closed"
        finally:
            session.close()

    def test_a_failed_exit_quotes_the_last_log_line(self, fake_sketcher, monkeypatch):
        monkeypatch.setenv("FAKE_SKETCH_EXIT", "3")
        session = sketch.SketchSession.launch()
        try:
            _wait_for(fake_sketcher / "ready")
            (fake_sketcher / "release").touch()
            _wait_for_exit(session)
            assert session.exit_message() == (
                "vibe-sketch exited with status 3: fake sketcher: the last line of its log"
            )
        finally:
            session.close()

    def test_a_launch_that_cannot_start_leaves_nothing_behind(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sketch.tempfile, "tempdir", str(tmp_path))
        with pytest.raises(OSError):
            sketch.SketchSession.launch(python=str(tmp_path / "no-such-python"))
        assert list(tmp_path.iterdir()) == []

    def test_exit_hook_routes_sigterm_and_stops_sessions(self, monkeypatch, _no_process_wide_hooks):
        install = _no_process_wide_hooks
        registered: list = []
        handlers: dict = {}
        monkeypatch.setattr(sketch, "_EXIT_HOOK_INSTALLED", False)
        monkeypatch.setattr(sketch.atexit, "register", registered.append)
        monkeypatch.setattr(sketch.signal, "getsignal", lambda signum: signal.SIG_DFL)
        monkeypatch.setattr(
            sketch.signal, "signal", lambda signum, handler: handlers.__setitem__(signum, handler)
        )
        install()
        install()  # once per process
        assert registered == [sketch._close_live_sessions]
        assert set(handlers) == {signal.SIGTERM}
        with pytest.raises(KeyboardInterrupt):
            handlers[signal.SIGTERM](signal.SIGTERM, None)

    def test_exit_hook_leaves_someone_elses_sigterm_handler(
        self, monkeypatch, _no_process_wide_hooks
    ):
        install = _no_process_wide_hooks
        handlers: dict = {}
        monkeypatch.setattr(sketch, "_EXIT_HOOK_INSTALLED", False)
        monkeypatch.setattr(sketch.atexit, "register", lambda func: None)
        monkeypatch.setattr(sketch.signal, "getsignal", lambda signum: signal.SIG_IGN)
        monkeypatch.setattr(
            sketch.signal, "signal", lambda signum, handler: handlers.__setitem__(signum, handler)
        )
        install()
        assert handlers == {}


def test_free_port_skips_a_taken_default(monkeypatch):
    taken = {sketch.DEFAULT_PORT, sketch.DEFAULT_PORT + 1}
    monkeypatch.setattr(sketch, "_port_free", lambda port: port not in taken)
    assert sketch.free_port() == sketch.DEFAULT_PORT + 2


def test_free_port_falls_back_to_any_free_port(monkeypatch):
    monkeypatch.setattr(sketch, "_port_free", lambda port: False)
    port = sketch.free_port()
    assert port > 0
    assert not sketch.DEFAULT_PORT <= port < sketch.DEFAULT_PORT + sketch.PORT_TRIES


# ── the viewer ────────────────────────────────────────────────────────────


def _app(tmp_path):
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    seed = tmp_path / "seed.qvf"
    seed.write_bytes(xyz_to_qvf(b"2\nc\nO 0 0 0\nH 0 0 1\n").getvalue())
    server = create_app(QVFReader(seed))
    server.controller.view_update = lambda *a, **k: None
    return server


@pytest.fixture
def captured_poll(monkeypatch):
    """Capture the poll coroutine instead of scheduling it, and make each
    ``send(None)`` one poll: a zero-second sleep yields exactly once."""
    import vibeview.app.files as files_mod

    monkeypatch.setattr(files_mod, "SKETCH_POLL_SECONDS", 0)
    scheduled: list = []
    monkeypatch.setattr(
        files_mod.asyncio, "ensure_future", lambda coroutine: scheduled.append(coroutine)
    )
    yield scheduled
    for coroutine in scheduled:
        coroutine.close()


def _step(coroutine) -> bool:
    """Run one poll; False once the loop has returned."""
    try:
        coroutine.send(None)
    except StopIteration:
        return False
    return True


class TestViewerWithoutTheExtra:
    """Force the absent branch rather than skipping (AGENTS.md)."""

    def test_the_button_reports_the_install_command(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "vibesketch", None)
        app = _app(tmp_path)
        assert app.state.sketch_available is False
        before = list(app.state.file_names)
        app.controller.sketch_molecule()
        assert app.state.file_names == before
        assert "[sketch]" in app.state.status_message, app.state.status_message
        assert "pip install" in app.state.status_message, app.state.status_message
        assert "<path-to-vibe-sketch>" in app.state.status_message

    def test_the_palette_action_reports_it_too(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "vibesketch", None)
        app = _app(tmp_path)
        app.controller.run_palette_action("sketch")
        assert "[sketch]" in app.state.status_message, app.state.status_message


class TestViewerWithTheExtra:
    def test_a_sent_structure_opens_as_a_new_file(self, tmp_path, fake_sketcher, captured_poll):
        app = _app(tmp_path)
        assert app.state.sketch_available is True
        before = len(app.state.file_names)
        app.state.builder_dialog = True

        app.controller.sketch_molecule()

        assert app.state.sketch_running is True
        assert app.state.builder_dialog is False
        assert "Opening vibe-sketch at http://127.0.0.1:" in app.state.status_message
        (poll,) = captured_poll
        assert _step(poll)  # up to the first sleep
        _wait_for(fake_sketcher / "ready")
        handoff = Path((fake_sketcher / "handoff_dir").read_text())

        assert _step(poll)  # the poll that finds the record
        assert app.state.status_message == "Received water.xyz (3 atoms) from vibe-sketch"
        assert len(app.state.file_names) == before + 1
        assert app.state.file_names[-1] == "water"
        assert app.state.active_file_idx == before
        assert list(handoff.glob("*.json")) == []

        (fake_sketcher / "release").touch()
        deadline = time.monotonic() + 30
        while _step(poll):
            assert time.monotonic() < deadline, "the poll loop never noticed the exit"
            time.sleep(0.02)
        assert app.state.status_message == "vibe-sketch closed"
        assert app.state.sketch_running is False
        assert not handoff.exists()

    def test_a_second_click_reopens_the_running_sketcher(
        self, tmp_path, fake_sketcher, captured_poll, monkeypatch
    ):
        opened: list[str] = []
        monkeypatch.setattr(sketch.webbrowser, "open", opened.append)
        app = _app(tmp_path)
        app.controller.sketch_molecule()
        _wait_for(fake_sketcher / "ready")
        app.controller.sketch_molecule()
        assert len(captured_poll) == 1, "a second click must not start a second sketcher"
        assert opened and opened[0].startswith("http://127.0.0.1:")
        assert "vibe-sketch is open at" in app.state.status_message

    def test_an_unreadable_structure_is_reported_and_adds_nothing(
        self, tmp_path, fake_sketcher, captured_poll, monkeypatch
    ):
        monkeypatch.setenv("FAKE_SKETCH_XYZ", "not an xyz file\n")
        app = _app(tmp_path)
        before = list(app.state.file_names)
        app.controller.sketch_molecule()
        (poll,) = captured_poll
        assert _step(poll)
        _wait_for(fake_sketcher / "ready")
        assert _step(poll)
        assert app.state.file_names == before
        assert app.state.status_message.startswith("Could not open from vibe-sketch: water.xyz")
        assert app.state.sketch_running is True  # the sketcher is still usable

    def test_a_failed_poll_is_reported_and_polling_continues(
        self, tmp_path, fake_sketcher, captured_poll, monkeypatch
    ):
        from vibeview.app.files import FilesControllers

        real_poll = FilesControllers.poll_sketch
        calls: list[int] = []

        def flaky_poll(self):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("render failed")
            real_poll(self)

        monkeypatch.setattr(FilesControllers, "poll_sketch", flaky_poll)
        app = _app(tmp_path)
        before = len(app.state.file_names)
        app.controller.sketch_molecule()
        (poll,) = captured_poll
        assert _step(poll)
        _wait_for(fake_sketcher / "ready")
        assert _step(poll)
        assert app.state.status_message == (
            "Could not show the structure from vibe-sketch: render failed"
        )
        assert _step(poll)
        assert len(app.state.file_names) == before + 1

    def test_a_sketcher_that_dies_at_start_is_reported(
        self, tmp_path, fake_sketcher, captured_poll, monkeypatch
    ):
        monkeypatch.setenv("FAKE_SKETCH_SEND", "0")
        monkeypatch.setenv("FAKE_SKETCH_EXIT", "1")
        (fake_sketcher / "release").touch()
        app = _app(tmp_path)
        app.controller.sketch_molecule()
        (poll,) = captured_poll
        deadline = time.monotonic() + 30
        while _step(poll):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert app.state.status_message == (
            "vibe-sketch exited with status 1: fake sketcher: the last line of its log"
        )
        assert app.state.sketch_running is False

    def test_no_scheduler_stops_the_sketcher_it_started(self, tmp_path, fake_sketcher, monkeypatch):
        import vibeview.app.files as files_mod

        launched: list[sketch.SketchSession] = []
        real_launch = sketch.SketchSession.launch

        def launch(**kwargs):
            launched.append(real_launch(**kwargs))
            return launched[-1]

        def no_loop(coroutine):
            raise RuntimeError("no running event loop")

        monkeypatch.setattr(sketch.SketchSession, "launch", staticmethod(launch))
        monkeypatch.setattr(files_mod.asyncio, "ensure_future", no_loop)
        app = _app(tmp_path)
        app.controller.sketch_molecule()
        assert app.state.status_message == "Could not start vibe-sketch: no running event loop"
        assert app.state.sketch_running is False
        (session,) = launched
        assert session.process.poll() is not None
        assert not session.handoff_dir.exists()


def test_doctor_lists_the_sketch_capability_with_its_note(monkeypatch):
    import vibeview.onboarding as onboarding

    monkeypatch.setattr(onboarding, "_module_available", lambda module: module != "vibesketch")
    capability = onboarding.doctor_report()["capabilities"]["sketch"]
    assert capability["available"] is False
    assert "[sketch]" in capability["install_hint"]
    assert "vibe-sketch checkout" in capability["note"]


def test_the_sketch_extra_is_declared_and_kept_out_of_all():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    extras = tomllib.loads((root / "pyproject.toml").read_text())["project"][
        "optional-dependencies"
    ]
    assert extras["sketch"] == ["vibesketch>=0.1"]
    assert "vibeview[sketch]" not in extras["all"]
