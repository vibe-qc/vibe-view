"""Summary banner — prints a table of every section on file open (§2.4 step 2)."""

from __future__ import annotations

from vibeview.kinds import classify_section
from vibeview.qvf import QVFReader


def format_banner(reader: QVFReader) -> str:
    """Format a human-readable summary banner for stdout.

    Lists every section by id and kind, marking each as:
    - rendered (kind in supported set)
    - skipped, unsupported (kind not recognised)
    - skipped, vendor namespace (x_<vendor>.*)
    - error, sha256 mismatch (if the section failed verification)

    The banner is printed at file-open time before the Trame server
    starts, so the user can see what sections are available and which
    were skipped.
    """
    def _row(text: str) -> str:
        # Border lines are 80 chars; every body row must match:
        # "║  " (3) + 75 content + " ║" (2) = 80. (The old per-line
        # format strings summed to 70/75/82 chars — visibly ragged box.)
        if len(text) > 75:
            text = text[:72] + "..."
        return f"║  {text:<75s} ║"

    lines: list[str] = []
    lines.append("")
    lines.append("╔" + "═" * 78 + "╗")
    label = reader.path.name if reader.path is not None else "<in-memory>"
    lines.append(_row(f"QVF file: {label}"))
    lines.append(
        _row(
            f"Source:   {reader.source.program} {reader.source.version}"
            f" — {reader.source.calculation}"
        )
    )
    lines.append("╠" + "═" * 78 + "╣")
    lines.append(_row(f"{'Section ID':<20s} {'Kind':<28s} {'Status'}"))
    lines.append("╠" + "═" * 78 + "╣")

    for section in reader.sections:
        section_id = section.id
        error = reader.section_error(section_id)
        if error is not None:
            status = "error, sha256 mismatch"
            detail = error
        else:
            status, detail = classify_section(section.kind)
            if detail is not None:
                status = f"{status}, {detail}"

        # Truncate long ids
        display_id = section_id if len(section_id) <= 20 else section_id[:17] + "..."
        display_kind = section.kind if len(section.kind) <= 28 else section.kind[:25] + "..."

        lines.append(_row(f"{display_id:<20s} {display_kind:<28s} {status}"))

    lines.append("╚" + "═" * 78 + "╝")
    lines.append("")

    # Count summary
    n_rendered = sum(
        1
        for s in reader.sections
        if classify_section(s.kind)[0] == "rendered" and reader.section_error(s.id) is None
    )
    n_skipped = sum(
        1
        for s in reader.sections
        if classify_section(s.kind)[0] == "skipped" and reader.section_error(s.id) is None
    )
    n_errors = sum(1 for s in reader.sections if reader.section_error(s.id) is not None)
    lines.append(
        f"  {n_rendered} section(s) will be rendered, {n_skipped} skipped, {n_errors} error(s)"
    )
    lines.append("")

    return "\n".join(lines)


def print_banner(reader: QVFReader) -> None:
    """Print the summary banner to stdout."""
    print(format_banner(reader))
