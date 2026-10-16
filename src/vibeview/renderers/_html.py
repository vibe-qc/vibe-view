"""HTML for the details panel: one document, one stylesheet, escaped cells.

Nine table renderers each typed a ``<!doctype html>`` wrapper with its own
drifting CSS, or returned a fragment with inline styles, and three of them
put producer strings into cells unescaped. The panel is a sandboxed
``srcdoc`` iframe, so a stray ``<`` breaks the table rather than the app,
but the tables should not break either. Every panel now builds its body
from these helpers, which escape every cell, and wraps it with
:func:`panel_document`, which carries the one stylesheet.
"""

from __future__ import annotations

import base64
import html
from collections.abc import Mapping, Sequence
from typing import Literal

MISSING = "—"

PANEL_CSS = (
    "html,body{margin:0;padding:0;font-family:system-ui,sans-serif;"
    "font-size:13px;line-height:1.4;}"
    "body{padding:8px 12px;overflow:auto;}"
    "table{border-collapse:collapse;}"
    "th,td{padding:2px 12px 2px 0;text-align:right;vertical-align:top;}"
    "th{border-bottom:1px solid currentColor;text-align:left;font-weight:600;}"
    "td:first-child,th:first-child{text-align:left;}"
    "table.data tr:nth-child(even){background:rgba(128,128,128,0.08);}"
    "table.meta{font-size:0.92em;}"
    "table.meta td{text-align:left;}"
    "table.meta td:first-child{opacity:0.6;white-space:nowrap;}"
    "caption,h2{font-size:0.9em;font-weight:600;text-align:left;margin:10px 0 4px;"
    "opacity:0.8;}"
    "pre{margin:0 0 10px;padding:8px 10px;border:1px solid rgba(128,128,128,0.4);"
    "border-radius:4px;overflow:auto;max-height:45vh;"
    "font-family:ui-monospace,Menlo,monospace;font-size:12px;line-height:1.4;"
    "white-space:pre-wrap;word-break:break-word;}"
    "td pre{margin:0;padding:0;border:0;max-height:none;font-size:0.9em;}"
    ".strip{padding:6px 0;opacity:0.9;font-size:0.92em;}"
    ".strip span{opacity:0.7;}"
    ".banner{margin:2px 0 8px;padding:6px 10px;border-radius:4px;font-size:0.9em;}"
    ".pending{background:#fff3e0;border:1px solid #ffb74d;color:#333;}"
    ".done{background:#e8f5e9;border:1px solid #81c784;color:#333;}"
    ".failed{background:#ffebee;border:1px solid #e57373;color:#333;}"
    ".warn{background:#fff3e0;border:1px solid #ffb74d;color:#333;}"
    ".elide{padding:2px 0;font-size:0.8em;opacity:0.6;font-style:italic;}"
    ".empty{opacity:0.7;}"
    "a.csv{color:#88aacc;text-decoration:none;font-size:11px;}"
    "table.events{font-size:11px;font-family:ui-monospace,Menlo,monospace;}"
    "table.events td{text-align:left;padding:1px 8px 1px 0;"
    "border-bottom:1px solid rgba(128,128,128,0.3);word-break:break-word;}"
    "table.events td:first-child{opacity:0.5;white-space:nowrap;}"
    "details{font-size:0.9em;margin:0 0 8px;}"
    "details pre{max-height:220px;}"
    "p.note{margin:4px 0;font-size:11px;opacity:0.7;}"
)

Banner = Literal["pending", "done", "failed", "warn"]


def panel_document(body: str, *, extra_css: str = "") -> str:
    """A complete document for the details iframe around an HTML ``body``."""
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        f"{PANEL_CSS}{extra_css}"
        "</style></head><body>"
        f"{body}"
        "</body></html>"
    )


def cell(value: object, *, numeric_format: str = ".4f") -> str:
    """Escaped cell text: floats formatted, lists joined, None a dash."""
    if value is None:
        return MISSING
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return format(value, numeric_format)
    if isinstance(value, list | tuple):
        return ", ".join(cell(item, numeric_format=numeric_format) for item in value)
    return html.escape(str(value))


