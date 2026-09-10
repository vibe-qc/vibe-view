"""File watcher for auto-reloading QVF files when they change on disk.

Change detection is content-based, not mtime-based (the 2026-07-02 audit
flagged the original mtime+size compare: it fired mid-write on partially
written archives and re-fired on spurious touches):

* **Settle delay** — after the file's ``(mtime_ns, size)`` first moves, the
  watcher waits until it has stopped moving for ``settle_delay`` seconds
  before looking at the content, so a writer streaming a large archive
  isn't observed mid-copy.
* **Content fingerprint** — a settled file is fingerprinted from its QVF
  manifest (per-section digests + a digest of the non-section manifest
  keys). A rewrite that produces identical content (touch, atomic-replace
  with the same bytes) does not fire. A file that fails to parse as a QVF
  archive falls back to a whole-file SHA-256.
* **Mid-write hold** — if the file previously parsed as a QVF and now
  doesn't (half-written zip: no end-of-central-directory yet), the watcher
  holds and re-checks instead of firing on a corrupt archive.
* **Per-section diff** — the fired :class:`QVFChange` names which sections
  were added / removed / changed since the last good snapshot, so the
  viewer can hot-reload only what moved (roadmap C1; the foundation for
  live result streaming).

Two consumption styles:

* :class:`QVFChangeTracker` — synchronous, no thread; call :meth:`poll`
  periodically (e.g. from an asyncio task that owns UI state).
* :class:`QVFFileWatcher` / :func:`watch_qvf` — the original background
  daemon-thread API, now built on the tracker.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

__all__ = ["QVFChange", "QVFChangeTracker", "QVFFileWatcher", "watch_qvf"]

# Fingerprint tuple: (mode, root_digest, {section_id: section_digest})
# mode is "qvf" (parsed manifest) or "raw" (whole-file hash fallback).
_Fingerprint = tuple[str, str, dict[str, str]]


@dataclass(frozen=True)
class QVFChange:
    """One effective on-disk change, diffed per section against the last
    good snapshot. Section fields are empty for a non-QVF (``is_qvf``
    False) file, where only whole-file identity is tracked."""

    path: str
    is_qvf: bool
    changed_sections: tuple[str, ...]  # present before + after, content moved
    added_sections: tuple[str, ...]
    removed_sections: tuple[str, ...]
    # Non-section manifest keys moved (qvf_version, source, viewer_defaults,
    # provenance/run_status extensions, ...). Always True for raw files.
    manifest_meta_changed: bool

    @property
    def touched_sections(self) -> tuple[str, ...]:
        """Sections needing a reload: changed + added."""
        return tuple(sorted({*self.changed_sections, *self.added_sections}))


def _fingerprint_file(path: Path) -> _Fingerprint | None:
    """Fingerprint the file's *content*. Returns None if unreadable."""
    try:
        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest.json is not an object")
        sections: dict[str, str] = {}
        raw_sections = manifest.get("sections")
        if isinstance(raw_sections, list):
            for sec in raw_sections:
                if isinstance(sec, dict) and isinstance(sec.get("id"), str):
                    blob = json.dumps(sec, sort_keys=True, default=str)
                    sections[sec["id"]] = hashlib.sha256(blob.encode()).hexdigest()
        root = {k: v for k, v in manifest.items() if k != "sections"}
        root_blob = json.dumps(root, sort_keys=True, default=str)
        return ("qvf", hashlib.sha256(root_blob.encode()).hexdigest(), sections)
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        pass  # not (yet) a readable QVF archive — try raw bytes below

    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return ("raw", h.hexdigest(), {})
    except OSError:
        return None


def _diff(path: str, old: _Fingerprint | None, new: _Fingerprint) -> QVFChange:
    mode, root, secs = new
    old_mode, old_root, old_secs = old if old is not None else ("", "", {})
    common = set(secs) & set(old_secs)
    return QVFChange(
        path=path,
        is_qvf=(mode == "qvf"),
        changed_sections=tuple(sorted(k for k in common if secs[k] != old_secs[k])),
        added_sections=tuple(sorted(set(secs) - set(old_secs))),
        removed_sections=tuple(sorted(set(old_secs) - set(secs))),
        manifest_meta_changed=(old is None or old_root != root or old_mode != mode),
    )


