import numpy as np
import pytest

from vibeview.zmatrix import from_cartesian, parse_zmatrix


def aligned_error(a, b):
    a, b = a-a.mean(0), b-b.mean(0)
    u, _, v = np.linalg.svd(a.T @ b)
    d = np.eye(3)
    d[-1, -1] = np.linalg.det(u @ v)
    return np.max(np.linalg.norm(a @ u @ d @ v - b, axis=1))


@pytest.mark.parametrize("style", ["gaussian", "orca", "mopac"])
@pytest.mark.parametrize("geometry", ["ring", "linear", "chain", "disconnected"])
def test_roundtrip_with_dummy_anchors(style, geometry):
    if geometry == "ring":
        t = np.arange(6)*np.pi/3
        xyz = np.column_stack((np.cos(t), np.sin(t), .2*np.cos(2*t)))
        bonds = [(i, (i+1) % 6) for i in range(6)]
    elif geometry == "linear":
        xyz = np.column_stack((np.arange(6), np.zeros((6, 2))))
        bonds = [(i, i+1) for i in range(5)]
    else:
        xyz = np.random.default_rng(42).normal(size=(6, 3))
        bonds = [] if geometry == "disconnected" else [(i, i+1) for i in range(5)]
    matrix = from_cartesian(["C"]*6, xyz, bonds)
    symbols, rebuilt = parse_zmatrix(matrix.text(style), style).cartesian()
    assert symbols == ["C"]*6
    assert aligned_error(xyz, rebuilt) < 1e-6
    if bonds:
        # At least the second real atom uses its bonded predecessor.
        assert matrix.rows[4].references[0] == 3


def test_variables_and_water_angle():
    matrix = parse_zmatrix("O\nH 1 R\nH 1 R 2 A\n\nR=0.96\nA=104.5\n")
    _, xyz = matrix.cartesian()
    assert np.linalg.norm(xyz[1]-xyz[0]) == pytest.approx(.96)
    u, v = xyz[1]-xyz[0], xyz[2]-xyz[0]
    angle = np.degrees(np.arccos(np.dot(u, v)/np.linalg.norm(u)/np.linalg.norm(v)))
    assert angle == pytest.approx(104.5)
    _, rebuilt = parse_zmatrix(matrix.text(variables=True)).cartesian()
    np.testing.assert_allclose(xyz, rebuilt, atol=1e-10)


def test_dihedral_sign_and_chirality():
    positive = parse_zmatrix("C\nC 1 1.5\nC 2 1.5 1 90\nH 3 1 2 90 1 60")
    negative = parse_zmatrix("C\nC 1 1.5\nC 2 1.5 1 90\nH 3 1 2 90 1 -D\nD=60")
    from vibeview.geometry_edit import parameter_value
    _, p = positive.cartesian()
    _, n = negative.cartesian()
    assert parameter_value(p, [3, 2, 1, 0]) == pytest.approx(60)
    assert parameter_value(n, [3, 2, 1, 0]) == pytest.approx(-60)


@pytest.mark.parametrize("text", [
    "C\nH 3 1", "C\nH 1 BAD", "C\nH 1 -1", "C\nH 1 nan",
    "C\nH 1 1\nH 1 1 1 90", "C\nH 1 1\nH 2 1 1 180\nH 3 1 2 90 1 60",
])
def test_malformed_and_ambiguous_geometry_refused(text):
    with pytest.raises(ValueError):
        parse_zmatrix(text)
