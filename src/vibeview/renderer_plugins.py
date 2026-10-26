"""Third-party section renderers for vibe-view (extension API v1).

A renderer package registers one object in the ``vibeview.renderers`` entry
point group, named after the section kind it renders. The object is a
:class:`RendererSpec` or a zero-argument factory returning one. The kind then
classifies as rendered everywhere vibe-view reports a section: the browser
lists it with the plugin's title and icon and shows the plugin's HTML in the
details panel, ``vibe-view info`` and the startup banner count it as rendered,
a ``critical`` section of that kind no longer blocks the open, and the
terminal shows its generic section pane.

Like :mod:`vibeview.importers`, this module is the whole contract and imports
nothing heavy: discovery reads entry-point metadata and loads the plugin
objects, never the viewer stack.

Contract, version 1 (``RENDERER_API_VERSION``); vibe-view 3.x keeps it
compatible:

* ``RendererSpec`` fields as declared here. New fields are added only with
  defaults.
* The renderer class is constructed as ``renderer(section, reader)`` with the
  :class:`~vibeview.qvf.Section` and the :class:`~vibeview.qvf.QVFReader` that
  owns it. ``load()`` returns the section's data, and ``render_to_html()``,
  called with no arguments, returns the HTML document the details panel shows.
  Subclassing :class:`vibeview.renderers.BaseRenderer` is optional.
* A renderer reads its section through ``section.id``, ``section.kind``,
  ``section.members``, ``section.model_extra`` and the reader's verified
  member methods ``read_json_member``, ``read_text_member`` and
  ``read_binary_member``.

Everything else in vibe-view, ``vibeview.app`` included, stays internal.
Representations and 3D actors are not part of version 1.
"""

from __future__ import annotations

import contextlib
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata
from typing import cast

from vibeview.importers import _distribution_name, _error_text

RENDERER_API_VERSION = 1
RENDERER_ENTRY_POINT_GROUP = "vibeview.renderers"

#: The sidebar icon a spec gets when it names none.
DEFAULT_PLUGIN_ICON = "mdi-puzzle-outline"

#: ``classify_section``'s detail for a kind an installed plugin renders.
PLUGIN_DETAIL = "via plugin"


class RendererPluginError(ValueError):
    """A renderer plugin could not be loaded or does not meet the contract."""


@dataclass(frozen=True)
class RendererSpec:
    """Versioned contract implemented by a third-party section renderer.

    ``kind`` is the QVF section kind rendered, and must equal the entry
    point's name. A vendor kind (``x_<vendor>.<name>``) is the usual choice; a
    kind vibe-view renders itself cannot be claimed. ``title`` is the sidebar
    and panel title and ``icon`` a Material Design Icons name (``mdi-...``).
    ``renderer`` is the class described in the module docstring.
    """

    kind: str
    title: str
    renderer: type
    icon: str = DEFAULT_PLUGIN_ICON
    description: str = ""
    api_version: int = RENDERER_API_VERSION


@dataclass(frozen=True)
class RendererStatus:
    """One discovered entry point, including isolated load failures."""

    entry_point: str
    distribution: str | None
    spec: RendererSpec | None
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.spec is not None and self.error is None


def _entry_points() -> tuple[metadata.EntryPoint, ...]:
    """Return renderer entry points across supported importlib APIs."""
    discovered = metadata.entry_points()
    selected: Sequence[metadata.EntryPoint]
    if hasattr(discovered, "select"):
        selected = discovered.select(group=RENDERER_ENTRY_POINT_GROUP)
    else:  # pragma: no cover - compatibility with older importlib-metadata
        legacy = cast(Mapping[str, Sequence[metadata.EntryPoint]], discovered)
        selected = legacy.get(RENDERER_ENTRY_POINT_GROUP, ())
    return tuple(sorted(selected, key=lambda ep: ep.name))


def _builtin_kinds() -> frozenset[str]:
    from vibeview.kinds import RENDERED_VIA_STRUCTURE, SUPPORTED_KINDS

    return SUPPORTED_KINDS | RENDERED_VIA_STRUCTURE


