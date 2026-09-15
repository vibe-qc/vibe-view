"""Gamma-point Bloch sums on one primitive cell, without a QC dependency."""

from __future__ import annotations

from dataclasses import replace
from itertools import product

import numpy as np

from vibeview.qvf import GridData

_BOHR_TO_ANGSTROM = 0.529177210903


def evaluate_gamma_mo(renderer, wf, coefficients, n_per_dim):
    """Sum translated atomic orbitals before contouring, retaining complex phase.

    Bloch, Z. Phys. 52, 555–600 (1929), doi:10.1007/BF01339455:
    at Gamma the lattice phase exp(i k.T) is one. This is the same lattice
    sum defined by PySCF's pbc.gto.eval_gto; no per-orbital renormalization
    or phase rotation is applied. Nonzero k needs a declared k-vector unit
    convention, which older QVF metadata does not provide.
    """
    if np.any(np.abs(wf.k_point) > 1e-12):
        raise ValueError("Non-Gamma Bloch orbitals are not supported; use a stored orbital grid")
    structure = renderer.reader.read_structure(wf.structure_ref)
    if not all(structure.pbc) or structure.lattice_vectors is None:
        raise ValueError("Gamma Bloch evaluation requires a fully periodic 3D cell")
    n = int(n_per_dim)
    if not 2 <= n <= 120:
        raise ValueError("Periodic orbital grid must have 2–120 points per axis")
    lattice = np.asarray(structure.lattice_vectors) / _BOHR_TO_ANGSTROM
    inverse = np.linalg.inv(lattice)
    axis = np.linspace(0.0, 1.0, n)
    fractional = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1)
    points = (fractional @ lattice).reshape(-1, 3)
    lo, hi = points.min(axis=0), points.max(axis=0)
    positions = np.asarray([atom.position for atom in structure.atoms]) / _BOHR_TO_ANGSTROM
    values = np.zeros(len(points), dtype=np.result_type(coefficients.dtype, float))
    index = 0
    dropped = 0.0
    dropped_l = 0
    weight = float(np.sum(np.abs(coefficients) ** 2)) or 1.0
    for shell in wf.shells:
        width = renderer._n_ao_per_shell(shell)
        block = coefficients[index : index + width]
        index += width
        if len(block) != width:
            raise ValueError("Periodic basis / MO-width mismatch")
        if shell.l > 3:
            dropped += float(np.sum(np.abs(block) ** 2))
            dropped_l = max(dropped_l, shell.l)
            continue
        if not 0 <= shell.center < len(positions):
            raise ValueError("Periodic shell references an atom outside the structure")
        if len(shell.exponents) != len(shell.coefficients) or not len(shell.exponents):
            raise ValueError("Periodic shell needs matching nonempty exponents and contractions")
        if not np.any(block):
            continue
        if np.any(shell.exponents <= 0) or not np.isfinite(shell.exponents).all():
            raise ValueError("Periodic Gaussian exponents must be finite and positive")
        # A conservative Gaussian-tail radius, including an angular-polynomial
        # margin. The generous exponent cutoff makes image truncation smaller
        # than the float32 field precision for ordinary normalized s/p/d/f bases.
        radius = np.sqrt((40.0 + 2 * shell.l) / float(np.min(shell.exponents)))
        center = positions[shell.center]
        center_fractional = center @ inverse
        extent = radius * np.linalg.norm(inverse, axis=0)
        first = np.floor(-center_fractional - extent).astype(int)
        last = np.ceil(1 - center_fractional + extent).astype(int)
        count = int(np.prod(last - first + 1))
        if count > 100_000:
            raise ValueError(
                "Basis is too diffuse for interactive lattice sums; use a stored orbital grid"
            )
        single = replace(wf, shells=[replace(shell, center=0)], n_ao=width)
        for translation in product(*(range(a, b + 1) for a, b in zip(first, last, strict=True))):
            translated = center + np.asarray(translation) @ lattice
            outside = np.maximum(np.maximum(lo - translated, translated - hi), 0)
            if outside @ outside > radius * radius:
                continue
            delta = points - translated
            mask = np.einsum("ij,ij->i", delta, delta) <= radius * radius
            if not np.any(mask):
                continue
            xyz = points[mask]
            values[mask] += renderer._evaluate_at_points(
                single, block, translated[None, :], xyz[:, 0], xyz[:, 1], xyz[:, 2]
            )
    renderer.last_dropped_l_fraction = dropped / weight
    renderer.last_dropped_l_max = dropped_l
    grid = GridData(
        origin=np.zeros(3),
        voxel_vectors=np.asarray(structure.lattice_vectors) / (n - 1),
        shape=(n, n, n),
    )
    return grid, values.reshape(grid.shape)
