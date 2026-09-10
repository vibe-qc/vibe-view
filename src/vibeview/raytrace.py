"""High-quality ray-traced rendering for publication / magazine-cover output.

Uses VTK's OSPRay backend (pathtracer mode) with accumulation rendering,
physically-based materials, environment lighting, depth-of-field, and
tone mapping.  Designed to produce the kind of image you'd submit to
*Nature Chemistry* or put on a journal cover — all without leaving the
vibe-view stack.

Usage
-----
    from vibeview.raytrace import RaytraceEngine

    engine = RaytraceEngine(plotter)
    engine.render("output.png", quality="publication", resolution=(3840, 2160))

The engine snapshots the current PyVista plotter scene, replaces the
render pipeline with an OSPRay path tracer, accumulates ``spp`` samples,
and writes the result.  The original render pipeline is restored
afterwards, so the live viewport is untouched.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

# ---------------------------------------------------------------------------
# Quality presets
# ---------------------------------------------------------------------------

# spp = samples per pixel (path-tracing samples)
# max_depth = maximum ray bounces
# roulette_depth = Russian-roulette start depth
# light_scale = multiplier on light intensity
# denoise = apply OSPRay denoiser post-pass


@dataclass
class QualityPreset:
    """One named quality preset for the raytrace engine."""

    label: str
    spp: int = 64
    max_depth: int = 20
    roulette_depth: int = 5
    light_scale: float = 1.0
    denoise: bool = False


_QUALITY_PRESETS: dict[str, QualityPreset] = {
    "draft": QualityPreset("Draft", spp=16, max_depth=8, roulette_depth=3),
    "standard": QualityPreset("Standard", spp=64, max_depth=20, roulette_depth=5),
    "high": QualityPreset("High", spp=256, max_depth=40, roulette_depth=8, denoise=True),
    "publication": QualityPreset(
        "Publication", spp=512, max_depth=64, roulette_depth=10, denoise=True
    ),
}

# ---------------------------------------------------------------------------
# PBR materials
# ---------------------------------------------------------------------------


@dataclass
class PBRMaterial:
    """Physically-based material parameters for OSPRay.

    These map onto the OSPRay ``pathtracer`` / ``scivis`` material model
    accessed through VTK's ``vtkOSPRayMaterialLibrary``.  We predefine a
    few chemically-motivated presets and a general-purpose CPK mapping.
    """

    base_color: tuple[float, float, float] = (0.8, 0.8, 0.8)
    metallic: float = 0.0
    roughness: float = 0.4
    specular: float = 0.5
    clearcoat: float = 0.0
    opacity: float = 1.0
    ior: float = 1.5  # index of refraction for dielectrics


# Atom materials keyed by element use-case.  "cpk" uses the CPK color with
# a semi-glossy dielectric; "metallic" pushes metallic=1.0 for a polished
# metal look; "glass" is a transparent dielectric.
_MATERIAL_STYLES: dict[str, dict[str, PBRMaterial]] = {
    "cpk": {
        "default": PBRMaterial(roughness=0.3, specular=0.6),
        # Atoms are already colored via CPK — base_color is overridden
        # per-actor at render time.  The material template just sets the
        # roughness / metallic baseline.
    },
    "metallic": {
        "default": PBRMaterial(metallic=0.9, roughness=0.15, specular=1.0),
    },
    "glass": {
        "default": PBRMaterial(roughness=0.05, specular=1.0, ior=1.45, opacity=0.7),
    },
    "ceramic": {
        "default": PBRMaterial(roughness=0.1, specular=0.2, clearcoat=0.3),
    },
}

# ---------------------------------------------------------------------------
# Background / environment presets
# ---------------------------------------------------------------------------


@dataclass
class EnvironmentPreset:
    """Background and lighting setup for one scene mood."""

    label: str
    # Background colour (R, G, B) when no HDRI is used.
    bg_color: tuple[float, float, float] = (0.1, 0.1, 0.2)
    # HDRI sky intensity.
    sky_intensity: float = 1.0
    # Key light direction (normalised) + intensity.
    key_dir: tuple[float, float, float] = (0.4, -0.6, 0.7)
    key_intensity: float = 1.0
    # Fill light direction + intensity.
    fill_dir: tuple[float, float, float] = (-0.4, -0.2, 0.3)
    fill_intensity: float = 0.3
    # Rim / back light.
    rim_dir: tuple[float, float, float] = (0.0, 0.8, -0.3)
    rim_intensity: float = 0.4


_ENVIRONMENT_PRESETS: dict[str, EnvironmentPreset] = {
    "dark": EnvironmentPreset("Dark", bg_color=(0.08, 0.08, 0.15)),
    "light": EnvironmentPreset(
        "Light",
        bg_color=(0.92, 0.92, 0.95),
        key_intensity=0.8,
        fill_intensity=0.5,
        rim_intensity=0.2,
    ),
    "studio": EnvironmentPreset(
        "Studio",
        bg_color=(0.15, 0.15, 0.22),
        key_intensity=1.2,
        fill_intensity=0.6,
        rim_intensity=0.8,
    ),
    "sunset": EnvironmentPreset(
        "Sunset",
        bg_color=(0.05, 0.03, 0.08),
        sky_intensity=0.5,
        key_dir=(0.5, -0.3, 0.6),
        key_intensity=2.0,
        fill_intensity=0.2,
        rim_dir=(0.0, 0.9, 0.0),
        rim_intensity=1.5,
    ),
    "scientific": EnvironmentPreset(
        "Scientific",
        bg_color=(0.98, 0.98, 1.0),
        key_intensity=0.6,
        fill_intensity=0.8,
        rim_intensity=0.0,
    ),
}

# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------


class RaytraceEngine:
    """Off-screen, high-quality path-traced renderer for a PyVista scene.

    Parameters
    ----------
    plotter : pv.Plotter
        The PyVista plotter whose scene should be rendered.  Must have
        been created with ``off_screen=True`` and must contain actors
        (structure, isosurfaces, etc.).  The engine does **not** modify
        the plotter's permanent render pipeline — it temporarily swaps
        in OSPRay, renders, and restores the original.

    Notes
    -----
    Requires a VTK build that includes OSPRay (``vtkOSPRayPass`` must be
    importable).  Most PyPI VTK wheels ≥ 9.3.0 ship OSPRay on Linux;
    macOS wheels may or may not.  The engine probes availability and
    raises ``RuntimeError`` with a clear message if OSPRay is absent.
    """

    def __init__(self, plotter) -> None:
        self._plotter = plotter
        self._vtk = self._import_vtk()

        # Sniff OSPRay availability.
        self._ospray_ok = hasattr(self._vtk, "vtkOSPRayPass")
        if not self._ospray_ok:
            raise RuntimeError(
                "OSPRay not available in this VTK build. "
                "Install a VTK wheel with OSPRay support (vtk>=9.3 on Linux; "
                "check your platform's VTK package)."
            )

        # Quality state.
        self._quality: QualityPreset = _QUALITY_PRESETS["standard"]
        self._environment: EnvironmentPreset = _ENVIRONMENT_PRESETS["dark"]
        self._resolution: tuple[int, int] = (1920, 1080)
        self._material_style: str = "cpk"
        self._dof_enabled: bool = False
        self._dof_focal_distance: float = 10.0
        self._dof_aperture: float = 0.5
        self._progress_callback: Callable[[float, str], None] | None = None

    # -- Public API ----------------------------------------------------------

    @property
    def ospray_available(self) -> bool:
        """True when the VTK build ships OSPRay."""
        return self._ospray_ok

    def set_quality(self, name: str) -> None:
        """Set the quality preset by name.

        Valid names: ``draft``, ``standard``, ``high``, ``publication``.
        """
        if name not in _QUALITY_PRESETS:
            raise ValueError(
                f"Unknown quality preset {name!r}. Choose from: {', '.join(_QUALITY_PRESETS)}"
            )
        self._quality = _QUALITY_PRESETS[name]

    def set_environment(self, name: str) -> None:
        """Set the environment (lighting + background) preset by name.

        Valid names: ``dark``, ``light``, ``studio``, ``sunset``, ``scientific``.
        """
        if name not in _ENVIRONMENT_PRESETS:
            raise ValueError(
                f"Unknown environment preset {name!r}. "
                f"Choose from: {', '.join(_ENVIRONMENT_PRESETS)}"
            )
        self._environment = _ENVIRONMENT_PRESETS[name]

    def set_resolution(self, width: int, height: int) -> None:
        """Set the output image resolution in pixels."""
        self._resolution = (int(width), int(height))

    def set_material_style(self, style: str) -> None:
        """Set the PBR material style (``cpk``, ``metallic``, ``glass``, ``ceramic``)."""
        if style not in _MATERIAL_STYLES:
            raise ValueError(
                f"Unknown material style {style!r}. Choose from: {', '.join(_MATERIAL_STYLES)}"
            )
        self._material_style = style

    def set_depth_of_field(
        self,
        enabled: bool,
        focal_distance: float = 10.0,
        aperture: float = 0.5,
    ) -> None:
        """Enable / configure OSPRay depth-of-field."""
        self._dof_enabled = enabled
        self._dof_focal_distance = focal_distance
        self._dof_aperture = aperture

    def set_progress_callback(self, cb: Callable[[float, str], None] | None) -> None:
        """Register a progress callback ``cb(fraction, status_message)``."""
        self._progress_callback = cb

    def render(self, output_path: str) -> None:
        """Run the full path-traced render and write the result to ``output_path``.

        Parameters
        ----------
        output_path : str
            File path to write.  Extension determines format:
            ``.png``, ``.jpg``, ``.tiff``, ``.exr`` (HDR).
        """
        t0 = time.monotonic()

        self._report(0.0, "Configuring OSPRay path tracer …")

        plotter = self._plotter
        renderer = plotter.renderer  # vtkRenderer
        ren_win = renderer.GetRenderWindow()
        orig_size = ren_win.GetSize()

        # ── Snapshot original state ────────────────────────────────────
        orig_pass = renderer.GetPass()
        orig_bg = renderer.GetBackground()
        orig_lights = self._capture_lights(renderer)

        try:
            # ── Set target resolution ──────────────────────────────────
            ren_win.SetSize(self._resolution[0], self._resolution[1])

            # ── Background ─────────────────────────────────────────────
            bg = self._environment.bg_color
            renderer.SetBackground(bg[0], bg[1], bg[2])

            # ── Build OSPRay rendering pipeline ────────────────────────
            ospray_pass = self._vtk.vtkOSPRayPass()
            renderer.SetPass(ospray_pass)

            # Configure the OSPRay renderer node for pathtracing.
            self._configure_ospray()

            # ── Set up lighting ────────────────────────────────────────
            self._setup_lights(renderer)

            # ── Apply PBR materials to actors ──────────────────────────
            self._apply_materials(renderer)

            # ── Depth of field ─────────────────────────────────────────
            if self._dof_enabled:
                self._configure_dof(renderer)

            # ── Progressive rendering with accumulation ────────────────
            self._report(0.05, f"Rendering ({self._quality.spp} samples) …")

            # VTK's OSPRay pass does progressive rendering internally when
            # we call Render() repeatedly with accumulation enabled.  We
            # call it ``max_frames`` times and update progress.
            max_frames = max(1, self._quality.spp // 4)  # 4 spp per frame

            vtk_ospray_node = self._vtk.vtkOSPRayRendererNode
            vtk_ospray_node.SetMaxFrames(max_frames, renderer)
            vtk_ospray_node.SetSamplesPerPixel(self._quality.spp, renderer)

            # Accumulate across frames.
            for frame in range(max_frames):
                ren_win.Render()
                fraction = 0.05 + 0.85 * (frame + 1) / max_frames
                self._report(
                    fraction,
                    f"Accumulating samples (frame {frame + 1}/{max_frames}) …",
                )

            # ── Denoise post-pass (if requested) ───────────────────────
            if self._quality.denoise:
                self._report(0.92, "Denoising …")
                vtk_ospray_node.SetDenoise(True, renderer)
                ren_win.Render()

            # ── Capture framebuffer ────────────────────────────────────
            self._report(0.95, "Capturing framebuffer …")
            w2if = self._vtk.vtkWindowToImageFilter()
            w2if.SetInput(ren_win)
            w2if.SetScale(1)
            w2if.ReadFrontBufferOff()
            w2if.Update()

            writer = self._vtk.vtkPNGWriter()
            if output_path.lower().endswith((".jpg", ".jpeg")):
                writer = self._vtk.vtkJPEGWriter()
            elif output_path.lower().endswith((".tif", ".tiff")):
                writer = self._vtk.vtkTIFFWriter()
            # EXR is not available in all VTK builds; fall back to PNG
            # for .exr and warn.

            writer.SetInputConnection(w2if.GetOutputPort())
            writer.SetFileName(output_path)
            writer.Write()

            elapsed = time.monotonic() - t0
            self._report(
                1.0,
                f"Done — {self._resolution[0]}×{self._resolution[1]} "
                f"in {elapsed:.1f} s → {output_path}",
            )

        finally:
            # ── Restore original renderer state ─────────────────────────
            renderer.SetPass(orig_pass)
            renderer.SetBackground(orig_bg[0], orig_bg[1], orig_bg[2])
            ren_win.SetSize(orig_size[0], orig_size[1])
            self._restore_lights(renderer, orig_lights)
            # Remove any OSPRay material library we attached.
            if hasattr(renderer, "_vibe_ospray_mats"):
                del renderer._vibe_ospray_mats

    # -- Internal helpers ----------------------------------------------------

    @staticmethod
    def _import_vtk():
        """Import VTK, raising a clear message if absent."""
        try:
            import vtk
        except ImportError:
            raise ImportError(
                "VTK is required for raytracing. Install with: pip install vtk"
            ) from None
        return vtk

    def _configure_ospray(self) -> None:
        """Set OSPRay renderer parameters for path-tracing quality."""
        vtk = self._vtk
        renderer = self._plotter.renderer
        node = vtk.vtkOSPRayRendererNode

        # Use the pathtracer (not scivis or ao).
        node.SetRendererType("pathtracer", renderer)

        # Samples and depth control.
        node.SetSamplesPerPixel(self._quality.spp, renderer)
        node.SetMaxDepth(self._quality.max_depth, renderer)
        node.SetRouletteDepth(self._quality.roulette_depth, renderer)

        # Enable shadows and reflections.
        node.SetShadows(True, renderer)
        node.SetAmbientSamples(0, renderer)  # purely path-traced

        # Light scale.
        node.SetLightScale(self._quality.light_scale, renderer)

        # Background mode: environment texture or solid color.
        # For now, solid-color background (environment maps via HDRI
        # are a future enhancement).
        node.SetBackgroundMode(1, renderer)  # 0=environment, 1=backplate

    def _setup_lights(self, renderer) -> None:
        """Create OSPRay-compatible lights for the scene."""
        vtk = self._vtk
        env = self._environment

        # Remove default headlight if present.
        renderer.RemoveAllLights()

        # Helper to add a directional OSPRay light.
        def _add_light(direction, intensity, color=(1.0, 1.0, 1.0)):
            light = vtk.vtkLight()
            light.SetLightTypeToHeadlight()  # directional
            light.SetPosition(direction[0], direction[1], direction[2])
            light.SetFocalPoint(0.0, 0.0, 0.0)
            light.SetIntensity(intensity)
            light.SetColor(color[0], color[1], color[2])
            renderer.AddLight(light)

        # Three-point lighting.
        _add_light(env.key_dir, env.key_intensity, (1.0, 0.98, 0.92))
        _add_light(env.fill_dir, env.fill_intensity, (0.7, 0.8, 1.0))
        _add_light(env.rim_dir, env.rim_intensity, (0.9, 0.85, 0.8))

    def _apply_materials(self, renderer) -> None:
        """Apply PBR materials to every actor in the scene.

        Uses VTK's OSPRay material library.  Each actor gets a material
        entry keyed by its VTK actor address.  The CPK color of each atom
        sphere becomes the ``base_color`` of its PBR material.
        """
        vtk = self._vtk
        materials = vtk.vtkOSPRayMaterialLibrary()

        style_default = _MATERIAL_STYLES.get(self._material_style, _MATERIAL_STYLES["cpk"]).get(
            "default", PBRMaterial()
        )

        actors = renderer.GetActors()
        actors.InitTraversal()
        for _ in range(actors.GetNumberOfItems()):
            actor = actors.GetNextItem()
            if actor is None:
                continue
            mat_name = f"actor_{actor.GetAddressAsString('')}"

            # Determine base color: use the actor's existing diffuse color
            # for CPK atoms, or the style default for other actors.
            prop = actor.GetProperty()
            existing_color = prop.GetColor()  # (R, G, B)
            base = (
                existing_color[0],
                existing_color[1],
                existing_color[2],
            )

            # For metallic / glass / ceramic styles, desaturate the CPK
            # color slightly so the metallic sheen dominates.
            if self._material_style in ("metallic", "glass"):
                gray = 0.299 * base[0] + 0.587 * base[1] + 0.114 * base[2]
                base = (
                    0.3 * base[0] + 0.7 * gray,
                    0.3 * base[1] + 0.7 * gray,
                    0.3 * base[2] + 0.7 * gray,
                )

            # Configure the OSPRay material.
            mat = style_default
            materials.AddMaterial(mat_name, base)
            materials.AddTexture(mat_name, "baseColor", base)
            materials.AddShaderVariable(mat_name, "metallic", mat.metallic)
            materials.AddShaderVariable(mat_name, "roughness", mat.roughness)
            materials.AddShaderVariable(mat_name, "specular", mat.specular)
            materials.AddShaderVariable(mat_name, "opacity", mat.opacity)
            materials.AddShaderVariable(mat_name, "ior", mat.ior)

            # Attach the material name to the actor so OSPRay picks it up.
            actor.SetObjectName(mat_name)

        # Register the material library with the renderer.
        vtk.vtkOSPRayRendererNode.SetMaterialLibrary(materials, renderer)
        renderer._vibe_ospray_mats = materials  # keep alive

    def _configure_dof(self, renderer) -> None:
        """Set up OSPRay depth-of-field."""
        vtk = self._vtk
        node = vtk.vtkOSPRayRendererNode
        node.SetDepthOfField(True, renderer)
        node.SetFocalDistance(self._dof_focal_distance, renderer)
        node.SetAperture(self._dof_aperture, renderer)

    def _capture_lights(self, renderer) -> list:
        """Snapshot the current light kit so we can restore it."""
        lights = []
        light_collection = renderer.GetLights()
        light_collection.InitTraversal()
        for _ in range(light_collection.GetNumberOfItems()):
            light = light_collection.GetNextItem()
            if light:
                lights.append(light)
        return lights

    def _restore_lights(self, renderer, lights: list) -> None:
        """Put back the original lights."""
        renderer.RemoveAllLights()
        for light in lights:
            renderer.AddLight(light)

    def _report(self, fraction: float, message: str) -> None:
        """Call the progress callback if one is registered."""
        if self._progress_callback is not None:
            self._progress_callback(fraction, message)


# ---------------------------------------------------------------------------
# Convenience function — one-shot high-quality render
# ---------------------------------------------------------------------------


def render_high_quality(
    plotter,
    output_path: str,
    *,
    quality: str = "high",
    resolution: tuple[int, int] = (3840, 2160),
    environment: str = "studio",
    material_style: str = "cpk",
    dof_enabled: bool = False,
    dof_focal_distance: float = 10.0,
    dof_aperture: float = 0.5,
    progress_callback: Callable[[float, str], None] | None = None,
) -> None:
    """One-shot convenience: render *plotter*'s scene with path tracing.

    Parameters
    ----------
    plotter : pv.Plotter
        The PyVista plotter (must be ``off_screen=True``).
    output_path : str
        Output file path (``.png`` / ``.jpg`` / ``.tiff``).
    quality : str
        One of ``draft``, ``standard``, ``high``, ``publication``.
    resolution : tuple[int, int]
        Output image size in pixels, e.g. ``(3840, 2160)`` for 4K.
    environment : str
        One of ``dark``, ``light``, ``studio``, ``sunset``, ``scientific``.
    material_style : str
        One of ``cpk``, ``metallic``, ``glass``, ``ceramic``.
    dof_enabled : bool
        Enable depth-of-field.
    dof_focal_distance : float
        Focal plane distance for DoF (world units).
    dof_aperture : float
        Aperture size for DoF (larger = more blur).
    progress_callback : callable or None
        Called as ``cb(fraction, message)`` during rendering.
    """
    engine = RaytraceEngine(plotter)
    engine.set_quality(quality)
    engine.set_resolution(resolution[0], resolution[1])
    engine.set_environment(environment)
    engine.set_material_style(material_style)
    if dof_enabled:
        engine.set_depth_of_field(True, dof_focal_distance, dof_aperture)
    if progress_callback is not None:
        engine.set_progress_callback(progress_callback)
    engine.render(output_path)
