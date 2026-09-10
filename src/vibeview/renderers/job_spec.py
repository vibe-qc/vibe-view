"""job.spec renderer — the declarative request a QVF container carries.

The QVF `job.spec` section (spec § 5.9) describes the calculation an
archive *requests*: job type, method, basis, functional, charge,
multiplicity, k-mesh, tasks, and open engine options. Paired with
`provenance.run_status` it tells a pending container ("this job has not
yet run") from a settled one (where the same section records what was
asked). Rendered as a metadata table; the payload is data, never code.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import JobSpecData, QVFReader, Section


class JobSpecRenderer(BaseRenderer):
    """Declarative job-request panel for a job.spec section."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: JobSpecData | None = None

    def load(self) -> JobSpecData:
        if self._data is None:
            self._data = self.reader.read_job_spec(self.section_id)
        return self._data

    def render_to_html(self) -> str:
        data = self.load()
        status = self.reader.run_status or ""
        rows: list[tuple[str, str]] = [("Job type", data.job_type)]
        if data.method:
            rows.append(("Method", str(data.method)))
        if data.functional:
            rows.append(("Functional", str(data.functional)))
        if data.basis:
            rows.append(("Basis", str(data.basis)))
        if data.charge is not None:
            rows.append(("Charge", str(data.charge)))
        if data.multiplicity is not None:
            rows.append(("Multiplicity", str(data.multiplicity)))
        if data.kpoints:
            rows.append(
                ("k-mesh", " × ".join(str(n) for n in data.kpoints))
            )
        rows.append(
            ("Tasks", ", ".join(data.tasks) if data.tasks else "single point")
        )
        for key in sorted(data.options):
            rows.append((f"option: {key}", repr(data.options[key])))

        parts = [
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            "html,body{margin:0;padding:0;"
            "font-family:system-ui,sans-serif;}"
            "table.meta{border-collapse:collapse;margin:8px 12px;"
            "font-size:0.9em;}"
            "table.meta td{padding:2px 12px 2px 0;vertical-align:top;}"
            "table.meta td:first-child{opacity:0.6;white-space:nowrap;}"
            ".banner{margin:10px 12px 2px;padding:6px 10px;"
            "border-radius:4px;font-size:0.9em;}"
            ".pending{background:#fff3e0;border:1px solid #ffb74d;}"
            ".done{background:#e8f5e9;border:1px solid #81c784;}"
            ".failed{background:#ffebee;border:1px solid #e57373;}"
            "</style></head><body>",
        ]
        if status == "pending":
            parts.append(
                "<div class='banner pending'>This archive describes a "
                "job that has <b>not yet run</b> — execute it with "
                "<code>vibeqc run &lt;file&gt;.qvf</code>.</div>"
            )
        elif status in ("converged", "failed"):
            css = "done" if status == "converged" else "failed"
            parts.append(
                f"<div class='banner {css}'>This is the request the "
                f"settled archive was run from (status: {status}).</div>"
            )
        parts.append("<table class='meta'>")
        for key, value in rows:
            parts.append(
                f"<tr><td>{html.escape(key)}</td>"
                f"<td>{html.escape(value)}</td></tr>"
            )
        parts.append("</table></body></html>")
        return "".join(parts)
