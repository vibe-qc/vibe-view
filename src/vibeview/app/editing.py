"""Editing controllers for one browser viewer instance."""

from __future__ import annotations

import contextlib
import math
from typing import TYPE_CHECKING, Any

import numpy as np

from vibeview.qvf import clamp_replication
from vibeview.units import format_length

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, StructureData


from vibeview.app import helpers as _helpers

if TYPE_CHECKING:
    from vibeview.app.runtime import ViewerRuntime

from vibeview import live_opt as _live_opt

_LIVE_OPT_ENGINE_TITLES = {
    "msindo": "MSINDO (semi-empirical)",
    "mace": "MACE (ML potential, MACE-MPA-0)",
}


class EditingControllers:
    def __init__(self, viewer: ViewerRuntime) -> None:
        self.viewer = viewer

    # ── Controller: measurement ──────────────────────────────────────
    def compute_bonds(self) -> None:
        """Compute and display bond distances + angles."""
        structure = next((s for s in self.viewer.reader.sections if s.kind == "structure"), None)
        if structure is None:
            self.viewer.server.state.measure_result = "No structure loaded"
            return

        # Section has no ``.payload`` attribute — read the structure
        # through the reader (positions are in Å, matching the covalent
        # radii below). The old json.loads(structure.payload) raised
        # AttributeError, so the ruler button silently did nothing.
        try:
            sdata = self.viewer.reader.read_structure()
        except Exception as e:  # noqa: BLE001
            self.viewer.server.state.measure_result = f"Could not read structure: {e}"
            return
        import numpy as np

        from vibeview.bonds import bond_endpoints, structure_bonds
        from vibeview.elements import z_to_symbol

        n = len(sdata.atoms)
        positions = np.asarray([a.position for a in sdata.atoms], dtype=float).reshape(n, 3)
        # The bonds the viewer draws, measured through their periodic images.
        # Neighbour positions are the far endpoints, so an angle at an atom
        # bonded across a cell face is the real angle, not one to the
        # in-cell copy.
        bonds: list[tuple[int, int, float]] = []
        neighbors: dict[int, list[tuple[int, np.ndarray]]] = {i: [] for i in range(n)}
        for bond in structure_bonds(sdata):
            i, j = bond[0], bond[1]
            start, end = bond_endpoints(positions, bond, sdata.lattice_vectors)
            d = float(np.linalg.norm(end - start))
            bonds.append((i, j, d))
            neighbors[i].append((j, end))
            neighbors[j].append((i, start + (start - end)))

        def _sym(idx: int) -> str:
            z = int(sdata.atoms[idx].atomic_number)
            return f"{z_to_symbol(z, str(z))}{idx + 1}"

        rows: list[str] = []
        # Distances.
        if bonds:
            rows.append(_helpers._BOND_DISTANCES_HEADER)
            unit = self.viewer.server.state.measure_unit
            for i, j, d in sorted(bonds, key=lambda x: x[2]):
                rows.append(f"  {_sym(i)}–{_sym(j)}: {format_length(d, unit)}")

        # Angles: for every atom with ≥2 neighbors, show all angles.
        angles: list[str] = []
        for center in range(n):
            nbrs = neighbors[center]
            if len(nbrs) < 2:
                continue
            pc = positions[center]
            for a in range(len(nbrs)):
                for b in range(a + 1, len(nbrs)):
                    ja, pa = nbrs[a]
                    jb, pb = nbrs[b]
                    v1 = pa - pc
                    v2 = pb - pc
                    n1 = float(np.linalg.norm(v1))
                    n2 = float(np.linalg.norm(v2))
                    if n1 < 1e-9 or n2 < 1e-9:
                        continue
                    cos = float(np.dot(v1, v2)) / (n1 * n2)
                    angle_deg = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
                    angles.append(f"  {_sym(ja)}–{_sym(center)}–{_sym(jb)}: {angle_deg:.1f}°")
        if angles:
            if rows:
                rows.append("")
            rows.append("── Bond Angles ──")
            rows.extend(angles)

        if rows:
            self.viewer.server.state.measure_result = "\n".join(rows)
            dist_count = len(bonds)
            ang_count = len(angles)
            parts = [f"{dist_count} bond(s)"]
            if ang_count:
                parts.append(f"{ang_count} angle(s)")
            self.viewer.server.state.status_message = ", ".join(parts)
        else:
            self.viewer.server.state.measure_result = "No bonds or angles detected"

    # ── Controller: interactive atom picking + measurement ────────────
    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("toggle_measure_mode"), which needs a trigger registration.
    def toggle_measure_mode(self, on: bool | None = None) -> None:
        state = self.viewer.server.state
        on = _helpers._first(on)
        state.measure_mode = (not state.measure_mode) if on is None else bool(on)
        if state.measure_mode and state.edit_mode:
            # Measure and edit both consume atom clicks; they are mutually
            # exclusive, as the manual says (#37-18).
            self.viewer.editing._leave_edit_mode()
        state.selected_atoms = []
        _helpers._remove_actors_by_prefix(self.viewer.plotter, _helpers._Actors.PICK_MARKER)
        if state.measure_mode:
            state.measure_result = _helpers._MEASURE_HINT
        else:
            state.measure_result = ""
        _helpers._push_view(self.viewer.plotter)

    def clear_picks(self) -> None:
        self.viewer.server.state.selected_atoms = []
        _helpers._remove_actors_by_prefix(self.viewer.plotter, _helpers._Actors.PICK_MARKER)
        self.viewer.server.state.measure_result = (
            _helpers._MEASURE_HINT if self.viewer.server.state.measure_mode else ""
        )
        _helpers._push_view(self.viewer.plotter)

    def on_pick(self, event: Any = None) -> None:
        """Handle viewport clicks: measure mode (distance/angle/dihedral)
        or edit mode (atom selection)."""
        state = self.viewer.server.state
        if not isinstance(event, dict):
            return
        if not state.measure_mode and not state.edit_mode and not state.residue_pick_mode:
            return
        # Find nearest atom to click point
        wp = None
        for key in ("worldPosition", "position3D", "position3d", "pickedPoint"):
            val = event.get(key)
            if val and len(val) >= 3:
                wp = val
                break
        if wp is None and isinstance(event.get("ray"), dict):
            wp = event["ray"].get("position")
        if not wp or len(wp) < 3:
            return
        try:
            sdata = self.viewer.reader.read_structure()
        except Exception:
            return
        import numpy as _np

        if not sdata.atoms:
            return
        if state.residue_pick_mode and not state.measure_mode and not state.edit_mode:
            residue_key = _helpers._pick_residue(
                sdata, self.viewer.plotter, state, wp[:3], self.viewer.viewer_state.replication
            )
            if residue_key is not None:
                chain, seq = residue_key
                self.viewer.display.set_residue_selection(f"{chain or '*'}/{seq}")
            return

        pos = _np.array([a.position for a in sdata.atoms], dtype=float)
        click = _np.array([float(c) for c in wp[:3]], dtype=float)
        dists = _np.linalg.norm(pos - click, axis=1)
        idx = int(_np.argmin(dists))
        min_dist = float(dists[idx])

        if state.edit_mode:
            if min_dist <= 1.5:
                # Near an existing atom — toggle selection
                if idx in state.edit_selected:
                    state.edit_selected.remove(idx)
                else:
                    state.edit_selected.append(idx)
                # In-place mutation, so the pushed snapshot aliases this same
                # list and compares equal — the client would keep its stale
                # copy. Three buttons bind to `edit_selected.length === 0`
                # (Delete Selected, Change Element, Fragment insert), so
                # without this they stay disabled after a viewport click.
                # Same issue as section_list / sidebar_entries above.
                state.dirty("edit_selected")
                _helpers._update_edit_highlights(
                    self.viewer.reader, self.viewer.plotter, state.edit_selected
                )
                state.status_message = f"Selected {len(state.edit_selected)} atom(s)"
                _helpers._push_view(self.viewer.plotter)
            else:
                # Clicked empty space — place a new atom
                wp_np = _np.array([float(c) for c in wp[:3]], dtype=float)
                new_element = self.viewer.server.state.edit_new_element or "C"
                # Save current state for undo
                self.viewer.editing._record_undo(sdata, tolerate_failure=True)
                _helpers._add_atom_to_structure(
                    self.viewer.reader, self.viewer.plotter, wp_np.tolist(), new_element
                )
                self.viewer.server.state.edit_selected = []
                self.viewer.server.state.status_message = f"Added {new_element} atom"
                _helpers._push_view(self.viewer.plotter)
                self.viewer.editing._live_opt_schedule()
            return

        if dists[idx] > 2.0:
            return

        if state.measure_mode:
            picked = [i for i in state.selected_atoms if 0 <= i < len(sdata.atoms)]
            if idx in picked:
                picked.remove(idx)
            else:
                picked.append(idx)
            state.selected_atoms = picked
            _helpers._update_pick_markers(self.viewer.reader, self.viewer.plotter, picked)
            state.measure_result = _helpers._measure_text(sdata, picked, state.measure_unit)
            _helpers._push_view(self.viewer.plotter)

    # ── Controller: periodic-table element picker ────────────────────
    def open_element_picker(self, target: str = "new") -> None:
        """Open the periodic-table dialog for 'new' or 'change' picking."""
        target = _helpers._first(target, "new")
        self.viewer.server.state.element_picker_target = (
            "change" if str(target) == "change" else "new"
        )
        self.viewer.server.state.element_picker_open = True

    def pick_element(self, symbol: Any = None) -> None:
        """Apply the clicked element and close the periodic table.

        Target 'new' sets the element used for added atoms; 'change'
        routes through edit_change_element (undoable, needs a selection).
        """
        symbol = _helpers._first(symbol)
        if not symbol:
            return
        state = self.viewer.server.state
        state.element_picker_open = False
        if state.element_picker_target == "change":
            self.viewer.editing.edit_change_element(str(symbol))
        else:
            state.edit_new_element = str(symbol)
            state.status_message = f"New atoms will be {symbol}"

    def on_hover(self, event: Any = None) -> None:
        """Remember the world position under the cursor + show the tooltip."""
        import time as _time

        state = self.viewer.server.state
        if not isinstance(event, dict):
            return
        wp = None
        for key in ("worldPosition", "position3D", "position3d", "pickedPoint"):
            val = event.get(key)
            if val and len(val) >= 3:
                wp = val
                break
        if not wp or len(wp) < 3:
            return
        try:
            sdata = _helpers._current_structure(self.viewer.reader)
        except Exception:
            return
        import numpy as _np

        pos = _np.array([a.position for a in sdata.atoms], dtype=float)
        point = _np.array([float(c) for c in wp[:3]], dtype=float)
        dists = _np.linalg.norm(pos - point, axis=1)
        idx = int(_np.argmin(dists))
        # Same near-atom threshold as on_pick.
        atom_idx = idx if float(dists[idx]) <= 1.5 else -1

        self.viewer._hover_state["world"] = [float(c) for c in wp[:3]]
        self.viewer._hover_state["atom_idx"] = atom_idx
        self.viewer._hover_state["at"] = _time.monotonic()

        # Tooltip: element + index over an atom, hidden over empty space.
        # Only write on change — every write is a client push.
        if atom_idx >= 0:
            text = f"{sdata.atoms[atom_idx].symbol} · atom {atom_idx}"
            if state.hover_tooltip != text:
                state.hover_tooltip = text
            if not state.hover_tooltip_visible:
                state.hover_tooltip_visible = True
        elif state.hover_tooltip_visible:
            state.hover_tooltip_visible = False

    # The contextmenu JS calls this right after opening the menu. A recent
    # hover pick (the pointer pauses to right-click, and the debounced pick
    # fires on that pause) tells us what the menu is pointing at; without
    # one the menu opens untargeted, exactly as before.
    # Trigger + set, like edit_redo: the JS invokes trame.trigger(...),
    # which needs the trigger registration; tests reach it via ctrl.
    def context_menu_opened(self) -> None:
        import time as _time

        # While the pointer rests on the canvas the last pick stays valid —
        # nothing moved, so nothing changed. Users pause to read the tooltip
        # before right-clicking, and on a busy scene the pick round trip
        # alone approaches seconds; the original 3 s window discarded
        # perfectly good targets (measured 3.95 s in the QA harness, with
        # the correct atom in the snapshot). 30 s only guards against truly
        # ancient state, e.g. long after the pointer left the canvas.
        fresh = (_time.monotonic() - self.viewer._hover_state["at"]) < 30.0
        self.viewer._ctx_menu_target["world"] = self.viewer._hover_state["world"] if fresh else None
        self.viewer._ctx_menu_target["atom_idx"] = (
            self.viewer._hover_state["atom_idx"] if fresh else -1
        )
        self.viewer.server.state.context_menu_atom_idx = self.viewer._ctx_menu_target["atom_idx"]

    # ── Controller: volume clip planes ────────────────────────────────
    def toggle_clip(self, on: bool | None = None) -> None:
        """Enable/disable volume clip planes.

        Wired from the Enable switch as
        ``update_modelValue=(ctrl.toggle_clip, "[$event]")``, so it is called
        with the switch's new boolean — *set* (don't flip) it, since ``v_model``
        has already written ``clip_enabled`` and a second flip would cancel the
        toggle out. Called with no argument it flips (so an icon/keyboard
        trigger still works). Mirrors ``toggle_measure_mode`` /
        ``toggle_atom_labels``.

        Before this took ``on``, the ``"[$event]"`` payload hit a zero-arg
        signature and every toggle raised ``TypeError`` server-side
        (``toggle_clip() takes 0 positional arguments but 1 was given``); the
        clip never applied through the switch even though ``clip_enabled``
        flipped via ``v_model`` (audit finding UI-OBS-G).
        """
        state = self.viewer.server.state
        state.clip_enabled = (not state.clip_enabled) if on is None else bool(on)
        if not state.clip_enabled:
            state.show_slice = False  # slice depends on clip axes
            state.clip_position_message = ""
        # Optimistic status first; _rebuild_clip overwrites it with "Clip
        # error: …" if the rebuild fails, so an error still wins.
        state.status_message = "Clip plane enabled" if state.clip_enabled else "Clip plane disabled"
        _helpers._rebuild_clip(
            self.viewer.reader, self.viewer.plotter, self.viewer.viewer_state, state
        )
        self.viewer.lifecycle._counted_view_update()

    def toggle_show_slice(self, on: bool | None = None) -> None:
        """Toggle 2D cross-section slice mode."""
        state = self.viewer.server.state
        state.show_slice = (not state.show_slice) if on is None else bool(on)
        if state.show_slice and not state.clip_enabled:
            state.clip_enabled = True  # slice needs clip-position sliders
        if not state.show_slice:
            state.clip_position_message = ""
        state.status_message = "2D slice enabled" if state.show_slice else "2D slice disabled"
        _helpers._rebuild_clip(
            self.viewer.reader, self.viewer.plotter, self.viewer.viewer_state, state
        )
        self.viewer.lifecycle._counted_view_update()

    def update_clip(self, axis: Any = None, value: Any = None) -> None:
        """Update clip planes from slider values.

        Client-side ``v_model`` changes can reach the browser before the
        corresponding state sync reaches Python.  Each slider therefore sends
        both its axis and ``$event`` so this callback updates the authoritative
        server state before rebuilding the scene.
        """
        state = self.viewer.server.state
        if axis in {"x", "y", "z"} and value is not None:
            try:
                setattr(state, f"clip_{axis}", min(1.0, max(0.0, float(value))))
            except (TypeError, ValueError):
                return
        if state.clip_enabled:
            _helpers._rebuild_clip(
                self.viewer.reader, self.viewer.plotter, self.viewer.viewer_state, state
            )
            self.viewer.lifecycle._counted_view_update()

    # ── Edit transactions ──────────────────────────────────────────────
    def _record_undo(
        self, sdata: StructureData, *, clear_redo: bool = True, tolerate_failure: bool = False
    ) -> None:
        """Push ``sdata`` onto the undo stack before an edit changes it.

        The one copy of what eleven edit controllers each spelled out
        (#37-34, #37-R5): snapshot, drop the redo stack (a new edit forks
        history), and mark both lists dirty. The lists are mutated in place,
        which trame's change tracking cannot see (the pushed snapshot aliases
        the same object), so without ``dirty`` Undo and Redo stay disabled in
        the client. ``tolerate_failure`` keeps the edit going without an undo
        entry when the snapshot itself fails, as the atom-placing paths do.
        """
        state = self.viewer.server.state
        try:
            state.edit_history.append(_helpers._structure_edit_snapshot(sdata))
        except Exception:
            if not tolerate_failure:
                raise
        if clear_redo:
            state.edit_future.clear()
        state.dirty("edit_history", "edit_future")

    # ── Controller: edit mode ──────────────────────────────────────────
    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("toggle_edit_mode"), which needs a trigger registration.
    def toggle_edit_mode(self) -> None:
        """Toggle edit mode on/off."""
        state = self.viewer.server.state
        state.edit_mode = not state.edit_mode
        if state.edit_mode:
            state.edit_selected = []
            if state.measure_mode:
                # Mutually exclusive with measuring (#37-18).
                state.measure_mode = False
                state.selected_atoms = []
                state.measure_result = ""
                _helpers._remove_actors_by_prefix(self.viewer.plotter, _helpers._Actors.PICK_MARKER)
            state.status_message = "Edit mode ON — click atoms to select, click empty space to add"
        else:
            self.viewer.editing._leave_edit_mode()
        _helpers._push_view(self.viewer.plotter)

    def _leave_edit_mode(self) -> None:
        """Turn edit mode off and drop its selection, freezes and live opt."""
        state = self.viewer.server.state
        state.edit_mode = False
        state.edit_selected = []
        _helpers._remove_actors_by_prefix(self.viewer.plotter, _helpers._Actors.EDIT_HIGHLIGHT)
        _helpers._reset_frozen(self.viewer.plotter, state)  # freezing is an edit-mode concept
        state.status_message = "Edit mode OFF"
        # Leaving the editor is a session boundary, not just a worker
        # cancellation: a delayed availability probe must not re-enable
        # and schedule a new optimization after the editor has closed.
        self.viewer.editing._disable_live_opt(clear_status=True)

    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("edit_delete_selected"), which needs a trigger registration.
    def edit_delete_selected(self) -> None:
        """Delete the currently selected atoms from the structure."""
        state = self.viewer.server.state
        if not state.edit_selected:
            state.status_message = "No atoms selected to delete"
            return
        try:
            sdata = self.viewer.reader.read_structure()
        except Exception:
            return
        selected_set = set(state.edit_selected)
        # Build new lists excluding selected
        new_positions = [
            a.position.tolist() for i, a in enumerate(sdata.atoms) if i not in selected_set
        ]
        new_symbols = [a.symbol for i, a in enumerate(sdata.atoms) if i not in selected_set]
        # Refuse before touching the stacks. This check used to sit after the
        # history push and future.clear() below, so selecting every atom and
        # pressing delete left a no-op undo entry behind and threw away the
        # redo stack, despite the delete itself being refused.
        if not new_symbols:
            state.status_message = "Cannot delete all atoms"
            return
        # Save current state for undo
        self.viewer.editing._record_undo(sdata)
        _helpers._rebuild_structure_from_positions(
            self.viewer.reader, self.viewer.plotter, new_positions, new_symbols
        )
        state.edit_selected = []
        # Deleting reindexes the atoms, so any frozen indices are now stale.
        _helpers._reset_frozen(self.viewer.plotter, state)
        state.status_message = f"Deleted {len(selected_set)} atom(s)"
        _helpers._push_view(self.viewer.plotter)
        self.viewer.editing._live_opt_schedule()

    # ── B3: freeze-atom constraints for live-opt ──
    # The wire protocol (live_opt.build_request) already carries `frozen`;
    # these controllers are the UI that populates it. Frozen atoms are held
    # fixed while the auto-optimize relaxes the rest.
    def edit_freeze_selected(self) -> None:
        """Hold the currently selected atoms fixed during live-opt."""
        state = self.viewer.server.state
        if not state.edit_selected:
            state.status_message = "Select atoms first, then freeze them"
            return
        try:
            natoms = len(self.viewer.reader.read_structure().atoms)
        except Exception:
            return
        already = set(state.frozen_atoms)
        selected = {i for i in state.edit_selected if 0 <= i < natoms}
        frozen = already | selected
        state.frozen_atoms = sorted(frozen)
        # Count what this call actually froze, not what was selected: an
        # atom that was already frozen, or an out-of-range index, is not a
        # new constraint and reporting it as one overstates the effect.
        n_new = len(selected - already)
        state.edit_selected = []
        _helpers._update_edit_highlights(
            self.viewer.reader, self.viewer.plotter, state.edit_selected
        )
        _helpers._update_frozen_highlights(
            self.viewer.reader, self.viewer.plotter, state.frozen_atoms
        )
        state.status_message = (
            f"Froze {n_new} atom(s) — {len(frozen)} held fixed during auto-optimize"
            if n_new
            else f"Already frozen — {len(frozen)} held fixed during auto-optimize"
        )
        _helpers._push_view(self.viewer.plotter)
        self.viewer.editing._live_opt_schedule()  # re-relax with the new constraint

    def edit_unfreeze_all(self) -> None:
        """Release every frozen atom."""
        state = self.viewer.server.state
        if not state.frozen_atoms:
            return
        _helpers._reset_frozen(self.viewer.plotter, state)
        state.status_message = "Unfroze all atoms"
        _helpers._push_view(self.viewer.plotter)
        self.viewer.editing._live_opt_schedule()  # re-relax now that everything is free

    def edit_change_element(self, new_element: str) -> None:
        """Change the element of all selected atoms."""
        state = self.viewer.server.state
        if not state.edit_selected or not new_element:
            state.status_message = "Select atoms and choose an element first"
            return
        try:
            sdata = self.viewer.reader.read_structure()
        except Exception:
            return
        # Save current state for undo
        self.viewer.editing._record_undo(sdata)
        # Build new symbol list
        new_symbols = (
            list(sdata.symbols) if hasattr(sdata, "symbols") else [a.symbol for a in sdata.atoms]
        )
        for idx in state.edit_selected:
            if 0 <= idx < len(new_symbols):
                new_symbols[idx] = new_element
        positions_list = [a.position.tolist() for a in sdata.atoms]
        _helpers._rebuild_structure_from_positions(
            self.viewer.reader, self.viewer.plotter, positions_list, new_symbols
        )
        n_changed = len(state.edit_selected)
        state.edit_selected = []
        state.status_message = f"Changed {n_changed} atom(s) to {new_element}"
        _helpers._push_view(self.viewer.plotter)
        self.viewer.editing._live_opt_schedule()

    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("edit_undo"), which needs a trigger registration.
    def edit_undo(self) -> None:
        """Restore the previous state from the undo stack."""
        state = self.viewer.server.state
        if not state.edit_history:
            state.status_message = "Nothing to undo"
            return
        # Save current state to redo stack
        try:
            sdata = self.viewer.reader.read_structure()
        except Exception:
            return
        self.viewer.editing._cancel_live_opt_intent(clear_status=True)
        state.edit_future.append(_helpers._structure_edit_snapshot(sdata))
        # Pop and restore previous state
        prev = state.edit_history.pop()
        # Both stacks were mutated in place; see the note on the clear() sites.
        state.dirty("edit_history", "edit_future")
        _helpers._rebuild_structure_from_positions(
            self.viewer.reader,
            self.viewer.plotter,
            prev["positions"],
            prev.get("symbols"),
            lattice_vectors=prev.get("lattice_vectors"),
        )
        state.edit_selected = []
        _helpers._reset_frozen(self.viewer.plotter, state)  # restored geometry may reindex atoms
        state.status_message = "Undo"
        _helpers._push_view(self.viewer.plotter)

    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("edit_redo"), which needs a trigger registration.
    def edit_redo(self) -> None:
        """Re-apply the most recently undone edit."""
        state = self.viewer.server.state
        if not state.edit_future:
            state.status_message = "Nothing to redo"
            return
        future = state.edit_future.pop()
        # Save current to undo stack, keeping the rest of the redo stack.
        with contextlib.suppress(Exception):
            self.viewer.editing._record_undo(self.viewer.reader.read_structure(), clear_redo=False)
        self.viewer.editing._cancel_live_opt_intent(clear_status=True)
        # The pop above mutated the redo stack in place.
        state.dirty("edit_history", "edit_future")
        _helpers._rebuild_structure_from_positions(
            self.viewer.reader,
            self.viewer.plotter,
            future["positions"],
            future.get("symbols"),
            lattice_vectors=future.get("lattice_vectors"),
        )
        state.edit_selected = []
        _helpers._reset_frozen(self.viewer.plotter, state)  # restored geometry may reindex atoms
        state.status_message = "Redo"
        _helpers._push_view(self.viewer.plotter)

    def _live_opt_run_is_current(
        self, run_epoch: int | None, source_reader: QVFReader | None
    ) -> bool:
        """Authorize a callback only for the immutable current run."""
        return (
            run_epoch == self.viewer._live_opt_run_epoch
            and source_reader is self.viewer.reader
            and self.viewer._live_opt_source_reader is source_reader
            and self.viewer.server.state.live_opt_enabled
        )

    def _live_opt_on_step(
        self, event: Any, run_epoch: int | None = None, source_reader: QVFReader | None = None
    ) -> None:
        if not self.viewer.editing._live_opt_run_is_current(run_epoch, source_reader):
            return
        state = self.viewer.server.state
        positions = event.get("positions") or []
        try:
            sdata = self.viewer.reader.read_structure()
        except Exception:
            return
        if len(positions) != len(sdata.atoms):
            return  # stale event from a pre-edit snapshot — ignore
        if not self.viewer._live_opt_undo_pushed:
            # One undo entry per relax run: undo returns to the sketch as
            # drawn, not to every intermediate optimizer step.
            self.viewer.editing._record_undo(sdata)
            self.viewer._live_opt_undo_pushed = True
        _helpers._rebuild_structure_from_positions(
            self.viewer.reader, self.viewer.plotter, positions
        )
        # Frozen atoms keep their indices across a relax; re-draw their markers
        # since the scene rebuild dropped custom actors.
        _helpers._update_frozen_highlights(
            self.viewer.reader, self.viewer.plotter, state.frozen_atoms
        )
        state.live_opt_status = (
            f"step {event.get('step', '?')} · E = {event.get('energy', 0.0):.4f} Ha"
            f" · gₘₐₓ = {event.get('gmax', 0.0):.4f} Ha/bohr"
        )
        _helpers._push_view(self.viewer.plotter)
        state.flush()  # async context — nothing else pushes to the client

    def _live_opt_on_done(
        self,
        converged: bool,
        steps: int,
        run_epoch: int | None = None,
        source_reader: QVFReader | None = None,
    ) -> None:
        if not self.viewer.editing._live_opt_run_is_current(run_epoch, source_reader):
            return
        self.viewer.server.state.live_opt_status = (
            f"relaxed in {steps} steps" if converged else f"not converged after {steps} steps"
        )
        self.viewer.server.state.flush()

    def _live_opt_on_error(
        self, msg: str, run_epoch: int | None = None, source_reader: QVFReader | None = None
    ) -> None:
        if not self.viewer.editing._live_opt_run_is_current(run_epoch, source_reader):
            return
        self.viewer.server.state.live_opt_status = f"live-opt error: {msg}"
        self.viewer.server.state.flush()

    def _live_opt_on_note(
        self, msg: str, run_epoch: int | None = None, source_reader: QVFReader | None = None
    ) -> None:
        if not self.viewer.editing._live_opt_run_is_current(run_epoch, source_reader):
            return
        # Non-geometry progress (e.g. MACE model loading / weight download).
        self.viewer.server.state.live_opt_status = msg
        self.viewer.server.state.flush()

    def _cancel_live_opt_run(self, *, clear_status: bool = False) -> None:
        """Cancel the worker and invalidate every callback from its run."""
        self.viewer.live_opt.cancel()
        self.viewer._live_opt_run_epoch += 1
        self.viewer._live_opt_source_reader = None
        self.viewer._live_opt_undo_pushed = False
        if clear_status:
            self.viewer.server.state.live_opt_status = ""

    def _cancel_live_opt_intent(self, *, clear_status: bool = False) -> None:
        """Invalidate both a pending capability probe and the current run."""
        self.viewer._live_opt_probe_epoch += 1
        self.viewer.editing._cancel_live_opt_run(clear_status=clear_status)

    def _disable_live_opt(self, *, clear_status: bool = False) -> None:
        """Turn auto-opt off and invalidate both pending probes and runs."""
        self.viewer.server.state.live_opt_enabled = False
        self.viewer.editing._cancel_live_opt_intent(clear_status=clear_status)

    def _live_opt_apply_probe(self, result: dict[str, Any]) -> None:
        """Record probe outcome: availability + the engine picker options."""
        state = self.viewer.server.state
        engines = [e for e in result.get("engines", []) if e in _LIVE_OPT_ENGINE_TITLES]
        state.live_opt_available = bool(result.get("available")) and bool(engines)
        state.live_opt_engine_options = [
            {"title": _LIVE_OPT_ENGINE_TITLES[e], "value": e} for e in engines
        ]
        if engines and state.live_opt_engine not in engines:
            state.live_opt_engine = engines[0]

    def _live_opt_schedule(self) -> None:
        """Debounce-start a relax of the current geometry (cancel-on-edit).

        Called after every editor mutation; a no-op unless the
        Auto-optimize switch is on.
        """
        state = self.viewer.server.state
        # Cancel before *every* eligibility guard. Otherwise an edit that
        # becomes too small, too large, or all-frozen leaves the previous
        # worker authorized against this same mutable reader.
        self.viewer.editing._cancel_live_opt_run()
        if not state.live_opt_enabled:
            return
        try:
            sdata = self.viewer.reader.read_structure()
        except Exception:
            state.live_opt_status = "live-opt unavailable for the current structure"
            return
        if len(sdata.atoms) > _live_opt.MAX_LIVE_OPT_ATOMS:
            state.live_opt_status = (
                f"structure too large for live-opt (>{_live_opt.MAX_LIVE_OPT_ATOMS} atoms)"
            )
            return
        if len(sdata.atoms) < 2:
            state.live_opt_status = "add at least 2 atoms to optimize"
            return
        # B3: atoms the user froze are held fixed during the relax. Clamp to the
        # current atom count (indices can go stale after a structural edit).
        natoms = len(sdata.atoms)
        frozen = sorted(i for i in state.frozen_atoms if 0 <= i < natoms)
        if len(frozen) >= natoms:
            state.live_opt_status = "all atoms frozen — unfreeze some to optimize"
            return
        run_epoch = self.viewer._live_opt_run_epoch
        source_reader = self.viewer.reader
        self.viewer._live_opt_source_reader = source_reader
        engine = state.live_opt_engine or "msindo"
        frozen_note = f" ({len(frozen)} frozen)" if frozen else ""
        state.live_opt_status = f"optimizing after edit pause ({engine}){frozen_note} …"
        self.viewer.live_opt.schedule(
            _live_opt.build_request(
                [a.symbol for a in sdata.atoms],
                [a.position.tolist() for a in sdata.atoms],
                engine=engine,
                frozen=frozen,
            ),
            on_step=lambda event: self.viewer.editing._live_opt_on_step(
                event, run_epoch, source_reader
            ),
            on_done=lambda converged, steps: self.viewer.editing._live_opt_on_done(
                converged, steps, run_epoch, source_reader
            ),
            on_error=lambda message: self.viewer.editing._live_opt_on_error(
                message, run_epoch, source_reader
            ),
            on_note=lambda message: self.viewer.editing._live_opt_on_note(
                message, run_epoch, source_reader
            ),
        )

    def toggle_live_opt(self, on: bool | None = None) -> None:
        """Auto-optimize switch. Wired like ``toggle_clip``: *set* (don't
        flip) when called with the switch's new value, flip when called
        with no argument. First enable probes the worker subprocess; if
        vibe-qc is not importable there, the switch snaps back off with
        the reason in the status line (decision 2: no hard dependency).
        """
        state = self.viewer.server.state
        self.viewer._live_opt_probe_epoch += 1
        probe_epoch = self.viewer._live_opt_probe_epoch
        state.live_opt_enabled = (not state.live_opt_enabled) if on is None else bool(on)
        if not state.live_opt_enabled:
            self.viewer.editing._cancel_live_opt_run(clear_status=True)
            return
        if state.live_opt_available is False:
            self.viewer.editing._disable_live_opt()
            return  # live_opt_status already carries the probe reason
        if state.live_opt_available is None:
            self.viewer.editing._cancel_live_opt_run()
            state.live_opt_status = "checking vibe-qc availability …"
            probe_reader = self.viewer.reader

            async def _probe_then_start() -> None:
                result = await _live_opt.probe_worker()
                request_is_current = (
                    probe_epoch == self.viewer._live_opt_probe_epoch
                    and probe_reader is self.viewer.reader
                    and state.live_opt_enabled
                )
                # Probe generations are last-writer-wins. A result from an
                # older toggle or reader must not overwrite newer capability,
                # intent, status, or a newly scheduled run.
                if not request_is_current:
                    state.flush()
                    return
                self.viewer.editing._live_opt_apply_probe(result)
                if not state.live_opt_available:
                    self.viewer.editing._disable_live_opt()
                    state.live_opt_status = (
                        f"live-opt unavailable: {result.get('reason', 'unknown')}"
                    )
                    state.flush()
                    return
                state.live_opt_status = "auto-optimize on"
                self.viewer.editing._live_opt_schedule()
                state.flush()  # async context — push the probe outcome

            _helpers._spawn_task(_probe_then_start(), run_if_no_loop=True)
            return
        state.live_opt_status = "auto-optimize on"
        self.viewer.editing._live_opt_schedule()

    def set_live_opt_engine(self, engine: Any = None) -> None:
        """Engine picker handler. Wired like ``toggle_clip``: ``v_model``
        already wrote ``live_opt_engine``, so with an argument we just
        record it; either way, switching engine while auto-optimize is on
        re-relaxes with the new engine. A controller (not a
        ``@state.change`` watcher) so repeated ``create_app`` calls rebind
        one handler instead of accumulating stale viewer watchers.
        """
        if engine:
            self.viewer.server.state.live_opt_engine = str(engine)
        if self.viewer.server.state.live_opt_enabled and self.viewer.server.state.live_opt_engine:
            self.viewer.editing._live_opt_schedule()

    # ── Controller: context menu actions ─────────────────────────────
    def context_select_atom(self) -> None:
        """Toggle selection of the atom the context menu was opened over."""
        idx = self.viewer.server.state.context_menu_atom_idx
        if idx < 0:
            return
        if not self.viewer.server.state.edit_mode:
            self.viewer.server.state.status_message = "Enable edit mode to select atoms"
            return
        if idx not in self.viewer.server.state.edit_selected:
            self.viewer.server.state.edit_selected.append(idx)
        else:
            self.viewer.server.state.edit_selected.remove(idx)
        self.viewer.server.state.dirty("edit_selected")  # in-place; see the click handler
        _helpers._update_edit_highlights(
            self.viewer.reader, self.viewer.plotter, self.viewer.server.state.edit_selected
        )
        self.viewer.server.state.status_message = (
            f"Selected {len(self.viewer.server.state.edit_selected)} atom(s)"
        )
        _helpers._push_view(self.viewer.plotter)

    def context_add_atom(self) -> None:
        """Add an atom at the position the context menu was opened over.

        The target comes from the hover-pick snapshot taken when the menu
        opened (``context_menu_opened``) — the DOM contextmenu event itself
        has no world position. Mirrors the empty-space branch of ``on_pick``.
        """
        state = self.viewer.server.state
        if not state.edit_mode:
            state.status_message = "Enable edit mode to add atoms"
            return
        world = self.viewer._ctx_menu_target["world"]
        if world is None:
            state.status_message = "Hover the spot in the viewport, then right-click"
            return
        if self.viewer._ctx_menu_target["atom_idx"] >= 0:
            state.status_message = "Too close to an existing atom — pick empty space"
            return
        try:
            sdata = self.viewer.reader.read_structure()
        except Exception:
            return
        new_element = state.edit_new_element or "C"
        self.viewer.editing._record_undo(sdata, tolerate_failure=True)
        _helpers._add_atom_to_structure(self.viewer.reader, self.viewer.plotter, world, new_element)
        state.edit_selected = []
        state.status_message = f"Added {new_element} atom"
        _helpers._push_view(self.viewer.plotter)
        self.viewer.editing._live_opt_schedule()

    def context_center_view(self) -> None:
        """Center the camera on the right-clicked position.

        Pans (keeps direction and distance) so the targeted point — the
        hovered atom's centre, or the picked world position — becomes the
        focal point. This used to call the fit-all ``reset_camera`` and
        ignore the target entirely (#37-8).
        """
        target = self.viewer._ctx_menu_target["world"]
        atom_idx = self.viewer._ctx_menu_target["atom_idx"]
        if atom_idx is not None and atom_idx >= 0:
            with contextlib.suppress(Exception):
                target = list(self.viewer.reader.read_structure().atoms[atom_idx].position)
        if target is None:
            self.viewer.server.state.status_message = (
                "Hover the spot in the viewport, then right-click"
            )
            return
        cam = self.viewer.plotter.camera
        focal = np.asarray(cam.focal_point, dtype=float)
        shift = np.asarray(target, dtype=float)[:3] - focal
        cam.position = tuple(np.asarray(cam.position, dtype=float) + shift)
        cam.focal_point = tuple(focal + shift)
        self.viewer.plotter.render()
        _helpers._push_camera(self.viewer.plotter)
        self.viewer.server.state.status_message = "View centered"

    # ── Controller: fragment insertion (v1.3) ────────────────────────
    def insert_fragment(self, name: str) -> None:
        """Insert a molecular fragment at the selected atom position."""
        if not name:
            return
        try:
            from vibeview.build_tools import FRAGMENTS
        except ImportError:
            self.viewer.server.state.status_message = "Build tools unavailable"
            return

        state = self.viewer.server.state
        try:
            sdata = self.viewer.reader.read_structure()
        except Exception:
            return

        if name not in FRAGMENTS:
            state.status_message = f"Unknown fragment: {name}"
            return

        fragment = FRAGMENTS[name]

        # Save undo state
        self.viewer.editing._record_undo(sdata)

        # Determine attachment point. Every FRAGMENTS entry has its base
        # atom at the relative origin, so the fragment must be offset from
        # the anchor by a bond length — the old code placed the base atom
        # exactly ON the anchor (or, unselected, on the molecule centre,
        # which for symmetric molecules is itself an atom site), and the
        # coincident pair crashed the scene rebuild with a NaN bond.
        allpos = np.array([a.position for a in sdata.atoms], dtype=float)
        if state.edit_selected:
            anchor_idx = state.edit_selected[-1]
            atom_pos = np.array(sdata.atoms[anchor_idx].position, dtype=float)
            # Grow outward: away from the centroid of the other atoms.
            centroid = allpos.mean(axis=0)
            outward = atom_pos - centroid
            norm = float(np.linalg.norm(outward))
            direction = outward / norm if norm > 1e-6 else np.array([1.0, 0.0, 0.0])
            anchor_pos = atom_pos + direction * 1.5
        else:
            # No anchor: set it down beside the structure, like the
            # library insert does.
            anchor_pos = np.array(
                [allpos[:, 0].max() + 2.5, allpos[:, 1].mean(), allpos[:, 2].mean()]
            )

        # Build new atom lists
        current_positions = [a.position.tolist() for a in sdata.atoms]
        current_symbols = [a.symbol for a in sdata.atoms]

        for frag_symbol, frag_rel_pos in fragment:
            abs_pos = (anchor_pos + np.array(frag_rel_pos, dtype=float)).tolist()
            current_positions.append(abs_pos)
            current_symbols.append(frag_symbol)

        _helpers._rebuild_structure_from_positions(
            self.viewer.reader, self.viewer.plotter, current_positions, current_symbols
        )
        state.edit_selected = []
        state.status_message = f"Inserted {name} fragment"
        _helpers._push_view(self.viewer.plotter)
        self.viewer.editing._live_opt_schedule()

    def insert_library_structure(self) -> None:
        """Insert a molecule from the qc structure library into the scene.

        Geometry comes from vibeqc_naming's curated DB (idealized, a starting
        point — same source as the toolbar molecule builder). Placed next to
        the last selected atom, or to the +x side of the current structure.
        """
        state = self.viewer.server.state
        name = (state.library_query or "").strip()
        if not name:
            return
        try:
            from vibeqc_naming import structure_from_name
        except ImportError:
            state.status_message = (
                "Structure library needs the vibeqc_naming package, which is not installed"
            )
            return
        try:
            atoms = structure_from_name(name)
        except Exception as e:  # noqa: BLE001 — a bad name must not kill the session
            state.status_message = f"Could not build {name!r}: {e}"
            return
        if not atoms:
            state.status_message = f"Unknown structure {name!r}"
            return
        from vibeview.elements import Z_TO_SYMBOL

        try:
            sdata = self.viewer.reader.read_structure()
        except Exception:
            return
        self.viewer.editing._record_undo(sdata)

        new_pos = np.array([[x, y, z] for _z, x, y, z in atoms], dtype=float)
        if state.edit_selected:
            anchor = np.array(sdata.atoms[state.edit_selected[-1]].position, dtype=float)
            target = anchor + np.array([2.5, 0.0, 0.0])
        else:
            allpos = np.array([a.position for a in sdata.atoms], dtype=float)
            target = np.array([allpos[:, 0].max() + 3.0, allpos[:, 1].mean(), allpos[:, 2].mean()])
        shifted = new_pos + (target - new_pos.mean(axis=0))

        cur_pos = [a.position.tolist() for a in sdata.atoms]
        cur_sym = [a.symbol for a in sdata.atoms]
        for (z, _x, _y, _zz), p in zip(atoms, shifted, strict=True):
            cur_sym.append(Z_TO_SYMBOL.get(int(z), "X"))
            cur_pos.append([float(p[0]), float(p[1]), float(p[2])])

        _helpers._rebuild_structure_from_positions(
            self.viewer.reader, self.viewer.plotter, cur_pos, cur_sym
        )
        state.edit_selected = []
        _helpers._remember_library_pick(state, name)
        state.status_message = f"Inserted {name} ({len(atoms)} atoms) from the structure library"
        _helpers._push_view(self.viewer.plotter)
        self.viewer.editing._live_opt_schedule()

    def open_library_structure(self) -> None:
        """Open a library structure as a new file in the Files dropdown."""
        name = (self.viewer.server.state.library_query or "").strip()
        self.viewer.server.state.builder_name = self.viewer.server.state.library_query
        self.viewer.files.build_molecule()
        if name:
            _helpers._remember_library_pick(self.viewer.server.state, name)

    def use_library_recent(self, name: str | None = None) -> None:
        """Re-select a recently used library structure from its chip."""
        name = _helpers._first(name)
        if name:
            self.viewer.server.state.library_query = str(name)

    def toggle_library_favorite(self, name: str | None = None) -> None:
        """Pin/unpin a library structure (star next to the search box)."""
        state = self.viewer.server.state
        name = _helpers._first(name)
        name = (str(name) if name else (state.library_query or "")).strip()
        if not name:
            state.status_message = "Pick a molecule to pin first"
            return
        favs = list(state.library_favorites or [])
        if name in favs:
            favs.remove(name)
            state.status_message = f"Unpinned {name}"
        else:
            favs.append(name)
            favs.sort()
            state.status_message = f"Pinned {name}"
        state.library_favorites = favs

    def add_hydrogens(self) -> None:
        """Saturate open valences on selected atoms with hydrogen."""
        from vibeview.build_tools import add_hydrogens as _add_H

        state = self.viewer.server.state
        try:
            sdata = self.viewer.reader.read_structure()
        except Exception:
            return

        # Save undo
        self.viewer.editing._record_undo(sdata)

        # Build atom dicts
        atoms = []
        for a in sdata.atoms:
            atoms.append(
                {
                    "symbol": a.symbol,
                    "position": a.position.tolist(),
                    "atomic_number": a.atomic_number or 0,
                }
            )

        # The button requires a selection and the manual promises "the
        # selected atoms"; every atom was saturated regardless (#37-17). An
        # empty selection (scripted/headless use) still saturates all.
        selection = [int(i) for i in (state.edit_selected or []) if 0 <= int(i) < len(atoms)]
        new_atoms = _add_H(atoms, only=selection or None)
        added = len(new_atoms) - len(atoms)

        positions = [a["position"] for a in new_atoms]
        symbols = [a["symbol"] for a in new_atoms]

        _helpers._rebuild_structure_from_positions(
            self.viewer.reader, self.viewer.plotter, positions, symbols
        )
        state.edit_selected = []
        scope = f" to {len(selection)} selected atom(s)" if selection else ""
        state.status_message = (
            f"Added hydrogens{scope}: {added} added ({len(new_atoms)} atoms total)"
        )
        _helpers._push_view(self.viewer.plotter)
        self.viewer.editing._live_opt_schedule()

    def edit_symmetrize(self) -> None:
        """Project the geometry onto its detected point group (undoable).

        Refuses honestly when no operations are found or the projection
        does not converge to an exactly invariant geometry. Deliberately
        does NOT reschedule live-opt: the optimizer knows nothing about
        symmetry and would immediately re-break what was just imposed.
        """
        from vibeview.symmetry import symmetrize_to_group

        state = self.viewer.server.state
        try:
            sdata = self.viewer.reader.read_structure()
        except Exception:
            return
        symbols = [a.symbol for a in sdata.atoms]
        positions = np.array([a.position for a in sdata.atoms], dtype=float)
        numbers = [a.atomic_number or 0 for a in sdata.atoms]
        res = symmetrize_to_group(symbols, positions, numbers)
        if not res.ok:
            state.status_message = f"Symmetrize: {res.reason}"
            return
        self.viewer.editing._cancel_live_opt_intent(clear_status=True)
        self.viewer.editing._record_undo(sdata)
        if res.positions is None:
            return
        _helpers._rebuild_structure_from_positions(
            self.viewer.reader, self.viewer.plotter, res.positions.tolist(), symbols
        )
        state.edit_selected = []
        self.viewer.exports.detect_symmetry()  # refresh the Symmetry card for the new geometry
        # after the refresh — detect_symmetry writes its own status line
        state.status_message = (
            f"Symmetrized {res.symbol_before} → {res.symbol_after} "
            f"({res.n_operations} operations, max shift {res.max_shift:.3f} Å)"
        )
        _helpers._push_view(self.viewer.plotter)

    def build_supercell(self) -> None:
        """Replicate the structure into a supercell."""
        from vibeview.build_tools import build_supercell as _sc

        state = self.viewer.server.state
        nx = max(1, int(state.build_supercell_nx or 1))
        ny = max(1, int(state.build_supercell_ny or 1))
        nz = max(1, int(state.build_supercell_nz or 1))
        state.build_supercell_dialog = False

        try:
            sdata = self.viewer.reader.read_structure()
        except Exception:
            return
        self.viewer.editing._cancel_live_opt_run(clear_status=True)

        self.viewer.editing._record_undo(sdata)

        atoms = []
        for a in sdata.atoms:
            atoms.append(
                {
                    "symbol": a.symbol,
                    "position": a.position.tolist(),
                    "atomic_number": a.atomic_number or 0,
                }
            )

        scaled_lattice = None
        if sdata.lattice_vectors is not None:
            lattice_array = np.asarray(sdata.lattice_vectors, dtype=float)
            if any(sdata.pbc):
                # A lower-dimensional QVF still carries three lattice rows,
                # but the non-periodic rows are synthesized bookkeeping. Do
                # not duplicate atoms or scale a vacuum/normal direction the
                # calculation did not treat as periodic.
                nx, ny, nz = clamp_replication((nx, ny, nz), sdata.pbc)
                scaled_lattice = lattice_array * np.asarray((nx, ny, nz), dtype=float)[:, None]
            lattice = lattice_array.tolist()
        else:
            # Estimate a bounding box for non-periodic systems
            positions_arr = np.array([a["position"] for a in atoms], dtype=float)
            box_min = positions_arr.min(axis=0)
            box_max = positions_arr.max(axis=0)
            cell = box_max - box_min + 5.0  # padding
            lattice = [
                [float(cell[0]), 0.0, 0.0],
                [0.0, float(cell[1]), 0.0],
                [0.0, 0.0, float(cell[2])],
            ]

        new_atoms = _sc(atoms, lattice, (nx, ny, nz))

        positions = [a["position"] for a in new_atoms]
        symbols = [a["symbol"] for a in new_atoms]

        # ``viewer_state.replication`` is a display-only tiling of the current
        # structure. Building a supercell commits that tiling into new atoms
        # and lattice vectors, so retaining an earlier display replication
        # would apply it a second time on the next canonical scene rebuild.
        # The immediate editor renderer always shows one committed cell; keep
        # controller state and every later renderer on that same definition.
        self.viewer.display._set_replication((1, 1, 1))
        _helpers._rebuild_structure_from_positions(
            self.viewer.reader,
            self.viewer.plotter,
            positions,
            symbols,
            lattice_vectors=scaled_lattice,
        )
        state.edit_selected = []
        state.status_message = f"Supercell {nx}x{ny}x{nz}: {len(new_atoms)} atoms"
        _helpers._push_view(self.viewer.plotter)
        self.viewer.editing._live_opt_schedule()

    # ── Controller: calculation-parameter panel ────────────────────────
    def export_py(self) -> None:
        """Export current structure + parameters as a vibe-qc .py input script."""
        try:
            from vibeview.input_generator import (
                _structure_calculation_params,
                generate_input_script,
            )
        except ImportError:
            self.viewer.server.state.status_message = "Input generator not available"
            return

        # Build atoms list from the structure reader
        try:
            sdata = self.viewer.reader.read_structure()
        except Exception:
            self.viewer.server.state.status_message = "No structure data to export"
            return

        template = self.viewer.server.state.calc_template or "single_point"

        try:
            params = {
                **_structure_calculation_params(sdata),
                "basis": self.viewer.server.state.calc_basis or "sto-3g",
                "method": self.viewer.server.state.calc_method or "rhf",
                "functional": self.viewer.server.state.calc_functional or "",
                "charge": int(self.viewer.server.state.calc_charge or 0),
                "multiplicity": int(self.viewer.server.state.calc_multiplicity or 1),
                "title": "vibe-view generated input",
            }
            script = generate_input_script(template, params)
        except Exception as e:
            self.viewer.server.state.status_message = f"Failed to generate script: {e}"
            return

        # Offered like every other download, so it is listed under Recent
        # Exports and cleared after delivery; it used to skip both (#37-39).
        self.viewer.exports._offer_download(
            script.encode(),
            "text/plain",
            "vibe-qc_input.py",
            label="vibe-qc input script",
            fmt="PY",
        )
        self.viewer.server.state.status_message = "Input script ready for download"
