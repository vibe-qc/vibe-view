"""Real VTK scene actor ownership, capture and browser-controller acceptance."""
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import numpy as np
import pytest
import pyvista as pv
from PIL import Image
from trame.app import get_server

from vibeview.headless import headless_plotter
from vibeview.model_scene_panel import add_current, bind, build_controls, execute, render, restore
from vibeview.qvf import QVFReader


def test_model_visibility_capture_and_restore(water_qvf, monkeypatch, tmp_path):
    from vibeview.app import helpers
    monkeypatch.setattr(helpers, '_push_view', Mock())
    server = get_server('scene-' + uuid4().hex, client_type='vue3')
    with QVFReader(water_qvf) as reader, headless_plotter(size=(320, 240)) as plotter:
        original = plotter.add_mesh(pv.Sphere(radius=.2), name='atom_original')
        viewer = SimpleNamespace(server=server, ctrl=server.controller, reader=reader,
                                 plotter=plotter, exports=SimpleNamespace(_offer_download=Mock()))
        bind(viewer)
        state = server.state
        add_current(viewer)
        assert not original.visibility
        assert any(name.startswith('working-scene-0-atom_') for name in plotter.actors)
        state.model_scene_name = 'second'
        add_current(viewer)
        state.model_scene_command = 'move second 3 0 0'
        execute(viewer)
        assert any(name.startswith('working-scene-1-atom_') for name in plotter.actors)
        state.model_scene_command = 'hide second'
        execute(viewer)
        assert not any(name.startswith('working-scene-1-') for name in plotter.actors)
        state.model_scene_command = 'show second'
        execute(viewer)
        plotter.view_isometric()
        plotter.reset_camera()
        state.model_scene_command = 'save scene.json'
        execute(viewer)
        saved = viewer.exports._offer_download.call_args.args[0]
        assert 'view_camera' in json.loads(saved)
        original_image = plotter.screenshot(return_img=True)
        state.model_scene_command = 'capture scene.png'
        execute(viewer)
        payload, mime, filename = viewer.exports._offer_download.call_args.args
        assert mime == 'image/png' and filename == 'scene.png'
        image = np.asarray(Image.open(io.BytesIO(payload)))
        assert image.shape[:2] == (240, 320) and np.ptp(image) > 0
        (tmp_path / 'scene.png').write_bytes(payload)
        state.model_scene_command = 'remove second'
        execute(viewer)
        state.model_scene_script = saved.decode()
        restore(viewer)
        assert state.model_scene_result == 'Scene restored'
        assert 'second' in viewer._model_scene_commands.scene.models
        restored_image = plotter.screenshot(return_img=True)
        np.testing.assert_array_equal(original_image, restored_image)
        state.model_scene_enabled = False
        render(viewer)
        assert original.visibility
        assert not any(name.startswith('working-scene-') for name in plotter.actors)


def test_invalid_restore_and_command_keep_scene(water_qvf, monkeypatch):
    from vibeview.app import helpers
    monkeypatch.setattr(helpers, '_push_view', Mock())
    server = get_server('scene-' + uuid4().hex, client_type='vue3')
    with QVFReader(water_qvf) as reader, headless_plotter(size=(100, 100)) as plotter:
        viewer = SimpleNamespace(server=server, ctrl=server.controller, reader=reader,
                                 plotter=plotter, exports=SimpleNamespace(_offer_download=Mock()))
        bind(viewer)
        add_current(viewer)
        before = viewer._model_scene_commands.scene.script()
        server.state.model_scene_command = 'move model1 nan 0 0'
        execute(viewer)
        assert 'failed' in server.state.model_scene_result
        assert viewer._model_scene_commands.scene.script() == before
        server.state.model_scene_script = '{bad json'
        restore(viewer)
        assert 'failed' in server.state.model_scene_result
        assert viewer._model_scene_commands.scene.script() == before


