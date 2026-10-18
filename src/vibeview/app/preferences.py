"""Preferences controllers for one browser viewer instance."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from vibeview.app import helpers as _helpers
from vibeview.qvf import QVFError
from vibeview.units import band_energy_unit as _band_energy_unit_key
from vibeview.units import energy_from_ev, energy_label, energy_to_ev
from vibeview.units import energy_unit as _energy_unit_key
from vibeview.units import length_unit as _length_unit_key

if TYPE_CHECKING:
    from vibeview.app.runtime import ViewerRuntime


class PreferencesControllers:
    def __init__(self, viewer: ViewerRuntime) -> None:
        self.viewer = viewer

    # ── Controller: dark UI theme ─────────────────────────────────────
    def toggle_dark_ui(self, enabled: bool | None = None) -> None:
        """Toggle dark mode for the UI chrome (not the 3D viewport)."""
        state = self.viewer.server.state
        state.ui_dark_mode = (not state.ui_dark_mode) if enabled is None else bool(enabled)
        state.status_message = f"UI theme: {'dark' if state.ui_dark_mode else 'light'}"

    # ── Auto-save session ───────────────────────────────────────────
    def auto_save_session(self) -> None:
        """Auto-save the current session state periodically."""
        import json
        import time
        from pathlib import Path

        state = self.viewer.server.state
        if not state.session_path:
            state.session_path = str(self.viewer.sessions._default_session_path())

        # Same document the explicit save writes (see _session_payload): this
        # rewrites the very file the user saved, so emitting a different
        # schema/version here silently destroyed their session.
        session_data = self.viewer.sessions._session_payload()
        session_data["timestamp"] = time.time()

        try:
            Path(state.session_path).write_text(json.dumps(session_data, indent=2))
            state.session_dirty = False
        except OSError:
            pass  # Silently skip if file can't be written

    # ── Controller: persistent settings ────────────────────────────
    def save_relocalize_backend(self) -> bool:
        from vibeview.settings import set_setting

        state = self.viewer.server.state
        path = str(state.settings_relocalize_backend_python or "").strip()
        if path and not Path(path).expanduser().is_absolute():
            state.relocalize_backend_status = "The backend Python path must be absolute"
            return False
        try:
            set_setting("relocalize_backend_python", path)
        except OSError as exc:
            state.relocalize_backend_status = f"Could not save backend setting: {exc}"
            return False
        self.viewer.wavefunction._cancel_relocalization()
        state.relocalize_backend_python = path
        self.viewer.wavefunction.check_relocalize_backend()
        return True

    def _apply_energy_unit(self, unit: str) -> None:
        """Re-label every open energy readout in ``unit`` (#58).

        The picker keeps its selection: row values are ``"{spin}:{index}"``
        keys, which a unit change does not touch.
        """
        state = self.viewer.server.state
        state.energy_unit = unit
        section_id = state.wf_section_id
        if not section_id or not self.viewer.reader.has_section(section_id):
            return
        from vibeview.renderers.wavefunction import WavefunctionRenderer

        renderer = WavefunctionRenderer(
            self.viewer.reader.get_section(section_id), self.viewer.reader
        )
        try:
            state.wf_mo_rows = renderer.mo_table(energy_unit=unit)
        except QVFError as exc:
            state.status_message = f"Error loading wavefunction: {exc}"
            return
        if state.chart_title == "Orbital Energies":
            self.viewer.wavefunction.show_energy_diagram()

    def _apply_length_unit(self, unit: str) -> None:
        """Re-render every open length readout in ``unit`` (#58).

        Interactive picks are re-measured from the current geometry; the
        bond table from the ruler button is rebuilt. Anything else in the
        measurement panel (the hint, an error) has no length in it and is
        left alone. A compare legend is relabelled from its stored RMSDs,
        and an open Bond Orders panel is rebuilt.
        """
        state = self.viewer.server.state
        state.measure_unit = unit
        if state.compare_mode and state.compare_legend:
            state.compare_legend = [
                {**entry, "label": _helpers._compare_label(entry["name"], entry["rmsd"], unit)}
                for entry in state.compare_legend
            ]
        self.viewer.display._redraw_section_panel(("bond_orders",))
        if state.measure_mode and state.selected_atoms:
            try:
                sdata = self.viewer.reader.read_structure()
            except QVFError:
                return
            picked = [i for i in state.selected_atoms if 0 <= i < len(sdata.atoms)]
            state.measure_result = _helpers._measure_text(sdata, picked, unit)
        elif str(state.measure_result or "").startswith(_helpers._BOND_DISTANCES_HEADER):
            self.viewer.editing.compute_bonds()

    def _apply_band_energy_unit(self, unit: str) -> None:
        """Redraw the bands / DOS panel with its energy axis in ``unit`` (#58).

        A window the reader typed is carried over into the new unit first,
        so the chart keeps the same view.
        """
        state = self.viewer.server.state
        old = state.band_energy_unit
        for key in ("bands_emin", "bands_emax"):
            value = _helpers._coerce_window_bound(getattr(state, key))
            if value is not None:
                setattr(state, key, energy_from_ev(energy_to_ev(value, old), unit))
        state.band_energy_unit = unit
        state.band_energy_label = energy_label(unit)
        self.viewer.display._redraw_bands_panel()

    def save_settings(self) -> None:
        """Persist user settings to disk."""
        from vibeview.settings import load_settings
        from vibeview.settings import save_settings as _save

        if not self.viewer.preferences.save_relocalize_backend():
            return
        settings = load_settings()
        settings.update(
            {
                "dark_background": self.viewer.server.state.settings_dark_background,
                "material_preset": self.viewer.server.state.settings_material_preset,
                "auto_save_interval": _helpers._auto_save_interval_seconds(
                    self.viewer.server.state.settings_auto_save_interval
                ),
                "energy_unit": _energy_unit_key(self.viewer.server.state.settings_energy_unit),
                "measure_unit": _length_unit_key(self.viewer.server.state.settings_measure_unit),
                "band_energy_unit": _band_energy_unit_key(
                    self.viewer.server.state.settings_band_energy_unit
                ),
            }
        )
        _save(settings)
        self.viewer.server.state.settings_dialog = False

        # Apply immediately
        new_unit = _energy_unit_key(self.viewer.server.state.settings_energy_unit)
        if new_unit != self.viewer.server.state.energy_unit:
            self.viewer.preferences._apply_energy_unit(new_unit)
        new_length_unit = _length_unit_key(self.viewer.server.state.settings_measure_unit)
        if new_length_unit != self.viewer.server.state.measure_unit:
            self.viewer.preferences._apply_length_unit(new_length_unit)
        new_band_unit = _band_energy_unit_key(self.viewer.server.state.settings_band_energy_unit)
        if new_band_unit != self.viewer.server.state.band_energy_unit:
            self.viewer.preferences._apply_band_energy_unit(new_band_unit)
        self.viewer.server.state.status_message = "Settings saved"

        if (
            self.viewer.server.state.settings_dark_background
            != self.viewer.server.state.dark_background
        ):
            _helpers._set_background_mode(
                self.viewer.plotter,
                self.viewer.server.state,
                bool(self.viewer.server.state.settings_dark_background),
            )
            _helpers._push_view(self.viewer.plotter)
