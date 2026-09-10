"""Material presets for vibe-view rendering (v1.6).

Provides configurable material styles for atoms, bonds, isosurfaces,
and backgrounds.  Includes presets for glossy, matte, glass, metallic,
and toon shading modes.

Which effects the user actually sees (audited 2026-07-17)
---------------------------------------------------------
The live viewport is drawn **client-side** by vtk.js: the server ships a
serialised scene (``render_window_serializer``) containing actors, their
properties, the camera and the lights. That boundary decides whether an
effect is visible in the viewport:

* **Actor properties reach the client.** ``apply_material_to_actor`` and
  toon shading set colour/specular/edge properties, so the Material Style
  dropdown and the toon toggle genuinely change the live view.
* **Renderer passes do not.** Anything wired with ``renderer.SetPass(...)``
  — SSAO, OSPRay, shadow maps — is absent from the serialised scene, so it
  can only shape *server-side* output: exported screenshots and the
  high-quality render. Presenting such an effect as a live-view toggle
  misleads the user (SSAO did exactly that, silently, for a long time).

So: before adding a rendering effect, check which side of that line it falls
on, and label it accordingly. A ``setup_shadow_pass`` helper was removed here
in 2026-07 — it was never called, never added its pass to the chain
(``docs/audit_2026_07_02.md`` finding 4), and being a pass could not have
reached the viewport anyway.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MaterialPreset:
    """One named material preset."""

    name: str
    # Atom properties
    atom_roughness: float = 0.3
    atom_metallic: float = 0.0
    atom_specular: float = 0.5
    atom_specular_power: float = 60.0
    # Bond properties
    bond_color: tuple[float, float, float] = (0.4, 0.4, 0.4)
    bond_roughness: float = 0.4
    # Isosurface properties
    iso_ambient: float = 0.2
    iso_diffuse: float = 0.6
    iso_specular: float = 0.3
    iso_specular_power: float = 40.0
    iso_opacity: float = 0.65
    # Background
    background_color: tuple[float, float, float] = (0.1, 0.1, 0.18)
    # Toon shading
    toon_mode: bool = False
    toon_levels: int = 4
    toon_outline: bool = False
    outline_width: float = 1.0


# ── Preset library ──────────────────────────────────────────────────────

MATERIAL_PRESETS: dict[str, MaterialPreset] = {
    "cpk_glossy": MaterialPreset(
        name="CPK Glossy (default)",
        atom_roughness=0.25,
        atom_metallic=0.05,
        atom_specular=0.6,
        atom_specular_power=80.0,
        iso_ambient=0.15,
        iso_diffuse=0.7,
        iso_specular=0.4,
        iso_specular_power=50.0,
        iso_opacity=0.65,
        background_color=(0.10, 0.10, 0.18),
    ),
    "matte": MaterialPreset(
        name="Matte",
        atom_roughness=0.8,
        atom_metallic=0.0,
        atom_specular=0.1,
        atom_specular_power=20.0,
        bond_color=(0.5, 0.5, 0.5),
        bond_roughness=0.8,
        iso_ambient=0.3,
        iso_diffuse=0.5,
        iso_specular=0.05,
        iso_opacity=0.7,
        background_color=(0.95, 0.95, 1.0),
    ),
    "glass": MaterialPreset(
        name="Glass / Translucent",
        atom_roughness=0.05,
        atom_metallic=0.0,
        atom_specular=1.0,
        atom_specular_power=200.0,
        iso_ambient=0.1,
        iso_diffuse=0.3,
        iso_specular=0.8,
        iso_specular_power=120.0,
        iso_opacity=0.35,
        background_color=(0.05, 0.05, 0.10),
    ),
    "metallic": MaterialPreset(
        name="Metallic",
        atom_roughness=0.15,
        atom_metallic=0.9,
        atom_specular=1.0,
        atom_specular_power=100.0,
        bond_color=(0.7, 0.7, 0.75),
        bond_roughness=0.2,
        iso_ambient=0.1,
        iso_diffuse=0.4,
        iso_specular=0.7,
        iso_specular_power=80.0,
        iso_opacity=0.6,
        background_color=(0.08, 0.08, 0.15),
    ),
    "toon": MaterialPreset(
        name="Toon / NPR",
        atom_roughness=0.5,
        atom_metallic=0.0,
        atom_specular=0.2,
        atom_specular_power=30.0,
        toon_mode=True,
        toon_levels=4,
        toon_outline=True,
        outline_width=1.5,
        iso_ambient=0.1,
        iso_diffuse=0.6,
        iso_specular=0.1,
        iso_opacity=0.7,
        background_color=(1.0, 1.0, 1.0),
    ),
    "scientific": MaterialPreset(
        name="Scientific / Publication",
        atom_roughness=0.2,
        atom_metallic=0.0,
        atom_specular=0.3,
        atom_specular_power=40.0,
        bond_color=(0.3, 0.3, 0.3),
        bond_roughness=0.3,
        iso_ambient=0.2,
        iso_diffuse=0.7,
        iso_specular=0.2,
        iso_opacity=0.6,
        background_color=(0.98, 0.98, 1.0),
    ),
}


def get_preset(name: str) -> MaterialPreset:
    """Get a material preset by name. Falls back to 'cpk_glossy'."""
    return MATERIAL_PRESETS.get(name, MATERIAL_PRESETS["cpk_glossy"])


def list_presets() -> list[dict]:
    """Return all available presets as a list of {value, title} dicts."""
    return [{"value": k, "title": v.name} for k, v in MATERIAL_PRESETS.items()]


# ── PBR helpers ─────────────────────────────────────────────────────────


def apply_material_to_actor(
    actor,
    preset: MaterialPreset,
    base_color: tuple[float, float, float] | None = None,
    is_bond: bool = False,
) -> None:
    """Apply a material preset to a PyVista/VTK actor.

    Uses VTK's property system to set roughness, metallic, specular.
    OSPRay materials require additional VTK calls.
    """
    prop = actor.GetProperty() if hasattr(actor, "GetProperty") else None
    if prop is None:
        return

    if is_bond:
        color = preset.bond_color
        # Mixed-order batched bonds use direct RGB cell scalars so one actor
        # can retain several order colours. A material preset intentionally
        # makes every bond uniform, so let its actor property take precedence
        # just as it does for the established per-bond actors.
        mapper = actor.GetMapper() if hasattr(actor, "GetMapper") else None
        if (
            mapper is not None
            and hasattr(mapper, "GetArrayName")
            and mapper.GetArrayName() == "bond_rgb"
        ):
            mapper.SetScalarVisibility(False)
    elif base_color:
        color = base_color
    else:
        color = (0.5, 0.5, 0.5)

    prop.SetColor(*color)
    prop.SetSpecular(preset.atom_specular if not is_bond else 0.3)
    prop.SetSpecularPower(preset.atom_specular_power if not is_bond else 30.0)
    prop.SetAmbient(0.2)
    prop.SetDiffuse(0.7)

    # Roughness is approximated through specular power
    if preset.atom_roughness > 0.5:
        prop.SetSpecular(0.1)
        prop.SetSpecularPower(10)

    # Toon outline
    if preset.toon_outline:
        prop.SetEdgeVisibility(True)
        prop.SetEdgeColor(0, 0, 0)
        prop.SetLineWidth(preset.outline_width)
    else:
        prop.SetEdgeVisibility(False)


def ambient_occlusion_pass(
    plotter,
    radius: float = 2.0,
    samples: int = 32,
) -> bool:
    """Add an ambient occlusion pass to the renderer. True if it applied.

    **Server-side only.** This attaches a VTK render pass to the renderer,
    and render passes are not part of the scene the client's VtkLocalView
    receives — ``render_window_serializer`` emits actors, properties, camera
    and lights, so a scene serialised with and without this pass is identical
    (verified 2026-07-17). SSAO therefore shapes what the *server* draws —
    exported screenshots and the high-quality render — and leaves the live
    vtk.js viewport untouched.

    Historical bug: this called ``ssao.SetSamples(samples)``, which
    vtkSSAOPass has never had (its sample count is ``SetKernelSize``), so the
    call raised AttributeError before ``SetPass`` — and the ``except
    ImportError`` here could not catch it. SSAO never applied at all, in any
    render. Callers wrap this in a broad ``except``, so the failure was
    silent and the UI switch read "on" while nothing happened.
    """
    try:
        import vtk

        renderer = plotter.renderer
        ssao = vtk.vtkSSAOPass()
        # Occlusion radius in world units. The visible magnitude depends
        # on camera distance and scene scale (pixel-diff measurements of
        # 0.5 vs 2.0 invert between sessions), so the long-standing 2.0
        # is retained rather than tuned on unstable evidence.
        ssao.SetRadius(radius)
        # Sample count *is* the kernel size for vtkSSAOPass; larger is
        # smoother but slower. Clamped to keep server renders responsive.
        ssao.SetKernelSize(max(4, min(int(samples), 256)))

        # Chain with existing render pass
        basic_passes = vtk.vtkRenderStepsPass()
        ssao.SetDelegatePass(basic_passes)

        # OSPRay interop: if OSPRay is active, attach SSAO before it
        # by moving the OSPRay pass to the SSAO pass's delegate chain.
        # This is approximate — full integration needs upstream VTK work.
        renderer.SetPass(ssao)
        return True
    except Exception:  # noqa: BLE001 — SSAO is optional polish, never fatal
        return False
