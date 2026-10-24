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
  can only shape *server-side* output: screenshots exported with the server
  render, videos and the high-quality render. Presenting such an effect as a
  live-view toggle misleads the user (SSAO did exactly that, silently, for a
  long time).

So: before adding a rendering effect, check which side of that line it falls
on, and label it accordingly. A ``setup_shadow_pass`` helper was removed here
in 2026-07 — it was never called, never added its pass to the chain
(``docs/audit_2026_07_02.md`` finding 4), and being a pass could not have
reached the viewport anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MaterialPreset:
    """One named material preset."""

    name: str
    # Atom properties. Roughness above 0.5 flattens the highlight; the
    # client renders in vtk.js, which has no PBR metallic channel, so a
    # preset describes its look through specular terms only.
    atom_roughness: float = 0.3
    atom_specular: float = 0.5
    atom_specular_power: float = 60.0
    # Bond properties
    bond_color: tuple[float, float, float] = (0.4, 0.4, 0.4)
    # Isosurface lighting. Opacity stays with the isosurface sliders.
    iso_ambient: float = 0.2
    iso_diffuse: float = 0.6
    iso_specular: float = 0.3
    iso_specular_power: float = 40.0
    # Background
    background_color: tuple[float, float, float] = (0.1, 0.1, 0.18)
    # Toon outlines. Flat shading itself is the viewer's toon switch.
    toon_outline: bool = False
    outline_width: float = 1.0


# ── Preset library ──────────────────────────────────────────────────────

MATERIAL_PRESETS: dict[str, MaterialPreset] = {
    "cpk_glossy": MaterialPreset(
        name="CPK Glossy (default)",
        atom_roughness=0.25,
        atom_specular=0.6,
        atom_specular_power=80.0,
        iso_ambient=0.15,
        iso_diffuse=0.7,
        iso_specular=0.4,
        iso_specular_power=50.0,
        background_color=(0.10, 0.10, 0.18),
    ),
    "matte": MaterialPreset(
        name="Matte",
        atom_roughness=0.8,
        atom_specular=0.1,
        atom_specular_power=20.0,
        bond_color=(0.5, 0.5, 0.5),
        iso_ambient=0.3,
        iso_diffuse=0.5,
        iso_specular=0.05,
        background_color=(0.95, 0.95, 1.0),
    ),
    "glass": MaterialPreset(
        name="Glass / Translucent",
        atom_roughness=0.05,
        atom_specular=1.0,
        atom_specular_power=200.0,
        iso_ambient=0.1,
        iso_diffuse=0.3,
        iso_specular=0.8,
        iso_specular_power=120.0,
        background_color=(0.05, 0.05, 0.10),
    ),
    "metallic": MaterialPreset(
        name="Metallic",
        atom_roughness=0.15,
        atom_specular=1.0,
        atom_specular_power=100.0,
        bond_color=(0.7, 0.7, 0.75),
        iso_ambient=0.1,
        iso_diffuse=0.4,
        iso_specular=0.7,
        iso_specular_power=80.0,
        background_color=(0.08, 0.08, 0.15),
    ),
    "toon": MaterialPreset(
        name="Toon / NPR",
        atom_roughness=0.5,
        atom_specular=0.2,
        atom_specular_power=30.0,
        toon_outline=True,
        outline_width=1.5,
        iso_ambient=0.1,
        iso_diffuse=0.6,
        iso_specular=0.1,
        background_color=(1.0, 1.0, 1.0),
    ),
    "scientific": MaterialPreset(
        name="Scientific / Publication",
        atom_roughness=0.2,
        atom_specular=0.3,
        atom_specular_power=40.0,
        bond_color=(0.3, 0.3, 0.3),
        iso_ambient=0.2,
        iso_diffuse=0.7,
        iso_specular=0.2,
        background_color=(0.98, 0.98, 1.0),
    ),
}


def get_preset(name: str) -> MaterialPreset:
    """Get a material preset by name. Falls back to 'cpk_glossy'."""
    return MATERIAL_PRESETS.get(name, MATERIAL_PRESETS["cpk_glossy"])


def list_presets() -> list[dict[str, str]]:
    """Return all available presets as a list of {value, title} dicts."""
    return [{"value": k, "title": v.name} for k, v in MATERIAL_PRESETS.items()]


# ── PBR helpers ─────────────────────────────────────────────────────────


