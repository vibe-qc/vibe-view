"""The client-side silhouette pass: what the server marks, and what the page
receives (#112). The pass itself runs in vtk.js; the served-browser lane in
``test_design_refresh.py`` reads back its frames.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vibeview.app.silhouette import (
    SILHOUETTE_JS,
    STATE_KEY,
    TARGET_PREFIXES,
    publish_silhouette_targets,
    silhouette_targets,
)

H2 = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"


def _plotter():
    import pyvista as pv

    return pv.Plotter(off_screen=True)


class TestTargets:
    def _scene(self):
        import pyvista as pv

        p = _plotter()
        lobe = pv.Sphere(radius=1.0, center=(2.0, 0.0, 0.0))
        p.add_mesh(lobe, name="mo_iso_pos")
        p.add_mesh(pv.Sphere(radius=0.8, center=(-2.0, 0.0, 0.0)), name="mo_iso_neg", opacity=0.5)
        p.add_mesh(pv.Sphere(radius=0.5), name="volume_density_0")
        p.add_mesh(pv.Tube(pointa=(0, 0, 0), pointb=(0, 0, 5)), name="cartoon_chain_A")
        p.add_mesh(pv.Sphere(radius=0.3), name="atom_0_0")
        p.add_mesh(pv.Cylinder(), name="bond_0_1")
        p.add_mesh(pv.Sphere(radius=0.35), name="outline_atom_0_0")
        hidden = p.add_mesh(pv.Sphere(radius=0.2), name="fermi_sheet_0")
        hidden.SetVisibility(False)
        grid = pv.ImageData(dimensions=(4, 4, 4))
        grid.point_data["v"] = np.arange(64, dtype=float)
        p.add_volume(grid, scalars="v", name="volume_render_density")
        return p, lobe

    def test_visible_surfaces_are_marked_translucent_ones_included(self):
        p, lobe = self._scene()
        try:
            targets = silhouette_targets(p)
            counts = sorted(int(t[0]) for t in targets)
            expected = sorted(
                p.actors[name].GetMapper().GetInput().GetNumberOfPoints()
                for name in ("mo_iso_pos", "mo_iso_neg", "volume_density_0", "cartoon_chain_A")
            )
            assert counts == expected
            # A signature is the point count and the bounds the client sees.
            lobe_entry = next(t for t in targets if t[1] > 0.5)
            assert lobe_entry[0] == lobe.n_points
            assert np.allclose(lobe_entry[1:], lobe.bounds)
        finally:
            p.close()

    def test_atoms_bonds_hulls_volumes_and_hidden_surfaces_are_left_out(self):
        p, _lobe = self._scene()
        try:
            marked = {tuple(t) for t in silhouette_targets(p)}
            assert len(marked) == 4
            for name in ("atom_0_0", "bond_0_1", "outline_atom_0_0", "fermi_sheet_0"):
                data = p.actors[name].GetMapper().GetInput()
                signature = (float(data.GetNumberOfPoints()), *map(float, data.GetBounds()))
                assert signature not in marked, name
        finally:
            p.close()

    def test_list_is_sorted_and_stable(self):
        p, _lobe = self._scene()
        try:
            first = silhouette_targets(p)
            assert first == sorted(first)
            assert silhouette_targets(p) == first
        finally:
            p.close()

    def test_empty_scene_has_no_targets(self):
        p = _plotter()
        try:
            assert silhouette_targets(p) == []
        finally:
            p.close()

    def test_prefixes_cover_isosurfaces_and_ribbons(self):
        assert {"volume_", "mo_iso_", "fermi_", "traj_volume_", "cartoon_"} == set(TARGET_PREFIXES)


class _State(dict):
    """The parts of a trame state ``publish_silhouette_targets`` uses."""

    entered = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *exc):
        return False


class TestPublish:
    def test_publishes_only_on_change(self):
        import pyvista as pv

        p = _plotter()
        state = _State({STATE_KEY: []})
        try:
            assert publish_silhouette_targets(state, p) is False
            p.add_mesh(pv.Sphere(), name="mo_iso_pos")
            assert publish_silhouette_targets(state, p) is True
            assert len(state[STATE_KEY]) == 1 and state.entered == 1
            assert publish_silhouette_targets(state, p) is False
            p.remove_actor("mo_iso_pos")
            assert publish_silhouette_targets(state, p) is True
            assert state[STATE_KEY] == []
        finally:
            p.close()


class TestViewer:
    @pytest.fixture
    def app(self):
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        return create_app(QVFReader(H2))

    def test_an_orbital_push_publishes_its_lobes(self, app):
        assert app.state[STATE_KEY] == []
        app.controller.activate_section("wf")
        app.state.wf_selected_mo = app.state.wf_mo_rows[0]["value"]
        app.controller.render_mo(app.state.wf_selected_mo)
        targets = app.state[STATE_KEY]
        assert targets and all(len(t) == 7 and t[0] > 0 for t in targets)

    def test_the_publication_preset_turns_outlines_on(self, app):
        app.controller.apply_view_preset("publication")
        assert app.state.figure_outlines is True


@pytest.fixture(scope="module")
def served():
    pytest.importorskip("trame")
    from tests._served import served_viewer

    return served_viewer()


class TestServedPage:
    def test_the_pass_ships_in_the_page(self, served):
        assert "window.vvSilhouette = {" in served
        assert "'vvSilhouettePass'" in served
        assert "uniform highp sampler2D uDepth;" in served
        assert "'silhouette_targets', FLAG = 'figure_outlines'" in served

    def test_it_installs_after_each_scene_load(self, served):
        tag = served.split('@afterSceneLoaded="', 1)[1].split('"', 1)[0]
        assert "window.vvSilhouette.install()" in tag
        assert "window.vvLabelFollow.rescan()" in tag

    def test_it_never_deletes_a_vtk_program(self):
        """The shader cache's releaseCurrentShaderProgram() deletes the
        program; the pass must only unbind it."""
        assert "releaseCurrentShaderProgram" not in SILHOUETTE_JS.split("// (The cache")[0]
        assert "shaderCache.setLastShaderProgramBound(null)" in SILHOUETTE_JS

    def test_it_leaves_picking_alone(self):
        assert "gl.getParameter(gl.FRAMEBUFFER_BINDING) !== null) return;" in SILHOUETTE_JS
