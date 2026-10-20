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


@pytest.fixture
def labelled_points() -> tuple[list[str], np.ndarray]:
    return ["O", "H", "H", "C"], np.array(
        [[0.0, 0.0, 0.0], [5.0, 0.0, 0.0], [0.0, 6.0, 0.0], [0.0, 0.0, 7.0]]
    )


@pytest.mark.parametrize("angle", [0.0, 0.5, 1.5, 2.0, 2.5, np.pi])
def test_species_alignment_preserves_same_order_rotated_copy(
    labelled_points: tuple[list[str], np.ndarray], angle: float
) -> None:
    symbols, points = labelled_points
    c, s = np.cos(angle), np.sin(angle)
    rotation = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    reference = points @ rotation.T + [2.0, -3.0, 4.0]
    original_points, original_reference = points.copy(), reference.copy()

    result = align_by_species(symbols, points, symbols, reference)

    assert result is not None
    assert result.rmsd_after == pytest.approx(0.0, abs=1e-10)
    assert result.rmsd_before == pytest.approx(rmsd(points, reference))
    assert result.transformed == pytest.approx(reference, abs=1e-10)
    assert points @ result.rotation.T + result.translation == pytest.approx(result.transformed)
    assert np.linalg.det(result.rotation) == pytest.approx(1.0)
    np.testing.assert_array_equal(points, original_points)
    np.testing.assert_array_equal(reference, original_reference)


def test_species_alignment_still_matches_reordered_identical_elements(
    labelled_points: tuple[list[str], np.ndarray],
) -> None:
    symbols, reference = labelled_points
    permutation = [0, 2, 1, 3]
    points = reference[permutation] + [2.0, -3.0, 4.0]

    result = align_by_species(symbols, points, symbols, reference)

    assert result is not None
    assert result.rmsd_after == pytest.approx(0.0, abs=1e-10)
    assert result.transformed == pytest.approx(reference[permutation], abs=1e-10)
    assert points @ result.rotation.T + result.translation == pytest.approx(result.transformed)


def test_species_alignment_never_pairs_different_elements_by_index(
    labelled_points: tuple[list[str], np.ndarray],
) -> None:
    symbols, reference = labelled_points
    # Coordinates match by index, but oxygen and carbon have exchanged sites.
    # An index fit would incorrectly report zero for these labelled structures.
    result = align_by_species(["C", "H", "H", "O"], reference.copy(), symbols, reference)

    assert result is not None
    assert result.rmsd_after > 1e-3


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


class TestAlignModule:
    def test_rmsd_identical(self):
        from vibeview.align import rmsd

        pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
        assert rmsd(pos, pos) == pytest.approx(0.0)

    def test_rmsd_shifted(self):
        from vibeview.align import rmsd

        # This rmsd() is index-wise, no superposition — shifted != identical
        pos_a = np.array([[0, 0, 0], [1, 0, 0]], dtype=float)
        pos_b = np.array([[1, 0, 0], [2, 0, 0]], dtype=float)
        r = rmsd(pos_a, pos_b)
        # Each atom pair offset by (1,0,0) -> per-pair squared dist = 1.0
        assert r == pytest.approx(1.0)

    def test_rmsd_different(self):
        from vibeview.align import rmsd

        pos_a = np.array([[0, 0, 0], [1, 0, 0]], dtype=float)
        pos_b = np.array([[0, 0, 0], [0, 1, 0]], dtype=float)
        r = rmsd(pos_a, pos_b)
        assert r > 0.0

    def test_rmsd_shape_mismatch_raises(self):
        from vibeview.align import rmsd

        pos_a = np.array([[0, 0, 0], [1, 0, 0]], dtype=float)
        pos_b = np.array([[0, 0, 0]], dtype=float)
        with pytest.raises(ValueError):
            rmsd(pos_a, pos_b)