class QVFChangeTracker:
    """Synchronous change detector — call :meth:`poll` periodically.

    Owns no thread and touches no UI, so it can be driven from whatever
    loop owns the viewer state (the app drives it from an asyncio task).

    Parameters
    ----------
    path : str or Path
        File to track. A baseline snapshot is taken immediately.
    settle_delay : float
        Seconds the file's ``(mtime_ns, size)`` must hold still after a
        change before the content is fingerprinted (default 0.5).
    """

    def __init__(self, path: str | Path, settle_delay: float = 0.5):
        self._path = Path(path)
        self._settle_delay = float(settle_delay)
        self._last_stat = self._stat()
        self._last_fp: _Fingerprint | None = _fingerprint_file(self._path)
        # (stat-at-detection, monotonic-time-of-last-movement) while a
        # change is waiting out the settle delay; also set while holding
        # on a mid-write archive.
        self._pending: tuple[tuple[int, int], float] | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def has_pending(self) -> bool:
        """True while a detected change is waiting to settle — callers may
        poll faster to keep reload latency near ``settle_delay``."""
        return self._pending is not None

    def _stat(self) -> tuple[int, int]:
        try:
            st = os.stat(self._path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return (0, -1)

    def poll(self) -> QVFChange | None:
        """Check the file once; return the change if one has settled."""
        now = time.monotonic()
        stat = self._stat()

        if self._pending is None:
            if stat == self._last_stat:
                return None
            self._pending = (stat, now)
            return None

        pending_stat, moved_at = self._pending
        if stat != pending_stat:
            self._pending = (stat, now)  # still moving — restart the clock
            return None
        if now - moved_at < self._settle_delay:
            return None

        # Settled: fingerprint the content.
        fp = _fingerprint_file(self._path)
        if fp is None:  # vanished/unreadable — rebaseline, don't fire
            self._pending = None
            self._last_stat = stat
            return None
        if self._last_fp is not None and self._last_fp[0] == "qvf" and fp[0] == "raw":
            # Previously a valid archive, now unparseable: a writer is
            # mid-rewrite (zip central directory lands last). Hold — the
            # next poll re-checks; firing now would hand the viewer a
            # corrupt file.
            return None

        old = self._last_fp
        self._last_fp = fp
        self._last_stat = stat
        self._pending = None
        if fp == old:
            return None  # touch / atomic replace with identical content
        return _diff(str(self._path), old, fp)


class QVFFileWatcher:
    """Background-thread file watcher (legacy convenience API).

    Parameters
    ----------
    path : str or Path
        Path to the file to watch.
    callback : callable
        Called with the path (str) after each settled, effective change.
    interval : float
        Idle polling interval in seconds (default: 5.0). While a change is
        settling the thread polls faster (~``settle_delay/2``).
    settle_delay : float
        See :class:`QVFChangeTracker` (default 0.5).
    on_event : callable, optional
        Called with the :class:`QVFChange` after ``callback``.
    """

    def __init__(
        self,
        path: str | Path,
        callback: Callable[[str], None],
        interval: float = 5.0,
        settle_delay: float = 0.5,
        on_event: Callable[[QVFChange], None] | None = None,
    ):
        self._path = Path(path)
        self._callback = callback
        self._on_event = on_event
        self._interval = float(interval)
        self._settle_delay = float(settle_delay)
        self._thread: threading.Thread | None = None
        self._running = False
        self._tracker: QVFChangeTracker | None = None
        self.last_event: QVFChange | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start watching in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._tracker = QVFChangeTracker(self._path, settle_delay=self._settle_delay)
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the watcher thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _watch_loop(self) -> None:
        # Sleep in short slices so stop() isn't blocked by a long interval
        # and a settling change is re-checked promptly.
        step = max(min(self._settle_delay / 2.0, self._interval), 0.02)
        slept = 0.0
        while self._running:
            time.sleep(step)
            slept += step
            if not self._running:
                break
            tracker = self._tracker
            if tracker is None:
                break
            if slept < self._interval and not tracker.has_pending:
                continue
            slept = 0.0
            try:
                event = tracker.poll()
                if event is not None:
                    self.last_event = event
                    if self._callback:
                        self._callback(str(self._path))
                    if self._on_event:
                        self._on_event(event)
            except Exception:
                pass  # a callback error must not kill the watch thread


def watch_qvf(
    path: str,
    callback: Callable[[str], None],
    interval: float = 5.0,
    settle_delay: float = 0.5,
    on_event: Callable[[QVFChange], None] | None = None,
) -> QVFFileWatcher:
    """Convenience: create and start a QVF file watcher.

    Returns the watcher instance, which can be stopped with ``.stop()``.
    """
    watcher = QVFFileWatcher(
        path, callback, interval=interval, settle_delay=settle_delay, on_event=on_event
    )
    watcher.start()
    return watcher
