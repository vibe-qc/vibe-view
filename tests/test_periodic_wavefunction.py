"""Complex archive decoding and Gamma Bloch sums without a producer dependency."""

from __future__ import annotations

import hashlib
import json
import zipfile
from itertools import product

import numpy as np
import pytest

from vibeview.qvf import QVFError, QVFReader
from vibeview.renderers.wavefunction import WavefunctionRenderer

BOHR = 0.529177210903


def write_wavefunction(
    tmp_path,
    *,
    unrestricted=False,
    encoding="complex_split_last_axis",
    k_point=(0, 0, 0),
    shape=None,
    lattice=None,
    alpha=0.8,
):
    lattice = np.eye(3) * 4 if lattice is None else np.asarray(lattice)
    coefficients = np.array([[[0.6, 0.8]]], dtype=np.float64)
    structure = {
        "atoms": [{"symbol": "He", "atomic_number": 2, "position": [0, 0, 0]}],
        "pbc": [True] * 3,
        "lattice_vectors": (lattice * BOHR).tolist(),
    }
    basis = {
        "structure_ref": "structure",
        "pure": True,
        "n_ao": 1,
        "shells": [{"center": 0, "l": 0, "exponents": [alpha], "coefficients": [1], "pure": True}],
    }
    meta = {
        "n_mo": 1,
        "n_ao": 1,
        "spin": "unrestricted" if unrestricted else "restricted",
        "energies": [-1],
        "occupations": [2],
        "coefficient_encoding": encoding,
        "coefficient_components": ["real", "imag"],
        "alpha": {"energies": [-1], "occupations": [1]},
        "beta": {"energies": [-1], "occupations": [1]},
    }
    if k_point is not None:
        meta["k_point"] = k_point
    blobs = {}

    def member(name, data, *, binary=False):
        blob = data.tobytes() if binary else json.dumps(data).encode()
        blobs[name] = blob
        result = {
            "path": name,
            "format": "binary" if binary else "json",
            "sha256": hashlib.sha256(blob).hexdigest(),
        }
        if binary:
            result.update(dtype="float64", shape=shape or list(data.shape))
        return result

    members = {"basis": member("basis.json", basis), "mo_metadata": member("meta.json", meta)}
    for key in (
        ["mo_coefficients_alpha", "mo_coefficients_beta"] if unrestricted else ["mo_coefficients"]
    ):
        members[key] = member(key + ".bin", coefficients, binary=True)
    manifest = {
        "qvf_version": 1,
        "source": {"program": "test", "version": "1", "calculation": "test"},
        "provenance": {"basis": "test"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {"structure": member("structure.json", structure)},
            },
            {"id": "wf", "kind": "wavefunction.gto", "members": members},
        ],
    }
    path = tmp_path / "periodic.qvf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, blob in blobs.items():
            archive.writestr(name, blob)
    return path


@pytest.mark.parametrize("unrestricted", [False, True])
def test_complex_coefficients_keep_both_components(tmp_path, unrestricted):
    with QVFReader(write_wavefunction(tmp_path, unrestricted=unrestricted)) as reader:
        wf = reader.read_wavefunction_gto("wf")
        blocks = (
            (wf.mo_coefficients_alpha, wf.mo_coefficients_beta)
            if unrestricted
            else (wf.mo_coefficients,)
        )
        for block in blocks:
            assert block.shape == (1, 1)
            np.testing.assert_allclose(block, [[0.6 + 0.8j]])
        np.testing.assert_array_equal(wf.k_point, [0, 0, 0])


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"shape": [1, 2]}, "shape"),
        ({"encoding": "unknown"}, "encoding"),
        ({"k_point": [0, 0]}, "k_point"),
    ],
)
def test_malformed_complex_metadata_is_reported(tmp_path, kwargs, message):
    with (
        QVFReader(write_wavefunction(tmp_path, **kwargs)) as reader,
        pytest.raises(QVFError, match=message),
    ):
        reader.read_wavefunction_gto("wf")


