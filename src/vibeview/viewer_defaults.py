"""Parse and apply viewer_defaults hints from manifest.json.

Viewer defaults are suggestions from the file producer, never hard
settings. The UI can override every one of them. This module provides
a mutable state object that the Trame app reads from and writes to.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from jsonschema import Draft202012Validator

from vibeview.energy_window import parse_energy_window
from vibeview.qvf import ViewerDefaults, _load_manifest_schema

#: How a volume section is drawn: the contour, or the field itself.
RENDER_MODES: tuple[str, ...] = ("isosurface", "volume")

# Use the pinned master's definition unchanged; malformed advisory hints
# must not invalidate an otherwise readable archive (QVF § 3.4).
_CAMERA_VALIDATOR = Draft202012Validator({
    "$ref": "#/$defs/Camera", "$defs": _load_manifest_schema()["$defs"],
})


def _camera_hint(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not _CAMERA_VALIDATOR.is_valid(value):
        return None
    # JSON Schema's number type also accepts Python NaN/Inf. Neither can
    # be sent to VTK as a usable camera coordinate or projection value.
    numbers = [part for key in ("position", "focal_point", "view_up") for part in value[key]]
    numbers.append(value.get("view_angle", value.get("parallel_scale")))
    if any(_finite(number) is None for number in numbers):
        return None
    return deepcopy(value)


@dataclass
class VolumeHints:
    """Mutable hints for a single volume section.

    ``render`` and ``transfer_function`` belong to direct volume rendering
    (docs/volume_rendering_design.md): the transfer function is the JSON
    the viewer edits, with ``range`` in data units, ``color`` nodes
    ``[x, r, g, b]`` and ``opacity`` nodes ``[x, a]``; ``None`` means the
    kind's default. Both are per section, like every other hint.
    """

    isovalue: float = 0.05
    colormap: str = "viridis"
    opacity: float = 0.6
    render: str = "isosurface"
    transfer_function: dict[str, Any] | None = None


def render_mode(value: Any, default: str = "isosurface") -> str:
    """``value`` as a render mode, or ``default`` when it is not one."""
    mode = str(value or "").strip().lower()
    return mode if mode in RENDER_MODES else default


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def validate_transfer_function(value: Any) -> dict[str, Any] | None:
    """``value`` as a clean transfer function, or None when it is not one.

    Accepts the JSON the viewer writes and a producer's hint: a ``range``
    of two finite numbers in order, at least one ``color`` node of four
    numbers with the colour in [0, 1], at least one ``opacity`` node of two
    numbers with the opacity in [0, 1]; ``preset`` and ``colormap`` are
    optional strings. Node containers must be lists or tuples; malformed
    containers drop the hint. Individual invalid nodes are dropped and
    valid nodes are sorted by position, so a hint cannot break a file.
    """
    if not isinstance(value, dict):
        return None
    rng = value.get("range")
    if not isinstance(rng, (list, tuple)) or len(rng) != 2:
        return None
    low, high = _finite(rng[0]), _finite(rng[1])
    if low is None or high is None or high <= low:
        return None
    color_nodes, opacity_nodes = value.get("color"), value.get("opacity")
    if not isinstance(color_nodes, (list, tuple)) or not isinstance(opacity_nodes, (list, tuple)):
        return None
    colors: list[list[float]] = []
    for node in color_nodes:
        if not isinstance(node, (list, tuple)) or len(node) != 4:
            continue
        parts = [_finite(part) for part in node]
        if any(part is None for part in parts):
            continue
        x, r, g, b = (float(part) for part in parts if part is not None)
        if all(0.0 <= channel <= 1.0 for channel in (r, g, b)):
            colors.append([x, r, g, b])
    alphas: list[list[float]] = []
    for node in opacity_nodes:
        if not isinstance(node, (list, tuple)) or len(node) != 2:
            continue
        position, alpha = _finite(node[0]), _finite(node[1])
        if position is None or alpha is None or not 0.0 <= alpha <= 1.0:
            continue
        alphas.append([position, alpha])
    if not colors or not alphas:
        return None
    clean: dict[str, Any] = {
        "range": [low, high],
        "color": sorted(colors, key=lambda node: node[0]),
        "opacity": sorted(alphas, key=lambda node: node[0]),
    }
    for key in ("preset", "colormap"):
        text = value.get(key)
        if isinstance(text, str) and text:
            clean[key] = text
    return clean


# Kind-specific default hints (diverging for difference densities, etc.).
_KIND_DEFAULT_HINTS: dict[str, VolumeHints] = {
    "volume.difference": VolumeHints(
        isovalue=0.005,
        colormap="RdBu",
        opacity=0.6,
    ),
    "volume.spin": VolumeHints(
        isovalue=0.005,
        colormap="RdBu",
        opacity=0.6,
    ),
    "volume.density": VolumeHints(
        isovalue=0.05,
        colormap="viridis",
        opacity=0.6,
    ),
    "volume.orbital": VolumeHints(
        isovalue=0.05,
        colormap="coolwarm",
        opacity=0.6,
    ),
    "volume.elf": VolumeHints(
        isovalue=0.5,
        colormap="plasma",
        opacity=0.6,
    ),
    # Electrostatic potential is a *signed* field; diverging colormap +
    # the ±-lobe render path (see app._is_signed) show both signs.
    "volume.potential": VolumeHints(
        isovalue=0.03,
        colormap="coolwarm",
        opacity=0.6,
    ),
    # Reduced density gradient (NCI). The isovalue is the RDG s(r) surface
    # (NCIPLOT default 0.3); the colormap hint is unused — NCI colors by
    # sign(λ₂)ρ with its own blue-green-red map (renderers/nci.nci_colormap).
    "volume.rdg": VolumeHints(
        isovalue=0.3,
        colormap="coolwarm",
        opacity=0.65,
    ),
}


def _default_for_kind(kind: str) -> VolumeHints:
    """Return fresh mutable hints without sharing the kind-default template."""
    if kind in _KIND_DEFAULT_HINTS:
        return replace(_KIND_DEFAULT_HINTS[kind])
    return VolumeHints()


def section_volume_hints(reader: Any, section_id: str) -> VolumeHints:
    """The hints a volume section opens with: the manifest's, over its kind's default.

    The one lookup for every surface that renders a volume without a user
    choice -- the browser viewer, ``vibe-view capture`` and ``vibe-view
    batch``. Each of the last two used to keep its own table, so a spin
    density captured at 0.05/coolwarm while the viewer opened it at
    0.005/RdBu (#39-6).
    """
    kinds = {s.id: s.kind for s in reader.sections}
    state = ViewerState.from_manifest(getattr(reader, "viewer_defaults", None), kinds)
    return replace(state.get_volume_hints(section_id))


@dataclass
class Bookmark:
    """One camera bookmark from viewer_defaults.bookmarks."""

    name: str
    camera: dict[str, Any]
    description: str = ""


@dataclass
class ViewerState:
    """Mutable viewer state initialised from viewer_defaults.

    The Trame app reads these values to set initial slider/dropdown
    positions, and writes back when the user changes them.
    """

    auto_open: list[str] = field(default_factory=list)
    replication: tuple[int, int, int] = (1, 1, 1)
    volume_hints: dict[str, VolumeHints] = field(default_factory=dict)
    # Which hint keys the manifest *explicitly* set per section, so
    # get_volume_hints can fill the rest from the kind-specific default
    # rather than the generic VolumeHints() default (see that method).
    # Section id to kind, for the kind defaults of sections the manifest
    # gave no hints for (filled by from_manifest).
    section_kinds: dict[str, str] = field(default_factory=dict)
    camera: dict[str, Any] | None = None
    bookmarks: list[Bookmark] = field(default_factory=list)
    # Per-section ``energy_window`` hints for the bands / DOS charts, as
    # (min, max) in eV relative to E_F (#26). A producer that knows which
    # part of its spectrum matters can ship a sensible first view; the
    # viewer's own controls override it like any other hint.
    energy_windows: dict[str, tuple[float, float]] = field(default_factory=dict)
    crossfade_blend: float = 0.5
    crossfade_volumes: tuple[str, str] | None = None

    # ── Mesh cache (A7-02) ───────────────────────────────────────────
    # Stores the last marching-cubes result per volume section so that
    # colormap / opacity slider changes can reuse the existing mesh
    # rather than re-marching. The cache is invalidated when isovalue or
    # replication changes (encoded in the cache key) and cleared
    # automatically when a new file is loaded (ViewerState is recreated
    # by _reload_active_file — no explicit eviction needed).
    #
    # Unsigned volumes: {"key": str, "mesh": pv.PolyData | None}
    # Signed volumes:   {"key": str, "pos":  pv.PolyData | None,
    #                               "neg":  pv.PolyData | None}
    #
    # A key of None means "not yet cached", an empty mesh (n_points==0)
    # means the march succeeded but produced no geometry (isovalue out
    # of range) — both are valid cached states.
    _mesh_cache: dict[str, dict[str, Any]] = field(default_factory=dict)

    def mesh_cache_key(self, isovalue: float, *, periodic_replication: int = 0) -> str:
        """Stable string key that encodes the marching-cubes inputs."""
        return f"{isovalue:.6g}:{self.replication}:pr{periodic_replication}"

    def get_cached_mesh(
        self, section_id: str, isovalue: float, *, periodic_replication: int = 0
    ) -> dict[str, Any] | None:
        """Return the cache entry for a section if the key still matches,
        else None (caller must re-march and call ``put_cached_mesh``)."""
        entry = self._mesh_cache.get(section_id)
        if entry and entry.get("key") == self.mesh_cache_key(
            isovalue, periodic_replication=periodic_replication
        ):
            return entry
        return None

    def put_cached_mesh(
        self, section_id: str, isovalue: float, *, periodic_replication: int = 0, **meshes: Any
    ) -> None:
        """Store mesh(es) under the current isovalue + replication key.

        Pass ``mesh=...`` for unsigned volumes, ``pos=...`` / ``neg=...``
        for signed volumes. ``None`` is a valid value (no geometry at this
        isovalue).
        """
        self._mesh_cache[section_id] = {
            "key": self.mesh_cache_key(isovalue, periodic_replication=periodic_replication),
            **meshes,
        }

    def invalidate_mesh_cache(self, section_id: str) -> None:
        """Remove a section from the mesh cache (forces re-march)."""
        self._mesh_cache.pop(section_id, None)

    @classmethod
    def from_manifest(
        cls,
        defaults: ViewerDefaults | None,
        section_kinds: Mapping[str, str] | None = None,
    ) -> ViewerState:
        """Build initial state from manifest viewer_defaults.

        ``section_kinds`` (section id to kind) lets each volume's hints be
        resolved here, once: the kind's defaults, overridden by whichever
        keys the manifest set. A section the manifest did not mention gets
        its kind's defaults on first lookup.
        """
        state = cls()
        state.section_kinds = dict(section_kinds or {})
        if defaults is None:
            return state

        state.auto_open = list(defaults.auto_open)

        # Camera hints: position, focal_point, view_up, view_angle.
        # Copy: popping directly from model_extra would mutate the pydantic
        # model in place, so a second from_manifest() on the same manifest
        # (e.g. app._reload_active_file) would lose all hints.
        extras = dict(getattr(defaults, "model_extra", None) or {})
        state.camera = _camera_hint(extras.pop("camera", None))

        # Bookmarks: ordered list of {name, camera, description?}
        # The schema treats `bookmarks` as a top-level key on
        # viewer_defaults, so Pydantic's extra="allow" captures it.
        bookmarks = extras.pop("bookmarks", None)
        if isinstance(bookmarks, list):
            for bm in bookmarks:
                if not isinstance(bm, dict):
                    continue
                name = bm.get("name")
                cam = _camera_hint(bm.get("camera"))
                if not isinstance(name, str) or cam is None:
                    continue
                state.bookmarks.append(
                    Bookmark(
                        name=name,
                        camera=cam,
                        description=str(bm.get("description", "")),
                    )
                )
        # Use the first bookmark as the initial camera if no `camera` hint set.
        if state.camera is None and state.bookmarks:
            state.camera = state.bookmarks[0].camera

        # Cross-fade between two volume sections
        cf = extras.pop("crossfade", None)
        if isinstance(cf, dict) and "a" in cf and "b" in cf:
            state.crossfade_volumes = (str(cf["a"]), str(cf["b"]))
            blend = _finite(cf.get("blend", 0.5))
            if blend is not None and 0.0 <= blend <= 1.0:
                state.crossfade_blend = blend

        # Per-section hints come from extra keys on the ViewerDefaults model
        # (captured via Pydantic's extra="allow" in model_extra).
        replication_set = False
        for section_id, hints in extras.items():
            if isinstance(hints, dict):
                base = _default_for_kind(state.section_kinds.get(section_id, ""))
                state.volume_hints[section_id] = VolumeHints(
                    isovalue=float(hints.get("isovalue", base.isovalue)),
                    colormap=str(hints.get("colormap", base.colormap)),
                    opacity=float(hints.get("opacity", base.opacity)),
                    render=render_mode(hints.get("render"), base.render),
                    transfer_function=validate_transfer_function(
                        hints.get("transfer_function")
                    ),
                )
                window = parse_energy_window(hints.get("energy_window"))
                if window is not None:
                    state.energy_windows[section_id] = window
                # The viewer has one supercell, so a replication hint is
                # global: the first section in manifest order that carries one
                # sets it, and later ones are ignored (they used to overwrite
                # it, so the last section decided).
                rep = hints.get("replication")
                if not replication_set and isinstance(rep, list) and len(rep) == 3:
                    state.replication = (int(rep[0]), int(rep[1]), int(rep[2]))
                    replication_set = True

        return state

    def get_energy_window(self, *section_ids: str) -> tuple[float, float] | None:
        """The manifest's energy-window hint for the first section that has one.

        The combined bands+DOS panel draws two sections on one axis, so it
        asks for both ids and takes whichever the producer hinted.
        """
        for section_id in section_ids:
            window = self.energy_windows.get(section_id)
            if window is not None:
                return window
        return None

    def get_volume_hints(self, section_id: str, kind: str = "") -> VolumeHints:
        """Hints for a volume section: the manifest's, merged over its kind's defaults.

        The merge happened in :meth:`from_manifest`. A section the manifest
        did not mention gets its kind's defaults here, from ``kind`` or the
        kind recorded at construction, and keeps them (the returned object is
        the one the sliders edit).
        """
        hints = self.volume_hints.get(section_id)
        if hints is None:
            hints = _default_for_kind(kind or self.section_kinds.get(section_id, ""))
            self.volume_hints[section_id] = hints
        return hints
