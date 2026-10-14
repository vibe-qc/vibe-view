"""PDB's documented unitary placeholder does not fabricate a periodic cell."""

import numpy as np
import pytest

from vibeview.converters import pdb_to_qvf
from vibeview.qvf import QVFReader


@pytest.mark.parametrize(
    "lengths,angles,group,z,periodic",
    [
        ((1.0, 1.0, 1.0), (90.0, 90.0, 90.0), "P 1", 1, False),
        ((1.0, 1.0, 1.0), (90.0, 90.0, 90.0), "P1", 1, False),
        ((1.5, 1.5, 1.5), (90.0, 90.0, 90.0), "P 1", 1, True),
        ((1.0, 2.0, 3.0), (90.0, 90.0, 90.0), "P 1", 1, True),
        ((1.0, 1.0, 1.0), (80.0, 90.0, 90.0), "P 1", 1, True),
        ((1.0, 1.0, 1.0), (90.0, 90.0, 90.0), "P 2", 1, True),
        ((1.0, 1.0, 1.0), (90.0, 90.0, 90.0), "P 1", 2, True),
        ((4.0, 6.0, 8.0), (75.0, 85.0, 65.0), "P 1", 1, True),
    ],
)
def test_documented_placeholder_and_real_small_cells(lengths, angles, group, z, periodic):
    a, b, c = lengths
    alpha, beta, gamma = angles
    text = (
        f"CRYST1{a:9.3f}{b:9.3f}{c:9.3f}{alpha:7.2f}{beta:7.2f}{gamma:7.2f} {group:<11}{z:4d}\n"
        "ATOM      1  O   HOH A   1       2.500   3.250  -1.000  1.00 20.00           O  \n"
        "END\n"
    )
    with QVFReader(pdb_to_qvf(text.encode())) as reader:
        structure = reader.read_structure()
    assert structure.pbc == (periodic,) * 3
    np.testing.assert_array_equal(structure.atoms[0].position, [2.5, 3.25, -1.0])
    assert structure.atoms[0].symbol == "O"
    if periodic:
        assert structure.lattice_vectors is not None
        np.testing.assert_allclose(np.linalg.norm(structure.lattice_vectors, axis=1), lengths)
    else:
        assert structure.lattice_vectors is None


@pytest.mark.parametrize(
    "z_field,periodic",
    [("0001", False), ("+001", False), ("   2", True), ("    ", True), ("   ?", True)],
)
def test_placeholder_uses_numeric_z_with_optional_invalid_metadata(z_field, periodic):
    text = (
        "CRYST1    1.000    1.000    1.000  90.00  90.00  90.00 P 1        " + z_field + "\n"
        "ATOM      1  O   HOH A   1       2.500   3.250  -1.000  1.00 20.00           O  \n"
    )
    with QVFReader(pdb_to_qvf(text.encode())) as reader:
        structure = reader.read_structure()
    assert structure.pbc == (periodic,) * 3
