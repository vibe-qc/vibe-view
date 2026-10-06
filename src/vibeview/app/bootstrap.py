"""Initial UI defaults and scene construction, separate from controller wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibeview.app import helpers as _helpers
from vibeview.plotter_compat import (
    view_isometric,
)
from vibeview.renderers import palette as _palette
from vibeview.renderers.nci import RDG_ISOVALUE
from vibeview.units import band_energy_unit as _band_energy_unit_key
from vibeview.units import energy_label
from vibeview.units import energy_unit as _energy_unit_key
from vibeview.units import length_unit as _length_unit_key
from vibeview.vibration_amplitude import DEFAULT_PEAK_EXCURSION_ANGSTROM

if TYPE_CHECKING:
    from vibeview.app.runtime import ViewerRuntime


def initialize_state(viewer: ViewerRuntime) -> None:
    _helpers._clamp_viewer_replication(viewer.reader, viewer.viewer_state)

    replication = list(viewer.viewer_state.replication)

    # Prepare section list for the sidebar
    viewer.section_list = _helpers._build_section_list(viewer.reader)

    # Collapse multiple volume.orbital sections into one sidebar entry; the
    # right-panel select then switches between them. Other kinds (density,
    # spectra, …) each keep their own entry with a friendly title.
    viewer.orbital_sections = [s for s in viewer.section_list if s["kind"] == "volume.orbital"]

    # Same pattern for basis.ao sections.
    viewer.basis_ao_sections = [s for s in viewer.section_list if s["kind"] == "basis.ao"]

    # Cheap early periodicity probe — the orbital sidebar-group title
    # ("Molecular Orbitals" vs "Crystalline Orbitals") needs the flag
    # before _build_structure_scene runs and rebinds it authoritatively
    # below. Only the structure JSON is parsed here; no rendering.
    # (Regression: a782ed40 referenced is_periodic here while it was
    # first assigned ~500 lines later → UnboundLocalError on every QVF
    # with volume.orbital sections.)
    viewer.is_periodic = _helpers._probe_is_periodic(viewer.reader)

    viewer.sidebar_entries = _helpers._build_sidebar_entries(
        viewer.reader, viewer.section_list, viewer.is_periodic
    )

    # Options for the right-panel orbital picker.
    viewer.orbital_options = [{"title": s["id"], "value": s["id"]} for s in viewer.orbital_sections]

    # Options for the right-panel AO picker.
    viewer.ao_options = [{"title": s["id"], "value": s["id"]} for s in viewer.basis_ao_sections]
    from vibeview.settings import load_settings

    persisted_settings = load_settings()

    # Saved settings seed the session they were saved for (#37-3): the
    # dialog persisted them but startup hard-coded the defaults, so the
    # dark-background, material and auto-save choices never survived a
    # restart.
    _startup_dark = bool(persisted_settings.get("dark_background", True))

    _startup_preset = str(persisted_settings.get("material_preset") or "cpk_glossy")

    from vibeview.material_presets import MATERIAL_PRESETS

    if _startup_preset not in MATERIAL_PRESETS:
        _startup_preset = "cpk_glossy"

    _startup_autosave = _helpers._auto_save_interval_seconds(
        persisted_settings.get("auto_save_interval")
    )

    _startup_energy_unit = _energy_unit_key(persisted_settings.get("energy_unit"))

    _startup_length_unit = _length_unit_key(persisted_settings.get("measure_unit"))

    _startup_band_energy_unit = _band_energy_unit_key(persisted_settings.get("band_energy_unit"))

    viewer.server.state.update(
        {
            "section_list": viewer.section_list,
            "sidebar_entries": viewer.sidebar_entries,
            "selected_section": None,
            "active_volume_id": None,
            "isovalue": 0.05,
            "colormap": "viridis",
            "opacity": 0.6,
            # Orbital lobes keep their own opacity, separate from the volume
            # isosurface one above: volume activation overwrites `opacity`
            # from the volume hints, which clobbered the orbital setting.
            # Defaults opaque — any value < 1.0 puts vtk.js into
            # depth-peeling, which is what hung the client on MO renders
            # (confirmed 2026-07-19: opaque renders fine, translucent hangs).
            "mo_opacity": 1.0,
            # ESP-mapped density: when colour_by_esp is True, the active
            # volume.density isosurface is coloured by electrostatic-potential
            # values from a companion volume.potential section instead of the
            # density scalar field.
            "color_by_esp": False,
            "esp_available": False,
            # Multi-isosurface layers — each dict has isovalue, opacity, colour.
            "extra_isosurfaces": [],
            "replication_nx": replication[0],
            "replication_ny": replication[1],
            "replication_nz": replication[2],
            "is_periodic": False,
            # Per-axis periodicity. Non-periodic axes get no replication control:
            # their lattice column is synthesized bookkeeping, not a cell edge.
            "pbc_a": False,
            "pbc_b": False,
            "pbc_c": False,
            "structure_dim": 3,
            # Grid-level periodic orbital replication (v2.0).
            # 0 = single cell, N = (2N+1)³ supercell; tiles the
            # scalar field before marching cubes for seamless orbitals.
            "periodic_replication": 0,
            "periodic_replication_options": [
                {"title": "Single cell", "value": 0},
                {"title": "3×3×3", "value": 1},
                {"title": "5×5×5", "value": 2},
                {"title": "7×7×7", "value": 3},
            ],
            # Cyclic-cluster wrap: roll a face-straddling orbital / Wannier
            # function whole into the cell (minimum-image). Periodic-only.
            "wrap_periodic_orbital": False,
            # Wannier-centre overlay (x_ccm.wannier_centers): shown-when-present
            # gate + user toggle.
            "has_wannier_centers": False,
            "show_wannier_centers": False,
            # ``show_atom_labels`` lives lower next to the other display toggles.
            # Atomic-properties overlay state. ``atom_properties_active`` is
            # the gate the right-panel controls watch; ``charge_kind`` picks
            # between Mulliken / Löwdin / Hirshfeld / IAO; ``color_by_charge``
            # swaps the CPK palette for a sign-based red/blue tint.
            "atom_properties_active": False,
            "atom_properties_section_id": None,
            "charge_kind": "mulliken",
            "charge_kind_options": [
                {"title": "Mulliken", "value": "mulliken"},
                {"title": "Löwdin", "value": "loewdin"},
                {"title": "Hirshfeld", "value": "hirshfeld"},
                {"title": "IAO", "value": "iao"},
            ],
            "color_by_charge": False,
            "colormap_options": [
                "viridis",
                "plasma",
                "inferno",
                "magma",
                "cividis",
                "coolwarm",
                "RdBu",
                "PiYG",
                "seismic",
                "Spectral",
            ],
            "volume_loaded": False,
            "bands_image": None,
            "bands_html": None,
            "bands_title": "Band Structure",
            # Energy window for the bands / DOS charts (#26). All-electron
            # periodic archives carry core states thousands of eV below E_F,
            # and an autoscaled axis flattens the valence region into a few
            # pixels around the E_F line. The activators resolve a window —
            # the manifest hint, else the renderer's valence default — and
            # write it back here, so the fields always show what is drawn.
            # `bands_window_auto` records whether that resolved window is
            # still the default or the reader has typed one in.
            "bands_window_available": False,
            "bands_window_auto": True,
            # Display unit of the bands / DOS energy axis and of the window
            # fields (#58); the archive and the window logic stay in eV.
            "band_energy_unit": _startup_band_energy_unit,
            "band_energy_label": energy_label(_startup_band_energy_unit),
            "bands_emin": None,
            "bands_emax": None,
            "phonon_html": None,
            "phonon_title": "Phonon",
            "eos_html": None,
            "eos_title": "Equation of State",
            "spectra_image": None,
            "spectra_html": None,
            "spectra_title": "Spectrum",
            # Multi-file spectrum comparison (design refresh 2026, item 8).
            "spectra_compare": False,
            # Spectra display controls: broadening multiplier + normalization.
            "spectra_gamma_scale": 1.0,
            "spectra_normalize": False,
            "spectra_x_unit": "native",
            "spectra_display": "both",  # both | envelope | sticks
            # Structure Library (design refresh 2026, item 7): curated
            # molecule DB from vibeqc_naming; [] when not installed.
            "library_query": None,
            "library_names": _helpers._library_structure_names(),
            # Recently used library structures, most-recent-first. Picking from
            # 83 names via autocomplete is tedious when you keep reaching for
            # the same handful, so used names come back as one-click chips.
            "library_recent": [],
            # Pinned library structures. Unlike library_recent (implicit,
            # session-scoped) these are chosen deliberately, so they ride in
            # the saved session alongside bookmarks.
            "library_favorites": [],
            # The details panel is a srcdoc iframe fed by renderers, and
            # much of what it shows is untrusted archive content: run
            # logs, embedded attachments, .bib text. Its iframe carries an
            # empty sandbox and there is no way to ask for scripts —
            # anything that needs them uses the chart channel below.
            "properties_html": None,
            "properties_title": "Details",
            # Chart channel: renderer-built Plotly figures that happen to
            # land in the bottom panel (SCF convergence, COOP/COHP, the
            # orbital energy diagram). Plotly is JavaScript, so this
            # iframe runs with `allow-scripts` — which is why it is a
            # channel of its own and no renderer of archive *text* can
            # reach it. Same arrangement as bands_html / eos_html /
            # spectra_html, which are scripted for the same reason.
            "chart_html": None,
            "chart_title": "Chart",
            # Attachments of the active run.record, for the download row.
            # Metadata only — attachment bytes are read on demand and go
            # straight to a download, never into the panel DOM.
            "run_record_attachments": [],
            "run_record_attachment_section": "",
            # SCF convergence plot: drop the initial-guess point, whose energy
            # dominates the Y scale and flattens the converged tail.
            "scf_skip_guess": False,
            # Plot |E - E_final| on a log axis so the convergence tail is
            # readable (design refresh 2026, SCF item).
            "scf_log_energy": False,
            # User-resizable side panels (drag the inner edge; px).
            "left_panel_width": 280,
            "right_panel_width": 300,
            "trajectory_energy_image": None,
            "trajectory_frame": 0,
            "trajectory_n_frames": 0,
            "trajectory_playing": False,
            "vibration_mode": 0,
            "vibration_n_modes": 0,
            "vibration_frequencies": [],
            "vibration_mode_items": [],
            "vibration_ir_intensities": [],
            # Orbital picker (right-panel dropdown for switching between
            # volume.orbital sections when they're collapsed in the sidebar).
            "orbital_options": viewer.orbital_options,
            "selected_orbital": (
                viewer.orbital_options[0]["value"] if viewer.orbital_options else None
            ),
            "ao_options": viewer.ao_options,
            "selected_ao": (viewer.ao_options[0]["value"] if viewer.ao_options else None),
            # Fermi-surface band selector (populated when a fermi_surface
            # section is active; None selection means "all bands").
            "fermi_band_options": [],
            "fermi_selected_bands": None,
            # Phase D1: compare/overlay mode (active when >1 file is loaded).
            "compare_mode": False,
            "compare_align": False,
            "compare_legend": [],
            "compare_highlight": None,  # index of file to highlight, or None for all
            "compare_highlight_options": [],  # populated when files change
            # Phase D2: density-difference (A - B) between two loaded files.
            "density_file_options": [
                {"title": _helpers._job_name(r), "value": i}
                for i, r in enumerate(viewer._all_readers)
                if any(s.kind == "volume.density" for s in r.sections)
            ],
            "diff_a": None,
            "diff_b": None,
            "vibration_amplitude": 1.0,
            # Peak atomic excursion (Å) at amplitude 1.0, shared with the
            # terminal (vibeview.vibration_amplitude, #38-28).
            "vibration_peak_default": DEFAULT_PEAK_EXCURSION_ANGSTROM,
            "vibration_playing": False,
            "vibration_phase": 0.0,
            "status_message": "",
            "crossfade_active": False,
            "crossfade_blend": viewer.viewer_state.crossfade_blend,
            # Wavefunction (MO) state
            "wf_section_id": None,
            # Click-to-render from the orbital energy diagram: the sandboxed
            # diagram iframe postMessages "{spin}:{index}@{nonce}" here; a
            # state-change watcher renders that MO (design refresh 2026).
            "mo_click_request": "",
            "wf_mo_rows": [],
            # Display unit for orbital energies (#58); archives stay in Eh.
            "energy_unit": _startup_energy_unit,
            # Display unit for measured lengths (#58); structures stay in Å.
            "measure_unit": _startup_length_unit,
            "wf_selected_mo": "restricted:0",
            "wf_selected_spin": "restricted",
            # Re-localize (vibeview.relocalize): ask vibe-qc for a
            # localization criterion the file does not already contain.
            # ``None`` = not yet probed, mirroring ``live_opt_available``.
            "relocalize_available": None,
            "relocalize_backend_python": persisted_settings.get("relocalize_backend_python", ""),
            "settings_relocalize_backend_python": persisted_settings.get(
                "relocalize_backend_python", ""
            ),
            "relocalize_capabilities": {},
            "relocalize_probe_running": False,
            "relocalize_backend_status": "Backend not checked",
            "relocalize_source": "supplied",
            "relocalize_reason": "Check the vibe-qc backend in Settings",
            "relocalize_result_summary": "",
            "relocalize_charge_rows": [],
            "relocalize_has_overlay": False,
            "relocalize_running": False,
            "relocalize_status": "",
            "relocalize_method": "ibo",
            "relocalize_method_options": [],
            "wf_n_per_dim": 60,
            "wf_component": "real",
            "wf_is_complex": False,
            "mo_last_component": "real",
            "relocalize_supported": False,
            # Total density vs spin density (rho_alpha - rho_beta). The
            # button below drives both; spin needs an unrestricted section.
            "wf_density_spin": False,
            # Gates the spin-density button; set per activated section.
            "wf_spin_unrestricted": False,
            # ELF isosurface level. 0.8 is the conventional choice for
            # showing shells / bonds / lone pairs; the density's 0.05 default
            # would be meaningless for a function bounded in [0, 1].
            "wf_elf_iso": 0.8,
            # NCI reduced-gradient isosurface level: the NCIPLOT plotting
            # convention, shared with the stored volume.rdg path (nci.py).
            "wf_nci_iso": RDG_ISOVALUE,
            # del^2 rho isosurface level (e/bohr^5). Drawn as a signed pair;
            # the NEGATIVE lobe is the chemically interesting one (Bader's
            # charge concentration), which is why the default is modest.
            "wf_laplacian_iso": 1.0,
            "wf_orbital_kind": "canonical",
            "wf_animating": False,
            "wf_anim_speed": 0.5,  # seconds per frame
            # Panel titles — dynamic based on is_periodic + orbital_kind
            "orbital_panel_title": "Molecular Orbitals",
            "wf_panel_title": "Molecular Orbitals",
            # Structure representation style. Biomolecules open as a
            # ribbon: see _default_representation().
            "representation_style": _helpers._default_representation(viewer.reader),
            # Cartoon ribbon colouring: "chain" or "structure".
            "cartoon_color_mode": "chain",
            "atom_color_mode": "element",
            "structure_color_mode": (
                "chain"
                if _helpers._default_representation(viewer.reader) == "cartoon"
                else "element"
            ),
            "residue_visibility": "all",
            "residue_pick_mode": False,
            "show_hydrogen_bonds": False,
            # Residue/chain selection (D4). Empty selects nothing; matching
            # residues render white over the active ribbon colour.
            "residue_selection": "",
            "residue_selection_summary": "",
            "residue_selection_chains": _helpers._selectable_chains(viewer.reader),
            "residue_selection_available": _helpers._residue_selection_available(viewer.reader),
            # Material presets (v1.6)
            "material_preset": _startup_preset,
            "material_preset_options": [
                {"title": "CPK Glossy", "value": "cpk_glossy"},
                {"title": "Matte", "value": "matte"},
                {"title": "Glass", "value": "glass"},
                {"title": "Metallic", "value": "metallic"},
                {"title": "Toon / NPR", "value": "toon"},
                {"title": "Scientific", "value": "scientific"},
            ],
            # Persistent settings dialog state
            "settings_dialog": False,
            "settings_dark_background": _startup_dark,
            "settings_material_preset": _startup_preset,
            "settings_auto_save_interval": _startup_autosave,
            "settings_energy_unit": _startup_energy_unit,
            "settings_measure_unit": _startup_length_unit,
            "settings_measure_unit_options": [
                {"title": "Ångström (Å)", "value": "angstrom"},
                {"title": "Bohr (a₀)", "value": "bohr"},
            ],
            "settings_band_energy_unit": _startup_band_energy_unit,
            "settings_band_energy_unit_options": [
                {"title": "Electronvolt (eV)", "value": "ev"},
                {"title": "Hartree (Eh)", "value": "hartree"},
                {"title": "kcal/mol", "value": "kcal_mol"},
            ],
            "settings_energy_unit_options": [
                {"title": "Hartree (Eh)", "value": "hartree"},
                {"title": "Electronvolt (eV)", "value": "ev"},
                {"title": "kcal/mol", "value": "kcal_mol"},
            ],
            "settings_interval_options": [
                {"title": "30 seconds", "value": 30},
                {"title": "60 seconds", "value": 60},
                {"title": "5 minutes", "value": 300},
                {"title": "15 minutes", "value": 900},
                {"title": "Disabled", "value": 0},
            ],
            # QA validation warnings surfaced in the status bar.
            "qa_warnings": [],
            # Isovalue units label — set when a volume section is activated.
            "isovalue_units": "e/bohr³",
            # LOD: subsample large grids for performance.
            "reduce_detail": True,
            # MO visibility toggle — lets the user hide the isosurface without
            # navigating away from the wavefunction section.
            "mo_visible": False,
            "mo_last_spin": None,
            "mo_last_index": None,
            # Exact recipe for the last successfully rendered computed
            # wavefunction surface. QVF-backed volume sections use the
            # separate active_volume_id lifecycle above.
            "wf_surface_kind": None,
            # Reaction-path waypoint state (mirrors trajectory state).
            "reaction_waypoints": [],
            "reaction_current_label": "",
            # Bookmarks (from manifest) + user bookmarks
            "bookmark_names": [bm.name for bm in viewer.viewer_state.bookmarks],
            "selected_bookmark": (
                viewer.viewer_state.bookmarks[0].name if viewer.viewer_state.bookmarks else None
            ),
            "user_bookmarks": [],  # [{name, camera, section_id, isovalue, colormap}]
            "user_bookmark_names": [],
            # v1.3 Presentation mode
            "presentation_mode": False,
            "presentation_slide": 0,
            "presentation_slides": [],  # list of {name, camera, sections, duration}
            "presentation_slide_duration": 5,  # seconds auto-advance
            "presentation_auto_advance": False,
            # True when the active slide's primary rendering lives in the
            # lower 2D panel rather than the VTK viewport.
            "presentation_panel_slide": False,
            # Session save/restore
            "session_path": "",
            "session_dirty": False,
            # About dialog
            "about_dialog": False,
            "about_version": "",
            "about_system_info": "",
            # Point group symmetry detection
            "point_group": "",
            "point_group_details": "",
            # Download manager
            "download_manager_dialog": False,
            "download_history": [],
            # Screenshot
            "screenshot_data": None,
            "screenshot_ready": False,
            "screenshot_dialog": False,
            "screenshot_scale": 2,
            "screenshot_scale_options": [1, 2, 4],
            "screenshot_transparent": False,
            # Video export
            "has_animatable": False,
            "video_kind": "",  # "trajectory", "vibration", "orbital"
            "video_export_dialog": False,
            "video_export_format": "mp4",
            "video_export_format_options": [
                {"title": "MP4 (H.264)", "value": "mp4"},
                {"title": "GIF (animated)", "value": "gif"},
                {"title": "PNG frames", "value": "frames"},
            ],
            "video_export_fps": 30,
            "video_export_fps_options": [
                {"title": "15 fps", "value": 15},
                {"title": "24 fps (film)", "value": 24},
                {"title": "30 fps", "value": 30},
                {"title": "60 fps", "value": 60},
            ],
            "video_export_kind": "turntable",
            "video_export_kind_options": [
                {"title": "360° Turntable", "value": "turntable"},
                {"title": "Trajectory", "value": "trajectory"},
                {"title": "Vibration", "value": "vibration"},
                {"title": "Orbital sweep", "value": "orbital"},
            ],
            "video_export_running": False,
            # High-quality raytrace render
            "hq_render_dialog": False,
            "hq_render_quality": "high",
            "hq_render_quality_options": [
                {"title": "Draft (fast, 16 spp)", "value": "draft"},
                {"title": "Standard (64 spp)", "value": "standard"},
                {"title": "High (256 spp, denoised)", "value": "high"},
                {"title": "Publication (512 spp, denoised)", "value": "publication"},
            ],
            "hq_render_resolution": "3840x2160",
            "hq_render_resolution_options": [
                {"title": "HD (1920×1080)", "value": "1920x1080"},
                {"title": "4K (3840×2160)", "value": "3840x2160"},
                {"title": "8K (7680×4320)", "value": "7680x4320"},
            ],
            "hq_render_environment": "studio",
            "hq_render_environment_options": [
                {"title": "Dark", "value": "dark"},
                {"title": "Light", "value": "light"},
                {"title": "Studio", "value": "studio"},
                {"title": "Sunset", "value": "sunset"},
                {"title": "Scientific", "value": "scientific"},
            ],
            "hq_render_material": "cpk",
            "hq_render_material_options": [
                {"title": "CPK (semi-gloss)", "value": "cpk"},
                {"title": "Metallic", "value": "metallic"},
                {"title": "Glass", "value": "glass"},
                {"title": "Ceramic", "value": "ceramic"},
            ],
            "hq_render_dof": False,
            "hq_render_save_disk": False,
            "hq_render_saved_path": "",
            "hq_render_progress": 0.0,
            "hq_render_progress_msg": "",
            "hq_render_running": False,
            # Display toggles
            "show_atom_labels": False,
            "dark_background": _startup_dark,
            "background_override": _helpers._startup_background_override(
                _startup_dark, MATERIAL_PRESETS[_startup_preset].background_color
            ),
            "raytrace_enabled": False,
            "toon_mode": False,
            # The figure look (#68): black inverted-hull outlines on atoms and
            # bonds, and the viewport-chrome switch. The Publication view
            # preset turns both on; clearing the picker restores the look
            # the first preset overwrote, kept in view_preset_restore.
            "figure_outlines": False,
            "chrome_hidden": False,
            "view_preset": None,
            "view_preset_restore": None,
            # OSPRay ray tracing is only meaningful when (a) the VTK build
            # ships OSPRay and (b) we're rendering server-side. Detected at
            # startup; the app-bar button is hidden when unavailable so it
            # can't throw "object has no attribute 'enable_ray_tracing'".
            "raytrace_available": _helpers._ospray_available(),
            # Screen-space ambient occlusion (VTK 9.2+)
            "ssao_enabled": True,
            # Orthographic (parallel) projection: no perspective distortion,
            # for crystallography / publication figures (design refresh 2026).
            "orthographic_projection": False,
            # Volume clip planes (0.0-1.0 fractional position)
            "clip_x": 0.5,
            "clip_y": 0.5,
            "clip_z": 0.5,
            "clip_enabled": False,
            # 2D cross-section slice mode — when enabled, replaces the
            # clipped isosurface with a colour-mapped plane at the clip
            # position sampling the volume data values.
            "show_slice": False,
            # Visual-only feedback for high-frequency clip-slider updates.
            # The focused slider already exposes aria-valuenow, so routing
            # every tick through the global polite status region would make
            # assistive technology announce an avoidable stream of updates.
            "clip_position_message": "",
            # True while an animation (trajectory/vibration/reaction.path)
            # has hidden the static structure actors; gates the rebuild on
            # return to a non-animation section (A5-03).
            "structure_hidden": False,
            # Measurement
            "measure_mode": False,
            "selected_atoms": [],
            "measure_result": "",
            # Calculation Parameters (v1.1: shown when structure is present)
            "show_param_panel": False,
            "calc_method": "rhf",
            "calc_functional": "pbe",
            "calc_basis": "sto-3g",
            "calc_charge": 0,
            "calc_multiplicity": 1,
            "calc_template": "single_point",
            "method_options": [
                {"title": "RHF", "value": "rhf"},
                {"title": "UHF", "value": "uhf"},
                {"title": "RKS (DFT)", "value": "rks"},
                {"title": "UKS (DFT)", "value": "uks"},
                {"title": "RMP2", "value": "rmp2"},
                {"title": "UMP2", "value": "ump2"},
            ],
            "functional_options": [
                {"title": "PBE", "value": "pbe"},
                {"title": "PBE0", "value": "pbe0"},
                {"title": "B3LYP", "value": "b3lyp"},
                {"title": "BLYP", "value": "blyp"},
                {"title": "BP86", "value": "bp86"},
                {"title": "TPSS", "value": "tpss"},
                {"title": "M06-2X", "value": "m06-2x"},
                {"title": "\u03c9B97X-D", "value": "wb97x-d"},
                {"title": "CAM-B3LYP", "value": "cam-b3lyp"},
                {"title": "LDA", "value": "lda"},
                {"title": "r\u00b2SCAN", "value": "r2scan"},
                {"title": "HSE06", "value": "hse06"},
            ],
            "basis_options": [
                {"title": "STO-3G", "value": "sto-3g"},
                {"title": "3-21G", "value": "3-21g"},
                {"title": "6-31G", "value": "6-31g"},
                {"title": "6-31G(d)", "value": "6-31gd"},
                {"title": "6-31G(d,p)", "value": "6-31gdp"},
                {"title": "6-311G(d,p)", "value": "6-311gdp"},
                {"title": "cc-pVDZ", "value": "cc-pvdz"},
                {"title": "cc-pVTZ", "value": "cc-pvtz"},
                {"title": "aug-cc-pVDZ", "value": "aug-cc-pvdz"},
                {"title": "aug-cc-pVTZ", "value": "aug-cc-pvtz"},
                {"title": "def2-SVP", "value": "def2-svp"},
                {"title": "def2-TZVP", "value": "def2-tzvp"},
                {"title": "def2-QZVP", "value": "def2-qzvp"},
            ],
            "calc_template_options": [
                {"title": "Single Point", "value": "single_point"},
                {"title": "Geometry Optimization", "value": "optimization"},
                {"title": "Frequencies", "value": "frequencies"},
                {"title": "Single Point + Freq + Opt", "value": "full"},
                {"title": "Periodic Single Point", "value": "periodic"},
            ],
            "periodic_template_options": [
                {"title": "Periodic Single Point", "value": "periodic"},
            ],
            # v1.2 Editor state
            "edit_mode": False,
            "edit_selected": [],  # atom indices currently selected for editing
            "frozen_atoms": [],  # atom indices held fixed during live-opt (B3)
            "edit_history": [],  # undo stack: previous atoms+lattice transactions
            "edit_future": [],  # redo stack
            "edit_new_element": "C",
            "element_picker_open": False,
            "element_picker_target": "new",  # new | change
            # M3 live geometry optimization (auto-relax while building)
            "live_opt_enabled": False,
            "live_opt_available": None,  # None = not yet probed
            "live_opt_status": "",
            "live_opt_engine": "msindo",
            "live_opt_engine_options": [],  # populated by the worker probe
            # Keyboard shortcuts
            "shortcuts_help_dialog": False,
            # Command palette (Ctrl/Cmd+K): fuzzy-search every quick action
            # (design refresh 2026). Static actions plus per-section "open"
            # entries; filtering is client-side on palette_query.
            "palette_open": False,
            "palette_query": "",
            "palette_actions": _helpers._palette_actions_for(viewer.reader),
            "shortcut_keys": {
                "Ctrl/Cmd+K": "Command palette",
                "e": "Toggle edit mode",
                "m": "Toggle measure mode",
                "r": "Reset camera",
                "s": "Screenshot",
                "v": "Video export dialog",
                "p": "Presentation mode",
                "Ctrl/Cmd+Z": "Undo (edit mode)",
                "Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y": "Redo (edit mode)",
                "Delete": "Delete selected atoms",
                "Escape": "Exit presentation, edit or measure mode",
                "ArrowLeft/Right": "Previous/next slide (presentation mode)",
                "?": "Show this help",
            },
            # Right-click context menu
            "context_menu_show": False,
            "context_menu_x": 0,
            "context_menu_y": 0,
            "context_menu_atom_idx": -1,
            # Hover tooltip
            "hover_tooltip": "",
            "hover_tooltip_visible": False,
            "hover_tooltip_x": 0,
            "hover_tooltip_y": 0,
            # Welcome / quickstart panel
            "welcome_visible": True,
            # File watcher for auto-reload on QVF changes
            "file_watcher_enabled": False,
            "file_watcher_interval": 5.0,
            # Streaming-checkpoint provenance of the active file (M4):
            # "" (not a live checkpoint) / "running" / "converged" / "failed",
            # plus a short "#seq · iter N · E ... Eh" summary for the app bar.
            "live_run_status": _helpers._checkpoint_summary(viewer.reader)[0],
            "live_checkpoint_text": _helpers._checkpoint_summary(viewer.reader)[1],
            "file_last_modified": "",
            # UI theme toggle. Defaults light to match the long-standing
            # visible default (the old dark-mode class was never applied, so
            # the chrome has always rendered light); the toggle now actually
            # switches the Vuetify theme (design refresh 2026 theme pass).
            "ui_dark_mode": False,
            # v1.3 Fragment Library state
            "build_fragment": None,
            "fragment_options": [
                {"title": "CH3  (methyl)", "value": "CH3"},
                {"title": "NH2  (amino)", "value": "NH2"},
                {"title": "OH   (hydroxyl)", "value": "OH"},
                {"title": "COOH (carboxyl)", "value": "COOH"},
                {"title": "Ph   (phenyl)", "value": "Ph"},
                {"title": "CHO  (aldehyde)", "value": "CHO"},
                {"title": "NO2  (nitro)", "value": "NO2"},
                {"title": "CN   (cyano)", "value": "CN"},
                {"title": "CF3  (trifluoromethyl)", "value": "CF3"},
                {"title": "SO3H (sulfonic acid)", "value": "SO3H"},
            ],
            # v1.3 Supercell dialog state
            "build_supercell_dialog": False,
            "build_supercell_nx": 1,
            "build_supercell_ny": 1,
            "build_supercell_nz": 1,
            # Export
            "export_data": None,
            "export_filename": "vibe-view-structure.obj",
            # Per-element colour overrides (design refresh 2026). The picker
            # lists only the elements actually present; overrides are keyed by
            # atomic number and applied inside cpk_color().
            "element_color_options": _helpers._present_elements(viewer.reader),
            "element_colors": {},
            "element_color_z": None,
            "element_color_value": _palette.ELEMENT_OVERRIDE_DEFAULT,
            # Multi-file support
            "header_title": _helpers._header_title(viewer.reader),
            "run_info": _helpers._run_info_text(viewer.reader),
            "file_names": [_helpers._job_name(r) for r in viewer._all_readers],
            # NOTE: must NOT start with an underscore — this key is bound in
            # the Vue template (the Files dropdown, v_model="active_file_idx"),
            # and Vue 3 refuses to expose `_`/`$`-prefixed identifiers to
            # template expressions, which threw ReferenceError on every render
            # and wiped out the whole app bar in multi-file mode (UI-01).
            "active_file_idx": 0,
            # File-open dialog (server-side path / directory open)
            "load_file_dialog": False,
            "load_file_bytes": "",
            "load_file_name": "",
            "open_path": "",
            "open_recursive": False,
            # Molecule builder (toolbar): name -> 3D structure, no file needed
            "builder_dialog": False,
            "builder_name": "",
            "builder_smiles": "",
            # RDKit is the optional [smiles] extra; the field is shown
            # either way so the install hint is discoverable.
            "smiles_available": _helpers._smiles_available(),
            "builder_available": _helpers._naming_available(),
            # vq Job Manager (M1/A1)
            "vq_panel_open": False,  # right-side Job Manager drawer
            "vq_jobs_list": [],
            "vq_jobs_loading": False,
            "vq_jobs_error": "",
            # vq feature/host overview (M1/A5)
            "vq_ov_host": "",  # e.g. "localhost · vq 0.15.x"
            "vq_ov_daemon": False,  # daemon healthy?
            "vq_ov_capacity": "",  # e.g. "max 12 CPU · 8 jobs"
            "vq_ov_load": "",  # e.g. "running 5 · pending 3 CPU"
            "vq_fetch_output_dir": "vq-fetched",
            # vq live monitor (M1/A3) — per-job status + log tail
            "vq_detail_job_id": "",  # job currently expanded in the panel
            "vq_detail_name": "",
            "vq_detail_state": "",
            "vq_detail_log": "",  # stdout/stderr tail text
            "vq_detail_loading": False,
            # v1.4 vq integration — submit + monitor
            "vq_submit_dialog": False,
            # Generated inputs stream a live checkpoint QVF by default so a
            # submitted job is immediately watchable from the Job Manager.
            "vq_submit_live_checkpoint": True,
            # Submit the job as a pending QVF *container* (structure +
            # job.spec, run_status="pending") instead of a generated .py
            # script, so the queue moves one file each way and the artifact
            # that comes back is the same file, settled. Needs vibeqc (the
            # QVF producer) importable next to vibeview; falls back to the
            # script path with a status message when it is not.
            "vq_submit_container": True,
            "vq_submit_container_available": _helpers._vibeqc_importable(),
            "vq_submit_output_dir": "",
            "vq_submitting": False,
            "vq_monitor_active": False,
            "vq_monitor_jobs": [],  # list of {id, label, state, progress}
            "vq_auto_open": True,
            # State slots for @ctrl.set one-shot actions (camera views,
            # geometry export).  Trame's Vue3 client requires these to be
            # initialised — without them, the JS setter crashes with
            # ``u.map is not a function``.  The values are never read.
            "camera_preset": None,
            "export_geometry": None,
            # File-picker v-model — must be initialised for @ctrl.set.
            # Lazy section loading for faster startup
            "memory_usage": "",
        }
    )


def initialize_scene(viewer: ViewerRuntime) -> None:

    # Overlay availability for the initial file (the reload path recomputes
    # this on every file switch).
    from vibeview.renderers.wannier import find_wannier_section as _find_wan

    viewer.server.state.has_wannier_centers = _find_wan(viewer.reader) is not None

    # ── Load structure eagerly (errors reported via status bar) ───
    viewer.structure_renderer, viewer.is_periodic = _helpers._build_structure_scene(
        viewer.reader,
        viewer.plotter,
        viewer.server.state,
        replication=_helpers._triple(viewer.viewer_state.replication),
        show_labels=bool(viewer.server.state.show_atom_labels),
        representation=str(getattr(viewer.server.state, "representation_style", "ball_and_stick")),
        cartoon_color_mode=str(getattr(viewer.server.state, "cartoon_color_mode", "chain")),
        residue_selection=str(getattr(viewer.server.state, "residue_selection", "") or ""),
    )

    # QA validation: check SCF convergence, energy sanity, etc.
    qa_warnings = _helpers._qa_validate(viewer.reader)

    if qa_warnings:
        viewer.server.state.qa_warnings = qa_warnings
        if not viewer.server.state.status_message:
            viewer.server.state.status_message = qa_warnings[0]

    view_isometric(viewer.plotter)

    _helpers._show_scene_grid(viewer.plotter, viewer.server.state)

    # ── Apply camera bookmark from viewer_defaults ────────────────
    _helpers._apply_camera(viewer.plotter, viewer.viewer_state.camera)

    _helpers._sync_orthographic_switch(viewer.plotter, viewer.server.state)

    # ── Cross-fade between two volumes (if configured) ───────────
    if viewer.viewer_state.crossfade_volumes:
        viewer.server.state.crossfade_active = True
        viewer.server.state.crossfade_blend = viewer.viewer_state.crossfade_blend
        va, vb = viewer.viewer_state.crossfade_volumes
        _helpers._rebuild_crossfade(
            viewer.reader,
            viewer.plotter,
            viewer.viewer_state,
            viewer.server.state,
            va,
            vb,
            viewer.viewer_state.crossfade_blend,
        )

    # Actor defaults are renderer implementation details; make the declared
    # application appearance authoritative on the initial scene too.
    _helpers._apply_scene_appearance(viewer.plotter, viewer.server.state)


def initialize_about(viewer: ViewerRuntime) -> None:

    # ── About info ──────────────────────────────────────────────────
    import platform

    import pyvista

    from vibeview.codenames import version_label

    viewer.server.state.about_version = f"vibe-view {version_label()}"

    viewer.server.state.about_system_info = (
        f"Python {platform.python_version()}\n"
        f"PyVista {pyvista.__version__}\n"
        f"OS {platform.system()} {platform.release()}"
    )

    try:
        import psutil

        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.1)
        viewer.server.state.about_system_info += (
            f"\nCPU: {cpu:.0f}% | RAM: {mem.used // (1024**3)}/{mem.total // (1024**3)} GB"
        )
    except ImportError:
        pass  # psutil not available