def _validate_spec(value: object, entry_point_name: str) -> RendererSpec:
    if callable(value) and not isinstance(value, (RendererSpec, type)):
        value = value()
    if not isinstance(value, RendererSpec):
        raise TypeError("entry point must expose RendererSpec or a zero-argument factory")

    if type(value.api_version) is not int:
        raise TypeError("api_version must be an integer")
    if value.api_version != RENDERER_API_VERSION:
        raise ValueError(
            f"unsupported renderer API {value.api_version}; "
            f"vibe-view supports {RENDERER_API_VERSION}"
        )
    if not isinstance(value.kind, str):
        raise TypeError("kind must be a string")
    if not value.kind or any(ch.isspace() for ch in value.kind):
        raise ValueError("kind must be a non-empty section kind without whitespace")
    if value.kind != entry_point_name:
        raise ValueError(f"kind {value.kind!r} must match entry point name {entry_point_name!r}")
    if value.kind in _builtin_kinds():
        raise ValueError(f"kind {value.kind!r} is rendered by vibe-view itself")
    if not isinstance(value.title, str):
        raise TypeError("title must be a string")
    if not value.title.strip():
        raise ValueError("title must be non-empty")
    if not isinstance(value.icon, str):
        raise TypeError("icon must be a string")
    if not value.icon.startswith("mdi-") or len(value.icon) == len("mdi-"):
        raise ValueError(f"icon {value.icon!r} must be a Material Design Icons name, 'mdi-...'")
    if not isinstance(value.description, str):
        raise TypeError("description must be a string")
    if not isinstance(value.renderer, type):
        raise TypeError("renderer must be a class")
    for method in ("load", "render_to_html"):
        if not callable(getattr(value.renderer, method, None)):
            raise TypeError(f"renderer class must define {method}()")
    return value


@lru_cache(maxsize=1)
def discover_renderers() -> tuple[RendererStatus, ...]:
    """Load installed renderer plugins, isolating failures per plugin."""
    statuses: list[RendererStatus] = []
    grouped: dict[str, list[metadata.EntryPoint]] = {}
    for entry_point in _entry_points():
        grouped.setdefault(entry_point.name, []).append(entry_point)

    for entry_point_name in sorted(grouped):
        entries = sorted(
            grouped[entry_point_name],
            key=lambda entry_point: _distribution_name(entry_point) or "",
        )
        if len(entries) > 1:
            # Two packages claiming one kind fail closed: neither renders it,
            # and both are reported, rather than one winning by install order.
            registrants = ", ".join(
                repr(_distribution_name(entry_point) or "unknown distribution")
                for entry_point in entries
            )
            error = f"duplicate entry point name {entry_point_name!r}; registered by {registrants}"
            statuses.extend(
                RendererStatus(
                    entry_point=entry_point_name,
                    distribution=_distribution_name(entry_point),
                    spec=None,
                    error=error,
                )
                for entry_point in entries
            )
            continue

        entry_point = entries[0]
        distribution = _distribution_name(entry_point)
        try:
            # Entry-point modules and factories are third-party code: a stray
            # print must not corrupt `vibe-view info --json` or the banner.
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                spec = _validate_spec(entry_point.load(), entry_point.name)
        except (Exception, SystemExit) as exc:
            statuses.append(
                RendererStatus(
                    entry_point=entry_point.name,
                    distribution=distribution,
                    spec=None,
                    error=_error_text(exc),
                )
            )
            continue
        statuses.append(
            RendererStatus(entry_point=entry_point.name, distribution=distribution, spec=spec)
        )
    return tuple(statuses)


def clear_renderer_cache() -> None:
    """Clear discovery state after installing a plugin in this process."""
    discover_renderers.cache_clear()


def available_renderers() -> tuple[RendererSpec, ...]:
    """Return successfully loaded renderer specs in deterministic order."""
    return tuple(status.spec for status in discover_renderers() if status.spec is not None)


def get_renderer_plugin(kind: str) -> RendererSpec | None:
    """Return the installed renderer for ``kind``, or ``None``."""
    for spec in available_renderers():
        if spec.kind == kind:
            return spec
    return None


def plugin_kinds() -> frozenset[str]:
    """The section kinds installed plugins render.

    Never raises: discovery isolates each plugin, and a failure of discovery
    itself leaves the built-in kinds as they are.
    """
    try:
        return frozenset(spec.kind for spec in available_renderers())
    except Exception:  # noqa: BLE001 — classification must not fail on plugins
        return frozenset()


def plugin_report() -> list[dict[str, object]]:
    """One row per discovered entry point, for ``vibe-view doctor``."""
    rows: list[dict[str, object]] = []
    for status in discover_renderers():
        rows.append(
            {
                "kind": status.entry_point,
                "distribution": status.distribution,
                "available": status.available,
                "title": status.spec.title if status.spec else None,
                "error": status.error,
            }
        )
    return rows
