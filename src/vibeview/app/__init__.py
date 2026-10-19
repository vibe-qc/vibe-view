"""Browser application composition root and compatibility exports."""

from __future__ import annotations

import asyncio  # noqa: F401
import base64  # noqa: F401
import contextlib  # noqa: F401
import math  # noqa: F401
import threading  # noqa: F401
import weakref  # noqa: F401
from collections.abc import Sequence as Sequence  # noqa: F401
from dataclasses import dataclass as dataclass  # noqa: F401
from datetime import UTC as UTC  # noqa: F401
from pathlib import Path as Path  # noqa: F401
from typing import TYPE_CHECKING as TYPE_CHECKING  # noqa: F401
from typing import Any as Any
from typing import Literal as Literal
from typing import cast as cast

import numpy as np  # noqa: F401
import pyvista as pv  # noqa: F401
from pyvista.plotting.colors import get_cmap_safe as get_cmap_safe  # noqa: F401

from vibeview._fmt import human_size as human_size  # noqa: F401

# The package's other modules. Their names are re-exported here, so
# ``vibeview.app.<name>`` keeps working for every caller (#37-R6).
from vibeview.app.controls import (  # noqa: F401
    _DIALOG_FOCUS_JS as _DIALOG_FOCUS_JS,
)
from vibeview.app.controls import (
    _build_controls as _build_controls,
)
from vibeview.app.controls import (
    _dialog_focus_restore as _dialog_focus_restore,
)
from vibeview.app.controls import (
    _element_button_style as _element_button_style,
)
from vibeview.app.controls import (
    _periodic_table_cells as _periodic_table_cells,
)
from vibeview.app.layout import (  # noqa: F401
    _retaining_view_updater as _retaining_view_updater,
)
from vibeview.app.scene import (  # noqa: F401
    _ON_DOM_CHANGE_JS as _ON_DOM_CHANGE_JS,
)
from vibeview.app.scene import (
    _SCENE_HOOKS as _SCENE_HOOKS,
)
from vibeview.app.scene import (
    _Actors as _Actors,
)
from vibeview.app.scene import (
    _scene_hooks as _scene_hooks,
)
from vibeview.app.scene import (
    _SceneHooks as _SceneHooks,
)
from vibeview.app.sidebar import (  # noqa: F401
    _BASIS_AO_GROUP_ID as _BASIS_AO_GROUP_ID,
)
from vibeview.app.sidebar import (
    _DEFAULT_KIND_ICON as _DEFAULT_KIND_ICON,
)
from vibeview.app.sidebar import (
    _KIND_ICONS as _KIND_ICONS,
)
from vibeview.app.sidebar import (
    _KIND_TITLES as _KIND_TITLES,
)
from vibeview.app.sidebar import (
    _ORBITAL_GROUP_ID as _ORBITAL_GROUP_ID,
)
from vibeview.app.sidebar import (
    _build_section_list as _build_section_list,
)
from vibeview.app.sidebar import (
    _build_sidebar_entries as _build_sidebar_entries,
)
from vibeview.app.sidebar import (
    _builder_name as _builder_name,
)
from vibeview.app.sidebar import (
    _iupac_name_entry as _iupac_name_entry,
)
from vibeview.app.sidebar import (
    _kind_icon as _kind_icon,
)
from vibeview.app.sidebar import (
    _orbital_group_sidebar_title as _orbital_group_sidebar_title,
)
from vibeview.app.sidebar import (
    _orbital_label as _orbital_label,
)
from vibeview.app.sidebar import (
    _sidebar_group_entry as _sidebar_group_entry,
)
from vibeview.app.sidebar import (
    _sidebar_section_title as _sidebar_section_title,
)
from vibeview.app.sidebar import (
    _status_icon as _status_icon,
)
from vibeview.app.sidebar import (
    _supported_kinds_set as _supported_kinds_set,
)
from vibeview.app.state import (  # noqa: F401
    _first as _first,
)
from vibeview.app.state import (
    _ViewerUIState as _ViewerUIState,
)
from vibeview.headless import new_plotter as new_plotter  # noqa: F401
from vibeview.plotter_compat import (  # noqa: F401
    cmap_argument as cmap_argument,
)
from vibeview.plotter_compat import (
    remove_actor as remove_actor,
)
from vibeview.plotter_compat import (
    reset_camera as reset_camera,
)
from vibeview.plotter_compat import (
    set_background as set_background,
)
from vibeview.plotter_compat import (
    show_grid as show_grid,
)
from vibeview.plotter_compat import (
    view_isometric as view_isometric,
)
from vibeview.plotter_compat import (
    view_xy as view_xy,
)
from vibeview.plotter_compat import (
    view_xz as view_xz,
)
from vibeview.plotter_compat import (
    view_yz as view_yz,
)
from vibeview.qvf import RELOCALIZED_PREFIX as RELOCALIZED_PREFIX  # noqa: F401
from vibeview.qvf import QVFError as QVFError
from vibeview.qvf import clamp_replication as clamp_replication
from vibeview.renderers import palette as _palette  # noqa: F401
from vibeview.renderers.cell import supercell_offsets as supercell_offsets  # noqa: F401
from vibeview.renderers.nci import RDG_ISOVALUE as RDG_ISOVALUE  # noqa: F401
from vibeview.units import ANGSTROM_TO_BOHR as _ANGSTROM_TO_BOHR  # noqa: F401
from vibeview.units import band_energy_unit as _band_energy_unit_key  # noqa: F401
from vibeview.units import energy_from_ev as energy_from_ev  # noqa: F401
from vibeview.units import energy_label as energy_label
from vibeview.units import energy_to_ev as energy_to_ev
from vibeview.units import energy_unit as _energy_unit_key  # noqa: F401
from vibeview.units import format_length as format_length
from vibeview.units import length_unit as _length_unit_key  # noqa: F401
from vibeview.vibration_amplitude import (
    DEFAULT_PEAK_EXCURSION_ANGSTROM as DEFAULT_PEAK_EXCURSION_ANGSTROM,  # noqa: F401
)
from vibeview.viewer_defaults import ViewerState as ViewerState  # noqa: F401

