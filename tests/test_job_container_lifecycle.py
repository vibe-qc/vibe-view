"""Viewer-side tests for the QVF job-container lifecycle (spec § 3.2,
§ 5.9): real containers produced by vibeqc's `run_container` — success,
early failure, forced reruns — plus the renderer safety contracts for
run.record attachments and the atomic pending → running → terminal
reload sequence.

These tests need vibeqc (the producer) in the same environment; they
skip cleanly when only vibe-view is installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

vibeqc = pytest.importorskip("vibeqc")

from vibeqc._vibeqc_core import Atom, Molecule  # noqa: E402

from vibeview.file_watcher import QVFChangeTracker  # noqa: E402
from vibeview.qvf import QVFReader  # noqa: E402
from vibeview.renderers.job_spec import JobSpecRenderer  # noqa: E402
from vibeview.renderers.run_record import (  # noqa: E402
    RunRecordRenderer,
    _ndjson_blocks,
)


def _h2() -> Molecule:
    return Molecule(
        [Atom(1, [0.0, 0.0, 0.0]), Atom(1, [0.0, 0.0, 1.4])], 0, 1
    )


def _run_success(tmp_path: Path, name: str = "job") -> Path:
    """A real settled container: pending in, converged out, with the
    perf + structured sidecars requested so all three attachment roles
    embed."""
    path = vibeqc.write_pending_qvf(
        _h2(),
        tmp_path / name,
        method="rhf",
        basis="sto-3g",
        options={"perf_log": True, "structured_log": True},
    )
    vibeqc.run_container(path)
    return Path(path)


def _latest_record_id(reader: QVFReader) -> str:
    return reader.run_record_sections()[-1].id


def _rewrite_without_member(path: Path, member_role: str) -> None:
    """Drop a member from the latest run.record, keeping the zip valid."""
    with zipfile.ZipFile(path, "r") as zf:
        names = {n: zf.read(n) for n in zf.namelist()}
    manifest = json.loads(names["manifest.json"])
    records = [
        s for s in manifest["sections"] if s["kind"] == "run.record"
    ]
    record = records[-1]
    member = record["members"].pop(member_role)
    names.pop(member["path"], None)
    files_member = record["members"].get("files")
    if files_member and files_member["path"] in names:
        files_doc = json.loads(names[files_member["path"]])
        files_doc.pop(member_role, None)
        files_bytes = json.dumps(files_doc).encode()
        names[files_member["path"]] = files_bytes
        files_member["sha256"] = hashlib.sha256(files_bytes).hexdigest()
    names["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in names.items():
            zf.writestr(name, data)


class TestSuccessContainer:
    def test_settled_container_is_done_and_warning_free(self, tmp_path):
        path = _run_success(tmp_path)
        with QVFReader(str(path)) as reader:
            assert reader.run_status == "converged"
            assert reader.lifecycle_warnings() == []
            history = reader.run_record_sections()
            assert len(history) == 1
            record = reader.read_run_record(history[0].id)
            assert record.input_text  # the executed job.spec JSON
            assert record.log_text  # complete log
            # The executed input is the declarative spec, never a script.
            assert json.loads(record.input_text)["job_type"] == "molecular"

    def test_perf_and_structured_attachments_render(self, tmp_path):
        path = _run_success(tmp_path)
        with QVFReader(str(path)) as reader:
            rid = _latest_record_id(reader)
            record = reader.read_run_record(rid)
            assert "attachment.perf" in record.attachment_roles
            assert "attachment.structured" in record.attachment_roles
            assert "attachment.system" in record.attachment_roles
            section = reader.get_section(rid)
            html = RunRecordRenderer(section, reader).render_to_html()
            assert "Performance log" in html
            assert "Structured events" in html
            assert "System manifest" in html
            # The structured log parsed as NDJSON → event table, not a
            # raw dump labeled as fallback.
            assert "not valid NDJSON" not in html
            assert "<table class='events'>" in html


class TestEarlyFailureContainer:
    def _failed_container(self, tmp_path, monkeypatch) -> Path:
        path = vibeqc.write_pending_qvf(
            _h2(), tmp_path / "boom", method="rhf", basis="sto-3g"
        )

        def exploding_run_job(molecule, **kwargs):
            raise RuntimeError("pre-output crash")

        monkeypatch.setattr(vibeqc, "run_job", exploding_run_job)
        with pytest.raises(RuntimeError, match="pre-output crash"):
            vibeqc.run_container(path)
        return Path(path)

    def test_zero_byte_log_is_a_complete_record(
        self, tmp_path, monkeypatch
    ):
        path = self._failed_container(tmp_path, monkeypatch)
        with QVFReader(str(path)) as reader:
            assert reader.run_status == "failed"
            # A run that died before output began logged exactly zero
            # bytes — that is a complete record, not a missing log.
            assert reader.lifecycle_warnings() == []
            rid = _latest_record_id(reader)
            record = reader.read_run_record(rid)
            assert record.log_text == ""
            section = reader.get_section(rid)
            html = RunRecordRenderer(section, reader).render_to_html()
            assert "ended before output began" in html
            # No incompleteness warning in the panel.
            assert "incomplete" not in html


class TestTerminalCompletenessWarnings:
    def test_missing_log_member_warns(self, tmp_path):
        path = _run_success(tmp_path)
        _rewrite_without_member(path, "log")
        with QVFReader(str(path)) as reader:
            warnings = reader.lifecycle_warnings()
            assert len(warnings) == 1
            assert "log" in warnings[0]
            rid = _latest_record_id(reader)
            html = RunRecordRenderer(
                reader.get_section(rid), reader
            ).render_to_html()
            assert "incomplete" in html

    def test_missing_input_member_warns(self, tmp_path):
        path = _run_success(tmp_path)
        _rewrite_without_member(path, "input")
        with QVFReader(str(path)) as reader:
            warnings = reader.lifecycle_warnings()
            assert len(warnings) == 1
            assert "input" in warnings[0]

    def test_non_terminal_status_never_warns(self, tmp_path):
        path = vibeqc.write_pending_qvf(
            _h2(), tmp_path / "job", method="rhf", basis="sto-3g"
        )
        with QVFReader(str(path)) as reader:
            assert reader.run_status == "pending"
            assert reader.lifecycle_warnings() == []


class TestForcedRerunHistory:
    def test_two_runs_render_as_history(self, tmp_path):
        path = _run_success(tmp_path)
        vibeqc.run_container(path, force=True)
        with QVFReader(str(path)) as reader:
            assert reader.run_status == "converged"
            assert reader.lifecycle_warnings() == []
            history = reader.run_record_sections()
            assert len(history) == 2
            # Ordered by sequence; the last entry is the latest run.
            seqs = [
                (getattr(s, "model_extra", None) or {}).get("sequence")
                for s in history
            ]
            assert seqs == sorted(seqs)
            first_pos = reader.run_record_position(history[0].id)
            last_pos = reader.run_record_position(history[-1].id)
            assert first_pos == (1, 2)
            assert last_pos == (2, 2)
            html_latest = RunRecordRenderer(
                reader.get_section(history[-1].id), reader
            ).render_to_html()
            assert "latest" in html_latest
            html_first = RunRecordRenderer(
                reader.get_section(history[0].id), reader
            ).render_to_html()
            assert "superseded by run #2" in html_first


class TestAtomicReloadTransitions:
    def test_pending_running_terminal_sequence(
        self, tmp_path, monkeypatch
    ):
        """Each atomic lifecycle rewrite of the container must be seen
        by the change tracker as a distinct, loadable state."""
        path = vibeqc.write_pending_qvf(
            _h2(), tmp_path / "job", method="rhf", basis="sto-3g"
        )
        pending_bytes = Path(path).read_bytes()

        captured: dict[str, bytes] = {}
        real_run_job = vibeqc.run_job

        def capturing_run_job(molecule, **kwargs):
            # The container was atomically marked running before the
            # runner started — capture that intermediate state.
            captured["running"] = Path(path).read_bytes()
            return real_run_job(molecule, **kwargs)

        monkeypatch.setattr(vibeqc, "run_job", capturing_run_job)
        vibeqc.run_container(path)
        terminal_bytes = Path(path).read_bytes()

        assert "running" in captured
        stage = tmp_path / "stage.qvf"

        def _replace_with(data: bytes) -> None:
            tmp = tmp_path / "stage.qvf.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, stage)  # the producer's atomic pattern

        _replace_with(pending_bytes)
        tracker = QVFChangeTracker(stage, settle_delay=0.0)
        statuses = []
        for data in (captured["running"], terminal_bytes):
            _replace_with(data)
            tracker.poll()  # detects movement, starts the settle clock
            event = tracker.poll()
            assert event is not None, "atomic rewrite must fire reload"
            assert event.is_qvf
            assert event.manifest_meta_changed  # run_status flip visible
            with QVFReader(str(stage)) as reader:
                statuses.append(reader.run_status)
        assert statuses == ["running", "converged"]

    def test_running_state_preserves_submitted_container(self, tmp_path):
        """The running-state archive still parses as a job container —
        a viewer refreshing mid-run keeps structure + spec + banner."""
        path = vibeqc.write_pending_qvf(
            _h2(), tmp_path / "job", method="rhf", basis="sto-3g"
        )
        from vibeqc.qvf_job import _mark_container_running

        _mark_container_running(Path(path))
        with QVFReader(str(path)) as reader:
            assert reader.run_status == "running"
            spec = reader.read_job_spec("job_spec")
            assert spec.method == "rhf"
            assert reader.lifecycle_warnings() == []


class TestAttachmentSafety:
    def _with_binary_attachment(self, tmp_path) -> Path:
        """A settled container plus an arbitrary opaque binary
        attachment (not on the renderer's text allowlist)."""
        path = _run_success(tmp_path)
        with zipfile.ZipFile(path, "r") as zf:
            names = {n: zf.read(n) for n in zf.namelist()}
        manifest = json.loads(names["manifest.json"])
        record = [
            s for s in manifest["sections"] if s["kind"] == "run.record"
        ][-1]
        blob = bytes(range(256)) * 16  # not UTF-8, not text
        blob_path = "run_record/attachments/blob"
        names[blob_path] = blob
        record["members"]["attachment.blob"] = {
            "path": blob_path,
            "format": "binary",
            "sha256": hashlib.sha256(blob).hexdigest(),
        }
        files_member = record["members"]["files"]
        files_doc = json.loads(names[files_member["path"]])
        files_doc["attachment.blob"] = {"filename": "restart.blob"}
        files_bytes = json.dumps(files_doc).encode()
        names[files_member["path"]] = files_bytes
        files_member["sha256"] = hashlib.sha256(files_bytes).hexdigest()
        names["manifest.json"] = json.dumps(manifest).encode()
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in names.items():
                zf.writestr(name, data)
        return path

    def test_binary_attachment_listed_never_rendered(self, tmp_path):
        path = self._with_binary_attachment(tmp_path)
        with QVFReader(str(path)) as reader:
            rid = _latest_record_id(reader)
            html = RunRecordRenderer(
                reader.get_section(rid), reader
            ).render_to_html()
            # Listed by filename with the extraction note, and its bytes
            # never enter the DOM.
            assert "restart.blob" in html
            assert "extract" in html
            assert "\x00" not in html
            assert "\x07" not in html

    def test_oversize_text_attachment_falls_back_to_listing(
        self, tmp_path
    ):
        path = _run_success(tmp_path)
        with QVFReader(str(path)) as reader:
            rid = _latest_record_id(reader)
            # A 1-byte cap forces even the system manifest through the
            # size gate: read returns None, renderer must list it.
            assert (
                reader.read_run_record_attachment(
                    rid, "attachment.system", max_bytes=1
                )
                is None
            )


class TestNdjsonRendering:
    def test_unparseable_lines_are_skipped_not_fatal(self):
        """A bad line costs that line, not the whole table.

        This replaces an earlier assertion that the *first* unparseable
        line sent the whole attachment to raw text. That lost the events
        of any run killed mid-write — precisely the case NDJSON is
        designed for: vibe-qc's structured log documents that "a partial
        write at crash-time still leaves earlier records parseable". The
        original concern (a partly-parsed table misrepresenting the log)
        is met by stating the skipped count rather than hiding it.
        """
        blocks = "".join(
            _ndjson_blocks('{"event": "scf"}\nnot json at all\n')
        )
        assert "<table class='events'>" in blocks, "good event was dropped"
        assert "scf" in blocks
        assert "could not be parsed" in blocks, "skipped line not disclosed"

    def test_crash_truncated_tail_keeps_earlier_events(self):
        good = [
            json.dumps({"event": "job_start"}),
            json.dumps({"event": "scf", "iter": 1}),
            json.dumps({"event": "scf", "iter": 2}),
        ]
        killed = "\n".join(good) + '\n{"event": "job_end", "exit_st'
        blocks = "".join(_ndjson_blocks(killed))
        assert blocks.count("<tr>") == 3, "surviving events not all shown"
        assert "could not be parsed" in blocks

    def test_wholly_unparseable_still_falls_back_to_raw_text(self):
        blocks = "".join(_ndjson_blocks("not json\nnor this\n"))
        assert "not valid NDJSON" in blocks
        assert "not json" in blocks  # raw text preserved
        assert "<table class='events'>" not in blocks

    def test_non_object_lines_fall_back(self):
        blocks = "".join(_ndjson_blocks("[1, 2, 3]\n"))
        assert "not valid NDJSON" in blocks

    def test_row_cap_elides_middle(self):
        lines = "\n".join(
            json.dumps({"event": "scf", "iter": i}) for i in range(1000)
        )
        blocks = "".join(_ndjson_blocks(lines))
        assert "events elided for display" in blocks
        # Head starts at 1, tail ends at 1000.
        assert "<td>1</td>" in blocks
        assert "<td>1000</td>" in blocks

    def test_small_log_renders_all_rows(self):
        lines = "\n".join(
            json.dumps({"event": "scf", "iter": i}) for i in range(5)
        )
        blocks = "".join(_ndjson_blocks(lines))
        assert "elided" not in blocks
        assert blocks.count("<tr>") == 5


class TestJobSpecPanelOnHistory:
    def test_job_spec_banner_reflects_latest_terminal_status(
        self, tmp_path
    ):
        path = _run_success(tmp_path)
        with QVFReader(str(path)) as reader:
            html = JobSpecRenderer(
                reader.get_section("job_spec"), reader
            ).render_to_html()
            assert "settled archive" in html
