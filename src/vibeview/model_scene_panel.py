"""Multi-model scene rendering and browser command/download adapter."""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import numpy as np

from vibeview.model_scene import ModelScene
from vibeview.qvf import QVFReader, Section, StructureData
from vibeview.scene_commands import SceneCommands

_PREFIX = 'working-scene-'
_BASE_PREFIXES = ('atom_', 'bond_', 'bonds_', 'cartoon_', 'cell_', 'structure_', 'cmp_')


class _NamespacedPlotter:
    def __init__(self, plotter: Any, prefix: str, origin: np.ndarray) -> None:
        self.plotter, self.prefix, self.origin = plotter, prefix, origin

    def __getattr__(self, name: str) -> Any:
        return getattr(self.plotter, name)

    def add_mesh(self, mesh: Any, **kwargs: Any) -> Any:
        name = str(kwargs.get('name', 'mesh'))
        if name.startswith('cell_'):
            mesh = mesh.translate(self.origin, inplace=False)
        kwargs['name'] = self.prefix + name
        return self.plotter.add_mesh(mesh, **kwargs)


def draw_models(plotter: Any, reader: QVFReader, scene: ModelScene) -> None:
    """Replace only owned actors, reusing the standard structure renderer."""
    from matplotlib.colors import to_rgb

    from vibeview.bonds import infer_bonds
    from vibeview.molecular_selection import positions
    from vibeview.renderers.structure import StructureRenderer

    for name in list(plotter.actors):
        if name.startswith(_PREFIX):
            plotter.remove_actor(name, render=False)
    for index, (identifier, model) in enumerate(scene.models.items()):
        if not model.visible:
            continue
        structure = model.structure
        renderer = StructureRenderer(Section(id='working-model', kind='structure'), reader)
        renderer._structure = structure
        renderer._bonds = structure.bonds
        if renderer._bonds is None:
            renderer._bonds = infer_bonds(positions(structure),
                [a.atomic_number for a in structure.atoms],
                lattice=structure.lattice_vectors, pbc=structure.pbc)
        renderer._bonds_loaded = True
        colours = ({i: to_rgb(model.colour) for i in range(len(structure.atoms))}
                   if model.colour else {})
        colours.update({i: (1., .8, 0.) for name, i in scene.selected if name == identifier})
        prefix = f'{_PREFIX}{index}-'
        wrapper = _NamespacedPlotter(plotter, prefix, model.cell_origin)
        renderer.add_to_plotter(wrapper, representation=model.style, atom_colors=colours)  # type: ignore[arg-type]
        # Cartoon colouring is not supplied by the atom-colour renderer argument.
        if model.colour and model.style == 'cartoon':
            for name, actor in plotter.actors.items():
                if name.startswith(prefix + 'cartoon_'):
                    actor.mapper.scalar_visibility = False
                    actor.prop.color = model.colour
        # Nor bond colouring: bond-only styles drew a coloured model grey, so
        # two overlaid wireframe models looked alike (#135 acceptance).
        if model.colour and model.style in ('wireframe', 'sticks_only'):
            for name, actor in plotter.actors.items():
                if name.startswith((prefix + 'bond_', prefix + 'bonds_batched_')):
                    actor.mapper.scalar_visibility = False
                    actor.prop.color = model.colour


def _commands(viewer: Any) -> SceneCommands:
    return viewer._model_scene_commands  # type: ignore[no-any-return]


def render(viewer: Any, *, push: bool = True, **_: Any) -> None:
    from vibeview.app import helpers

    if getattr(viewer, '_model_scene_busy', False):
        return
    viewer._model_scene_busy = True
    try:
        state, plotter = viewer.server.state, viewer.plotter
        hidden = viewer._model_scene_hidden
        if state.model_scene_enabled:
            for name, actor in list(plotter.actors.items()):
                if name.startswith(_BASE_PREFIXES):
                    if name not in hidden or hidden[name][0] is not actor:
                        hidden[name] = (actor, actor.visibility)
                    actor.visibility = False
            draw_models(plotter, viewer.reader, _commands(viewer).scene)
        else:
            for name in list(plotter.actors):
                if name.startswith(_PREFIX):
                    plotter.remove_actor(name, render=False)
            for name, (actor, visible) in hidden.items():
                if plotter.actors.get(name) is actor:
                    actor.visibility = visible
            hidden.clear()
        state.model_scene_models = [
            {'name': name, 'atoms': len(model.structure.atoms), 'visible': model.visible,
             'style': model.style, 'colour': model.colour or 'element'}
            for name, model in _commands(viewer).scene.models.items()]
        state.model_scene_history = list(reversed(_commands(viewer).history))
        if isinstance(state.figure_scene, dict):
            from vibeview.figure_panel import render as render_figure
            render_figure(viewer)
        if push:
            helpers._push_view(plotter)
    finally:
        viewer._model_scene_busy = False


