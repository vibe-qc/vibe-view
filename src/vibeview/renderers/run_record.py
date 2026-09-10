"""run.record renderer — the self-contained record of one program run.

The QVF `run.record` section (spec § 5.8) carries the verbatim input
handed to a quantum-chemistry code and the full log/output it produced,
plus a `program` identity. We render a metadata header, the input, and
the log as monospace text.

Logs can reach tens of MB and the panel travels over the websocket into
an iframe srcdoc, so the log is size-gated: beyond `_LOG_RENDER_CAP`
bytes only the head and tail slices are rendered, with an elision
marker stating how much was skipped (the full text is always in the
archive itself — this gates the DOM, not the data).
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, RunRecordData, Section

# Render the whole log up to this many characters; beyond it, slice.
_LOG_RENDER_CAP = 2 * 1024 * 1024
_LOG_HEAD_CHARS = 128 * 1024
_LOG_TAIL_CHARS = 512 * 1024

# Attachment roles this renderer shows as text. Everything else — any
# arbitrary binary attachment — is deliberately never rendered, never
# interpreted, and never executed: it is listed by name/size only and
# stays available as plain bytes inside the archive (any zip tool
# extracts it). The allowlist is the safety boundary.
_TEXT_ATTACHMENTS = {
    "attachment.system": "System manifest",
    "attachment.perf": "Performance log",
}
_NDJSON_ATTACHMENT = "attachment.structured"
# Structured event logs are NDJSON; render at most this many event rows
# (head + tail slices beyond it) and fall back to raw text — same
# size-gated <pre> as the log — when any line fails to parse.
_NDJSON_MAX_ROWS = 300
_NDJSON_HEAD_ROWS = 200
_NDJSON_TAIL_ROWS = 100


class RunRecordRenderer(BaseRenderer):
    """Program input + log panel for a run.record section."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: RunRecordData | None = None

    def load(self) -> RunRecordData:
        if self._data is None:
            self._data = self.reader.read_run_record(self.section_id)
        return self._data

    def render_to_html(self) -> str:
        """Self-contained scrollable HTML document for the bottom panel."""
        data = self.load()
        meta_rows = _meta_rows(data)
        position, total = self.reader.run_record_position(self.section_id)
        if total > 1:
            label = f"#{position} of {total}"
            label += (
                " — latest" if position == total
                else f" (superseded by run #{total})"
            )
            meta_rows.insert(0, ("Run", label))
        parts = [
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            "html,body{margin:0;padding:0;"
            "font-family:system-ui,sans-serif;}"
            "table.meta{border-collapse:collapse;margin:8px 12px;"
            "font-size:0.85em;}"
            "table.meta td{padding:1px 10px 1px 0;vertical-align:top;}"
            "table.meta td:first-child{opacity:0.6;white-space:nowrap;}"
            "h2{font-size:0.9em;margin:10px 12px 4px;opacity:0.75;}"
            "pre{margin:0 12px 10px;padding:8px 10px;"
            "border:1px solid #ddd;border-radius:4px;overflow:auto;"
            "max-height:45vh;font-family:ui-monospace,Menlo,monospace;"
            "font-size:12px;line-height:1.4;white-space:pre-wrap;"
            "word-break:break-word;}"
            ".elide{margin:0 12px;padding:2px 10px;font-size:0.8em;"
            "opacity:0.6;font-style:italic;}"
            ".warn{margin:8px 12px 2px;padding:6px 10px;font-size:0.85em;"
            "background:#fff3e0;border:1px solid #ffb74d;border-radius:4px;}"
            "table.events{border-collapse:collapse;margin:0 12px 10px;"
            "font-size:11px;font-family:ui-monospace,Menlo,monospace;}"
            "table.events td{padding:1px 8px 1px 0;vertical-align:top;"
            "border-bottom:1px solid #eee;word-break:break-word;}"
            "table.events td:first-child{opacity:0.5;white-space:nowrap;}"
            "</style></head><body>",
        ]
        # Lifecycle consistency: a terminal container whose latest record
        # is incomplete gets an explicit warning instead of silence.
        for warning in self.reader.lifecycle_warnings():
            parts.append(
                f"<div class='warn'>&#9888; {html.escape(warning)}</div>"
            )
        parts.append("<table class='meta'>")
        for key, value in meta_rows:
            parts.append(
                f"<tr><td>{html.escape(key)}</td>"
                f"<td>{html.escape(value)}</td></tr>"
            )
        parts.append("</table>")

        if data.input_text is not None:
            label = "Input"
            filename = _filename(data, "input")
            if filename:
                label += f" — {filename}"
            description = _description(data, "input")
            if description:
                label += f" ({description})"
            parts.append(f"<h2>{html.escape(label)}</h2>")
            parts.append(f"<pre>{html.escape(data.input_text)}</pre>")

        if data.log_text is not None:
            label = "Log"
            filename = _filename(data, "log")
            if filename:
                label += f" — {filename}"
            if data.log_text == "":
                # An empty log member is a legitimate complete record: a
                # run that failed before its output channel opened logged
                # exactly zero bytes (spec § 5.9 container lifecycle).
                label += " (empty — the run ended before output began)"
                parts.append(f"<h2>{html.escape(label)}</h2>")
                parts.append(
                    "<div class='elide'>no output was produced</div>"
                )
            else:
                label += f" ({_human_size(data.log_size)}"
                if _truncated_flag(data):
                    label += ", truncated at archive time"
                label += ")"
                parts.append(f"<h2>{html.escape(label)}</h2>")
                parts.extend(_log_blocks(data.log_text))

        unrendered: list[str] = []
        for role in data.attachment_roles:
            entry = data.files.get(role, {})
            name = entry.get("filename", role)
            rendered = False
            if role in _TEXT_ATTACHMENTS:
                # Allowlisted text sidecars (.system manifest, .perf
                # timing log) render like the log; anything that fails
                # the size/UTF-8 gate falls through to the name listing.
                text = self.reader.read_run_record_attachment(
                    self.section_id, role
                )
                if text is not None:
                    label = _TEXT_ATTACHMENTS[role]
                    if entry.get("filename"):
                        label += f" — {entry['filename']}"
                    parts.append(f"<h2>{html.escape(label)}</h2>")
                    parts.extend(_log_blocks(text))
                    rendered = True
            elif role == _NDJSON_ATTACHMENT:
                text = self.reader.read_run_record_attachment(
                    self.section_id, role
                )
                if text is not None:
                    label = "Structured events"
                    if entry.get("filename"):
                        label += f" — {entry['filename']}"
                    parts.append(f"<h2>{html.escape(label)}</h2>")
                    parts.extend(_ndjson_blocks(text))
                    rendered = True
            if not rendered:
                unrendered.append(str(name))
        if unrendered:
            parts.append("<h2>Attachments (not rendered)</h2>")
            parts.append(
                "<div class='elide'>"
                + html.escape(", ".join(unrendered))
                + " — stored as opaque bytes in the archive; extract "
                "with any zip tool</div>"
            )
        parts.append("</body></html>")
        return "".join(parts)


