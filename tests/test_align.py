"""Tests for the Kabsch geometry-overlay math layer (``vibeview.align``)."""

from __future__ import annotations

import numpy as np
import pytest

from vibeview.align import (
    AlignResult,
    align,
    align_by_species,
    kabsch,
    rmsd,
)


def _random_rotation(rng: np.random.Generator) -> np.ndarray:
    """A random proper rotation via QR of a Gaussian matrix (det forced +1)."""
    q, r = np.linalg.qr(rng.standard_normal((3, 3)))
    q = q @ np.diag(np.sign(np.diag(r)))
    if np.linalg.det(q) < 0:
        q[:, 0] = -q[:, 0]
    return q


def test_exact_recovery_of_rotation_and_translation() -> None:
    rng = np.random.default_rng(0)
    P = rng.standard_normal((10, 3))
    R_true = _random_rotation(rng)
    t_true = rng.standard_normal(3)
    Q = (R_true @ P.T).T + t_true

    result = align(P, Q)

    assert result.rmsd_after < 1e-10
    # Recovered rotation is a proper rotation.
    assert np.linalg.det(result.rotation) == pytest.approx(1.0, abs=1e-9)
    assert result.rotation @ result.rotation.T == pytest.approx(np.eye(3), abs=1e-9)
    # And it matches the applied transform.
    assert result.rotation == pytest.approx(R_true, abs=1e-9)
    assert result.transformed == pytest.approx(Q, abs=1e-9)


def test_reflection_is_not_matched_by_improper_rotation() -> None:
    rng = np.random.default_rng(1)
    P = rng.standard_normal((10, 3))
    # Mirror through the xy-plane -> a chiral structure that no proper rotation
    # can superpose onto the original.
    Q = P.copy()
    Q[:, 2] = -Q[:, 2]

    r, _t = kabsch(P, Q)
    assert np.linalg.det(r) == pytest.approx(1.0, abs=1e-9)

    result = align(P, Q)
    assert np.linalg.det(result.rotation) == pytest.approx(1.0, abs=1e-9)
    # A reflection cannot be undone by a proper rotation: residual stays positive.
    assert result.rmsd_after > 1e-3


def test_rmsd_before_greater_than_after_on_perturbed_copy() -> None:
    rng = np.random.default_rng(2)
    P = rng.standard_normal((10, 3))
    R_true = _random_rotation(rng)
    t_true = rng.standard_normal(3)
    Q = (R_true @ P.T).T + t_true + 0.01 * rng.standard_normal((10, 3))

    result = align(P, Q)
    assert result.rmsd_before > result.rmsd_after
    assert result.rmsd_after < result.rmsd_before  # sanity, symmetric statement


def test_align_by_species_permuted_within_element() -> None:
    # Clearly separated atoms so the greedy per-element pairing is unambiguous.
    symbols = ["O", "H", "H", "C"]
    Q = np.array(
        [
            [0.0, 0.0, 0.0],   # O
            [5.0, 0.0, 0.0],   # H
            [0.0, 5.0, 0.0],   # H
            [0.0, 0.0, 5.0],   # C
        ]
    )
    # Same structure, atoms permuted (the two H swapped, O/C reordered).
    perm = [3, 2, 1, 0]
    p_symbols = [symbols[i] for i in perm]
    P = Q[perm].copy()

    result = align_by_species(p_symbols, P, symbols, Q)
    assert isinstance(result, AlignResult)
    assert result.rmsd_after < 1e-6
    # transformed is returned in P's own row order.
    assert result.transformed.shape == P.shape


def test_align_by_species_returns_none_on_mismatch() -> None:
    P = np.zeros((3, 3))
    Q = np.zeros((3, 3))
    # Different species multiset, same count.
    assert align_by_species(["H", "H", "O"], P, ["H", "O", "O"], Q) is None
    # Different count.
    assert (
        align_by_species(["H", "H"], np.zeros((2, 3)), ["H", "H", "H"], np.zeros((3, 3)))
        is None
    )


def test_value_errors_on_degenerate_inputs() -> None:
    good = np.zeros((4, 3))

    # Shape mismatch (paired).
    with pytest.raises(ValueError):
        kabsch(good, np.zeros((5, 3)))
    with pytest.raises(ValueError):
        align(good, np.zeros((5, 3)))

    # Wrong dimensionality.
    with pytest.raises(ValueError):
        kabsch(np.zeros((4, 2)), np.zeros((4, 2)))

    # Empty (N < 1).
    with pytest.raises(ValueError):
        align(np.zeros((0, 3)), np.zeros((0, 3)))

    # rmsd shape mismatch.
    with pytest.raises(ValueError):
        rmsd(good, np.zeros((5, 3)))

    # Symbol-length mismatch in align_by_species.
    with pytest.raises(ValueError):
        align_by_species(["H", "H"], good, ["H"] * 4, good)
