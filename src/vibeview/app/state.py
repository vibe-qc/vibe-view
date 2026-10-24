"""Types for the browser viewer's trame state and trigger arguments.

Part of the ``vibeview.app`` package; see its ``__init__`` for ``create_app``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:

    pass


class _ViewerUIState(Protocol):
    """The trame state keys the module-level scene helpers read and write.

    ``create_app`` declares every key in its state defaults; this names the
    subset the helpers outside it touch, so mypy flags a misspelt key there
    instead of trame quietly creating a new one (the phantom
    ``active_section`` of #37-44 was such a key). Values stay ``Any``: the
    browser writes them back as JSON, so a declared type would be a
    promise the client does not keep. ``tests/test_structural_contracts.py``
    keeps this list and the defaults in step.
    """

    active_volume_id: Any
    atom_properties_active: Any
    atom_properties_section_id: Any
    background_override: Any
    band_energy_unit: Any
    bands_emax: Any
    bands_emin: Any
    bands_html: Any
    bands_title: Any
    bands_window_auto: Any
    bands_window_available: Any
    browser_capture_ready: Any
    calc_charge: Any
    calc_multiplicity: Any
    charge_kind: Any
    chart_html: Any
    chart_title: Any
    clip_enabled: Any
    clip_position_message: Any
    clip_x: Any
    clip_y: Any
    clip_z: Any
    color_by_charge: Any
    colormap: Any
    compare_legend: Any
    compare_mode: Any
    dark_background: Any
    eos_html: Any
    energy_unit: Any
    esp_available: Any
    extra_isosurfaces: Any
    fermi_band_options: Any
    fermi_selected_bands: Any
    figure_outlines: Any
    frozen_atoms: Any
    has_animatable: Any
    inspection_rows: Any
    inspection_summary: Any
    is_periodic: Any
    isovalue: Any
    isovalue_units: Any
    library_recent: Any
    measure_unit: Any
    mo_last_index: Any
    mo_last_spin: Any
    mo_visible: Any
    opacity: Any
    orbital_panel_title: Any
    orthographic_projection: Any
    pbc_a: Any
    pbc_b: Any
    pbc_c: Any
    periodic_replication: Any
    phonon_html: Any
    properties_html: Any
    properties_title: Any
    raytrace_enabled: Any
    reaction_current_label: Any
    reaction_waypoints: Any
    relocalize_capabilities: Any
    relocalize_charge_rows: Any
    relocalize_has_overlay: Any
    relocalize_method: Any
    relocalize_method_options: Any
    relocalize_reason: Any
    relocalize_result_summary: Any
    relocalize_source: Any
    relocalize_supported: Any
    representation_style: Any
    residue_selection: Any
    residue_visibility: Any
    run_record_attachment_section: Any
    run_record_attachments: Any
    screenshot_source: Any
    selected_section: Any
    show_atom_labels: Any
    silhouette_targets: Any
    spectra_html: Any
    spectra_title: Any
    spectra_settings_key: Any
    ssao_enabled: Any
    status_message: Any
    structure_dim: Any
    structure_hidden: Any
    toon_mode: Any
    trajectory_energy_image: Any
    trajectory_frame: Any
    trajectory_n_frames: Any
    trajectory_playing: Any
    trajectory_thumbnails: Any
    vibration_amplitude: Any
    vibration_arrow_cutoff: Any
    vibration_arrow_color: Any
    vibration_arrow_thickness: Any
    vibration_frequencies: Any
    vibration_ir_intensities: Any
    vibration_mode: Any
    vibration_displacement_modes: Any
    vibration_displacement_amplitudes: Any
    vibration_mode_items: Any
    vibration_n_modes: Any
    vibration_phase: Any
    vibration_phase_step: Any
    vibration_playing: Any
    vibration_thumbnails: Any
    video_kind: Any
    vol_max_dim: Any
    vol_mode: Any
    vol_preset: Any
    vol_range: Any
    vol_sent_shape: Any
    vol_shade: Any
    vol_tf: Any
    vol_window: Any
    vol_window_bounds: Any
    vol_window_high: Any
    vol_window_low: Any
    vol_window_step: Any
    volume_loaded: Any
    wf_animating: Any
    wf_component: Any
    wf_density_spin: Any
    wf_elf_iso: Any
    wf_is_complex: Any
    wf_laplacian_iso: Any
    wf_mo_rows: Any
    wf_n_per_dim: Any
    wf_nci_iso: Any
    wf_orbital_kind: Any
    wf_panel_title: Any
    wf_section_id: Any
    wf_selected_mo: Any
    wf_selected_spin: Any
    wf_spin_unrestricted: Any
    wf_surface_kind: Any

def _first(value: Any, default: Any = None) -> Any:
    """A trigger argument as the value it carries.

    Vue sends ``[$event]`` as a one-element list, a direct Python call passes
    the value itself, and an empty list stands for no value. Seventeen
    controllers unwrapped this by hand, each slightly differently (#37-44).
    """
    if isinstance(value, (list, tuple)):
        return value[0] if value else default
    return value
