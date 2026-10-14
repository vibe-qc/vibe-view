"""Wavefunction controllers for one browser viewer instance."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from vibeview.qvf import RELOCALIZED_PREFIX, QVFError

if TYPE_CHECKING:
    from collections.abc import Callable


from vibeview.app import helpers as _helpers

if TYPE_CHECKING:
    from vibeview.app.runtime import ViewerRuntime


class WavefunctionControllers:
    def __init__(self, viewer: ViewerRuntime) -> None:
        self.viewer = viewer

    def _cancel_relocalization(self, message: str = "") -> None:
        self.viewer._relocalize_generation += 1
        task, self.viewer._relocalize_task = self.viewer._relocalize_task, None
        if task is not None and not task.done():
            task.get_loop().call_soon_threadsafe(task.cancel)
        self.viewer.server.state.relocalize_running = False
        self.viewer.server.state.relocalize_status = message

    def cancel_relocalize(self) -> None:
        self.viewer.wavefunction._cancel_relocalization("Re-localization cancelled")

    def clear_relocalization(self) -> None:
        self.viewer.wavefunction._cancel_relocalization()
        self.viewer.files._invalidate_wavefunction_renders()
        with self.viewer._wf_render_lock:
            state = self.viewer.server.state
            ids = [
                sid
                for sid in self.viewer.reader.wavefunction_overlay_ids
                if sid.startswith(RELOCALIZED_PREFIX)
            ]
            source_id = None
            if state.wf_section_id in ids:
                source_id = _helpers._relocalize_source_section(
                    self.viewer.reader, state.wf_section_id
                )
                _helpers._remove_actors_by_prefix(self.viewer.plotter, _helpers._Actors.MO_SURFACE)
                state.mo_visible = False
                state.wf_surface_kind = None
            self.viewer.reader.clear_wavefunction_overlays(ids)
            self.viewer.sidebar_entries[:] = [
                e for e in self.viewer.sidebar_entries if e["id"] not in ids
            ]
            self.viewer.section_list[:] = [
                e for e in self.viewer.section_list if e["id"] not in ids
            ]
            state.sidebar_entries = self.viewer.sidebar_entries
            state.section_list = self.viewer.section_list
            state.dirty("sidebar_entries", "section_list")
            state.relocalize_has_overlay = False
            state.relocalize_result_summary = ""
            state.relocalize_charge_rows = []
            if source_id:
                state.selected_section = source_id
                _helpers._activate_wavefunction(
                    self.viewer.reader, state, self.viewer.reader.get_section(source_id)
                )
            # Nothing relocalized, nothing in the scene changed. Every edit
            # and every live-optimization step comes through here, and the
            # unconditional push doubled their scene serializations (#37-47).
            if ids:
                _helpers._push_view(self.viewer.plotter)

    def _geometry_invalidates_relocalization(self) -> None:
        self.viewer.wavefunction.clear_relocalization()
        self.viewer.server.state.relocalize_supported = False
        self.viewer.server.state.relocalize_reason = (
            "Geometry edits invalidate archived orbitals; choose New RHF + localize explicitly"
        )

    def check_relocalize_backend(self, **_: Any) -> None:
        from vibeview.relocalize import probe_worker

        state = self.viewer.server.state
        path = str(state.relocalize_backend_python or "")
        if (
            self.viewer._relocalize_probe_task is not None
            and not self.viewer._relocalize_probe_task.done()
        ):
            self.viewer._relocalize_probe_task.cancel()
        state.relocalize_probe_running = True
        state.relocalize_backend_status = "Checking native backend and localization methods…"
        state.relocalize_available = None
        state.relocalize_capabilities = {}
        _helpers._refresh_relocalize_state(self.viewer.reader, state)

        async def work() -> None:
            try:
                result = await probe_worker(path)
                if (
                    path != state.relocalize_backend_python
                    or asyncio.current_task() is not self.viewer._relocalize_probe_task
                ):
                    return
                state.relocalize_available = result["available"]
                state.relocalize_capabilities = (
                    result["capabilities"] if result["available"] else {}
                )
                if result["available"]:
                    cap = result["capabilities"]
                    ready = [name for name, spec in cap["methods"].items() if spec.get("ready")]
                    state.relocalize_backend_status = (
                        f"vibe-qc {cap.get('backend_version', '')}: " + ", ".join(ready) + " ready"
                    )
                else:
                    state.relocalize_backend_status = result["reason"]
                _helpers._refresh_relocalize_state(self.viewer.reader, state)
            finally:
                if asyncio.current_task() is self.viewer._relocalize_probe_task:
                    state.relocalize_probe_running = False
                    state.flush()

        try:
            self.viewer._relocalize_probe_task = asyncio.get_running_loop().create_task(work())
        except RuntimeError:
            state.relocalize_probe_running = False
            state.relocalize_backend_status = "Check the backend after the viewer starts"

    def _relocalize_source_changed(self, **_: Any) -> None:
        self.viewer.wavefunction._cancel_relocalization()
        _helpers._refresh_relocalize_state(self.viewer.reader, self.viewer.server.state)

    def run_relocalize(self) -> None:
        from vibeview.relocalize import (
            METHOD_LABELS,
            overlay_section_id,
            request_fingerprint,
            request_from_reader,
            run_relocalization,
            wavefunction_from_result,
        )

        state = self.viewer.server.state
        if state.relocalize_running:
            return
        _helpers._refresh_relocalize_state(self.viewer.reader, state)
        if not state.relocalize_supported:
            state.relocalize_status = state.relocalize_reason
            return
        method, source = str(state.relocalize_method), str(state.relocalize_source)
        if method not in [o["value"] for o in state.relocalize_method_options]:
            state.relocalize_status = "Selected method is unavailable"
            return
        source_reader = self.viewer.reader
        source_id = _helpers._relocalize_source_section(self.viewer.reader, state.wf_section_id)
        canonical = self.viewer.reader.read_wavefunction_gto(source_id)
        path = str(state.relocalize_backend_python or "")
        revision = self.viewer.reader.geometry_revision
        generation = self.viewer._relocalize_generation
        try:
            request = request_from_reader(
                self.viewer.reader, method, canonical_section_id=source_id, source=source
            )
        except (QVFError, ValueError) as exc:
            state.relocalize_status = str(exc)
            return
        fingerprint = request_fingerprint(request)
        state.relocalize_running = True
        state.relocalize_status = (
            "Calculating new RHF orbitals and localizing…"
            if source == "fresh_rhf"
            else "Re-localizing the archived occupied subspace…"
        )

        def current() -> bool:
            return bool(
                generation == self.viewer._relocalize_generation
                and self.viewer.reader is source_reader
                and self.viewer.reader.geometry_revision == revision
                and state.relocalize_backend_python == path
            )

        async def work() -> None:
            try:
                result = await run_relocalization(request, backend_python=path)
                with self.viewer._wf_render_lock:
                    if not current():
                        return
                    if "error" in result:
                        state.relocalize_status = f"Failed: {result['error']}"
                        return
                    latest = request_from_reader(
                        self.viewer.reader, method, canonical_section_id=source_id, source=source
                    )
                    if request_fingerprint(latest) != fingerprint:
                        state.relocalize_status = "Discarded result: source data changed"
                        return
                    overlay = wavefunction_from_result(result, canonical, request)
                    overlay.relocalization["source_section_id"] = source_id
                    section_id = overlay_section_id(method, source)
                    # Never shadow a section that came from the archive.
                    if section_id not in self.viewer.reader.wavefunction_overlay_ids and any(
                        s.id == section_id for s in self.viewer.reader.sections
                    ):
                        section_id += "_" + request["id"][:8]
                    self.viewer.reader.set_wavefunction_overlay(section_id, overlay)
                    entry = {
                        "id": section_id,
                        "title": _helpers._sidebar_section_title(
                            {"id": section_id, "kind": "wavefunction.gto"}
                        ),
                        "subtitle": "wavefunction.gto — new RHF"
                        if source == "fresh_rhf"
                        else "wavefunction.gto — archived subspace",
                        "status": "ok",
                        "supported": True,
                        "icon": _helpers._status_icon("ok"),
                        "kind_icon": _helpers._kind_icon("wavefunction.gto"),
                        "warn": False,
                        "disabled": False,
                    }
                    self.viewer.sidebar_entries[:] = [
                        e for e in self.viewer.sidebar_entries if e["id"] != section_id
                    ] + [entry]
                    self.viewer.section_list[:] = [
                        e for e in self.viewer.section_list if e["id"] != section_id
                    ] + [
                        {
                            "id": section_id,
                            "kind": "wavefunction.gto",
                            "status": "ok",
                            "detail": "Session overlay",
                            "supported": True,
                        }
                    ]
                    state.sidebar_entries = self.viewer.sidebar_entries
                    state.section_list = self.viewer.section_list
                    state.dirty("sidebar_entries", "section_list")
                    state.relocalize_running = False
                    self.viewer.display.activate_section(section_id)
                    state.relocalize_status = (
                        f"{METHOD_LABELS[method]}: {len(result['occupations'])} localized orbitals"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if current():
                    state.relocalize_status = f"Failed: {exc}"
            finally:
                if generation == self.viewer._relocalize_generation:
                    state.relocalize_running = False
                    state.flush()

        try:
            self.viewer._relocalize_task = asyncio.get_running_loop().create_task(work())
        except RuntimeError:
            state.relocalize_running = False
            state.relocalize_status = "Start the viewer before localizing"

    def toggle_color_by_charge(self, on: bool) -> None:
        self.viewer.server.state.color_by_charge = bool(on)
        if self.viewer.server.state.atom_properties_active:
            try:
                _helpers._render_atom_properties_overlay(
                    self.viewer.reader, self.viewer.plotter, self.viewer.server.state
                )
            except Exception as e:  # noqa: BLE001 — keep UI state truthful
                self.viewer.server.state.atom_properties_active = False
                self.viewer.server.state.atom_properties_section_id = None
                self.viewer.server.state.status_message = f"Charge overlay error: {e}"

    def render_mo(self, mo_key: Any, _expected_epoch: int | None = None) -> None:
        state = self.viewer.server.state
        section_id = state.wf_section_id
        if not section_id:
            if _expected_epoch is None:
                state.status_message = "No wavefunction section active."
            return
        source_reader = self.viewer.reader
        component = str(state.wf_component or "real")
        with self.viewer._wf_epoch_lock:
            render_epoch = (
                self.viewer._wf_render_epoch if _expected_epoch is None else int(_expected_epoch)
            )
        # mo_key is the composite "{spin}:{index}" from the picker. Fall
        # back gracefully to a bare integer (restricted) for robustness.
        spin, index = _helpers._parse_mo_key(mo_key)
        with self.viewer._wf_render_lock:
            if not self.viewer.files._wavefunction_render_is_current(
                render_epoch, section_id, source_reader
            ):
                return
            try:
                section = source_reader.get_section(section_id)
                message = _helpers._render_mo_volume(
                    source_reader,
                    self.viewer.plotter,
                    self.viewer.viewer_state,
                    state,
                    section,
                    index,
                    spin,
                    update_state=False,
                    component=component,
                )
            except Exception as e:  # noqa: BLE001
                with self.viewer._wf_epoch_lock:
                    if self.viewer.files._wavefunction_render_is_current(
                        render_epoch, section_id, source_reader
                    ):
                        state.wf_animating = False
                        _helpers._fail_wavefunction_surface(
                            self.viewer.plotter, state, f"MO render error: {e}"
                        )
                    else:
                        _helpers._remove_actors_by_prefix(
                            self.viewer.plotter, _helpers._Actors.MO_SURFACE
                        )
                return

            # The section/file may have changed while the grid was evaluated.
            # Helpers create server actors but no longer push them, so a stale
            # render can be removed here without ever reaching the client.
            with self.viewer._wf_epoch_lock:
                if not self.viewer.files._wavefunction_render_is_current(
                    render_epoch, section_id, source_reader
                ):
                    _helpers._remove_actors_by_prefix(
                        self.viewer.plotter, _helpers._Actors.MO_SURFACE
                    )
                    return
                _helpers._finalize_wavefunction_surface_appearance(self.viewer.plotter, state)
                # Store the recipe only at the same guarded commit boundary as
                # the final client push.
                state.wf_selected_spin = spin
                state.mo_last_spin = spin
                state.mo_last_index = index
                state.mo_last_component = component
                state.wf_surface_kind = "mo"
                state.mo_visible = True
                state.volume_loaded = True
                if message is not None:
                    state.status_message = message

    def toggle_mo_visibility(self, visible: bool) -> None:
        state = self.viewer.server.state
        if not bool(visible):
            state.mo_visible = False
            self.viewer.files._invalidate_wavefunction_renders()
            with self.viewer._wf_render_lock:
                _helpers._remove_actors_by_prefix(self.viewer.plotter, _helpers._Actors.MO_SURFACE)
                _helpers._push_view(self.viewer.plotter)
            state.status_message = "Computed isosurface hidden"
        else:
            render_epoch = self.viewer.files._invalidate_wavefunction_renders()
            source_reader = self.viewer.reader
            section_id = state.wf_section_id
            with self.viewer._wf_render_lock:
                if not self.viewer.files._wavefunction_render_is_current(
                    render_epoch, section_id, source_reader
                ):
                    return
                try:
                    restored = _helpers._replay_wavefunction_surface(
                        source_reader, self.viewer.plotter, self.viewer.viewer_state, state
                    )
                except Exception as e:  # noqa: BLE001
                    restored = False
                    message = f"Isosurface re-render error: {e}"
                else:
                    message = "No computed isosurface is available to show"

                # Replays create actors but do not push. Commit visibility and
                # the one client update only while the reader/section epoch is
                # still current; otherwise discard the unseen stale actors.
                with self.viewer._wf_epoch_lock:
                    if not self.viewer.files._wavefunction_render_is_current(
                        render_epoch, section_id, source_reader
                    ):
                        _helpers._remove_actors_by_prefix(
                            self.viewer.plotter, _helpers._Actors.MO_SURFACE
                        )
                        return
                    if restored:
                        _helpers._finalize_wavefunction_surface_appearance(
                            self.viewer.plotter, state
                        )
                        state.mo_visible = True
                        state.volume_loaded = True
                    else:
                        _helpers._fail_wavefunction_surface(self.viewer.plotter, state, message)

    def render_frontier(self, which: str) -> None:
        """One-click HOMO/LUMO render: pick the marked row and render it."""
        which = _helpers._first(which)
        state = self.viewer.server.state
        marker = str(which).upper()
        row = _helpers._frontier_row(state.wf_mo_rows, marker)
        if row is None:
            state.status_message = f"No {marker} in this wavefunction's MO list"
            return
        state.wf_selected_mo = row["value"]
        self.viewer.wavefunction.render_mo(row["value"])

    def _on_mo_click_request(self, mo_click_request: Any = None, **_kwargs: Any) -> None:
        """Render the MO clicked in the orbital energy diagram.

        The value is ``"{spin}:{index}@{nonce}"``; the nonce only forces the
        change event on repeat clicks and is stripped before dispatch.
        """
        if not mo_click_request:
            return
        key = str(mo_click_request).split("@", 1)[0]
        if not key:
            return
        self.viewer.server.state.wf_selected_mo = key
        self.viewer.wavefunction.render_mo(key)

    def step_mo(self, direction: int) -> None:
        """Move to the previous (-1) or next (+1) MO in the picker."""
        _helpers._step_mo_selection(self.viewer.server.state, direction)

    def step_mo_render(self, direction: int) -> None:
        """Step to the prev/next MO and immediately render it."""
        self.viewer.wavefunction.step_mo(direction)
        self.viewer.wavefunction.render_mo(self.viewer.server.state.wf_selected_mo)

    def toggle_mo_animation(self) -> None:
        """Start/stop auto-advancing through MOs."""
        state = self.viewer.server.state
        if state.wf_animating:
            stop_epoch = self.viewer.files._invalidate_wavefunction_renders()
            source_reader = self.viewer.reader
            section_id = state.wf_section_id
            with self.viewer._wf_render_lock:
                if not self.viewer.files._wavefunction_render_is_current(
                    stop_epoch, section_id, source_reader
                ):
                    return

                has_surface_actor = any(
                    str(name).startswith("mo_iso_") for name in self.viewer.plotter.actors
                )
                restored = True
                message = None
                if state.mo_visible and not has_surface_actor:
                    # A canceled worker may have removed the last committed
                    # actor before noticing its stale epoch. Recreate that
                    # committed recipe so the stopped/visible flags remain
                    # truthful on both server and client.
                    if not state.wf_surface_kind:
                        restored = False
                        message = "Animation stopped; no surface recipe is available"
                    else:
                        try:
                            restored = _helpers._replay_wavefunction_surface(
                                source_reader, self.viewer.plotter, self.viewer.viewer_state, state
                            )
                        except Exception as e:  # noqa: BLE001
                            restored = False
                            message = f"Animation stop restore error: {e}"
                        else:
                            if not restored:
                                message = "Animation stopped; surface could not be restored"

                with self.viewer._wf_epoch_lock:
                    if not self.viewer.files._wavefunction_render_is_current(
                        stop_epoch, section_id, source_reader
                    ):
                        _helpers._remove_actors_by_prefix(
                            self.viewer.plotter, _helpers._Actors.MO_SURFACE
                        )
                        return
                    if restored:
                        if not has_surface_actor and state.mo_visible:
                            _helpers._finalize_wavefunction_surface_appearance(
                                self.viewer.plotter, state
                            )
                        state.status_message = "MO animation stopped"
                    else:
                        _helpers._fail_wavefunction_surface(
                            self.viewer.plotter, state, message or ""
                        )
            return

        epoch = self.viewer.files._invalidate_wavefunction_renders()
        with self.viewer._wf_epoch_lock:
            state.wf_animating = True
        state.status_message = "MO animation started"
        self.viewer.wavefunction._run_mo_animation_step(epoch, initial=True)

    def _run_mo_animation_step(self, epoch: int, *, initial: bool = False) -> None:
        """Render one guarded animation frame, then schedule its successor."""
        with self.viewer._wf_epoch_lock:
            if epoch != self.viewer._wf_render_epoch or not self.viewer.server.state.wf_animating:
                return
            # Keep invalidation out of the gap between checking the epoch and
            # advancing the picker. Otherwise an old timer can step the newly
            # activated wavefunction even though its render is rejected.
            previous_key = self.viewer.server.state.wf_selected_mo
            mo_key = _helpers._step_mo_selection(self.viewer.server.state, 1)
        # Starting at the final (or only) orbital still renders it once.
        # Subsequent ticks stop at that boundary instead of evaluating forever.
        if mo_key is None or (mo_key == previous_key and not initial):
            with self.viewer._wf_epoch_lock:
                if epoch == self.viewer._wf_render_epoch:
                    self.viewer.server.state.wf_animating = False
                    self.viewer.server.state.status_message = (
                        "MO animation stopped: no orbitals"
                        if mo_key is None
                        else "MO animation finished"
                    )
                    self.viewer.server.state.flush()
            return
        self.viewer.wavefunction.render_mo(mo_key, epoch)
        with self.viewer._wf_epoch_lock:
            if epoch == self.viewer._wf_render_epoch:
                self.viewer.server.state.flush()
        self.viewer.wavefunction._schedule_mo_step(epoch)

    def _schedule_mo_step(self, epoch: int) -> None:
        """Keep scheduled VTK work and state updates on the server event loop."""
        state = self.viewer.server.state
        with self.viewer._wf_epoch_lock:
            if epoch != self.viewer._wf_render_epoch or not state.wf_animating:
                return
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                state.wf_animating = False
                state.status_message = "MO animation unavailable: no running event loop"
                state.flush()
                return
            self.viewer._wf_anim_timer = loop.call_later(
                float(state.wf_anim_speed), self.viewer.wavefunction._run_mo_animation_step, epoch
            )

    def show_energy_diagram(self) -> None:
        """Render the orbital energy-level diagram in the bottom panel."""
        state = self.viewer.server.state
        section_id = state.wf_section_id
        if not section_id:
            return
        from vibeview.renderers.wavefunction import WavefunctionRenderer

        section = self.viewer.reader.get_section(section_id)
        renderer = WavefunctionRenderer(section, self.viewer.reader)
        try:
            # Chart channel: the diagram is scripted (clicking a level
            # posts a message back to render that orbital).
            state.chart_html = renderer.render_energy_diagram(energy_unit=state.energy_unit)
            state.chart_title = "Orbital Energies"
            state.status_message = "Energy diagram rendered."
        except Exception as e:  # noqa: BLE001
            state.status_message = f"Diagram error: {e}"

    def compute_wf_density(self, spin: bool = False) -> None:
        """Compute + render the total density (Σ occ_i |ψ_i|²) or, with
        ``spin=True``, the spin density ρ_α − ρ_β from the active
        wavefunction.gto section."""
        requested_spin = bool(spin)
        self.viewer.wavefunction._compute_wavefunction_surface(
            "spin_density" if requested_spin else "density",
            _helpers._render_wf_density,
            "Density",
            density_spin=requested_spin,
        )

    def _compute_wavefunction_surface(
        self,
        surface_kind: str,
        render_surface: Callable[..., Any],
        error_label: str,
        *,
        density_spin: bool | None = None,
    ) -> None:
        """Run one computed-surface action as a guarded render transaction."""
        state = self.viewer.server.state
        previous_spin = bool(state.wf_density_spin)
        if density_spin is not None:
            state.wf_density_spin = density_spin
        sid = state.wf_section_id
        if not sid:
            state.wf_density_spin = previous_spin
            state.status_message = "No wavefunction section active."
            return
        render_epoch = self.viewer.files._invalidate_wavefunction_renders()
        source_reader = self.viewer.reader
        with self.viewer._wf_render_lock:
            if not self.viewer.files._wavefunction_render_is_current(
                render_epoch, sid, source_reader
            ):
                return
            try:
                message = render_surface(
                    source_reader,
                    self.viewer.plotter,
                    self.viewer.viewer_state,
                    state,
                    source_reader.get_section(sid),
                    update_state=False,
                )
            except Exception as e:  # noqa: BLE001
                with self.viewer._wf_epoch_lock:
                    if self.viewer.files._wavefunction_render_is_current(
                        render_epoch, sid, source_reader
                    ):
                        state.wf_density_spin = previous_spin
                        _helpers._fail_wavefunction_surface(
                            self.viewer.plotter, state, f"{error_label} compute error: {e}"
                        )
                    else:
                        _helpers._remove_actors_by_prefix(
                            self.viewer.plotter, _helpers._Actors.MO_SURFACE
                        )
                return

            # Section/file switches invalidate immediately while waiting for
            # the render lock. Keep stale helper-created actors server-side
            # only, then remove them without ever pushing to the client.
            with self.viewer._wf_epoch_lock:
                if not self.viewer.files._wavefunction_render_is_current(
                    render_epoch, sid, source_reader
                ):
                    _helpers._remove_actors_by_prefix(
                        self.viewer.plotter, _helpers._Actors.MO_SURFACE
                    )
                    return
                _helpers._finalize_wavefunction_surface_appearance(self.viewer.plotter, state)
                state.wf_surface_kind = surface_kind
                state.mo_visible = True
                state.volume_loaded = True
                if message is not None:
                    state.status_message = message

    def compute_wf_elf(self) -> None:
        """Compute + render the electron localization function from the
        active wavefunction.gto section (Becke & Edgecombe 1990)."""
        self.viewer.wavefunction._compute_wavefunction_surface(
            "elf", _helpers._render_wf_elf, "ELF"
        )

    def compute_wf_nci(self) -> None:
        """Compute + render the NCI reduced-gradient isosurface, coloured by
        sign(lambda2)*rho (Johnson et al. 2010)."""
        self.viewer.wavefunction._compute_wavefunction_surface(
            "nci", _helpers._render_wf_nci, "NCI"
        )

    def compute_wf_laplacian(self) -> None:
        """Compute + render del^2 rho from the active wavefunction section."""
        self.viewer.wavefunction._compute_wavefunction_surface(
            "laplacian", _helpers._render_wf_laplacian, "Laplacian"
        )
