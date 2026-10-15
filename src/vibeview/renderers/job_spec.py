"""job.spec renderer — the declarative request a QVF container carries.

The QVF `job.spec` section (spec § 5.9) describes the calculation an
archive *requests*: job type, method, basis, functional, charge,
multiplicity, k-mesh, tasks, and open engine options. Paired with
`provenance.run_status` it tells a pending container ("this job has not
yet run") from a settled one (where the same section records what was
asked). Rendered as a metadata table; the payload is data, never code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibeview.renderers import BaseRenderer
from vibeview.renderers._html import banner, kv_table, panel_document

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

        parts: list[str] = []
        if status == "pending":
            parts.append(
                banner(
                    "This archive describes a job that has not yet run — execute it "
                    "with vibeqc run <file>.qvf.",
                    "pending",
                )
            )
        elif status in ("converged", "failed"):
            parts.append(
                banner(
                    f"This is the request the settled archive was run from (status: {status}).",
                    "done" if status == "converged" else "failed",
                )
            )
        parts.append(kv_table(rows))
        return panel_document("".join(parts))
