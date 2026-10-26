"""Finite Cartesian selection measurements; distances follow input coordinate units.

Planes and principal axes use the eigenvectors of the unweighted centred
coordinate covariance (orthogonal least squares). No masses or periodic images
are inferred. Degenerate principal subspaces have no unique axis orientation.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from vibeview.align import kabsch_fit, rmsd


def _points(points: np.ndarray, minimum: int = 1) -> np.ndarray:
    values = np.asarray(points, dtype=float)
    if values.ndim != 2 or values.shape[1] != 3 or len(values) < minimum:
        raise ValueError(f"Need at least {minimum} Cartesian points with shape (N, 3)")
    if not np.isfinite(values).all():
        raise ValueError("Coordinates must be finite")
    return values


def centroid(points: np.ndarray) -> np.ndarray:
    """Unweighted geometric centre; an empty selection is an error."""
    return np.asarray(_points(points).mean(axis=0))


@dataclass(frozen=True)
class PrincipalAxes:
    centre: np.ndarray
    variances: np.ndarray
    axes: np.ndarray
    unique: bool


def principal_axes(points: np.ndarray) -> PrincipalAxes:
    """Return descending population variances and corresponding unit axes (rows).

    Eigenvector signs put the largest-magnitude component positive, followed by
    a right-handed basis correction. Repeated eigenvalues flag ``unique=False``;
    axes inside such a subspace must not be interpreted as physical directions.
    """
    values = _points(points)
    centre = centroid(values)
    centred = values - centre
    eigenvalues, vectors = np.linalg.eigh(centred.T @ centred / len(values))
    variances = np.maximum(eigenvalues[::-1], 0)
    axes = vectors[:, ::-1].T.copy()
    for axis in axes:
        if axis[np.argmax(np.abs(axis))] < 0:
            axis *= -1
    if np.linalg.det(axes) < 0:
        axes[-1] *= -1
    scale = float(variances[0])
    unique = scale > 0 and bool(np.all(np.abs(np.diff(variances)) > scale * 1e-12))
    return PrincipalAxes(centre, variances, axes, unique)


@dataclass(frozen=True)
class Plane:
    centre: np.ndarray
    normal: np.ndarray
    rms_distance: float


def best_fit_plane(points: np.ndarray) -> Plane:
    """Least-squares plane; refuse collinear or nonunique normal directions."""
    values = _points(points, minimum=3)
    axes = principal_axes(values)
    scale = float(axes.variances[0])
    if scale == 0 or axes.variances[1] - axes.variances[2] <= scale * 1e-12:
        raise ValueError("Selection does not define a unique plane normal")
    normal = axes.axes[-1].copy()
    if normal[np.argmax(np.abs(normal))] < 0:
        normal *= -1
    distances = (values - axes.centre) @ normal
    return Plane(axes.centre, normal, float(np.sqrt(np.mean(distances**2))))


def _plane_normal(plane: Plane) -> np.ndarray:
    normal = np.asarray(plane.normal, dtype=float)
    if normal.shape != (3,) or not np.isfinite(normal).all():
        raise ValueError("Plane normal must be a finite 3-vector")
    length = float(np.linalg.norm(normal))
    if length == 0:
        raise ValueError("Plane normal must be nonzero")
    return normal / length


def plane_angle(first: Plane, second: Plane) -> float:
    """Unoriented acute angle between planes in degrees, in [0, 90]."""
    cosine = abs(float(np.dot(_plane_normal(first), _plane_normal(second))))
    return float(np.degrees(np.arccos(np.clip(cosine, 0, 1))))


def point_plane_distance(point: np.ndarray, plane: Plane) -> float:
    """Unsigned orthogonal distance; normal normalization is explicit."""
    value = np.asarray(point, dtype=float)
    centre = np.asarray(plane.centre, dtype=float)
    if value.shape != (3,) or centre.shape != (3,) or not np.isfinite([value, centre]).all():
        raise ValueError("Point and plane centre must be finite 3-vectors")
    return abs(float(np.dot(value - centre, _plane_normal(plane))))


def selection_rmsd(first: np.ndarray, second: np.ndarray, *, superpose: bool = False) -> float:
    """RMSD of ordered correspondences, optionally after a proper Kabsch fit.

    The caller supplies atom pairing; no element-based reordering is inferred.
    Proper rotations preserve chirality. See :mod:`vibeview.align` for reference.
    """
    a, b = _points(first), _points(second)
    if a.shape != b.shape:
        raise ValueError("Selections must have the same number of atoms")
    return kabsch_fit(a, b)[1] if superpose else rmsd(a, b)