def execute(viewer: Any) -> None:
    state = viewer.server.state
    try:
        result = _commands(viewer).execute(str(state.model_scene_command or ''))
        state.model_scene_result = json.dumps(result, indent=2, allow_nan=False)
        # Ready for the next command; history keeps this one. A failed
        # command stays for correction. Typing used to append (#139).
        state.model_scene_command = ''
        state.model_scene_enabled = True
        render(viewer)
    except (ValueError, TypeError, KeyError, IndexError, OSError) as exc:
        state.model_scene_result = f'Command failed: {exc}'


def add_current(viewer: Any) -> None:
    state = viewer.server.state
    try:
        identifier = str(state.model_scene_name or '').strip()
        structure = viewer.reader.read_structure()
        # Retain reader-owned inferred/explicit connectivity for this snapshot.
        import copy
        structure = copy.deepcopy(structure)
        structure.bonds = viewer.reader.connectivity(structure)
        _commands(viewer).scene.add(identifier, structure)
        state.model_scene_enabled = True
        render(viewer)
        state.model_scene_result = f'Added {identifier}'
    except (ValueError, TypeError, KeyError) as exc:
        state.model_scene_result = f'Add failed: {exc}'


def restore(viewer: Any) -> None:
    from vibeview.app import helpers

    state = viewer.server.state
    try:
        text = str(state.model_scene_script or '')
        data = json.loads(text)
        scene = ModelScene.replay(text) if 'events' in data else ModelScene.from_dict(data)
        camera = data.get('view_camera')
        if camera:
            for key in ('position', 'focal_point', 'view_up'):
                value = np.asarray(camera[key], dtype=float)
                if value.shape != (3,) or not np.isfinite(value).all():
                    raise ValueError('Invalid saved camera')
            for key in ('parallel_scale', 'view_angle'):
                if key in camera and (not np.isfinite(float(camera[key]))
                                      or float(camera[key]) <= 0):
                    raise ValueError('Invalid saved camera projection')
        history = data.get('command_history', [])
        if not isinstance(history, list) or any(not isinstance(row, str) for row in history):
            raise ValueError('Invalid command history')
        from vibeview.figure_scene import FigureScene
        decorations = (FigureScene.from_dict(data['figure_scene'])
                       if 'figure_scene' in data else None)
        _commands(viewer).scene = scene
        _commands(viewer).history = history.copy()
        state.model_scene_enabled = True
        if decorations is not None:
            state.figure_scene = decorations.to_dict()
        render(viewer)
        if camera:
            helpers._apply_camera(viewer.plotter, camera)
            helpers._sync_orthographic_switch(viewer.plotter, state)
        state.model_scene_result = 'Scene restored'
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        state.model_scene_result = f'Restore failed: {exc}'


