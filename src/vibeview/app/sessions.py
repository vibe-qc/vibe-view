"""Sessions controllers for one browser viewer instance."""

from __future__ import annotations

import asyncio
import contextlib
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyvista.plotting.colors import get_cmap_safe

from vibeview.plotter_compat import (
    cmap_argument,
)
from vibeview.qvf import clamp_replication

if TYPE_CHECKING:
    from vibeview.qvf import Section


from vibeview.app import helpers as _helpers

if TYPE_CHECKING:
    from vibeview.app.runtime import ViewerRuntime


class SessionsControllers:
    def __init__(self, viewer: ViewerRuntime) -> None:
        self.viewer = viewer

    def apply_bookmark(self, name: str) -> None:
        for bm in self.viewer.viewer_state.bookmarks:
            if bm.name == name:
                _helpers._apply_camera(self.viewer.plotter, bm.camera)
                _helpers._sync_orthographic_switch(self.viewer.plotter, self.viewer.server.state)
                self.viewer.plotter.render()
                self.viewer.server.state.status_message = f"Camera → {name}"
                return
        self.viewer.server.state.status_message = f"Unknown bookmark: {name}"

    def update_crossfade(self, blend: float) -> None:
        state = self.viewer.server.state
        state.crossfade_blend = blend
        self.viewer.viewer_state.crossfade_blend = blend
        if self.viewer.viewer_state.crossfade_volumes:
            va, vb = self.viewer.viewer_state.crossfade_volumes
            # Build a blended view: rebuild both volumes with blend opacity
            _helpers._rebuild_crossfade(
                self.viewer.reader,
                self.viewer.plotter,
                self.viewer.viewer_state,
                state,
                va,
                vb,
                blend,
            )

    def sync_client_camera(
        self,
        position: Any = None,
        focal_point: Any = None,
        view_up: Any = None,
        parallel_scale: Any = None,
    ) -> None:
        """Copy the client's camera onto the server plotter after a drag.

        VtkLocalView renders client-side and the client owns the camera;
        ``push_camera`` only goes the other way. Without this, everything
        that reads ``plotter`` for "the current view" saw whatever the last
        server-side ``reset_camera`` left — measured: rotating until 18% of
        the viewport's pixels changed, then saving two bookmarks either
        side of the rotation, produced byte-identical cameras.

        Fires once per interaction end, not per mouse move, so writing the
        plotter here is cheap. Kept deliberately tolerant: a malformed
        payload must never break interaction, and the previous camera is a
        safe thing to keep.
        """
        try:
            pos = [float(v) for v in position]
            foc = [float(v) for v in focal_point]
            up = [float(v) for v in view_up]
        except (TypeError, ValueError):
            return
        if not (len(pos) == len(foc) == len(up) == 3):
            return
        self.viewer.plotter.camera_position = [pos, foc, up]
        # Labels are polygonal text, so facing the viewer is geometry, not a
        # render flag: they have to be rebuilt when the viewpoint moves
        # (roadmap D5). Only the label actors are re-meshed, and only once
        # per interaction end, for the server-side renders (screenshots,
        # video frames). The client drew its own copy facing the camera
        # throughout the interaction (#111), so the rebuilt geometry is not
        # pushed back: the next scene push for any other reason carries it.
        from vibeview.renderers.structure import reorient_labels

        reorient_labels(self.viewer.plotter)
        if parallel_scale is not None:
            # A junk scale must not discard an otherwise good pose.
            with contextlib.suppress(TypeError, ValueError):
                self.viewer.plotter.camera.parallel_scale = float(parallel_scale)

    def save_user_bookmark(self, name: str) -> None:
        state = self.viewer.server.state
        if not name or not name.strip():
            state.status_message = "Bookmark name required"
            return
        name = name.strip()
        cam = _helpers._camera_to_dict(self.viewer.plotter)
        bm = {
            "name": name,
            "camera": cam,
            # The open section is `selected_section`; `active_section` is not a
            # declared state key, so this recorded "" in every bookmark — which
            # also starved the presentation slides that read section_id.
            "section_id": state.selected_section or "",
            "isovalue": state.isovalue,
            "colormap": state.colormap,
            "opacity": state.opacity,
            **self.viewer.sessions._saved_view_extras(),
        }
        existing = [i for i, b in enumerate(state.user_bookmarks) if b["name"] == name]
        if existing:
            state.user_bookmarks[existing[0]] = bm
        else:
            state.user_bookmarks.append(bm)
        # Both branches mutate the list in place, which trame cannot see:
        # the snapshot it pushed to the client aliases this same list, so
        # nothing is dirty and the client's copy stays at whatever it was
        # (measured: names showed ['before', 'after'] while the client's
        # user_bookmarks was still []). `user_bookmark_names` below is a
        # fresh assignment, which is why the dropdown looked correct and
        # hid this. Every read of user_bookmarks is server-side today, so
        # there is no visible symptom yet — but this is the same defect
        # class as the edit_history/edit_future stacks, and the next
        # client-side read would inherit it silently.
        state.dirty("user_bookmarks")
        state.user_bookmark_names = [b["name"] for b in state.user_bookmarks]
        state.session_dirty = True
        state.status_message = f"Bookmark saved: {name}"

    def apply_user_bookmark(self, name: str) -> None:
        state = self.viewer.server.state
        for bm in state.user_bookmarks:
            if bm["name"] == name:
                self.viewer.sessions._restore_view_appearance(bm)
                section_id, _section = self.viewer.sessions._prepare_saved_view_restore(bm)
                if section_id:
                    state.selected_section = section_id
                    self.viewer.display.activate_section(section_id)
                    self.viewer.sessions._restore_computed_surface(bm)
                # Section activation may rebuild the scene or reset its
                # camera, so the saved pose must be the final render change.
                if self.viewer.sessions._apply_saved_view_camera(bm):
                    _helpers._push_view(self.viewer.plotter)
                state.status_message = f"Restored: {name}"
                return
        state.status_message = f"Unknown bookmark: {name}"

    def _stop_presentation_auto_advance(self) -> None:
        """Cancel presentation playback without leaving a stale iteration."""
        self.viewer._presentation_auto_epoch += 1
        if self.viewer._presentation_auto_task is not None:
            self.viewer._presentation_auto_task.cancel()
            self.viewer._presentation_auto_task = None
        self.viewer.server.state.presentation_auto_advance = False

    def _presentation_delay_seconds(self) -> float:
        try:
            return max(0.1, float(self.viewer.server.state.presentation_slide_duration or 5))
        except (TypeError, ValueError):
            return 5.0

    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("toggle_presentation"), which needs a trigger registration.
    def toggle_presentation(self) -> None:
        """Enter/exit fullscreen presentation mode using bookmarks as slides."""
        state = self.viewer.server.state
        if not state.presentation_mode:
            if not state.user_bookmarks:
                state.status_message = "Create bookmarks first to use as slides"
                return
            # Presentation has no edit chrome. Leave edit mode through its
            # normal cleanup path so selections, highlights, constraints, and
            # pending live optimization cannot mutate a hidden structure.
            if state.edit_mode:
                self.viewer.editing.toggle_edit_mode()
            self.viewer.sessions._stop_presentation_auto_advance()
            state.presentation_mode = True
            # Preserve the complete bookmark record. Rebuilding a narrower
            # dict used to discard the saved section and render settings, so
            # every slide showed whichever section happened to be open.
            state.presentation_slides = [dict(bookmark) for bookmark in state.user_bookmarks]
            state.presentation_slide = 0
            if state.presentation_slides:
                self.viewer.sessions._apply_slide(state.presentation_slides[0])
            state.status_message = (
                f"Presentation mode: {len(state.presentation_slides)} slides. "
                "Arrow keys to navigate."
            )
            _helpers._push_view(self.viewer.plotter)
        else:
            self.viewer.sessions._stop_presentation_auto_advance()
            state.presentation_mode = False
            state.status_message = "Presentation mode exited"

    def _saved_view_setting(self, saved_view: dict[str, Any], name: str) -> Any:
        """Read a saved visual setting, including the legacy slide shape."""
        if name in saved_view:
            return saved_view[name]
        legacy_state = saved_view.get("state")
        if isinstance(legacy_state, dict):
            return legacy_state.get(name)
        return None

    def _prepare_saved_view_restore(
        self,
        saved_view: dict[str, Any],
    ) -> tuple[str | None, Section | None]:
        """Restore controls and volume hints before section activation.

        Volume activation treats ``ViewerState`` hints as authoritative and
        copies them back to the UI while rebuilding the mesh. Updating only
        Trame state, or doing so after activation, creates a split view where
        the controls claim saved values but the live actor still uses stale
        ones. Bookmarks, sessions, and presentation slides all pass through
        this helper so their restore order cannot drift again.
        """
        state = self.viewer.server.state
        # The structure on show comes first, as it does when a structure is
        # selected (#107): a view saved on the second species of a document
        # would otherwise come back on the first. A saved id that is not a
        # structure of this archive, and an older view without the field,
        # change nothing.
        shown = self.viewer.sessions._saved_view_setting(saved_view, "shown_structure")
        reader = self.viewer.reader
        if (
            isinstance(shown, str)
            and reader.has_section(shown)
            and reader.get_section(shown).kind == "structure"
            and shown != reader.shown_structure_id
        ):
            self.viewer.display._switch_structure(shown)
        self.viewer.sessions._restore_vector_glyphs(
            self.viewer.sessions._saved_view_setting(saved_view, "vector_glyphs")
        )
        self.viewer.sessions._restore_complex_field(
            self.viewer.sessions._saved_view_setting(saved_view, "complex_field")
        )
        saved_bloch = self.viewer.sessions._saved_view_setting(saved_view, "bloch_selections")
        if isinstance(saved_bloch, dict):
            state.bloch_selections = {
                **dict(state.bloch_selections or {}),
                **{key: dict(value) for key, value in saved_bloch.items()
                   if isinstance(key, str) and isinstance(value, dict)},
            }
        saved_force = self.viewer.sessions._saved_view_setting(saved_view, "force_arrow_length")
        with contextlib.suppress(TypeError, ValueError):
            length = float(saved_force) if saved_force is not None else 0.0
            if math.isfinite(length) and length > 0:
                state.force_arrow_length = length
        self.viewer.sessions._restore_volume_series(
            self.viewer.sessions._saved_view_setting(saved_view, "volume_series")
        )
        saved_isovalue = self.viewer.sessions._saved_view_setting(saved_view, "isovalue")
        saved_colormap = self.viewer.sessions._saved_view_setting(saved_view, "colormap")
        saved_opacity = self.viewer.sessions._saved_view_setting(saved_view, "opacity")
        restored_isovalue = None
        restored_colormap = None
        restored_opacity = None
        if saved_isovalue is not None:
            with contextlib.suppress(TypeError, ValueError):
                candidate = float(saved_isovalue)
                if math.isfinite(candidate) and candidate > 0:
                    restored_isovalue = candidate
                    state.isovalue = restored_isovalue
        if saved_opacity is not None:
            with contextlib.suppress(TypeError, ValueError):
                candidate = float(saved_opacity)
                if math.isfinite(candidate) and 0 <= candidate <= 1:
                    restored_opacity = candidate
                    state.opacity = restored_opacity
        if saved_colormap is not None:
            colormap_name = str(saved_colormap)
            with contextlib.suppress(ImportError, TypeError, ValueError):
                get_cmap_safe(cmap_argument(colormap_name))
                restored_colormap = colormap_name
                state.colormap = restored_colormap

        legacy_state = saved_view.get("state")
        section_id = saved_view.get("section_id") or saved_view.get("active_section")
        if not section_id and isinstance(legacy_state, dict):
            section_id = legacy_state.get("section_id") or legacy_state.get("active_section")
        section = next(
            (item for item in self.viewer.reader.sections if item.id == section_id),
            None,
        )
        if section is not None and (
            section.kind.startswith("volume.") or section.kind == "basis.ao"
        ):
            from vibeview.viewer_defaults import render_mode, validate_transfer_function

            hints = self.viewer.viewer_state.get_volume_hints(section.id, kind=section.kind)
            if restored_isovalue is not None:
                hints.isovalue = restored_isovalue
            if restored_opacity is not None:
                hints.opacity = restored_opacity
            if restored_colormap is not None:
                hints.colormap = restored_colormap
            saved_render = self.viewer.sessions._saved_view_setting(saved_view, "render")
            if saved_render is not None:
                hints.render = render_mode(saved_render, hints.render)
            saved_tf = self.viewer.sessions._saved_view_setting(saved_view, "transfer_function")
            if saved_tf is not None:
                hints.transfer_function = validate_transfer_function(saved_tf)
        return section_id, section

    def _apply_saved_view_camera(self, saved_view: dict[str, Any]) -> bool:
        """Apply a saved camera after any section rebuild; report success."""
        camera = saved_view.get("camera")
        if not camera:
            return False
        try:
            _helpers._apply_camera(self.viewer.plotter, camera)
            _helpers._sync_orthographic_switch(self.viewer.plotter, self.viewer.server.state)
            self.viewer.plotter.render()
        except Exception:  # noqa: BLE001 - malformed saved views stay recoverable
            return False
        return True

    def _apply_slide(self, slide: dict[str, Any]) -> None:
        """Apply a presentation slide: restore camera and section selection."""
        state = self.viewer.server.state
        self.viewer.sessions._restore_view_appearance(slide)
        section_id, section = self.viewer.sessions._prepare_saved_view_restore(slide)
        if section_id:
            state.presentation_panel_slide = bool(
                section is not None
                and (
                    section.kind in _helpers._two_d_panel_kinds()
                    or section.kind in {"scan.surface", "reaction.waypoints"}
                )
            )
            state.selected_section = section_id
            # Trigger section activation so volume/isosurface/etc. reappear.
            self.viewer.display.activate_section(section_id)
            self.viewer.sessions._restore_computed_surface(slide)
        else:
            state.presentation_panel_slide = False

        # Apply the camera last so any section rebuild cannot replace the
        # bookmarked pose before the client receives it.
        self.viewer.sessions._apply_saved_view_camera(slide)
        _helpers._push_view(self.viewer.plotter)

    # These named triggers are required by the global ArrowLeft/ArrowRight
    # handler. A plain @ctrl.set controller is callable by Python widgets but
    # is absent from the client trigger registry.
    def presentation_next(self) -> None:
        """Advance to the next slide."""
        state = self.viewer.server.state
        if not state.presentation_mode or not state.presentation_slides:
            return
        state.presentation_slide = (state.presentation_slide + 1) % len(state.presentation_slides)
        self.viewer.sessions._apply_slide(state.presentation_slides[state.presentation_slide])
        state.status_message = (
            f"Slide {state.presentation_slide + 1}/{len(state.presentation_slides)}"
        )

    def presentation_prev(self) -> None:
        """Go to previous slide."""
        state = self.viewer.server.state
        if not state.presentation_mode or not state.presentation_slides:
            return
        state.presentation_slide = (state.presentation_slide - 1) % len(state.presentation_slides)
        self.viewer.sessions._apply_slide(state.presentation_slides[state.presentation_slide])
        state.status_message = (
            f"Slide {state.presentation_slide + 1}/{len(state.presentation_slides)}"
        )

    def toggle_presentation_auto_advance(self) -> None:
        """Start or stop timed cyclic slide advancement."""
        state = self.viewer.server.state
        if state.presentation_auto_advance:
            self.viewer.sessions._stop_presentation_auto_advance()
            state.status_message = "Automatic slide advance paused"
            return
        self.viewer.sessions._stop_presentation_auto_advance()
        if not state.presentation_mode or len(state.presentation_slides) < 2:
            state.status_message = "Automatic advance needs at least two slides"
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            state.status_message = "Automatic slide advance needs the running viewer"
            return

        state.presentation_auto_advance = True
        self.viewer._presentation_auto_epoch += 1
        my_epoch = self.viewer._presentation_auto_epoch

        async def _loop() -> None:
            try:
                while state.presentation_mode and state.presentation_auto_advance:
                    await asyncio.sleep(self.viewer.sessions._presentation_delay_seconds())
                    if (
                        my_epoch != self.viewer._presentation_auto_epoch
                        or not state.presentation_mode
                        or not state.presentation_auto_advance
                    ):
                        return
                    self.viewer.sessions.presentation_next()
                    state.flush()
            finally:
                if my_epoch == self.viewer._presentation_auto_epoch:
                    self.viewer._presentation_auto_task = None
                    state.presentation_auto_advance = False
                    with contextlib.suppress(Exception):
                        state.flush()

        self.viewer._presentation_auto_task = loop.create_task(_loop())
        state.status_message = (
            "Automatic slide advance every "
            f"{self.viewer.sessions._presentation_delay_seconds():g} s"
        )

    def _session_payload(self) -> dict[str, Any]:
        """The on-disk session document. Single source of truth.

        Both the explicit save and the auto-save write through this. They used
        to build their own dicts: the auto-save emitted ``version: "2.0"`` with
        differently-named fields, while ``load_session`` accepts only
        ``version == 1``. Since the auto-save loop rewrites the *same*
        ``session_path``, an explicitly saved session was silently replaced
        within 60 s by a document the loader then refused ("Bad version") —
        and the fields the auto-save omitted (replication, library_favorites)
        were dropped with it. Keeping one builder makes that drift impossible.
        """
        state = self.viewer.server.state
        return {
            "version": 1,
            # The open section lives in `selected_section`; `active_section` is
            # never declared in the state defaults, so reading it here recorded
            # null. Field name kept for compatibility with existing session
            # files and the bookmark/presentation readers. Session loading
            # routes it through the same guarded activation as interactive
            # section changes.
            "active_section": state.selected_section,
            "isovalue": state.isovalue,
            "colormap": state.colormap,
            "opacity": state.opacity,
            "camera": _helpers._camera_to_dict(self.viewer.plotter),
            # Read the declared per-axis keys. `state.replication` is not a
            # state key at all, and a trame state returns None for an
            # undeclared name rather than raising — so getattr's [1, 1, 1]
            # default never applied and list(None) raised TypeError, taking
            # down every session save and autosave with it.
            "replication": [
                int(state.replication_nx or 1),
                int(state.replication_ny or 1),
                int(state.replication_nz or 1),
            ],
            "user_bookmarks": state.user_bookmarks,
            # Pinned library structures are a deliberate choice, so they
            # persist with the session (recents are implicit, and don't).
            "library_favorites": list(state.library_favorites or []),
            **self.viewer.sessions._saved_view_extras(),
        }

    def _restore_vector_glyphs(self, saved: Any) -> None:
        """Put saved Vector field settings back before the section redraws (#156).

        A value out of the card's range, or of the wrong type, is skipped and
        the current setting kept.
        """
        if not isinstance(saved, dict):
            return
        state = self.viewer.server.state
        checks: tuple[tuple[str, str, Any], ...] = (
            ("stride", "vector_stride", lambda v: int(v) if 0 <= int(v) <= 64 else None),
            ("length_scale", "vector_length_scale",
             lambda v: float(v) if math.isfinite(float(v)) and float(v) > 0 else None),
            ("cutoff", "vector_cutoff",
             lambda v: float(v) if math.isfinite(float(v)) and float(v) >= 0 else None),
        )
        for field, key, convert in checks:
            with contextlib.suppress(TypeError, ValueError):
                value = convert(saved.get(field))
                if value is not None:
                    state[key] = value
        colormap = saved.get("colormap")
        if isinstance(colormap, str) and colormap:
            with contextlib.suppress(ImportError, TypeError, ValueError):
                get_cmap_safe(cmap_argument(colormap))
                state.vector_colormap = colormap

    def _restore_volume_series(self, saved: Any) -> None:
        """Put the saved Volume series slabs and isovalues back (#158).

        A negative slab, or a non-positive or non-finite isovalue, is
        skipped; a slab past the series' end is clamped when it is drawn.
        """
        if not isinstance(saved, dict):
            return
        state = self.viewer.server.state
        for field, key, check in (
            ("slabs", "series_slabs", lambda v: int(v) if int(v) >= 0 else None),
            ("isovalues", "series_isovalues",
             lambda v: float(v) if math.isfinite(float(v)) and float(v) > 0 else None),
        ):
            values = saved.get(field)
            if not isinstance(values, dict):
                continue
            kept = dict(state[key] or {})
            for section_id, value in values.items():
                with contextlib.suppress(TypeError, ValueError):
                    checked = check(value)
                    if isinstance(section_id, str) and checked is not None:
                        kept[section_id] = checked
            state[key] = kept

    def _restore_complex_field(self, saved: Any) -> None:
        """Put the saved Complex field part and isovalues back (#157).

        An unknown part or a non-positive or non-finite isovalue is skipped.
        """
        if not isinstance(saved, dict):
            return
        from vibeview.renderers.complex_field import PARTS

        state = self.viewer.server.state
        part = saved.get("part")
        if part in PARTS:
            state.complex_part = part
        isovalues = saved.get("isovalues")
        if isinstance(isovalues, dict):
            kept = dict(state.complex_isovalues or {})
            for section_id, value in isovalues.items():
                with contextlib.suppress(TypeError, ValueError):
                    number = float(value)
                    if isinstance(section_id, str) and math.isfinite(number) and number > 0:
                        kept[section_id] = number
            state.complex_isovalues = kept

    def _saved_view_extras(self) -> dict[str, Any]:
        """View fields beyond camera/section/volume hints (#37-16).

        Bookmarks, slides and sessions kept only the camera, the section and
        the volume hints, so restoring one dropped the representation, the
        material and any computed wavefunction surface (the HOMO you saved
        came back as a bare structure).
        """
        state = self.viewer.server.state
        extras: dict[str, Any] = {
            # The structure on show, which the saved section alone cannot
            # tell once a section that names no structure is selected over
            # another structure (#106); restored first, as a structure
            # selection is (#107).
            "shown_structure": self.viewer.reader.shown_structure_id,
            "representation_style": state.representation_style,
            "material_preset": state.material_preset,
            # Direct volume rendering: the active volume's mode and its
            # transfer function, restored into the section's hints.
            "render": str(state.vol_mode or "isosurface"),
            "transfer_function": state.vol_tf if isinstance(state.vol_tf, dict) else None,
            # The Vector field card's arrows (#156).
            "vector_glyphs": {
                "stride": int(state.vector_stride or 0),
                "length_scale": float(state.vector_length_scale or 1.0),
                "cutoff": float(state.vector_cutoff if state.vector_cutoff is not None else 0.05),
                "colormap": str(state.vector_colormap or "viridis"),
            },
            # The Bloch orbitals card's selection per section (#162).
            "bloch_selections": dict(state.bloch_selections or {}),
            # The Forces card's arrow length (#159).
            "force_arrow_length": float(state.force_arrow_length or 1.0),
            # The Volume series card's slab and isovalue per section (#158).
            "volume_series": {
                "slabs": dict(state.series_slabs or {}),
                "isovalues": dict(state.series_isovalues or {}),
            },
            # The Complex field card's part and per-section isovalues (#157).
            "complex_field": {
                "part": str(state.complex_part or "real"),
                "isovalues": dict(state.complex_isovalues or {}),
            },
        }
        from vibeview.figure_panel import scene

        extras["figure_scene"] = scene(self.viewer).to_dict()
        kind = state.wf_surface_kind
        if kind and state.mo_visible:
            surface: dict[str, Any] = {"kind": str(kind)}
            if kind == "mo":
                surface.update(
                    {
                        "index": state.mo_last_index,
                        "spin": state.mo_last_spin,
                        "component": state.mo_last_component,
                    }
                )
            extras["surface"] = surface
        return extras

    def _restore_view_appearance(self, saved_view: dict[str, Any]) -> None:
        """Restore representation and material before the section rebuild."""
        state = self.viewer.server.state
        from vibeview.figure_scene import FigureScene

        if "figure_scene" in saved_view:
            state.figure_scene = FigureScene.from_dict(saved_view["figure_scene"]).to_dict()
        style = saved_view.get("representation_style")
        if isinstance(style, str) and style != state.representation_style:
            self.viewer.display.set_representation_style(style)
        preset = saved_view.get("material_preset")
        if isinstance(preset, str) and preset != state.material_preset:
            from vibeview.material_presets import MATERIAL_PRESETS

            if preset in MATERIAL_PRESETS:
                self.viewer.display.set_material_preset(preset)

    def _restore_computed_surface(self, saved_view: dict[str, Any]) -> None:
        """Replay a saved computed surface after its section is active."""
        surface = saved_view.get("surface")
        state = self.viewer.server.state
        if not isinstance(surface, dict) or not state.wf_section_id:
            return
        kind = surface.get("kind")
        if kind not in ("mo", "density", "spin_density", "elf", "nci", "laplacian"):
            return
        if kind == "mo":
            raw_index = surface.get("index")
            if isinstance(raw_index, bool) or not isinstance(raw_index, (int, float, str)):
                return
            try:
                index = int(raw_index)
            except ValueError:
                return
            state.mo_last_index = index
            state.mo_last_spin = str(surface.get("spin") or "restricted")
            state.mo_last_component = str(surface.get("component") or "real")
        state.wf_surface_kind = kind
        try:
            with self.viewer._wf_render_lock:
                restored = _helpers._replay_wavefunction_surface(
                    self.viewer.reader, self.viewer.plotter, self.viewer.viewer_state, state
                )
                if restored:
                    _helpers._finalize_wavefunction_surface_appearance(self.viewer.plotter, state)
        except Exception as e:  # noqa: BLE001 - a saved view stays recoverable
            restored = False
            state.status_message = f"Saved surface could not be restored: {e}"
        if restored:
            state.mo_visible = True
            state.volume_loaded = True
            _helpers._push_view(self.viewer.plotter)
        else:
            state.wf_surface_kind = None

    def _saved_session_replication(self, session: dict[str, Any]) -> tuple[int, int, int] | None:
        """Return a complete integral replication triple, or ignore it safely."""
        saved = session.get("replication")
        if not isinstance(saved, (list, tuple)) or len(saved) != 3:
            return None

        restored: list[int] = []
        for value in saved:
            if isinstance(value, bool):
                return None
            try:
                candidate = float(value)
            except (OverflowError, TypeError, ValueError):
                return None
            if not math.isfinite(candidate) or not candidate.is_integer():
                return None
            restored.append(int(candidate))
        return restored[0], restored[1], restored[2]

    def _restore_session_replication(self, session: dict[str, Any]) -> None:
        """Synchronize a saved supercell before activating its target section."""
        saved = self.viewer.sessions._saved_session_replication(session)
        if saved is None:
            return

        state = self.viewer.server.state
        target = clamp_replication(
            saved,
            (bool(state.pbc_a), bool(state.pbc_b), bool(state.pbc_c)),
        )
        try:
            current_ui = (
                int(state.replication_nx),
                int(state.replication_ny),
                int(state.replication_nz),
            )
            current_render = tuple(int(value) for value in self.viewer.viewer_state.replication)
        except (OverflowError, TypeError, ValueError):
            current_ui = None
            current_render = None
        if current_ui == target and current_render == target:
            return

        # Reuse the interactive path so periodic-axis clamping, animation
        # cancellation, ViewerState synchronization, and the rendered
        # supercell cannot drift between session and UI restores.
        self.viewer.display.update_replication(*target)

    def _default_session_path(self) -> Path:
        """Where a session lives when the user names no path.

        One location for the explicit save, the load button and the auto-save:
        ``<stem>.vibe-view-session.json`` beside the archive (what the manual
        promises), or the cache's auto-session for an in-memory structure. The
        explicit save used to write ``session.vibe-session`` into the process
        cwd while the auto-save wrote beside the archive (#37-2).
        """
        if self.viewer.reader.path:
            return (
                Path(self.viewer.reader.path.parent)
                / f"{self.viewer.reader.path.stem}.vibe-view-session.json"
            )
        from vibeview.paths import auto_session_path

        return auto_session_path()

    def save_session(self, path: str | None = None) -> None:
        import json as _json
        from pathlib import Path as _Path  # not in scope here; NameError without it

        state = self.viewer.server.state
        # state.session_path is a str, so the old `Path(path) if path else
        # state.session_path` left sp a plain string on the no-argument call
        # (the Save Session button) and sp.write_text() below would fail.
        sp = _Path(path or state.session_path or self.viewer.sessions._default_session_path())
        session = self.viewer.sessions._session_payload()
        sp.write_text(_json.dumps(session, indent=2))
        state.session_path = str(sp)
        state.session_dirty = False
        state.status_message = f"Session saved: {sp}"

    def load_session(self, path: str) -> None:
        import json as _json
        from pathlib import Path as _Path  # not in scope here; NameError without it

        state = self.viewer.server.state
        # The Load Session button sends '' (the path lives in the text field
        # bound to session_path); Path('') is '.', a directory, which read as
        # "Load error: [Errno 21] Is a directory" (#37-1).
        path = _helpers._first(path, "")
        path = str(path or state.session_path or self.viewer.sessions._default_session_path())
        sp = _Path(path)
        if not sp.exists():
            state.status_message = f"Not found: {path}"
            return
        if not sp.is_file():
            state.status_message = f"Not a session file: {path}"
            return
        try:
            session = _json.loads(sp.read_text())
        except Exception as e:
            state.status_message = f"Load error: {e}"
            return
        if session.get("version") != 1:
            state.status_message = "Bad version"
            return
        self.viewer.sessions._restore_view_appearance(session)
        section_id, _section = self.viewer.sessions._prepare_saved_view_restore(session)
        # Replication rebuilds the base scene. Apply it before activating the
        # saved section so specialized Fermi, animation, and overlay renderers
        # are not immediately erased, and volume activation sees the target
        # ViewerState replication from its first mesh build.
        self.viewer.sessions._restore_session_replication(session)
        if section_id:
            # Field name kept for existing session files; the state key that
            # drives the UI is selected_section, and activation is what
            # actually re-opens the section.
            state.selected_section = section_id
            self.viewer.display.activate_section(section_id)
            self.viewer.sessions._restore_computed_surface(session)
        # A section rebuild may reset its camera (for example reciprocal-space
        # surfaces), and replication resets the client camera to fit the grown
        # scene, so the saved pose is deliberately applied last.
        if self.viewer.sessions._apply_saved_view_camera(session):
            _helpers._push_view(self.viewer.plotter)
        state.user_bookmarks = session.get("user_bookmarks", [])
        state.library_favorites = session.get("library_favorites", [])
        state.user_bookmark_names = [b["name"] for b in state.user_bookmarks]
        state.session_path = str(sp)
        state.session_dirty = False
        state.status_message = f"Session loaded: {path}"