def test_gamma_matches_independent_gaussian_lattice_sum(tmp_path):
    lattice = np.array([[4, 0, 0], [1, 3.5, 0], [0.2, 0.4, 4.2]])
    with QVFReader(write_wavefunction(tmp_path, lattice=lattice)) as reader:
        renderer = WavefunctionRenderer(reader.get_section("wf"), reader)
        grid, real = renderer.evaluate_mo(0, n_per_dim=9)
        _, imag = renderer.evaluate_mo(0, n_per_dim=9, component="imag")
        _, magnitude = renderer.evaluate_mo(0, n_per_dim=9, component="magnitude")
        fractional = np.stack(np.meshgrid(*([np.linspace(0, 1, 9)] * 3), indexing="ij"), axis=-1)
        points = fractional @ lattice
        expected = np.zeros(real.shape)
        # Independent analytic normalized s Gaussian, deliberately oversized image cube.
        for translation in product(range(-5, 6), repeat=3):
            delta = points - np.asarray(translation) @ lattice
            expected += (2 * 0.8 / np.pi) ** 0.75 * np.exp(-0.8 * np.sum(delta**2, axis=-1))
        np.testing.assert_allclose(real, 0.6 * expected, atol=3e-8)
        np.testing.assert_allclose(imag, 0.8 * expected, atol=3e-8)
        np.testing.assert_allclose(magnitude, expected, atol=3e-8)
        np.testing.assert_allclose(grid.voxel_vectors * 8, lattice * BOHR)
        for axis in range(3):
            np.testing.assert_allclose(
                np.take(real, 0, axis=axis), np.take(real, -1, axis=axis), atol=1e-7
            )
        # Neighbor tails must contribute: the field is not an isolated atomic Gaussian.
        assert real[0, 0, 0] > 0.6 * (2 * 0.8 / np.pi) ** 0.75


def test_non_gamma_and_incomplete_periodic_fields_fail_explicitly(tmp_path):
    with QVFReader(write_wavefunction(tmp_path, k_point=[0.1, 0, 0])) as reader:
        renderer = WavefunctionRenderer(reader.get_section("wf"), reader)
        with pytest.raises(ValueError, match="Non-Gamma"):
            renderer.evaluate_mo(0, n_per_dim=9)
        for evaluate in (
            renderer.evaluate_density,
            renderer.evaluate_elf,
            renderer.evaluate_nci,
            renderer.evaluate_density_laplacian,
        ):
            with pytest.raises(ValueError, match="single k-point"):
                evaluate(n_per_dim=9)


def test_complex_molecular_density_uses_modulus_squared(tmp_path):
    with QVFReader(write_wavefunction(tmp_path, k_point=None)) as reader:
        renderer = WavefunctionRenderer(reader.get_section("wf"), reader)
        _, density, _ = renderer.evaluate_density(n_per_dim=15)
        _, real = renderer.evaluate_mo(0, n_per_dim=15)
        _, imag = renderer.evaluate_mo(0, n_per_dim=15, component="imag")
        np.testing.assert_allclose(density, 2 * (real**2 + imag**2), atol=1e-7)


def test_localizer_rejects_periodic_structure_before_worker(tmp_path):
    from vibeview.relocalize import request_from_reader

    with (
        QVFReader(write_wavefunction(tmp_path)) as reader,
        pytest.raises(ValueError, match="Periodic re-localization"),
    ):
        request_from_reader(reader, "ibo")


def test_diffuse_gamma_sum_matches_separable_analytic_sum(tmp_path):
    alpha = 0.048
    with QVFReader(write_wavefunction(tmp_path, alpha=alpha)) as reader:
        renderer = WavefunctionRenderer(reader.get_section("wf"), reader)
        _, magnitude = renderer.evaluate_mo(0, n_per_dim=9, component="magnitude")
        x = np.linspace(0, 4, 9)
        one_dimensional = sum(np.exp(-alpha * (x - 4 * t) ** 2) for t in range(-25, 26))
        expected = (
            (2 * alpha / np.pi) ** 0.75
            * one_dimensional[:, None, None]
            * one_dimensional[None, :, None]
            * one_dimensional[None, None, :]
        )
        np.testing.assert_allclose(magnitude, expected, rtol=1e-7)


def test_excessive_lattice_sum_stops_before_allocating_images(tmp_path):
    with QVFReader(write_wavefunction(tmp_path, alpha=1e-8)) as reader:
        renderer = WavefunctionRenderer(reader.get_section("wf"), reader)
        with pytest.raises(ValueError, match="too diffuse"):
            renderer.evaluate_mo(0, n_per_dim=3)
