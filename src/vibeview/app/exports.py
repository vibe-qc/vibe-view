"""Exports controllers for one browser viewer instance."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibeview.app import helpers as _helpers
from vibeview.plotter_compat import (
    set_background,
)

if TYPE_CHECKING:
    from vibeview.app.runtime import ViewerRuntime


class ExportsControllers:
    def __init__(self, viewer: ViewerRuntime) -> None:
        self.viewer = viewer

    # ── Controller: screenshot export ─────────────────────────────────
    # @ctrl.set alone is not client-callable; the keyboard-shortcut JS
    # invokes trame.trigger("save_screenshot"), which needs a trigger registration.
    def save_screenshot(self) -> None:
        """Capture viewport with 2x supersampling anti-aliasing.

        Renders at double the target resolution, then Lanczos-
        downsamples for publication-quality output."""
        import io

        import numpy as _np
        from PIL import Image as _Image
        from PIL import ImageFilter as _ImageFilter

        scale = max(1, min(4, int(self.viewer.server.state.screenshot_scale or 2)))
        transparent = bool(self.viewer.server.state.screenshot_transparent)
        self.viewer.server.state.screenshot_dialog = False

        win_size = self.viewer.plotter.window_size
        orig_bg = self.viewer.plotter.background_color
        target_w = win_size[0] * scale
        target_h = win_size[1] * scale
        ssaa = 2  # supersample 2x for anti-aliasing
        try:
            if transparent:
                set_background(self.viewer.plotter, "white")
            # plotter.screenshot(return_img=True) returns a NumPy (H, W, 3|4)
            # array, NOT a PIL Image — wrap it before using PIL methods.
            arr = self.viewer.plotter.screenshot(
                return_img=True,
                window_size=(target_w * ssaa, target_h * ssaa),
            )
            img = _Image.fromarray(_np.asarray(arr))
            # Lanczos downscale + unsharp mask for crisp edges.
            img = img.resize((target_w, target_h), _Image.Resampling.LANCZOS)
            img = img.filter(_ImageFilter.UnsharpMask(radius=0.5, percent=60, threshold=2))
            if transparent:
                # Make white background transparent.
                img = _helpers._white_to_transparent(img)
        finally:
            if transparent:
                set_background(self.viewer.plotter, orig_bg)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        png_bytes = buf.getvalue()
        b64 = base64.b64encode(png_bytes).decode()
        self.viewer.server.state.screenshot_data = f"data:image/png;base64,{b64}"
        self.viewer.server.state.screenshot_ready = True

        _helpers._save_screenshot_disk(png_bytes, self.viewer.reader)

        self.viewer.server.state.status_message = (
            f"Screenshot captured ({target_w}\u00d7{target_h}, SSAA 2x)"
        )
        self.viewer.exports._record_download("Screenshot", "PNG")
        # Reset ready flag after a short delay so next click works
        _helpers._spawn_task(self.viewer.exports._reset_screenshot_flag())

    def apply_view_preset(self, name: Any) -> None:
        """One-click view presets (design refresh 2026, items 3+6): each sets
        representation, material (incl. background), SSAO, and atom labels
        together. Order matters — the label/representation rebuilds recreate
        the actors, so the material is applied after the last rebuild and the
        SSAO pass is (re)configured last.
        """
        name = _helpers._first(name)
        # A preset styles the scene; it does not decide that a protein
        # should be drawn atom-by-atom. Fall back to whatever this file
        # opens in, so applying a preset to a biomolecule keeps the
        # ribbon instead of forcing a 158 s bond inference.
        rep_default = _helpers._default_representation(self.viewer.reader)
        presets = {
            # White background, publication-grade material, no chrome.
            "publication": (rep_default, "scientific", False, False),
            # Dark, glossy, depth-cued — for talks and screenshots.
            "presentation": (rep_default, "cpk_glossy", True, False),
            # Flat material + atom labels — for working analysis.
            "analysis": (rep_default, "matte", False, True),
        }
        p = presets.get(str(name))
        if p is None:
            return  # cleared selection — leave the scene as-is
        rep, mat, ssao, labels = p
        # Set the whole look, then rebuild once: the rebuild applies the
        # SSAO pass and the material and pushes the view. Chaining the four
        # controllers rebuilt the scene twice and pushed it five times
        # (#37-48). The preset's representation is the file's own default,
        # which a cartoon-less structure never gets, so no backbone check.
        state = self.viewer.server.state
        state.view_preset = name
        state.representation_style = rep
        state.structure_color_mode = (
            state.cartoon_color_mode if rep == "cartoon" else state.atom_color_mode
        )
        state.show_atom_labels = bool(labels)
        state.material_preset = mat
        state.background_override = None  # the material's background, as a preset pick does
        state.ssao_enabled = bool(ssao)
        _helpers._rebuild_scene(
            self.viewer.reader, self.viewer.plotter, self.viewer.viewer_state, state
        )
        state.status_message = f"View preset: {name}"

    def run_palette_action(self, action_id: Any) -> None:
        """Dispatch a command-palette action to its controller, then close
        the palette (design refresh 2026). Keep the ids in sync with
        _PALETTE_ACTIONS.
        """
        action_id = _helpers._first(action_id)
        aid = str(action_id)
        if aid.startswith("open:"):
            self.viewer.server.state.palette_open = False
            self.viewer.server.state.palette_query = ""
            self.viewer.display.activate_section(aid[len("open:") :])
            return
        dispatch: dict[str, Callable[[], None]] = {
            "cam_iso": lambda: self.viewer.display.camera_preset("isometric"),
            "cam_top": lambda: self.viewer.display.camera_preset("xy"),
            "cam_front": lambda: self.viewer.display.camera_preset("xz"),
            "cam_side": lambda: self.viewer.display.camera_preset("yz"),
            "cam_reset": lambda: self.viewer.display.camera_preset("reset"),
            "view_pub": lambda: self.viewer.exports.apply_view_preset("publication"),
            "view_pres": lambda: self.viewer.exports.apply_view_preset("presentation"),
            "view_ana": lambda: self.viewer.exports.apply_view_preset("analysis"),
            "mo_homo": lambda: self.viewer.wavefunction.render_frontier("HOMO"),
            "mo_lumo": lambda: self.viewer.wavefunction.render_frontier("LUMO"),
            "energy_diagram": self.viewer.wavefunction.show_energy_diagram,
            "labels": self.viewer.display.toggle_atom_labels,
            "background": self.viewer.display.toggle_background,
            "ui_theme": self.viewer.preferences.toggle_dark_ui,
            "orthographic": self.viewer.display.toggle_orthographic,
            "screenshot": self.viewer.exports.save_screenshot,
            "symmetry": self.viewer.exports.detect_symmetry,
            "symmetrize": self.viewer.editing.edit_symmetrize,
            "exp_obj": lambda: self.viewer.exports.export_geometry("obj"),
            "exp_gltf": lambda: self.viewer.exports.export_geometry("gltf"),
            "exp_xyz": lambda: self.viewer.exports.export_geometry("xyz"),
            "exp_cif": lambda: self.viewer.exports.export_geometry("cif"),
            "exp_pov": lambda: self.viewer.exports.export_geometry("pov"),
            "exp_blend": lambda: self.viewer.exports.export_geometry("blend"),
            "exp_svg": lambda: self.viewer.exports.export_geometry("svg"),
            "exp_cml": lambda: self.viewer.exports.export_geometry("cml"),
            "exp_py": lambda: self.viewer.editing.export_py(),
        }
        fn = dispatch.get(str(action_id))
        self.viewer.server.state.palette_open = False
        self.viewer.server.state.palette_query = ""
        if fn is not None:
            fn()

    def show_profile(self) -> None:
        """Display performance profile statistics."""
        from vibeview.profiler import profiler

        self.viewer.server.state.status_message = profiler.get_summary()

    def detect_symmetry(self) -> None:
        """Detect and display the molecular point group."""
        try:
            from vibeview.symmetry import detect_from_qvf, pg_summary

            # The active reader, which carries any unsaved editor geometry.
            pg = detect_from_qvf(self.viewer.reader)
            if pg:
                self.viewer.server.state.point_group = pg.symbol
                self.viewer.server.state.point_group_details = pg_summary(pg)
                self.viewer.server.state.status_message = f"Point group: {pg.symbol}"
            else:
                self.viewer.server.state.status_message = "Could not detect point group"
        except Exception as e:
            self.viewer.server.state.status_message = f"Symmetry detection error: {e}"

    async def _reset_screenshot_flag(self) -> None:
        await asyncio.sleep(0.5)
        self.viewer.server.state.screenshot_ready = False
        # The client downloaded the data URL when it changed; keeping a
        # multi-megabyte base64 PNG in state afterwards only bloats every
        # later state sync (#37-51). None also lets an identical next
        # capture re-trigger the download.
        self.viewer.server.state.screenshot_data = None
        self.viewer.server.state.flush()

    # ── Controller: geometry export ───────────────────────────────────
    def export_geometry(self, fmt: Any) -> None:
        """Export the current structure geometry to a file.

        Receives a plain string from the client
        (``'obj'``, ``'gltf'``, ``'xyz'``).
        Also handles list-wrapped values for robustness.
        """
        fmt = _helpers._first(fmt)
        import tempfile
        from pathlib import Path

        # XYZ is a text format — build from structure data directly.
        if fmt == "xyz":
            try:
                sdata = self.viewer.reader.read_structure()
                from vibeview.geometry_text import xyz_text

                export_bytes = xyz_text(sdata).encode()
            except Exception as e:
                self.viewer.server.state.status_message = f"XYZ export error: {e}"
                return
            ext = ".xyz"
            mime = "chemical/x-xyz"
        elif fmt == "cif":
            try:
                sdata = self.viewer.reader.read_structure()
                from vibeview.geometry_text import cif_text

                export_bytes = cif_text(sdata).encode()
            except Exception as e:
                self.viewer.server.state.status_message = f"CIF export error: {e}"
                return
            ext = ".cif"
            mime = "chemical/x-cif"
        elif fmt == "pov":
            from vibeview.povray_export import export_povray

            with tempfile.NamedTemporaryFile(suffix=".pov", delete=False, mode="w") as f:
                tmp = f.name
            try:
                export_povray(self.viewer.reader, tmp, style="ball_and_stick")
                with open(tmp, "rb") as f:
                    export_bytes = f.read()
            finally:
                Path(tmp).unlink(missing_ok=True)
            ext = ".pov"
            mime = "text/plain"
        elif fmt == "blend":
            from vibeview.blender_export import export_blender_script

            with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
                tmp = f.name
            try:
                export_blender_script(self.viewer.reader, tmp, style="ball_and_stick")
                with open(tmp, "rb") as f:
                    export_bytes = f.read()
            finally:
                Path(tmp).unlink(missing_ok=True)
            ext = "_blender.py"
            mime = "text/plain"
        elif fmt == "svg":
            from vibeview.export_svg import export_svg

            with tempfile.NamedTemporaryFile(suffix=".svg", delete=False, mode="w") as f:
                tmp = f.name
            try:
                export_svg(self.viewer.reader, tmp, style="ball_and_stick")
                with open(tmp, "rb") as f:
                    export_bytes = f.read()
            finally:
                Path(tmp).unlink(missing_ok=True)
            ext = ".svg"
            mime = "image/svg+xml"
        elif fmt == "cml":
            from vibeview.export_cml import export_cml

            with tempfile.NamedTemporaryFile(suffix=".cml", delete=False, mode="w") as f:
                tmp = f.name
            try:
                export_cml(self.viewer.reader, tmp)
                with open(tmp, "rb") as f:
                    export_bytes = f.read()
            finally:
                Path(tmp).unlink(missing_ok=True)
            ext = ".cml"
            mime = "chemical/x-cml"
        else:
            if fmt not in ("obj", "gltf", "stl"):
                fmt = "obj"
            ext = {"obj": ".obj", "gltf": ".gltf", "stl": ".stl"}[fmt]
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                tmp = f.name
            try:
                # save_graphic only writes vector formats (svg/eps/pdf/ps/tex) —
                # use the dedicated scene exporters for obj/gltf. export_obj
                # writes <stem>.obj (+ .mtl), so strip the suffix for it.
                if fmt == "gltf":
                    self.viewer.plotter.export_gltf(tmp)
                elif fmt == "stl":
                    # STL has no scene exporter: merge the visible actors'
                    # surfaces (in world coordinates) into one triangle mesh.
                    # export_obj refuses a .stl name, and would write the
                    # wrong format under it anyway (#37-23).
                    _helpers._scene_surface_mesh(self.viewer.plotter).save(tmp, binary=True)
                else:  # obj
                    self.viewer.plotter.export_obj(tmp)
                with open(tmp, "rb") as f:
                    export_bytes = f.read()
                mime = {"obj": "model/obj", "gltf": "model/gltf+json", "stl": "model/stl"}[fmt]
            finally:
                Path(tmp).unlink(missing_ok=True)

        self.viewer.exports._offer_download(
            export_bytes,
            mime,
            f"vibe-view-structure{ext}",
            label=f"Export ({fmt.upper()})",
            fmt=fmt.upper(),
        )
        # Also write to disk next to the QVF file.
        _helpers._save_export_disk(export_bytes, self.viewer.reader, fmt)
        self.viewer.server.state.status_message = f"Exported as {fmt.upper()}"

    def _record_download(self, label: str, fmt: str) -> None:
        """List a delivered file under Recent Exports (newest first, 50 kept)."""
        from datetime import datetime

        state = self.viewer.server.state
        entry = {
            "name": label,
            "path": str(self.viewer.reader.path) if self.viewer.reader.path else "",
            "format": fmt,
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        state.download_history = [entry, *(state.download_history or [])][:50]

    def _offer_download(
        self, payload: bytes, mime: str, filename: str, *, label: str, fmt: str
    ) -> None:
        """Hand ``payload`` to the browser as a download named ``filename``.

        The one copy of what four exporters spelled out (#37-39): the data
        URL the client watches (``export_data``), the file name, the Recent
        Exports entry, and the reset that drops the payload after delivery.
        """
        state = self.viewer.server.state
        state.export_data = f"data:{mime};base64,{base64.b64encode(payload).decode()}"
        state.export_filename = filename
        self.viewer.exports._record_download(label, fmt)
        self.viewer.exports._schedule_export_reset()

    def _schedule_export_reset(self) -> None:
        # Headless controller calls have no running loop. Do not create a
        # coroutine that can never be awaited, and leave the download ready.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.call_later(0.5, self.viewer.exports._clear_export_payload)

    def _clear_export_payload(self) -> None:
        """Drop a delivered download from state (#37-51)."""
        self.viewer.server.state.export_data = None
        self.viewer.server.state.flush()

    # ── Controller: video export dialog launcher ────────────────────
    def open_video_export_dialog(self) -> None:
        """Open the video export dialog, auto-selecting kind from file."""
        state = self.viewer.server.state
        # Default kind to the first animatable section found, or turntable
        if state.video_kind in ("trajectory", "vibration", "orbital"):
            state.video_export_kind = state.video_kind
        else:
            state.video_export_kind = "turntable"
        state.video_export_dialog = True

    # ── Controller: video export (background thread) ────────────────
    def do_video_export(self) -> None:
        """Run video export off the event loop, then update the UI.

        Structured like ``start_hq_render``: the blocking render runs in an
        executor so the viewport stays responsive, and the final state
        (spinner off + status) is written from the async finalize and
        flushed. The previous version ran the render in a bare
        ``threading.Thread`` and set ``video_export_running = False`` /
        ``status_message`` from inside it — but a plain thread's writes are
        never pushed to the client (see ``_live_opt_on_done``: "nothing else
        pushes to the client"), so the export dialog is ``persistent`` on
        ``video_export_running`` and stayed open with the spinner turning
        forever, and the completion/error message never appeared, even though
        the file had already been written.
        """
        import concurrent.futures
        from pathlib import Path

        state = self.viewer.server.state
        fmt = state.video_export_format or "mp4"
        fps = int(state.video_export_fps or 30)
        kind = state.video_export_kind or "turntable"
        # Read on the event loop, not in the render worker.
        vib_mode = int(state.vibration_mode or 0)
        vib_amplitude = float(state.vibration_amplitude or 1.0)

        state.video_export_running = True
        state.video_export_dialog = False

        stem = Path(self.viewer.reader.path).stem if self.viewer.reader.path else "vibe-view"
        ext = fmt if fmt != "frames" else ""
        out = Path(f"{stem}_{kind}.{ext}") if ext else Path(f"{stem}_{kind}")

        def _render() -> Path | None:
            from vibeview.animation import (
                render_orbital_animation,
                render_trajectory_video,
                render_turntable,
                render_vibration_video,
            )

            if kind == "trajectory":
                return render_trajectory_video(self.viewer.reader, out, format=fmt, fps=fps)
            if kind == "vibration":
                return render_vibration_video(
                    self.viewer.reader,
                    out,
                    mode=vib_mode,
                    format=fmt,
                    fps=fps,
                    amplitude=vib_amplitude,
                )
            if kind == "orbital":
                return render_orbital_animation(self.viewer.reader, out, format=fmt, fps=fps)
            if kind == "turntable":
                # Orbits the live plotter's camera: hold the render lock so a
                # section switch cannot rebuild the scene between frames (#37-11).
                with self.viewer._wf_render_lock:
                    return render_turntable(self.viewer.plotter, out, format=fmt, fps=fps)
            return None

        async def _run_and_finalise() -> None:
            loop = asyncio.get_running_loop()
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    result = await loop.run_in_executor(pool, _render)
                if result:
                    if fmt == "frames":
                        n_frames = len(list(result.glob("frame_*.png")))
                        state.status_message = f"Video exported: {n_frames} frames in {result}/"
                    else:
                        state.status_message = f"Video exported: {result.name}"
                else:
                    state.status_message = "Video export failed"
            except Exception as e:  # noqa: BLE001
                state.status_message = f"Video export error: {e}"
            finally:
                state.video_export_running = False
                state.flush()  # async context — nothing else pushes to the client

        _helpers._spawn_task(_run_and_finalise(), run_if_no_loop=True)

    # ── Controller: high-quality raytrace render ───────────────────────
    def start_hq_render(self) -> None:
        """Launch a high-quality OSPRay path-traced render in a background
        thread, updating progress in the UI.  When complete, triggers a
        client-side download of the resulting PNG.

        This is the magazine-cover workflow: the user dials in quality /
        resolution / lighting / material, clicks render, and waits for
        the path tracer to accumulate.  Progress is shown in the dialog;
        the live viewport is untouched.
        """
        import concurrent.futures
        import tempfile
        from pathlib import Path

        from vibeview.raytrace import render_high_quality

        # Parse resolution string "WxH".
        res_str = self.viewer.server.state.hq_render_resolution or "3840x2160"
        try:
            w_str, h_str = res_str.split("x")
            width, height = int(w_str), int(h_str)
        except (ValueError, AttributeError):
            width, height = 3840, 2160

        quality = self.viewer.server.state.hq_render_quality or "high"
        environment = self.viewer.server.state.hq_render_environment or "studio"
        material = self.viewer.server.state.hq_render_material or "cpk"

        self.viewer.server.state.hq_render_running = True
        self.viewer.server.state.hq_render_progress = 0.0
        self.viewer.server.state.hq_render_progress_msg = "Starting render …"

        dof_enabled = bool(self.viewer.server.state.hq_render_dof)
        # We'll write to a temp file, then read it back for download.
        tmp_path = Path(tempfile.mkdtemp()) / "vibe-view-hq-render.png"

        progress: dict[str, Any] = {}

        def _progress_callback(fraction: float, message: str) -> None:
            """Called from the worker thread: record only. Trame state is
            written and flushed by the polling loop on the event loop, since
            a worker thread's state writes never reach the client (#37-10)."""
            progress["fraction"] = fraction
            progress["message"] = message

        def _render_worker() -> str | None:
            """Run the render in a background thread.  Returns None on
            success, or an error message string."""
            try:
                # The path tracer renders the live plotter: hold the render
                # lock so a section switch cannot rebuild the scene
                # mid-render (#37-11).
                with self.viewer._wf_render_lock:
                    render_high_quality(
                        self.viewer.plotter,
                        str(tmp_path),
                        quality=quality,
                        resolution=(width, height),
                        environment=environment,
                        material_style=material,
                        dof_enabled=dof_enabled,
                        progress_callback=_progress_callback,
                    )
                return None
            except Exception as exc:
                return str(exc)

        async def _run_and_finalise() -> None:
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = loop.run_in_executor(pool, _render_worker)
                # Relay worker progress to the client while it renders.
                while True:
                    done, _pending = await asyncio.wait({future}, timeout=0.25)
                    if progress:
                        self.viewer.server.state.hq_render_progress = float(
                            progress.get("fraction", 0.0)
                        )
                        self.viewer.server.state.hq_render_progress_msg = str(
                            progress.get("message", "")
                        )
                        self.viewer.server.state.flush()
                    if done:
                        break
                error = future.result()

            if error:
                self.viewer.server.state.hq_render_running = False
                self.viewer.server.state.hq_render_progress_msg = f"Render failed: {error}"
                self.viewer.server.state.status_message = f"HQ render error: {error}"
                self.viewer.server.state.flush()
                return

            save_disk = bool(self.viewer.server.state.hq_render_save_disk)
            try:
                png_bytes = tmp_path.read_bytes()
            except OSError as exc:
                self.viewer.server.state.hq_render_running = False
                self.viewer.server.state.hq_render_progress_msg = f"Read error: {exc}"
                self.viewer.server.state.status_message = f"HQ render read error: {exc}"
                self.viewer.server.state.flush()
                return
            finally:
                # Clean up the temp file.
                try:
                    tmp_path.unlink(missing_ok=True)
                    tmp_path.parent.rmdir()
                except OSError:
                    pass

            self.viewer.server.state.hq_render_running = False
            self.viewer.server.state.hq_render_progress_msg = f"Done — {width}×{height}"

            if save_disk and self.viewer.reader.path is not None:
                # Write alongside the QVF file.
                from datetime import datetime

                stem = self.viewer.reader.path.stem
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                out = self.viewer.reader.path.parent / f"{stem}_render_{quality}_{ts}.png"
                out.write_bytes(png_bytes)
                self.viewer.server.state.hq_render_saved_path = str(out)
                self.viewer.server.state.status_message = f"HQ render saved → {out.name}"
            else:
                # Browser download (reuse screenshot download trigger).
                b64 = base64.b64encode(png_bytes).decode()
                self.viewer.server.state.screenshot_data = f"data:image/png;base64,{b64}"
                self.viewer.server.state.screenshot_ready = True
                self.viewer.server.state.status_message = (
                    f"HQ render complete ({width}×{height}, {quality} quality)"
                )
                if self.viewer.reader.path is None:
                    self.viewer.server.state.status_message += " — downloaded"
                _helpers._spawn_task(self.viewer.exports._reset_screenshot_flag())
            self.viewer.server.state.flush()  # async context — push the finished state

        _helpers._spawn_task(_run_and_finalise(), run_if_no_loop=True)