if TYPE_CHECKING:
    from collections.abc import Callable as Callable  # noqa: F401

    from vibeview.material_presets import MaterialPreset as MaterialPreset  # noqa: F401
    from vibeview.qvf import GridData as GridData  # noqa: F401
    from vibeview.qvf import QVFReader as QVFReader
    from vibeview.qvf import Section as Section
    from vibeview.qvf import StructureData as StructureData
    from vibeview.renderers.volume import VolumeRenderer as VolumeRenderer  # noqa: F401


from vibeview.app import helpers as _helpers
from vibeview.app.bindings import bind_controllers as bind_controllers
from vibeview.app.bootstrap import initialize_about as initialize_about
from vibeview.app.bootstrap import initialize_scene as initialize_scene
from vibeview.app.bootstrap import initialize_state as initialize_state
from vibeview.app.display import DisplayControllers as DisplayControllers
from vibeview.app.editing import EditingControllers as EditingControllers
from vibeview.app.exports import ExportsControllers as ExportsControllers
from vibeview.app.files import FilesControllers as FilesControllers
from vibeview.app.helpers import (  # noqa: F401
    _BOND_DISTANCES_HEADER as _BOND_DISTANCES_HEADER,
)
from vibeview.app.helpers import (
    _CLIP_ACTOR_NAMES as _CLIP_ACTOR_NAMES,
)
from vibeview.app.helpers import (
    _COMPARE_PALETTE as _COMPARE_PALETTE,
)
from vibeview.app.helpers import (
    _FRAME_PLOT_RENDERERS as _FRAME_PLOT_RENDERERS,
)
from vibeview.app.helpers import (
    _FRONTIER_ALIASES as _FRONTIER_ALIASES,
)
from vibeview.app.helpers import (
    _GUI_SCAN_ERROR_SAMPLES as _GUI_SCAN_ERROR_SAMPLES,
)
from vibeview.app.helpers import (
    _GUI_SCAN_MAX_CANDIDATES as _GUI_SCAN_MAX_CANDIDATES,
)
from vibeview.app.helpers import (
    _GUI_SCAN_MAX_ENTRIES as _GUI_SCAN_MAX_ENTRIES,
)
from vibeview.app.helpers import (
    _GUI_SCAN_PRUNED_DIR_NAMES as _GUI_SCAN_PRUNED_DIR_NAMES,
)
from vibeview.app.helpers import (
    _ISOSURFACE_ACTOR_PREFIXES as _ISOSURFACE_ACTOR_PREFIXES,
)
from vibeview.app.helpers import (
    _ISOVALUE_UNITS as _ISOVALUE_UNITS,
)
from vibeview.app.helpers import (
    _LIBRARY_RECENT_MAX as _LIBRARY_RECENT_MAX,
)
from vibeview.app.helpers import (
    _MEASURE_HINT as _MEASURE_HINT,
)
from vibeview.app.helpers import (
    _PALETTE_ACTIONS as _PALETTE_ACTIONS,
)
from vibeview.app.helpers import (
    _PANEL_SPECS as _PANEL_SPECS,
)
from vibeview.app.helpers import (
    _READER_NAMES as _READER_NAMES,
)
from vibeview.app.helpers import (
    _SECTION_HANDLERS as _SECTION_HANDLERS,
)
from vibeview.app.helpers import (
    _SSAO_PASS as _SSAO_PASS,
)
from vibeview.app.helpers import (
    _STRUCTURE_MEMO as _STRUCTURE_MEMO,
)
from vibeview.app.helpers import (
    _SUBSCRIPT as _SUBSCRIPT,
)
from vibeview.app.helpers import (
    _activate_atom_properties as _activate_atom_properties,
)
from vibeview.app.helpers import (
    _activate_bands as _activate_bands,
)
from vibeview.app.helpers import (
    _activate_density_diff as _activate_density_diff,
)
from vibeview.app.helpers import (
    _activate_dos as _activate_dos,
)
from vibeview.app.helpers import (
    _activate_fermi as _activate_fermi,
)
from vibeview.app.helpers import (
    _activate_panel as _activate_panel,
)
from vibeview.app.helpers import (
    _activate_reaction_path as _activate_reaction_path,
)
from vibeview.app.helpers import (
    _activate_reaction_waypoints as _activate_reaction_waypoints,
)
from vibeview.app.helpers import (
    _activate_run_record as _activate_run_record,
)
from vibeview.app.helpers import (
    _activate_scan_surface as _activate_scan_surface,
)
from vibeview.app.helpers import (
    _activate_scf_history as _activate_scf_history,
)
from vibeview.app.helpers import (
    _activate_spectra as _activate_spectra,
)
from vibeview.app.helpers import (
    _activate_topology_qtaim as _activate_topology_qtaim,
)
from vibeview.app.helpers import (
    _activate_trajectory as _activate_trajectory,
)
from vibeview.app.helpers import (
    _activate_vibrations as _activate_vibrations,
)
from vibeview.app.helpers import (
    _activate_volume as _activate_volume,
)
from vibeview.app.helpers import (
    _activate_wavefunction as _activate_wavefunction,
)
from vibeview.app.helpers import (
    _add_atom_to_structure as _add_atom_to_structure,
)
from vibeview.app.helpers import (
    _apply_camera as _apply_camera,
)
from vibeview.app.helpers import (
    _apply_scene_appearance as _apply_scene_appearance,
)
from vibeview.app.helpers import (
    _auto_save_interval_seconds as _auto_save_interval_seconds,
)
from vibeview.app.helpers import (
    _base_opacity as _base_opacity,
)
from vibeview.app.helpers import (
    _bfactor_range_message as _bfactor_range_message,
)
from vibeview.app.helpers import (
    _bond_orders_status as _bond_orders_status,
)
from vibeview.app.helpers import (
    _bond_orders_title as _bond_orders_title,
)
from vibeview.app.helpers import (
    _build_signed_contour as _build_signed_contour,
)
from vibeview.app.helpers import (
    _build_structure_scene as _build_structure_scene,
)
from vibeview.app.helpers import (
    _camera_to_dict as _camera_to_dict,
)
from vibeview.app.helpers import (
    _charge_color as _charge_color,
)
from vibeview.app.helpers import (
    _checkpoint_summary as _checkpoint_summary,
)
from vibeview.app.helpers import (
    _chemical_formula as _chemical_formula,
)
from vibeview.app.helpers import (
    _clamp_viewer_replication as _clamp_viewer_replication,
)
from vibeview.app.helpers import (
    _clear_output_panels as _clear_output_panels,
)
from vibeview.app.helpers import (
    _clip_to_planes as _clip_to_planes,
)
from vibeview.app.helpers import (
    _coerce_window_bound as _coerce_window_bound,
)
from vibeview.app.helpers import (
    _computed_surface_opacity as _computed_surface_opacity,
)
from vibeview.app.helpers import (
    _current_structure as _current_structure,
)
from vibeview.app.helpers import (
    _default_background as _default_background,
)
from vibeview.app.helpers import (
    _default_representation as _default_representation,
)
from vibeview.app.helpers import (
    _draw_computed_surfaces as _draw_computed_surfaces,
)
from vibeview.app.helpers import (
    _draw_structure as _draw_structure,
)
from vibeview.app.helpers import (
    _draw_wannier_overlay as _draw_wannier_overlay,
)
from vibeview.app.helpers import (
    _fail_wavefunction_surface as _fail_wavefunction_surface,
)
from vibeview.app.helpers import (
    _finalize_wavefunction_surface_appearance as _finalize_wavefunction_surface_appearance,
)
from vibeview.app.helpers import (
    _frame_plot_renderer as _frame_plot_renderer,
)
from vibeview.app.helpers import (
    _frontier_row as _frontier_row,
)
from vibeview.app.helpers import (
    _get_lattice_vectors as _get_lattice_vectors,
)
from vibeview.app.helpers import (
    _grids_match as _grids_match,
)
from vibeview.app.helpers import (
    _gui_directory_scan_notice as _gui_directory_scan_notice,
)
from vibeview.app.helpers import (
    _GuiDirectoryScanResult as _GuiDirectoryScanResult,
)
from vibeview.app.helpers import (
    _header_title as _header_title,
)
from vibeview.app.helpers import (
    _is_pruned_gui_directory as _is_pruned_gui_directory,
)
from vibeview.app.helpers import (
    _job_name as _job_name,
)
from vibeview.app.helpers import (
    _job_spec_title as _job_spec_title,
)
from vibeview.app.helpers import (
    _library_structure_names as _library_structure_names,
)
from vibeview.app.helpers import (
    _measure_text as _measure_text,
)
from vibeview.app.helpers import (
    _naming_available as _naming_available,
)
from vibeview.app.helpers import (
    _open_atom_properties as _open_atom_properties,
)
from vibeview.app.helpers import (
    _open_dos_total as _open_dos_total,
)
from vibeview.app.helpers import (
    _open_structure as _open_structure,
)
from vibeview.app.helpers import (
    _orbital_is_wrappable as _orbital_is_wrappable,
)
from vibeview.app.helpers import (
    _ospray_available as _ospray_available,
)
from vibeview.app.helpers import (
    _palette_actions_for as _palette_actions_for,
)
from vibeview.app.helpers import (
    _panel_handler as _panel_handler,
)
from vibeview.app.helpers import (
    _PanelSpec as _PanelSpec,
)
from vibeview.app.helpers import (
    _parse_mo_key as _parse_mo_key,
)
from vibeview.app.helpers import (
    _pick_residue as _pick_residue,
)
from vibeview.app.helpers import (
    _plot_spectra_kinds as _plot_spectra_kinds,
)
from vibeview.app.helpers import (
    _prepare_scene_actor_replacement as _prepare_scene_actor_replacement,
)
from vibeview.app.helpers import (
    _present_elements as _present_elements,
)
from vibeview.app.helpers import (
    _probe_is_periodic as _probe_is_periodic,
)
from vibeview.app.helpers import (
    _publish_energy_window as _publish_energy_window,
)
from vibeview.app.helpers import (
    _push_camera as _push_camera,
)
from vibeview.app.helpers import (
    _push_view as _push_view,
)
from vibeview.app.helpers import (
    _qa_validate as _qa_validate,
)
from vibeview.app.helpers import (
    _reader_state_handler as _reader_state_handler,
)
from vibeview.app.helpers import (
    _reapply_raytrace as _reapply_raytrace,
)
from vibeview.app.helpers import (
    _rebuild_clip as _rebuild_clip,
)
from vibeview.app.helpers import (
    _rebuild_crossfade as _rebuild_crossfade,
)
from vibeview.app.helpers import (
    _rebuild_scene as _rebuild_scene,
)
from vibeview.app.helpers import (
    _rebuild_scene_unlocked as _rebuild_scene_unlocked,
)
from vibeview.app.helpers import (
    _rebuild_structure_from_positions as _rebuild_structure_from_positions,
)
from vibeview.app.helpers import (
    _rebuild_volume as _rebuild_volume,
)
from vibeview.app.helpers import (
    _refresh_relocalize_state as _refresh_relocalize_state,
)
from vibeview.app.helpers import (
    _relocalize_source_section as _relocalize_source_section,
)
from vibeview.app.helpers import (
    _remember_base_opacity as _remember_base_opacity,
)
from vibeview.app.helpers import (
    _remember_library_pick as _remember_library_pick,
)
from vibeview.app.helpers import (
    _remove_actors_by_prefix as _remove_actors_by_prefix,
)
from vibeview.app.helpers import (
    _remove_atom_charge_labels as _remove_atom_charge_labels,
)
from vibeview.app.helpers import (
    _remove_atom_index_labels as _remove_atom_index_labels,
)
from vibeview.app.helpers import (
    _remove_static_structure as _remove_static_structure,
)
from vibeview.app.helpers import (
    _render_atom_properties_overlay as _render_atom_properties_overlay,
)
from vibeview.app.helpers import (
    _render_atom_properties_overlay_impl as _render_atom_properties_overlay_impl,
)
from vibeview.app.helpers import (
    _render_compare_overlay as _render_compare_overlay,
)
from vibeview.app.helpers import (
    _render_mo_volume as _render_mo_volume,
)
from vibeview.app.helpers import (
    _render_nci_surface as _render_nci_surface,
)
from vibeview.app.helpers import (
    _render_trajectory_atoms as _render_trajectory_atoms,
)
from vibeview.app.helpers import (
    _render_vibration_atoms as _render_vibration_atoms,
)
from vibeview.app.helpers import (
    _render_wf_density as _render_wf_density,
)
from vibeview.app.helpers import (
    _render_wf_elf as _render_wf_elf,
)
from vibeview.app.helpers import (
    _render_wf_laplacian as _render_wf_laplacian,
)
from vibeview.app.helpers import (
    _render_wf_nci as _render_wf_nci,
)
from vibeview.app.helpers import (
    _replay_wavefunction_surface as _replay_wavefunction_surface,
)
from vibeview.app.helpers import (
    _requested_energy_window as _requested_energy_window,
)
from vibeview.app.helpers import (
    _reset_frozen as _reset_frozen,
)
from vibeview.app.helpers import (
    _reset_scene as _reset_scene,
)
from vibeview.app.helpers import (
    _residue_selection_available as _residue_selection_available,
)
from vibeview.app.helpers import (
    _residue_selection_summary as _residue_selection_summary,
)
from vibeview.app.helpers import (
    _restore_cpk_atoms as _restore_cpk_atoms,
)
from vibeview.app.helpers import (
    _rollback_atom_properties_overlay as _rollback_atom_properties_overlay,
)
from vibeview.app.helpers import (
    _run_info_text as _run_info_text,
)
from vibeview.app.helpers import (
    _save_export_disk as _save_export_disk,
)
from vibeview.app.helpers import (
    _save_screenshot_disk as _save_screenshot_disk,
)
from vibeview.app.helpers import (
    _scan_gui_directory as _scan_gui_directory,
)
from vibeview.app.helpers import (
    _scan_gui_glob as _scan_gui_glob,
)
from vibeview.app.helpers import (
    _scene_background as _scene_background,
)
from vibeview.app.helpers import (
    _scene_surface_mesh as _scene_surface_mesh,
)
from vibeview.app.helpers import (
    _section_handler as _section_handler,
)
from vibeview.app.helpers import (
    _section_title as _section_title,
)
from vibeview.app.helpers import (
    _SectionContext as _SectionContext,
)
from vibeview.app.helpers import (
    _SectionHandler as _SectionHandler,
)
from vibeview.app.helpers import (
    _selectable_chains as _selectable_chains,
)
from vibeview.app.helpers import (
    _set_background_mode as _set_background_mode,
)
from vibeview.app.helpers import (
    _smiles_available as _smiles_available,
)
from vibeview.app.helpers import (
    _spawn_task as _spawn_task,
)
from vibeview.app.helpers import (
    _startup_background_override as _startup_background_override,
)
from vibeview.app.helpers import (
    _step_mo_selection as _step_mo_selection,
)
from vibeview.app.helpers import (
    _structure_display_options as _structure_display_options,
)
from vibeview.app.helpers import (
    _structure_edit_snapshot as _structure_edit_snapshot,
)
from vibeview.app.helpers import (
    _sync_orthographic_switch as _sync_orthographic_switch,
)
from vibeview.app.helpers import (
    _system_descriptor as _system_descriptor,
)
from vibeview.app.helpers import (
    _triple as _triple,
)
from vibeview.app.helpers import (
    _two_d_panel_kinds as _two_d_panel_kinds,
)
from vibeview.app.helpers import (
    _update_edit_highlights as _update_edit_highlights,
)
from vibeview.app.helpers import (
    _update_frozen_highlights as _update_frozen_highlights,
)
from vibeview.app.helpers import (
    _update_pick_markers as _update_pick_markers,
)
from vibeview.app.helpers import (
    _update_reaction_label as _update_reaction_label,
)
from vibeview.app.helpers import (
    _update_trajectory_plot as _update_trajectory_plot,
)
from vibeview.app.helpers import (
    _vibeqc_importable as _vibeqc_importable,
)
from vibeview.app.helpers import (
    _white_to_transparent as _white_to_transparent,
)
from vibeview.app.helpers import (
    build_pending_container as build_pending_container,
)
from vibeview.app.helpers import (
    compute_volume_difference as compute_volume_difference,
)
from vibeview.app.helpers import (
    serve as serve,
)
from vibeview.app.lifecycle import LifecycleControllers as LifecycleControllers
from vibeview.app.playback import PlaybackControllers as PlaybackControllers
from vibeview.app.preferences import PreferencesControllers as PreferencesControllers
from vibeview.app.queue import QueueControllers as QueueControllers
from vibeview.app.runtime import ViewerRuntime as ViewerRuntime
from vibeview.app.sessions import SessionsControllers as SessionsControllers
from vibeview.app.wavefunction import WavefunctionControllers as WavefunctionControllers
from vibeview.qvf import QVFReader as QVFReader


