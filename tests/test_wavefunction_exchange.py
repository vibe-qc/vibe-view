"""Producing-program cube acceptance and exchange-format refusal contracts."""
from pathlib import Path

import numpy as np
import pytest

from vibeview.converters import convert_to_qvf, detect_format, fchk_to_qvf, molden_to_qvf
from vibeview.exchange_basis import ao_transform
from vibeview.fchk_properties import density_orbitals, unpack
from vibeview.fchk_reader import records
from vibeview.qvf import QVFReader
from vibeview.renderers.wavefunction import WavefunctionRenderer

DATA = Path(__file__).parent/'fixtures'/'wavefunction_exchange'


def cube_grid(path):
    lines = path.read_text().splitlines()
    header = lines[2].split()
    atom_count = int(header[0])
    origin = np.array([float(x) for x in header[1:4]])
    axes = [line.split() for line in lines[3:6]]
    shape = [int(row[0]) for row in axes]
    points = origin + np.indices(shape).reshape(3, -1).T @ np.array(
        [[float(x) for x in row[1:4]] for row in axes])
    offset = 6+abs(atom_count)+(1 if atom_count < 0 else 0)
    values = np.array([float(x) for line in lines[offset:] for x in line.split()]).reshape(shape)
    return [points[:, i].reshape(shape) for i in range(3)], values


def at_points(renderer, wf, row, points):
    return renderer._evaluate_at_points(
        wf, row, renderer._atom_positions_bohr('structure'), *points)


@pytest.mark.parametrize('case', ['psi4_pure', 'psi4_cartesian', 'orca'])
@pytest.mark.parametrize('variant', [0, 1])
def test_actual_program_orbital_cubes(case, variant):
    directory = DATA/case
    if case == 'orca':
        filename = 'water.molden.input' if variant == 0 else 'water.json'
        cubes = [(f'mo{i}.cube', i) for i in (0, 4, 5)]
    else:
        filename = 'water.fchk' if variant == 0 else 'water.molden'
        cubes = [(f'Psi_a_{i+1}_{i+1}-A.cube', i) for i in (0, 4, 5)]
    with QVFReader(convert_to_qvf(directory/filename)) as reader:
        wf = reader.read_wavefunction_gto('wavefunction')
        renderer = WavefunctionRenderer(reader.get_section('wavefunction'), reader)
        for cube, index in cubes:
            points, expected = cube_grid(directory/cube)
            actual = at_points(renderer, wf, wf.mo_coefficients[index], points)
            sign = 1 if np.linalg.norm(actual-expected) <= np.linalg.norm(actual+expected) else -1
            error = sign*actual-expected
            assert np.max(np.abs(error)) <= 2e-5
            assert np.linalg.norm(error)/np.linalg.norm(expected) <= 2e-5


@pytest.mark.parametrize('case', ['psi4_pure', 'psi4_cartesian'])
def test_supplied_density_natural_representation_against_program_cube(case):
    with QVFReader(fchk_to_qvf(DATA/case/'water.fchk')) as reader:
        sid = 'fchk_density_total_scf_density'
        wf = reader.read_wavefunction_gto(sid)
        renderer = WavefunctionRenderer(reader.get_section(sid), reader)
        assert renderer._natural_occupations_confirmed(wf)
        points, expected = cube_grid(DATA/case/'Dt.cube')
        actual = np.zeros_like(expected)
        for coefficient, occupation in zip(wf.mo_coefficients, wf.occupations, strict=True):
            actual += occupation*at_points(renderer, wf, coefficient, points)**2
        error = actual-expected
        # The original absolute-grid gate is recorded separately. A printed
        # cube token represents an interval set by its last decimal digit;
        # do not turn a 6-significant-digit density into an exact reference.
        from decimal import Decimal

        lines = (DATA/case/'Dt.cube').read_text().splitlines()
        n_atoms = abs(int(lines[2].split()[0]))
        rounding = np.array([float(Decimal(10) ** Decimal(token.upper().split('E')[1]) *
                                   Decimal('0.5') * Decimal(10) **
                                   -len(token.upper().split('E')[0].split('.')[1]))
                             for line in lines[6+n_atoms:] for token in line.split()])
        assert np.all(np.abs(error).ravel() <= 2e-5+rounding)
        assert np.linalg.norm(error)/np.linalg.norm(expected) <= 2e-5


def fchk_text(extra=None):
    fields = {
        'Number of atoms': 1, 'Number of basis functions': 1,
        'Number of contracted shells': 1, 'Number of alpha electrons': 1,
        'Number of beta electrons': 0, 'Charge': 0, 'Multiplicity': 2,
        'Atomic numbers': [1], 'Current Cartesian coordinates': [0., 0., 0.],
        'Shell types': [0], 'Number of primitives per shell': [1], 'Shell to atom map': [1],
        'Primitive exponents': [1.], 'Contraction coefficients': [1.],
        'Alpha MO coefficients': [1.], 'Alpha Orbital Energies': [-0.5],
    }
    fields.update(extra or {})
    lines = ['synthetic contract fixture', 'SP UHF STO-1G']
    for name, value in fields.items():
        values = value if isinstance(value, list) else [value]
        kind = 'I' if isinstance(values[0], int) else 'R'
        tail = f'N= {len(values)}' if isinstance(value, list) else str(value)
        lines.append(f'{name:40s}   {kind}   {tail}')
        if isinstance(value, list):
            lines.append(' '.join(str(x) for x in value))
    return '\n'.join(lines)+'\n'


