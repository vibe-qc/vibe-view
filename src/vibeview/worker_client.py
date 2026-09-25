"""One lifecycle for the JSON-lines worker subprocesses.

Two clients drove a ``python -m <worker> [--probe]`` child that speaks one
JSON object per stdout line: re-localization (``relocalize.py``) and live
optimization (``live_opt.py``). They had two event parsers of different
rigour, two exchange loops and two ways of reaping a child, and the file
browser terminated its viewer children without waiting for them. This
module is the lifecycle they share.

Two parsers, on purpose. :func:`parse_event` is strict: a duplicate key or
a non-finite number is a protocol error, which is what a request/response
exchange wants. :func:`parse_event_or_none` is the streaming filter: a
worker that streams steps may also print a library warning to stdout, and
a line that is not an event is skipped rather than fatal.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

MAX_STDOUT_BYTES = 32 * 1024 * 1024
STDERR_TAIL_BYTES = 8192
READ_CHUNK = 65536


# ── events ────────────────────────────────────────────────────────────


def parse_event(line: bytes | str) -> dict[str, Any]:
    """One JSON-object event, strictly: no duplicate keys, no NaN/Infinity."""

    def pairs(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON event key")
            result[key] = value
        return result

    def nonfinite(_: str) -> Any:
        raise ValueError("Non-finite JSON event")

    if isinstance(line, bytes):
        line = line.decode("utf-8")
    value = json.loads(line, object_pairs_hook=pairs, parse_constant=nonfinite)
    if not isinstance(value, dict):
        raise ValueError("Worker event must be an object")
    return value


def parse_event_or_none(line: bytes | str) -> dict[str, Any] | None:
    """One event from a stream, or None for a blank line or non-event noise."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    line = line.strip()
    if not line:
        return None
    try:
        return parse_event(line)
    except ValueError:
        return None


def parse_events(stdout: bytes) -> list[dict[str, Any]]:
    """Every non-blank line of a finished worker's stdout, strictly parsed."""
    return [parse_event(line) for line in stdout.splitlines() if line.strip()]


# ── reaping ───────────────────────────────────────────────────────────


async def reap(process: asyncio.subprocess.Process) -> None:
    """Kill a child that is still running and wait for it, so nothing lingers."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()


def terminate_and_wait(process: subprocess.Popen[Any], *, timeout_s: float = 5.0) -> None:
    """Terminate a synchronous child and wait for it; kill it if it ignores the request."""
    if process.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        process.wait()


# ── request/response exchange ─────────────────────────────────────────


@dataclass(frozen=True)
class Exchange:
    """What a finished worker left behind."""

    stdout: bytes
    stderr_tail: str
    returncode: int | None

    @property
    def diagnostics(self) -> str:
        """The last line of stderr, for a one-line reason."""
        lines = self.stderr_tail.strip().splitlines()
        return lines[-1][-300:] if lines else ""


async def exchange(
    command: Sequence[str],
    payload: bytes = b"",
    *,
    timeout_s: float,
    max_stdout_bytes: int = MAX_STDOUT_BYTES,
) -> Exchange:
    """Run ``command``, feed it ``payload`` on stdin, collect stdout and a stderr tail.

    Stdout is bounded by ``max_stdout_bytes`` (a runaway worker cannot
    exhaust memory) and only the last :data:`STDERR_TAIL_BYTES` of stderr
    are kept. On timeout or cancellation the child is killed and awaited
    before the exception propagates, so a probe cannot leave a worker
    behind. A timeout raises :class:`TimeoutError`.
    """
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def drain(stream: asyncio.StreamReader | None, *, tail: bool = False) -> bytes:
        data = bytearray()
        if stream is None:  # pragma: no cover - PIPE was requested above
            return bytes(data)
        while chunk := await stream.read(READ_CHUNK):
            data.extend(chunk)
            if tail:
                del data[:-STDERR_TAIL_BYTES]
            elif len(data) > max_stdout_bytes:
                raise ValueError(f"Worker output exceeds {max_stdout_bytes // (1024 * 1024)} MiB")
        return bytes(data)

    async def send() -> None:
        stdin = process.stdin
        if stdin is None:  # pragma: no cover - PIPE was requested above
            return
        try:
            if payload:
                stdin.write(payload)
                await stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            stdin.close()

    tasks: list[asyncio.Task[Any]] = [
        asyncio.create_task(send()),
        asyncio.create_task(drain(process.stdout)),
        asyncio.create_task(drain(process.stderr, tail=True)),
        asyncio.create_task(process.wait()),
    ]
    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout_s)
        return Exchange(
            stdout=tasks[1].result(),
            stderr_tail=tasks[2].result().decode("utf-8", "replace"),
            returncode=tasks[3].result(),
        )
    finally:
        await reap(process)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def probe(
    command: Sequence[str], *, timeout_s: float, max_stdout_bytes: int = MAX_STDOUT_BYTES
) -> Exchange:
    """Run the worker's ``--probe`` mode with no request."""
    return await exchange(
        [*command, "--probe"], b"", timeout_s=timeout_s, max_stdout_bytes=max_stdout_bytes
    )


# ── streaming ─────────────────────────────────────────────────────────


class WorkerStream:
    """A running worker whose stdout events are read one at a time.

    Use through :func:`stream_events`. ``process`` is exposed so an owner
    can kill the child from outside the loop (cancel-on-edit); the stream's
    own exit always reaps it.
    """

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Each event line until the worker closes stdout; noise lines are skipped."""
        stdout = self.process.stdout
        assert stdout is not None  # PIPE was requested
        while True:
            line = await stdout.readline()
            if not line:
                return
            event = parse_event_or_none(line)
            if event is not None:
                yield event


@contextlib.asynccontextmanager
async def stream_events(command: Sequence[str], payload: bytes) -> AsyncIterator[WorkerStream]:
    """Start a worker, hand it ``payload`` on stdin, and reap it on exit however the block ends.

    Raises :class:`OSError` when the command cannot be started; the caller
    decides how to report that. stderr is discarded: a streaming worker
    reports through its events.
    """
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        assert process.stdin is not None
        process.stdin.write(payload)
        process.stdin.write_eof()
        yield WorkerStream(process)
    finally:
        await reap(process)
