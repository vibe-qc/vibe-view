"""Symmetrize-to-group (design refresh 2026: symmetry actions).

symmetrize_to_group finds the symmetry operations a near-symmetric
geometry supports, closes them into a group, and orbit-averages the
positions. The result must be *exactly* invariant — the function verifies
that internally and refuses rather than returning a half-symmetric
geometry.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from vibeview.symmetry import detect_point_group, symmetrize_to_group

_RNG = np.random.default_rng(42)


def _jitter(pos, amp=0.04):
    return np.asarray(pos, dtype=float) + _RNG.uniform(-amp, amp, size=np.shape(pos))


def _water():
    return ["O", "H", "H"], _jitter([[0, 0, 0.12], [0.76, 0, -0.48], [-0.76, 0, -0.48]])


def _ammonia():
    r, z_h = 0.94, -0.38
    pos = [[0, 0, 0]] + [
        [r * math.cos(2 * math.pi * k / 3), r * math.sin(2 * math.pi * k / 3), z_h]
        for k in range(3)
    ]
    return ["N", "H", "H", "H"], _jitter(pos)


def _benzene():
    rc, rh = 1.397, 2.481
    pos = [[rc * math.cos(math.pi * k / 3), rc * math.sin(math.pi * k / 3), 0] for k in range(6)]
    pos += [[rh * math.cos(math.pi * k / 3), rh * math.sin(math.pi * k / 3), 0] for k in range(6)]
    return ["C"] * 6 + ["H"] * 6, _jitter(pos, 0.02)


@pytest.mark.parametrize(
    ("builder", "expect_symbol", "expect_ops"),
    [
        (_water, "C2v", 4),
        (_ammonia, "C3v", 6),
        # Detection undersells benzene as D2h, but the operation closure
        # (perpendicular C2 axes x mirrors) generates the full D6h.
        (_benzene, "D6h", 24),
    ],
    ids=["water", "ammonia", "benzene"],
)
def test_symmetrize_recovers_the_group(builder, expect_symbol, expect_ops):
    symbols, pos = builder()
    res = symmetrize_to_group(symbols, pos)
    assert res.ok, res.reason
    assert res.symbol_after == expect_symbol
    assert res.n_operations == expect_ops
    # the applied displacement stays of the jitter's order
    assert res.max_shift < 0.15
    # re-detection on the result agrees
    assert detect_point_group(symbols, res.positions).symbol == expect_symbol


def test_symmetrized_geometry_has_exact_metric_symmetry():
    """Independent invariance check via metric invariants — no reliance on
    which inertia axis the detector labels "principal". Exact C2v water
    has exactly equal O-H distances; exact C3v ammonia has three equal
    N-H and three equal H-H distances. The input jitter breaks these at
    the 1e-2 level; symmetrization must restore them to machine noise.
    """
    symbols, pos = _water()
    res = symmetrize_to_group(symbols, pos)
    assert res.ok
    p = res.positions
    d_oh = [np.linalg.norm(p[0] - p[1]), np.linalg.norm(p[0] - p[2])]
    assert abs(d_oh[0] - d_oh[1]) < 1e-9, "O-H bond lengths differ"

    symbols, pos = _ammonia()
    # the jittered input genuinely breaks the equalities the check uses
    d_in = [np.linalg.norm(pos[0] - pos[k]) for k in (1, 2, 3)]
    assert max(d_in) - min(d_in) > 1e-3
    res = symmetrize_to_group(symbols, pos)
    assert res.ok
    p = res.positions
    d_nh = [np.linalg.norm(p[0] - p[k]) for k in (1, 2, 3)]
    d_hh = [np.linalg.norm(p[1] - p[2]), np.linalg.norm(p[2] - p[3]),
            np.linalg.norm(p[1] - p[3])]
    assert max(d_nh) - min(d_nh) < 1e-9, "N-H distances differ"
    assert max(d_hh) - min(d_hh) < 1e-9, "H-H distances differ"


def test_asymmetric_molecule_is_refused():
    symbols = ["C", "H", "F", "Cl"]
    pos = np.array([[0, 0, 0], [1.1, 0, 0], [-0.4, 1.2, 0], [-0.5, -0.6, 1.4]], float)
    res = symmetrize_to_group(symbols, pos)
    assert res.ok is False
    assert "no symmetry operations" in res.reason
    assert res.positions is None


def test_single_atom_and_empty_are_refused():
    assert symmetrize_to_group(["He"], np.zeros((1, 3))).ok is False
    assert symmetrize_to_group([], np.zeros((0, 3))).ok is False
