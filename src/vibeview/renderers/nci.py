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

from collections.abc import Sequence
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import pyvista as pv
    from matplotlib.colors import LinearSegmentedColormap

    from vibeview.qvf import GridData

# NCIPLOT (Contreras-García 2011) default plotting conventions:
RDG_ISOVALUE = 0.3       # s(r) isosurface value defining the NCI surface
COLOR_CLIM = 0.05        # sign(λ₂)ρ color range, atomic units (±0.05 a.u.)


def nci_colormap() -> LinearSegmentedColormap:
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

    lam2 = middle_eigenvalue(hxx, hxy, hxz, hyy, hyz, hzz)
    field: np.ndarray = np.sign(lam2) * rho
    return field


def middle_eigenvalue(
    hxx: np.ndarray,
    hxy: np.ndarray,
    hxz: np.ndarray,
    hyy: np.ndarray,
    hyz: np.ndarray,
    hzz: np.ndarray,
) -> np.ndarray:
    """λ₂ of a symmetric 3×3 matrix at every grid point, in closed form.

    Smith, "Eigenvalues of a symmetric 3 × 3 matrix", Commun. ACM 4, 168
    (1961): shift by the mean of the trace, scale by the deviatoric norm,
    and read the three eigenvalues off the trigonometric solution of the
    characteristic cubic. Elementwise over the six unique components, so
    a 60³ grid needs six arrays rather than the ``(n, 3, 3)`` block plus
    LAPACK workspace that ``eigvalsh`` would allocate.
    """
    hxx, hxy, hxz, hyy, hyz, hzz = (np.asarray(h, dtype=np.float64) for h in
                                    (hxx, hxy, hxz, hyy, hyz, hzz))
    p1 = hxy * hxy + hxz * hxz + hyz * hyz
    q = (hxx + hyy + hzz) / 3.0
    p2 = (hxx - q) ** 2 + (hyy - q) ** 2 + (hzz - q) ** 2 + 2.0 * p1
    p = np.sqrt(np.maximum(p2 / 6.0, 0.0))
    safe_p = np.where(p > 0.0, p, 1.0)
    bxx, byy, bzz = (hxx - q) / safe_p, (hyy - q) / safe_p, (hzz - q) / safe_p
    bxy, bxz, byz = hxy / safe_p, hxz / safe_p, hyz / safe_p
    det_b = (
        bxx * (byy * bzz - byz * byz)
        - bxy * (bxy * bzz - byz * bxz)
        + bxz * (bxy * byz - byy * bxz)
    )
    r = np.clip(det_b / 2.0, -1.0, 1.0)
    phi = np.arccos(r) / 3.0
    eig1 = q + 2.0 * p * np.cos(phi)
    eig3 = q + 2.0 * p * np.cos(phi + 2.0 * np.pi / 3.0)
    # The trace fixes the third; a diagonal-only matrix (p == 0) is q.
    lam2: np.ndarray = np.where(p > 0.0, 3.0 * q - eig1 - eig3, q)
    return lam2


def build_nci_mesh(
    rdg_data: np.ndarray,
    color_data: np.ndarray | None,
    grid: GridData,
    isovalue: float = RDG_ISOVALUE,
    *,
    replication: tuple[int, int, int] = (1, 1, 1),
    lattice_vectors: np.ndarray | None = None,
    pbc: Sequence[bool] | None = None,
) -> pv.PolyData | None:
    """Contour the RDG field at ``isovalue``; carry sign(λ₂)ρ onto the surface.

    Builds a PyVista grid with the RDG field as ``rdg`` and (when available)
    the coloring field as ``color``; ``contour(scalars="rdg")`` interpolates
    every point array — including ``color`` — onto the new isosurface vertices,
    which is exactly the NCI plot. Returns the PolyData (with a ``color`` point
    array when ``color_data`` is given), or None if the contour is empty.

    The grid geometry is converted bohr→Å to match the atoms, mirroring
    :func:`vibeview.renderers.volume.build_isosurface_mesh`.
    """
    from vibeview.renderers.volume import build_isosurface_mesh

    contour = build_isosurface_mesh(
        rdg_data,
        grid,
        isovalue,
        replication=replication,
        lattice_vectors=lattice_vectors,
        pbc=pbc,
        extra_point_arrays={"color": color_data} if color_data is not None else None,
    )
    if contour is None or contour.n_points == 0:
        return None
    # Preserve the public NCI mesh's scalar names for existing consumers.
    contour.rename_array("values", "rdg")
    return contour
