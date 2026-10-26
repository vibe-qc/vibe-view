"""Display controllers for one browser viewer instance."""

from __future__ import annotations

import contextlib
import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from vibeview._fmt import human_size
from vibeview.plotter_compat import (
    reset_camera,
    view_isometric,
    view_xy,
    view_xz,
    view_yz,
)
from vibeview.qvf import QVFError, clamp_replication
from vibeview.renderers import palette as _palette

if TYPE_CHECKING:
    from collections.abc import Callable


from vibeview.app import helpers as _helpers

if TYPE_CHECKING:
    from vibeview.app.runtime import ViewerRuntime


class DisplayControllers:
    def __init__(self, viewer: ViewerRuntime) -> None:
        self.viewer = viewer

    def _ensure_static_structure_shown(self) -> bool:
        """Rebuild the static structure if a previous animation hid it.

        The animation render paths (trajectory / vibrations / reaction.path)
        call ``_remove_static_structure`` and set ``structure_hidden`` so the
        frozen equilibrium geometry doesn't ghost behind the moving atoms
        (A5-03). When the user returns to any non-animation section we drop
        the stale animation actors and redraw the structure. Returns whether
        it redrew, so the dispatcher does not redraw the atoms a second time
        (#37-53).
        """
        if not self.viewer.server.state.structure_hidden:
            return False
        rebuilt = False
        for prefix in (
            _helpers._Actors.VIB_ATOM,
            _helpers._Actors.TRAJ_ATOM,
            _helpers._Actors.TRAJ_CELL,
        ):
            _helpers._remove_actors_by_prefix(self.viewer.plotter, prefix)
        if self.viewer.structure_renderer is not None:
            _helpers._remove_static_structure(self.viewer.plotter)  # avoid duplicate actors
            # StructureRenderer caches its first StructureData. Reusing the
            # pre-edit instance after compare/animation mode would therefore
            # redraw the archive atoms and cell over a newer edit overlay.
            # Rebuild from the active reader so the restored scene sees the
            # same current atoms+lattice as export and input generation.
            self.viewer.structure_renderer, self.viewer.is_periodic = (
                _helpers._build_structure_scene(
                    self.viewer.reader,
                    self.viewer.plotter,
                    self.viewer.server.state,
                    replication=_helpers._triple(self.viewer.viewer_state.replication),
                    show_labels=bool(self.viewer.server.state.show_atom_labels),
                    representation=str(
                        getattr(self.viewer.server.state, "representation_style", "ball_and_stick")
                    ),
                    cartoon_color_mode=str(
                        getattr(self.viewer.server.state, "cartoon_color_mode", "chain")
                    ),
                    residue_selection=str(
                        getattr(self.viewer.server.state, "residue_selection", "") or ""
                    ),
                )
            )
            rebuilt = True
        # ── Rebuild the active volume ──────────────────────────────────
        self.viewer.server.state.structure_hidden = False
        return rebuilt

    def _switch_structure(self, section_id: str) -> str:
        """Show another structure of the archive; return the status line (#90).

        An unsaved edit stays with the structure it was made on, the way an
        edit stays with its file on a file switch: the reader keeps it, and
        selecting that structure again shows the edited geometry. Undo and
        redo, the edit selection, frozen atoms, measurement picks, the
        residue selection and live optimization all index the outgoing
        atoms, so they end here, as they do on a file switch. The
        calculation panel takes the incoming structure's charge and
        multiplicity when the archive states them.
        """
        viewer = self.viewer
        state = viewer.server.state
        reader = viewer.reader
        previous = reader.shown_structure_id
        viewer.files._cancel_live_opt_for_reader_change()
        viewer.files._clear_reader_scoped_edit_state()
        state.selected_atoms = []
        state.measure_result = _helpers._MEASURE_HINT if state.measure_mode else ""
        _helpers._remove_actors_by_prefix(viewer.plotter, _helpers._Actors.PICK_MARKER)
        state.residue_selection = ""
        state.residue_pick_mode = False

        reader.show_structure(section_id)

        for prefix in (
            _helpers._Actors.VIB_ATOM,
            _helpers._Actors.TRAJ_ATOM,
            _helpers._Actors.TRAJ_CELL,
        ):
            _helpers._remove_actors_by_prefix(viewer.plotter, prefix)
        _helpers._remove_static_structure(viewer.plotter)
        # A molecule shown after a crystal must not keep the crystal's supercell.
        _helpers._clamp_viewer_replication(reader, viewer.viewer_state)
        self._set_replication(viewer.viewer_state.replication)
        viewer.structure_renderer, viewer.is_periodic = _helpers._build_structure_scene(
            reader,
            viewer.plotter,
            state,
            replication=_helpers._triple(viewer.viewer_state.replication),
            show_labels=bool(state.show_atom_labels),
            representation=str(getattr(state, "representation_style", "ball_and_stick")),
            cartoon_color_mode=str(getattr(state, "cartoon_color_mode", "chain")),
            residue_selection="",
        )
        state.structure_hidden = False
        _helpers._apply_scene_appearance(viewer.plotter, state)
        # Structure-derived panels and pickers follow the shown structure.
        state.shown_structure_id = _helpers._shown_structure_marker(reader)
        state.element_color_options = _helpers._present_elements(reader)
        state.header_title = _helpers._header_title(reader)
        state.residue_selection_chains = _helpers._selectable_chains(reader)
        state.residue_selection_available = _helpers._residue_selection_available(reader)
        state.residue_selection_summary = _helpers._residue_selection_summary(reader, "")
        _helpers._seed_calculation_charge(state, reader)
        reset_camera(viewer.plotter)
        _helpers._push_view(viewer.plotter)
        _helpers._push_camera(viewer.plotter)

        message = f"Showing structure {_helpers._structure_display_name(reader, section_id)}"
        if previous is not None and previous in reader.edited_structure_ids:
            message += (
                f" — the edits of {_helpers._structure_display_name(reader, previous)} are kept; "
                "select it to continue"
            )
        if section_id in reader.edited_structure_ids:
            message += " (with your unsaved edits)"
        return message

    def _activate_section_impl(self, section_id: str) -> None:
        """Invalidate worker renders, then mutate the VTK scene exclusively."""
        if self.viewer.server.state.relocalize_running:
            self.viewer.wavefunction._cancel_relocalization()
        self.viewer.files._invalidate_wavefunction_renders()
        with self.viewer._wf_render_lock:
            self.viewer.display._activate_section_impl_unlocked(section_id)

    def _activate_section_impl_unlocked(self, section_id: str) -> None:
        """Lazy activation: called when user clicks a section in the sidebar."""
        state = self.viewer.server.state
        # Synthetic "Molecular Orbitals" sidebar entry: route to the first
        # orbital section and let the right-panel picker switch between them.
        if section_id == _helpers._ORBITAL_GROUP_ID:
            if not self.viewer.orbital_sections:
                state.status_message = "No orbital sections in this file."
                return
            first_id = self.viewer.orbital_sections[0]["id"]
            state.selected_orbital = first_id
            self.viewer.display.activate_section(first_id)
            return
        if section_id == _helpers._BASIS_AO_GROUP_ID:
            if not self.viewer.basis_ao_sections:
                state.status_message = "No basis function sections in this file."
                return
            first_id = self.viewer.basis_ao_sections[0]["id"]
            state.selected_ao = first_id
            self.viewer.display.activate_section(first_id)
            return
        section = next((s for s in self.viewer.reader.sections if s.id == section_id), None)
        if section is None:
            state.status_message = f"Section {section_id!r} not found."
            return
        push_generation = _helpers._scene_hooks(self.viewer.plotter).push_generation
        # Keep the orbital picker in sync when the user activates an orbital
        # directly (e.g. via the right-panel select).
        if section.kind == "volume.orbital":
            state.selected_orbital = section_id
        # Same for basis.ao.
        if section.kind == "basis.ao":
            state.selected_ao = section_id

        state.selected_section = section_id
        # Show the calculation-parameter panel only when the structure
        # section is active; default calc_template to "periodic" for
        # periodic systems.
        # By kind, like every other structure test: a file whose structure
        # section has another id lost the panel (#37-43).
        is_structure = section.kind == "structure"
        state.show_param_panel = is_structure
        if is_structure and state.is_periodic:
            state.calc_template = "periodic"
        # Remember whether we're switching away from atom_properties so we
        # can restore the CPK colour palette on the 3D atoms below.
        _was_atom_props = bool(state.atom_properties_active)
        # A filmstrip still rendering for the previous section must not land
        # on this one.
        self.viewer.playback.cancel_filmstrips()
        _helpers._clear_output_panels(state)
        # Whether this switch changed the server-side scene. A 2D panel whose
        # cleanup removed nothing needs no push: serializing the whole 3D
        # scene on every chart click was the cost (#37-49).
        scene_changed = _helpers._remove_atom_charge_labels(self.viewer.plotter)
        # Drop any 3D overlay actors from a previously activated section
        # when their per-section replay state is cleared. Without removing the
        # computed surface too, a 2D or different wavefunction section could
        # show stale MO geometry with no visibility control left to remove it.
        # A previously shown Fermi surface lives in reciprocal space; drop its
        # sheets + cell wireframe whenever we switch to anything else.
        for prefix in (
            _helpers._Actors.QTAIM, _helpers._Actors.MO_SURFACE, _helpers._Actors.FERMI,
            _helpers._Actors.VECTOR, _helpers._Actors.COMPLEX, _helpers._Actors.FORCE,
            _helpers._Actors.PROPERTY, _helpers._Actors.BLOCH,
        ):
            scene_changed |= bool(_helpers._remove_actors_by_prefix(self.viewer.plotter, prefix))
        # A properties.electric section hid the root dipole arrow it restates (#161).
        root_dipole = self.viewer.plotter.actors.get(_helpers._Actors.DIPOLE)
        if root_dipole is not None and not root_dipole.GetVisibility():
            root_dipole.SetVisibility(True)
            scene_changed = True
        # The Vector and Complex field cards belong to the section that drew.
        state.vector_section_id = ""
        state.complex_section_id = ""
        # A QVF-backed volume has its own active-volume lifecycle and may stay
        # beside a 2D panel. It must still be removed when entering a computed
        # wavefunction view, or an MO would render inside an uncontrolled old
        # density (reported 2026-07-19).
        if section.kind == "wavefunction.gto":
            scene_changed |= bool(
                _helpers._remove_actors_by_prefix(self.viewer.plotter, _helpers._Actors.VOLUME)
            )
        # Navigating into a section exits compare/overlay mode (a distinct mode).
        if state.compare_mode:
            state.compare_mode = False
            state.compare_legend = []
            _helpers._remove_actors_by_prefix(self.viewer.plotter, _helpers._Actors.COMPARE)
            scene_changed = True
        handler = _helpers._section_handler(section.kind)
        # Selecting another structure of the archive shows it (QVF spec
        # § 4.1: a consumer offers the structures besides the first). A
        # section whose structure_ref names another structure describes that
        # one, so it is shown first and the section is drawn over it rather
        # than over whatever was on show (#106). A section that names none
        # makes no statement and leaves the shown structure alone.
        switch_note = None
        described = self.viewer.reader.named_structure_ref(section.id)
        if described is not None and described != self.viewer.reader.shown_structure_id:
            switch_note = self.viewer.display._switch_structure(described)
            scene_changed = True
        # If we're leaving an animation, restore the static structure that
        # the trajectory/vibration/reaction path hid (A5-03). fermi_surface
        # replaces the real-space scene entirely, so it stays hidden there too.
        rebuilt = switch_note is not None
        if not rebuilt and (handler is None or not handler.keeps_structure_hidden):
            rebuilt = self.viewer.display._ensure_static_structure_shown()
            scene_changed |= rebuilt
        # Leaving atom_properties: the atoms may still carry the charge tint.
        # One step for every kind that shows the atoms as they are (#37-27);
        # a structure the line above just redrew is already in CPK (#37-53).
        if _was_atom_props and not rebuilt and (handler is None or handler.restores_cpk):
            _helpers._restore_cpk_atoms(self.viewer.reader, self.viewer.plotter, state)
            scene_changed = True
        status_before = state.status_message
        if handler is not None:
            handler.activate(
                _helpers._SectionContext(
                    self.viewer.reader,
                    self.viewer.plotter,
                    state,
                    self.viewer.viewer_state,
                    self.viewer._all_readers,
                ),
                section,
            )
        if switch_note is not None:
            # A section drawn over a newly shown structure reports both: the
            # switch, then what the section's own activation said.
            loaded = state.status_message if state.status_message != status_before else ""
            state.status_message = (
                f"{switch_note} — {loaded}"
                if loaded and section.kind != "structure"
                else switch_note
            )
        # The scrubber's thumbnails render in the background, so the panel
        # opens at once and the strip fills in.
        if section.kind in ("trajectory", "reaction.path"):
            self.viewer.playback.refresh_trajectory_filmstrip()
        elif section.kind == "vibrations":
            self.viewer.playback.refresh_vibration_filmstrip()

        # Sync the scene when the target activator did not already do so;
        # otherwise VtkLocalView can retain removed charge or computed-surface
        # actors until a later 3D action. Avoid a duplicate push for volume and
        # other mesh activators because serializing those scenes is expensive,
        # and skip it for a 2D panel that left the scene as it was.
        pushed = _helpers._scene_hooks(self.viewer.plotter).push_generation != push_generation
        if not pushed and (scene_changed or section.kind not in _helpers._two_d_panel_kinds()):
            self.viewer.plotter.render()
            _helpers._push_view(self.viewer.plotter)

    def activate_section(self, section_id: str) -> None:
        """Guarded entry point for section switching.

        The per-kind render dispatch (`_activate_section_impl`) does a lot of
        synchronous work in a dozen renderers; a single one raising must not
        take down or freeze the whole viewer. Catch it, log it, surface a
        status message, and leave the 3D viewport usable so the user can just
        switch to another section instead of a dead/frozen app.
        """
        try:
            self.viewer.display._activate_section_impl(section_id)
        except Exception as e:  # noqa: BLE001 — a bad section must stay recoverable
            import traceback

            traceback.print_exc()
            self.viewer.server.state.status_message = (
                f"Could not open section {section_id!r}: {type(e).__name__}: {e}"
            )
            # Best-effort: restore a viewable static structure + resync client.
            try:
                self.viewer.display._ensure_static_structure_shown()
                self.viewer.plotter.render()
                _helpers._push_view(self.viewer.plotter)
            except Exception:  # noqa: BLE001 — recovery is best-effort
                pass

    # Section kinds each ``vibe-view open --view`` opens, in preference order;
    # a trailing "." matches the kind family (#141).
    OPEN_VIEW_KINDS: dict[str, tuple[str, ...]] = {
        "orbitals": ("wavefunction.gto", "volume.orbital"),
        "density": ("volume.density", "volume."),
        "mode": ("vibrations",),
        "spectrum": ("spectra.",),
    }

    def open_view(
        self,
        view: str | None = None,
        section: str | None = None,
        mode: int | None = None,
        orbital: str | None = None,
    ) -> list[str]:
        """Open on a chosen result view; return warnings for what didn't fit (#141).

        ``section`` names the section outright; otherwise ``view`` picks the
        first section of its kinds. ``mode`` is the 1-based vibration mode the
        picker shows; ``orbital`` is a wavefunction MO label (see
        ``_helpers._orbital_row``) or a ``volume.orbital`` section id or label.
        """
        viewer = self.viewer
        state = viewer.server.state
        sections = viewer.reader.sections
        warnings: list[str] = []
        target = None
        if section is not None:
            target = next((s for s in sections if s.id == section), None)
            if target is None:
                available = ", ".join(s.id for s in sections)
                warnings.append(f"no section {section!r} in this file (have: {available})")
        elif view is not None:
            kinds = self.OPEN_VIEW_KINDS[view]
            for kind in kinds:
                target = next(
                    (s for s in sections if s.kind == kind
                     or (kind.endswith(".") and s.kind.startswith(kind)
                         and s.kind != "volume.orbital")),
                    None,
                )
                if target is not None:
                    break
            if target is None:
                warnings.append(f"no {view} section in this file ({' / '.join(kinds)})")
        if target is not None and orbital is not None and target.kind == "volume.orbital":
            # A precomputed orbital grid is its own section: the label names it.
            wanted = str(orbital).strip().lower()
            named = next(
                (s for s in sections if s.kind == "volume.orbital" and wanted in (
                    s.id.lower(), str((s.model_extra or {}).get("label") or "").lower())),
                None,
            )
            if named is None:
                available = ", ".join(s.id for s in sections if s.kind == "volume.orbital")
                warnings.append(f"no orbital {orbital!r} (have: {available})")
            else:
                target = named
            orbital = None
        if target is not None:
            self.activate_section(target.id)
        active = target.kind if target is not None and state.selected_section == target.id else None
        if mode is not None:
            n_modes = int(state.vibration_n_modes or 0)
            if active != "vibrations":
                warnings.append(f"--mode {mode} needs a vibrations section")
            elif not 1 <= mode <= n_modes:
                warnings.append(f"no mode {mode} (this file has modes 1-{n_modes})")
            else:
                viewer.playback.on_vibration_changed(mode - 1, state.vibration_amplitude)
        if orbital is not None:
            row = _helpers._orbital_row(state.wf_mo_rows, orbital)
            if active != "wavefunction.gto":
                warnings.append(f"--orbital {orbital} needs a wavefunction or orbital section")
            elif row is None:
                warnings.append(
                    f"no orbital {orbital!r} in this wavefunction "
                    "(use HOMO, LUMO-1, a picker number such as 12, or alpha:/beta:)"
                )
            else:
                state.wf_selected_mo = row["value"]
                viewer.wavefunction.render_mo(row["value"])
        return warnings

    def _refresh_active_volume(
        self, *, hints: dict[str, Any] | None = None, invalidate_mesh: bool = False
    ) -> None:
        """Rebuild the active volume after one of its controls changed.

        One guard for every volume control, which used three (#37-30): the
        control's ``hints`` are recorded on the active volume, and the
        surface is rebuilt only while that volume is loaded.
        ``invalidate_mesh`` drops the cached mesh for a change the cache key
        does not see. ``_rebuild_volume`` pushes the view itself, so no
        control pushes again.
        """
        state = self.viewer.server.state
        vol_id = state.active_volume_id
        if not vol_id:
            return
        if hints:
            volume_hints = self.viewer.viewer_state.get_volume_hints(vol_id)
            for name, value in hints.items():
                setattr(volume_hints, name, value)
        if not state.volume_loaded:
            return
        if invalidate_mesh:
            self.viewer.viewer_state.invalidate_mesh_cache(vol_id)
        _helpers._rebuild_volume(
            self.viewer.reader, self.viewer.plotter, self.viewer.viewer_state, state, vol_id
        )

    def set_volume_mode(self, mode: Any = None) -> None:
        """Draw the active volume as an isosurface or as a volume.

        Volume mode (docs/volume_rendering_design.md) sends a capped uint8
        grid drawn through a transfer function. The clip planes apply to
        the contour only, so entering volume mode turns them off; that
        toggle's rebuild then draws the volume, which is why it returns
        early. A grid the payload refuses puts the mode back and says why.
        """
        state = self.viewer.server.state
        chosen = str(_helpers._first(mode) or "isosurface")
        state.vol_mode = chosen if chosen in ("isosurface", "volume") else "isosurface"
        if state.vol_mode == "volume" and bool(state.clip_enabled):
            # Record the choice on the section first: the clip toggle's
            # rebuild reads the hint, not the picker.
            vol_id = state.active_volume_id
            if vol_id:
                self.viewer.viewer_state.get_volume_hints(vol_id).render = state.vol_mode
            self.viewer.editing.toggle_clip(False)
            return
        self.viewer.display._refresh_active_volume(hints={"render": state.vol_mode})

    def toggle_volume_mode(self) -> None:
        """Flip the active volume between the isosurface and the volume."""
        state = self.viewer.server.state
        if not state.active_volume_id:
            state.status_message = "Open a volume section first"
            return
        self.viewer.display.set_volume_mode(
            "isosurface" if str(state.vol_mode) == "volume" else "volume"
        )

    def _refresh_volume_transfer_function(self) -> None:
        """Rebuild the active volume from the editor's state, as its hint."""
        from vibeview.renderers.volume_render import (
            RAMP_POSITIONS,
            VolumeRenderError,
            transfer_function_from_editor,
        )

        state = self.viewer.server.state
        window = state.vol_window
        if not isinstance(window, list) or len(window) != 2:
            return
        ramp = [float(getattr(state, f"vol_ramp_{i}", 1.0)) for i in range(len(RAMP_POSITIONS))]
        try:
            tf = transfer_function_from_editor(
                window=(float(window[0]), float(window[1])),
                colormap=str(state.colormap),
                ramp=ramp,
                preset=str(state.vol_preset or "ramp"),
            )
        except VolumeRenderError as e:
            state.status_message = f"Transfer function: {e}"
            return
        self.viewer.display._refresh_active_volume(hints={"transfer_function": tf})

    def update_volume_nodes(self, nodes: Any = None) -> None:
        """Replace the opacity curve with nodes placed one by one (the editor).

        ``nodes`` is a list of ``[x, a]`` pairs in data units from the page's
        curve widget; they are clamped into the window, sorted, and need at
        least two. The function becomes the custom curve, the six sliders
        show its sampling, and only the property re-sends.
        """
        from vibeview.renderers.volume_render import (
            CUSTOM_PRESET,
            VolumeRenderError,
            transfer_function_from_editor,
        )

        state = self.viewer.server.state
        window = state.vol_window
        if not isinstance(nodes, (list, tuple)) or not isinstance(window, list):
            return
        if len(window) != 2:
            return
        try:
            tf = transfer_function_from_editor(
                window=(float(window[0]), float(window[1])),
                colormap=str(state.colormap),
                ramp=[],
                preset=CUSTOM_PRESET,
                opacity_nodes=nodes,
            )
        except VolumeRenderError as e:
            state.status_message = f"Opacity curve: {e}"
            return
        state.vol_preset = CUSTOM_PRESET
        if str(state.vol_mode) == "volume":
            self.viewer.display._refresh_active_volume(hints={"transfer_function": tf})

    def set_volume_preset(self, name: Any = None) -> None:
        """Start the opacity ramp from a preset; the window and colormap stay.

        "Custom curve" names the nodes the editor placed and resets nothing.
        """
        from vibeview.renderers.volume_render import (
            CUSTOM_PRESET,
            PRESET_OPTIONS,
            ramp_for_preset,
        )

        state = self.viewer.server.state
        chosen = str(_helpers._first(name) or "")
        if chosen not in {value for value, _title in PRESET_OPTIONS}:
            return
        state.vol_preset = chosen
        if chosen == CUSTOM_PRESET:
            return
        for index, value in enumerate(ramp_for_preset(chosen)):
            setattr(state, f"vol_ramp_{index}", value)
        if str(state.vol_mode) == "volume":
            self.viewer.display._refresh_volume_transfer_function()

    def update_volume_window(self, end: Any = None, value: Any = None) -> None:
        """Move one end of the window; the other end gives way if they cross.

        The window is the quantisation range too, so moving it re-sends the
        grid; the ramp and the colour nodes follow it.
        """
        state = self.viewer.server.state
        which = str(_helpers._first(end) or "")
        try:
            number = float(_helpers._first(value))
        except (TypeError, ValueError):
            return
        window = state.vol_window
        if which not in ("low", "high") or not isinstance(window, list) or len(window) != 2:
            return
        low, high = float(window[0]), float(window[1])
        bounds = state.vol_window_bounds
        step = float(state.vol_window_step or 0.0) or 1e-9
        if which == "low":
            low = number
            high = max(high, low + step)
        else:
            high = number
            low = min(low, high - step)
        if isinstance(bounds, list) and len(bounds) == 2:
            low = max(low, float(bounds[0]))
            high = min(high, float(bounds[1]))
            if high <= low:
                low, high = float(bounds[0]), float(bounds[1])
        state.vol_window = [low, high]
        state.vol_window_low = low
        state.vol_window_high = high
        if str(state.vol_mode) == "volume":
            self.viewer.display._refresh_volume_transfer_function()

    def update_volume_ramp(self, index: Any = None, value: Any = None) -> None:
        """Set one of the six opacity sliders and redraw through the new ramp."""
        from vibeview.renderers.volume_render import RAMP_POSITIONS

        state = self.viewer.server.state
        try:
            position = int(_helpers._first(index))
            alpha = float(_helpers._first(value))
        except (TypeError, ValueError):
            return
        if not 0 <= position < len(RAMP_POSITIONS):
            return
        setattr(state, f"vol_ramp_{position}", float(min(1.0, max(0.0, alpha))))
        if str(state.vol_mode) == "volume":
            self.viewer.display._refresh_volume_transfer_function()

    def toggle_volume_shade(self, enabled: bool | None = None) -> None:
        """Shade the rendered volume by its gradient, or draw it unshaded."""
        state = self.viewer.server.state
        enabled = _helpers._first(enabled)
        state.vol_shade = (not state.vol_shade) if enabled is None else bool(enabled)
        if str(state.vol_mode) == "volume":
            self.viewer.display._refresh_active_volume()

    def update_isovalue(self, isovalue: float) -> None:
        state = self.viewer.server.state
        state.isovalue = isovalue
        self.viewer.display._refresh_active_volume(hints={"isovalue": isovalue})

    def update_fermi_bands(self, selected: Any) -> None:
        """Re-render the Fermi surface showing only the selected band indices."""
        state = self.viewer.server.state
        state.fermi_selected_bands = list(selected) if selected else []
        section = next(
            (s for s in self.viewer.reader.sections if s.id == state.selected_section), None
        )
        if section is not None and section.kind == "fermi_surface":
            _helpers._activate_fermi(
                self.viewer.reader, self.viewer.plotter, self.viewer.viewer_state, state, section
            )

    def update_colormap(self, colormap: str) -> None:
        state = self.viewer.server.state
        state.colormap = colormap
        if str(state.vol_mode) == "volume" and state.active_volume_id:
            # A producer's custom palette gives way to the chosen colormap.
            hints = self.viewer.viewer_state.get_volume_hints(state.active_volume_id)
            tf = hints.transfer_function
            if isinstance(tf, dict) and tf.get("colormap") == "custom":
                tf = dict(tf)
                tf["colormap"] = colormap
                hints.transfer_function = tf
        self.viewer.display._refresh_active_volume(hints={"colormap": colormap})

    def on_vector_glyphs_changed(
        self,
        stride: Any = None,
        length_scale: Any = None,
        cutoff: Any = None,
        colormap: Any = None,
        **kwargs: Any,
    ) -> None:
        """Redraw the vector-field arrows with the settings the client sent (#156).

        Each control passes all four values with the trigger, which reaches
        the server before the v-model state does (#122).
        """
        state = self.viewer.server.state
        for key, value, kind in (
            ("vector_stride", stride, int),
            ("vector_length_scale", length_scale, float),
            ("vector_cutoff", cutoff, float),
        ):
            try:
                if value is not None:
                    state[key] = kind(value)
            except (TypeError, ValueError):
                pass
        if isinstance(colormap, str) and colormap:
            state.vector_colormap = colormap
        section = next(
            (s for s in self.viewer.reader.sections if s.id == state.vector_section_id), None
        )
        if section is not None and section.kind == "volume.vector":
            _helpers._activate_vector_field(
                self.viewer.reader, self.viewer.plotter, state, section
            )

    def on_bloch_changed(
        self,
        kpoint: Any = None,
        spin: Any = None,
        orbital: Any = None,
        part: Any = None,
        isovalue: Any = None,
        **kwargs: Any,
    ) -> None:
        """Redraw the Bloch orbital the card chose (#162).

        Every control sends all five values with the trigger (#122). A new
        k-point, spin or orbital sends isovalue 0, which re-fits it.
        """
        state = self.viewer.server.state
        section = next(
            (s for s in self.viewer.reader.sections if s.id == state.bloch_section_id), None
        )
        if section is None or section.kind != "wavefunction.bloch":
            return
        selection = dict(dict(state.bloch_selections or {}).get(section.id) or {})
        for key, value, kind in (
            ("kpoint", kpoint, int), ("spin", spin, int),
            ("orbital", orbital, int), ("isovalue", isovalue, float),
        ):
            with contextlib.suppress(TypeError, ValueError):
                if value is not None:
                    selection[key] = kind(value)
        if isinstance(part, str) and part:
            selection["part"] = part
        state.bloch_selections = {**dict(state.bloch_selections or {}), section.id: selection}
        _helpers._activate_bloch(self.viewer.reader, self.viewer.plotter, state, section)

    def on_force_arrows_changed(self, length: Any = None, **kwargs: Any) -> None:
        """Redraw the force arrows at the length the Forces card sent (#159)."""
        state = self.viewer.server.state
        with contextlib.suppress(TypeError, ValueError):
            value = float(length) if length is not None else 0.0
            if math.isfinite(value) and value > 0:
                state.force_arrow_length = value
        section = next(
            (s for s in self.viewer.reader.sections if s.id == state.force_section_id), None
        )
        if section is not None and section.kind == "forces":
            _helpers._activate_forces(self.viewer.reader, self.viewer.plotter, state, section)

    def on_complex_field_changed(
        self, part: Any = None, isovalue: Any = None, **kwargs: Any
    ) -> None:
        """Redraw the complex field with the part and isovalue the client sent (#157).

        Both controls pass both values with the trigger, which reaches the
        server before the v-model state does (#122).
        """
        state = self.viewer.server.state
        if isinstance(part, str) and part:
            state.complex_part = part
        section = next(
            (s for s in self.viewer.reader.sections if s.id == state.complex_section_id), None
        )
        if section is None or section.kind != "volume.complex":
            return
        try:
            value = float(isovalue) if isovalue is not None else 0.0
        except (TypeError, ValueError):
            value = 0.0
        if math.isfinite(value) and value > 0:
            state.complex_isovalues = {**dict(state.complex_isovalues or {}), section.id: value}
        _helpers._activate_complex_field(self.viewer.reader, self.viewer.plotter, state, section)

    def toggle_scf_skip_guess(self, value: Any = None) -> None:
        """Re-render the SCF convergence plot with/without the initial guess."""
        state = self.viewer.server.state
        state.scf_skip_guess = bool(value) if value is not None else not state.scf_skip_guess
        section = next(
            (s for s in self.viewer.reader.sections if s.id == state.selected_section), None
        )
        if section is not None and section.kind == "scf_history":
            _helpers._activate_scf_history(self.viewer.reader, state, section)

    def toggle_scf_log_energy(self, value: Any = None) -> None:
        """Re-render the SCF plot on a linear or log |E − E_final| axis."""
        state = self.viewer.server.state
        state.scf_log_energy = bool(value) if value is not None else not state.scf_log_energy
        section = next(
            (s for s in self.viewer.reader.sections if s.id == state.selected_section), None
        )
        if section is not None and section.kind == "scf_history":
            _helpers._activate_scf_history(self.viewer.reader, state, section)

    def update_spectra_view(
        self,
        gamma_scale: Any = None,
        normalize: Any = None,
        x_unit: Any = None,
        display: Any = None,
        lineshape: Any = None, fwhm: Any = None, unit: Any = None,
        grid_min: Any = None, grid_max: Any = None, grid_step: Any = None,
    ) -> None:
        """Re-render the active spectrum after a display-control change."""
        state = self.viewer.server.state
        if isinstance(gamma_scale, (int, float)) and not isinstance(gamma_scale, bool):
            state.spectra_gamma_scale = max(0.05, float(gamma_scale))
        if normalize is not None:
            state.spectra_normalize = bool(normalize)
        if x_unit is not None:
            state.spectra_x_unit = str(x_unit) or "native"
        if display is not None:
            state.spectra_display = str(display) or "both"
        try:
            for name, value in (("fwhm", fwhm), ("grid_min", grid_min),
                                ("grid_max", grid_max), ("grid_step", grid_step)):
                if value is not None:
                    setattr(state, f"spectra_{name}", float(value) if value != "" else None)
            if lineshape is not None:
                state.spectra_lineshape = str(lineshape)
            if unit is not None:
                state.spectra_width_unit = str(unit)
        except (TypeError, ValueError):
            state.status_message = "Spectrum widths and grid values must be numbers"
            return
        settings = dict(getattr(state, "spectra_settings", {}) or {})
        settings[getattr(state, "spectra_settings_key", "")] = {
            name: getattr(state, f"spectra_{name}", None)
            for name in ("lineshape", "fwhm", "width_unit", "grid_min", "grid_max", "grid_step")
        }
        state.spectra_settings = settings
        section = next(
            (s for s in self.viewer.reader.sections if s.id == state.selected_section), None
        )
        if section is not None and section.kind in _helpers._plot_spectra_kinds():
            _helpers._activate_spectra(self.viewer.reader, state, section, self.viewer._all_readers)

    def _redraw_bands_panel(self) -> None:
        """Re-run whichever activator owns the bottom panel right now."""
        self._redraw_section_panel(("bands", "dos.total", "dos.projected", "dos.coop", "dos.cohp"))

    def _redraw_section_panel(self, kinds: tuple[str, ...]) -> None:
        """Re-run the selected section's activator when its kind is one of ``kinds``.

        A panel that follows a display unit is rebuilt this way when the unit
        is saved (#58); any other selection is left alone.
        """
        state = self.viewer.server.state
        section = next(
            (s for s in self.viewer.reader.sections if s.id == state.selected_section), None
        )
        if section is None or section.kind not in kinds:
            return
        handler = _helpers._section_handler(section.kind)
        assert handler is not None
        handler.activate(
            _helpers._SectionContext(
                self.viewer.reader,
                self.viewer.plotter,
                state,
                self.viewer.viewer_state,
                self.viewer._all_readers,
            ),
            section,
        )

    def update_bands_energy_window(self, *_ignored: Any) -> None:
        """Redraw the bands / DOS chart on a reader-set energy window (#26).

        Called with nothing: the fields v-model straight onto state, so
        both the Enter and the blur binding read the committed values from
        there. A pair that does not make a range — a blank field, an
        inverted pair, a half-typed minus sign — falls back to the
        previous window rather than blanking the chart; that call lives in
        ``_requested_energy_window``.
        """
        state = self.viewer.server.state
        # VTextField with type="number" still v-models as a string.
        state.bands_emin = _helpers._coerce_window_bound(state.bands_emin)
        state.bands_emax = _helpers._coerce_window_bound(state.bands_emax)
        state.bands_window_auto = True
        if state.bands_emin is not None and state.bands_emax is not None:
            state.bands_window_auto = False
        self.viewer.display._redraw_bands_panel()

    def reset_bands_energy_window(self) -> None:
        """Drop a reader-set window, back to the default view (#26)."""
        state = self.viewer.server.state
        state.bands_window_auto = True
        state.bands_emin = None
        state.bands_emax = None
        self.viewer.display._redraw_bands_panel()

    def download_run_attachment(self, role: str | None = None) -> None:
        """Offer one run.record attachment as a browser download.

        Attachments are arbitrary bytes by spec, so they are never
        rendered or interpreted — this is the only path that touches
        their payload, and it hands the bytes straight to a download.
        The media type is forced to ``application/octet-stream`` so the
        browser saves rather than displays: serving an archive-supplied
        type could invite a text/html attachment to be rendered as a
        document.
        """

        role = _helpers._first(role)
        state = self.viewer.server.state
        section_id = str(state.run_record_attachment_section or "")
        if not role or not section_id:
            return
        entry = next(
            (a for a in (state.run_record_attachments or []) if a.get("role") == role),
            None,
        )
        if entry is None:
            state.status_message = f"Unknown attachment: {role}"
            return
        if entry.get("too_large"):
            state.status_message = (
                f"{entry['filename']} is too large to download from the "
                "viewer — extract it from the archive directly."
            )
            return
        try:
            payload = self.viewer.reader.read_run_record_attachment_bytes(section_id, str(role))
        except QVFError as e:
            state.status_message = f"Attachment unreadable: {e}"
            return
        if payload is None:
            state.status_message = f"Attachment unavailable: {role}"
            return
        self.viewer.exports._offer_download(
            payload,
            "application/octet-stream",
            entry["filename"],
            label=f"Attachment ({entry['filename']})",
            fmt="BYTES",
        )
        state.status_message = f"Downloading {entry['filename']}"

    def export_spectra_csv(self) -> None:
        """Export the active spectrum's broadened envelope + peaks as CSV,
        offered as a browser download (design refresh 2026, spectra controls).
        """

        from vibeview.renderers.spectra import SpectraRenderer

        state = self.viewer.server.state
        section = next(
            (s for s in self.viewer.reader.sections if s.id == state.selected_section), None
        )
        if section is None or section.kind not in _helpers._plot_spectra_kinds():
            return
        csv = SpectraRenderer(section, self.viewer.reader).to_csv(
            lineshape=getattr(state, "spectra_lineshape", None),
            fwhm=getattr(state, "spectra_fwhm", None),
            unit=getattr(state, "spectra_width_unit", "native"),
            grid_min=getattr(state, "spectra_grid_min", None),
            grid_max=getattr(state, "spectra_grid_max", None),
            grid_step=getattr(state, "spectra_grid_step", None),
            gamma_scale=float(getattr(state, "spectra_gamma_scale", 1.0)),
            normalize=bool(getattr(state, "spectra_normalize", False)),
            x_unit=str(getattr(state, "spectra_x_unit", "native")),
        )
        stem = _helpers._job_name(self.viewer.reader) or "spectrum"
        self.viewer.exports._offer_download(
            csv.encode("utf-8"),
            "text/csv",
            f"{stem}-{section.id}.csv",
            label=f"Spectrum CSV ({section.id})",
            fmt="CSV",
        )
        state.status_message = f"Exported {state.export_filename}"

    def export_spectra_ascii(self) -> None:
        """Download the same deterministic spectrum file as the CLI."""
        from vibeview.renderers.spectra import SpectraRenderer

        state = self.viewer.server.state
        section = next((s for s in self.viewer.reader.sections
                        if s.id == state.selected_section), None)
        if section is None or section.kind not in _helpers._plot_spectra_kinds():
            return
        try:
            text = SpectraRenderer(section, self.viewer.reader).to_ascii(
                lineshape=getattr(state, "spectra_lineshape", "lorentzian"),
                fwhm=getattr(state, "spectra_fwhm", None),
                unit=getattr(state, "spectra_width_unit", "native"),
                normalize=bool(getattr(state, "spectra_normalize", False)),
                x_unit=getattr(state, "spectra_x_unit", "native"),
                grid_min=getattr(state, "spectra_grid_min", None),
                grid_max=getattr(state, "spectra_grid_max", None),
                grid_step=getattr(state, "spectra_grid_step", None),
            )
        except ValueError as exc:
            state.status_message = str(exc)
            return
        self.viewer.exports._offer_download(
            text.encode("utf-8"), "text/plain", f"spectrum-{section.id}.txt",
            label=f"Spectrum ASCII ({section.id})", fmt="TXT",
        )
        state.status_message = f"Exported {state.export_filename}"

    def toggle_spectra_compare(self, value: Any = None) -> None:
        """Re-render the active spectrum with/without the multi-file overlay."""
        state = self.viewer.server.state
        state.spectra_compare = bool(value) if value is not None else not state.spectra_compare
        section = next(
            (s for s in self.viewer.reader.sections if s.id == state.selected_section), None
        )
        if section is not None and section.kind in _helpers._plot_spectra_kinds():
            _helpers._activate_spectra(self.viewer.reader, state, section, self.viewer._all_readers)

    def toggle_color_by_esp(self, on: bool | None = None) -> None:
        """Enable/disable ESP colour-mapping on the density isosurface."""
        state = self.viewer.server.state
        state.color_by_esp = (not state.color_by_esp) if on is None else bool(on)
        if state.color_by_esp:
            state.colormap = "RdBu"
        self.viewer.display._refresh_active_volume()

    def toggle_reduce_detail(self, on: bool | None = None) -> None:
        """Toggle LOD subsampling for large volume grids."""
        state = self.viewer.server.state
        state.reduce_detail = (not state.reduce_detail) if on is None else bool(on)
        self.viewer.display._refresh_active_volume(invalidate_mesh=True)

    def add_extra_isosurface(self) -> None:
        """Add a secondary isosurface layer at half the current isovalue."""
        state = self.viewer.server.state
        iso = float(state.isovalue or 0.05)
        extras = list(state.extra_isosurfaces or [])
        colour = _palette.EXTRA_ISOSURFACES[len(extras) % len(_palette.EXTRA_ISOSURFACES)]
        extras.append({"isovalue": iso * 0.5, "opacity": 0.35, "colour": colour})
        state.extra_isosurfaces = extras
        self.viewer.display._refresh_active_volume()

    def remove_extra_isosurface(self, index: int) -> None:
        """Remove an extra isosurface layer by index."""
        state = self.viewer.server.state
        extras = list(state.extra_isosurfaces or [])
        if 0 <= index < len(extras):
            extras.pop(index)
            state.extra_isosurfaces = extras
        self.viewer.display._refresh_active_volume()

    def update_opacity(self, opacity: float) -> None:
        state = self.viewer.server.state
        state.opacity = opacity
        self.viewer.display._refresh_active_volume(hints={"opacity": opacity})

    def update_mo_opacity(self, opacity: float) -> None:
        """Retint the orbital lobes.

        Kept separate from update_opacity: MO lobes are their own actors
        (mo_iso_pos/neg) rather than an "active volume", and activating a
        volume section overwrites ``state.opacity`` from that volume's hints,
        which used to clobber whatever the user had set for the orbital.
        Retints in place — re-evaluating the MO grid to change one number
        costs ~1 s.
        """
        state = self.viewer.server.state
        state.mo_opacity = float(opacity)
        retinted = False
        # Every computed surface, not only the orbital's two lobes (#37-36).
        for _name, _actor in list(self.viewer.plotter.actors.items()):
            if not str(_name).startswith(_helpers._Actors.MO_SURFACE):
                continue
            _prop = _actor.GetProperty() if _actor is not None else None
            if _prop is not None:
                _prop.SetOpacity(
                    _helpers._base_opacity(self.viewer.plotter, str(_name)) * float(opacity)
                )
                retinted = True
        if retinted:
            self.viewer.plotter.render()
            _helpers._push_view(self.viewer.plotter)

    def set_periodic_replication(self, value: Any) -> None:
        """Set grid-level periodic orbital replication.

        When > 0 the volume grid is tiled N×N×N into a (2N+1)³
        supercell *before* marching cubes, so orbital isosurfaces
        repeat seamlessly across adjacent unit cells.
        """
        state = self.viewer.server.state
        state.periodic_replication = int(value or 0)
        self.viewer.display._refresh_active_volume()
        state.status_message = f"Cell replication: {state.periodic_replication}"

    def toggle_wrap_periodic(self, on: bool | None = None) -> None:
        """Toggle minimum-image recentre of the active periodic orbital.

        Rolls a face-straddling orbital / Wannier function whole into the
        cell. The mesh cache is keyed on (isovalue, replication), not the
        wrap flag, so invalidate it before rebuilding.
        """
        state = self.viewer.server.state
        state.wrap_periodic_orbital = (not state.wrap_periodic_orbital) if on is None else bool(on)
        self.viewer.display._refresh_active_volume(invalidate_mesh=True)
        state.status_message = (
            "Wrap to cell centre: on" if state.wrap_periodic_orbital else "Wrap to cell centre: off"
        )

    def toggle_wannier_centers(self, on: bool | None = None) -> None:
        """Show/hide the Wannier-centre marker overlay."""
        state = self.viewer.server.state
        state.show_wannier_centers = (not state.show_wannier_centers) if on is None else bool(on)
        n = _helpers._draw_wannier_overlay(
            self.viewer.reader, self.viewer.plotter, state.show_wannier_centers
        )
        _helpers._push_view(self.viewer.plotter)
        if state.show_wannier_centers:
            state.status_message = f"Wannier centres: {n} shown"
        else:
            state.status_message = "Wannier centres: hidden"

    def _set_replication(self, counts: Sequence[int]) -> None:
        """Set the displayed supercell, in the one place that writes it.

        The renderers read ``viewer_state.replication`` and the Nx/Ny/Nz
        inputs show ``replication_n*``. Four writers kept the two in step by
        hand, and a session restore needed its own re-sync (#37-20).
        """
        nx, ny, nz = (int(n) for n in counts)
        self.viewer.viewer_state.replication = (nx, ny, nz)
        state = self.viewer.server.state
        state.replication_nx, state.replication_ny, state.replication_nz = nx, ny, nz

    def update_replication(self, nx: int, ny: int, nz: int) -> None:
        # VTextField with type="number" still v-models as a string.
        nx, ny, nz = int(nx), int(ny), int(nz)
        state = self.viewer.server.state
        # Stop any playing animation first. Each animation loop
        # (trajectory / vibration) rebuilds + pushes a frame on a timer;
        # left running, those frames race the replication rebuild below and
        # keep overwriting it with single-cell geometry, so the supercell
        # flickers or never appears ("multiplying with animations is buggy",
        # reported 2026-07-16). This controller is synchronous, so clearing
        # the flags here means the loops see them false and exit before they
        # can push again.
        if getattr(state, "trajectory_playing", False):
            state.trajectory_playing = False
        if getattr(state, "vibration_playing", False):
            state.vibration_playing = False
        # Clamp non-periodic axes to 1. The UI hides those inputs, but a stale
        # client value or a scripted call must not tile along a synthesized
        # lattice column (for a 2D slab that would stack phantom sheets).
        nx, ny, nz = clamp_replication(
            (nx, ny, nz),
            (bool(state.pbc_a), bool(state.pbc_b), bool(state.pbc_c)),
        )
        self.viewer.display._set_replication((nx, ny, nz))
        # Rebuild the whole 3D scene with new replication
        rebuild_error = _helpers._rebuild_scene(
            self.viewer.reader, self.viewer.plotter, self.viewer.viewer_state, state
        )
        if rebuild_error is None:
            state.status_message = f"Replication: {nx}×{ny}×{nz}"
        # Refit the CLIENT camera to the grown lattice. The scene now spans
        # nx*ny*nz cells but VtkLocalView keeps its own camera, so without
        # this the view stays zoomed into the original single cell (a
        # replicated crystal renders as a few clipped giant atoms). The
        # server-side reset in camera_preset('reset') doesn't reach the
        # local view; local_view.reset_camera does.
        with contextlib.suppress(Exception):
            self.viewer.ctrl.view_reset_camera()

    def toggle_atom_labels(self, show: bool | None = None) -> None:
        # Called with an explicit bool from the right-panel switch
        # (update_modelValue → [$event]) and with no args from the app-bar
        # icon button. The latter flips the current state.
        if show is None:
            show = not bool(self.viewer.server.state.show_atom_labels)
        self.viewer.server.state.show_atom_labels = bool(show)
        _helpers._rebuild_scene(
            self.viewer.reader,
            self.viewer.plotter,
            self.viewer.viewer_state,
            self.viewer.server.state,
        )

    def set_representation_style(self, style: str) -> None:
        """Switch structure representation: ball_and_stick, space_filling,
        sticks_only, wireframe, or cartoon.

        ``cartoon`` needs residue identity (a PDB, not an XYZ). The
        renderer falls back to ball-and-stick without it; say so here
        rather than leaving the picker reading "Cartoon" over an
        unchanged scene.
        """
        valid = {
            "ball_and_stick",
            "space_filling",
            "sticks_only",
            "wireframe",
            "cartoon",
        }
        if style not in valid:
            return
        if style == "cartoon":
            try:
                structure = self.viewer.reader.read_structure()
            except Exception:  # noqa: BLE001 — fall through to the plain switch
                structure = None
            if structure is not None and not (
                structure.has_residues and len(structure.backbone_trace())
            ):
                self.viewer.server.state.status_message = (
                    "Cartoon needs a backbone: this structure carries no "
                    "residue/chain information (open a PDB to use it)."
                )
                return
        self.viewer.server.state.representation_style = style
        self.viewer.server.state.structure_color_mode = (
            self.viewer.server.state.cartoon_color_mode
            if style == "cartoon"
            else self.viewer.server.state.atom_color_mode
        )
        _helpers._rebuild_scene(
            self.viewer.reader,
            self.viewer.plotter,
            self.viewer.viewer_state,
            self.viewer.server.state,
        )

    def set_cartoon_color_mode(self, mode: str) -> None:
        """Colour the cartoon ribbon by chain, secondary structure,
        residue type, or b-factor."""
        mode = _helpers._first(mode)
        if mode not in ("element", "chain", "structure", "residue", "bfactor"):
            return
        self.viewer.server.state.cartoon_color_mode = mode
        self.viewer.server.state.atom_color_mode = mode
        self.viewer.server.state.structure_color_mode = mode
        rebuild_error = _helpers._rebuild_scene(
            self.viewer.reader,
            self.viewer.plotter,
            self.viewer.viewer_state,
            self.viewer.server.state,
        )
        if mode == "bfactor" and rebuild_error is None:
            # The b-factor ramp is normalised over this structure, so the
            # colours mean nothing without the range they span. Say it
            # rather than leaving the user to guess the scale, and say so
            # plainly when the file carries no b-factors at all — the
            # ribbon falls back to chain colour and would otherwise look
            # like the mode silently failed.
            #
            # AFTER a successful rebuild, not before: otherwise a failure
            # message is hidden by the colour-range summary.
            self.viewer.server.state.status_message = _helpers._bfactor_range_message(
                self.viewer.reader
            )

    def set_residue_selection(self, spec: Any = None, *_ignored: Any) -> None:
        """Select residues or chains in every structure representation (D4).

        Takes a selection string — ``A``, ``A/24-38``, ``*/24-38``, comma
        or space separated. See
        ``vibeview.renderers.structure.parse_residue_selection``. Called
        with nothing it reads the field's committed value off state, which
        is how the Enter and blur handlers share one entry point; the DOM
        event object those pass is ignored.

        The summary is the whole point of the control: a selection that
        matches nothing must say so, or it looks like a render bug. So
        this reports the residue count, and calls out an absent chain id
        or an unreadable term separately.
        """
        spec = _helpers._first(spec, "")
        if spec is None or isinstance(spec, dict):
            # No argument, or a DOM event from the blur / Enter handler.
            spec = self.viewer.server.state.residue_selection or ""
        spec = str(spec)
        self.viewer.server.state.residue_selection = spec
        if not spec.strip():
            self.viewer.server.state.residue_visibility = "all"
        self.viewer.server.state.residue_selection_summary = _helpers._residue_selection_summary(
            self.viewer.reader, spec
        )

        # Every representation now draws the selection. Avoid a rebuild only
        # on structures that have no residue membership and therefore cannot
        # match any term.
        if self.viewer.server.state.residue_selection_available:
            _helpers._rebuild_scene(
                self.viewer.reader,
                self.viewer.plotter,
                self.viewer.viewer_state,
                self.viewer.server.state,
            )

    def set_residue_visibility(self, mode: str = "all") -> None:
        if mode not in ("all", "isolate", "hide"):
            return
        if mode != "all":
            from vibeview.renderers.structure import _selected_atom_indices, parse_residue_selection

            terms, _ = parse_residue_selection(self.viewer.server.state.residue_selection or "")
            if not _selected_atom_indices(self.viewer.reader.read_structure(), terms):
                self.viewer.server.state.residue_visibility = "all"
                self.viewer.server.state.status_message = (
                    "Select residues before hiding or isolating them."
                )
                return
        self.viewer.server.state.residue_visibility = mode
        _helpers._rebuild_scene(
            self.viewer.reader,
            self.viewer.plotter,
            self.viewer.viewer_state,
            self.viewer.server.state,
        )

    def set_residue_pick_mode(self, enabled: bool = False) -> None:
        self.viewer.server.state.residue_pick_mode = bool(enabled)
        if enabled:
            self.viewer.server.state.measure_mode = False
            self.viewer.server.state.edit_mode = False
            self.viewer.server.state.status_message = (
                "Click a residue to select it; use the selection field for ranges."
            )
        else:
            self.viewer.server.state.status_message = "Residue picking off"

    def apply_inspection(self) -> None:
        _helpers._rebuild_scene(self.viewer.reader, self.viewer.plotter,
                                self.viewer.viewer_state, self.viewer.server.state)

    def toggle_hydrogen_bonds(self, enabled: bool = False) -> None:
        self.viewer.server.state.show_hydrogen_bonds = bool(enabled)
        _helpers._rebuild_scene(
            self.viewer.reader,
            self.viewer.plotter,
            self.viewer.viewer_state,
            self.viewer.server.state,
        )
        if enabled:
            from vibeview.renderers.structure import hydrogen_bond_contacts

            count = len(hydrogen_bond_contacts(self.viewer.reader.read_structure()))
            self.viewer.server.state.status_message = (
                f"{count} geometric hydrogen-bond contact(s) in the input cell; "
                "explicit H required."
            )

    def set_element_color(self, z: Any = None, color: Any = None) -> None:
        """Override the render colour of one element (design refresh 2026).

        Keyed by atomic number; ``set_color_overrides`` feeds cpk_color(), so
        every render path — glyph batching, per-atom actors, replicated cells,
        compare mode — picks the new colour up on the rebuild below.
        """
        from vibeview.renderers.structure import set_color_overrides

        state = self.viewer.server.state
        z = _helpers._first(z)
        if z is None:
            z = state.element_color_z
        if not color:
            color = state.element_color_value
        if z is None or not color:
            state.status_message = "Pick an element and a colour first"
            return
        # dict keys round-trip through the client as strings; normalise.
        overrides = {int(k): v for k, v in (state.element_colors or {}).items()}
        overrides[int(z)] = str(color)
        state.element_colors = {str(k): v for k, v in overrides.items()}
        set_color_overrides(overrides)
        rebuild_error = _helpers._rebuild_scene(
            self.viewer.reader, self.viewer.plotter, self.viewer.viewer_state, state
        )
        sym = next(
            (e["title"] for e in (state.element_color_options or []) if e["value"] == int(z)),
            str(z),
        )
        if rebuild_error is None:
            state.status_message = f"{sym} colour → {color}"

    def reset_element_colors(self) -> None:
        """Drop all per-element colour overrides (back to the CPK palette)."""
        from vibeview.renderers.structure import set_color_overrides

        state = self.viewer.server.state
        state.element_colors = {}
        set_color_overrides(None)
        rebuild_error = _helpers._rebuild_scene(
            self.viewer.reader, self.viewer.plotter, self.viewer.viewer_state, state
        )
        if rebuild_error is None:
            state.status_message = "Element colours reset to CPK"

    def set_material_preset(self, name: str) -> None:
        """Apply a material preset to the scene."""
        self.viewer.server.state.material_preset = name
        self.viewer.server.state.background_override = None
        preset = _helpers._apply_scene_appearance(self.viewer.plotter, self.viewer.server.state)
        self.viewer.server.state.status_message = f"Material: {preset.name}"
        self.viewer.lifecycle._counted_view_update()

    def set_charge_kind(self, kind: str) -> None:
        self.viewer.server.state.charge_kind = kind or "mulliken"
        if self.viewer.server.state.atom_properties_active:
            # Re-render both the HTML table (filtered to selected method)
            # and the 3D colour overlay.
            overlay_attempted = False
            try:
                section = self.viewer.reader.get_section(
                    self.viewer.server.state.atom_properties_section_id
                )
                from vibeview.renderers.atom_properties import AtomPropertiesRenderer

                renderer = AtomPropertiesRenderer(section, self.viewer.reader)
                with contextlib.suppress(QVFError):
                    self.viewer.server.state.properties_html = renderer.render_to_html(
                        charge_kind=(
                            self.viewer.server.state.charge_kind
                            if self.viewer.server.state.charge_kind
                            else None
                        )
                    )
                overlay_attempted = True
                _helpers._render_atom_properties_overlay(
                    self.viewer.reader, self.viewer.plotter, self.viewer.server.state
                )
            except Exception as e:  # noqa: BLE001 — keep UI state truthful
                if not overlay_attempted:
                    _helpers._rollback_atom_properties_overlay(
                        self.viewer.reader, self.viewer.plotter, self.viewer.server.state, push=True
                    )
                self.viewer.server.state.atom_properties_active = False
                self.viewer.server.state.atom_properties_section_id = None
                self.viewer.server.state.status_message = f"Charge overlay error: {e}"

    # ── Controller: raytracing toggle ─────────────────────────────────
    def toggle_raytrace(self) -> None:
        """Toggle OSPRay raytraced rendering on/off.

        PyVista has no ``enable_ray_tracing`` method — OSPRay is wired by
        attaching a ``vtkOSPRayPass`` to the renderer. Guarded by the
        startup availability probe so it never throws on builds without
        OSPRay (the button is also hidden in that case).
        """
        if not self.viewer.server.state.raytrace_available:
            self.viewer.server.state.status_message = (
                "Ray tracing unavailable: this VTK build has no OSPRay"
            )
            self.viewer.server.state.raytrace_enabled = False
            return
        enabled = not self.viewer.server.state.raytrace_enabled
        try:
            import vtk

            renderer = self.viewer.plotter.renderer
            if enabled:
                self.viewer.plotter._ospray_pass = vtk.vtkOSPRayPass()
                renderer.SetPass(self.viewer.plotter._ospray_pass)
                self.viewer.server.state.status_message = "Ray tracing enabled (OSPRay)"
            else:
                renderer.SetPass(None)
                self.viewer.server.state.status_message = "Ray tracing disabled"
            self.viewer.server.state.raytrace_enabled = enabled
            self.viewer.lifecycle._counted_view_update()
        except Exception as e:
            self.viewer.server.state.status_message = f"Ray tracing unavailable: {e}"
            self.viewer.server.state.raytrace_enabled = False

    def toggle_ssao(self, enabled: bool | None = None) -> None:
        """Toggle screen-space ambient occlusion."""
        state = self.viewer.server.state
        state.ssao_enabled = (not state.ssao_enabled) if enabled is None else bool(enabled)
        if state.ssao_enabled:
            try:
                from vibeview.material_presets import ambient_occlusion_pass

                ambient_occlusion_pass(self.viewer.plotter, **_helpers._SSAO_PASS)
                state.status_message = "Ambient occlusion on (exported images)"
            except Exception:
                state.status_message = "Ambient occlusion unavailable on this VTK build"
                state.ssao_enabled = False
        else:
            # Restore default render pass
            with contextlib.suppress(Exception):
                self.viewer.plotter.renderer.SetPass(None)
            state.status_message = "Ambient occlusion off"
        # Refresh the framebuffer now: screenshots taken from the current
        # frame otherwise show the pre-toggle pass state.
        with contextlib.suppress(Exception):
            self.viewer.plotter.render()
        _helpers._push_view(self.viewer.plotter)

    def toggle_toon(self, enabled: bool | None = None) -> None:
        """Toggle non-photorealistic toon rendering.

        Wired like ``toggle_clip``: the switch sends
        ``update_modelValue=(ctrl.toggle_toon, "[$event]")`` *after* its
        ``v_model`` already wrote ``toon_mode``, so with an argument we
        set (never flip — a second flip would cancel the toggle out).
        The old zero-argument signature made every switch flip raise
        TypeError server-side, which is why the switch visibly did
        nothing. No argument (palette / keyboard use) still flips.
        """
        state = self.viewer.server.state
        state.toon_mode = (not state.toon_mode) if enabled is None else bool(enabled)
        if state.toon_mode:
            from vibeview.renderers.structure import enable_toon_rendering

            ok = enable_toon_rendering(self.viewer.plotter)
            state.status_message = (
                "Toon rendering enabled" if ok else "Toon rendering not available"
            )
            if not ok:
                state.toon_mode = False
        else:
            from vibeview.renderers.structure import disable_toon_rendering

            disable_toon_rendering(self.viewer.plotter)
            state.status_message = "Toon rendering disabled"
        _helpers._push_view(self.viewer.plotter)

    def toggle_orthographic(self, enabled: bool | None = None) -> None:
        """Switch between perspective and orthographic (parallel) projection.

        Orthographic drops perspective foreshortening — parallel lattice
        edges stay parallel — which is what crystallography and publication
        figures want (design refresh 2026, rendering options).
        """
        state = self.viewer.server.state
        state.orthographic_projection = (
            (not state.orthographic_projection) if enabled is None else bool(enabled)
        )
        # Best-effort: no camera headless, and the client push mirrors
        # camera_preset so VtkLocalView picks up the projection (audit L1).
        with contextlib.suppress(Exception):
            self.viewer.plotter.camera.parallel_projection = bool(state.orthographic_projection)
            self.viewer.plotter.render()
            push = _helpers._scene_hooks(self.viewer.plotter).push_camera
            if push is not None:
                push()
        state.status_message = (
            "Orthographic projection" if state.orthographic_projection else "Perspective projection"
        )

    def toggle_figure_outlines(self, enabled: bool | None = None) -> None:
        """Outline every atom and bond with a black inverted hull (#68).

        The hull is geometry, so unlike a silhouette pass it reaches the
        vtk.js client and stays correct as the client rotates. Isosurfaces,
        ribbons, cell edges and labels are not outlined.
        """
        state = self.viewer.server.state
        enabled = _helpers._first(enabled)
        state.figure_outlines = (not state.figure_outlines) if enabled is None else bool(enabled)
        _helpers._apply_scene_appearance(self.viewer.plotter, state)
        state.status_message = (
            "Figure outlines on" if state.figure_outlines else "Figure outlines off"
        )
        self.viewer.lifecycle._counted_view_update()

    def toggle_chrome(self, enabled: bool | None = None, *, push: bool = True) -> None:
        """Hide or show the viewport chrome for a clean figure (#68).

        Chrome is what the page draws over, around or beside the scene
        without being part of it: the bounds grid and its axis labels, the
        status, session and job chips in the app bar, the atom hover tooltip
        and the welcome card. Hiding it also leaves measure and edit mode,
        whose pick markers and selection highlights are chrome inside the
        scene; entering either mode again draws them. The drawers, the result
        panel, the unit cell and measurements stay, as content; presentation
        mode remains the full-screen view. A view preset passes
        ``push=False`` and lets its one rebuild push the grid change.
        """
        state = self.viewer.server.state
        enabled = _helpers._first(enabled)
        state.chrome_hidden = (not state.chrome_hidden) if enabled is None else bool(enabled)
        if state.chrome_hidden:
            if state.measure_mode:
                self.viewer.editing.toggle_measure_mode(False)
            if state.edit_mode:
                self.viewer.editing.toggle_edit_mode()
            state.hover_tooltip_visible = False
        _helpers._show_scene_grid(self.viewer.plotter, state)
        state.status_message = (
            "Viewport chrome hidden" if state.chrome_hidden else "Viewport chrome shown"
        )
        if push:
            _helpers._push_view(self.viewer.plotter)

    def show_memory(self) -> None:
        """Display the decoded arrays the active reader holds, by section kind."""
        kinds = {s.id: s.kind for s in self.viewer.reader.sections}
        usage: dict[str, int] = {}
        for section_id, size in self.viewer.reader.cached_bytes().items():
            kind = kinds.get(section_id, section_id)
            usage[kind] = usage.get(kind, 0) + size
        lines = ["Memory usage by section kind:"]
        for kind, size in sorted(usage.items(), key=lambda x: -x[1]):
            lines.append(f"  {kind}: {human_size(size)}")
        lines.append(f"  Total: {human_size(sum(usage.values()))}")
        self.viewer.server.state.memory_usage = "\n".join(lines)
        self.viewer.server.state.status_message = self.viewer.server.state.memory_usage.split("\n")[
            0
        ]

    # ── Controller: display toggles ───────────────────────────────────
    # ``toggle_atom_labels`` is wired earlier via ``@ctrl.set`` so the
    # tuple-form ``update_modelValue=(ctrl.toggle_atom_labels, "[$event]")``
    # binding on the right-panel switch actually routes to the function.
    # The previous ``@ctrl.trigger`` registration + ``_add_atom_labels``
    # helper here was a no-op (FunctionNotImplementedError on click — the
    # proxy was registered as a trigger without a func, AND the helper
    # read ``structure.payload`` which doesn't exist on Section).
    def toggle_background(self) -> None:
        """Toggle between dark and light 3D viewport background."""
        dark = not self.viewer.server.state.dark_background
        _helpers._set_background_mode(self.viewer.plotter, self.viewer.server.state, dark)
        self.viewer.server.state.status_message = f"Background: {'dark' if dark else 'light'}"
        self.viewer.lifecycle._counted_view_update()

    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("camera_preset"), which needs a trigger registration.
    def camera_preset(self, view: Any) -> None:
        """Set the camera to a named preset view.

        Receives a plain string from the client (JS ``'isometric'``,
        not ``['isometric']``).  Also handles the list-wrapped legacy
        path for robustness during migration.
        """
        view = _helpers._first(view)
        presets: dict[str, Callable[[], Any]] = {
            "isometric": lambda: view_isometric(self.viewer.plotter),
            "xy": lambda: view_xy(self.viewer.plotter),
            "xz": lambda: view_xz(self.viewer.plotter),
            "yz": lambda: view_yz(self.viewer.plotter),
            "reset": lambda: reset_camera(self.viewer.plotter),
        }
        fn = presets.get(view)
        if fn is not None:
            fn()
            self.viewer.plotter.render()
            self.viewer.server.state.status_message = f"Camera: {view}"
            self.viewer.lifecycle._counted_view_update()
            # Move the client's own camera, not just geometry (L1).
            self.viewer.ctrl.view_push_camera()
