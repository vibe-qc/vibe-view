"""Durable scene replay, proper transforms and selection across models."""
import json

import numpy as np
import pytest

from vibeview.model_scene import ModelScene
from vibeview.qvf import Atom, StructureData


def molecule(offset=0):
    return StructureData([
        Atom('O', np.array([0., 0, 0]) + offset, 8, chain_id='A', residue_seq=1),
        Atom('H', np.array([1., 0, 0]) + offset, 1, chain_id='A', residue_seq=1),
        Atom('H', np.array([0., 1, 0]) + offset, 1, chain_id='A', residue_seq=1),
    ], (False,) * 3, None, [(0, 1, 1., (0, 0, 0)), (0, 2, 1., (0, 0, 0))])


def canonical(scene):
    return json.dumps(scene.to_dict(), sort_keys=True, allow_nan=False)


def test_replay_matches_saved_scene_and_is_detached():
    source = molecule()
    scene = ModelScene()
    scene.add('first', source)
    scene.add('second', molecule(4))
    source.atoms[0].position[:] = 1000
    assert scene.select('model first and not element H') == {('first', 0)}
    scene.name_group('active site')
    scene.appearance('second', colour='#ff0000', style='wireframe', visible=False)
    rotation = np.array([[0., -1, 0], [1, 0, 0], [0, 0, 1]])
    scene.transform('second', rotation, np.array([1., 2, 3]))
    scene.select('group "active site" or (model second and index 5-6)')
    # JSON boundary matches what disk save/load actually does.
    loaded = ModelScene.from_dict(json.loads(canonical(scene)))
    replayed = ModelScene.replay(scene.script())
    assert canonical(scene) == canonical(loaded) == canonical(replayed)
    assert canonical(ModelScene.replay(loaded.script())) == canonical(scene)
    assert len(replayed.journal) == len(scene.journal)
    scene.models['first'].structure.atoms[0].position[:] = 900
    assert replayed.models['first'].structure.atoms[0].position[0] == 0


def test_alignment_replays_the_measured_transform():
    scene = ModelScene()
    scene.add('fixed', molecule())
    scene.add('moving', molecule(4))
    value = scene.align_model('moving', [0, 1, 2], [('fixed', i) for i in range(3)])
    assert value < 1e-14
    np.testing.assert_allclose(scene.coordinates([('moving', i) for i in range(3)]),
                               scene.coordinates([('fixed', i) for i in range(3)]), atol=1e-14)
    assert canonical(ModelScene.replay(scene.script())) == canonical(scene)


def test_periodic_cell_and_bond_images_rotate_together():
    structure = molecule()
    structure.pbc = (True, True, True)
    structure.dim = 3
    structure.lattice_vectors = np.diag([2., 3, 4])
    structure.bonds = [(0, 1, 1., (1, 0, 0))]
    original = structure.atoms[1].position + structure.lattice_vectors[0]
    rotation = np.array([[0., -1, 0], [1, 0, 0], [0, 0, 1]])
    translation = np.array([2., 3, 4])
    scene = ModelScene()
    scene.add('cell', structure)
    scene.transform('cell', rotation, translation)
    moved = scene.models['cell'].structure
    np.testing.assert_allclose(moved.atoms[1].position + moved.lattice_vectors[0],
                               rotation @ original + translation)
    assert canonical(ModelScene.replay(scene.script())) == canonical(scene)


def test_remove_prunes_group_and_selection_and_replays():
    scene = ModelScene()
    scene.add('one', molecule())
    scene.add('two', molecule(2))
    scene.select('all')
    scene.name_group('both')
    scene.remove('one')
    assert scene.selected == {('two', i) for i in range(3)}
    assert scene.groups['both'] == scene.selected
    assert canonical(ModelScene.replay(scene.script())) == canonical(scene)


def test_cross_model_radius_uses_transformed_coordinates_and_hidden_atoms():
    scene = ModelScene()
    scene.add('one', molecule())
    scene.add('two', molecule(10))
    scene.transform('two', np.eye(3), np.array([-10., -10, -10]))
    scene.appearance('two', visible=False)
    assert scene.select('within 0 of (model one and element O)') == {('one', 0), ('two', 0)}


@pytest.mark.parametrize('operation', [
    lambda scene: scene.add('one', molecule()),
    lambda scene: scene.transform('one', -np.eye(3), np.zeros(3)),
    lambda scene: scene.transform('one', np.eye(3) * 2, np.zeros(3)),
    lambda scene: scene.appearance('one', visible=False, style='invalid'),
    lambda scene: scene.appearance('one', colour='not-a-colour'),
    lambda scene: scene.name_group('bad', {('absent', 0)}),
    lambda scene: scene.select('not a valid selection'),
    lambda scene: scene.align_model('one', [0, 0], [('one', 0), ('one', 1)]),
])
def test_failed_commands_do_not_mutate_or_record(operation):
    scene = ModelScene()
    scene.add('one', molecule())
    before, journal = canonical(scene), scene.script()
    with pytest.raises(ValueError):
        operation(scene)
    assert canonical(scene) == before
    assert scene.script() == journal


def test_bad_version_or_executable_event_refused():
    with pytest.raises(ValueError):
        ModelScene.from_dict({'version': 999})
    with pytest.raises(ValueError):
        ModelScene.replay(json.dumps({'version': 1, 'events': [{'action': 'exec', 'code': '1+1'}]}))
