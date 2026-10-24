import numpy as np
import pytest

from vibeview.coordination_builder import TEMPLATES, coordinate, directions
from vibeview.qvf import Atom, StructureData


def metal():
    return StructureData([Atom("Fe", np.zeros(3), 26)], (False, False, False), None, [])


@pytest.mark.parametrize("template", list(TEMPLATES))
def test_template_distances_angles_and_connectivity(template):
    original = metal()
    s = coordinate(original, 0, template, "ammonia", 2.1)
    donors = [j for i, j, _, _ in s.bonds if i == 0]
    positions = np.array([s.atoms[j].position for j in donors])
    np.testing.assert_allclose(np.linalg.norm(positions, axis=1), 2.1, atol=1e-10)
    np.testing.assert_allclose(
        positions @ positions.T / 2.1**2, directions(template) @ directions(template).T, atol=1e-10
    )
    assert len(original.atoms) == 1


def test_existing_ligands_preserved_and_hydrogen_replaced():
    s = metal()
    s.atoms.extend(
        [Atom("Cl", np.array([2.1, 0.0, 0.0]), 17), Atom("H", np.array([-1.0, 0.0, 0.0]), 1)]
    )
    s.bonds = [(0, 1, 1.0, (0, 0, 0)), (0, 2, 1.0, (0, 0, 0))]
    result = coordinate(s, 0, "square planar", "water", 2.1, True)
    np.testing.assert_array_equal(result.atoms[1].position, s.atoms[1].position)
    assert len([b for b in result.bonds if b[0] == 0]) == 4


def test_chelate_rigid_fit_and_refusal():
    from vibeview.coordination_builder import ligand_geometry

    _, coords, donors = ligand_geometry("ethylenediamine")
    distance = np.linalg.norm(coords[donors[1]] - coords[donors[0]]) / np.sqrt(2)
    s = coordinate(metal(), 0, "square planar", "ethylenediamine", distance)
    assert len([b for b in s.bonds if b[0] == 0]) == 4
    with pytest.raises(ValueError, match="separation"):
        coordinate(metal(), 0, "square planar", "ethylenediamine", 10)
