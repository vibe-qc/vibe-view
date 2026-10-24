"""The page layout: the Vue tree ``create_app`` builds around the 3D view.

Part of the ``vibeview.app`` package; see its ``__init__`` for ``create_app``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyvista as pv

from vibeview.app.atom_drag import ATOM_DRAG_JS, CAMERA_CONTROLS, DRAG_CONTROLS
from vibeview.app.browser_capture import BROWSER_CAPTURE_JS, CLIENT_CAMERA_ARG
from vibeview.app.controls import _DIALOG_FOCUS_JS, _build_controls, _dialog_focus_restore
from vibeview.app.label_followers import (
    LABEL_FOLLOWER_EVENTS,
    LABEL_FOLLOWER_HANDLER,
    LABEL_FOLLOWER_HANDLERS,
    LABEL_FOLLOWER_JS,
    LABEL_FOLLOWER_SCENE_HANDLER,
    install_label_array_serializer,
)
from vibeview.app.scene import _ON_DOM_CHANGE_JS, _scene_hooks
from vibeview.app.silhouette import (
    SILHOUETTE_JS,
    SILHOUETTE_SCENE_HANDLER,
    publish_silhouette_targets,
)
from vibeview.figure_overlay_browser import FIGURE_OVERLAY_JS

if TYPE_CHECKING:
    from collections.abc import Callable



# The browser's WebGL version and renderer string, for the About dialog, so a
# user can place their machine in the client performance matrix (#114). Read
# once from a throwaway context, which is released at once.
_GRAPHICS_INFO_JS = r"""
typeof document !== 'undefined' && (function () {
  if (window.vvGraphicsInfo) return;
  function probe() {
    var canvas = document.createElement('canvas');
    var gl = canvas.getContext('webgl2'), version = 'WebGL2';
    if (!gl) { gl = canvas.getContext('webgl'); version = gl ? 'WebGL1' : 'no WebGL'; }
    if (!gl) return version;
    var ext = gl.getExtension('WEBGL_debug_renderer_info');
    var renderer = ext
      ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)
      : gl.getParameter(gl.RENDERER);
    var lose = gl.getExtension('WEBGL_lose_context');
    if (lose) lose.loseContext();
    return version + ', ' + renderer;
  }
  window.vvGraphicsInfo = probe();
  var attempts = 0;
  var timer = setInterval(function () {
    var state = window.trame && window.trame.state;
    if (state && state.set) {
      state.set('client_graphics', window.vvGraphicsInfo);
      clearInterval(timer);
    } else if (++attempts > 240) {
      clearInterval(timer);
    }
  }, 250);
})();
"""


def _retaining_view_updater(
    plotter: pv.Plotter,
    update: Callable[..., Any],
    before_push: Callable[[], Any] | None = None,
) -> Callable[..., Any]:
    """Keep the last sent scene alive until its replacement is serialized.

    trame-vtk identifies VTK objects by memory address. Releasing actors
    before rebuilding can reuse a mapper's address for a lookup table;
    vtk.js then updates the wrong instance and its viewport stops updating.
    Retaining the actor roots also retains their mapper/data/property graph.
    ``before_push`` runs ahead of every push: the layout publishes the
    silhouette pass's targets there (#112).
    """
    previous_actors = tuple(plotter.actors.values())

    def update_view(*args: Any, **kwargs: Any) -> Any:
        nonlocal previous_actors
        current_actors = tuple(plotter.actors.values())
        if before_push is not None:
            before_push()
        # trame-vtk rebuilds its serializer registry when its protocol
        # starts; make sure the label arrays go out with every push (#111).
        install_label_array_serializer()
        result = update(*args, **kwargs)
        previous_actors = current_actors
        return result

    return update_view


def build_layout(server: Any, ctrl: Any, plotter: pv.Plotter) -> None:
    """Build the page around ``plotter``: app bar, drawers, dialogs, the 3D
    view and the bottom panels. Moved out of ``create_app`` (#37-R6); it
    reads only the server, its controller and the plotter."""
    from trame.ui.vuetify3 import VAppLayout
    from trame.widgets import client, html
    from trame.widgets import vuetify3 as v
    from trame_vtk.widgets.vtk import VtkLocalView

    # Canonical Vuetify 3 layout: <v-app-bar>, drawers, and <v-main> are all
    # *siblings* of <v-app>. Vuetify then auto-sizes <v-main> to fill the
    # viewport between the drawers and the app bar, which is what makes the
    # 3D canvas resize cleanly with the browser window.
    with VAppLayout(server):
        # Theme CSS + light/dark switch are injected by _THEME_JS below (a
        # <style> element appended at runtime). An html.Style() block here
        # renders no <style> tag under trame's VAppLayout — which is why the
        # old .dark-mode override never took effect and the toggle did
        # nothing (design refresh 2026 theme pass).
        with v.VAppBar(
            color="primary",
            density="compact",
            elevation=2,
            v_if=("!presentation_mode",),
        ):
            v.VAppBarTitle("{{ header_title }}")
            # Streaming-checkpoint status of the active file (M4): pulses
            # while the producer is running, settles green/red at the end.
            # Announce only state transitions. The visible chip also carries
            # sequence/iteration/energy text that changes every checkpoint;
            # putting the live region on the whole chip would read every one.
            html.Span(
                "Job status: {{ live_run_status }}",
                v_if=("live_run_status && !chrome_hidden",),
                classes="d-sr-only",
                role="status",
                aria_live="polite",
                aria_atomic="true",
                __properties=[
                    "role",
                    ("aria_live", "aria-live"),
                    ("aria_atomic", "aria-atomic"),
                ],
            )
            with v.VChip(
                v_if=("live_run_status && !chrome_hidden",),
                size="small",
                variant="elevated",
                color=(
                    "live_run_status === 'pending' ? 'warning' : "
                    "(live_run_status === 'running' ? 'info' : "
                    "(live_run_status === 'converged' ? 'success' : 'error'))",
                ),
                classes="ml-3",
            ):
                v.VIcon(
                    "{{ live_run_status === 'pending' ? 'mdi-clock-outline' : "
                    "(live_run_status === 'running' ? 'mdi-progress-clock' : "
                    "(live_run_status === 'converged' ? 'mdi-check-circle' : "
                    "'mdi-alert-circle')) }}",
                    size="small",
                    classes="mr-1",
                )
                html.Span(
                    "{{ live_run_status }}"
                    "{{ live_checkpoint_text ? ' · ' + live_checkpoint_text : '' }}"
                )
            v.VSpacer()
            # Files dropdown (shown when multiple files are loaded)
            with v.VSelect(
                v_if=("file_names.length > 1",),
                v_model=("active_file_idx",),
                # Object items so the select emits the integer index, not
                # the filename string (int(filename) used to crash).
                items=("file_names.map((n, i) => ({ title: n, value: i }))",),
                label="Files",
                density="compact",
                hide_details=True,
                style="max-width: 180px; margin-right: 12px;",
                update_modelValue=(ctrl.switch_file, "[$event]"),
            ):
                pass
            # Camera presets — tooltips via native HTML title attribute.
            # Camera views collapse into one labelled menu — four unlabelled
            # arrow icons read as "some arrows" in a 24-icon bar (design
            # refresh 2026, item 1).
            with v.VMenu():
                with v.Template(v_slot_activator="{ props }"):
                    v.VBtn(
                        "Camera",
                        v_bind="props",
                        prepend_icon="mdi-video-switch-outline",
                        size="small",
                        variant="text",
                        title="Camera views",
                    )
                with v.VList(density="compact"):
                    for _label, _icon, _view in (
                        ("Isometric", "mdi-axis-arrow", "isometric"),
                        ("Top (XY plane)", "mdi-arrow-down-bold", "xy"),
                        ("Front (XZ plane)", "mdi-arrow-right-bold", "xz"),
                        ("Side (YZ plane)", "mdi-arrow-left-bold", "yz"),
                        ("Reset / fit all", "mdi-fit-to-page-outline", "reset"),
                    ):
                        v.VListItem(
                            title=_label,
                            prepend_icon=_icon,
                            click=(ctrl.camera_preset, f"['{_view}']"),
                        )
            # Symmetry detection
            v.VBtn(
                icon="mdi-axis-arrow-info",
                small=True,
                title="Detect point group",
                click=ctrl.detect_symmetry,
            )
            # Molecule builder. An icon + dialog rather than an inline text
            # field: the app bar already overflows its 29 buttons at 1500 px,
            # and an inline field gets flex-shrunk to a few pixels wide.
            v.VBtn(
                v_if=("builder_available || sketch_available",),
                icon="mdi-molecule",
                small=True,
                title="Build molecule: sketch, name or SMILES",
                click="builder_dialog = true",
            )
            # Screenshot
            v.VBtn(
                icon="mdi-camera",
                small=True,
                title="Export screenshot",
                click="screenshot_dialog = true",
            )
            v.VBtn(
                icon="mdi-presentation",
                small=True,
                title="Presentation mode (fullscreen slideshow)",
                click=ctrl.toggle_presentation,
            )
            # Video export (shown when animatable sections exist)
            v.VBtn(
                icon="mdi-video",
                small=True,
                title="Export video (turntable / trajectory / vibration / orbital)",
                click=ctrl.open_video_export_dialog,
                v_if=("has_animatable",),
            )
            # Diagnostics / info collapse into one labelled menu. This also
            # retires a duplicate: "Memory usage" and "Memory usage by
            # section" were two buttons firing the same ctrl.show_memory, and
            # the former shared the mdi-information icon with About (design
            # refresh 2026, item 1).
            with v.VMenu():
                with v.Template(v_slot_activator="{ props }"):
                    v.VBtn(
                        "Info",
                        v_bind="props",
                        prepend_icon="mdi-information-outline",
                        size="small",
                        variant="text",
                        title="Diagnostics, settings & help",
                    )
                with v.VList(density="compact"):
                    v.VListItem(
                        title="Keyboard shortcuts",
                        subtitle="? for help",
                        prepend_icon="mdi-keyboard",
                        click="shortcuts_help_dialog = true",
                    )
                    v.VListItem(
                        title="Memory usage by section",
                        prepend_icon="mdi-memory",
                        click=ctrl.show_memory,
                    )
                    v.VListItem(
                        title="Performance profile",
                        prepend_icon="mdi-chart-timeline-variant",
                        click=ctrl.show_profile,
                    )
                    v.VListItem(
                        title="Download manager",
                        subtitle="recent exports",
                        prepend_icon="mdi-download",
                        click="download_manager_dialog = true",
                    )
                    v.VDivider()
                    v.VListItem(
                        title="Settings",
                        prepend_icon="mdi-cog",
                        click="settings_dialog = true",
                    )
                    v.VListItem(
                        title="About vibe-view",
                        prepend_icon="mdi-information",
                        click="about_dialog = true",
                    )
            # Geometry / scene export — one labeled menu instead of eight
            # cryptic buttons (design refresh 2026, item 1). All entries
            # route through the same ctrl.export_geometry handler.
            with v.VMenu():
                with v.Template(v_slot_activator="{ props }"):
                    v.VBtn(
                        "Export",
                        v_bind="props",
                        prepend_icon="mdi-export-variant",
                        size="small",
                        variant="text",
                        title="Export geometry / scene…",
                    )
                with v.VList(density="compact"):
                    for _label, _icon, _fmt in (
                        ("Wavefront OBJ (mesh)", "mdi-cube-outline", "obj"),
                        ("glTF (mesh)", "mdi-rotate-3d", "gltf"),
                        ("XYZ coordinates", "mdi-axis-arrow", "xyz"),
                        ("CIF (crystal)", "mdi-diamond-stone", "cif"),
                        ("POV-Ray scene", "mdi-camera-iris", "pov"),
                        ("Blender scene script", "mdi-blender-software", "blend"),
                        ("SVG vector graphic", "mdi-svg", "svg"),
                        ("CML markup", "mdi-xml", "cml"),
                    ):
                        v.VListItem(
                            title=_label,
                            prepend_icon=_icon,
                            click=(ctrl.export_geometry, f"['{_fmt}']"),
                        )
                    v.VDivider()
                    # Render setting for server-side exports. It lives here
                    # rather than among the viewport toggles because a
                    # renderer pass shapes screenshots and video frames
                    # only — the live viewport is drawn client-side from
                    # serialized actors and can never show it.
                    v.VListItem(
                        title=(
                            "ssao_enabled ? 'Ambient occlusion: on "
                            "(screenshots & video)' : 'Ambient occlusion: "
                            "off (screenshots & video)'",
                        ),
                        prepend_icon="mdi-box-shadow",
                        click=ctrl.toggle_ssao,
                    )
            # Display toggles
            v.VBtn(
                icon="mdi-format-text",
                small=True,
                title="Toggle atom labels",
                click=ctrl.toggle_atom_labels,
            )
            v.VBtn(
                icon="mdi-ruler",
                small=True,
                title="List all bond distances & angles",
                click=ctrl.compute_bonds,
            )
            v.VBtn(
                icon="mdi-pencil",
                small=True,
                title="Edit mode",
                click=ctrl.toggle_edit_mode,
            )
            # Open another QVF file from disk
            v.VBtn(
                icon="mdi-folder-open",
                small=True,
                title="Open .qvf file from disk",
                click="load_file_dialog = true",
            )
            # vq Job Manager
            v.VBtn(
                icon="mdi-server-network",
                small=True,
                title="vq Job Manager — monitor & fetch jobs",
                click=ctrl.list_vq_jobs,
            )
            # Submit to vq
            v.VBtn(
                icon="mdi-cloud-upload",
                small=True,
                title="Submit to vq cluster",
                click="vq_submit_dialog = true",
                v_if=("calc_template",),
            )
            v.VBtn(
                icon=("dark_background ? 'mdi-weather-night' : 'mdi-weather-sunny'",),
                small=True,
                title="Toggle dark/light background",
                click=ctrl.toggle_background,
            )
            v.VBtn(
                icon=("raytrace_enabled ? 'mdi-lightbulb-on' : 'mdi-lightbulb'",),
                small=True,
                title="Toggle OSPRay ray tracing",
                click=ctrl.toggle_raytrace,
                v_if=("raytrace_available",),
            )
            # High-quality raytrace render (publication / magazine-cover)
            v.VBtn(
                icon="mdi-image-filter-hdr",
                small=True,
                title="High-quality raytrace render",
                click="hq_render_dialog = true",
                v_if=("raytrace_available",),
            )
            # Client-side download triggers. A bare <script> in a Vue
            # template is NOT executed by Vue, so the old setInterval
            # auto-clicker never ran — screenshot/export "succeeded"
            # server-side but nothing ever downloaded. ClientStateChange
            # runs JS whenever the watched state var changes; _vvDownload
            # builds a transient <a download> and clicks it. A Vue 3
            # template resolves every name not starting with "_" through
            # the component, so `document` is undefined there and the
            # inline handler this replaced, guarded on it, never ran
            # (#116); the page-script helper is reached as a bare
            # underscored global instead.
            client.Script(
                "window._vvDownload=function(d,n){"
                "var a=document.createElement('a');a.href=d;a.download=n;"
                "document.body.appendChild(a);a.click();a.remove();};"
                # The browser's camera for an export click, as the
                # [position, focal point, view up, parallel scale] that
                # sync_client_camera takes; null without a vtk view.
                "window._vvClientCamera=function(){"
                "var refs=window.trame&&window.trame.refs||{};"
                "for(var k in refs){try{var v=refs[k].$.provides.view;"
                "if(v&&v.renderer){var c=v.renderer.getActiveCamera();"
                "return [c.getPosition(),c.getFocalPoint(),c.getViewUp(),"
                "c.getParallelScale()];}}catch(e){}}return null;};"
            )
            _DL_JS = "typeof _vvDownload === 'function' && {var} && _vvDownload({var}, {name})"
            client.ClientStateChange(
                value="export_data",
                change=_DL_JS.format(var="export_data", name="export_filename"),
            )
            client.ClientStateChange(
                value="screenshot_data",
                change=_DL_JS.format(var="screenshot_data", name="'vibe-view-screenshot.png'"),
            )
            # File-input setup: the load dialog's hidden <input type=file>
            # mounts with the dialog, after the state change that opens it,
            # so _vvWireFileInput attaches its change listener a beat later,
            # once per element. The listener reads the file as base64 and
            # hands it to load_file_from_bytes with its name, as trigger
            # arguments: a trigger does not wait for a state flush, so
            # bytes sent through state arrived after the trigger. The Browse
            # button's _vvBrowseFile wires the input too and opens the
            # chooser. Like _vvDownload these are page-script helpers: the
            # inline handlers they replace used `setTimeout` and `document`,
            # which a Vue 3 template cannot see, so opening the dialog
            # threw, Browse opened nothing and no file ever loaded (#117).
            client.Script(
                "(function(){"
                "function read(){"
                "var f=this.files[0];if(!f)return;"
                "var r=new FileReader();"
                "r.onload=function(e){"
                "window.trame.trigger('load_file_from_bytes',"
                "[e.target.result.split(',')[1],f.name])"
                "};"
                "r.readAsDataURL(f);"
                "this.value=''"
                "}"
                "function wire(){"
                "var el=document.getElementById('qvf-file-input');"
                "if(el&&!el._vq_wired){el._vq_wired=1;el.addEventListener('change',read);}"
                "return el;"
                "}"
                "window._vvWireFileInput=function(){setTimeout(wire,50);};"
                "window._vvBrowseFile=function(){var el=wire();if(el)el.click();};"
                "})()"
            )
            client.ClientStateChange(
                value="load_file_dialog",
                change=(
                    "typeof _vvWireFileInput === 'function' && load_file_dialog"
                    " && _vvWireFileInput()"
                ),
            )

            # State-driven dialogs have no Vuetify activator slot, so Vuetify
            # cannot return focus when their focused close button unmounts.
            # Capture a keyed origin when focus first enters each modal, then
            # restore it after that dialog leaves the DOM. Transient menu
            # items retain their stable activator, while stacked dialogs keep
            # focus inside the modal that remains visible.
            client.Script(_DIALOG_FOCUS_JS)

            # Keyboard shortcut handler via client-side JS.
            # Listens for global keydown events and triggers server-side
            # actions via trame.trigger().  Bound once (window._vibe_keyboard_bound).
            _SHORTCUT_JS = (
                "typeof document !== 'undefined' && (function(){"
                "if(window._vibe_keyboard_bound)return;"
                "window._vibe_keyboard_bound=1;"
                "document.addEventListener('keydown',function(e){"
                "var t=window.trame;if(!t||!t.trigger||!t.state||e.defaultPrevented)return;"
                "var k=e.key.toLowerCase();"
                "var command=e.ctrlKey||e.metaKey;"
                # Command palette: Ctrl/Cmd+K opens from anywhere (before the
                # input-field guard below), design refresh 2026.
                "if(k==='k'&&command&&!e.altKey){e.preventDefault();"
                "t.state.set('palette_open',true);t.state.flush();return;}"
                "var target=e.target;"
                # The persistent presentation overlay can own Vuetify's top
                # overlay slot even while the palette has keyboard focus.
                # Close the focused palette explicitly before the input guard.
                "if(k==='escape'&&!e.repeat&&target&&target.closest&&"
                "target.closest('[role=dialog][aria-label=\"Command palette\"]')){"
                "e.preventDefault();t.state.set('palette_open',false);"
                "t.state.flush();return;}"
                "if(target&&(target.tagName==='INPUT'||target.tagName==='TEXTAREA'||"
                "target.tagName==='SELECT'||target.isContentEditable||"
                "(target.closest&&target.closest('[contenteditable=true]'))))return;"
                # A modal owns its keyboard interaction, especially Escape.
                # Do not run viewer actions behind it.
                "if(document.querySelector('[role=dialog]'))return;"
                "if(k==='escape'){if(e.repeat)return;"
                "if(t.state.get('presentation_mode')){e.preventDefault();"
                "t.trigger('toggle_presentation');return;}"
                "if(t.state.get('edit_mode')){e.preventDefault();"
                "t.trigger('toggle_edit_mode');return;}"
                "if(t.state.get('measure_mode')){e.preventDefault();"
                "t.trigger('toggle_measure_mode',[false]);return;}return;}"
                "if((k==='arrowleft'||k==='arrowright')&&"
                "t.state.get('presentation_mode')&&!command&&!e.altKey){"
                "e.preventDefault();t.trigger(k==='arrowleft' ? "
                "'presentation_prev' : 'presentation_next');return;}"
                # Editing is deliberately unavailable while its controls are
                # hidden by presentation mode. Keep undo/delete from mutating
                # geometry invisibly, and do not let `e` re-enter edit mode.
                "if(t.state.get('presentation_mode')&&(k==='e'||k==='delete'||"
                "k==='backspace'||(command&&(k==='z'||k==='y')))){"
                "e.preventDefault();return;}"
                "if(command&&!e.altKey&&k==='z'){e.preventDefault();"
                "t.trigger(e.shiftKey?'edit_redo':'edit_undo');return;}"
                "if(command&&!e.altKey&&k==='y'){e.preventDefault();"
                "t.trigger('edit_redo');return;}"
                "if(command||e.altKey||e.repeat)return;"
                "if(k==='?'){e.preventDefault();t.state.set('shortcuts_help_dialog',true);"
                "t.state.flush();return;}"
                "if(k==='e'){t.trigger('toggle_edit_mode');return;}"
                "if(k==='m'){t.trigger('toggle_measure_mode');return;}"
                "if(k==='r'){t.trigger('camera_preset',['reset']);return;}"
                "if(k==='s'){t.trigger('save_screenshot');return;}"
                "if(k==='v'){t.state.set('video_export_dialog',true);t.state.flush();return;}"
                "if(k==='p'){t.trigger('toggle_presentation');return;}"
                # Playback, the terminal's keys: Space plays or pauses the
                # trajectory or the vibration on show, [ and ] step a frame or
                # a phase step, < and > change the normal mode. A focused
                # button keeps Space, which activates it natively.
                "var traj=t.state.get('trajectory_n_frames')>0;"
                "var vib=t.state.get('vibration_n_modes')>0;"
                "if(e.key===' '){if(target&&target.tagName==='BUTTON')return;"
                "if(traj){e.preventDefault();t.trigger('trajectory_play_toggle');return;}"
                "if(vib){e.preventDefault();t.trigger('vibration_play_toggle');return;}return;}"
                "if(k==='['||k===']'){var d=k===']'?1:-1;"
                "if(traj){e.preventDefault();t.trigger('trajectory_step',[d]);return;}"
                "if(vib){e.preventDefault();t.trigger('vibration_step_phase',[d]);return;}return;}"
                "if((k==='<'||k==='>')&&vib){e.preventDefault();"
                "t.trigger('vibration_step_mode',[k==='>'?1:-1]);return;}"
                "if(k==='delete'||k==='backspace'){e.preventDefault();"
                "t.trigger('edit_delete_selected');return;}"
                "});"
                "})()"
            )
            # Install via a global script tag: a ClientStateChange handler
            # only runs when its state CHANGES, and nothing ever changes
            # these states before the listener exists — so the listener was
            # never installed and every keyboard shortcut was dead in the
            # browser (the handler itself reads window.trame lazily).
            client.Script(_SHORTCUT_JS)

            # Side panels are user-resizable: a slim col-resize separator on
            # the inner edge live-resizes during a drag, then commits the width
            # to trame state so Vuetify reflows the main layout. The same
            # separator is keyboard-operable and exposes its rendered width as
            # an ARIA range value. A 1s re-attach interval survives either
            # drawer's v_if unmount/remount cycle (design refresh 2026, item 2).
            _PANEL_RESIZE_JS = (
                "typeof document !== 'undefined' && (function(){"
                "if(window._vibe_panel_resize)return;window._vibe_panel_resize=1;"
                + _ON_DOM_CHANGE_JS +
                "var observers={};"
                "var style=document.createElement('style');"
                "style.textContent='.vv-panel-separator:focus-visible{'"
                "+'outline:3px solid #82b1ff;outline-offset:-3px;'"
                "+'background:rgba(130,177,255,.35)!important;}';"
                "document.head.appendChild(style);"
                "function clamp(value,min,max){return Math.min(max,Math.max(min,value));}"
                "function commit(key,value){var t=window.trame;"
                "if(t&&t.state){t.state.set(key,value);t.state.flush();}}"
                "function attach(id,side,key,label,fallback){"
                "var d=document.getElementById(id);"
                "if(!d||d._vvresz)return;d._vvresz=1;"
                "var h=document.createElement('div');"
                "h.id=id+'-separator';h.className='vv-panel-separator';"
                "h.style.cssText='position:absolute;top:0;bottom:0;'"
                "+(side==='left'?'right':'left')+':0;width:6px;cursor:col-resize;z-index:1007;';"
                "h.tabIndex=0;h.setAttribute('role','separator');"
                "h.setAttribute('aria-label',label);"
                "h.setAttribute('aria-orientation','vertical');"
                "h.setAttribute('aria-controls',id);"
                "h.setAttribute('aria-valuemin','180');"
                "h.setAttribute('aria-valuemax','640');"
                "h.title=label+' (use Left and Right arrow keys)';"
                "d.appendChild(h);"
                "function read(){var width=Math.round("
                "d.getBoundingClientRect().width||d.offsetWidth);"
                "if(!width){var t=window.trame;"
                "width=Number(t&&t.state&&t.state.get(key))||fallback;}"
                "return clamp(width,180,640);}"
                "function announce(width){h.setAttribute('aria-valuenow',String(width));"
                "h.setAttribute('aria-valuetext',String(width)+' pixels');}"
                "function apply(width,save){width=clamp(Math.round(width),180,640);"
                "d.style.width=width+'px';announce(width);"
                "if(save)commit(key,width);return width;}"
                "var initial=read();announce(initial);"
                "h._vvrestore=initial>180?initial:fallback;"
                "h.addEventListener('mousedown',function(e){"
                "if(e.button!==0)return;e.preventDefault();h.focus({preventScroll:true});"
                "var sx=e.clientX,sw=read(),last=sw;"
                "function mm(ev){last=apply("
                "sw+(side==='left'?ev.clientX-sx:sx-ev.clientX),false);}"
                "function mu(){window.removeEventListener('mousemove',mm);"
                "window.removeEventListener('mouseup',mu);"
                "commit(key,last);}"
                "window.addEventListener('mousemove',mm);"
                "window.addEventListener('mouseup',mu);});"
                "h.addEventListener('keydown',function(e){"
                "var step=e.shiftKey?50:10,current=read(),next=null;"
                "if(e.key==='ArrowLeft')next=current+(side==='left'?-step:step);"
                "else if(e.key==='ArrowRight')next=current+(side==='left'?step:-step);"
                "else if(e.key==='Home')next=180;"
                "else if(e.key==='End')next=640;"
                "else if(e.key==='Enter'){if(current<=180){next=h._vvrestore||fallback;}"
                "else{h._vvrestore=current;next=180;}}else{return;}"
                "if(e.key!=='Enter'&&next<=180&&current>180)h._vvrestore=current;"
                "e.preventDefault();apply(next,true);});"
                "if(observers[id])observers[id].disconnect();"
                "if(typeof ResizeObserver!=='undefined'){"
                "observers[id]=new ResizeObserver(function(){"
                "var width=Math.round(d.getBoundingClientRect().width||d.offsetWidth);"
                "if(width)announce(clamp(width,180,640));});"
                "observers[id].observe(d);}}"
                "function attachAll(){"
                "attach('vv-left-panel','left','left_panel_width','Result sections',280);"
                "attach('vv-right-panel','right','right_panel_width','Section controls',300);}"
                "window._vvOnDomChange(attachAll);"
                "})()"
            )
            client.Script(_PANEL_RESIZE_JS)

            # Bridge clicks on the orbital energy diagram (in a sandboxed
            # iframe) back to the server: relay the posted render key into
            # mo_click_request state, with a nonce so re-clicking the same
            # level still fires the @state.change watcher (design refresh
            # 2026: click a level to render that orbital).
            _MO_CLICK_JS = (
                "typeof window !== 'undefined' && (function(){"
                "if(window._vibe_mo_click)return;window._vibe_mo_click=1;"
                "window.addEventListener('message',function(e){"
                "var d=e&&e.data;"
                "if(!d||d.type!=='vibeview-mo-click'||d.key==null)return;"
                "var t=window.trame;if(!t||!t.state)return;"
                "t.state.set('mo_click_request',String(d.key)+'@'+Date.now());"
                "t.state.flush();});})()"
            )
            client.Script(_MO_CLICK_JS)

            # Horizontal separator between the 3D viewport and bottom panel.
            # The panel keeps its native resize:vertical corner grip; the bar
            # adds mouse and keyboard resizing, with a ResizeObserver keeping
            # its ARIA value synchronized with the rendered height. A
            # full-window overlay during drag stops the VTK canvas from
            # receiving the move and rotating the molecule (reported
            # 2026-07-16). Re-attaches on the 1s tick because both elements are
            # v_if-gated on selected_section.
            _BOTTOM_RESIZE_JS = (
                "typeof document !== 'undefined' && (function(){"
                "if(window._vibe_hsplit)return;window._vibe_hsplit=1;"
                + _ON_DOM_CHANGE_JS +
                "var observer=null,current=null;"
                "window.addEventListener('resize',function(){"
                "if(current&&current.splitter.isConnected&&current.panel.isConnected)"
                "current.refresh();});"
                "function maxHeight(){var vh=window.innerHeight||"
                "document.documentElement.clientHeight||2000;"
                "return Math.max(80,Math.floor(vh*0.85));}"
                "function attach(){"
                "var s=document.getElementById('vv-hsplit'),"
                "p=document.getElementById('vv-bottom-panel');"
                "if(!s||!p||s._vvhs)return;s._vvhs=1;"
                "s.classList.add('vv-panel-separator');s.tabIndex=0;"
                "s.setAttribute('role','separator');"
                "s.setAttribute('aria-label','Result details');"
                "s.setAttribute('aria-orientation','horizontal');"
                "s.setAttribute('aria-controls','vv-bottom-panel');"
                "s.setAttribute('aria-valuemin','80');"
                "s.title='Result details (use Up and Down arrow keys)';"
                "function read(){return Math.round(p.getBoundingClientRect().height||"
                "p.offsetHeight||80);}"
                "function announce(height){var maximum=maxHeight();"
                "height=Math.min(maximum,Math.max(80,Math.round(height)));"
                "s.setAttribute('aria-valuemax',String(maximum));"
                "s.setAttribute('aria-valuenow',String(height));"
                "s.setAttribute('aria-valuetext',String(height)+' pixels');}"
                "function apply(height){var maximum=maxHeight();"
                "height=Math.min(maximum,Math.max(80,Math.round(height)));"
                "p.style.height=height+'px';announce(height);return height;}"
                "var initial=read();announce(initial);"
                "s._vvrestore=initial>80?initial:Math.round(maxHeight()*0.35);"
                "s.addEventListener('mousedown',function(e){"
                "if(e.button!==0)return;e.preventDefault();s.focus({preventScroll:true});"
                "var sy=e.clientY,sh=read();"
                "var ov=document.createElement('div');"
                "ov.style.cssText='position:fixed;inset:0;z-index:3000;cursor:row-resize;';"
                "document.body.appendChild(ov);"
                "function mm(ev){apply(sh+(sy-ev.clientY));}"
                "function mu(){document.removeEventListener('mousemove',mm);"
                "document.removeEventListener('mouseup',mu);ov.remove();}"
                "document.addEventListener('mousemove',mm);"
                "document.addEventListener('mouseup',mu);});"
                "s.addEventListener('keydown',function(e){"
                "var step=e.shiftKey?50:10,current=read(),next=null;"
                "if(e.key==='ArrowUp')next=current+step;"
                "else if(e.key==='ArrowDown')next=current-step;"
                "else if(e.key==='Home')next=80;"
                "else if(e.key==='End')next=maxHeight();"
                "else if(e.key==='Enter'){if(current<=80){next=s._vvrestore||"
                "Math.round(maxHeight()*0.35);}else{s._vvrestore=current;next=80;}}"
                "else{return;}"
                "if(e.key!=='Enter'&&next<=80&&current>80)s._vvrestore=current;"
                "e.preventDefault();apply(next);});"
                "if(observer)observer.disconnect();"
                "if(typeof ResizeObserver!=='undefined'){observer=new ResizeObserver(function(){"
                "announce(read());});observer.observe(p);}"
                "current={splitter:s,panel:p,refresh:function(){announce(read());}};}"
                "window._vvOnDomChange(attach);})()"
            )
            client.Script(_BOTTOM_RESIZE_JS)

            # Instant toolbar tooltips. The app-bar buttons carry a title=
            # attribute, but the native title tooltip is unreliable/slow in
            # the Electron shell (reported 2026-07-16: no tooltip on hover).
            # Move each title into data-vtip (so the native one never fires)
            # and show a lightweight custom tooltip on hover instead.
            _TOOLBAR_TIPS_JS = (
                "typeof document !== 'undefined' && (function(){"
                "if(window._vibe_tips)return;window._vibe_tips=1;"
                "var tip=document.createElement('div');"
                "tip.style.cssText='position:fixed;z-index:3001;"
                "background:rgba(33,33,33,.96);color:#fff;padding:4px 8px;"
                "border-radius:4px;font-size:12px;line-height:1.3;"
                "pointer-events:none;opacity:0;transition:opacity .08s;"
                "white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,.4);';"
                "document.body.appendChild(tip);"
                "var sel='.v-app-bar [title],.v-app-bar [data-vtip]';"
                "function show(el){var t=el.getAttribute('title');"
                "if(t){"
                "if(!el.hasAttribute('aria-label')&&!el.hasAttribute('aria-labelledby')){"
                "el.setAttribute('aria-label',t);}"
                "el.setAttribute('data-vtip',t);el.removeAttribute('title');}"
                "t=el.getAttribute('data-vtip');if(!t)return;"
                "tip.textContent=t;var r=el.getBoundingClientRect();"
                "var vw=window.innerWidth||document.documentElement.clientWidth||2000;"
                "var half=tip.offsetWidth/2;"
                "var cx=Math.max(4+half,Math.min(r.left+r.width/2,vw-4-half));"
                "tip.style.left=cx+'px';tip.style.top=(r.bottom+6)+'px';"
                "tip.style.transform='translateX(-50%)';tip.style.opacity='1';}"
                "document.addEventListener('mouseover',function(e){"
                "var el=e.target.closest&&e.target.closest(sel);if(el)show(el);});"
                "document.addEventListener('mouseout',function(e){"
                "var el=e.target.closest&&e.target.closest(sel);"
                "if(el)tip.style.opacity='0';});})()"
            )
            client.Script(_TOOLBAR_TIPS_JS)

            # Apply the Vuetify theme class from ui_dark_mode. The toggle used
            # to only flip state (the old .dark-mode class was never bound),
            # so the chrome never actually changed; this swaps
            # v-theme--light / v-theme--dark on <div class="v-application">,
            # which drives every component's surface + accent (design refresh
            # 2026 theme pass). Polls so it applies on first paint and tracks
            # later toggles without a per-change trame binding.
            _THEME_JS = (
                "typeof document !== 'undefined' && (function(){"
                "if(window._vibe_theme)return;window._vibe_theme=1;"
                + _ON_DOM_CHANGE_JS +
                # Inject the theme stylesheet once: an indigo accent over both
                # Vuetify themes (.v-theme--light/.v-theme--dark, two classes
                # so it beats Vuetify's single-class rule), a deep-navy dark
                # surface matching the 3D viewport, and denser card headers.
                # Vars are RGB triplets (Vuetify wraps them). The dark theme
                # lightens the accent and darkens its on-colour: the light
                # theme's #4361EE reads at 3.4:1 on the dark surface, below
                # WCAG AA for text; #6B86FF reads at 5.3:1 and its dark text
                # at 6.5:1 (#68 theme pass; pinned by test_publication_mode).
                "var css=document.createElement('style');"
                "css.textContent="
                "'.v-application.v-theme--light,.v-application.v-theme--dark{"
                "--v-theme-primary:67,97,238;--v-theme-on-primary:255,255,255;}'"
                "+'.v-application.v-theme--dark{--v-theme-primary:107,134,255;"
                "--v-theme-on-primary:15,15,35;--v-theme-background:15,15,35;"
                "--v-theme-surface:26,26,46;--v-theme-on-surface:228,230,245;"
                "--v-theme-on-background:228,230,245;"
                "--v-theme-surface-variant:40,42,66;}'"
                "+'.v-application .v-card-title{font-weight:600;"
                "letter-spacing:.01em;font-size:1.02rem;}'"
                "+'.v-application .v-card-subtitle{opacity:.75;}'"
                "+'.vv-presentation-panel{flex:1 1 auto!important;"
                "min-height:0!important;height:100%!important;"
                "max-height:none!important;resize:none!important;}'"
                "+'.vv-presentation-overlay-content{pointer-events:none!important;}'"
                # The scrubber's filmstrip: thumbnails as buttons, the current
                # one outlined in the accent; a drag across them seeks.
                "+'.vv-filmstrip{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px;"
                "user-select:none;touch-action:none;}'"
                "+'.vv-thumb{padding:0;border:2px solid transparent;border-radius:4px;"
                "background:none;line-height:0;cursor:pointer;}'"
                "+'.vv-thumb img{display:block;width:72px;height:48px;border-radius:2px;"
                "pointer-events:none;}'"
                "+'.vv-thumb-active{border-color:rgb(var(--v-theme-primary));}'"
                "+'.vv-thumb:focus-visible{outline:2px solid rgb(var(--v-theme-primary));"
                "outline-offset:1px;}'"
                # The structure library's pictures: the pick's preview and
                # a small one inside each favourite and recent chip.
                "+'.vv-library-preview{display:block;width:96px;height:64px;"
                "margin-top:6px;border-radius:4px;}'"
                "+'.vv-chip-thumb{width:30px;height:20px;border-radius:2px;"
                "margin-right:4px;vertical-align:middle;}';"
                "document.head.appendChild(css);"
                # The filmstrip's drag: with the button held, pointer events
                # stay routed to the thumbnail that was pressed, so the strip
                # looks under the pointer itself and seeks once per thumbnail.
                "window._vvScrub=function(e,seek){"
                "var el=document.elementFromPoint(e.clientX,e.clientY);"
                "var b=el&&el.closest?el.closest('.vv-thumb'):null;if(!b)return;"
                "var i=b.getAttribute('data-index');"
                "if(i===null||e.currentTarget._vvLast===i)return;"
                "e.currentTarget._vvLast=i;seek(Number(i));};"
                # Swap the theme from ui_dark_mode on first paint and on later
                # toggles (the toggle used to only flip state). Vuetify stamps
                # a v-theme--light/dark class on EVERY themed component, not
                # just the root, so flip the class on all of them. Components
                # that (re)render later are re-themed on the DOM change, and a
                # toggle arrives through a watch on ui_dark_mode (#37-55).
                "function apply(){var t=window.trame;"
                "if(!t||!t.state)return;"
                "var dark=!!t.state.get('ui_dark_mode');"
                "var want=dark?'v-theme--dark':'v-theme--light';"
                "var other=dark?'v-theme--light':'v-theme--dark';"
                "document.querySelectorAll('.'+other).forEach(function(el){"
                "el.classList.remove(other);el.classList.add(want);});}"
                "window._vvOnDomChange(apply);"
                "(function watch(tries){var t=window.trame;"
                "if(t&&t.state&&t.state.watch){t.state.watch(['ui_dark_mode'],apply);return;}"
                "if(tries>0)setTimeout(function(){watch(tries-1);},100);})(100);})()"
            )
            client.Script(_THEME_JS)

            v.VChip(
                "{{ status_message }}",
                color="warning",
                small=True,
                v_if=("status_message && !chrome_hidden",),
                role="status",
                aria_live="polite",
                aria_atomic="true",
                __properties=[
                    "role",
                    ("aria_live", "aria-live"),
                    ("aria_atomic", "aria-atomic"),
                ],
            )
            # Auto-save session indicator
            v.VChip(
                "Session: {{ session_path.split('/').pop() }}",
                v_if=("session_path && !chrome_hidden",),
                size="x-small",
                color="success",
                variant="outlined",
                classes="ml-2",
            )

        # ── File-open dialog ─────────────────────────────────────────────
        # Command palette (Ctrl/Cmd+K): fuzzy-search quick actions
        # (design refresh 2026).
        with v.VDialog(
            v_model=("palette_open",),
            max_width="520px",
            scrim=True,
            afterLeave=_dialog_focus_restore("Command palette"),
            aria_label="Command palette",
            __properties=[("aria_label", "aria-label")],
        ), v.VCard():
            v.VTextField(
                v_model=("palette_query",),
                placeholder="Type a command… (camera, view, HOMO, screenshot)",
                prepend_inner_icon="mdi-console-line",
                variant="solo",
                density="compact",
                hide_details=True,
                clearable=True,
                autofocus=True,
            )
            with v.VList(
                density="compact",
                nav=True,
                max_height="360px",
                style="overflow-y:auto",
            ):
                v.VListItem(
                    v_for=(
                        "a in palette_actions.filter(x => !palette_query || "
                        "(x.title + ' ' + x.sub).toLowerCase()"
                        ".includes(palette_query.toLowerCase()))",
                    ),
                    key=("a.id",),
                    title=("a.title",),
                    subtitle=("a.sub",),
                    prepend_icon=("a.icon",),
                    click=(ctrl.run_palette_action, "[a.id]"),
                )

        with v.VDialog(
            v_model=("builder_dialog",),
            max_width="420px",
            afterLeave=_dialog_focus_restore("Build molecule"),
            aria_label="Build molecule",
            __properties=[("aria_label", "aria-label")],
        ), v.VCard():
            v.VCardTitle("Build molecule")
            v.VCardText(
                "Generates 3D coordinates and loads them as a new "
                "structure. Either way the geometry is a starting point, "
                "not an optimized one."
            )
            with v.VCardText():
                # Sketching in vibe-sketch is the way to draw a molecule
                # (#77). The button stays when the [sketch] extra is missing:
                # clicking it puts the install command in the status bar.
                v.VBtn(
                    "Sketch in vibe-sketch",
                    prepend_icon="mdi-draw",
                    block=True,
                    variant="tonal",
                    color="primary",
                    click=ctrl.sketch_molecule,
                )
                v.VCardText(
                    "Draw the molecule, then choose Send to vibe-view in the "
                    "sketcher. It opens here as a new structure.",
                    classes="text-caption text-medium-emphasis pa-0 pt-2",
                    v_if=("sketch_available",),
                )
                v.VCardText(
                    "Sketching needs vibe-sketch, the optional [sketch] extra. "
                    "Run vibe-view doctor for the install command.",
                    classes="text-caption text-medium-emphasis pa-0 pt-2",
                    v_if=("!sketch_available",),
                )
                v.VDivider(classes="my-3")
                # `keyup__enter=` is silently dropped: trame only emits
                # listeners whose name it was told about, and no widget
                # declares that key. The modifier has to be registered
                # explicitly through __events for Enter to reach Python.
                v.VTextField(
                    v_if=("builder_available",),
                    v_model=("builder_name",),
                    label="Molecule name",
                    placeholder="pyridine",
                    prepend_inner_icon="mdi-molecule",
                    density="compact",
                    hide_details=True,
                    clearable=True,
                    autofocus=True,
                    __events=[("keyup_enter", "keyup.enter")],
                    keyup_enter=ctrl.build_molecule,
                )
                v.VDivider(classes="my-3", v_if=("builder_available",))
                # SMILES reaches molecules the curated name database does
                # not have. Enter is registered the same way -- trame only
                # emits listeners it was told about, so keyup__enter alone
                # is silently dropped.
                v.VTextField(
                    v_model=("builder_smiles",),
                    label="or SMILES",
                    placeholder="c1ccccc1O",
                    prepend_inner_icon="mdi-graph-outline",
                    density="compact",
                    hide_details=True,
                    clearable=True,
                    __events=[("keyup_enter", "keyup.enter")],
                    keyup_enter=ctrl.build_from_smiles,
                )
                v.VCardText(
                    "SMILES needs an optional extra. Run vibe-view doctor "
                    "for the install command.",
                    classes="text-caption text-medium-emphasis pa-0 pt-2",
                    v_if=("!smiles_available",),
                )
            with v.VCardActions():
                v.VSpacer()
                v.VBtn("Cancel", variant="text", click="builder_dialog = false")
                v.VBtn(
                    "Build",
                    v_if=("builder_available",),
                    variant="tonal",
                    click=ctrl.build_molecule,
                )
                v.VBtn(
                    "Build SMILES",
                    variant="tonal",
                    click=ctrl.build_from_smiles,
                    disabled=("!builder_smiles",),
                )

        with v.VDialog(
            v_model=("load_file_dialog",),
            max_width="400px",
            afterLeave=_dialog_focus_restore("Open chemistry files"),
            aria_label="Open chemistry files",
            __properties=[("aria_label", "aria-label")],
        ), v.VCard():
            v.VCardTitle("Open chemistry file(s)")
            v.VCardText(
                "Choose a QVF, structure, cube or TREXIO HDF5 file, or enter a "
                "server-side path / directory / glob below."
            )
            # Browser-native file picker via hidden HTML input.
            # VFileInput's content serialisation varies across Trame
            # versions; a raw <input type=file> with FileReader is
            # reliable everywhere.  _vvWireFileInput's change listener
            # reads the file as base64 and hands it to the server.
            html.Input(
                id="qvf-file-input",
                type="file",
                accept=".qvf,.xyz,.cif,.cube,.pdb,.mol2,.gjf,.com,.gro,.sdf,.mol,.trexio,.h5,.hdf5",
                style="display: none;",
            )
            v.VBtn(
                "Browse chemistry file\u2026",
                prepend_icon="mdi-file-upload",
                block=True,
                variant="tonal",
                click="typeof _vvBrowseFile === 'function' && _vvBrowseFile()",
            )
            v.VCardText("Server-side path:", classes="text-caption mt-2")
            v.VTextField(
                v_model=("open_path",),
                label="Path to chemistry file or directory",
                placeholder="~/jobs/  or  /path/to/calc.qvf",
                density="compact",
                hide_details=True,
                clearable=True,
                __events=[("keyup_enter", "keyup.enter")],
                keyup_enter=ctrl.open_path,
            )
            v.VCheckbox(
                v_model=("open_recursive",),
                label="Search subdirectories recursively",
                density="compact",
                hide_details=True,
            )
            with v.VCardActions():
                v.VSpacer()
                v.VBtn("Close", click=ctrl.close_load_dialog)
                v.VBtn(
                    "Open",
                    color="primary",
                    variant="flat",
                    click=ctrl.open_path,
                )

        # ── vq Job Manager (M1/A1) — a real, always-available panel ─────
        # Temporary right drawer so it overlays without disturbing the
        # permanent sections/controls drawers.
        with v.VNavigationDrawer(
            v_model=("vq_panel_open",),
            v_if=("!presentation_mode",),
            location="right",
            temporary=True,
            width=440,
            aria_label="vq Job Manager",
            __properties=[("aria_label", "aria-label")],
        ), html.Div(classes="pa-3"):
            with v.VRow(dense=True, align="center", classes="mb-1"):
                v.VIcon("mdi-server-network", classes="mr-2")
                html.Span("vq Job Manager", classes="text-h6")
                v.VSpacer()
                v.VBtn(
                    icon="mdi-refresh",
                    size="small",
                    variant="text",
                    title="Refresh now",
                    click=ctrl.vq_refresh_jobs,
                )
                v.VBtn(
                    icon="mdi-close",
                    size="small",
                    variant="text",
                    aria_label="Close vq Job Manager",
                    __properties=[("aria_label", "aria-label")],
                    click="vq_panel_open = false",
                )
            # ── Queue overview strip (A5): host · vq version · daemon
            # health · capacity · current load ──
            with html.Div(
                v_if=("vq_ov_host",),
                classes="d-flex align-center flex-wrap mb-1 text-caption",
                style="gap: 6px; color: var(--v-theme-on-surface);",
            ):
                v.VIcon(
                    "mdi-circle",
                    size="10",
                    color=("vq_ov_daemon ? 'success' : 'error'",),
                    title=("vq_ov_daemon ? 'daemon healthy' : 'daemon down'",),
                )
                html.Span("{{ vq_ov_host }}", classes="font-weight-medium")
                v.VChip(
                    "{{ vq_ov_capacity }}",
                    v_if=("vq_ov_capacity",),
                    size="x-small",
                    label=True,
                    variant="tonal",
                )
                v.VChip(
                    "{{ vq_ov_load }}",
                    v_if=("vq_ov_load",),
                    size="x-small",
                    label=True,
                    variant="tonal",
                    color="info",
                )
            with v.VRow(dense=True, align="center"):
                v.VSwitch(
                    v_model=("vq_monitor_active",),
                    label="Live monitor (all states, auto-refresh 5s)",
                    density="compact",
                    color="primary",
                    hide_details=True,
                )
            v.VTextField(
                v_model=("vq_fetch_output_dir",),
                label="Fetch results into",
                density="compact",
                hide_details=True,
                prepend_inner_icon="mdi-folder-download",
                classes="mb-2",
            )
            v.VDivider()
            v.VProgressLinear(
                v_if=("vq_jobs_loading",), indeterminate=True, color="primary"
            )
            # Error banner (vq missing / queue error)
            v.VAlert(
                "{{ vq_jobs_error }}",
                v_if=("vq_jobs_error",),
                type="warning",
                density="compact",
                variant="tonal",
                classes="my-2 text-caption",
            )
            # Job rows — explicit flex layout (avoids fragile v-slots)
            # ── Live status + log tail for the expanded job (A3) ──
            # Placed ABOVE the list so it's visible the moment a job is
            # clicked; the list scrolls below it.
            with v.VCard(
                v_if=("vq_detail_job_id",),
                variant="tonal",
                classes="mb-2",
            ):
                with v.VCardTitle(classes="d-flex align-center text-body-2"):
                    html.Span("{{ vq_detail_name }}")
                    v.VChip(
                        "{{ vq_detail_state }}",
                        size="x-small",
                        label=True,
                        classes="ml-2",
                    )
                    v.VSpacer()
                    v.VBtn(
                        icon="mdi-close",
                        size="x-small",
                        variant="text",
                        aria_label="Close job details",
                        __properties=[("aria_label", "aria-label")],
                        click="vq_detail_job_id = ''",
                    )
                html.Pre(
                    "{{ vq_detail_log }}",
                    style=(
                        "max-height: 24vh; overflow: auto; margin: 0 12px 12px;"
                        "font-size: 0.68rem; white-space: pre-wrap;"
                        "font-family: 'JetBrains Mono', monospace;"
                        "color: var(--v-theme-on-surface);"
                    ),
                )

            with html.Div(
                v_if=("!vq_jobs_loading && !vq_jobs_error",),
                style="overflow-y: auto; max-height: calc(100vh - 240px);",
            ):
                with html.Div(
                    v_for=("job in vq_jobs_list",),
                    key=("job.id",),
                    classes="d-flex align-center py-2",
                    style="border-bottom: 1px solid rgba(255,255,255,0.08);",
                ):
                    v.VChip(
                        "{{ job.state }}",
                        color=("job.color",),
                        size="x-small",
                        label=True,
                        classes="mr-3",
                        style="min-width: 78px; justify-content: center;",
                    )
                    with html.Div(style="flex: 1 1 auto; min-width: 0;"):
                        html.Div(
                            "{{ job.name }}",
                            classes="text-body-2 text-truncate",
                        )
                        html.Div(
                            "{{ job.id.slice(0,12) }}"
                            "{{ job.elapsed ? '  ·  ' + job.elapsed : '' }}",
                            classes="text-caption text-medium-emphasis",
                        )
                    v.VBtn(
                        icon="mdi-text-box-search-outline",
                        size="small",
                        variant="text",
                        title="Show status + log tail",
                        click=(ctrl.vq_show_job_status, "[job.id, job.name]"),
                    )
                    v.VBtn(
                        icon="mdi-monitor-eye",
                        size="small",
                        variant="text",
                        title="Watch live (checkpoint QVF)",
                        v_if=("job.watchable",),
                        click=(ctrl.vq_watch_live, "[job.id]"),
                    )
                    v.VBtn(
                        icon="mdi-open-in-app",
                        size="small",
                        variant="text",
                        title="Fetch & open results",
                        disabled=("!job.openable",),
                        click=(ctrl.open_vq_job, "[job.id]"),
                    )
                html.Div(
                    "No jobs found. Turn on Live monitor to see "
                    "queued and running jobs.",
                    v_if=("vq_jobs_list.length === 0",),
                    classes="text-caption text-medium-emphasis pa-4 text-center",
                )

        # ── vq Submit dialog ──────────────────────────────────────────
        with v.VDialog(
            v_model=("vq_submit_dialog",),
            max_width="450px",
            afterLeave=_dialog_focus_restore("Submit to vq"),
            aria_label="Submit to vq",
            __properties=[("aria_label", "aria-label")],
        ), v.VCard():
            v.VCardTitle("Submit to vq")
            v.VCardText(
                "Submit a native ORCA input from the current geometry to the local vq queue. "
                "Requires the ORCA executable registered as 'orca' in vq. "
                "ORCA output is captured in the job's stdout log.",
                v_if=("calc_engine === 'ORCA'",),
            )
            v.VCardText(
                "{{ calc_orca_method }} / {{ calc_orca_basis }} · {{ calc_template }} · "
                "charge {{ calc_charge }}, multiplicity {{ calc_multiplicity }} · "
                "{{ calc_orca_nprocs }} processes · {{ calc_orca_maxcore }} MB/process. "
                "Queue memory includes 33% headroom above total MaxCore.",
                v_if=("calc_engine === 'ORCA'",), classes="text-caption",
            )
            v.VTextField(
                v_if=("calc_engine === 'ORCA'",), v_model=("calc_orca_wall_minutes",),
                label="Wall time (minutes)", type="number", min=1,
                classes="px-4", density="compact",
            )
            v.VCardText(
                "{{ vq_submit_container"
                " ? 'This will submit one pending QVF container "
                "(structure + job.spec). vq runs it and settles the same "
                "file in place, so the archive you get back is the whole "
                "calculation.'"
                " : 'This will generate a vibe-qc input script and submit "
                "it to the vq job queue for execution.' }}",
                v_if=("calc_engine !== 'ORCA'",),
            )
            v.VCardText(
                "Template: {{ calc_template }}  |  Method: {{ calc_method }}  "
                "|  Basis: {{ calc_basis }}",
                v_if=("calc_engine !== 'ORCA'",),
                classes="text-caption",
            )
            # Script mode submits an input generated from the open structure
            # and the parameters above. A ".py input path" field used to sit
            # here, but its value was never read (#37-42).
            v.VCardSubtitle(
                "Submits a vibe-qc input generated from this structure "
                "and these parameters.",
                v_if=("calc_engine !== 'ORCA' && !vq_submit_container",),
            )
            v.VSwitch(
                v_model=("vq_submit_container",),
                v_if=("calc_engine !== 'ORCA'",),
                label=(
                    "Submit as QVF container (one file out, "
                    "same file back)"
                ),
                density="compact",
                color="primary",
                hide_details=True,
                classes="px-4",
                disabled=("!vq_submit_container_available",),
            )
            v.VSwitch(
                v_model=("vq_submit_live_checkpoint",),
                v_if=("calc_engine !== 'ORCA'",),
                label=(
                    "vq_submit_container"
                    " ? 'Stream live checkpoints"
                    " (containers settle in place instead)'"
                    " : 'Stream live checkpoints"
                    " (watchable while running)'",
                ),
                density="compact",
                color="primary",
                hide_details=True,
                classes="px-4",
                disabled=("vq_submit_container",),
            )
            with v.VProgressLinear(
                v_if=("vq_submitting",),
                indeterminate=True,
                color="primary",
            ):
                pass
            with v.VCardActions():
                v.VSpacer()
                v.VBtn("Cancel", click="vq_submit_dialog = false", disabled=("vq_submitting",))
                v.VBtn(
                    "Submit to vq",
                    color="primary",
                    variant="flat",
                    click=ctrl.vq_submit_job,
                    disabled=("vq_submitting",),
                )

        # ── Supercell dialog (v1.3) ────────────────────────────────────
        with v.VDialog(
            v_model=("build_supercell_dialog",),
            max_width="400px",
            afterLeave=_dialog_focus_restore("Build supercell"),
            aria_label="Build supercell",
            __properties=[("aria_label", "aria-label")],
        ), v.VCard():
            v.VCardTitle("Build Supercell")
            v.VCardText("Specify replication factors Nx, Ny, Nz:")
            with v.VRow(dense=True):
                v.VTextField(
                    v_model=("build_supercell_nx", 1),
                    v_if=("!is_periodic || pbc_a",),
                    label="Nx",
                    type="number",
                    min=1,
                    density="compact",
                    hide_details=True,
                    style="max-width: 80px",
                )
                v.VTextField(
                    v_model=("build_supercell_ny", 1),
                    v_if=("!is_periodic || pbc_b",),
                    label="Ny",
                    type="number",
                    min=1,
                    density="compact",
                    hide_details=True,
                    style="max-width: 80px",
                )
                v.VTextField(
                    v_model=("build_supercell_nz", 1),
                    v_if=("!is_periodic || pbc_c",),
                    label="Nz",
                    type="number",
                    min=1,
                    density="compact",
                    hide_details=True,
                    style="max-width: 80px",
                )
            with v.VCardActions():
                v.VSpacer()
                v.VBtn("Cancel", click="build_supercell_dialog = false")
                v.VBtn(
                    "Build",
                    color="primary",
                    click=ctrl.build_supercell,
                )

        # ── Screenshot dialog ─────────────────────────────────────────
        with v.VDialog(
            v_model=("screenshot_dialog",),
            max_width="400px",
            afterLeave=_dialog_focus_restore("Export screenshot"),
            aria_label="Export screenshot",
            __properties=[("aria_label", "aria-label")],
        ), v.VCard():
            v.VCardTitle("Export Screenshot")
            v.VCardText("Resolution multiplier")
            v.VSelect(
                v_model=("screenshot_scale",),
                items=("screenshot_scale_options",),
                label="Scale",
                density="compact",
                hide_details=True,
            )
            v.VSwitch(
                v_model=("screenshot_transparent",),
                label="Transparent background",
                density="compact",
                color="primary",
            )
            # The browser's frame has the outlines of isosurfaces and
            # ribbons; only a server render has ambient occlusion (#116).
            v.VSelect(
                v_model=("screenshot_source",),
                items=("screenshot_source_options",),
                label="Render",
                density="compact",
                hint=(
                    "Only the browser view outlines isosurfaces and ribbons; "
                    "only the server render adds ambient occlusion."
                ),
                persistent_hint=True,
            )
            with v.VCardActions():
                v.VSpacer()
                v.VBtn("Cancel", click="screenshot_dialog = false")
                v.VBtn(
                    "Capture",
                    color="primary",
                    click=ctrl.save_screenshot,
                )

        # ── Video export dialog ──────────────────────────────────────
        with v.VDialog(
            v_model=("video_export_dialog",),
            max_width="450px",
            persistent=("video_export_running",),
            afterLeave=_dialog_focus_restore("Export video"),
            aria_label="Export video",
            __properties=[("aria_label", "aria-label")],
        ), v.VCard():
            v.VCardTitle("Export Video")
            v.VCardText("Format, frame rate, and animation type for video export")
            v.VSelect(
                v_model=("video_export_format", "mp4"),
                items=("video_export_format_options",),
                item_title="title",
                item_value="value",
                label="Format",
                density="compact",
                hide_details=True,
            )
            v.VSelect(
                v_model=("video_export_fps", 30),
                items=("video_export_fps_options",),
                item_title="title",
                item_value="value",
                label="Frame rate",
                density="compact",
                hide_details=True,
            )
            v.VSelect(
                v_model=("video_export_kind", "turntable"),
                items=("video_export_kind_options",),
                item_title="title",
                item_value="value",
                label="Animation type",
                density="compact",
                hide_details=True,
            )
            v.VProgressLinear(
                v_if=("video_export_running",),
                indeterminate=True,
                color="primary",
            )
            with v.VCardActions():
                v.VSpacer()
                v.VBtn(
                    "Cancel",
                    click="video_export_dialog = false",
                    disabled=("video_export_running",),
                )
                v.VBtn(
                    "Export",
                    color="primary",
                    click=(ctrl.do_video_export, f"[{CLIENT_CAMERA_ARG}]"),
                    disabled=("video_export_running",),
                )

        # ── High-quality raytrace render dialog ────────────────────────
        with v.VDialog(
            v_model=("hq_render_dialog",),
            max_width="520px",
            persistent=("hq_render_running",),
            afterLeave=_dialog_focus_restore("High-quality raytrace render"),
            aria_label="High-quality raytrace render",
            __properties=[("aria_label", "aria-label")],
        ), v.VCard():
            v.VCardTitle("High-Quality Raytrace Render")
            v.VCardSubtitle("Publication / magazine-cover quality output")
            with v.VCardText():
                # Quality preset
                v.VSelect(
                    v_model=("hq_render_quality",),
                    items=("hq_render_quality_options",),
                    label="Quality",
                    density="compact",
                    hide_details=True,
                    disabled=("hq_render_running",),
                )
                # Resolution
                v.VSelect(
                    v_model=("hq_render_resolution",),
                    items=("hq_render_resolution_options",),
                    label="Resolution",
                    density="compact",
                    hide_details=True,
                    disabled=("hq_render_running",),
                )
                # Environment / lighting
                v.VSelect(
                    v_model=("hq_render_environment",),
                    items=("hq_render_environment_options",),
                    label="Lighting",
                    density="compact",
                    hide_details=True,
                    disabled=("hq_render_running",),
                )
                # Material style
                v.VSelect(
                    v_model=("hq_render_material",),
                    items=("hq_render_material_options",),
                    label="Material",
                    density="compact",
                    hide_details=True,
                    disabled=("hq_render_running",),
                )
                # Depth of field toggle
                v.VSwitch(
                    v_model=("hq_render_dof",),
                    label="Depth of field",
                    density="compact",
                    color="primary",
                    disabled=("hq_render_running",),
                )
                # Save to disk alongside QVF file
                v.VSwitch(
                    v_model=("hq_render_save_disk",),
                    label="Save next to .qvf file",
                    density="compact",
                    color="primary",
                    disabled=("hq_render_running",),
                )
                v.VCardText(
                    "{{ hq_render_saved_path }}",
                    v_if=("hq_render_saved_path",),
                    classes="text-caption text-green",
                )
                # Progress bar (shown during render)
                v.VProgressLinear(
                    v_model=("hq_render_progress",),
                    color="primary",
                    height="8",
                    indeterminate=("hq_render_running && hq_render_progress < 0.05",),
                    v_if=("hq_render_running",),
                )
                v.VCardText(
                    "{{ hq_render_progress_msg }}",
                    v_if=("hq_render_running",),
                    classes="text-caption",
                )
            with v.VCardActions():
                v.VSpacer()
                v.VBtn(
                    "Cancel",
                    click="hq_render_dialog = false",
                    disabled=("hq_render_running",),
                )
                v.VBtn(
                    "Render",
                    color="primary",
                    click=ctrl.start_hq_render,
                    disabled=("hq_render_running",),
                )

        # ── Keyboard shortcuts dialog ────────────────────────────────
        with v.VDialog(
            v_model=("shortcuts_help_dialog",),
            max_width="400px",
            afterLeave=_dialog_focus_restore("Keyboard shortcuts"),
            aria_label="Keyboard shortcuts",
            __properties=[("aria_label", "aria-label")],
        ), v.VCard():
            v.VCardTitle("Keyboard Shortcuts")
            with v.VCardText(), v.VList(density="compact"), v.VListItem(
                v_for=("(key, desc) in Object.entries(shortcut_keys)",),
                key=("key",),
            ), v.VListItemTitle():
                v.VChip(
                    "{{ key }}",
                    size="x-small",
                    color="primary",
                    variant="outlined",
                    classes="mr-2",
                    label=True,
                )
                html.Span("{{ desc }}")
            with v.VCardActions():
                v.VSpacer()
                v.VBtn("Close", click="shortcuts_help_dialog = false")

        # ── About dialog ────────────────────────────────────────────
        with v.VDialog(
            v_model=("about_dialog",),
            max_width="450px",
            afterLeave=_dialog_focus_restore("About vibe-view"),
            aria_label="About vibe-view",
            __properties=[("aria_label", "aria-label")],
        ), v.VCard():
            v.VCardTitle("vibe-view")
            v.VCardSubtitle("{{ about_version }}")
            v.VCardText(
                "GPU-accelerated interactive 3D viewer and molecular editor "
                "for QVF and quantum-chemistry data from many codes.\n\n"
                "Features: structure editing, fragment building, crystal builder, "
                "POV-Ray/Blender export, vq integration, material presets, "
                "presentation mode, and more."
            )
            v.VDivider()
            v.VCardText(
                "{{ about_system_info }}",
                classes="text-caption",
                style="white-space: pre; font-family: monospace;",
            )
            # The browser's graphics, which the server cannot see (#114).
            v.VCardText(
                "Browser graphics: {{ client_graphics || 'unknown' }}",
                classes="text-caption",
                style="font-family: monospace;",
            )
            client.Script(_GRAPHICS_INFO_JS)
            v.VDivider()
            v.VCardText("License: MPL 2.0", classes="text-caption")
            v.VCardText("https://vibe-qc.com", classes="text-caption")
            with v.VCardActions():
                v.VSpacer()
                v.VBtn("Close", click="about_dialog = false")

        # ── Settings dialog ───────────────────────────────────────────
        with v.VDialog(
            v_model=("settings_dialog",),
            max_width="450px",
            afterLeave=_dialog_focus_restore("Settings"),
            aria_label="Settings",
            __properties=[("aria_label", "aria-label")],
        ), v.VCard():
            v.VCardTitle("Settings")
            v.VTextField(
                v_model=("settings_relocalize_backend_python",),
                label="vibe-qc Python executable",
                placeholder="/path/to/backend/bin/python",
                hint=("Global backend for re-localization. "
                      "Use Python from a separate vibe-qc installation."),
                persistent_hint=True,
                classes="ma-3",
            )
            v.VBtn(
                "Save backend and check",
                click=ctrl.save_relocalize_backend,
                loading=("relocalize_probe_running",),
                classes="mx-3",
            )
            v.VAlert("{{ relocalize_backend_status }}", density="compact", classes="ma-3")
            v.VSwitch(
                v_model=("settings_dark_background",),
                label="Dark background (default)",
                density="compact",
                color="primary",
                hide_details=True,
            )
            v.VSwitch(
                v_model=("ui_dark_mode", True),
                label="Dark UI theme",
                density="compact",
                color="primary",
                hide_details=True,
                update_modelValue=(ctrl.toggle_dark_ui, "[$event]"),
            )
            v.VSelect(
                v_model=("settings_material_preset",),
                items=("material_preset_options",),
                item_title="title",
                item_value="value",
                label="Default material style",
                density="compact",
                hide_details=True,
            )
            v.VSelect(
                v_model=("settings_auto_save_interval", 60),
                items=("settings_interval_options",),
                item_title="title",
                item_value="value",
                label="Auto-save interval",
                density="compact",
                hide_details=True,
            )
            v.VSelect(
                v_model=("settings_energy_unit",),
                items=("settings_energy_unit_options",),
                item_title="title",
                item_value="value",
                label="Energy unit",
                density="compact",
                hide_details=True,
            )
            v.VSelect(
                v_model=("settings_measure_unit",),
                items=("settings_measure_unit_options",),
                item_title="title",
                item_value="value",
                label="Length unit",
                density="compact",
                hide_details=True,
            )
            v.VSelect(
                v_model=("settings_band_energy_unit",),
                items=("settings_band_energy_unit_options",),
                item_title="title",
                item_value="value",
                label="Band / DOS energy unit",
                density="compact",
                hide_details=True,
            )
            with v.VCardActions():
                v.VSpacer()
                v.VBtn("Cancel", click="settings_dialog = false")
                v.VBtn(
                    "Save",
                    color="primary",
                    click=ctrl.save_settings,
                )

        # ── Download manager dialog ──────────────────────────────────
        with v.VDialog(
            v_model=("download_manager_dialog",),
            max_width="500px",
            afterLeave=_dialog_focus_restore("Recent exports"),
            aria_label="Recent exports",
            __properties=[("aria_label", "aria-label")],
        ), v.VCard():
            v.VCardTitle("Recent Exports")
            with v.VList(density="compact", v_if=("download_history.length > 0",)):
                v.VListItem(
                    v_for=("item in download_history",),
                    key=("item.time",),
                    title=("item.name",),
                    subtitle=("item.format + ' — ' + item.time",),
                    prepend_icon="mdi-file-download",
                )
            v.VCardText(
                "No exports yet. Use the export buttons or take a screenshot.",
                v_if=("download_history.length === 0",),
                classes="text-caption",
            )
            with v.VCardActions():
                v.VSpacer()
                v.VBtn(
                    "Clear History", size="small", variant="text", click="download_history = []"
                )
                v.VBtn("Close", click="download_manager_dialog = false")

        # Left sidebar — sibling of v-main so Vuetify offsets the content.
        with v.VNavigationDrawer(
            permanent=True,
            v_if=("!presentation_mode",),
            width=("left_panel_width",),
            id="vv-left-panel",
            aria_label="Result sections",
            __properties=[("aria_label", "aria-label")],
        ):
            v.VListSubheader("Sections")
            # Filter box (design refresh 2026 roadmap): narrows the section
            # list by title/subtitle, client-side.
            v.VTextField(
                v_model=("section_filter", ""),
                placeholder="Filter sections…",
                prepend_inner_icon="mdi-magnify",
                density="compact",
                hide_details=True,
                clearable=True,
                classes="mx-2 mb-1",
                v_if=("sidebar_entries.length > 6",),
            )
            v.VDivider()
            with v.VList(density="compact", nav=True):
                v.VListItem(
                    v_for=(
                        "entry in sidebar_entries.filter(e => !section_filter || "
                        "((e.title || '') + ' ' + (e.subtitle || ''))"
                        ".toLowerCase().includes(section_filter.toLowerCase()))",
                    ),
                    key=("entry.id",),
                    title=("entry.title",),
                    subtitle=("entry.subtitle",),
                    # Kind glyph leads (scannable); the status tick is only
                    # worth pixels when something is wrong, so a warning badge
                    # trails on error/unsupported (design refresh 2026). The
                    # exact status stays in the subtitle either way.
                    prepend_icon=("entry.kind_icon || entry.icon",),
                    # The eye marks the structure the 3D view shows when the
                    # archive holds several (#90).
                    append_icon=(
                        "entry.warn ? 'mdi-alert-circle' : "
                        "(shown_structure_id && entry.id === shown_structure_id"
                        " ? 'mdi-eye-outline' : null)",
                    ),
                    disabled=("entry.disabled",),
                    click=(
                        ctrl.activate_section,
                        "[entry.id]",
                    ),
                )

        # Right control panel — sibling, gated by selected_section.
        with v.VNavigationDrawer(
            location="right",
            permanent=True,
            width=("right_panel_width",),
            v_if=("selected_section && !presentation_mode",),
            id="vv-right-panel",
            aria_label="Section controls",
            __properties=[("aria_label", "aria-label")],
        ):
            _build_controls(server, ctrl)

        # noqa: SIM117 below — the nesting mirrors the Vuetify DOM and each
        # level carries its own comment; flattening it would move 500+ lines
        # of layout one indent left for no gain.
        with v.VMain():  # noqa: SIM117
            # Inside main: viewport on top, bottom panel below. Use flex column
            # so the viewport fills the available height while the bottom panel
            # only takes the space its cards need.
            #
            # The bottom panel is user-resizable: drag its top edge or the
            # handle in its bottom-right corner.  CSS ``resize: vertical``
            # with a fixed ``height`` gives the browser-native resize widget.
            with html.Div(
                style="height: 100%; display: flex; flex-direction: column;",
            ):
                with html.Div(
                    # position:relative anchors the absolutely-positioned
                    # welcome card to the viewport — without it the card
                    # anchors to the page and slides under the 280px
                    # sections drawer (left half hidden, text truncated).
                    style="flex: 1 1 auto; min-height: 0; padding: 4px; position: relative;",
                    # Keep vtk.js mounted while a 2D slide owns the surface.
                    # Unmounting here can drop a camera push during fast or
                    # automatic panel-to-3D navigation.
                    v_show=("!presentation_mode || !presentation_panel_slide",),
                ):
                    server.state.atom_camera_controls = CAMERA_CONTROLS
                    server.state.atom_drag_controls = DRAG_CONTROLS
                    client.Script(ATOM_DRAG_JS)
                    client.Script(LABEL_FOLLOWER_JS)
                    client.Script(SILHOUETTE_JS)
                    client.Script(BROWSER_CAPTURE_JS)
                    client.Script(FIGURE_OVERLAY_JS)
                    # trame-vtk fills its serializer registry when its
                    # protocol is configured at server start, which is after
                    # this layout is built and after the server-ready
                    # callbacks have been queued. Configure hooks run in
                    # registration order, so this one wraps the registry
                    # right after the vtk module filled it, before the first
                    # client asks for the scene.
                    server.add_protocol_to_configure(
                        lambda _protocol: install_label_array_serializer()
                    )
                    local_view = VtkLocalView(
                        plotter.ren_win,
                        interactor_settings=(
                            "edit_mode && edit_drag_mode "
                            "? atom_drag_controls : atom_camera_controls",
                        ),
                        # Hardware-pick on click so measure mode can map a
                        # click to the nearest atom (handler is a no-op
                        # unless measure mode is on, so rotate/zoom are
                        # unaffected). Hover picks are debounced client-side
                        # (~10 ms after the pointer pauses, not per
                        # mousemove), so adding them costs one pick per
                        # hover stop; they feed the atom tooltip and give
                        # the right-click menu its target.
                        picking_modes=("['click','hover']",),
                        click=(ctrl.on_pick, "[$event]"),
                        hover=(ctrl.on_hover, "[$event]"),
                        # Client -> server camera readback. In VtkLocalView
                        # the client owns the camera, and nothing reported
                        # it back, so every feature that persists "the
                        # current view" silently stored the server's stale
                        # camera instead (see sync_client_camera).
                        #
                        # $event carries {type, pokedRenderer, firstRenderer};
                        # the renderers hold functions, so passing $event
                        # itself fails to encode ("Unrecognized object:
                        # [object Function]") and the handler never runs.
                        # Pull the plain number arrays out in JS instead.
                        interactor_events=("vv_camera_events", [
                            "EndAnimation", "LeftButtonPress", "MouseMove", "LeftButtonRelease",
                            "MouseLeave", *LABEL_FOLLOWER_EVENTS,
                        ]),
                        LeftButtonPress="window.vvAtomDrag && window.vvAtomDrag('begin', $event)",
                        # Labels face the camera through the vertex shader
                        # (#111); a hover hands the follower its renderer
                        # before any drag, and is a no-op after that.
                        MouseMove=(
                            "window.vvAtomDrag && window.vvAtomDrag('move', $event); "
                            + LABEL_FOLLOWER_HANDLER
                        ),
                        **LABEL_FOLLOWER_HANDLERS,
                        # Each scene load: new label meshes onto the shader
                        # (#111), and the outline pass installed (#112).
                        after_scene_loaded=(
                            LABEL_FOLLOWER_SCENE_HANDLER + "; " + SILHOUETTE_SCENE_HANDLER
                        ),
                        LeftButtonRelease="window.vvAtomDrag && window.vvAtomDrag('end', $event)",
                        MouseLeave="window.vvAtomDrag && window.vvAtomDrag('end', null)",
                        EndAnimation=(
                            ctrl.sync_client_camera,
                            "[$event.pokedRenderer.getActiveCamera().getPosition(),"
                            " $event.pokedRenderer.getActiveCamera().getFocalPoint(),"
                            " $event.pokedRenderer.getActiveCamera().getViewUp(),"
                            " $event.pokedRenderer.getActiveCamera().getParallelScale()]",
                        ),
                    )
                    # Bind controller method so handlers can push the mesh
                    # state to the client after plotter edits.
                    view_update = _retaining_view_updater(
                        plotter,
                        local_view.update,
                        # Which isosurfaces and ribbons the browser outlines
                        # with figure outlines on: published with every push,
                        # so the pass follows the scene it receives (#112).
                        before_push=lambda: publish_silhouette_targets(server.state, plotter),
                    )
                    ctrl.view_update = view_update
                    ctrl.view_reset_camera = local_view.reset_camera
                    # push_camera sends the server plotter's exact camera to the
                    # client, unlike update() (geometry only — the client keeps
                    # its own camera) and reset_camera() (fits-all, loses zoom).
                    # This is what makes camera presets/bookmarks move the live
                    # view (audit L1).
                    ctrl.view_push_camera = local_view.push_camera
                    # Stash on the plotter so module-level _rebuild_* helpers
                    # can reach the updater without taking ctrl as an extra
                    # positional arg through 12 call sites.
                    _scene_hooks(plotter).update = view_update
                    _scene_hooks(plotter).push_camera = local_view.push_camera

                    # Right-click context menu: JavaScript intercepts
                    # right-clicks on the VTK canvas and opens the Vuetify
                    # context menu at the click position.
                    _CONTEXT_JS = (
                        # Bind on document: vtk.js overlays an interaction
                        # layer above the canvas, so contextmenu events never
                        # reach canvas-level listeners (verified: the event
                        # bubbles to document but 'canvas' is not in its
                        # path). Bounds-check against the canvas instead.
                        "typeof document !== 'undefined' && (function(){"
                        "if(window._vibe_ctx_wired)return;"
                        "window._vibe_ctx_wired=1;"
                        "document.addEventListener('contextmenu',function(e){"
                        "var el=document.querySelector('.vtk-view canvas, "
                        ".js-vtk-view canvas, canvas');"
                        "if(!el)return;"
                        "var r=el.getBoundingClientRect();"
                        "if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)return;"
                        "e.preventDefault();"
                        "var t=window.trame;if(!t)return;"
                        "t.state.set('context_menu_x',e.clientX);"
                        "t.state.set('context_menu_y',e.clientY);"
                        "t.state.set('context_menu_show',true);"
                        "t.state.flush();"
                        # The server resolves what the menu points at from
                        # the last debounced hover pick (the pointer pauses
                        # to right-click, so one just fired); it sets
                        # context_menu_atom_idx, which gates 'Select Atom'.
                        "if(t.trigger)t.trigger('context_menu_opened');"
                        "});"
                        "})()"
                    )
                    # Script-tag install — see the keyboard-shortcut
                    # comment: state-change installers never fired.
                    client.Script(_CONTEXT_JS)

                    # Hover tooltip: track mouse position on VTK canvas
                    _VIEWPORT_ARIA_LABEL = "Interactive 3D molecular structure viewport"
                    _TOOLTIP_JS = (
                        "typeof document !== 'undefined' && (function(){"
                        "var _tries=0;var _iv=setInterval(function(){"
                        "if(++_tries>40){clearInterval(_iv);return;}"
                        "var el=document.querySelector('.vtk-view canvas, "
                        ".js-vtk-view canvas, canvas');"
                        "if(!el)return;"
                        f"el.setAttribute('aria-label',{_VIEWPORT_ARIA_LABEL!r});"
                        "if(el._vibe_tt_wired)return;"
                        "clearInterval(_iv);"
                        "el._vibe_tt_wired=1;"
                        # Throttled to 80 ms: unthrottled this flushed a
                        # websocket state update on EVERY mousemove over the
                        # canvas (~60/s while interacting) — for a tooltip
                        # that only repositions. Content comes from the
                        # debounced hover pick (on_hover), not from here.
                        "el.addEventListener('mousemove',function(e){"
                        "var t=window.trame;if(!t)return;"
                        "var n=Date.now();"
                        "if(n-(el._vv_tt_last||0)<80)return;"
                        "el._vv_tt_last=n;"
                        "t.state.set('hover_tooltip_x',e.clientX);"
                        "t.state.set('hover_tooltip_y',e.clientY);"
                        "t.state.flush();"
                        "});"
                        "el.addEventListener('mouseleave',function(){"
                        "var t=window.trame;if(!t)return;"
                        "t.state.set('hover_tooltip_visible',false);"
                        "t.state.flush();"
                        "});"
                        "},250);"
                        "})()"
                    )
                    # Script-tag install — the old trigger state was only
                    # ever set by the very handler this JS installs.
                    client.Script(_TOOLTIP_JS)

                    # Welcome / quickstart panel
                    with v.VCard(
                        v_if=(
                            "!selected_section && welcome_visible"
                            " && sidebar_entries.length > 0 && !chrome_hidden",
                        ),
                        classes="ma-4 pa-4",
                        style=(
                            "position: absolute; top: 20px; left: 20px; "
                            "z-index: 50; max-width: 400px; opacity: 0.95;"
                        ),
                    ):
                        v.VCardTitle("👋 Welcome to vibe-view")
                        v.VCardText(
                            "Click a section in the left sidebar to start exploring your data. "
                            "Here are some quick tips:"
                        )
                        with v.VList(density="compact"):
                            v.VListItem(
                                title="Structure",
                                subtitle="Click to see atoms and bonds in 3D",
                                prepend_icon="mdi-molecule",
                            )
                            v.VListItem(
                                title="Molecular Orbitals",
                                subtitle="View HOMO, LUMO, and other orbitals as isosurfaces",
                                prepend_icon="mdi-orbit",
                            )
                            v.VListItem(
                                title="Press ? for keyboard shortcuts",
                                subtitle="e=edit, m=measure, r=reset, s=screenshot",
                                prepend_icon="mdi-keyboard",
                            )
                        v.VBtn(
                            "Got it",
                            size="small",
                            variant="text",
                            click="welcome_visible = false",
                        )

                # Resize handle — a grabber bar between viewport and bottom
                # panel. Drag it to resize (see _BOTTOM_RESIZE_JS); a
                # full-window overlay during the drag keeps the VTK canvas
                # from swallowing the gesture and rotating the molecule.
                with html.Div(
                    id="vv-hsplit",
                    style=(
                        "flex: 0 0 8px; background: #444; cursor: row-resize;"
                        "border-top: 1px solid #666; border-bottom: 1px solid #333;"
                    ),
                    v_if=("selected_section && !presentation_mode",),
                ):
                    pass

                # Bottom panel: 2D plots. Hidden when no section is selected
                # so the viewport gets the entire main area.
                # User-resizable via the native ``resize: vertical`` handle
                # (bottom-right corner).  The explicit ``height`` (not
                # flex-driven) is required for the resize widget to appear.
                with html.Div(
                    id="vv-bottom-panel",
                    style=(
                        "flex: 0 0 auto; min-height: 80px; height: 35vh;"
                        "max-height: 85vh; overflow: auto; resize: vertical;"
                        "box-sizing: border-box;"
                        "padding: 8px;"
                        # Flex column so a chart card can be told to fill the
                        # panel. Without it the card is auto-height, `height:
                        # 100%` on the iframe resolves against nothing, and the
                        # iframe falls back to its min-height: title + 300px
                        # does not fit in 35vh, so the chart hung past the
                        # bottom of the window with its x-axis clipped.
                        "display: flex; flex-direction: column;"
                    ),
                    v_if=(
                        "selected_section && "
                        "(!presentation_mode || presentation_panel_slide)",
                    ),
                    classes=("presentation_mode ? 'vv-presentation-panel' : ''",),
                ):
                    # Spectra/bands HTML comes from Plotly
                    # (Renderer.render_to_html) and carries inline <script>
                    # tags. Vue's v-html refuses to execute those (XSS
                    # protection), so we host each snippet in a sandboxed
                    # iframe via srcdoc — iframes evaluate their own scripts.
                    with v.VCard(
                        v_if=("bands_html",),
                        style=(
                            "flex: 1 1 auto; min-height: 0;"
                            "display: flex; flex-direction: column;"
                        ),
                    ):
                        v.VCardTitle("{{ bands_title }}")
                        # Energy window (#26). An all-electron periodic
                        # archive puts core states thousands of eV below
                        # E_F, so the autoscaled axis flattens the valence
                        # region; the chart opens on a valence window and
                        # these fields move it. Commit on Enter or on
                        # leaving the field, not per keystroke — every
                        # commit re-renders the whole figure.
                        with v.VRow(
                            dense=True,
                            classes="px-4 align-center",
                            style="flex: 0 0 auto;",
                            v_if=("bands_window_available",),
                        ):
                            with v.VCol(cols=4):
                                v.VTextField(
                                    v_model=("bands_emin",),
                                    label=("'E − E_F min (' + band_energy_label + ')'",),
                                    aria_label=(
                                        "'Energy window minimum in ' + band_energy_label",
                                    ),
                                    __properties=[("aria_label", "aria-label")],
                                    type="number",
                                    density="compact",
                                    hide_details=True,
                                    __events=["blur", ("keyup_enter", "keyup.enter")],
                                    keyup_enter=ctrl.update_bands_energy_window,
                                    blur=ctrl.update_bands_energy_window,
                                )
                            with v.VCol(cols=4):
                                v.VTextField(
                                    v_model=("bands_emax",),
                                    label=("'E − E_F max (' + band_energy_label + ')'",),
                                    aria_label=(
                                        "'Energy window maximum in ' + band_energy_label",
                                    ),
                                    __properties=[("aria_label", "aria-label")],
                                    type="number",
                                    density="compact",
                                    hide_details=True,
                                    __events=["blur", ("keyup_enter", "keyup.enter")],
                                    keyup_enter=ctrl.update_bands_energy_window,
                                    blur=ctrl.update_bands_energy_window,
                                )
                            with v.VCol(cols=4):
                                v.VBtn(
                                    "Reset window",
                                    size="small",
                                    variant="text",
                                    disabled=("bands_window_auto",),
                                    click=ctrl.reset_bands_energy_window,
                                )
                        html.Iframe(
                            srcdoc=("bands_html",),
                            style=(
                                "width: 100%; flex: 1 1 auto; min-height: 160px;"
                                " border: none;"
                            ),
                            sandbox="allow-scripts",
                        )

                    with v.VCard(
                        v_if=("phonon_html",),
                        style=(
                            "flex: 1 1 auto; min-height: 0;"
                            "display: flex; flex-direction: column;"
                        ),
                    ):
                        v.VCardTitle("{{ phonon_title }}")
                        html.Iframe(
                            srcdoc=("phonon_html",),
                            style=(
                                "width: 100%; flex: 1 1 auto; min-height: 160px;"
                                " border: none;"
                            ),
                            sandbox="allow-scripts",
                        )

                    with v.VCard(
                        v_if=("eos_html",),
                        style=(
                            "flex: 1 1 auto; min-height: 0;"
                            "display: flex; flex-direction: column;"
                        ),
                    ):
                        v.VCardTitle("{{ eos_title }}")
                        html.Iframe(
                            srcdoc=("eos_html",),
                            style=(
                                "width: 100%; flex: 1 1 auto; min-height: 160px;"
                                " border: none;"
                            ),
                            sandbox="allow-scripts",
                        )

                    with v.VCard(
                        v_if=("spectra_html",),
                        style=(
                            "flex: 1 1 auto; min-height: 0;"
                            "display: flex; flex-direction: column;"
                        ),
                    ):
                        v.VCardTitle("{{ spectra_title }}")
                        v.VCheckbox(
                            v_model=("spectra_compare",),
                            label="Compare across open files",
                            density="compact",
                            hide_details=True,
                            classes="px-4",
                            v_if=("file_names.length > 1",),
                            update_modelValue=(ctrl.toggle_spectra_compare, "[$event]"),
                        )
                        with v.VRow(
                            dense=True, classes="px-4 align-center", style="flex: 0 0 auto;"
                        ):
                            with v.VCol(cols=3):
                                v.VTextField(
                                    v_model=("spectra_fwhm",), type="number",
                                    label="FWHM", density="compact", hide_details=True,
                                    update_modelValue=(ctrl.update_spectra_view,
                                                       "[null,null,null,null,null,$event]"),
                                )
                            with v.VCol(cols=3):
                                v.VSelect(
                                    v_model=("spectra_lineshape",),
                                    items=("['lorentzian','gaussian']",), label="Line shape",
                                    density="compact", hide_details=True,
                                    update_modelValue=(ctrl.update_spectra_view,
                                                       "[null,null,null,null,$event]"),
                                )
                            with v.VCol(cols=3):
                                v.VSelect(
                                    v_model=("spectra_width_unit",),
                                    items=("['native','eV','cm⁻¹']",), label="FWHM unit",
                                    density="compact", hide_details=True,
                                    update_modelValue=(ctrl.update_spectra_view,
                                                       "[null,null,null,null,null,null,$event]"),
                                )
                            with v.VCol(cols=3):
                                v.VCheckbox(
                                    v_model=("spectra_normalize",),
                                    label="Normalize",
                                    density="compact",
                                    hide_details=True,
                                    update_modelValue=(
                                        ctrl.update_spectra_view,
                                        "[null, $event]",
                                    ),
                                )
                        with v.VRow(
                            dense=True, classes="px-4 align-center", style="flex: 0 0 auto;"
                        ):
                            with v.VCol(cols=7):
                                v.VSelect(
                                    v_model=("spectra_x_unit",),
                                    items=(
                                        "spectra_x_unit_options",
                                        [
                                            {"title": "Native unit", "value": "native"},
                                            {"title": "Wavenumber (cm⁻¹)", "value": "cm⁻¹"},
                                            {"title": "Energy (eV)", "value": "eV"},
                                            {"title": "Wavelength (nm)", "value": "nm"},
                                        ],
                                    ),
                                    item_title="title",
                                    item_value="value",
                                    label="X unit",
                                    density="compact",
                                    hide_details=True,
                                    update_modelValue=(
                                        ctrl.update_spectra_view,
                                        "[null, null, $event]",
                                    ),
                                )
                            with v.VCol(cols=5):
                                v.VSelect(
                                    v_model=("spectra_display",),
                                    items=(
                                        "spectra_display_options",
                                        [
                                            {"title": "Envelope + sticks", "value": "both"},
                                            {"title": "Envelope only", "value": "envelope"},
                                            {"title": "Sticks only", "value": "sticks"},
                                        ],
                                    ),
                                    item_title="title",
                                    item_value="value",
                                    label="Display",
                                    density="compact",
                                    hide_details=True,
                                    update_modelValue=(
                                        ctrl.update_spectra_view,
                                        "[null, null, null, $event]",
                                    ),
                                )
                        html.Div("Grid range/step in native energy units (blank = auto)",
                                 classes="px-4 text-caption")
                        with v.VRow(dense=True, classes="px-4"):
                            for offset, name in enumerate(("min", "max", "step")):
                                with v.VCol(cols=4):
                                    v.VTextField(
                                        v_model=(f"spectra_grid_{name}",), type="number",
                                        label=f"Grid {name}", density="compact", hide_details=True,
                                        update_modelValue=(ctrl.update_spectra_view,
                                                           "[" + "null," * (7 + offset)
                                                           + "$event]"),
                                    )
                        with (
                            v.VRow(dense=True, classes="px-4", style="flex: 0 0 auto;"),
                            v.VCol(cols=12, classes="text-right py-0"),
                        ):
                                v.VBtn("Export CSV", size="small", variant="tonal",
                                       click=(ctrl.export_spectra_csv,))
                                v.VBtn("Export ASCII", size="small", variant="tonal",
                                       click=(ctrl.export_spectra_ascii,))
                        html.Iframe(
                            srcdoc=("spectra_html",),
                            style=(
                                "width: 100%; flex: 1 1 auto; min-height: 140px;"
                                " border: none;"
                            ),
                            sandbox="allow-scripts",
                        )

                    with v.VCard(v_if=("trajectory_energy_image",)):
                        v.VCardTitle("Energy Profile")
                        # A click on a trajectory's profile jumps to the frame
                        # under the pointer: the x fraction of the image goes to
                        # the renderer, which maps it through the axes it drew.
                        # A scan surface uses the same image key and ignores it.
                        _fit = "max-width: 100%; max-height: 100%;"
                        _style = f"trajectory_n_frames > 0 ? '{_fit} cursor: pointer;' : '{_fit}'"
                        html.Img(
                            src=("`data:image/png;base64,${trajectory_energy_image}`",),
                            style=(_style,),
                            title=("trajectory_n_frames > 0 ? 'Click to jump to a frame' : ''",),
                            click=(
                                f"trigger('{ctrl.trigger_name(ctrl.seek_energy_profile)}', "
                                "[$event.offsetX / $event.currentTarget.clientWidth])"
                            ),
                        )

                    # Charts that land in the bottom panel (SCF convergence,
                    # COOP/COHP, orbital energies). Separate card + separate
                    # state key from the properties panel below, because
                    # Plotly needs `allow-scripts` and the properties panel
                    # carries untrusted archive text that must never run.
                    with v.VCard(
                        v_if=("chart_html",),
                        style=(
                            "flex: 1 1 auto; min-height: 0;"
                            "display: flex; flex-direction: column;"
                        ),
                    ):
                        v.VCardTitle("{{ chart_title }}")
                        v.VCheckbox(
                            v_model=("scf_skip_guess",),
                            label="Hide initial guess (first SCF point)",
                            density="compact",
                            hide_details=True,
                            classes="px-4",
                            v_if=("chart_title === 'SCF Convergence'",),
                            update_modelValue=(ctrl.toggle_scf_skip_guess, "[$event]"),
                        )
                        v.VCheckbox(
                            v_model=("scf_log_energy",),
                            label="Log scale (|E − E_final|)",
                            density="compact",
                            hide_details=True,
                            classes="px-4",
                            v_if=("chart_title === 'SCF Convergence'",),
                            update_modelValue=(ctrl.toggle_scf_log_energy, "[$event]"),
                        )
                        html.Iframe(
                            srcdoc=("chart_html",),
                            style=(
                                "width: 100%; flex: 1 1 auto; min-height: 160px;"
                                " border: none;"
                            ),
                            # Plotly is JavaScript. Safe here because this
                            # channel only ever carries renderer-built
                            # figures: archive strings reach it through
                            # Plotly's JSON serialisation, which escapes
                            # '<' and '/' (pinned in
                            # tests/test_details_panel_sandbox.py).
                            sandbox="allow-scripts",
                        )

                    with v.VCard(v_if=("properties_html",)):
                        v.VCardTitle("{{ properties_title }}")
                        # The bottom panel is now user-resizable, so use
                        # ``height: 100%`` + ``min-height`` instead of
                        # viewport-relative ``vh`` units that overflow the
                        # panel.  The iframe scrolls internally when content
                        # exceeds the card.
                        # Attachment downloads live OUT here, not in the
                        # panel: that iframe is script-free by
                        # construction (empty sandbox, no opt-in), so it
                        # cannot call a controller. Bytes never enter it
                        # either — this row is the only way to reach them.
                        with v.VRow(
                            dense=True,
                            classes="px-4 pb-1 align-center",
                            v_if=("run_record_attachments.length > 0",),
                        ):
                            v.VBtn(
                                "{{ a.filename }} ({{ a.size }} B)",
                                v_for=("a in run_record_attachments",),
                                key=("a.role",),
                                size="x-small",
                                variant="tonal",
                                classes="mr-2 mb-1",
                                prepend_icon="mdi-download",
                                disabled=("a.too_large",),
                                title=(
                                    "a.too_large ? 'Too large to download from "
                                    "the viewer — extract it from the archive' "
                                    ": (a.description || a.role)"
                                ),
                                click=(ctrl.download_run_attachment, "[a.role]"),
                            )
                        html.Iframe(
                            srcdoc=("properties_html",),
                            style="width: 100%; min-height: 300px; height: 100%; border: none;",
                            # Deny, with no way to ask. This panel carries
                            # untrusted archive content (run logs, embedded
                            # attachments, citation text); an empty sandbox
                            # attribute is the most restrictive one, so such
                            # content cannot execute even if it ever escaped
                            # escaping. A renderer that needs scripts writes
                            # to chart_html instead — it cannot grant them
                            # here.
                            sandbox="",
                        )

        # ── Context menu (right-click on viewport) ──
        with v.VMenu(
            v_model=("context_menu_show",),
            position_x=("context_menu_x",),
            position_y=("context_menu_y",),
            absolute=True,
            close_on_content_click=True,
        ), v.VList(density="compact"):
            v.VListItem(
                "Select Atom",
                prepend_icon="mdi-cursor-pointer",
                click=ctrl.context_select_atom,
                v_if=("context_menu_atom_idx >= 0",),
            )
            v.VListItem(
                "Add Atom Here",
                prepend_icon="mdi-atom",
                click=ctrl.context_add_atom,
            )
            v.VListItem(
                "Center View Here",
                prepend_icon="mdi-crosshairs-gps",
                click=ctrl.context_center_view,
            )
            v.VDivider()
            v.VListItem(
                "Screenshot",
                prepend_icon="mdi-camera",
                click=ctrl.save_screenshot,
            )

        # ── Hover tooltip ──
        with html.Div(
            v_if=("hover_tooltip_visible && !chrome_hidden",),
            style=(
                "position: fixed; z-index: 10000; background: rgba(0,0,0,0.85); color: white; "
                "padding: 8px 12px; border-radius: 6px; font-size: 13px; font-family: monospace; "
                "pointer-events: none; white-space: pre; max-width: 250px;"
            ),
            left=("hover_tooltip_x + 'px'",),
            top=("hover_tooltip_y + 10 + 'px'",),
        ):
            "{{ hover_tooltip }}"

        # ── Presentation overlay (v1.3) ──────────────────────────────────
        with v.VOverlay(
            v_model=("presentation_mode",),
            contained=False,
            persistent=True,
            retain_focus=False,
            scrim=False,
            content_class="vv-presentation-overlay-content",
            role="region",
            aria_label="Presentation mode",
            __properties=["role", ("aria_label", "aria-label")],
        ), html.Div(
            style=(
                "width: 100vw; height: 100vh; position: relative;"
                " color: white; pointer-events: none;"
            ),
        ):
            # The live VTK viewport stays visible underneath. App chrome
            # is v-if-gated while presentation_mode is true, so the
            # viewport expands to the full browser before these controls
            # are painted over it.
            v.VCardText(
                "{{ presentation_slides.length > 0 ? "
                "presentation_slides[presentation_slide].name : '' }}",
                classes="text-h3 text-white text-center",
                style=(
                    "position: absolute; top: 24px; left: 50%;"
                    " transform: translateX(-50%); z-index: 100;"
                    " width: max-content; max-width: calc(100vw - 48px);"
                    " padding: 8px 18px; border-radius: 10px;"
                    " background: rgba(0,0,0,0.68);"
                    " text-shadow: 0 1px 3px #000;"
                ),
            )
            v.VChip(
                "{{ presentation_slide + 1 }} / {{ presentation_slides.length }}",
                color="white",
                variant="outlined",
                style=(
                    "position: absolute; bottom: 24px; right: 24px;"
                    " z-index: 100; background: rgba(0,0,0,0.68);"
                ),
            )
            # Navigation controls
            with html.Div(
                style=(
                    "position: absolute; bottom: 24px; left: 50%;"
                    " transform: translateX(-50%); display: flex; gap: 12px;"
                    " z-index: 200; padding: 4px 8px; border-radius: 24px;"
                    " background: rgba(0,0,0,0.68); pointer-events: auto;"
                ),
            ):
                v.VBtn(
                    icon="mdi-chevron-left",
                    size="large",
                    color="white",
                    variant="text",
                    title="Previous slide",
                    aria_label="Previous slide",
                    __properties=[("aria_label", "aria-label")],
                    disabled=("presentation_slides.length < 2",),
                    click=ctrl.presentation_prev,
                )
                v.VBtn(
                    icon=(
                        "presentation_auto_advance ? 'mdi-pause' : 'mdi-play'",
                    ),
                    size="large",
                    color="white",
                    variant="text",
                    title=(
                        "presentation_auto_advance ? "
                        "'Pause automatic slide advance' : "
                        "'Start automatic slide advance'",
                    ),
                    aria_label=(
                        "presentation_auto_advance ? "
                        "'Pause automatic slide advance' : "
                        "'Start automatic slide advance'",
                    ),
                    __properties=[("aria_label", "aria-label")],
                    disabled=("presentation_slides.length < 2",),
                    click=ctrl.toggle_presentation_auto_advance,
                )
                v.VBtn(
                    icon="mdi-chevron-right",
                    size="large",
                    color="white",
                    variant="text",
                    title="Next slide",
                    aria_label="Next slide",
                    __properties=[("aria_label", "aria-label")],
                    disabled=("presentation_slides.length < 2",),
                    click=ctrl.presentation_next,
                )
                v.VBtn(
                    icon="mdi-close",
                    size="large",
                    color="red",
                    variant="text",
                    title="Exit presentation",
                    aria_label="Exit presentation",
                    __properties=[("aria_label", "aria-label")],
                    click=ctrl.toggle_presentation,
                )

