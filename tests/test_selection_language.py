"""Selection grammar and finite-coordinate semantics against explicit sets."""
import numpy as np
import pytest

from vibeview.molecular_selection import select
from vibeview.qvf import Atom, StructureData


@pytest.fixture
def molecule():
    atoms = [Atom(symbol, np.array(point, dtype=float), number,
                  chain_id=chain, residue_seq=seq)
             for symbol, number, point, chain, seq in [
                 ('O', 8, (0, 0, 0), 'A', 1),
                 ('H', 1, (1, 0, 0), 'A', 1),
                 ('H', 1, (0, 1, 0), 'A', 1),
                 ('C', 6, (4, 0, 0), 'B', 2),
                 ('N', 7, (5, 0, 0), 'B', 2)]]
    return StructureData(atoms, (False, False, False), None, None)


@pytest.mark.parametrize(('expression', 'expected'), [
    ('all', {0, 1, 2, 3, 4}), ('none', set()), ('', set()),
    ('index 1,3-5', {0, 2, 3, 4}), ('index 1, 3-5', {0, 2, 3, 4}), ('element h', {1, 2}),
    ('chain A and not element H', {0}),
    ('element O or element C and chain A', {0}),
    ('(element O or element C) and chain B', {3}),
    ('not (element H or (element C or element N))', {0}),
    ('within 1 of (index 1)', {0, 1, 2}),
    ('within 1 of (element C or element N) and not element N', {3}),
    ('within 0 of (none)', set()), ('residue A/1', {0, 1, 2}),
    ('model 1', {0, 1, 2, 3, 4}), ('model 2', set()),
])
def test_grammar(molecule, expression, expected):
    assert select(molecule, expression) == expected


def test_explicit_model_and_named_group(molecule):
    groups = {'active site': {0, 3}}
    assert select(molecule, 'group "active site" and model second',
                  groups=groups, model_ids=['first'] * 3 + ['second'] * 2) == {3}
    assert groups == {'active site': {0, 3}}


@pytest.mark.parametrize('expression', [
    'all garbage', '(all', 'all)', 'not', 'all and', 'all or or none',
    'index 0', 'index 2-1', 'index 1-1000000000000', 'index 1,,2',
    'element', 'element and', 'residue A', 'unknown x', 'group absent',
    'within -1 of (all)', 'within nan of (all)', 'within 2 of all',
    'within 1 of ()', '__import__(os)',
])
def test_invalid_expressions(molecule, expression):
    with pytest.raises(ValueError):
        select(molecule, expression)


def test_invalid_external_context(molecule):
    with pytest.raises(ValueError, match='one entry'):
        select(molecule, model_ids=['one'])
    with pytest.raises(ValueError, match='out of range'):
        select(molecule, 'group invalid', groups={'invalid': {-1}})
