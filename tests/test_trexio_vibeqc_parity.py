"""Optional real-producer parity: SCF -> TREXIO -> QVF -> orbital values.

Runs where vibe-qc's native core is installed, like the existing libint parity
lane. The regular TREXIO tests require no producer. Use --basetemp to retain
these real exports, converted archives and JSON measurements for review.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from vibeview import __version__ as viewer_version
from vibeview.converters import convert_to_qvf
from vibeview.qvf import QVFReader
from vibeview.renderers.wavefunction import WavefunctionRenderer

vibeqc = pytest.importorskip("vibeqc")
pytest.importorskip("trexio")


@pytest.fixture(scope="module", params=["rhf", "uhf"])
def calculation(request):
    from vibeqc import Atom, BasisSet, Molecule

    if request.param == "rhf":
        molecule = Molecule(
            [
                Atom(8, [0.0, 0.0, 0.0]),
                Atom(1, [0.0, 1.43, -0.98]),
                Atom(1, [0.0, -1.43, -0.98]),
            ]
        )
        run_scf = vibeqc.run_rhf
    else:
        molecule = Molecule(
            [Atom(8, [0.0, 0.0, 0.0]), Atom(1, [0.0, 0.0, 1.83])],
            multiplicity=2,
        )
        run_scf = vibeqc.run_uhf
    basis = BasisSet(molecule, "def2-svp")
    result = run_scf(molecule, basis)
    assert result.converged
    return request.param, molecule, basis, result


@pytest.mark.parametrize("backend", ["hdf5", "text"])
def test_native_orbitals_survive_trexio_import(calculation, backend, tmp_path):
    from vibeqc.output.formats.trexio import write_trexio

    spin, molecule, basis, result = calculation
    source = tmp_path / (f"{spin}.hdf5" if backend == "hdf5" else f"{spin}.trexio")
    write_trexio(source, molecule, basis, result, backend=backend)
    payload = convert_to_qvf(source).getvalue()
    (tmp_path / f"{spin}.qvf").write_bytes(payload)
    positions = np.array([atom.xyz for atom in molecule.atoms])
    axes = [np.array([-1.3, 0.4, 1.1]), np.array([-0.7, 0.9, 1.5]), np.array([-1.1, 0.3, 0.8])]
    mesh = np.meshgrid(*axes, indexing="ij")
    points = np.stack([axis.ravel() for axis in mesh], axis=1)
    atomic_orbitals = np.asarray(vibeqc.evaluate_ao(basis, points))
    if atomic_orbitals.shape == (len(points), basis.nbasis):
        atomic_orbitals = atomic_orbitals.T
    assert atomic_orbitals.shape == (basis.nbasis, len(points))
    with QVFReader(payload) as reader:
        actual_positions = np.array([atom.position for atom in reader.read_structure().atoms])
        np.testing.assert_allclose(actual_positions, positions * 0.529177210903, atol=1e-12)
        for section in reader.sections:
            for member in section.members.values():
                reader._verify_and_read(member)
        renderer = WavefunctionRenderer(reader.get_section("wavefunction"), reader)
        wf = renderer.load()
        if spin == "rhf":
            blocks = [
                (
                    "restricted",
                    result.mo_coeffs,
                    result.mo_energies,
                    wf.mo_coefficients,
                    wf.energies,
                )
            ]
        else:
            blocks = [
                (
                    "alpha",
                    result.mo_coeffs_alpha,
                    result.mo_energies_alpha,
                    wf.mo_coefficients_alpha,
                    wf.alpha_energies,
                ),
                (
                    "beta",
                    result.mo_coeffs_beta,
                    result.mo_energies_beta,
                    wf.mo_coefficients_beta,
                    wf.beta_energies,
                ),
            ]
        measurements = []
        for label, native_coefficients, native_energies, coefficients, energies in blocks:
            np.testing.assert_allclose(energies, native_energies, rtol=1e-12, atol=1e-12)
            reference = np.asarray(native_coefficients).T @ atomic_orbitals
            actual = np.array(
                [
                    renderer._evaluate_on_grid(wf, row, positions, *axes).ravel()
                    for row in coefficients
                ]
            )
            np.testing.assert_allclose(actual, reference, rtol=1e-10, atol=1e-12)
            measurements.append(
                {
                    "spin": label,
                    "n_mo": len(coefficients),
                    "grid_points": len(points),
                    "max_absolute_error": float(np.max(np.abs(actual - reference))),
                }
            )
        (tmp_path / "validation.json").write_text(
            json.dumps(
                {
                    "source": source.name,
                    "backend": backend,
                    "basis": "def2-svp",
                    "n_ao": basis.nbasis,
                    "viewer_version": viewer_version,
                    "producer_version": vibeqc.__version__,
                    "scf_energy_hartree": result.energy,
                    "orbital_comparisons": measurements,
                },
                indent=2,
            )
            + "\n"
        )
