"""Independent cube coordinates and x/y/z sample ordering."""

import json
import struct
import zipfile

import numpy as np
import pytest

from vibeview.converters import cube_to_qvf
from vibeview.qvf import QVFReader

# CODATA 2018, deliberately independent of the production conversion helper.
BOHR_ANG = 0.529177210903
ORIGIN = np.array([1.2, -2.3, 0.4])
VOXELS = np.array([[0.3, 0.1, 0.0], [0.0, 0.4, 0.2], [0.1, 0.0, -0.5]])
ATOM = np.array([2.1, -0.2, 1.3])


def cube(counts, *, angstrom=False, orbital=False):
    factor = BOHR_ANG if angstrom else 1.0
    def numbers(v):
        return " ".join(format(x, ".15g") for x in v)
    lines = ["orbital field" if orbital else "density field", "coordinate regression"]
    lines.append(f"{-1 if orbital else 1} " + numbers(ORIGIN * factor))
    lines.extend(f"{n} " + numbers(v * factor) for n, v in zip(counts, VOXELS, strict=True))
    lines.append("6 6 " + numbers(ATOM * factor))
    if orbital:
        lines.append("1 7")
    # The cube z index moves fastest, as specified by Gaussian/VMD.
    lines.append(" ".join(str(100*i + 10*j + k) for i in range(2)
                          for j in range(3) for k in range(4)))
    return ("\n".join(lines) + "\n").encode()


@pytest.mark.parametrize("angstrom", [False, True])
@pytest.mark.parametrize("orbital", [False, True])
def test_cube_units_order_and_signed_skew_axes(angstrom, orbital):
    counts = [-2, -3, -4] if angstrom else [2, 3, 4]
    output = cube_to_qvf(cube(counts, angstrom=angstrom, orbital=orbital))
    with QVFReader(output) as reader:
        grid = reader.read_volume_grid("vol_0")
        data = reader.read_volume_data("vol_0")
        assert grid.shape == (2, 3, 4)
        np.testing.assert_allclose(grid.origin, ORIGIN, atol=1e-13)
        np.testing.assert_allclose(grid.voxel_vectors, VOXELS, atol=1e-13)
        np.testing.assert_allclose(reader.read_structure().atoms[0].position, ATOM * BOHR_ANG)
        for index in np.ndindex(2, 3, 4):
            assert data[index] == 100*index[0] + 10*index[1] + index[2]
            np.testing.assert_allclose(grid.origin + np.array(index) @ grid.voxel_vectors,
                                       ORIGIN + np.array(index) @ VOXELS, atol=1e-13)
        assert reader.get_section("vol_0").kind == (
            "volume.orbital" if orbital else "volume.density"
        )


@pytest.mark.parametrize("counts", [[0, 3, 4], [2, 0, 4], [2, 3, 0]])
def test_zero_voxel_count_is_rejected(counts):
    with pytest.raises(ValueError, match="nonzero"):
        cube_to_qvf(cube(counts))


@pytest.mark.parametrize("counts", [[-2, 3, 4], [2, -3, 4], [2, 3, -4], [-2, -3, 4]])
def test_mixed_unit_flags_are_rejected(counts):
    with pytest.raises(ValueError, match="mixed.*sign"):
        cube_to_qvf(cube(counts))


def test_positive_cube_binary_remains_little_endian_c_order():
    with zipfile.ZipFile(cube_to_qvf(cube([2, 3, 4]))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        member = manifest["sections"][1]["members"]["data"]
        assert member["dtype"] == "float32"
        assert archive.read(member["path"]) == struct.pack(
            "<24f", *(100*i + 10*j + k for i in range(2) for j in range(3) for k in range(4))
        )
