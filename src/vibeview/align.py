"""Structure superposition for cross-system comparison (Phase D4).

Kabsch least-squares fit: the rigid rotation (+ translation) that minimizes the
RMSD between two index-corresponded point sets. Kabsch, Acta Crystallogr. A 32,
922 (1976), doi:10.1107/S0567739476001873.

Used by vibe-view's compare/overlay mode to align loaded structures to a
reference before drawing them, so the residual displacement (not a difference in
coordinate frame) is what the user sees.

Two API layers live here:

* ``kabsch_fit`` / ``rmsd`` — the original index-corresponded primitives used by
  the batch/compare paths (``api.py``, ``cli.py``, ``batch_compare.py``,
  ``app.py``).
* ``kabsch`` / ``align`` / ``align_by_species`` / :class:`AlignResult` — the math
  layer for the 2026 design-refresh roadmap item "Geometry overlay/diff of two
  open files: align (Kabsch), RMSD readout, ghost second structure". These return
  the rotation/translation explicitly plus a before/after RMSD readout, and add a
  species-aware pairing helper for the two-open-files overlay. The UI wiring (the
  ghost structure, the RMSD readout widget) lives in ``app.py`` and is not part of
  this module.

Pure numpy, no other dependencies.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


def kabsch_fit(mobile: np.ndarray, ref: np.ndarray) -> tuple[np.ndarray, float]:
    """Superpose ``mobile`` onto ``ref``; return ``(aligned_mobile, rmsd)``.

    Both arrays are ``[N, 3]`` with row-wise (index) atom correspondence. The
    optimal rotation is taken from the SVD of the cross-covariance
    ``H = P0ᵀ Q0`` (P0/Q0 = centred coordinates); the determinant guard

        d = sign(det(V Uᵀ));  R = V · diag(1, 1, d) · Uᵀ

    forces ``det(R) = +1`` so a proper rotation is always returned, never a
    roto-reflection (mirroring a chiral structure onto its enantiomer). Kabsch
    (1976).
    """
    P = np.asarray(mobile, dtype=float)
    Q = np.asarray(ref, dtype=float)
    if P.ndim != 2 or P.shape[1] != 3 or P.shape != Q.shape:
        raise ValueError(
            f"kabsch_fit: need matching [N, 3] arrays, got {P.shape} vs {Q.shape}"
        )
    centroid_p = P.mean(axis=0)
    centroid_q = Q.mean(axis=0)
    p0 = P - centroid_p
    q0 = Q - centroid_q

    u, _s, vt = np.linalg.svd(p0.T @ q0)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    rot = vt.T @ np.diag([1.0, 1.0, d]) @ u.T

    aligned = p0 @ rot.T + centroid_q
    rmsd_val = float(np.sqrt(np.mean(np.sum((aligned - Q) ** 2, axis=1))))
    return aligned, rmsd_val


def rmsd(a: np.ndarray, b: np.ndarray) -> float:
    """Index-wise RMSD between two ``[N, 3]`` coordinate sets (no superposition)."""
    arr_a = np.asarray(a, dtype=float)
    arr_b = np.asarray(b, dtype=float)
    if arr_a.shape != arr_b.shape:
        raise ValueError(f"rmsd: shape mismatch {arr_a.shape} vs {arr_b.shape}")
    return float(np.sqrt(np.mean(np.sum((arr_a - arr_b) ** 2, axis=1))))


# ---------------------------------------------------------------------------
# Overlay/diff math layer (2026 design-refresh roadmap).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlignResult:
    """Outcome of aligning point set ``P`` onto ``Q``.

    Attributes:
        rotation: ``(3, 3)`` proper rotation matrix (``det == +1``).
        translation: ``(3,)`` translation applied after rotation.
        rmsd_before: RMSD of ``P`` vs ``Q`` before alignment.
        rmsd_after: RMSD of the aligned copy vs ``Q``.
        transformed: ``(N, 3)`` aligned copy of ``P`` (``rotation @ P_i + translation``).
    """

    rotation: np.ndarray
    translation: np.ndarray
    rmsd_before: float
    rmsd_after: float
    transformed: np.ndarray


def _as_points(a: np.ndarray, name: str) -> np.ndarray:
    """Coerce to a validated ``(N, 3)`` float array (N >= 1)."""
    arr = np.asarray(a, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(f"{name} must have shape (N, 3), got {arr.shape}")
    if arr.shape[0] < 1:
        raise ValueError(f"{name} must contain at least one point")
    return arr


def _require_paired(p: np.ndarray, q: np.ndarray) -> None:
    if p.shape != q.shape:
        raise ValueError(f"P and Q must have the same shape, got {p.shape} vs {q.shape}")


def kabsch(P: np.ndarray, Q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Optimal rigid rotation + translation mapping ``P`` onto ``Q``.

    Minimises the RMSD of ``R @ P_i + t`` against ``Q_i`` over proper rotations
    ``R`` (no reflection) and translations ``t``.

    Kabsch, W. "A solution for the best rotation to relate two sets of vectors."
    Acta Cryst. A32, 922-923 (1976), doi:10.1107/S0567739476001873. Given centred
    coordinates P_c, Q_c, form the covariance H = P_c^T Q_c, take its SVD
    H = U S V^T, and the optimal rotation is R = V D U^T where
    D = diag(1, 1, sign(det(V U^T))) removes the improper (reflection) branch that
    plain SVD would otherwise admit.

    Args:
        P, Q: ``(N, 3)`` arrays of matching length.

    Returns:
        ``(R, t)`` with ``R`` a ``(3, 3)`` proper rotation and ``t`` a ``(3,)``
        translation, such that ``R @ P_i + t`` approximates ``Q_i``.
    """
    p = _as_points(P, "P")
    q = _as_points(Q, "Q")
    _require_paired(p, q)

    p_centroid = p.mean(axis=0)
    q_centroid = q.mean(axis=0)
    p_c = p - p_centroid
    q_c = q - q_centroid

    # Covariance matrix H = P_c^T Q_c, then SVD (Kabsch 1976, eqs. 1-6).
    h = p_c.T @ q_c
    u, _s, vt = np.linalg.svd(h)
    v = vt.T

    # Reflection correction: d = sign(det(V U^T)) forces a proper rotation
    # (det R = +1) instead of the reflection SVD would otherwise pick.
    d = np.sign(np.linalg.det(v @ u.T))
    correction = np.diag(np.array([1.0, 1.0, d]))
    r = v @ correction @ u.T

    t = q_centroid - r @ p_centroid
    return r, t