def create_app(readers: QVFReader | Sequence[QVFReader]) -> Any:
    """Build a Trame viewer from one archive or a nonempty reader sequence.

    Reader ownership, worker generations and scene locks belong to one runtime;
    area controllers share it so file replacement is visible to every callback.
    """
    if isinstance(readers, (list, tuple)):
        if not readers:
            raise ValueError("create_app requires at least one QVFReader")
        all_readers = list(readers)
    else:
        all_readers = [readers]
    reader = all_readers[0]

    try:
        # The widget modules (F401) are imported only so a missing [viewer]
        # extra fails with the hint below rather than inside the layout;
        # vibeview.app.layout imports them itself.
        from trame.app import get_server as get_server
        from trame.ui.vuetify3 import VAppLayout as VAppLayout  # noqa: F401
        from trame.widgets import client as client  # noqa: F401
        from trame.widgets import html as html
        from trame.widgets import vuetify3 as v  # noqa: F401
        from trame_vtk.modules import vtk as vtk_module
        from trame_vtk.widgets.vtk import VtkLocalView as VtkLocalView  # noqa: F401
    except ImportError as e:
        from vibeview.install_hints import install_hint as install_hint

        # Name an extra that exists: this used to say ``.[gpu]``, which is not
        # one of vibe-view's extras (viewer/capture/tui/ase/... ), so the one
        # message a newcomer sees pointed at an install command that fails.
        raise ImportError(
            f"the interactive viewer needs the [viewer] extra ({e}).\n"
            f"  {install_hint('viewer')}\n"
            f"`vibe-view show` and `vibe-view capture` need no extra."
        ) from None
    server = get_server(client_type="vue3")
    server.enable_module(vtk_module)
    viewer = ViewerRuntime(
        server=server,
        plotter=_helpers.new_plotter(),
        reader=reader,
        _all_readers=all_readers,
        viewer_state=ViewerState.from_manifest(
            reader.viewer_defaults, {s.id: s.kind for s in reader.sections}
        ),
    )

    # ── SSAO for server-side renders (screenshots, video frames) ──
    # A renderer pass never reaches the client's live viewport — vtk.js
    # redraws from serialized actors — so this shapes exports only.
    # Matches the ssao_enabled state default (True); radius/samples are the
    # shared _SSAO_PASS every other path uses.
    try:
        from vibeview.material_presets import ambient_occlusion_pass as ambient_occlusion_pass

        ambient_occlusion_pass(viewer.plotter, **_helpers._SSAO_PASS)
    except Exception:
        pass  # SSAO not available on this VTK build

    # trame's server is a process-wide singleton, so every create_app call
    # appended its state watchers to the previous app's: a second viewer in
    # one process ran each play/stop, monitor and MO-click handler twice,
    # once against the dead app's plotter (#37-13). Unregister the previous
    # app's watchers and register through _watch_state from here on.
    for _key, _func in getattr(viewer.server, "_vibeview_state_watchers", ()):
        _callbacks = viewer.server.state._change_callbacks.get(_key)
        if _callbacks:
            _callbacks[:] = [entry for entry in _callbacks if entry[0] is not _func]

    viewer.server._vibeview_state_watchers = []
    initialize_state(viewer)
    initialize_scene(viewer)
    viewer.display = DisplayControllers(viewer)
    viewer.editing = EditingControllers(viewer)
    viewer.exports = ExportsControllers(viewer)
    viewer.files = FilesControllers(viewer)
    viewer.lifecycle = LifecycleControllers(viewer)
    viewer.playback = PlaybackControllers(viewer)
    viewer.preferences = PreferencesControllers(viewer)
    viewer.queue = QueueControllers(viewer)
    viewer.sessions = SessionsControllers(viewer)
    viewer.wavefunction = WavefunctionControllers(viewer)
    from vibeview.live_opt import LiveOptService as LiveOptService

    viewer.live_opt = LiveOptService(
        on_step=viewer.editing._live_opt_on_step,
        on_done=viewer.editing._live_opt_on_done,
        on_error=viewer.editing._live_opt_on_error,
        on_note=viewer.editing._live_opt_on_note,
    )
    _helpers._scene_hooks(viewer.plotter).wf_render_lock = viewer._wf_render_lock
    _helpers._scene_hooks(viewer.plotter).state = server.state
    bind_controllers(viewer)

    # ── UI Layout ─────────────────────────────────────────────────────

    from vibeview.app.layout import build_layout as build_layout

    build_layout(viewer.server, viewer.ctrl, viewer.plotter)
    initialize_about(viewer)

    for _auto_id in viewer.viewer_state.auto_open:
        if not viewer.reader.has_section(_auto_id):
            continue
        # The first structure auto_open lists is the one shown (QVF spec
        # § 4.1); opening a later listed structure would replace it (#90).
        if (
            viewer.reader.get_section(_auto_id).kind == "structure"
            and _auto_id != viewer.reader.first_structure_id
        ):
            continue
        viewer.display.activate_section(_auto_id)
        if viewer.server.state.selected_section == _auto_id and not str(
            viewer.server.state.status_message or ""
        ).startswith("Could not open"):
            viewer.server.state.status_message = f"Auto-opened: {_auto_id}"

    # Start auto-save loop
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(viewer.lifecycle._auto_save_loop())
    except RuntimeError:
        pass  # No event loop (e.g. in offscreen tests)
    return server