def kv_table(rows: Sequence[tuple[str, object]], *, block_values: bool = False) -> str:
    """A two-column key/value table; block values render as ``<pre>``."""
    if not rows:
        return ""
    out = ["<table class='meta'>"]
    for key, value in rows:
        text = cell(value)
        if block_values or "\n" in text:
            text = f"<pre>{text}</pre>"
        out.append(f"<tr><td>{html.escape(str(key))}</td><td>{text}</td></tr>")
    out.append("</table>")
    return "".join(out)


def data_table(
    columns: Sequence[str],
    rows: Sequence[Mapping[str, object] | Sequence[object]],
    *,
    caption: str = "",
    numeric_format: str = ".4f",
    footer: Sequence[object] | None = None,
) -> str:
    """A header-and-rows table. Mapping rows fill by column name, missing → dash.

    Rows that carry none of ``columns`` are skipped; with no rows left the
    result is empty so the caller can omit the section.
    """
    body: list[str] = []
    for row in rows:
        if isinstance(row, Mapping):
            if not any(name in row for name in columns):
                continue
            values = [row.get(name) for name in columns]
        else:
            values = list(row)
        cells = "".join(f"<td>{cell(v, numeric_format=numeric_format)}</td>" for v in values)
        body.append(f"<tr>{cells}</tr>")
    if not body:
        return ""
    head = "".join(f"<th>{html.escape(name)}</th>" for name in columns)
    parts = ["<table class='data'>"]
    if caption:
        parts.append(f"<caption>{html.escape(caption)}</caption>")
    parts.append(f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody>")
    if footer is not None:
        cells = "".join(f"<td>{cell(v, numeric_format=numeric_format)}</td>" for v in footer)
        parts.append(f"<tfoot><tr>{cells}</tr></tfoot>")
    parts.append("</table>")
    return "".join(parts)


def kv_strip(items: Mapping[str, object]) -> str:
    """A one-line ``key: value · key: value`` strip; None values are left out."""
    parts = [
        f"<span>{html.escape(str(k))}:</span> <strong>{cell(v)}</strong>"
        for k, v in items.items()
        if v is not None
    ]
    if not parts:
        return ""
    return "<div class='strip'>" + " &nbsp;·&nbsp; ".join(parts) + "</div>"


def banner(message: str, kind: Banner) -> str:
    """A status banner: pending, done, failed or warn."""
    return f"<div class='banner {kind}'>{html.escape(message)}</div>"


def empty_panel(message: str) -> str:
    """A complete document saying why there is nothing to show."""
    return panel_document(f"<p class='empty'>{html.escape(message)}</p>")


def csv_download_link(text: str, filename: str) -> str:
    """A data-URL download link for ``text`` as ``filename``."""
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return (
        f"<p><a class='csv' href='data:text/csv;base64,{encoded}' "
        f"download='{html.escape(filename, quote=True)}'>⬇ Download CSV</a></p>"
    )


def heading(text: str) -> str:
    """A section heading inside a panel."""
    return f"<h2>{html.escape(text)}</h2>"


def elide(text: str) -> str:
    """A muted note about content left out of the panel."""
    return f"<div class='elide'>{html.escape(text)}</div>"


def column_order(rows: Sequence[Mapping[str, object]], preferred: Sequence[str]) -> list[str]:
    """Column order for a table of loosely keyed rows.

    The ``preferred`` keys that any row carries come first, in that order,
    then every other key in encounter order, so a producer's extra fields
    are shown rather than dropped.
    """
    columns: list[str] = [key for key in preferred if any(key in row for row in rows)]
    seen = set(columns)
    for row in rows:
        for key in row:
            if key not in seen:
                columns.append(key)
                seen.add(key)
    return columns


def note(text: str) -> str:
    """A small muted line, for counts and thresholds above a table."""
    return f"<p class='note'>{html.escape(text)}</p>"


def preformatted(text: str) -> str:
    """Escaped monospace text, scrolling when long."""
    return f"<pre>{html.escape(text)}</pre>"


def details_block(summary: str, body: str) -> str:
    """A collapsed ``<details>`` with an escaped summary around ready HTML."""
    return f"<details><summary>{html.escape(summary)}</summary>{body}</details>"
