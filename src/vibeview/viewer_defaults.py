"""Parse and apply viewer_defaults hints from manifest.json.

Viewer defaults are suggestions from the file producer, never hard
settings. The UI can override every one of them. This module provides
a mutable state object that the Trame app reads from and writes to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vibeview.qvf import ViewerDefaults


@dataclass
class VolumeHints:
    """Mutable hints for a single volume section."""

    isovalue: float = 0.05
    colormap: str = "viridis"
    opacity: float = 0.6


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
    """Return sensible VolumeHints for a given kind."""
    if kind in _KIND_DEFAULT_HINTS:
        return _KIND_DEFAULT_HINTS[kind]
    return VolumeHints()


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
    _provided_hint_keys: dict[str, set[str]] = field(default_factory=dict)
    camera: dict[str, Any] | None = None
    bookmarks: list[Bookmark] = field(default_factory=list)
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
    ) -> dict | None:
        """Return the cache entry for a section if the key still matches,
        else None (caller must re-march and call ``put_cached_mesh``)."""
        entry = self._mesh_cache.get(section_id)
        if entry and entry.get("key") == self.mesh_cache_key(
            isovalue, periodic_replication=periodic_replication
        ):
            return entry
        return None

    def put_cached_mesh(
        self, section_id: str, isovalue: float, *, periodic_replication: int = 0, **meshes
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
    def from_manifest(cls, defaults: ViewerDefaults | None) -> ViewerState:
        """Build initial state from manifest viewer_defaults."""
        state = cls()
        if defaults is None:
            return state

        state.auto_open = list(defaults.auto_open)

        # Camera hints: position, focal_point, view_up, view_angle.
        # Copy: popping directly from model_extra would mutate the pydantic
        # model in place, so a second from_manifest() on the same manifest
        # (e.g. app._reload_active_file) would lose all hints.
        extras = dict(getattr(defaults, "model_extra", None) or {})
        cam = extras.pop("camera", None)
        if isinstance(cam, dict):
            state.camera = cam

        # Bookmarks: ordered list of {name, camera, description?}
        # The schema treats `bookmarks` as a top-level key on
        # viewer_defaults, so Pydantic's extra="allow" captures it.
        bookmarks = extras.pop("bookmarks", None)
        if isinstance(bookmarks, list):
            for bm in bookmarks:
                if not isinstance(bm, dict):
                    continue
                name = bm.get("name")
                cam = bm.get("camera")
                if not isinstance(name, str) or not isinstance(cam, dict):
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
            state.crossfade_blend = float(cf.get("blend", 0.5))

        # Per-section hints come from extra keys on the ViewerDefaults model
        # (captured via Pydantic's extra="allow" in model_extra).
        for section_id, hints in extras.items():
            if isinstance(hints, dict):
                state.volume_hints[section_id] = VolumeHints(
                    isovalue=float(hints.get("isovalue", 0.05)),
                    colormap=str(hints.get("colormap", "viridis")),
                    opacity=float(hints.get("opacity", 0.6)),
                )
                state._provided_hint_keys[section_id] = {
                    k for k in ("isovalue", "colormap", "opacity") if k in hints
                }
                # Global replication hint (last one wins, or use the first)
                if "replication" in hints:
                    rep = hints["replication"]
                    if isinstance(rep, list) and len(rep) == 3:
                        state.replication = (int(rep[0]), int(rep[1]), int(rep[2]))

        return state

    def get_volume_hints(self, section_id: str, kind: str = "") -> VolumeHints:
        """Get hints for a volume section, with kind-specific defaults for
        anything the manifest didn't explicitly set.

        Previously, a manifest that set *any* hint key for a section (even
        just ``opacity``) caused the whole VolumeHints to use the generic
        0.05/viridis fallback — so a ``volume.difference`` / ``volume.spin``
        section lost its diverging-colormap / small-isovalue kind default
        (0.005 / RdBu) and rendered a likely-empty or misleading isosurface.
        We now merge: kind default as the base, manifest-provided keys
        override.
        """
        if section_id not in self.volume_hints:
            self.volume_hints[section_id] = _default_for_kind(kind)
            return self.volume_hints[section_id]

        provided = self._provided_hint_keys.get(section_id)
        if kind and provided is not None and provided != {"isovalue", "colormap", "opacity"}:
            base = _default_for_kind(kind)
            current = self.volume_hints[section_id]
            merged = VolumeHints(
                isovalue=current.isovalue if "isovalue" in provided else base.isovalue,
                colormap=current.colormap if "colormap" in provided else base.colormap,
                opacity=current.opacity if "opacity" in provided else base.opacity,
            )
            self.volume_hints[section_id] = merged
            # Mark fully-resolved so a second call doesn't re-merge.
            self._provided_hint_keys[section_id] = {"isovalue", "colormap", "opacity"}
        return self.volume_hints[section_id]
