"""Independent native fields without six-digit cube printing loss."""
import struct
from pathlib import Path

import numpy as np
import pytest

from vibeview.converters import convert_to_qvf
from vibeview.qvf import QVFReader
from vibeview.renderers.wavefunction import WavefunctionRenderer

DATA = Path(__file__).parent / 'fixtures' / 'wavefunction_precision'


def evaluate(reader, section, points, mo=None):
    wf = reader.read_wavefunction_gto(section)
    renderer = WavefunctionRenderer(reader.get_section(section), reader)

    def orbital(row):
        return renderer._evaluate_at_points(
            wf, row, renderer._atom_positions_bohr('structure'), *points.T)

    if mo is not None:
        return orbital(wf.mo_coefficients[mo])
    return sum(occ * orbital(row)**2 for occ, row in
               zip(wf.occupations, wf.mo_coefficients, strict=True))


def agrees(actual, expected, phase_allowed=False):
    if phase_allowed and np.linalg.norm(actual + expected) < np.linalg.norm(actual - expected):
        actual = -actual
    delta = actual - expected
    assert np.max(np.abs(delta)) <= 2e-5
    assert np.linalg.norm(delta) / np.linalg.norm(expected) <= 2e-5


@pytest.mark.parametrize('case', ['pure', 'cartesian'])
@pytest.mark.parametrize('suffix', ['fchk', 'molden'])
def test_native_psi4_fields(case, suffix):
    with np.load(DATA / case / 'native-grid.npz') as reference:
        points = reference['points']
        with QVFReader(convert_to_qvf(DATA / case / f'water.{suffix}')) as reader:
            for column, mo in enumerate(reference['mo_indices']):
                agrees(evaluate(reader, 'wavefunction', points, int(mo)),
                       reference['orbitals'][:, column], True)
            agrees(evaluate(reader, 'wavefunction', points), reference['density'])
            if suffix == 'fchk':
                agrees(evaluate(reader, 'fchk_density_total_scf_density', points),
                       reference['density'])


@pytest.mark.parametrize('route', ['water.molden.input', 'water.json'])
def test_native_orca_binary_core(route):
    raw = (DATA / 'orca-core.bin').read_bytes()
    assert struct.unpack('<5i', raw[:20]) == (3, 1, 17, 17, 17)
    assert len(raw) == 44 + 4 * 17**3
    # Exact producer input is Bohr; binary header bounds are rounded Angstrom.
    assert np.allclose(struct.unpack('<6f', raw[20:44]),
                       [-4 * .529177210903, 4 * .529177210903] * 3, rtol=0, atol=2e-7)
    z, y, x = np.meshgrid(*[np.linspace(-4, 4, 17)] * 3, indexing='ij')
    points = np.column_stack([x.ravel(), y.ravel(), z.ravel()])
    expected = np.frombuffer(raw, dtype='<f4', offset=44).astype(float)
    source = DATA.parent / 'wavefunction_exchange' / 'orca_f' / route
    with QVFReader(convert_to_qvf(source)) as reader:
        agrees(evaluate(reader, 'wavefunction', points, 0), expected, True)
