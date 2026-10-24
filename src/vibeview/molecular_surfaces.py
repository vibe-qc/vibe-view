"""Atom-derived surfaces, independent implementation of distance-transform SES.

Xu & Zhang (2009), PLoS ONE 4:e8140, doi:10.1371/journal.pone.0008140.
Only the published construction is used; no EDTSurf source is incorporated.
VdW/SAS are sphere unions. SES erodes the expanded union by the probe radius,
using Euclidean distances to solvent-centre grid points. Finite-grid estimates
must be converged in spacing; these are not analytical Connolly surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from vibeview.elements import vdw_radius
from vibeview.molecular_selection import positions, select
from vibeview.qvf import StructureData


@dataclass
class Surface:
    mesh: Any
    atom_indices: np.ndarray
    kind: str
    spacing: float
    probe: float

    @property
    def area(self) -> float:
        return float(self.mesh.area)

    @property
    def volume(self) -> float:
        if self.mesh.n_open_edges:
            raise ValueError("Surface is not closed; enclosed volume is undefined")
        return abs(float(self.mesh.volume))


def build_surface(
    structure: StructureData,
    *,
    kind: str = "ses",
    selection: str = "all",
    probe: float = 1.4,
    spacing: float = 0.25,
    max_voxels: int = 16_000_000,
) -> Surface:
    """Build a finite selected molecule; do not infer periodic surface topology."""
    import pyvista as pv

    if kind not in {"vdw", "sas", "ses"}:
        raise ValueError("Surface kind must be vdw, sas or ses")
    if not np.isfinite([probe, spacing]).all() or probe < 0 or spacing <= 0:
        raise ValueError("Probe must be non-negative and spacing positive, both finite")
    selected = np.array(sorted(select(structure, selection)), dtype=int)
    if not len(selected):
        raise ValueError("Surface selection is empty")
    xyz = positions(structure)[selected]
    radii = np.array([vdw_radius(structure.atoms[i].atomic_number) for i in selected])
    expanded = radii + (probe if kind != "vdw" else 0)
    low = np.min(xyz - expanded[:, None], axis=0) - 3 * spacing
    high = np.max(xyz + expanded[:, None], axis=0) + 3 * spacing
    dimensions = np.ceil((high - low) / spacing) + 1
    if not np.isfinite(dimensions).all() or np.any(dimensions > max_voxels):
        raise ValueError("Surface grid exceeds memory limit; increase spacing")
    shape = dimensions.astype(int)
    if int(np.prod(shape, dtype=object)) > max_voxels:
        raise ValueError("Surface grid exceeds memory limit; use a coarser spacing or selection")
    field = np.full(tuple(shape), np.inf, dtype=np.float32)
    # Local atom boxes avoid a full atom-by-grid distance matrix.
    for center, radius in zip(xyz, expanded, strict=True):
        lo = np.maximum(0, np.floor((center - radius - spacing - low) / spacing).astype(int))
        hi = np.minimum(shape, np.ceil((center + radius + spacing - low) / spacing).astype(int) + 1)
        axes = [low[j] + np.arange(lo[j], hi[j]) * spacing - center[j] for j in range(3)]
        distance = (
            np.sqrt(
                axes[0][:, None, None] ** 2
                + axes[1][None, :, None] ** 2
                + axes[2][None, None, :] ** 2
            )
            - radius
        )
        slices = tuple(slice(int(lo[j]), int(hi[j])) for j in range(3))
        field[slices] = np.minimum(field[slices], distance)
    field[~np.isfinite(field)] = spacing * 2
    level = 0.0
    if kind == "ses" and probe > 0:
        try:
            from scipy.ndimage import distance_transform_edt
        except ImportError as exc:
            from vibeview.install_hints import install_hint

            raise ImportError("SES requires scipy; " + install_hint("surfaces")) from exc
        # Locate the nearest sampled solvent centre, then subtract its analytic
        # union signed distance to interpolate the SAS boundary within the voxel.
        # This avoids treating voxel centres as the actual solvent boundary.
        distance, nearest = distance_transform_edt(
            field <= 0, sampling=spacing, return_indices=True)
        field = distance - field[tuple(nearest)]
        level = probe
    grid = pv.ImageData(dimensions=tuple(shape), spacing=(spacing,) * 3, origin=tuple(low))
    grid.point_data["distance"] = field.ravel(order="F")
    mesh = grid.contour([level], scalars="distance").triangulate()
    if not mesh.n_points:
        raise ValueError("Surface is empty at this grid spacing")
    # Nearest sphere surface, not nearest centre, determines property attribution.
    owner = np.empty(mesh.n_points, dtype=int)
    chunk = max(1, min(4096, 2_000_000 // len(selected)))
    for start in range(0, mesh.n_points, chunk):
        p = np.asarray(mesh.points[start : start + chunk])
        distances = np.linalg.norm(p[:, None, :] - xyz[None, :, :], axis=2) - radii
        owner[start : start + chunk] = selected[np.argmin(np.abs(distances), axis=1)]
    return Surface(mesh, owner, kind, spacing, probe)


def mesh_style(style: str) -> str:
    """Shared VTK style for molecular and field isosurfaces."""
    if style not in {"solid", "mesh", "dots"}:
        raise ValueError("Surface style must be solid, mesh or dots")
    return {"solid": "surface", "mesh": "wireframe", "dots": "points"}[style]
