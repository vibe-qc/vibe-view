"""Publication mode: the one-click figure look (#68).

The Publication view preset sets the scientific material, turns SSAO and
labels off, outlines every atom and bond with an inverted hull and hides the
viewport chrome. Clearing the picker restores the look the first preset
overwrote, and the look survives a file reload. ``vibe-view capture
--publication`` reaches the same look headlessly, and the UI theme's accent
meets WCAG AA in both themes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_H2 = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"


def _outline_names(plotter) -> set[str]:
    return {str(n) for n in plotter.actors if str(n).startswith("outline_")}


def _atom_actor(plotter):
    return next(
        a
        for n, a in plotter.actors.items()
        if str(n).startswith("atom_") and "labels" not in str(n)
    )


def _app_with_plotter(monkeypatch, readers):
    """``create_app`` plus the plotter it built, captured through the factory."""
    import vibeview.app as appmod

    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    monkeypatch.setattr(appmod.pv, "Plotter", create_plotter)
    server = appmod.create_app(readers)
    server.controller.view_update = lambda *_args, **_kwargs: None
    return server, created[0]


# ── The outline hull itself ──────────────────────────────────────────────


def test_outline_hull_is_a_closed_offset_of_the_mesh():
    import numpy as np
    import pyvista as pv

    from vibeview.material_presets import outline_hull

    sphere = pv.Sphere(radius=0.5, theta_resolution=16, phi_resolution=16)
    hull = outline_hull(sphere, thickness=0.05)
    radii = np.linalg.norm(hull.points, axis=1)
    assert radii.min() == pytest.approx(0.55, abs=0.01)
    assert radii.max() == pytest.approx(0.55, abs=0.01)
    assert hull.n_open_edges == 0, "the hull must stay closed, or the outline shows gaps"
    # A cylinder duplicates its rim vertices; the hull merges them so the
    # enlarged cap and the enlarged side still meet.
    cylinder = pv.Cylinder(radius=0.1, height=1.0, resolution=12)
    assert outline_hull(cylinder, thickness=0.05).n_open_edges == 0


def test_add_figure_outlines_hulls_atoms_and_bonds_only(water_qvf):
    import pyvista as pv

    from vibeview.headless import add_structure, headless_plotter
    from vibeview.material_presets import (
        add_figure_outlines,
        outlined_actor_names,
        remove_figure_outlines,
    )
    from vibeview.qvf import QVFReader

    reader = QVFReader(water_qvf)
    try:
        with headless_plotter((200, 150)) as plotter:
            assert add_structure(plotter, reader, show_labels=True)
            plotter.add_mesh(pv.Sphere(radius=0.3), name="mo_iso_test")
            plotter.add_mesh(pv.Sphere(radius=0.3), name="cell_wireframe", style="wireframe")
            outlined = outlined_actor_names(plotter)
            assert outlined, "water has atom and bond actors to outline"
            assert all(n.startswith(("atom", "bond")) for n in outlined)
            assert not any("labels" in n for n in outlined)

            count = add_figure_outlines(plotter)
            assert count == len(outlined)
            assert _outline_names(plotter) == {f"outline_{n}" for n in outlined}
            hull = plotter.actors[f"outline_{outlined[0]}"]
            prop = hull.GetProperty()
            assert prop.GetFrontfaceCulling() == 1, "only the back faces draw: the outline"
            assert prop.GetLighting() == 0
            assert prop.GetColor() == pytest.approx((0.0, 0.0, 0.0))
            # Applying again replaces rather than stacks.
            assert add_figure_outlines(plotter) == count
            assert len(_outline_names(plotter)) == count
            assert remove_figure_outlines(plotter) == count
            assert not _outline_names(plotter)
    finally:
        reader.close()


def test_removing_an_actor_group_removes_its_outlines(water_qvf):
    import vibeview.app as app
    from vibeview.headless import add_structure, headless_plotter
    from vibeview.material_presets import OUTLINE_ACTOR_PREFIX, add_figure_outlines
    from vibeview.qvf import QVFReader

    assert app._Actors.OUTLINE == OUTLINE_ACTOR_PREFIX
    reader = QVFReader(water_qvf)
    try:
        with headless_plotter((200, 150)) as plotter:
            assert add_structure(plotter, reader)
            add_figure_outlines(plotter)
            assert any(n.startswith("outline_atom") for n in _outline_names(plotter))
            assert any(n.startswith("outline_bond") for n in _outline_names(plotter))
            app._remove_actors_by_prefix(plotter, app._Actors.ATOM)
            names = _outline_names(plotter)
            assert not any(n.startswith("outline_atom") for n in names)
            assert any(n.startswith("outline_bond") for n in names)
    finally:
        reader.close()


# ── The Publication preset in the browser ────────────────────────────────


def test_publication_preset_sets_the_figure_look(water_qvf, monkeypatch):
    from vibeview.material_presets import outlined_actor_names
    from vibeview.qvf import QVFReader

    reader = QVFReader(water_qvf)
    try:
        server, plotter = _app_with_plotter(monkeypatch, reader)
        state = server.state
        before = {
            "material_preset": state.material_preset,
            "show_atom_labels": bool(state.show_atom_labels),
            "ssao_enabled": bool(state.ssao_enabled),
        }
        assert state.figure_outlines is False
        assert state.chrome_hidden is False
        assert state.view_preset_restore is None

        server.controller.apply_view_preset(["publication"])  # as the picker sends it

        assert state.view_preset == "publication"
        assert state.material_preset == "scientific"
        assert state.ssao_enabled is False
        assert state.show_atom_labels is False
        assert state.figure_outlines is True
        assert state.chrome_hidden is True
        assert state.dark_background is False
        assert plotter.renderer.cube_axes_actor is None, "the bounds grid is chrome"
        assert _outline_names(plotter) == {f"outline_{n}" for n in outlined_actor_names(plotter)}
        assert _outline_names(plotter), "the preset outlines the atoms and bonds"
        restore = state.view_preset_restore
        assert isinstance(restore, dict)
        for key, value in before.items():
            assert restore[key] == value, key
        assert restore["figure_outlines"] is False
        assert restore["chrome_hidden"] is False
    finally:
        reader.close()


def test_clearing_the_preset_restores_the_previous_look(water_qvf, monkeypatch):
    import vibeview.app as app
    from vibeview.qvf import QVFReader

    reader = QVFReader(water_qvf)
    try:
        server, plotter = _app_with_plotter(monkeypatch, reader)
        state = server.state
        server.controller.set_material_preset("matte")
        server.controller.toggle_atom_labels(True)
        background_before = state.background_override

        rebuilds: list[int] = []
        pushes: list[int] = []
        real_rebuild, real_push = app._rebuild_scene, app._push_view
        monkeypatch.setattr(
            app._helpers, "_rebuild_scene",
            lambda *a, **k: (rebuilds.append(1), real_rebuild(*a, **k))[1]
        )
        monkeypatch.setattr(app._helpers, "_push_view", lambda p: (pushes.append(1), real_push(p)))

        server.controller.apply_view_preset("publication")
        assert (len(rebuilds), len(pushes)) == (1, 1)
        assert state.material_preset == "scientific"
        assert _outline_names(plotter)

        server.controller.apply_view_preset([None])  # the picker's clear button
        assert (len(rebuilds), len(pushes)) == (2, 2), "the restore is one rebuild and one push"
        assert state.view_preset is None
        assert state.view_preset_restore is None
        assert state.material_preset == "matte"
        assert state.show_atom_labels is True
        assert state.background_override == background_before
        assert state.figure_outlines is False
        assert state.chrome_hidden is False
        assert plotter.renderer.cube_axes_actor is not None, "the grid is back"
        assert not _outline_names(plotter)
        assert "restored" in state.status_message

        # Clearing with nothing applied leaves the scene alone.
        server.controller.apply_view_preset(None)
        assert (len(rebuilds), len(pushes)) == (2, 2)
    finally:
        reader.close()


def test_switching_presets_keeps_the_first_restore_point(water_qvf, monkeypatch):
    from vibeview.qvf import QVFReader

    reader = QVFReader(water_qvf)
    try:
        server, plotter = _app_with_plotter(monkeypatch, reader)
        state = server.state
        server.controller.set_material_preset("glass")
        server.controller.apply_view_preset("publication")
        first = dict(state.view_preset_restore)
        server.controller.apply_view_preset("analysis")
        assert state.view_preset_restore == first, "the second preset keeps the first snapshot"
        assert state.material_preset == "matte"
        assert state.figure_outlines is False
        assert state.chrome_hidden is False
        assert not _outline_names(plotter)
        server.controller.apply_view_preset(None)
        assert state.material_preset == "glass"
    finally:
        reader.close()


def test_publication_look_survives_a_file_reload(water_qvf, monkeypatch):
    from vibeview.material_presets import get_preset, outlined_actor_names
    from vibeview.qvf import QVFReader

    readers = [QVFReader(water_qvf), QVFReader(_H2)]
    try:
        server, plotter = _app_with_plotter(monkeypatch, readers)
        state = server.state
        server.controller.apply_view_preset("publication")
        old_outlines = _outline_names(plotter)
        old_atom = _atom_actor(plotter)

        server.controller.switch_file(1)  # drives _reload_active_file

        assert state.view_preset == "publication"
        assert state.material_preset == "scientific"
        assert state.figure_outlines is True
        assert state.chrome_hidden is True
        assert plotter.renderer.cube_axes_actor is None, "the reload keeps the grid down"
        new_atom = _atom_actor(plotter)
        assert new_atom is not old_atom
        outlines = _outline_names(plotter)
        assert outlines == {f"outline_{n}" for n in outlined_actor_names(plotter)}
        assert outlines, "the reloaded scene is outlined again"
        assert outlines != old_outlines or len(readers[1].read_structure().atoms) == len(
            readers[0].read_structure().atoms
        )
        assert plotter.renderer.GetBackground() == pytest.approx(
            get_preset("scientific").background_color, abs=1 / 255
        )
        assert new_atom.GetProperty().GetSpecular() == pytest.approx(
            get_preset("scientific").atom_specular
        )
    finally:
        for reader in readers:
            reader.close()


def test_toggles_stand_alone_and_reach_the_palette(water_qvf, monkeypatch):
    from vibeview.qvf import QVFReader

    reader = QVFReader(water_qvf)
    try:
        server, plotter = _app_with_plotter(monkeypatch, reader)
        state = server.state
        ctrl = server.controller

        ctrl.toggle_figure_outlines([True])  # the switch sends [$event]
        assert state.figure_outlines is True
        assert _outline_names(plotter)
        ctrl.toggle_figure_outlines()
        assert state.figure_outlines is False
        assert not _outline_names(plotter)

        assert plotter.renderer.cube_axes_actor is not None
        ctrl.toggle_measure_mode(True)
        ctrl.toggle_chrome([True])
        assert state.chrome_hidden is True
        assert state.measure_mode is False, "pick markers are chrome: hiding it leaves measure mode"
        assert plotter.renderer.cube_axes_actor is None
        ctrl.toggle_chrome(False)
        assert plotter.renderer.cube_axes_actor is not None
        ctrl.toggle_edit_mode()
        assert state.edit_mode is True
        ctrl.run_palette_action("chrome")
        assert state.chrome_hidden is True
        assert state.edit_mode is False
        ctrl.run_palette_action("chrome")
        assert state.chrome_hidden is False

        ctrl.run_palette_action("outlines")
        assert state.figure_outlines is True
        ctrl.run_palette_action("view_pub")
        assert state.view_preset == "publication"
        ctrl.run_palette_action("view_clear")
        assert state.view_preset is None
        assert state.figure_outlines is True, "the look before the preset had outlines on"
    finally:
        reader.close()


def test_the_layout_gates_exactly_the_chrome_behind_the_switch():
    """The hidden set is a contract with the manual: the three app-bar chips,
    the hover tooltip and the welcome card, and nothing else."""
    import vibeview.app.layout as layout

    source = Path(layout.__file__).read_text(encoding="utf-8")
    source = re.sub(r'"\s*\n\s*"', "", source)  # join implicit string concatenations
    gated = re.findall(r'"([^"]*) && !chrome_hidden"', source)
    assert sorted(gated) == sorted(
        [
            "live_run_status",  # the sr-only announcement
            "live_run_status",  # the job-status chip
            "status_message",
            "session_path",
            "hover_tooltip_visible",
            "!selected_section && welcome_visible && sidebar_entries.length > 0",
        ]
    ), gated


# ── Headless capture ──────────────────────────────────────────────────────


def test_capture_publication_reaches_the_figure_look(water_qvf, tmp_path):
    from click.testing import CliRunner

    from vibeview.cli import main
    from vibeview.headless import (
        add_structure,
        apply_publication_look,
        headless_plotter,
        render_structure_frame,
    )
    from vibeview.material_presets import get_preset, outlined_actor_names
    from vibeview.qvf import QVFReader

    reader = QVFReader(water_qvf)
    try:
        dark = render_structure_frame(reader, None, size=(160, 120))
        light = render_structure_frame(reader, None, size=(160, 120), publication=True)
        assert dark[0, 0, :3].max() < 80, "the default headless background is dark"
        assert light[0, 0, :3].min() >= 240, "the publication background is near white"

        with headless_plotter((160, 120)) as plotter:
            assert add_structure(plotter, reader)
            apply_publication_look(plotter)
            assert _outline_names(plotter) == {
                f"outline_{n}" for n in outlined_actor_names(plotter)
            }
            preset = get_preset("scientific")
            assert _atom_actor(plotter).GetProperty().GetSpecular() == pytest.approx(
                preset.atom_specular
            )
            assert plotter.renderer.GetBackground() == pytest.approx(
                preset.background_color, abs=1 / 255
            )
    finally:
        reader.close()

    out = tmp_path / "figure.png"
    result = CliRunner().invoke(
        main, ["capture", str(water_qvf), "--publication", "-o", str(out), "--size", "160x120"]
    )
    assert result.exit_code == 0, result.output
    assert out.stat().st_size > 100


# ── Theme and accent pass ─────────────────────────────────────────────────


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """WCAG 2.1 contrast ratio, (L1 + 0.05) / (L2 + 0.05)."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_theme_accent_meets_wcag_aa_in_both_themes():
    """The light block sets the shared accent; the dark block overrides it.
    Every text-on-background pair the accent takes part in reads at 4.5:1."""
    import vibeview.app.layout as layout

    source = Path(layout.__file__).read_text(encoding="utf-8")

    def tokens(name: str) -> list[tuple[int, int, int]]:
        found = re.findall(rf"--v-theme-{name}:(\d+),(\d+),(\d+)", source)
        return [(int(r), int(g), int(b)) for r, g, b in found]

    primary, on_primary = tokens("primary"), tokens("on-primary")
    assert len(primary) == 2 and len(on_primary) == 2, "a shared accent and a dark override"
    light_primary, dark_primary = primary
    light_on_primary, dark_on_primary = on_primary
    (dark_surface,) = tokens("surface")
    (dark_background,) = tokens("background")
    (dark_on_surface,) = tokens("on-surface")
    white = (255, 255, 255)

    assert _contrast(light_primary, white) >= 4.5
    assert _contrast(light_on_primary, light_primary) >= 4.5
    assert _contrast(dark_primary, dark_surface) >= 4.5
    assert _contrast(dark_primary, dark_background) >= 4.5
    assert _contrast(dark_on_primary, dark_primary) >= 4.5
    assert _contrast(dark_on_surface, dark_surface) >= 4.5
    assert _contrast(dark_on_surface, dark_background) >= 4.5
