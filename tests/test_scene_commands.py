"""Command parsing and numerical results through owner-supplied I/O adapters."""
import json

import numpy as np
import pytest

from vibeview.model_scene import ModelScene
from vibeview.qvf import Atom, StructureData
from vibeview.scene_commands import SceneCommands


def structure():
    return StructureData([Atom('C', np.array(p, dtype=float), 6) for p in
                          [[0, 0, 0], [2, 0, 0], [0, 2, 0], [0, 0, 2]]],
                         (False,) * 3, None, None)


def test_command_session_save_and_replay(tmp_path):
    loaded, captured = [], []

    def loader(path):
        loaded.append(path)
        return structure()

    def writer(name, text):
        (tmp_path / name).write_text(text)

    commands = SceneCommands(load_structure=loader, write_text=writer,
                             capture=captured.append)
    for line in ['open reference "reference file.xyz"', 'open mobile mobile.xyz',
                 'colour mobile red', 'style mobile wireframe', 'hide mobile',
                 'show mobile', 'move mobile 1 2 2', 'rotate mobile 0 0 1 90',
                 'select model reference and index 1-3', 'group "plane atoms"',
                 'select\tgroup "plane atoms"']:
        commands.execute(line)
    assert loaded == ['reference file.xyz', 'mobile.xyz']
    assert commands.scene.selected == {('reference', i) for i in range(3)}
    assert commands.execute('measure centroid "model reference"')['centroid'] == [.5, .5, .5]
    assert commands.execute('measure point-plane "index 4" "group \'plane atoms\'"') == {
        'distance': 2., 'units': 'angstrom'}
    result = commands.execute('align mobile "model mobile" "model reference"')
    assert result['rmsd'] < 1e-14
    commands.execute('capture "figure one.png"')
    commands.execute('save scene.json')
    commands.execute('save-script replay.json')
    replayed = ModelScene.replay((tmp_path / 'replay.json').read_text())
    assert json.loads(json.dumps(replayed.to_dict())) == json.loads(
        (tmp_path / 'scene.json').read_text())
    assert captured == ['figure one.png']
    assert commands.history[-1] == 'save-script replay.json'


def test_measurement_does_not_change_selection_or_journal():
    commands = SceneCommands()
    commands.scene.add('one', structure())
    commands.execute('select index 1')
    before = commands.scene.script()
    assert commands.execute('measure plane "index 1-3"')['normal'] == [0., 0., 1.]
    assert commands.execute('measure axes "all"')['unique'] is False
    assert commands.execute('measure rmsd "all" "all"')['rmsd'] == 0
    assert commands.execute('measure rmsd-fit "all" "all"')['rmsd'] < 1e-14
    assert commands.execute('measure plane-angle "index 1-3" "index 1,2,4"')['angle'] == 90
    assert commands.scene.script() == before
    assert commands.scene.selected == {('one', 0)}


@pytest.mark.parametrize('line', [
    '', 'select', 'unknown', 'open id', 'move one 1 2', 'rotate one 0 0 0 3',
    'rotate one 0 0 1 nan', 'colour one invalid-colour', 'hide one extra',
    'measure', 'measure nonsense all', 'measure centroid', 'measure rmsd all',
    'measure point-plane "all" "index 1-3"', 'align one "none" "none"',
    'open id some.xyz', 'save x.json', 'capture x.png',
])
def test_failed_command_is_not_history_or_scene_mutation(line):
    commands = SceneCommands()
    commands.scene.add('one', structure())
    before = commands.scene.script()
    with pytest.raises(ValueError):
        commands.execute(line)
    assert commands.scene.script() == before
    assert commands.history == []


def test_open_failure_and_duplicate_do_not_replace_model():
    def loader(path):
        raise OSError('missing')
    commands = SceneCommands(load_structure=loader)
    commands.scene.add('one', structure())
    with pytest.raises(ValueError, match='already exists'):
        commands.execute('open one file.xyz')
    with pytest.raises(OSError):
        commands.execute('open two file.xyz')
    assert list(commands.scene.models) == ['one']
    assert commands.history == []
