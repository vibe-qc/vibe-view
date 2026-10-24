import numpy as np
import pytest

from vibeview.molecular_builder import conformers, rotate_bond, structure_molecule
from vibeview.qvf import Atom, QVFReader, StructureData


def chain():
    return StructureData(
        [
            Atom("C", np.array(p, dtype=float), 6)
            for p in [[0, 1, 0], [0, 0, 0], [1.5, 0, 0], [1.5, 1, 0]]
        ],
        (False, False, False),
        None,
        [(i, i + 1, 1.0, (0, 0, 0)) for i in range(3)],
    )


def test_rotation_smaller_tie_second_ring_frozen_and_immutability():
    s = chain()
    before = np.array([a.position for a in s.atoms])
    result = rotate_bond(s, [1, 2], 90)
    np.testing.assert_allclose(result[:3], before[:3], atol=1e-12)
    np.testing.assert_allclose(result[3], [1.5, 0, 1], atol=1e-12)
    np.testing.assert_array_equal([a.position for a in s.atoms], before)
    np.testing.assert_allclose(rotate_bond(s, [1, 2], 90, [1, 2]), result)
    with pytest.raises(ValueError, match="frozen"):
        rotate_bond(s, [1, 2], 90, [3])
    s.bonds.append((0, 3, 1.0, (0, 0, 0)))
    with pytest.raises(ValueError, match="Ring"):
        rotate_bond(s, [1, 2], 90)


def test_conformers_reproducible_energy_ranked_full_frames():
    from rdkit import Chem

    mol = Chem.AddHs(Chem.MolFromSmiles("CCCC"))
    a, b = conformers(mol, 3, 19), conformers(mol, 3, 19)
    assert a.energies == b.energies == sorted(a.energies)
    assert all(a.converged)
    assert len(a.ids) == 3
    with QVFReader(a.archive()) as reader:
        assert len(reader.read_structure().atoms) == mol.GetNumAtoms()
        assert reader.read_structure().bonds is not None
        coords = reader.read_binary_member("conformers", "coords")
        assert coords.shape == (3, mol.GetNumAtoms(), 3)
        assert len(reader.provenance["energies_kcal_mol"]) == 3
        again = structure_molecule(reader.read_structure())
        assert again.GetNumAtoms() == mol.GetNumAtoms()


def test_incomplete_valences_refuse():
    with pytest.raises(ValueError, match="hydrogens"):
        structure_molecule(chain())
    with pytest.raises(ValueError, match="1–100"):
        conformers(None, 101)


def test_conformer_and_built_archives_open_on_their_structure():
    """#140: builder outputs declared no auto_open, so they opened with no
    section selected and the builder card disappeared after each build."""
    from rdkit import Chem

    from vibeview.builder_panel import molecule_archive
    from vibeview.fragment_catalog import fragment

    mol = Chem.AddHs(Chem.MolFromSmiles("CCO"))
    for archive in (conformers(mol, 2, 7).archive(), molecule_archive(fragment("ethanol"), "t")):
        with QVFReader(archive) as reader:
            assert reader.viewer_defaults.auto_open == ["structure"]
            assert reader.first_structure_id == "structure"


def test_a_built_sequence_opens_with_its_structure_selected(water_qvf):
    from vibeview.app import create_app

    with QVFReader(water_qvf) as reader:
        app = create_app([reader])
        state = app.state
        state.mb_sequence_kind, state.mb_backbone, state.mb_sequence = "peptide", "helix", "AG"
        app.controller.mb_sequence_build()
        assert state.status_message.startswith("Sequence built")
        assert state.selected_section == "structure"