def align(P: np.ndarray, Q: np.ndarray) -> AlignResult:
    """Align ``P`` onto ``Q`` via Kabsch and report before/after RMSD.

    Args:
        P, Q: ``(N, 3)`` arrays of matching length, paired atom-for-atom.

    Returns:
        An :class:`AlignResult` carrying the transform, the aligned copy of ``P``,
        and the RMSD before and after alignment.
    """
    p = _as_points(P, "P")
    q = _as_points(Q, "Q")
    _require_paired(p, q)

    rmsd_before = rmsd(p, q)
    r, t = kabsch(p, q)
    transformed = (r @ p.T).T + t
    rmsd_after = rmsd(transformed, q)
    return AlignResult(
        rotation=r,
        translation=t,
        rmsd_before=rmsd_before,
        rmsd_after=rmsd_after,
        transformed=transformed,
    )


def _greedy_pair_by_element(
    p_symbols: Sequence[str],
    p: np.ndarray,
    q_symbols: Sequence[str],
    q: np.ndarray,
) -> np.ndarray | None:
    """Reorder ``P`` so its atoms line up with ``Q`` per element.

    Centroid-shifts both sets, then, within each element symbol, greedily pairs
    each ``Q`` atom to its nearest unused ``P`` atom of the same element. Returns
    the permutation index array ``perm`` such that ``perm[j]`` is the ``P`` atom
    paired with ``Q`` atom ``j``, or ``None`` if the element multisets differ.

    Limitation: this is a per-element greedy nearest-neighbour match, not a graph
    isomorphism. Inputs already in the same atom ordering pair exactly; permuted
    inputs pair correctly when atoms of a given element are clearly separated, but
    greedy matching can mispair symmetric or nearly coincident atoms.
    """
    if Counter(p_symbols) != Counter(q_symbols):
        return None

    # Centroid-shift decouples the pairing from an overall translation.
    p_c = p - p.mean(axis=0)
    q_c = q - q.mean(axis=0)

    n = len(p_symbols)
    perm = np.full(n, -1, dtype=int)
    used = np.zeros(n, dtype=bool)

    for element in Counter(q_symbols):
        p_idx = [i for i, s in enumerate(p_symbols) if s == element]
        q_idx = [j for j, s in enumerate(q_symbols) if s == element]
        for j in q_idx:
            best_i = -1
            best_d = np.inf
            for i in p_idx:
                if used[i]:
                    continue
                d = float(np.sum((p_c[i] - q_c[j]) ** 2))
                if d < best_d:
                    best_d = d
                    best_i = i
            used[best_i] = True
            perm[j] = best_i

    return perm


def align_by_species(
    p_symbols: Sequence[str],
    P: np.ndarray,
    q_symbols: Sequence[str],
    Q: np.ndarray,
) -> AlignResult | None:
    """Align two labelled structures, pairing atoms by element.

    Only proceeds when the two structures have the same atom count *and* the same
    multiset of element symbols; otherwise returns ``None`` (no exception). Atoms
    are paired within each element by greedy nearest-neighbour after a centroid
    shift, then the paired coordinates go through :func:`align`.

    Args:
        p_symbols: element symbols for ``P`` (length N).
        P: ``(N, 3)`` coordinates for the structure to be moved.
        q_symbols: element symbols for ``Q`` (length M).
        Q: ``(M, 3)`` coordinates for the reference structure.

    Returns:
        An :class:`AlignResult` (with ``transformed`` ordered to match the original
        ``P`` rows), or ``None`` if counts or species differ.

    Limitation: no graph isomorphism. Same-ordering inputs align exactly; permuted
    inputs align approximately (see :func:`_greedy_pair_by_element`).
    """
    p = _as_points(P, "P")
    q = _as_points(Q, "Q")
    if len(p_symbols) != p.shape[0]:
        raise ValueError("p_symbols length must match number of rows in P")
    if len(q_symbols) != q.shape[0]:
        raise ValueError("q_symbols length must match number of rows in Q")
    if p.shape[0] != q.shape[0]:
        return None

    perm = _greedy_pair_by_element(p_symbols, p, q_symbols, q)
    if perm is None:
        return None

    # perm[j] is the P-atom paired with Q-atom j: reorder P to line up with Q.
    p_reordered = p[perm]
    result = align(p_reordered, q)

    # Restore the original P row order in the transformed output so callers can
    # overlay the ghost structure against their own atom list.
    inverse = np.empty_like(perm)
    inverse[perm] = np.arange(perm.shape[0])
    transformed_in_p_order = result.transformed[inverse]
    return AlignResult(
        rotation=result.rotation,
        translation=result.translation,
        rmsd_before=result.rmsd_before,
        rmsd_after=result.rmsd_after,
        transformed=transformed_in_p_order,
    )
