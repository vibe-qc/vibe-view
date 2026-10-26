import itertools

import numpy as np
import pytest

from vibeview.experimental_map import open_map


def write_map(path, order=(1, 2, 3), endian='<', mode=2, origin=(0, 0, 0)):
    header = bytearray(1024)
    ints = np.frombuffer(header, dtype=endian+'i4')
    floats = np.frombuffer(header, dtype=endian+'f4')
    ints[:4] = [2, 3, 4, mode]
    ints[4:7] = [2, -1, 3]
    ints[7:10] = [10, 20, 30]
    floats[10:16] = [20, 60, 120, 90, 90, 90]
    ints[16:19] = order
    floats[19:22] = [0, 23, 11.5]
    ints[22:24] = [1, 80]
    floats[49:52] = origin
    floats[54] = 2.5
    header[208:212] = b'MAP '
    header[212:214] = b'DA' if endian == '<' else b'\x11\x11'
    data = np.arange(24).reshape(4, 3, 2)
    dtype = {0:'i1',1:'i2',2:'f4',6:'u2',12:'f2'}[mode]
    path.write_bytes(header + b' '*80 + data.astype(endian+dtype).tobytes())
    return data


@pytest.mark.parametrize('order', list(itertools.permutations((1, 2, 3))))
@pytest.mark.parametrize('endian', ['<', '>'])
def test_axis_geometry_and_byte_order(tmp_path, order, endian):
    path = tmp_path/'map.mrc'
    expected = write_map(path, order, endian)
    m = open_map(path)
    assert isinstance(m.data, np.memmap)
    np.testing.assert_array_equal(m.data, expected.transpose(2, 1, 0))
    vectors = np.diag([2., 3., 4.])[np.array(order)-1]
    np.testing.assert_allclose(m.voxel_vectors, vectors, atol=1e-12)
    np.testing.assert_allclose(m.origin, np.array([2, -1, 3]) @ vectors, atol=1e-12)
    assert m.sigma_level(2) == 16.5
    sample, voxels, stride = m.sampled(4)
    assert sample.size <= 4
    np.testing.assert_allclose(voxels, vectors*stride, atol=1e-12)
    assert m.sigma_level(2) == 16.5


@pytest.mark.parametrize('mode', [0, 1, 2, 6, 12])
def test_modes_and_explicit_origin(tmp_path, mode):
    path = tmp_path/'map.ccp4'
    expected = write_map(path, mode=mode, origin=(1.25, -2, 5))
    m = open_map(path)
    np.testing.assert_array_equal(m.origin, [1.25, -2, 5])
    np.testing.assert_array_equal(m.data, expected.transpose(2, 1, 0))


def test_malformed_maps_and_undefined_sigma(tmp_path):
    path = tmp_path/'map.mrc'
    write_map(path)
    data = bytearray(path.read_bytes())
    path.write_bytes(data[:-1])
    with pytest.raises(ValueError, match='Truncated'):
        open_map(path)
    np.frombuffer(data, '<f4')[54] = -1
    path.write_bytes(data)
    with pytest.raises(ValueError, match='positive RMS'):
        open_map(path).sigma_level(1)
    np.frombuffer(data, '<i4')[3] = 4
    path.write_bytes(data)
    with pytest.raises(ValueError, match='Unsupported'):
        open_map(path)


def test_nonorthogonal_cell_coordinates(tmp_path):
    path = tmp_path/'skew.map'
    write_map(path)
    data = bytearray(path.read_bytes())
    np.frombuffer(data, '<f4')[15] = 60
    path.write_bytes(data)
    m = open_map(path)
    expected = np.array([[2, 0, 0], [1.5, 3*np.sqrt(3)/2, 0], [0, 0, 4]])
    np.testing.assert_allclose(m.voxel_vectors, expected, atol=1e-12)
    np.testing.assert_allclose(m.origin, np.array([2, -1, 3]) @ expected, atol=1e-12)


def test_surface_world_placement_and_selection_zone():
    from vibeview.experimental_map import ExperimentalMap, map_surface
    axis = np.linspace(-2, 2, 41)
    x, y, z = np.meshgrid(axis, axis, axis, indexing='ij')
    data = x*x + y*y + z*z
    offset = np.array([10., -3., 5.])
    m = ExperimentalMap(data, offset-2, np.eye(3)*.1, 0, 1, 0, 12, (1, 2, 3), 'test')
    mesh = map_surface(m, 1, sigma=False)
    radii = np.linalg.norm(mesh.points-offset, axis=1)
    assert mesh.n_points > 100
    assert np.max(np.abs(radii-1)) < .005
    selected = np.array([offset + [1, 0, 0]])
    zoned = map_surface(m, 1, selection=selected, radius=.5)
    assert 0 < zoned.n_points < mesh.n_points
    assert np.max(np.linalg.norm(zoned.points-selected[0], axis=1)) <= .50001
    assert map_surface(m, 1, selection=np.empty((0, 3))).n_points == 0
    opposite = map_surface(m, 1, selection=np.array([offset + [-1, 0, 0]]), radius=.5)
    assert opposite.center[0] < offset[0]
    with pytest.raises(ValueError, match='radius'):
        map_surface(m, 1, selection=selected, radius=-1)


def test_qvf_grid_units_and_full_resolution_provenance(tmp_path):
    from vibeview.experimental_map import map_to_qvf
    from vibeview.qvf import QVFReader
    from vibeview.units import BOHR_TO_ANGSTROM
    path = tmp_path/'map.mrc'
    write_map(path, order=(3, 1, 2))
    original = open_map(path)
    with QVFReader(map_to_qvf(path, max_voxels=4)) as reader:
        grid = reader.read_volume_grid('map')
        assert reader.get_section('map').kind == 'volume.generic'
        data, vectors, stride = original.sampled(4)
        np.testing.assert_array_equal(reader.read_volume_data('map'), data)
        np.testing.assert_allclose(grid.origin*BOHR_TO_ANGSTROM, original.origin)
        np.testing.assert_allclose(grid.voxel_vectors*BOHR_TO_ANGSTROM, vectors)
        metadata = reader.provenance['experimental_map']
        assert metadata['stride'] == stride > 1
        assert not metadata['full_resolution']
        assert metadata['header_statistics']['rms'] == 2.5


def test_registered_path_and_upload(tmp_path):
    from vibeview.converters import convert_bytes_to_qvf, convert_to_qvf, detect_format
    from vibeview.qvf import QVFReader
    path = tmp_path/'density.map'
    expected = write_map(path)
    assert detect_format(path) == 'mrc'
    for output in (convert_to_qvf(path), convert_bytes_to_qvf(path.read_bytes(), 'map.ccp4')):
        with QVFReader(output) as reader:
            np.testing.assert_array_equal(
                reader.read_volume_data('map'), expected.transpose(2, 1, 0)
            )
