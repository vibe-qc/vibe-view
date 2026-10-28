"""Live frames reuse glyphs and update connectivity without moving stored atoms."""
import numpy as np
import pyvista as pv

from vibeview.headless import headless_plotter
from vibeview.renderers.moving_structure import MovingStructure


def test_reuses_inputs_and_tracks_bond_formation_and_breaking():
    with headless_plotter() as plotter:
        moving = MovingStructure([6, 6, 6])
        positions = np.array([[0., 0., 0.], [1.4, 0., 0.], [6., 0., 0.]])
        moving.update(plotter, positions)
        atoms, bonds = moving.atoms, moving.bonds
        source = atoms.GetMapper().GetInputDataObject(1, 0)
        assert bonds.GetMapper().GetInput().GetNumberOfPoints() == 1
        positions[2, 0] = 2.8
        moving.update(plotter, positions)
        assert moving.atoms is atoms and moving.bonds is bonds
        assert atoms.GetMapper().GetInputDataObject(1, 0) is source
        assert bonds.GetMapper().GetInput().GetNumberOfPoints() == 2
        np.testing.assert_allclose(pv.wrap(atoms.GetMapper().GetInput()).points, positions)
        moving.update(plotter, positions * 10)
        assert not bonds.GetVisibility()
        moving.update(plotter, positions)
        assert bonds.GetVisibility()
        plotter.add_mesh(pv.Sphere(), name="traj_atom_0")
        moving.update(plotter, positions)
        assert "traj_atom_0" not in plotter.renderer.actors
        plotter.clear_actors()
        moving.update(plotter, positions)
        assert len(plotter.renderer.actors) == 2


def test_periodic_bond_uses_short_image():
    with headless_plotter() as plotter:
        moving = MovingStructure([6, 6])
        moving.update(plotter, np.array([[0.1, 0, 0], [9, 0, 0]]),
                      lattice=np.eye(3) * 10, pbc=(True, False, False))
        points = pv.wrap(moving.bonds.GetMapper().GetInput())
        np.testing.assert_allclose(points['vv_glyph_scale'][:, 0], [1.1], atol=1e-6)
