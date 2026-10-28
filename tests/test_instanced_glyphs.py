"""Instanced atom and bond glyphs for large structures (roadmap: nanotubes)."""

from __future__ import annotations

import numpy as np
import pytest

pv = pytest.importorskip("pyvista")

from vibeview import nanotube as nt  # noqa: E402
from vibeview.qvf import QVFReader  # noqa: E402
from vibeview.qvf_writer import structure_archive  # noqa: E402
from vibeview.renderers import structure as srender  # noqa: E402


def _reader(tmp_path, tube):
    path = structure_archive(tube.atoms(), tube.pbc, tube.lattice).write(tmp_path / "t.qvf")
    return QVFReader(path)


@pytest.fixture
def scene(tmp_path):
    tube = nt.single_wall(10, 10, 40.0, end="hydrogen")  # 720 atoms: instanced
    reader = _reader(tmp_path, tube)
    plotter = pv.Plotter(off_screen=True)
    section = next(s for s in reader.sections if s.kind == "structure")
    srender.StructureRenderer(section, reader).add_to_plotter(plotter)
    yield tube, plotter
    plotter.close()
    reader.close()


def test_large_structures_draw_one_instanced_actor_each_for_atoms_and_bonds(scene):
    tube, plotter = scene
    atoms = plotter.actors["atom_group_0"].GetMapper()
    bonds = plotter.actors["bonds_batched_0"].GetMapper()
    assert srender.is_glyph_mapper(atoms) and srender.is_glyph_mapper(bonds)
    points = atoms.GetInputDataObject(0, 0)
    assert points.GetNumberOfPoints() == tube.n_atoms
    rgb = pv.wrap(points).point_data[srender.GLYPH_RGB_ARRAY]
    hydrogen = np.array(tube.symbols) == "H"
    assert len({tuple(c) for c in rgb[hydrogen]}) == 1
    assert not np.array_equal(rgb[hydrogen][0], rgb[~hydrogen][0])
    bond_points = pv.wrap(bonds.GetInputDataObject(0, 0))
    assert bond_points.point_data[srender.GLYPH_SCALE_ARRAY].shape[1] == 3
    assert srender.GLYPH_DIRECTION_ARRAY in bond_points.point_data


def test_expanded_geometry_matches_what_is_drawn(scene):
    tube, plotter = scene
    mesh = pv.wrap(srender.actor_geometry(plotter.actors["atom_group_0"]))
    source = plotter.actors["atom_group_0"].GetMapper().GetInputDataObject(1, 0)
    assert mesh.n_points == tube.n_atoms * source.GetNumberOfPoints()
    lo, hi = tube.positions.min(axis=0), tube.positions.max(axis=0)
    bounds = np.array(mesh.bounds).reshape(3, 2)
    assert np.all(bounds[:, 0] < lo) and np.all(bounds[:, 1] > hi)
    assert np.all(bounds[:, 0] > lo - 1.0) and np.all(bounds[:, 1] < hi + 1.0)
    bonds = pv.wrap(srender.actor_geometry(plotter.actors["bonds_batched_0"]))
    # Every bond cylinder lies between atoms: its points are within the
    # atoms' box grown by the bond radius.
    bb = np.array(bonds.bounds).reshape(3, 2)
    assert np.all(bb[:, 0] > lo - 0.2) and np.all(bb[:, 1] < hi + 0.2)


def test_mesh_export_expands_instances(scene):
    from vibeview.app.helpers import _scene_surface_mesh

    _tube, plotter = scene
    mesh = _scene_surface_mesh(plotter)
    assert mesh.n_cells > 10_000


def test_small_structures_keep_the_merged_path(tmp_path):
    tube = nt.single_wall(5, 5, 10.0, end="open")
    assert tube.n_atoms < srender.INSTANCED_ATOM_THRESHOLD
    reader = _reader(tmp_path, tube)
    plotter = pv.Plotter(off_screen=True)
    try:
        section = next(s for s in reader.sections if s.kind == "structure")
        srender.StructureRenderer(section, reader).add_to_plotter(plotter)
        assert not any(srender.is_glyph_mapper(a.GetMapper()) for a in plotter.actors.values()
                       if hasattr(a, "GetMapper"))
    finally:
        plotter.close()
        reader.close()


def test_cutaway_filters_the_drawn_instances(tmp_path):
    tube = nt.single_wall(10, 10, 80.0)
    reader = _reader(tmp_path, tube)
    plotter = pv.Plotter(off_screen=True)
    try:
        section = next(s for s in reader.sections if s.kind == "structure")
        srender.StructureRenderer(section, reader).add_to_plotter(plotter, cutaway="half")
        drawn = plotter.actors["atom_group_0"].GetMapper().GetInputDataObject(0, 0)
        assert 0.4 * tube.n_atoms < drawn.GetNumberOfPoints() < 0.6 * tube.n_atoms
    finally:
        plotter.close()
        reader.close()


def test_headless_capture_plumbs_cutaway_and_property_colours(tmp_path):
    from vibeview.headless import add_structure, headless_plotter

    tube = nt.single_wall(10, 10, 80, end="open")
    with _reader(tmp_path, tube) as reader, headless_plotter() as plotter:
        add_structure(plotter, reader, cutaway="half", atom_color_mode="coordination")
        points = pv.wrap(plotter.actors['atom_group_0'].GetMapper().GetInput())
        assert 500 <= points.n_points < tube.n_atoms
        assert len(np.unique(points[srender.GLYPH_RGB_ARRAY], axis=0)) > 1
