"""Synthetic ORCA JSON and independent solid-harmonic regressions."""

from __future__ import annotations

import copy
import math

import numpy as np
import pytest

from vibeview.orca_orbitals import add_molecule
from vibeview.qvf import QVFReader
from vibeview.qvf_writer import QVFArchiveBuilder
from vibeview.units import BOHR_TO_ANGSTROM, HARTREE_TO_EV


def _molecule(shells="spdf", spin="RHF"):
    count = sum(2 * "spdf".index(s) + 1 for s in shells)
    coefficients = np.eye(count)
    if spin == "UHF":
        coefficients = np.vstack((coefficients, -coefficients))
    rows = [
        {
            "MOCoefficients": row.tolist(),
            "Occupancy": float(index < 2),
            "OrbitalEnergy": -1 + 0.1 * index,
            "OrbitalSymLabel": f"sym{index}",
        }
        for index, row in enumerate(coefficients)
    ]
    return {
        "Molecule": {
            "Atoms": [
                {
                    "ElementNumber": 8,
                    "NuclearCharge": 6.0,
                    "Coords": [0.1, -0.2, 0.3],
                    "Idx": 0,
                    "Basis": [
                        {"Shell": s, "Exponents": [0.6, 1.2], "Coefficients": [0.3, -0.8]}
                        for s in shells
                    ],
                }
            ],
            "CoordinateUnits": "Bohr",
            "HFTyp": spin,
            "Charge": 0,
            "Multiplicity": 1,
            "MolecularOrbitals": {"EnergyUnit": "Eh", "MOs": rows},
        }
    }


def _archive(data):
    builder = QVFArchiveBuilder(program="ORCA")
    add_molecule(builder, data)
    return builder.to_bytesio()


def _orca_harmonics(ell, x, y, z):
    """ORCA manual 9.3.3: angular parts normalized on the unit sphere."""
    r2 = x * x + y * y + z * z
    factors = (
        [np.ones_like(x) / math.sqrt(4 * math.pi)],
        [math.sqrt(3 / (4 * math.pi)) * a for a in (z, x, y)],
        [
            math.sqrt(15 / (4 * math.pi)) * a
            for a in (math.sqrt(3) / 6 * (3 * z * z - r2), x * z, y * z, (x * x - y * y) / 2, x * y)
        ],
        [
            math.sqrt(105 / (4 * math.pi)) * a
            for a in (
                math.sqrt(15) / 30 * z * (5 * z * z - 3 * r2),
                math.sqrt(10) / 20 * x * (5 * z * z - r2),
                math.sqrt(10) / 20 * y * (5 * z * z - r2),
                (x * x - y * y) * z / 2,
                x * y * z,
                -math.sqrt(6) / 12 * x * (x * x - 3 * y * y),
                -math.sqrt(6) / 12 * y * (3 * x * x - y * y),
            )
        ],
    )
    return factors[ell]


def test_spherical_orbital_values_match_orca_s_p_d_f_definition():
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    data = _molecule()
    original = copy.deepcopy(data)
    axes = [np.array([0.17, 0.68]), np.array([-0.91, 0.32]), np.array([0.27, 1.2])]
    positions = np.array([data["Molecule"]["Atoms"][0]["Coords"]])
    grid = np.meshgrid(*axes, indexing="ij")
    x, y, z = [grid[i] - positions[0, i] for i in range(3)]
    expected = []
    for ell in range(4):
        # Independently normalized radial Gaussians. The spherical angular
        # part has unit surface norm, so the radial norm is a gamma integral.
        radial = sum(
            c
            * math.sqrt(2 * (2 * a) ** (ell + 1.5) / math.gamma(ell + 1.5))
            * np.exp(-a * (x * x + y * y + z * z))
            for a, c in ((0.6, 0.3), (1.2, -0.8))
        )
        expected.extend(radial * angular for angular in _orca_harmonics(ell, x, y, z))
    with QVFReader(_archive(data)) as reader:
        renderer = WavefunctionRenderer(reader.get_section("wavefunction"), reader)
        wf = renderer.load()
        for index, row in enumerate(wf.mo_coefficients):
            actual = renderer._evaluate_on_grid(wf, row, positions, *axes)
            np.testing.assert_allclose(actual, expected[index], rtol=2e-14, atol=2e-14)
        assert reader.read_structure().atoms[0].atomic_number == 8
        np.testing.assert_allclose(
            reader.read_structure().atoms[0].position, positions[0] * BOHR_TO_ANGSTROM
        )
        np.testing.assert_allclose(wf.shells[0].coefficients, [0.3, -0.8])
    assert data == original


