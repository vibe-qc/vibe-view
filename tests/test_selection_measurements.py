"""Hand-computable geometric oracles, invariance and degeneracy checks."""
import numpy as np
import pytest

from vibeview.selection_measurements import (
    Plane,
    best_fit_plane,
    centroid,
    plane_angle,
    point_plane_distance,
    principal_axes,
    selection_rmsd,
)


def test_centroid_covariance_and_axes():
    points = np.array([[3., 0, 0], [-3, 0, 0], [0, 2, 0], [0, -2, 0],
                       [0, 0, 1], [0, 0, -1]]) + [5, 6, 7]
    np.testing.assert_allclose(centroid(points), [5, 6, 7])
    axes = principal_axes(points)
    np.testing.assert_allclose(axes.variances, [3, 4/3, 1/3])
    np.testing.assert_allclose(axes.axes, np.eye(3))
    assert axes.unique
    np.testing.assert_allclose(axes.axes @ axes.axes.T, np.eye(3))
    assert np.linalg.det(axes.axes) == pytest.approx(1)


def test_plane_distance_angle_and_translation():
    xy = np.array([[0., 0, 2], [2, 0, 2], [0, 2, 2], [2, 2, 2]])
    plane = best_fit_plane(xy)
    assert plane.rms_distance == pytest.approx(0)
    np.testing.assert_allclose(plane.normal, [0, 0, 1])
    assert point_plane_distance(np.array([4., 6, -1]), plane) == pytest.approx(3)
    yz = best_fit_plane(np.array([[3., 0, 0], [3, 1, 0], [3, 0, 1]]))
    assert plane_angle(plane, yz) == pytest.approx(90)
    tilted = Plane(np.zeros(3), np.array([1., 0, 1]), 0)
    assert plane_angle(plane, tilted) == pytest.approx(45)
    assert plane_angle(plane, Plane(plane.centre, -plane.normal, 0)) == pytest.approx(0)
    translated = best_fit_plane(xy + [7, 8, 9])
    assert point_plane_distance(np.array([4., 6, -1]) + [7, 8, 9], translated) == pytest.approx(3)


def test_rmsd_translation_and_chirality():
    points = np.array([[0., 0, 0], [1, 0, 0], [0, 2, 0], [0, 0, 3]])
    assert selection_rmsd(points, points + [1, 2, 2]) == pytest.approx(3)
    assert selection_rmsd(points, points + [1, 2, 2], superpose=True) < 1e-14
    mirrored = points * [-1, 1, 1]
    assert selection_rmsd(points, mirrored, superpose=True) > .1


def test_degenerate_subspaces_and_planes():
    assert not principal_axes(np.zeros((1, 3))).unique
    assert not principal_axes(np.eye(3)).unique
    for points in [np.zeros((3, 3)), np.array([[0., 0, 0], [1, 0, 0], [2, 0, 0]]),
                   np.vstack((np.eye(3), -np.eye(3)))]:
        with pytest.raises(ValueError, match='unique plane'):
            best_fit_plane(points)


@pytest.mark.parametrize('points', [np.empty((0, 3)), np.ones(3), np.ones((3, 2)),
                                    np.array([[np.nan, 0, 0]])])
def test_bad_coordinates(points):
    for function in [centroid, principal_axes, best_fit_plane]:
        with pytest.raises(ValueError):
            function(points)


def test_invalid_correspondence_and_normal():
    with pytest.raises(ValueError, match='same number'):
        selection_rmsd(np.ones((2, 3)), np.ones((3, 3)))
    with pytest.raises(ValueError, match='nonzero'):
        point_plane_distance(np.zeros(3), Plane(np.zeros(3), np.zeros(3), 0))


def test_rotated_plane_and_axis_covariance():
    angle = np.pi / 4
    rotation = np.array([[np.cos(angle), 0, np.sin(angle)],
                         [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]])
    points = np.array([[3., 0, 0], [-3, 0, 0], [0, 2, 0], [0, -2, 0]])
    transformed = points @ rotation.T + [4, 5, 6]
    plane = best_fit_plane(transformed)
    expected_normal = rotation[:, 2]
    assert abs(np.dot(plane.normal, expected_normal)) == pytest.approx(1)
    axes = principal_axes(transformed)
    np.testing.assert_allclose(axes.variances, [4.5, 2, 0], atol=1e-14)
    np.testing.assert_allclose(np.abs(axes.axes @ rotation), np.eye(3), atol=1e-14)
    assert selection_rmsd(points, transformed, superpose=True) < 1e-14
