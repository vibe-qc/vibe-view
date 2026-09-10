"""file_watcher hardening tests (roadmap C1 / audit finding #7).

The original watcher compared only mtime+size, so it fired mid-write on
half-written archives and re-fired on content-identical touches. These
tests pin the hardened contract: settle delay, content fingerprint,
mid-write hold, and the per-section diff that hot-reload consumes.
"""

from __future__ import annotations

import itertools
import json
import os
import time
import zipfile

from vibeview.file_watcher import (
    QVFChange,
    QVFChangeTracker,
    QVFFileWatcher,
    watch_qvf,
)


def _write_qvf(path, section_shas: dict[str, str], run_status: str | None = None):
    """Write a minimal QVF-shaped zip whose per-section content identity is
    steered via each section's member sha256 (as real writers do)."""
    manifest = {
        "qvf_version": 1,
        "source": {"program": "test", "version": "0", "calculation": "scf"},
        "sections": [
            {
                "id": sid,
                "kind": "structure" if sid == "structure" else "scf_history",
                "members": {
                    "data": {"path": f"{sid}.json", "format": "json", "sha256": sha}
                },
            }
            for sid, sha in section_shas.items()
        ],
    }
    if run_status is not None:
        manifest["provenance"] = {"run_status": run_status}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
    # The tracker's movement signal is (mtime_ns, size). The sha payloads
    # above are all the same length, so consecutive writes that land in
    # one filesystem timestamp tick (CI overlayfs) leave the stat pair
    # unchanged and the rewrite invisible. Force a strictly increasing
    # mtime per write so every rewrite registers as movement.
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + next(_WRITE_TICK)))


_WRITE_TICK = itertools.count(1)


_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64