def _meta_rows(data: RunRecordData) -> list[tuple[str, str]]:
    rows = [("Program", data.program)]
    if data.program_version:
        rows.append(("Version", data.program_version))
    if data.command:
        rows.append(("Command", data.command))
    if data.exit_status is not None:
        rows.append(("Exit status", str(data.exit_status)))
    if data.started_utc:
        rows.append(("Started (UTC)", data.started_utc))
    if data.finished_utc:
        rows.append(("Finished (UTC)", data.finished_utc))
    if data.sequence is not None:
        rows.append(("Sequence", str(data.sequence)))
    return rows


def _filename(data: RunRecordData, role: str) -> str | None:
    entry = data.files.get(role)
    if isinstance(entry, dict):
        name = entry.get("filename")
        return str(name) if name else None
    return None


def _description(data: RunRecordData, role: str) -> str | None:
    entry = data.files.get(role)
    if isinstance(entry, dict):
        text = entry.get("description")
        return str(text) if text else None
    return None


def _truncated_flag(data: RunRecordData) -> bool:
    entry = data.files.get("log")
    return bool(isinstance(entry, dict) and entry.get("truncated"))


def _ndjson_blocks(text: str) -> list[str]:
    """A structured NDJSON event log as a table, or raw text on failure.

    Lines that do not parse as JSON objects are counted and skipped
    rather than discarding the whole table. This is the format's own
    contract — vibe-qc's structured log documents that "a partial write
    at crash-time still leaves earlier records parseable", and a
    crash-truncated tail is exactly when the preceding events matter
    most. Bailing on the first bad line rendered a killed run's whole
    event log as raw text.

    The earlier concern that a partly-parsed table "misrepresents the
    log" is met by stating the skipped count under the table instead of
    hiding it: the reader sees every event that survived *and* is told
    how many lines did not. Only when *nothing* parses does the whole
    attachment go through the raw-text path (`_log_blocks`, which
    carries its own size gate). Row rendering is capped at
    `_NDJSON_MAX_ROWS` (head + tail slices with an elision note), so a
    long run cannot flood the DOM.
    """
    import json as _json

    lines = [ln for ln in text.splitlines() if ln.strip()]
    events: list[dict] = []
    unparsed = 0
    for ln in lines:
        try:
            obj = _json.loads(ln)
        except ValueError:
            unparsed += 1
            continue
        if not isinstance(obj, dict):
            unparsed += 1
            continue
        events.append(obj)
    if not events:
        # Nothing parsed at all: a mislabelled or wholly corrupt file.
        return [
            "<div class='elide'>not valid NDJSON — showing raw "
            "text</div>",
            *_log_blocks(text),
        ]

    def _rows(chunk: list[dict], start: int) -> list[str]:
        out = []
        for i, event in enumerate(chunk, start=start):
            compact = _json.dumps(
                event, separators=(", ", ": "), ensure_ascii=False
            )
            label = str(
                event.get("event") or event.get("kind") or ""
            )
            out.append(
                f"<tr><td>{i}</td>"
                f"<td>{html.escape(label)}</td>"
                f"<td>{html.escape(compact)}</td></tr>"
            )
        return out

    def _unparsed_note() -> list[str]:
        if not unparsed:
            return []
        return [
            f"<div class='elide'>{unparsed:,} line(s) could not be parsed "
            "and were skipped — expected at the tail of a log whose run "
            "was killed mid-write.</div>"
        ]

    parts: list[str] = ["<table class='events'>"]
    if len(events) <= _NDJSON_MAX_ROWS:
        parts.extend(_rows(events, 1))
        parts.append("</table>")
        parts.extend(_unparsed_note())
        return parts
    parts.extend(_rows(events[:_NDJSON_HEAD_ROWS], 1))
    parts.append("</table>")
    skipped = len(events) - _NDJSON_HEAD_ROWS - _NDJSON_TAIL_ROWS
    parts.append(
        f"<div class='elide'>… {skipped:,} events elided for display "
        "— the full event log is stored in the archive …</div>"
    )
    parts.append("<table class='events'>")
    parts.extend(
        _rows(
            events[-_NDJSON_TAIL_ROWS:],
            len(events) - _NDJSON_TAIL_ROWS + 1,
        )
    )
    parts.append("</table>")
    parts.extend(_unparsed_note())
    return parts


def _log_blocks(log_text: str) -> list[str]:
    """The log as one <pre>, or head+tail slices when it is huge."""
    if len(log_text) <= _LOG_RENDER_CAP:
        return [f"<pre>{html.escape(log_text)}</pre>"]
    head = log_text[:_LOG_HEAD_CHARS]
    tail = log_text[-_LOG_TAIL_CHARS:]
    skipped = len(log_text) - len(head) - len(tail)
    return [
        f"<pre>{html.escape(head)}</pre>",
        f"<div class='elide'>… {skipped:,} characters elided for display "
        "— the full log is stored in the archive …</div>",
        f"<pre>{html.escape(tail)}</pre>",
    ]


def _human_size(n_bytes: int) -> str:
    if n_bytes >= 1024 * 1024:
        return f"{n_bytes / (1024 * 1024):.1f} MB"
    if n_bytes >= 1024:
        return f"{n_bytes / 1024:.1f} kB"
    return f"{n_bytes} B"
