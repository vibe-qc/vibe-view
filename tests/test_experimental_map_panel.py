from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from tests.test_experimental_map import write_map
from vibeview.experimental_map import map_to_qvf
from vibeview.experimental_map_panel import build_controls, choose_bin, render
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import structure_archive


def test_level_histogram_zone_and_style(tmp_path, monkeypatch):
    from vibeview.app import helpers
    path = tmp_path/'map.mrc'
    write_map(path)
    monkeypatch.setattr(helpers, '_push_view', Mock())
    monkeypatch.setattr(helpers, '_remove_actors_by_prefix', Mock())
    state = SimpleNamespace(expmap_enabled=True, expmap_source='0:map', expmap_zone=False,
                            expmap_level=1, expmap_units='sigma', expmap_style='surface',
                            expmap_radius=3, expmap_opacity=.4, selected_atoms=[], edit_selected=[])
    atoms = [{'symbol':'C', 'atomic_number':6, 'position':[5, 0, 20]}]
    with QVFReader(map_to_qvf(path)) as map_reader, QVFReader(
        structure_archive(atoms, (False, False, False)).to_bytesio()
    ) as model:
        viewer = SimpleNamespace(reader=model, _all_readers=[map_reader, model],
                                 server=SimpleNamespace(state=state), plotter=Mock())
        render(viewer)
        whole = viewer.plotter.add_mesh.call_args.args[0]
        assert whole.n_points > 0
        assert len(state.expmap_histogram) == 40
        assert 'Absolute level 14;' in state.expmap_notice
        state.expmap_style = 'mesh'
        state.expmap_zone = True
        state.selected_atoms = [0]
        render(viewer)
        zone = viewer.plotter.add_mesh.call_args.args[0]
        assert viewer.plotter.add_mesh.call_args.kwargs['style'] == 'wireframe'
        assert np.max(np.linalg.norm(zone.points-[5, 0, 20], axis=1)) <= 3.00001
        state.expmap_zone = False
        choose_bin(viewer, 10)
        assert state.expmap_units == 'absolute'
        assert state.expmap_level == state.expmap_histogram[10]['level']
        state.expmap_level = 'invalid'
        render(viewer)
        assert 'Map:' in state.status_message


def test_panel_template():
    from trame.app import get_server
    from trame.widgets import html
    from trame.widgets import vuetify3 as v
    server = get_server('experimental-map-template', client_type='vue3')
    with html.Div(trame_server=server) as root:
        build_controls(v, html, server.controller)
    template = str(root)
    assert 'expmap_histogram' in template
    assert 'expmap_radius' in template
    assert "['sigma', 'absolute']" in template