def bind(viewer: Any) -> None:
    from vibeview.app import helpers
    from vibeview.converters import convert_to_qvf

    state, ctrl = viewer.server.state, viewer.ctrl
    for key, value in {'model_scene_enabled': False, 'model_scene_command': '',
                       'model_scene_result': '', 'model_scene_name': 'model1',
                       'model_scene_models': [], 'model_scene_history': [],
                       'model_scene_script': ''}.items():
        state.setdefault(key, value)

    def load(path: str) -> StructureData:
        filename = Path(path).expanduser()
        source = filename if filename.suffix.lower() == '.qvf' else convert_to_qvf(filename)
        with QVFReader(source) as reader:
            structure = reader.read_structure()
            structure.bonds = reader.connectivity(structure)
            return structure

    def write(filename: str, text: str) -> None:
        data = json.loads(text)
        data['view_camera'] = helpers._camera_to_dict(viewer.plotter)
        data['command_history'] = _commands(viewer).history.copy()
        from vibeview.figure_panel import scene as figure_scene
        data['figure_scene'] = figure_scene(viewer).to_dict()
        payload = json.dumps(data, indent=2, allow_nan=False).encode()
        viewer.exports._offer_download(payload, 'application/json', Path(filename).name,
                                       label='Model scene', fmt='JSON')

    def capture(filename: str) -> None:
        from PIL import Image
        state.model_scene_enabled = True
        render(viewer)
        output = io.BytesIO()
        from vibeview.figure_panel import composite
        from vibeview.figure_panel import render as render_figure
        render_figure(viewer)
        image = Image.fromarray(np.asarray(viewer.plotter.screenshot(return_img=True)))
        composite(viewer, image).save(output, format='PNG')
        viewer.exports._offer_download(output.getvalue(), 'image/png',
                                       Path(filename).with_suffix('.png').name,
                                       label='Model scene', fmt='PNG')

    viewer._model_scene_commands = SceneCommands(load_structure=load, write_text=write,
                                                 capture=capture)
    viewer._model_scene_hidden = {}
    hooks = helpers._scene_hooks(viewer.plotter)
    previous_refresh = hooks.geometry_changed

    def refresh_after_rebuild() -> None:
        if previous_refresh is not None:
            previous_refresh()
        if state.model_scene_enabled and not getattr(viewer, '_model_scene_busy', False):
            actors = viewer.plotter.actors
            base_visible = any(name.startswith(_BASE_PREFIXES) and actor.visibility
                               for name, actor in actors.items())
            owned_missing = (any(model.visible for model in _commands(viewer).scene.models.values())
                             and not any(name.startswith(_PREFIX) for name in actors))
            if base_visible or owned_missing:
                render(viewer, push=False)

    hooks.geometry_changed = refresh_after_rebuild
    ctrl.set('model_scene_execute')(lambda: execute(viewer))
    ctrl.set('model_scene_add')(lambda: add_current(viewer))
    ctrl.set('model_scene_restore')(lambda: restore(viewer))
    state.change('model_scene_enabled', 'active_file_index', 'selected_section')(
        lambda **kw: render(viewer))


def build_controls(v: Any, html: Any, ctrl: Any) -> None:
    with v.VCard(classes='pa-2 mb-2'):
        v.VCardTitle('Working model scene', classes='text-subtitle-1')
        html.Div('Add open structures as models; commands act on this scene.',
                 classes='text-caption')
        v.VTextField(v_model=('model_scene_name',), label='New model name')
        v.VBtn('Add current structure', click=ctrl.model_scene_add, size='small')
        v.VSwitch(v_model=('model_scene_enabled',), label='Show working scene')
        with html.Div(v_for='model in model_scene_models', key=('model.name',)):
            html.Span('{{ model.name }}: {{ model.atoms }} atoms; {{ model.style }}; '
                      '{{ model.visible ? "shown" : "hidden" }}')
        v.VCombobox(v_model=('model_scene_command',), items=('model_scene_history',),
                    label='Command / history', placeholder='select model model1 and element O',
                    __events=[('keyup_enter', 'keyup.enter')], keyup_enter=ctrl.model_scene_execute)
        v.VBtn('Run command', click=ctrl.model_scene_execute, size='small')
        html.Pre('{{ model_scene_result }}',
                 style='white-space:pre-wrap;max-height:200px;overflow:auto')
        html.Div('Commands: open NAME PATH, select EXPR, show/hide NAME, colour/style NAME VALUE, '
                 'move/rotate, align, measure, save FILE, save-script FILE, capture FILE.',
                 classes='text-caption')
        v.VTextarea(v_model=('model_scene_script',), label='Paste saved scene or scene script JSON',
                    rows=3, auto_grow=False)
        v.VBtn('Restore scene / replay script', click=ctrl.model_scene_restore, size='small')
