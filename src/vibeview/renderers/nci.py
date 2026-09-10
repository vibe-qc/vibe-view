"""NCI (non-covalent interactions) rendering for the `volume.rdg` kind.

The reduced density gradient s(r) = |∇ρ| / (2(3π²)^(1/3) ρ^(4/3)) develops
low-value isosurfaces in the regions *between* fragments; coloring those
surfaces by sign(λ₂)ρ — where λ₂ is the second (middle) eigenvalue of the
density Hessian — distinguishes the interaction types. This is the NCI analysis
of:

  Johnson, Keinan, Mori-Sánchez, Contreras-García, Cohen & Yang,
  "Revealing Noncovalent Interactions", J. Am. Chem. Soc. 132, 6498 (2010),
  doi:10.1021/ja100936w.

with the s-isosurface / sign(λ₂)ρ-coloring plotting convention of:

  Contreras-García, Johnson, Keinan, Chaudret, Piquemal, Beratan & Yang,
  "NCIPLOT: A Program for Plotting Noncovalent Interaction Regions",
  J. Chem. Theory Comput. 7, 625 (2011), doi:10.1021/ct100641a.

The producer ships only the RDG field s(r) (QVF spec §4.11); the coloring field
is computed here from a co-present `volume.density` section. Color convention
(NCIPLOT): blue = attractive (sign(λ₂)ρ < 0, e.g. H-bonds), green = van der
Waals (sign(λ₂)ρ ≈ 0), red = repulsive/steric (sign(λ₂)ρ > 0).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import pyvista as pv

    from vibeview.qvf import GridData

# NCIPLOT (Contreras-García 2011) default plotting conventions:
RDG_ISOVALUE = 0.3       # s(r) isosurface value defining the NCI surface
COLOR_CLIM = 0.05        # sign(λ₂)ρ color range, atomic units (±0.05 a.u.)


def nci_colormap():
    """Blue→green→red diverging colormap (the NCIPLOT sign(λ₂)ρ convention).

    Blue at the negative clim (attractive), green at zero (van der Waals), red
    at the positive clim (repulsive).
    """
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list(
        "nci_bgr", ["#0000ff", "#00cc00", "#ff0000"]
    )


def sign_lambda2_rho(rho: np.ndarray, voxel_vectors: np.ndarray) -> np.ndarray:
    """The NCI coloring field sign(λ₂)·ρ on the density grid.

    λ₂ is the second (middle) eigenvalue of the Hessian H_ij = ∂²ρ/∂x_i∂x_j,
    formed here by second-order finite differences (``np.gradient`` applied
    twice) using the per-axis grid spacings. Johnson et al. (2010), Fig. 1 +
    discussion: the sign of λ₂ separates bonded/attractive regions (λ₂ < 0)
    from steric/repulsive ones (λ₂ > 0); |sign(λ₂)ρ| ≈ 0 marks van der Waals
    contacts.

    The grid is assumed (approximately) Cartesian — NCI grids are by
    construction — so only the per-axis spacings ``|voxel_vectors[i]|`` enter.
    A uniform rescale of the spacing scales every Hessian entry equally and so
    leaves the eigenvalue *signs* (all the coloring uses) unchanged; the density
    units (e/bohr³) are preserved, matching the ±0.05 a.u. color range.
    """
    h = [float(np.linalg.norm(voxel_vectors[i])) or 1.0 for i in range(3)]
    gx, gy, gz = np.gradient(rho, h[0], h[1], h[2], edge_order=2)
    hxx = np.gradient(gx, h[0], axis=0, edge_order=2)
    hyy = np.gradient(gy, h[1], axis=1, edge_order=2)
    hzz = np.gradient(gz, h[2], axis=2, edge_order=2)
    hxy = np.gradient(gx, h[1], axis=1, edge_order=2)
    hxz = np.gradient(gx, h[2], axis=2, edge_order=2)
    hyz = np.gradient(gy, h[2], axis=2, edge_order=2)

    hess = np.empty(rho.shape + (3, 3), dtype=np.float64)
    hess[..., 0, 0] = hxx
    hess[..., 1, 1] = hyy
    hess[..., 2, 2] = hzz
    hess[..., 0, 1] = hess[..., 1, 0] = hxy
    hess[..., 0, 2] = hess[..., 2, 0] = hxz
    hess[..., 1, 2] = hess[..., 2, 1] = hyz

    # eigvalsh returns eigenvalues in ascending order: λ₁ ≤ λ₂ ≤ λ₃.
    lam2 = np.linalg.eigvalsh(hess)[..., 1]
    return np.sign(lam2) * rho


def build_nci_mesh(
    rdg_data: np.ndarray,
    color_data: np.ndarray | None,
    grid: GridData,
    isovalue: float = RDG_ISOVALUE,
) -> "pv.PolyData | None":
    """Contour the RDG field at ``isovalue``; carry sign(λ₂)ρ onto the surface.

    Builds a PyVista grid with the RDG field as ``rdg`` and (when available)
    the coloring field as ``color``; ``contour(scalars="rdg")`` interpolates
    every point array — including ``color`` — onto the new isosurface vertices,
    which is exactly the NCI plot. Returns the PolyData (with a ``color`` point
    array when ``color_data`` is given), or None if the contour is empty.

    The grid geometry is converted bohr→Å to match the atoms, mirroring
    :func:`vibeview.renderers.volume.build_isosurface_mesh`.
    """
    import pyvista as pv

    from vibeview.renderers.volume import _BOHR_TO_ANGSTROM

    nx, ny, nz = grid.shape
    voxel = grid.voxel_vectors * _BOHR_TO_ANGSTROM
    origin = grid.origin * _BOHR_TO_ANGSTROM
    dims = (nx, ny, nz)

    spacing = [float(voxel[i, i]) for i in range(3)]
    origin_xyz = [float(origin[i]) for i in range(3)]
    flip_axes: list[int] = []
    for i in range(3):
        if spacing[i] < 0.0:
            origin_xyz[i] += (dims[i] - 1) * spacing[i]
            spacing[i] = -spacing[i]
            flip_axes.append(i)

    def _oriented(arr: np.ndarray) -> np.ndarray:
        return np.flip(arr, axis=tuple(flip_axes)) if flip_axes else arr

    mesh = pv.ImageData(dimensions=dims, spacing=tuple(spacing), origin=tuple(origin_xyz))
    mesh.point_data["rdg"] = _oriented(rdg_data).ravel(order="F")
    if color_data is not None:
        mesh.point_data["color"] = _oriented(color_data).ravel(order="F")

    contour = mesh.contour(isosurfaces=[isovalue], scalars="rdg")
    if contour.n_points == 0:
        return None
    return contour
