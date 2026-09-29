"""worker_client is the one subprocess lifecycle for the JSON-lines workers."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from vibeview import worker_client as wc


def _worker(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / "worker.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


class TestParsing:
    def test_strict_parse_event(self) -> None:
        assert wc.parse_event(b'{"a": 1}') == {"a": 1}
        with pytest.raises(ValueError, match="Duplicate"):
            wc.parse_event('{"a": 1, "a": 2}')
        with pytest.raises(ValueError, match="Non-finite"):
            wc.parse_event('{"a": NaN}')
        with pytest.raises(ValueError, match="object"):
            wc.parse_event("[1, 2]")
        with pytest.raises(ValueError):
            wc.parse_event("not json")

    def test_lenient_parse_event_or_none(self) -> None:
        assert wc.parse_event_or_none("") is None
        assert wc.parse_event_or_none("  \n") is None
        assert wc.parse_event_or_none("UserWarning: something") is None
        assert wc.parse_event_or_none(b"[1]") is None
        assert wc.parse_event_or_none(b'{"step": 1}') == {"step": 1}
        assert wc.parse_event_or_none(b'\xff\xfe{"a": 1}') is None

    def test_parse_events_skips_blank_lines_only(self) -> None:
        assert wc.parse_events(b'{"a": 1}\n\n{"b": 2}\n') == [{"a": 1}, {"b": 2}]
        with pytest.raises(ValueError):
            wc.parse_events(b'{"a": 1}\nnoise\n')


class TestExchange:
    def test_collects_stdout_stderr_tail_and_returncode(self, tmp_path: Path) -> None:
        cmd = _worker(
            tmp_path,
            """
            import sys
            data = sys.stdin.read()
            print('{"echo": %d}' % len(data))
            print("warning line", file=sys.stderr)
            sys.exit(3)
            """,
        )
        reply = asyncio.run(wc.exchange(cmd, b"hello", timeout_s=10.0))
        assert reply.stdout == b'{"echo": 5}\n'
        assert reply.stderr_tail.strip() == "warning line"
        assert reply.diagnostics == "warning line"
        assert reply.returncode == 3

    def test_timeout_reaps_the_child(self, tmp_path: Path) -> None:
        pidfile = tmp_path / "pid"
        cmd = _worker(
            tmp_path,
            f"""
            import os, time
            open({str(pidfile)!r}, "w").write(str(os.getpid()))
            time.sleep(60)
            """,
        )
        with pytest.raises(TimeoutError):
            asyncio.run(wc.exchange(cmd, b"", timeout_s=0.5))
        with pytest.raises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)

    def test_bounded_stdout(self, tmp_path: Path) -> None:
        cmd = _worker(tmp_path, "import sys\nsys.stdout.write('x' * 200000)\n")
        with pytest.raises(ValueError, match="exceeds"):
            asyncio.run(wc.exchange(cmd, b"", timeout_s=10.0, max_stdout_bytes=100_000))

    def test_probe_appends_the_flag(self, tmp_path: Path) -> None:
        cmd = _worker(tmp_path, "import sys, json\nprint(json.dumps({'args': sys.argv[1:]}))\n")
        reply = asyncio.run(wc.probe(cmd, timeout_s=10.0))
        assert wc.parse_events(reply.stdout) == [{"args": ["--probe"]}]

    def test_missing_command_raises_oserror(self) -> None:
        with pytest.raises(OSError):
            asyncio.run(wc.exchange(["/nonexistent/python"], b"", timeout_s=1.0))


class TestStreaming:
    def test_streams_events_and_skips_noise(self, tmp_path: Path) -> None:
        cmd = _worker(
            tmp_path,
            """
            import sys, json
            req = json.loads(sys.stdin.read())
            print("some library warning", flush=True)
            for k in range(req["n"]):
                print(json.dumps({"step": k}), flush=True)
            print(json.dumps({"done": True}), flush=True)
            """,
        )

        async def run() -> list[dict]:
            seen = []
            async with wc.stream_events(cmd, b'{"n": 2}\n') as stream:
                async for event in stream.events():
                    seen.append(event)
                    if event.get("done"):
                        break
            assert stream.process.returncode is not None
            return seen

        assert asyncio.run(run()) == [{"step": 0}, {"step": 1}, {"done": True}]

    def test_exit_reaps_a_worker_that_keeps_running(self, tmp_path: Path) -> None:
        pidfile = tmp_path / "pid"
        cmd = _worker(
            tmp_path,
            f"""
            import os, sys, time
            open({str(pidfile)!r}, "w").write(str(os.getpid()))
            print('{{"step": 0}}', flush=True)
            time.sleep(60)
            """,
        )

        async def run() -> None:
            async with wc.stream_events(cmd, b"") as stream:
                async for _event in stream.events():
                    break  # leave early: the child is still running

        asyncio.run(run())
        with pytest.raises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)


def test_terminate_and_wait_reaps_a_synchronous_child() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    wc.terminate_and_wait(proc, timeout_s=5.0)
    assert proc.poll() is not None
    wc.terminate_and_wait(proc)  # already gone: a no-op


def test_no_module_keeps_a_private_lifecycle() -> None:
    from vibeview import live_opt, relocalize

    assert not hasattr(relocalize, "_exchange")
    assert not hasattr(relocalize, "parse_event")
    assert live_opt.parse_event(b'{"step": 1}') == {"step": 1}
    assert live_opt.parse_event("noise") is None