@pytest.mark.parametrize("spin", ["RHF", "ROHF", "UHF", "CASSCF"])
def test_spin_blocks_keep_energies_occupations_and_symmetry(spin):
    data = _molecule("sp", spin)
    rows = data["Molecule"]["MolecularOrbitals"]["MOs"]
    data["Molecule"]["MolecularOrbitals"]["EnergyUnit"] = "eV"
    with QVFReader(_archive(data)) as reader:
        wf = reader.read_wavefunction_gto("wavefunction")
        if spin == "UHF":
            assert wf.spin == "unrestricted"
            np.testing.assert_allclose(wf.mo_coefficients_beta, -wf.mo_coefficients_alpha)
            np.testing.assert_allclose(
                wf.beta_energies, np.array([r["OrbitalEnergy"] for r in rows[4:]]) / HARTREE_TO_EV
            )
            assert wf.symmetry_labels_beta == [r["OrbitalSymLabel"] for r in rows[4:]]
            np.testing.assert_allclose(wf.alpha_occupations, [1, 1, 0, 0])
            np.testing.assert_allclose(wf.beta_occupations, [0, 0, 0, 0])
        else:
            assert wf.spin == "restricted"
            np.testing.assert_allclose(
                wf.energies, np.array([r["OrbitalEnergy"] for r in rows]) / HARTREE_TO_EV
            )
            assert wf.symmetry_labels == [r["OrbitalSymLabel"] for r in rows]


def test_geometry_only_and_absent_orbital_metadata():
    data = _molecule("s")
    row = data["Molecule"]["MolecularOrbitals"]["MOs"][0]
    for name in ("Occupancy", "OrbitalEnergy", "OrbitalSymLabel"):
        del row[name]
    with QVFReader(_archive(data)) as reader:
        wf = reader.read_wavefunction_gto("wavefunction")
        assert wf.energies.size == 0 and wf.occupations.size == 0
    del data["Molecule"]["MolecularOrbitals"]
    del data["Molecule"]["Atoms"][0]["Basis"]
    with QVFReader(_archive(data)) as reader:
        assert [s.kind for s in reader.sections] == ["structure"]


def test_casscf_preserves_fractional_occupations_without_guessing_canonicalization():
    data = _molecule("sp", "CASSCF")
    occupations = [1.9, 1.6, 0.4, 0.1]
    for row, occupation in zip(
        data["Molecule"]["MolecularOrbitals"]["MOs"], occupations, strict=True
    ):
        row["Occupancy"] = occupation
    with QVFReader(_archive(data)) as reader:
        wf = reader.read_wavefunction_gto("wavefunction")
        np.testing.assert_allclose(wf.occupations, occupations)
        metadata = reader.read_json_member("wavefunction", "mo_metadata")
        assert "orbital_kind" not in metadata
        assert metadata["source_wavefunction"] == "CASSCF"
        assert "canonicalization" in metadata["density_unavailable_reason"]
        from vibeview.renderers.wavefunction import WavefunctionRenderer, occupation_problem

        assert "canonicalization" in occupation_problem(wf, None, 4)
        renderer = WavefunctionRenderer(reader.get_section("wavefunction"), reader)
        _, values = renderer.evaluate_mo(0, n_per_dim=6)
        assert np.isfinite(values).all() and np.any(values)
        for field in (
            renderer.evaluate_density, renderer.evaluate_elf,
            renderer.evaluate_nci, renderer.evaluate_density_laplacian,
        ):
            with pytest.raises(ValueError, match="canonicalization"):
                field(n_per_dim=6)


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda m: m.update(CoordinateUnits="unknown"), "CoordinateUnits"),
        (lambda m: m.update(HFTyp="GHF"), "HFTyp"),
        (lambda m: m["Atoms"][0].update(Coords=[0, float("nan"), 0]), "Coords"),
        (lambda m: m["Atoms"][0].update(ElementNumber=True), "ElementNumber"),
        (lambda m: m["Atoms"][0].update(Idx=1), "Idx"),
        (lambda m: m["Atoms"][0].pop("Basis"), "Basisset"),
        (lambda m: m["Atoms"][0]["Basis"][0].update(Shell="g"), "spherical"),
        (lambda m: m["Atoms"][0]["Basis"][0].update(Exponents=[-1, 1]), "positive"),
        (lambda m: m["Atoms"][0]["Basis"][0].update(Coefficients=[1]), "Coefficients"),
        (lambda m: m["MolecularOrbitals"].update(EnergyUnit="kJ/mol"), "EnergyUnit"),
        (lambda m: m["MolecularOrbitals"]["MOs"][0].update(MOCoefficients=[1]), "MOCoefficients"),
        (lambda m: m["MolecularOrbitals"]["MOs"][0].update(Occupancy=-1), "occupations"),
        (lambda m: m["MolecularOrbitals"]["MOs"][0].pop("OrbitalEnergy"), "OrbitalEnergy"),
    ],
)
def test_unsupported_or_malformed_data_is_not_partially_imported(change, match):
    data = _molecule()
    change(data["Molecule"])
    builder = QVFArchiveBuilder()
    with pytest.raises(ValueError, match=match):
        add_molecule(builder, data)
    assert builder.manifest["sections"] == []


def test_uhf_requires_equal_blocks():
    data = _molecule("sp", "UHF")
    data["Molecule"]["MolecularOrbitals"]["MOs"].pop()
    with pytest.raises(ValueError, match="equal alpha and beta"):
        _archive(data)
