"""Files controllers for one browser viewer instance."""

from __future__ import annotations

import asyncio
import base64
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibeview.plotter_compat import (
    reset_camera,
    view_isometric,
)
from vibeview.qvf import QVFError
from vibeview.viewer_defaults import ViewerState

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader


from vibeview.app import helpers as _helpers

if TYPE_CHECKING:
    from vibeview.app.runtime import ViewerRuntime
    from vibeview.sketch import SketchSession

#: Seconds between looks at the vibe-sketch hand-off directory.
SKETCH_POLL_SECONDS = 1.0


class FilesControllers:
    def __init__(self, viewer: ViewerRuntime) -> None:
        self.viewer = viewer

    def _wavefunction_render_is_current(
        self,
        epoch: int,
        section_id: str,
        source_reader: QVFReader | None,
    ) -> bool:
        with self.viewer._wf_epoch_lock:
            return (
                epoch == self.viewer._wf_render_epoch
                and self.viewer.reader is source_reader
                and self.viewer.server.state.wf_section_id == section_id
            )

    def _invalidate_wavefunction_renders(self) -> int:
        """Cancel animation and make every already-running MO render stale."""
        with self.viewer._wf_epoch_lock:
            self.viewer._wf_render_epoch += 1
            timer = self.viewer._wf_anim_timer
            self.viewer._wf_anim_timer = None
            self.viewer.server.state.wf_animating = False
            epoch = self.viewer._wf_render_epoch
        if timer is not None:
            timer.cancel()
        return epoch

    def _cancel_live_opt_for_reader_change(self) -> None:
        """Stop geometry callbacks that belong to a replaced reader."""
        self.viewer.editing._cancel_live_opt_intent(clear_status=True)

    def _clear_reader_scoped_edit_state(self) -> None:
        """Discard atom-indexed edit state before binding another structure."""
        state = self.viewer.server.state
        state.edit_history.clear()
        state.edit_future.clear()
        # Both stacks are mutated in place. Without dirty(), Trame's client
        # snapshot aliases these lists and leaves Undo/Redo enabled for the
        # previous reader.
        state.dirty("edit_history", "edit_future")
        state.edit_selected = []
        state.dirty("edit_selected")
        _helpers._remove_actors_by_prefix(self.viewer.plotter, _helpers._Actors.EDIT_HIGHLIGHT)
        _helpers._reset_frozen(self.viewer.plotter, state)

        # These interaction targets all contain atom indices or world-space
        # coordinates from the outgoing structure.
        state.element_picker_open = False
        state.element_picker_target = "new"
        state.context_menu_show = False
        state.context_menu_atom_idx = -1
        state.hover_tooltip = ""
        state.hover_tooltip_visible = False
        self.viewer._ctx_menu_target.update(world=None, atom_idx=-1)
        self.viewer._hover_state.update(world=None, atom_idx=-1, at=float("-inf"))

    def _update_live_status(self, r: QVFReader) -> None:
        """Reflect a streaming checkpoint's provenance in the app bar."""
        status, text = _helpers._checkpoint_summary(r)
        self.viewer.server.state.live_run_status = status
        self.viewer.server.state.live_checkpoint_text = text

    def _reload_active_file(self, preserve_camera: bool = False, same_file: bool = False) -> None:
        """Invalidate worker renders, then replace the VTK scene exclusively."""
        self.viewer.wavefunction._cancel_relocalization()
        self.viewer.files._invalidate_wavefunction_renders()
        with self.viewer._wf_render_lock:
            return self.viewer.files._reload_active_file_unlocked(preserve_camera, same_file)

    def _reload_active_file_unlocked(
        self, preserve_camera: bool = False, same_file: bool = False
    ) -> None:
        """Rebuild the entire UI for the currently-active reader.

        Called once during initialisation (index 0) and again whenever
        the user switches files via the AppBar dropdown.  Uses
        the shared runtime to replace reader-scoped state so all handlers
        and helpers see the new reader and its data.

        ``preserve_camera=True`` keeps the user's current viewpoint
        instead of applying the file's camera bookmark — the file
        watcher's hot-reload path, where yanking the camera on every
        checkpoint would make live monitoring unusable.

        ``same_file=True`` marks that hot reload: the incoming reader is a
        fresh object for the SAME archive, so state scoped to the file
        (the residue selection) survives even though the reader changed.
        """

        incoming_reader = self.viewer._all_readers[self.viewer.server.state.active_file_idx]
        reader_changed = incoming_reader is not self.viewer.reader
        if incoming_reader is not self.viewer.reader:
            self.viewer.files._cancel_live_opt_for_reader_change()
            self.viewer.files._clear_reader_scoped_edit_state()
            # A residue selection names one file's chains and residues;
            # re-applying it to another structure highlighted arbitrary
            # residues. Maintainer decision (#37-19): a switch to another
            # file clears it; a hot reload of the same file keeps it. The
            # watcher always hands over a fresh reader object, so reader
            # identity cannot tell the two apart; its same_file flag does.
            if not same_file:
                self.viewer.server.state.residue_selection = ""

        # Probe the incoming file (a structure-less file must not inherit
        # the previous file's periodic flag), so the orbital group title
        # below is right before _build_structure_scene rebinds it (#37-6).
        self.viewer.is_periodic = _helpers._probe_is_periodic(incoming_reader)

        self.viewer.reader = incoming_reader
        self.viewer.viewer_state = ViewerState.from_manifest(
            self.viewer.reader.viewer_defaults, {s.id: s.kind for s in self.viewer.reader.sections}
        )
        _helpers._clamp_viewer_replication(self.viewer.reader, self.viewer.viewer_state)
        self.viewer.server.state.residue_visibility = "all"
        self.viewer.server.state.residue_pick_mode = False
        self.viewer.server.state.residue_selection_chains = _helpers._selectable_chains(
            self.viewer.reader
        )
        self.viewer.server.state.residue_selection_available = (
            _helpers._residue_selection_available(self.viewer.reader)
        )
        self.viewer.server.state.residue_selection_summary = _helpers._residue_selection_summary(
            self.viewer.reader, str(self.viewer.server.state.residue_selection or "")
        )
        self.viewer.display._set_replication(self.viewer.viewer_state.replication)
        replication = list(self.viewer.viewer_state.replication)

        # Rebuild section list, in place: the UI retains the same list.
        self.viewer.section_list[:] = _helpers._build_section_list(self.viewer.reader)
        self.viewer.server.state.section_list = self.viewer.section_list
        # section_list is a shared list mutated in place; trame's
        # setattr drops the push when the assigned object IS the pushed one
        # (`value == pushed_state[key]` — identity ⇒ equal), so the client
        # would never see the rebuilt list without an explicit dirty().
        self.viewer.server.state.dirty("section_list")

        # Rebuild sidebar entries with the same builder the first load uses
        # (#37-25); the copy that stood here once dropped the basis.ao group.
        self.viewer.orbital_sections = [
            s for s in self.viewer.section_list if s["kind"] == "volume.orbital"
        ]
        self.viewer.basis_ao_sections = [
            s for s in self.viewer.section_list if s["kind"] == "basis.ao"
        ]
        self.viewer.sidebar_entries[:] = _helpers._build_sidebar_entries(
            self.viewer.reader, self.viewer.section_list, self.viewer.is_periodic
        )
        self.viewer.server.state.sidebar_entries = self.viewer.sidebar_entries
        self.viewer.server.state.dirty("sidebar_entries")  # same in-place-mutation issue
        # The palette's "Open section" entries belong to the loaded file.
        self.viewer.server.state.palette_actions = _helpers._palette_actions_for(self.viewer.reader)

        self.viewer.orbital_options = [
            {"title": s["id"], "value": s["id"]} for s in self.viewer.orbital_sections
        ]
        self.viewer.server.state.orbital_options = self.viewer.orbital_options
        self.viewer.server.state.selected_orbital = (
            self.viewer.orbital_options[0]["value"] if self.viewer.orbital_options else None
        )
        self.viewer.ao_options = [
            {"title": s["id"], "value": s["id"]} for s in self.viewer.basis_ao_sections
        ]
        self.viewer.server.state.ao_options = self.viewer.ao_options
        self.viewer.server.state.selected_ao = (
            self.viewer.ao_options[0]["value"] if self.viewer.ao_options else None
        )

        # Update header title (derived state, not stored).
        self.viewer.server.state.header_title = _helpers._header_title(self.viewer.reader)
        self.viewer.server.state.shown_structure_id = _helpers._shown_structure_marker(
            self.viewer.reader
        )
        self.viewer.server.state.element_color_options = _helpers._present_elements(
            self.viewer.reader
        )
        self.viewer.server.state.run_info = _helpers._run_info_text(self.viewer.reader)
        self.viewer.files._update_live_status(self.viewer.reader)
        from vibeview.renderers.wannier import find_wannier_section

        self.viewer.server.state.has_wannier_centers = (
            find_wannier_section(self.viewer.reader) is not None
        )
        self.viewer.server.state.file_names = [
            _helpers._job_name(r) for r in self.viewer._all_readers
        ]
        self.viewer.server.state.compare_highlight_options = (
            [
                {"title": f"{n} (file {i + 1})", "value": i}
                for i, n in enumerate(self.viewer.server.state.file_names)
            ]
            if len(self.viewer._all_readers) > 1
            else []
        )

        # Clear all active panel state.
        _helpers._clear_output_panels(self.viewer.server.state)
        self.viewer.server.state.selected_section = None
        self.viewer.server.state.status_message = ""
        self.viewer.server.state.is_periodic = False
        # File-switch-only resets (not in _clear_output_panels, which also
        # runs on every section click): a stale atom selection from the
        # previous file would otherwise index the new (possibly smaller)
        # structure and crash _measure_text (A1-02). The scene is rebuilt
        # below, so the static structure is shown (A5-03).
        self.viewer.server.state.measure_mode = False
        self.viewer.server.state.selected_atoms = []
        self.viewer.server.state.measure_result = ""
        self.viewer.server.state.structure_hidden = False
        _helpers._reset_frozen(
            self.viewer.plotter, self.viewer.server.state
        )  # frozen indices belong to the old file

        # Rebuild the 3D scene.
        saved_camera = self.viewer.plotter.camera_position if preserve_camera else None
        _helpers._reset_scene(self.viewer.plotter, self.viewer.server.state)

        self.viewer.structure_renderer, self.viewer.is_periodic = _helpers._build_structure_scene(
            self.viewer.reader,
            self.viewer.plotter,
            self.viewer.server.state,
            replication=_helpers._triple(replication),
            show_labels=bool(self.viewer.server.state.show_atom_labels),
            representation=str(
                getattr(self.viewer.server.state, "representation_style", "ball_and_stick")
            ),
            cartoon_color_mode=str(
                getattr(self.viewer.server.state, "cartoon_color_mode", "chain")
            ),
            residue_selection=str(getattr(self.viewer.server.state, "residue_selection", "") or ""),
        )

        # QA validation: check SCF convergence, energy sanity, etc. Always
        # assigned: the previous file's warnings must not outlive it (#37-5).
        qa_warnings = _helpers._qa_validate(self.viewer.reader)
        self.viewer.server.state.qa_warnings = list(qa_warnings or [])
        if qa_warnings and not self.viewer.server.state.status_message:
            self.viewer.server.state.status_message = qa_warnings[0]

        _helpers._show_scene_grid(self.viewer.plotter, self.viewer.server.state)
        _helpers._apply_scene_appearance(self.viewer.plotter, self.viewer.server.state)
        if saved_camera is not None:
            self.viewer.plotter.camera_position = saved_camera
        else:
            view_isometric(self.viewer.plotter)
            # Apply the camera BEFORE pushing so the new file's bookmark
            # reaches the client (previously the push happened first, so the
            # camera was set server-side but never sent — A1-04).
            _helpers._apply_camera(self.viewer.plotter, self.viewer.viewer_state.camera)
            _helpers._sync_orthographic_switch(self.viewer.plotter, self.viewer.server.state)
        _helpers._push_view(self.viewer.plotter)
        if saved_camera is None:
            # _push_view sends geometry; in VtkLocalView the client owns the
            # camera and keeps its own until one is pushed explicitly. Without
            # this the server framed the new structure correctly and the
            # browser went on showing the previous file's zoom -- building
            # phenol while water was open left the ring far outside the
            # viewport. Skipped when preserving the camera, which is the
            # hot-reload path that deliberately keeps the user's viewpoint.
            _helpers._push_camera(self.viewer.plotter)

        # Re-bootstrap the cross-fade card for the new file: it must turn ON
        # for a file that declares a crossfade and OFF for one that doesn't,
        # rather than inheriting the previous file's state (A1-03).
        if self.viewer.viewer_state.crossfade_volumes:
            self.viewer.server.state.crossfade_active = True
            self.viewer.server.state.crossfade_blend = self.viewer.viewer_state.crossfade_blend
            va, vb = self.viewer.viewer_state.crossfade_volumes
            _helpers._rebuild_crossfade(
                self.viewer.reader,
                self.viewer.plotter,
                self.viewer.viewer_state,
                self.viewer.server.state,
                va,
                vb,
                self.viewer.viewer_state.crossfade_blend,
            )
            # Cross-fade actors are created after the first scene push.
            _helpers._apply_scene_appearance(self.viewer.plotter, self.viewer.server.state)
            self.viewer.plotter.render()
            _helpers._push_view(self.viewer.plotter)
        else:
            self.viewer.server.state.crossfade_active = False

        if reader_changed:
            self.viewer.files._sync_file_watcher_for_active_reader()

    def build_molecule(self) -> None:
        """Build a structure from a molecule name typed into the toolbar.

        The generated geometry comes from ``vibeqc_naming``'s curated
        structure database, not from a conformer search — it is a starting
        point for a calculation, not a converged geometry.
        """
        name = (self.viewer.server.state.builder_name or "").strip()
        if not name:
            return
        try:
            from vibeqc_naming import known_names, structure_from_name
        except ImportError:
            self.viewer.server.state.status_message = (
                "Molecule builder needs the vibeqc_naming package, which is not installed"
            )
            return

        try:
            atoms = structure_from_name(name)
        except Exception as e:  # noqa: BLE001 — a bad name must not kill the session
            self.viewer.server.state.status_message = f"Could not build {name!r}: {e}"
            return
        if not atoms:
            self.viewer.server.state.status_message = (
                f"Unknown molecule {name!r} — {len(known_names())} names are available"
            )
            return

        from vibeview.converters import atoms_to_qvf
        from vibeview.qvf import QVFReader

        try:
            new_reader = QVFReader(atoms_to_qvf(atoms, label=name))
        except (QVFError, ValueError) as e:
            self.viewer.server.state.status_message = f"Could not build {name!r}: {e}"
            return

        self.viewer.files._adopt_reader(new_reader)
        self.viewer.server.state.builder_name = ""
        self.viewer.server.state.builder_dialog = False
        self.viewer.server.state.status_message = (
            f"Built {name} — {len(atoms)} atoms (idealized geometry)"
        )

    def build_from_smiles(self) -> None:
        """Build a 3D structure from a SMILES string typed into the dialog.

        RDKit embeds the initial geometry (ETKDG); the relax that makes it a
        real structure is vibe-qc's, per roadmap decision 2. Auto-optimize is
        switched on for the new file rather than run once here, so the user
        watches it converge in the viewport and can undo it — the same path
        every other editor mutation takes.
        """
        smiles = (self.viewer.server.state.builder_smiles or "").strip()
        if not smiles:
            return

        from vibeview.converters import smiles_to_qvf
        from vibeview.qvf import QVFReader

        try:
            new_reader = QVFReader(smiles_to_qvf(smiles))
        except (QVFError, ValueError) as e:
            # smiles_to_qvf raises ValueError for a missing extra, an
            # unparseable string, and a failed embed alike, and each message
            # already names the cause — including the pip command.
            self.viewer.server.state.status_message = str(e)
            return

        self.viewer.files._adopt_reader(new_reader)
        n_atoms = len(new_reader.read_structure().atoms)
        self.viewer.server.state.builder_smiles = ""
        self.viewer.server.state.builder_dialog = False
        self.viewer.server.state.live_opt_enabled = True
        self.viewer.editing._live_opt_schedule()
        self.viewer.server.state.status_message = (
            f"Built {smiles} — {n_atoms} atoms (ETKDG start; relaxing with vibe-qc)"
        )

    def sketch_molecule(self) -> None:
        """Open vibe-sketch to draw a molecule (#77).

        The sketcher runs as a child process and sends structures back through
        the hand-off directory that :mod:`vibeview.sketch` describes; a poll
        task opens each one as a new file. A second click while the sketcher
        runs reopens its page instead of starting another server.
        """
        from vibeview import sketch

        state = self.viewer.server.state
        if not sketch.sketch_available():
            state.sketch_available = False
            state.status_message = sketch.sketch_missing_message()
            return
        session = self.viewer._sketch_session
        if session is not None and session.running:
            session.open_browser()
            state.builder_dialog = False
            state.status_message = (
                f"vibe-sketch is open at {session.url}; Send to vibe-view brings a structure here"
            )
            return
        if session is not None:
            self.viewer.files._stop_sketch()
        try:
            session = sketch.SketchSession.launch()
        except OSError as exc:
            state.status_message = f"Could not start vibe-sketch: {exc}"
            return
        self.viewer._sketch_session = session
        coroutine = self.viewer.files._sketch_poll_loop(session)
        try:
            self.viewer._sketch_task = asyncio.ensure_future(coroutine)
        except RuntimeError as exc:
            # Without a scheduler nothing would ever pick up a hand-off.
            coroutine.close()
            self.viewer.files._stop_sketch()
            state.status_message = f"Could not start vibe-sketch: {exc}"
            return
        state.sketch_running = True
        state.builder_dialog = False
        state.status_message = (
            f"Opening vibe-sketch at {session.url}; Send to vibe-view brings a structure here"
        )

    async def _sketch_poll_loop(self, session: SketchSession) -> None:
        """Pick up hand-offs until *session* ends or is replaced."""
        while self.viewer._sketch_session is session:
            await asyncio.sleep(SKETCH_POLL_SECONDS)
            if self.viewer._sketch_session is not session:
                break
            try:
                self.viewer.files.poll_sketch()
            except Exception as exc:  # noqa: BLE001 — one failed render must not end the hand-off
                self.viewer.server.state.status_message = (
                    f"Could not show the structure from vibe-sketch: {exc}"
                )
            self.viewer.server.state.flush()

    def poll_sketch(self) -> None:
        """Open what the sketcher sent since the last poll.

        Each structure becomes a new file, and the last one is shown. A record
        the contract refuses, or a file that cannot be read, is reported and
        adds nothing. Once the sketcher has exited and nothing is left to read,
        the session is closed and the exit reported.
        """
        from vibeview.sketch import SketchHandoffError, load_sketched_structure

        session = self.viewer._sketch_session
        if session is None:
            return
        state = self.viewer.server.state
        opened: list[str] = []
        refused: list[str] = []
        for item in session.receive():
            if isinstance(item, SketchHandoffError):
                refused.append(str(item))
                continue
            try:
                reader = load_sketched_structure(item)
                n_atoms = len(reader.read_structure().atoms)
            except Exception as exc:  # noqa: BLE001 — a bad hand-off must not end the session
                refused.append(f"{item.name}: {str(exc) or type(exc).__name__}")
                continue
            self.viewer._all_readers.append(reader)
            opened.append(f"{item.name} ({n_atoms} atoms)")
        if opened:
            self.viewer.files._activate_file(len(self.viewer._all_readers) - 1)
        messages: list[str] = []
        if opened:
            messages.append(f"Received {', '.join(opened)} from vibe-sketch")
        if refused:
            messages.append(f"Could not open from vibe-sketch: {'; '.join(refused)}")
        if not session.running and not session.pending_records():
            messages.append(session.exit_message())
            self.viewer.files._stop_sketch()
        if messages:
            state.status_message = " — ".join(messages)

    def _stop_sketch(self) -> None:
        """Stop the sketcher this viewer started and forget its session.

        The poll loop notices the session is gone and returns on its own, so
        the task is not cancelled from inside itself.
        """
        session = self.viewer._sketch_session
        self.viewer._sketch_session = None
        self.viewer._sketch_task = None
        if session is not None:
            session.close()
        self.viewer.server.state.sketch_running = False

    def _activate_file(self, idx: int) -> None:
        """Make ``_all_readers[idx]`` the active file and show it.

        The one copy of what file switching, uploads, builds, path opens,
        vq fetches and live checkpoints each did (#37-28). All of them now
        re-pick the representation for the incoming file (a molecule opens
        ball-and-stick, a biomolecule as a ribbon), which only the Files
        dropdown used to do. The watcher's hot reload does not come through
        here, and keeps whatever the user selected.
        """
        state = self.viewer.server.state
        state.active_file_idx = idx
        state.file_names = [_helpers._job_name(r) for r in self.viewer._all_readers]
        _helpers._seed_calculation_charge(state, self.viewer._all_readers[idx])
        state.representation_style = _helpers._default_representation(self.viewer._all_readers[idx])
        state.structure_color_mode = (
            state.cartoon_color_mode
            if state.representation_style == "cartoon"
            else state.atom_color_mode
        )
        self.viewer.files._reload_active_file()

    def _adopt_reader(self, new_reader: QVFReader) -> None:
        """Add ``new_reader`` to the open files and show it."""
        self.viewer._all_readers.append(new_reader)
        self.viewer.files._activate_file(len(self.viewer._all_readers) - 1)

    def switch_file(self, file_idx: Any) -> None:
        """Switch the active file when the user picks from the Files dropdown."""
        # Robust to either an integer index or a filename string.
        try:
            idx = int(file_idx)
        except (TypeError, ValueError):
            names = [_helpers._job_name(r) for r in self.viewer._all_readers]
            idx = names.index(file_idx) if file_idx in names else -1
        if idx < 0 or idx >= len(self.viewer._all_readers):
            return
        self.viewer.files._activate_file(idx)
        self.viewer.server.state.status_message = (
            f"Switched to {_helpers._job_name(self.viewer.reader)} "
            f"(file {idx + 1}/{len(self.viewer._all_readers)})"
        )

    def toggle_compare_mode(self, on: bool | None = None) -> None:
        """Phase D1: overlay all loaded structures, colour-coded by file.

        Called from the switch (``[$event]`` → the new bool) or with no arg to
        flip. Entering clears the single-file scene and draws the overlay;
        leaving restores the active file's structure.
        """
        state = self.viewer.server.state
        enabled = (not state.compare_mode) if on is None else bool(on)
        state.compare_mode = enabled
        if enabled:
            _helpers._remove_static_structure(self.viewer.plotter)
            for prefix in (
                _helpers._Actors.VOLUME,
                _helpers._Actors.VIB_ATOM,
                _helpers._Actors.TRAJ_ATOM,
                _helpers._Actors.MO_SURFACE,
                _helpers._Actors.FERMI,
            ):
                _helpers._remove_actors_by_prefix(self.viewer.plotter, prefix)
            state.structure_hidden = True
            state.compare_legend = _helpers._render_compare_overlay(
                self.viewer.plotter,
                self.viewer._all_readers,
                align=state.compare_align,
                highlight=state.compare_highlight,
                length_unit=state.measure_unit,
            )
            view_isometric(self.viewer.plotter)
            reset_camera(self.viewer.plotter)
            state.status_message = f"Compare mode: {len(state.compare_legend)} structures overlaid"
        else:
            _helpers._remove_actors_by_prefix(self.viewer.plotter, _helpers._Actors.COMPARE)
            state.compare_legend = []
            # Keep the hidden flag set until the restore helper consumes it.
            # Clearing it first makes the helper return immediately, leaving
            # compare mode's static-scene removal permanent.
            self.viewer.display._ensure_static_structure_shown()
            state.status_message = "Compare mode off"
        self.viewer.plotter.render()
        _helpers._push_view(self.viewer.plotter)

    def toggle_compare_align(self, on: bool | None = None) -> None:
        """Phase D4: toggle Kabsch RMSD-fit alignment of the overlaid structures
        onto the first file; re-renders the overlay when compare mode is active.
        """
        state = self.viewer.server.state
        state.compare_align = (not state.compare_align) if on is None else bool(on)
        if state.compare_mode:
            state.compare_legend = _helpers._render_compare_overlay(
                self.viewer.plotter,
                self.viewer._all_readers,
                align=state.compare_align,
                highlight=state.compare_highlight,
                length_unit=state.measure_unit,
            )
            self.viewer.plotter.render()
            _helpers._push_view(self.viewer.plotter)

    def set_compare_highlight(self, idx: Any) -> None:
        """Highlight a specific file in compare mode, dimming the rest."""
        idx = _helpers._first(idx)
        state = self.viewer.server.state
        state.compare_highlight = idx if idx is not None and idx != "" else None
        if state.compare_mode:
            state.compare_legend = _helpers._render_compare_overlay(
                self.viewer.plotter,
                self.viewer._all_readers,
                align=state.compare_align,
                highlight=state.compare_highlight,
                length_unit=state.measure_unit,
            )
            self.viewer.plotter.render()
            _helpers._push_view(self.viewer.plotter)

    def show_density_diff(self) -> None:
        """Phase D2: render the density difference between the two selected files."""
        state = self.viewer.server.state
        opts = state.density_file_options or []
        a = state.diff_a if state.diff_a is not None else (opts[0]["value"] if opts else None)
        b = (
            state.diff_b
            if state.diff_b is not None
            else (opts[1]["value"] if len(opts) > 1 else None)
        )
        if a is None or b is None or a == b:
            state.status_message = (
                "Density difference: pick two distinct files with a density section"
            )
            return
        _helpers._activate_density_diff(
            self.viewer.plotter, self.viewer.viewer_state, state, self.viewer._all_readers, a, b
        )

    def load_file_from_bytes(self) -> None:
        """Load a file uploaded from the browser's file picker.

        Auto-detects supported structure, cube and TREXIO HDF5 files and converts them
        to QVF in-memory before opening.
        """
        import io

        self.viewer.server.state.load_file_dialog = False
        b64_str = self.viewer.server.state.load_file_bytes or ""
        fname = self.viewer.server.state.load_file_name or "uploaded.qvf"
        self.viewer.server.state.load_file_bytes = ""
        self.viewer.server.state.load_file_name = ""
        if not b64_str:
            return

        try:
            raw = base64.b64decode(b64_str)
        except Exception as e:
            self.viewer.server.state.status_message = f"Error decoding file: {e}"
            return

        from vibeview.qvf import QVFError, QVFReader

        try:
            # One converter table for uploads and paths (#37-29).
            from vibeview.converters import convert_bytes_to_qvf, detect_format
            from vibeview.orca_import import is_orca_json

            if detect_format(fname) in (None, "qvf") and not is_orca_json(raw):
                new_reader = QVFReader(io.BytesIO(raw))
            else:
                new_reader = QVFReader(convert_bytes_to_qvf(raw, fname))
        except (QVFError, ValueError) as e:
            self.viewer.server.state.status_message = f"Error opening {fname}: {e}"
            return

        # Name the upload after its file; it has no path, and showed as
        # "<in-memory>" in the Files list.
        _helpers._READER_NAMES[new_reader] = Path(fname).stem
        self.viewer.files._adopt_reader(new_reader)
        self.viewer.server.state.status_message = (
            f"Loaded {fname} (file {len(self.viewer._all_readers)}/{len(self.viewer._all_readers)})"
        )

    def close_load_dialog(self) -> None:
        self.viewer.server.state.load_file_dialog = False

    def open_path(self) -> None:
        """Open a supported file, a glob, or supported files in a directory.

        Server-side filesystem access (vibe-view is a local/LAN server and
        the files live where the jobs ran). Directory input scans for
        recursively when the box is ticked, which is how you sweep a whole
        batch of jobs. Candidates are checked through the shared importer
        registry so installed third-party plugins work here too. New readers
        are appended to the existing multi-file list, so the Files dropdown
        switches between them.
        """
        import os

        from vibeview.qvf import QVFReader

        raw = (self.viewer.server.state.open_path or "").strip()
        if not raw:
            self.viewer.server.state.status_message = (
                "Enter a .qvf or .xyz file or a directory path"
            )
            return
        target = Path(os.path.expanduser(raw))
        recursive = bool(self.viewer.server.state.open_recursive)
        scan_result: _helpers._GuiDirectoryScanResult | None = None

        from vibeview.trexio_import import is_trexio_directory

        if is_trexio_directory(target):
            candidates = [str(target)]
        elif target.is_dir():
            self.viewer.server.state.status_message = f"Scanning {target}..."
            scan_result = _helpers._scan_gui_directory(target, recursive=recursive)
            candidates = [str(path) for path in scan_result.candidates]
        elif any(ch in raw for ch in "*?["):
            try:
                scan_result = _helpers._scan_gui_glob(os.path.expanduser(raw), recursive=recursive)
            except ValueError as exc:
                self.viewer.server.state.status_message = str(exc)
                return
            candidates = [str(path) for path in scan_result.candidates]
        elif target.is_file():
            candidates = [str(target)]
        else:
            self.viewer.server.state.status_message = f"Path not found: {raw}"
            return

        # Directory scanning already applies the shared importer predicate as
        # it walks, which lets the scan stop once it has enough supported
        # files. File and glob input still need the same filter here.
        if scan_result is None:
            from vibeview.converters import is_supported_path

            candidates = [c for c in candidates if is_supported_path(c)]
        scan_notice = (
            _helpers._gui_directory_scan_notice(scan_result) if scan_result is not None else ""
        )
        if not candidates:
            message = f"No supported structure files found under {raw}"
            if scan_notice:
                message += f" ({scan_notice})"
            self.viewer.server.state.status_message = message
            return

        from vibeview.orca_dataset import orca_dataset_key

        # Converted files have an in-memory archive and a separate source
        # path. All companions of an ORCA calculation share one identity.
        loaded = {
            orca_dataset_key(source) or source.resolve()
            for reader in self.viewer._all_readers
            if (source := reader.source_path or reader.path) is not None
        }
        added = 0
        open_errors = 0
        first_error = ""
        for path in candidates:
            identity = orca_dataset_key(path) or Path(path).resolve()
            if identity in loaded:
                continue
            try:
                from vibeview.converters import convert_to_qvf, detect_format

                fmt = detect_format(path)
                if fmt is not None and fmt != "qvf":
                    qvf_buf = convert_to_qvf(path)
                    reader = QVFReader(qvf_buf)
                    reader.source_path = Path(path).resolve()
                else:
                    reader = QVFReader(path)
                self.viewer._all_readers.append(reader)
                loaded.add(identity)
                added += 1
            except Exception as exc:  # noqa: BLE001 — skip unreadable files
                open_errors += 1
                if not first_error:
                    detail = str(exc) or type(exc).__name__
                    if len(detail) > 2000:
                        detail = detail[:2000] + "…"
                    first_error = f"{Path(path).name}: {detail}"

        if added == 0:
            message = (
                f"No new files added ({len(candidates)} found, "
                f"{open_errors} unreadable, rest already open)"
            )
            if scan_notice:
                message += f" ({scan_notice})"
            if first_error:
                message += f" — {first_error}"
            self.viewer.server.state.status_message = message
            return
        self.viewer.files._activate_file(len(self.viewer._all_readers) - 1)
        self.viewer.server.state.load_file_dialog = False
        self.viewer.server.state.open_path = ""
        msg = f"Opened {added} file(s) — {len(self.viewer._all_readers)} total"
        details: list[str] = []
        if open_errors:
            details.append(f"{open_errors} unreadable file(s) skipped: {first_error}")
        if scan_notice:
            details.append(scan_notice)
        if details:
            msg += f" ({'; '.join(details)})"
        self.viewer.server.state.status_message = msg

    def _close_retired_readers(self) -> None:
        while self.viewer._retired_readers:
            with contextlib.suppress(Exception):
                self.viewer._retired_readers.pop().close()

    def _apply_qvf_change(self, event: Any) -> None:
        """Hot-reload the viewer from a settled on-disk change.

        Swaps in a fresh :class:`QVFReader` — the old reader holds an open
        zip handle on the *replaced* archive and caches decoded members,
        so reusing it after a rewrite renders stale data. Then reloads
        only what the per-section diff says moved (roadmap C1):

        * changes confined to 2D-panel sections (SCF history growing, a
          spectrum updating) refresh just the active panel — the 3D scene,
          camera, and selection aren't touched at all;
        * anything else (structure moved, sections added/removed, 3D
          sections changed, non-QVF content) runs the full reload with the
          user's camera preserved, then restores the active section.
        """
        from vibeview.qvf import QVFReader

        state = self.viewer.server.state
        idx = state.active_file_idx
        active_reader = self.viewer._all_readers[idx]
        if active_reader.path is None or str(active_reader.path) != event.path:
            return  # the user switched files since the watcher started
        expected_reader = active_reader
        try:
            fresh = QVFReader(event.path)
        except QVFError as e:
            state.status_message = f"Auto-reload failed: {e}"
            return
        # Opening the replacement archive does not touch shared render state,
        # but swapping the runtime reader does. Serialize that swap with
        # computed surfaces so their final reader/section guard cannot become
        # stale between validation and actor cleanup. Revalidate after taking
        # the lock because a file switch may have completed while ``fresh``
        # was opening or while this callback waited for a grid evaluation.
        with self.viewer._wf_render_lock:
            state = self.viewer.server.state
            idx = state.active_file_idx
            active_reader = self.viewer._all_readers[idx]
            if (
                active_reader is not expected_reader
                or active_reader.path is None
                or str(active_reader.path) != event.path
            ):
                fresh.close()
                return

            kinds = {s.id: s.kind for s in fresh.sections}
            # Light path only when every changed section drives a 2D side
            # panel: those re-materialize fully on activate. 3D kinds
            # (volume.*, trajectory, vibrations, …) re-read from the reader on
            # later interactions, so they need the full rebuild.
            panel_kinds = _helpers._two_d_panel_kinds()
            panel_only = (
                event.is_qvf
                and not event.added_sections
                and not event.removed_sections
                and all(kinds.get(sid) in panel_kinds for sid in event.changed_sections)
            )
            if panel_only:
                # Re-localized wavefunctions exist only in this viewer
                # session. A panel-only archive refresh cannot invalidate
                # them, so carry their synthetic sections and payloads onto
                # the fresh reader before swapping the runtime reader. Full reloads
                # intentionally discard them because 3D/source data changed.
                try:
                    # The panel-only path deliberately keeps the current 3D
                    # scene. Carry its viewer-side structure overlay onto the
                    # fresh archive reader too, or export and the next edit
                    # would silently snap back to the pristine file while the
                    # viewport still showed the edited geometry. The matching
                    # undo/redo stacks stay valid for this same logical file.
                    # Every edited structure keeps its edit, not only the
                    # shown one (#90).
                    for structure_id in active_reader.edited_structure_ids:
                        snapshot = _helpers._structure_edit_snapshot(
                            active_reader.read_structure(structure_id)
                        )
                        fresh.set_edit_overlay(
                            snapshot["positions"],
                            snapshot["symbols"],
                            lattice_vectors=snapshot["lattice_vectors"],
                            section_id=structure_id,
                        )
                    for overlay_id in active_reader.wavefunction_overlay_ids:
                        fresh.set_wavefunction_overlay(
                            overlay_id,
                            active_reader.read_wavefunction_gto(overlay_id),
                        )
                except (QVFError, ValueError) as e:
                    fresh.close()
                    state.status_message = f"Auto-reload failed: {e}"
                    return

            # The fresh reader of the same file keeps showing the structure
            # the user selected, while the archive still has it (#90).
            shown = active_reader.shown_structure_id
            if shown is not None and shown != fresh.shown_structure_id:
                with contextlib.suppress(QVFError):
                    fresh.show_structure(shown)

            self.viewer._all_readers[idx] = fresh
            self.viewer._retired_readers.append(active_reader)

            previously_active = state.selected_section
            touched = set(event.touched_sections)
            prev_run_status = state.live_run_status
            # Follow the head of a growing trajectory: if the user sat on the
            # last frame before the reload, jump to the new last frame after.
            was_at_head = (
                state.trajectory_n_frames > 0
                and state.trajectory_frame >= state.trajectory_n_frames - 1
            )
            if panel_only:
                self.viewer.files._cancel_live_opt_for_reader_change()
                self.viewer.wavefunction._cancel_relocalization()
                if self.viewer.reader is active_reader:
                    self.viewer.reader = fresh  # handlers read the same runtime record
                if previously_active and previously_active in touched:
                    self.viewer.display.activate_section(previously_active)
                self.viewer.files._update_live_status(fresh)
            else:
                # also updates live status
                self.viewer.files._reload_active_file(preserve_camera=True, same_file=True)
                if previously_active and previously_active in kinds:
                    self.viewer.display.activate_section(previously_active)
                    if (
                        was_at_head
                        and kinds.get(previously_active) == "trajectory"
                        and state.trajectory_n_frames > 0
                    ):
                        self.viewer.playback.set_trajectory_frame(state.trajectory_n_frames - 1)
            self.viewer.files._close_retired_readers()
            what = ", ".join(sorted(touched)) if touched else "manifest"
            if prev_run_status == "running" and state.live_run_status in (
                "converged",
                "failed",
            ):
                state.status_message = (
                    f"Job {state.live_run_status} — final results loaded ({what})"
                )
            else:
                state.status_message = f"Reloaded from disk ({what})"

    def _watcher_task_is_running(self, task: asyncio.Task[Any] | None) -> bool:
        """Return whether *task* still owns an active polling coroutine."""
        if task is None:
            return False
        done = getattr(task, "done", None)
        return not bool(done()) if callable(done) else True

    def _stop_file_watcher(self) -> None:
        """Cancel the task that owns the previous archive path, if any."""
        task = self.viewer._watcher_task
        self.viewer._watcher_task = None
        self.viewer._watcher_path = None
        if task is not None and self.viewer.files._watcher_task_is_running(task):
            task.cancel()

    def _sync_file_watcher_for_active_reader(self) -> str | None:
        """Make watcher ownership match the current reader and UI state."""
        state = self.viewer.server.state
        desired_path = str(self.viewer.reader.path) if self.viewer.reader.path is not None else None

        if not state.file_watcher_enabled:
            self.viewer.files._stop_file_watcher()
            return None
        if desired_path is None:
            # An in-memory reader has no file the server can poll. Leaving the
            # switch enabled would claim that auto-reload is active when the
            # old path is still the only one being watched.
            self.viewer.files._stop_file_watcher()
            state.file_watcher_enabled = False
            return "Auto-reload unavailable for an in-memory file"
        if (
            self.viewer.files._watcher_task_is_running(self.viewer._watcher_task)
            and self.viewer._watcher_path == desired_path
        ):
            return None

        self.viewer.files._stop_file_watcher()

        from vibeview.file_watcher import QVFChangeTracker

        tracker = QVFChangeTracker(desired_path)
        watch_path = desired_path
        self.viewer._watcher_path = watch_path

        async def _watch() -> None:
            while (
                self.viewer.server.state.file_watcher_enabled
                and self.viewer._watcher_path == watch_path
            ):
                # Poll fast while a write is settling so the reload lands
                # ~settle_delay after the writer finishes.
                interval = float(self.viewer.server.state.file_watcher_interval or 5.0)
                await asyncio.sleep(0.25 if tracker.has_pending else interval)
                if (
                    not self.viewer.server.state.file_watcher_enabled
                    or self.viewer._watcher_path != watch_path
                ):
                    break
                event = tracker.poll()
                if event is not None:
                    self.viewer.files._apply_qvf_change(event)
                    self.viewer.server.state.flush()

        def _watch_finished(task: asyncio.Task[Any]) -> None:
            """Release ownership and report an unexpected watcher exit."""
            cancelled = getattr(task, "cancelled", None)
            if callable(cancelled) and cancelled():
                return
            error = None
            exception = getattr(task, "exception", None)
            if callable(exception):
                try:
                    error = exception()
                except asyncio.CancelledError:
                    return
            if task is not self.viewer._watcher_task:
                return
            self.viewer._watcher_task = None
            self.viewer._watcher_path = None
            state.file_watcher_enabled = False
            if error is None:
                state.status_message = "Auto-reload stopped"
            else:
                state.status_message = f"Auto-reload stopped: {error}"
            state.flush()

        coroutine = _watch()
        try:
            task = asyncio.ensure_future(coroutine)
        except RuntimeError as error:
            # A watcher cannot truthfully remain enabled without a scheduler.
            coroutine.close()
            self.viewer._watcher_task = None
            self.viewer._watcher_path = None
            state.file_watcher_enabled = False
            return f"Auto-reload unavailable: {error}"
        self.viewer._watcher_task = task
        add_done_callback = getattr(task, "add_done_callback", None)
        if callable(add_done_callback):
            add_done_callback(_watch_finished)
        return None

    def toggle_file_watcher(self, enabled: bool | None = None) -> None:
        """Enable/disable auto-reload of the active file on disk changes."""
        state = self.viewer.server.state
        state.file_watcher_enabled = (
            (not state.file_watcher_enabled) if enabled is None else bool(enabled)
        )
        sync_status = self.viewer.files._sync_file_watcher_for_active_reader()
        if sync_status is not None:
            state.status_message = sync_status
        elif state.file_watcher_enabled:
            state.status_message = "Auto-reload enabled"
        else:
            state.status_message = "Auto-reload disabled"
