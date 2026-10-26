"""A sample vibe-view renderer plugin (extension API v1).

It renders the vendor section kind ``x_example.timings``: one JSON member,
``steps``, holding a list of ``{"step": <name>, "seconds": <number>}``
records, shown as a table in the browser's details panel. It uses only the
public contract in :mod:`vibeview.renderer_plugins`.
"""

from __future__ import annotations

import html
from typing import Any

from vibeview.renderer_plugins import RENDERER_API_VERSION, RendererSpec

KIND = "x_example.timings"

_STYLE = """
:root { color-scheme: light dark; font: 14px system-ui, sans-serif; }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 4px 8px; border-bottom: 1px solid #8884; }
th { text-align: left; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
caption { text-align: left; font-weight: 600; padding-bottom: 6px; }
"""


class TimingsRenderer:
    """Constructed with the section and its reader, as the contract states."""

    def __init__(self, section: Any, reader: Any) -> None:
        self.section = section
        self.reader = reader
        self._steps: list[tuple[str, float]] | None = None

    def load(self) -> list[tuple[str, float]]:
        """The ``(step, seconds)`` rows, read once from the verified member."""
        if self._steps is None:
            raw = self.reader.read_json_member(self.section.id, "steps")
            if not isinstance(raw, list):
                raise ValueError("member 'steps' must be a JSON list")
            rows = []
            for record in raw:
                if not isinstance(record, dict):
                    raise ValueError("each step must be a JSON object")
                seconds = record.get("seconds")
                if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
                    raise ValueError("each step needs a numeric 'seconds'")
                rows.append((str(record.get("step", "")), float(seconds)))
            self._steps = rows
        return self._steps

    def render_to_html(self) -> str:
        """A complete HTML document; archive text is escaped."""
        steps = self.load()
        total = sum(seconds for _step, seconds in steps)
        label = (self.section.model_extra or {}).get("label")
        caption = html.escape(label if isinstance(label, str) and label else "Step timings")
        body = "".join(
            f"<tr><td>{html.escape(step)}</td><td class='num'>{seconds:.3f}</td>"
            f"<td class='num'>{(100 * seconds / total if total else 0):.1f} %</td></tr>"
            for step, seconds in steps
        )
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{_STYLE}</style></head><body><table>"
            f"<caption>{caption}</caption>"
            "<thead><tr><th>Step</th><th>Time (s)</th><th>Share</th></tr></thead>"
            f"<tbody>{body}</tbody>"
            f"<tfoot><tr><th>Total</th><td class='num'>{total:.3f}</td><td></td></tr></tfoot>"
            "</table></body></html>"
        )


def renderer() -> RendererSpec:
    """The entry point: a zero-argument factory returning the spec."""
    return RendererSpec(
        kind=KIND,
        title="Step Timings",
        renderer=TimingsRenderer,
        icon="mdi-timer-outline",
        description="Wall-clock time per calculation step, as a table",
        api_version=RENDERER_API_VERSION,
    )