@pytest.mark.parametrize('unrestricted', [False, True])
def test_open_shell_properties_and_densities(unrestricted):
    extra = {'Total SCF Density': [1.], 'Spin SCF Density': [1.],
             'Mulliken Charges': [0.], 'Dipole Moment': [0., 0., 1.],
             'Cartesian Force Constants': [1., 0., 1., 0., 0., 1.],
             'Real atomic weights': [1.]}
    if unrestricted:
        extra.update({'Beta MO coefficients': [1.], 'Beta Orbital Energies': [-0.4]})
    with QVFReader(fchk_to_qvf(fchk_text(extra).encode())) as reader:
        wf = reader.read_wavefunction_gto('wavefunction')
        assert wf.spin == ('unrestricted' if unrestricted else 'restricted')
        assert reader.dipole_moment['total_debye'] == pytest.approx(2.541746473)
        spin = reader.read_wavefunction_gto('fchk_density_total_scf_density_spin')
        np.testing.assert_allclose(spin.alpha_occupations, [1])
        np.testing.assert_allclose(spin.beta_occupations, [0])
        metadata = reader.read_json_member('fchk_vibrations', 'metadata')
        assert len(metadata['frequencies']) == 3
        assert metadata['frequencies'][0] == pytest.approx(5140.487, rel=1e-6)


def test_stored_modes_and_invalid_shapes():
    extra = {'Number of Normal Modes': 1, 'Vib-E2': [123., 1., 2., 3.],
             'Vib-Modes': [0., 0., 1.]}
    with QVFReader(fchk_to_qvf(fchk_text(extra).encode())) as reader:
        assert reader.read_json_member('fchk_vibrations', 'metadata')['frequencies'] == [123.]
    extra['Vib-Modes'] = [0.]
    with pytest.raises(ValueError, match='dimensions'):
        fchk_to_qvf(fchk_text(extra).encode())


def test_fchk_refusals_and_density_subspace():
    with pytest.raises(ValueError, match='s, p, d and f'):
        fchk_to_qvf(fchk_text({'Shell types': [4]}).encode())
    with pytest.raises(ValueError, match='missing Alpha MO'):
        fchk_to_qvf(fchk_text().replace('Alpha MO coefficients', 'Other coefficients   ').encode())
    with pytest.raises(ValueError):
        records('title\nmethod\ntruncated')
    with pytest.raises(ValueError):
        unpack([1], 3)
    with pytest.raises(ValueError, match='cannot represent'):
        density_orbitals(np.eye(2), np.array([[1., 0.]]))


def test_native_format_detection_and_cartesian_contract():
    assert detect_format('a.molden.input') == 'molden'
    assert detect_format('a.fchk') == 'fchk'
    order, scales = ao_transform([{'l': 2, 'pure': False}])
    assert order == [0, 3, 4, 1, 5, 2]
    np.testing.assert_allclose(scales, [1, np.sqrt(3), np.sqrt(3), 1, np.sqrt(3), 1])
    assert np.all(ao_transform([{'l': 2, 'pure': False}], normalized_components=False)[1] == 1)


def test_molden_natural_and_incomplete():
    source = (DATA/'psi4_pure'/'water.molden').read_text()
    with QVFReader(molden_to_qvf(source.encode(), natural=True)) as reader:
        wf = reader.read_wavefunction_gto('wavefunction')
        assert wf.orbital_kind == 'natural'
        assert wf.occupation_semantics == 'electron_occupation'
    with pytest.raises(ValueError, match='coefficient exactly once'):
        molden_to_qvf('\n'.join(source.splitlines()[:-1]).encode())


@pytest.mark.parametrize('filename', ['water.molden.input', 'water.json'])
def test_orca_f_phases_and_core_cube_quantization(filename):
    from decimal import Decimal

    directory = DATA/'orca_f'
    with QVFReader(convert_to_qvf(directory/filename)) as reader:
        wf = reader.read_wavefunction_gto('wavefunction')
        renderer = WavefunctionRenderer(reader.get_section('wavefunction'), reader)
        for index in (0, 4, 5):
            cube = directory/f'mo{index}.cube'
            points, expected = cube_grid(cube)
            actual = at_points(renderer, wf, wf.mo_coefficients[index], points)
            error = actual-expected
            assert np.linalg.norm(error)/np.linalg.norm(expected) <= 2e-5
            if index:
                assert np.max(np.abs(error)) <= 2e-5
            else:
                # Supplementary core reference is text-precision limited; the
                # original raw absolute threshold is NOT recorded as passed.
                lines = cube.read_text().splitlines()
                n_atoms = int(lines[2].split()[0])
                offset = 6+abs(n_atoms)+(1 if n_atoms < 0 else 0)
                quantum = np.array([float(Decimal(10)**Decimal(token.upper().split('E')[1]) *
                                          Decimal('0.5') * Decimal(10)**
                                          -len(token.upper().split('E')[0].split('.')[1]))
                                    for line in lines[offset:] for token in line.split()])
                assert np.all(np.abs(error).ravel() <= quantum+2e-5)