class TestChangeTracker:
    def test_no_change_no_fire(self, tmp_path):
        f = tmp_path / "t.qvf"
        _write_qvf(f, {"structure": _SHA_A})
        tr = QVFChangeTracker(f, settle_delay=0.0)
        assert tr.poll() is None
        assert tr.poll() is None

    def test_section_content_change_diffed(self, tmp_path):
        f = tmp_path / "t.qvf"
        _write_qvf(f, {"structure": _SHA_A, "scf": _SHA_B})
        tr = QVFChangeTracker(f, settle_delay=0.0)
        _write_qvf(f, {"structure": _SHA_A, "scf": _SHA_C})
        tr.poll()  # detects movement, starts settle clock
        ev = tr.poll()
        assert isinstance(ev, QVFChange)
        assert ev.is_qvf
        assert ev.changed_sections == ("scf",)
        assert ev.added_sections == ()
        assert ev.removed_sections == ()
        assert ev.manifest_meta_changed is False
        assert ev.touched_sections == ("scf",)

    def test_added_and_removed_sections(self, tmp_path):
        f = tmp_path / "t.qvf"
        _write_qvf(f, {"structure": _SHA_A, "old": _SHA_B})
        tr = QVFChangeTracker(f, settle_delay=0.0)
        _write_qvf(f, {"structure": _SHA_A, "new": _SHA_C})
        tr.poll()
        ev = tr.poll()
        assert ev.added_sections == ("new",)
        assert ev.removed_sections == ("old",)
        assert ev.changed_sections == ()

    def test_manifest_meta_change_flagged(self, tmp_path):
        """run_status flipping (running → converged) must be visible even
        when no section content moved — M4 keys off this."""
        f = tmp_path / "t.qvf"
        _write_qvf(f, {"structure": _SHA_A}, run_status="running")
        tr = QVFChangeTracker(f, settle_delay=0.0)
        _write_qvf(f, {"structure": _SHA_A}, run_status="converged")
        tr.poll()
        ev = tr.poll()
        assert ev is not None
        assert ev.manifest_meta_changed is True
        assert ev.changed_sections == ()

    def test_touch_with_identical_content_no_fire(self, tmp_path):
        f = tmp_path / "t.qvf"
        _write_qvf(f, {"structure": _SHA_A})
        tr = QVFChangeTracker(f, settle_delay=0.0)
        os.utime(f, (time.time() + 10, time.time() + 10))
        tr.poll()
        assert tr.poll() is None  # mtime moved, content identical

    def test_settle_delay_holds_fire(self, tmp_path):
        f = tmp_path / "t.qvf"
        _write_qvf(f, {"structure": _SHA_A})
        tr = QVFChangeTracker(f, settle_delay=0.15)
        _write_qvf(f, {"structure": _SHA_B})
        assert tr.poll() is None  # movement detected
        assert tr.poll() is None  # settle window still open
        assert tr.has_pending
        time.sleep(0.2)
        ev = tr.poll()
        assert ev is not None and ev.changed_sections == ("structure",)
        assert not tr.has_pending

    def test_still_moving_restarts_settle_clock(self, tmp_path):
        f = tmp_path / "t.qvf"
        _write_qvf(f, {"structure": _SHA_A})
        tr = QVFChangeTracker(f, settle_delay=0.15)
        _write_qvf(f, {"structure": _SHA_B})
        tr.poll()
        time.sleep(0.2)
        _write_qvf(f, {"structure": _SHA_C, "extra": _SHA_A})  # writer active
        assert tr.poll() is None  # stat moved again → clock restarts
        time.sleep(0.2)
        ev = tr.poll()
        assert ev is not None
        assert ev.changed_sections == ("structure",)
        assert ev.added_sections == ("extra",)

    def test_midwrite_invalid_archive_held_until_valid(self, tmp_path):
        """A half-written zip (central directory absent) must never fire;
        the change fires once the writer finishes."""
        f = tmp_path / "t.qvf"
        _write_qvf(f, {"structure": _SHA_A})
        tr = QVFChangeTracker(f, settle_delay=0.0)
        f.write_bytes(b"PK\x03\x04 half-written garbage")
        tr.poll()
        assert tr.poll() is None  # settled but unparseable → hold
        assert tr.poll() is None  # keeps holding, doesn't rebaseline
        _write_qvf(f, {"structure": _SHA_B})
        tr.poll()  # movement
        ev = tr.poll()
        assert ev is not None and ev.changed_sections == ("structure",)

    def test_plain_file_raw_fallback(self, tmp_path):
        """Non-QVF content is tracked by whole-file hash (no sections)."""
        f = tmp_path / "notes.txt"
        f.write_text("initial")
        tr = QVFChangeTracker(f, settle_delay=0.0)
        f.write_text("modified")
        tr.poll()
        ev = tr.poll()
        assert ev is not None
        assert ev.is_qvf is False
        assert ev.manifest_meta_changed is True
        assert ev.touched_sections == ()

    def test_vanished_file_rebaselines_without_firing(self, tmp_path):
        f = tmp_path / "t.qvf"
        _write_qvf(f, {"structure": _SHA_A})
        tr = QVFChangeTracker(f, settle_delay=0.0)
        f.unlink()
        tr.poll()
        assert tr.poll() is None


class TestThreadWatcher:
    def test_callback_and_event_delivered(self, tmp_path):
        f = tmp_path / "t.qvf"
        _write_qvf(f, {"structure": _SHA_A})
        calls, events = [], []
        w = QVFFileWatcher(
            str(f),
            lambda p: calls.append(p),
            interval=0.05,
            settle_delay=0.05,
            on_event=events.append,
        )
        w.start()
        try:
            time.sleep(0.1)
            _write_qvf(f, {"structure": _SHA_B})
            deadline = time.monotonic() + 3.0
            while not events and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            w.stop()
        assert calls and calls[0] == str(f)
        assert events and events[0].changed_sections == ("structure",)
        assert w.last_event is events[0]

    def test_watch_qvf_helper_roundtrip(self, tmp_path):
        f = tmp_path / "t.qvf"
        _write_qvf(f, {"structure": _SHA_A})
        w = watch_qvf(str(f), lambda p: None, interval=0.05)
        assert w.is_running
        w.stop()
        assert not w.is_running
        w.stop()  # idempotent