def test_translated_periodic_cell_draws_at_transformed_origin(water_qvf, monkeypatch):
    from vibeview.app import helpers
    monkeypatch.setattr(helpers, '_push_view', Mock())
    server = get_server('scene-' + uuid4().hex, client_type='vue3')
    with QVFReader(water_qvf) as reader, headless_plotter(size=(100, 100)) as plotter:
        viewer = SimpleNamespace(server=server, ctrl=server.controller, reader=reader,
                                 plotter=plotter, exports=SimpleNamespace(_offer_download=Mock()))
        bind(viewer)
        structure = reader.read_structure()
        structure.pbc, structure.dim = (True, True, True), 3
        structure.lattice_vectors = np.diag([3., 4, 5])
        structure.bonds = []
        scene = viewer._model_scene_commands.scene
        scene.add('cell', structure)
        scene.transform('cell', np.eye(3), np.array([10., 20, 30]))
        server.state.model_scene_enabled = True
        render(viewer)
        actor = plotter.actors['working-scene-0-cell_wireframe']
        assert actor.bounds == pytest.approx((10, 13, 20, 24, 30, 35))


def test_controls_template():
    from trame.widgets import html
    from trame.widgets import vuetify3 as v
    server = get_server('scene-template-' + uuid4().hex, client_type='vue3')
    with html.Div(trame_server=server) as root:
        build_controls(v, html, server.controller)
    template = str(root)
    for key in ['model_scene_command', 'model_scene_history', 'model_scene_script',
                'model_scene_enabled']:
        assert key in template


def test_registered_app_scene_survives_file_switch(water_qvf, monkeypatch):
    from vibeview import model_scene_panel
    from vibeview.app import create_app
    viewers = []
    original_bind = model_scene_panel.bind

    def capture_viewer(viewer):
        viewers.append(viewer)
        original_bind(viewer)

    monkeypatch.setattr(model_scene_panel, 'bind', capture_viewer)
    with QVFReader(water_qvf) as first, QVFReader(water_qvf) as second:
        server = create_app([first, second])
        viewer = viewers[-1]
        try:
            server.controller.model_scene_add()
            server.state.model_scene_command = 'move model1 5 0 0'
            server.controller.model_scene_execute()
            server.state.flush()
            server.controller.switch_file(1)
            server.state.flush()
            assert any(name.startswith('working-scene-') for name in viewer.plotter.actors)
            assert all(not actor.visibility for name, actor in viewer.plotter.actors.items()
                       if name.startswith('atom_'))
        finally:
            viewer.plotter.close()


@pytest.mark.parametrize('style,coloured', [
    ('wireframe', True), ('sticks_only', True), ('ball_and_stick', False),
])
def test_model_colour_reaches_bond_only_styles(water_qvf, style, coloured):
    """#135 acceptance: `colour b orange` left a wireframe model grey."""
    from matplotlib.colors import to_rgb

    from vibeview.model_scene import ModelScene
    from vibeview.model_scene_panel import draw_models

    with QVFReader(water_qvf) as reader, headless_plotter(size=(100, 100)) as plotter:
        scene = ModelScene()
        scene.add('b', reader.read_structure())
        scene.appearance('b', colour='orange', style=style)
        draw_models(plotter, reader, scene)
        bonds = [actor for name, actor in plotter.actors.items()
                 if name.startswith(('working-scene-0-bond_', 'working-scene-0-bonds_batched_'))]
        assert bonds
        orange = pytest.approx(to_rgb('orange'))
        for actor in bonds:
            assert (tuple(actor.prop.color.float_rgb) == orange) == coloured


def test_command_box_clears_after_success_and_keeps_a_failed_command(water_qvf, monkeypatch):
    """#139: the box kept the last command, so the next typing appended to it."""
    from vibeview.app import helpers
    monkeypatch.setattr(helpers, '_push_view', Mock())
    server = get_server('scene-' + uuid4().hex, client_type='vue3')
    with QVFReader(water_qvf) as reader, headless_plotter(size=(100, 100)) as plotter:
        viewer = SimpleNamespace(server=server, ctrl=server.controller, reader=reader,
                                 plotter=plotter, exports=SimpleNamespace(_offer_download=Mock()))
        bind(viewer)
        state = server.state
        add_current(viewer)
        state.model_scene_command = 'move model1 1 0 0'
        execute(viewer)
        assert state.model_scene_command == ''
        assert state.model_scene_history[0] == 'move model1 1 0 0'
        state.model_scene_command = 'move model1 nan 0 0'
        execute(viewer)
        assert state.model_scene_result.startswith('Command failed')
        assert state.model_scene_command == 'move model1 nan 0 0'
