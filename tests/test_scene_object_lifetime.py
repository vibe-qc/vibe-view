"""A scene replacement must not alias VTK IDs still held by vtk.js."""

import gc
import weakref

import pytest
import pyvista as pv

from vibeview.app import _retaining_view_updater


def test_previous_scene_lives_until_replacement_is_sent():
    plotter = pv.Plotter(off_screen=True)
    actor = plotter.add_mesh(pv.Sphere(), name="atom")
    old = weakref.ref(actor)
    seen = []
    update = _retaining_view_updater(plotter, lambda: seen.append(old() is not None))
    del actor
    plotter.clear()
    gc.collect()
    assert old() is not None
    plotter.add_mesh(pv.Cube(), name="replacement")
    update()
    assert seen == [True]
    gc.collect()
    assert old() is None
    plotter.close()


def test_failed_push_retains_previous_scene():
    plotter = pv.Plotter(off_screen=True)
    actor = plotter.add_mesh(pv.Sphere())
    old = weakref.ref(actor)

    def fail():
        raise RuntimeError("serialization failed")

    update = _retaining_view_updater(plotter, fail)
    del actor
    plotter.clear()
    with pytest.raises(RuntimeError):
        update()
    gc.collect()
    assert old() is not None
    plotter.close()