def apply_material_to_actor(
    actor: Any,
    preset: MaterialPreset,
    base_color: tuple[float, float, float] | None = None,
    is_bond: bool = False,
    is_isosurface: bool = False,
) -> None:
    """Apply a material preset to a PyVista/VTK actor.

    Atoms take the preset's atom terms, bonds its bond colour with fixed
    specular terms, and isosurfaces (``is_isosurface``) its ``iso_*``
    lighting. Uses VTK's classic lighting model, which is what reaches the
    vtk.js client.
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
    if is_isosurface:
        prop.SetAmbient(preset.iso_ambient)
        prop.SetDiffuse(preset.iso_diffuse)
        prop.SetSpecular(preset.iso_specular)
        prop.SetSpecularPower(preset.iso_specular_power)
    else:
        prop.SetAmbient(0.2)
        prop.SetDiffuse(0.7)
        prop.SetSpecular(preset.atom_specular if not is_bond else 0.3)
        prop.SetSpecularPower(preset.atom_specular_power if not is_bond else 30.0)
        # Roughness is approximated through specular power
        if not is_bond and preset.atom_roughness > 0.5:
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
    plotter: Any,
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


# ── Figure outlines (#68) ──────────────────────────────────────────────────

#: World-unit thickness of a figure outline, in the structure's own units
#: (ångström). A world unit rather than a pixel width: vtk.js draws every
#: line one pixel wide whatever ``SetLineWidth`` says, and a drawn outline
#: should scale with the molecule as the camera zooms.
FIGURE_OUTLINE_THICKNESS = 0.05

#: Name prefix of the outline hull actors: ``outline_<outlined actor name>``.
#: ``vibeview.app`` removes a hull together with the group of the actor it
#: outlines, so the prefix is also ``_Actors.OUTLINE`` there.
OUTLINE_ACTOR_PREFIX = "outline_"

#: Actor-name prefixes that receive an outline: atoms and bonds, including
#: the glyph-batched large-system actors (``atom_group_``, ``bonds_``).
#: Isosurfaces, ribbons, cell edges, labels and markers are left alone.
_OUTLINED_PREFIXES = ("atom", "bond")


def outline_hull(mesh: Any, thickness: float = FIGURE_OUTLINE_THICKNESS) -> Any:
    """``mesh`` pushed outward along its vertex normals by ``thickness``.

    The inverted-hull outline of Raskar and Cohen, "Image precision
    silhouette edges", Proc. I3D 1999: drawn with its front faces culled, the
    enlarged copy shows only where it pokes out past the original's
    silhouette, which is an outline of constant world width from every
    camera. Being geometry rather than a render pass, it reaches the vtk.js
    client and stays correct as the client rotates. Duplicate vertices are
    merged first so a cylinder's cap rim moves as one and the hull stays
    closed.
    """
    import pyvista as pv

    surface = pv.wrap(mesh).clean()
    with_normals = surface.compute_normals(
        split_vertices=False, consistent_normals=True, auto_orient_normals=False
    )
    hull = with_normals.copy()
    hull.points = with_normals.points + thickness * with_normals.point_data["Normals"]
    return hull


def outlined_actor_names(plotter: Any) -> list[str]:
    """The names of the actors a figure outline applies to, in scene order."""
    names: list[str] = []
    for name in getattr(plotter, "actors", {}):
        label = str(name)
        if label.startswith(OUTLINE_ACTOR_PREFIX) or "labels" in label:
            continue
        if label.startswith(_OUTLINED_PREFIXES):
            names.append(label)
    return names


def remove_figure_outlines(plotter: Any) -> int:
    """Remove every outline hull; returns how many there were."""
    removed = 0
    for name in list(getattr(plotter, "actors", {})):
        if str(name).startswith(OUTLINE_ACTOR_PREFIX):
            plotter.remove_actor(name)
            removed += 1
    return removed


def add_figure_outlines(plotter: Any, thickness: float = FIGURE_OUTLINE_THICKNESS) -> int:
    """Outline every atom and bond actor with a black inverted hull.

    Replaces the outlines already present, so a rebuilt scene gets hulls
    around its new actors and none around actors that are gone. Returns how
    many actors were outlined. An actor whose geometry cannot be offset is
    skipped rather than failing the whole look.
    """
    remove_figure_outlines(plotter)
    count = 0
    for name in outlined_actor_names(plotter):
        actor = plotter.actors[name]
        mapper = actor.GetMapper() if hasattr(actor, "GetMapper") else None
        source = mapper.GetInput() if mapper is not None else None
        if source is None:
            continue
        try:
            hull = outline_hull(source, thickness)
        except Exception:  # noqa: BLE001 — an un-outlinable actor is not an error
            continue
        plotter.add_mesh(
            hull,
            color="black",
            lighting=False,
            specular=0.0,
            culling="front",
            pickable=False,
            name=f"{OUTLINE_ACTOR_PREFIX}{name}",
            show_scalar_bar=False,
            reset_camera=False,
        )
        count += 1
    return count
