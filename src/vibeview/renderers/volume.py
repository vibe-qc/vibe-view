"""Volume renderer — isosurface rendering for density and orbitals.

Lazy extraction: the grid descriptor is read eagerly, but the .dat
blob is read from the zip only when the user activates the section
in the UI. Supports adjustable isovalue, colormap, opacity, and
component selection for orbital sections.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyvista as pv

from vibeview.renderers import BaseRenderer
from vibeview.viewer_defaults import VolumeHints

if TYPE_CHECKING:
    from vibeview.qvf import GridData, QVFReader, Section

# QVF v1 contract (design § 1.3a): grid origin + voxel_vectors are in
# bohr; structure atoms + lattice_vectors are in Å. PyVista renders
# everything in one world-coordinate system, so we convert grid
# geometry to Å at the renderer boundary. CODATA 2018; must match
# vibeqc.output.formats.qvf._BOHR_TO_ANGSTROM.
_BOHR_TO_ANGSTROM = 0.529177210903


def _circular_centroid_index(weight_1d: np.ndarray) -> tuple[float | None, float]:
    """Centroid of a periodic 1-D weight profile, as a (possibly
    fractional) grid index in ``[0, n)``, plus the mean resultant length
    ``R`` in ``[0, 1]``.

    A periodic axis has no "ends", so a plain weighted mean is wrong for a
    feature straddling index 0/n. Treat index ``k`` as an angle
    ``2πk/n`` and take the circular mean. ``R`` measures how peaked the
    profile is: ``R≈1`` for a sharp localized blob, ``R≈0`` for a
    delocalized (uniform) field where "centering" is meaningless.
    """
    n = len(weight_1d)
    total = float(weight_1d.sum())
    if total <= 0.0:
        return None, 0.0
    ang = 2.0 * np.pi * np.arange(n) / n
    cx = float((weight_1d * np.cos(ang)).sum()) / total
    cy = float((weight_1d * np.sin(ang)).sum()) / total
    resultant = float(np.hypot(cx, cy))
    mean_ang = np.arctan2(cy, cx) % (2.0 * np.pi)
    return (mean_ang / (2.0 * np.pi)) * n, resultant


def wrap_grid_to_center(
    data: np.ndarray,
    grid: "GridData",
    pbc,
    lattice_vectors: np.ndarray,
    *,
    min_resultant: float = 0.05,
) -> tuple[np.ndarray, bool]:
    """Roll a periodic (torus) grid so a localized feature sits at the
    cell centre, using minimum-image periodicity.

    For a cyclic-cluster / Born–von-Kármán grid, a Wannier function or
    crystalline orbital centred near a cell face is split across the
    opposite face and reads as broken. Rolling the grid (a periodic
    permutation of voxels, so the cell footprint is unchanged) brings the
    feature whole into the middle of the cell.

    Only rolls a periodic axis when the grid actually spans ~one cell
    along it (``n·|voxel| ≈ |lattice|``); a molecular / non-torus grid is
    left untouched, since rolling it would wrap padding into the field.
    Delocalized fields (low resultant) are also left alone. Returns
    ``(data, rolled_any)``.
    """
    if lattice_vectors is None:
        return data, False
    vox_ang = grid.voxel_vectors * _BOHR_TO_ANGSTROM
    out = data
    absd = np.abs(data)
    rolled = False
    for ax in range(3):
        if not bool(pbc[ax]):
            continue
        n = grid.shape[ax]
        len_vox = float(np.linalg.norm(vox_ang[ax]))
        len_cell = float(np.linalg.norm(lattice_vectors[ax]))
        if len_cell <= 0.0 or abs(n * len_vox - len_cell) / len_cell > 0.2:
            continue  # grid not torus-aligned on this axis — unsafe to wrap
        weight = absd.sum(axis=tuple(a for a in range(3) if a != ax))
        centroid, resultant = _circular_centroid_index(weight)
        if centroid is None or resultant < min_resultant:
            continue  # delocalized — nothing to centre
        shift = int(round(n / 2.0 - centroid))
        if shift % n != 0:
            out = np.roll(out, shift, axis=ax)
            absd = np.roll(absd, shift, axis=ax)
            rolled = True
    return out, rolled


def build_isosurface_mesh(
    data: np.ndarray,
    grid: GridData,
    isovalue: float,
    *,
    grid_units: str = "bohr",
    replication: tuple[int, int, int] = (1, 1, 1),
    lattice_vectors: np.ndarray | None = None,
    wrap_to_center: bool = False,
    pbc=None,
) -> pv.PolyData | None:
    """Marching-cubes isosurface for a scalar field on a grid.

    Shared by :meth:`VolumeRenderer.make_mesh` and the per-frame
    reaction-path volume animation (W1). Stored QVF volumes carry origin +
    voxel_vectors in **bohr**, while fields sampled on demand by
    :class:`~vibeview.renderers.wavefunction.WavefunctionRenderer` already
    use angstroms. ``grid_units`` makes that boundary explicit so both land
    in the same Å world frame as the atoms. Returns the contour PolyData, or
    None if ``data`` is None.
    """
    if data is None:
        return None

    # Minimum-image recentre (cyclic-cluster / periodic wrap): roll a
    # face-straddling feature whole into the cell before contouring.
    if wrap_to_center and pbc is not None and lattice_vectors is not None:
        data, _ = wrap_grid_to_center(data, grid, pbc, lattice_vectors)

    nx, ny, nz = grid.shape

    if grid_units not in {"bohr", "angstrom"}:
        raise ValueError("grid_units must be 'bohr' or 'angstrom'")
    # Stored volumes need bohr -> Å; evaluated wavefunction fields have
    # already crossed that boundary. Structure, bond, lattice and camera
    # coordinates are all Å. See the QVF design § 1.3a.
    scale = _BOHR_TO_ANGSTROM if grid_units == "bohr" else 1.0
    voxel_vecs = grid.voxel_vectors * scale
    origin = grid.origin * scale

    is_orthogonal = True
    for i in range(3):
        for j in range(3):
            if i != j and abs(voxel_vecs[i, j]) > 1e-10:
                is_orthogonal = False
                break

    if is_orthogonal:
        # pv.ImageData requires non-negative spacing. A grid whose voxel
        # vector points opposite its axis (negative diagonal — a flipped-axis
        # cube) is a valid QVF grid; flip the data along that axis and move the
        # origin to the low corner so the geometry is unchanged while the
        # spacing is positive (otherwise pv.ImageData raises ValueError).
        spacing = [float(voxel_vecs[i, i]) for i in range(3)]
        origin_xyz = [float(origin[i]) for i in range(3)]
        dims = (nx, ny, nz)
        flip_axes: list[int] = []
        for i in range(3):
            if spacing[i] < 0.0:
                origin_xyz[i] += (dims[i] - 1) * spacing[i]
                spacing[i] = -spacing[i]
                flip_axes.append(i)
        oriented = np.flip(data, axis=tuple(flip_axes)) if flip_axes else data
        # PyVista renamed UniformGrid → ImageData and dims →
        # dimensions in 0.40. The project pins pyvista>=0.44, so we
        # use the current names. Dimensions match the data shape
        # (point-centred values); pv.contour() reads point_data.
        mesh = pv.ImageData(
            dimensions=dims,
            spacing=tuple(spacing),
            origin=tuple(origin_xyz),
        )
        mesh.point_data["values"] = oriented.ravel(order="F")
        contour = mesh.contour(isosurfaces=[isovalue], scalars="values")
    else:
        ii, jj, kk = np.meshgrid(
            np.arange(nx, dtype=np.float64),
            np.arange(ny, dtype=np.float64),
            np.arange(nz, dtype=np.float64),
            indexing="ij",
        )
        pts_x = origin[0] + ii * voxel_vecs[0, 0] + jj * voxel_vecs[1, 0] + kk * voxel_vecs[2, 0]
        pts_y = origin[1] + ii * voxel_vecs[0, 1] + jj * voxel_vecs[1, 1] + kk * voxel_vecs[2, 1]
        pts_z = origin[2] + ii * voxel_vecs[0, 2] + jj * voxel_vecs[1, 2] + kk * voxel_vecs[2, 2]
        sgrid = pv.StructuredGrid(pts_x, pts_y, pts_z)
        sgrid.point_data["values"] = data.ravel(order="F")
        contour = sgrid.contour(isosurfaces=[isovalue], scalars="values")

    # ── Periodic replication ────────────────────────────────────
    # Slab and polymer lattices can carry synthesized bookkeeping vectors on
    # non-periodic axes. Replicating a surface along one of those directions
    # detaches it from the atoms into vacuum, while structure rendering stays
    # in the physical dimensions. Apply the same PBC mask when it is known.
    effective_replication = tuple(max(1, int(r)) for r in replication)
    if pbc is not None:
        effective_replication = tuple(
            count if bool(flag) else 1
            for count, flag in zip(effective_replication, pbc, strict=True)
        )
    if lattice_vectors is not None and any(r > 1 for r in effective_replication):
        contour = _replicate_mesh(
            contour, lattice_vectors, effective_replication
        )

    return contour


class VolumeRenderer(BaseRenderer):
    """Isosurface renderer for volume.density and volume.orbital."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._grid: GridData | None = None
        self._data: np.ndarray | None = None  # lazy
        self._loaded: bool = False

    def load_grid(self) -> GridData:
        """Eager load the grid descriptor (tiny JSON, called at file open)."""
        if self._grid is None:
            self._grid = self.reader.read_volume_grid(self.section_id)
        return self._grid

    def load_data(self) -> np.ndarray:
        """Lazy load the volumetric data blob.

        Called only when the user activates this section in the UI.
        The .dat blob can be tens of MB — this is why it's lazy.
        """
        if self._data is None:
            self._data = self.reader.read_volume_data(self.section_id)
            self._loaded = True
        return self._data

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def component(self) -> str | None:
        return self.section.component

    @property
    def label(self) -> str:
        """Human-readable label for the sidebar."""
        comp = f" ({self.component})" if self.component else ""
        return f"{self.section_id}{comp}"

    def make_mesh(
        self,
        hints: VolumeHints,
        replication: tuple[int, int, int] = (1, 1, 1),
        lattice_vectors: np.ndarray | None = None,
        wrap_to_center: bool = False,
        pbc=None,
    ) -> pv.PolyData | None:
        """Build an isosurface mesh from the volumetric data.

        Parameters
        ----------
        hints:
            Isovalue, colormap, and opacity hints.
        replication:
            (nx, ny, nz) replication counts along lattice vectors.
            Only used when ``lattice_vectors`` is not None.
        lattice_vectors:
            [3, 3] array of lattice vectors in angstroms. When
            provided and any replication count > 1, the isosurface
            is replicated along these vectors.
        wrap_to_center:
            When True (with ``pbc`` and ``lattice_vectors`` for a torus
            grid), roll the field so a face-straddling orbital / Wannier
            function is recentred whole in the cell (minimum-image).
        pbc:
            Per-axis periodicity flags; required for ``wrap_to_center``.

        Returns
        -------
        PyVista PolyData with the marching-cubes isosurface (possibly
        replicated), or None if the data hasn't been loaded yet.
        """
        data = self.load_data()
        grid = self.load_grid()

        if data is None:
            return None

        # Handle component selection for orbital sections
        if self.kind == "volume.orbital" and self.component:
            data = self._apply_component(data, self.component)

        return build_isosurface_mesh(
            data,
            grid,
            hints.isovalue,
            replication=replication,
            lattice_vectors=lattice_vectors,
            wrap_to_center=wrap_to_center,
            pbc=pbc,
        )

    @staticmethod
    def _apply_component(data: np.ndarray, component: str) -> np.ndarray:
        """Transform complex orbital data based on component selection."""
        if component == "real":
            return data.real if np.iscomplexobj(data) else data
        elif component == "imag":
            return data.imag if np.iscomplexobj(data) else np.zeros_like(data)
        elif component == "abs":
            return np.abs(data)
        elif component == "density":
            return np.abs(data) ** 2
        else:
            return data


def _replicate_mesh(
    mesh: pv.PolyData,
    lattice_vectors: np.ndarray,
    replication: tuple[int, int, int],
) -> pv.PolyData:
    """Replicate a PolyData mesh Nx×Ny×Nz times along lattice vectors.

    Each replica is translated by ``ix*a + iy*b + iz*c`` and merged
    into a single PolyData. The original mesh (replica (0,0,0)) is
    included in the output.
    """
    if mesh.n_points == 0:
        return mesh

    rx, ry, rz = replication
    a, b, c = lattice_vectors[0], lattice_vectors[1], lattice_vectors[2]

    replicas: list[pv.PolyData] = []
    for ix in range(rx):
        for iy in range(ry):
            for iz in range(rz):
                if ix == 0 and iy == 0 and iz == 0:
                    replicas.append(mesh)
                else:
                    offset = ix * a + iy * b + iz * c
                    translated = mesh.copy()
                    translated.points += offset
                    replicas.append(translated)

    if len(replicas) == 1:
        return replicas[0]
    return replicas[0].merge(replicas[1:])


def replicate_volume_for_periodic(
    data: np.ndarray,
    grid: "GridData",
    lattice_vectors: np.ndarray,
    replication: tuple[int, int, int] = (1, 1, 1),
) -> tuple[np.ndarray, "GridData"]:
    """Replicate a volume grid across unit cell translations.

    For periodic systems, orbital isosurfaces should repeat across
    adjacent unit cells. This function tiles the scalar field so that
    a single marching-cubes pass produces a seamless supercell isosurface
    — in contrast to :func:`_replicate_mesh`, which duplicates the
    already-contoured mesh and can leave visible seams at cell boundaries.

    Parameters
    ----------
    data : np.ndarray
        The scalar field data, shaped ``grid.shape``.
    grid : GridData
        The original grid descriptor (origin + voxel_vectors in **bohr**).
    lattice_vectors : np.ndarray
        [3, 3] lattice vectors in **angstroms**.
    replication : (nx, ny, nz)
        Number of additional cells in each direction, so the total
        supercell is (2*nx+1) × (2*ny+1) × (2*nz+1) cells.

    Returns
    -------
    tuple[np.ndarray, GridData]
        ``(replicated_data, new_grid)``. The new grid has its origin
        shifted to account for the added cells on the negative side.
    """
    nx, ny, nz = replication
    nx = max(0, nx)
    ny = max(0, ny)
    nz = max(0, nz)

    if nx == 0 and ny == 0 and nz == 0:
        return data, grid

    # Convert lattice vectors from Å to bohr — the grid works in bohr.
    lat_bohr = np.asarray(lattice_vectors, dtype=float) / _BOHR_TO_ANGSTROM
    voxel = np.asarray(grid.voxel_vectors, dtype=float)
    shape = np.asarray(grid.shape, dtype=int)
    origin = np.asarray(grid.origin, dtype=float)

    # Estimate how many grid points span one unit cell:
    #   cell_extent = lattice_vectors_bohr @ inv(voxel_vectors)
    # gives the offset in grid-index space for a single cell translation.
    # Take the diagonal for orthogonal / near-orthogonal grids; the
    # full matrix handles non-orthogonal cells when needed.
    cell_extent = np.abs(lat_bohr @ np.linalg.inv(voxel))
    cell_shape_diag = np.ceil(np.diag(cell_extent)).astype(int)
    cx, cy, cz = int(cell_shape_diag[0]), int(cell_shape_diag[1]), int(cell_shape_diag[2])

    # New grid dimensions: original plus extra cells on each side.
    new_shape = (
        shape[0] + 2 * nx * cx,
        shape[1] + 2 * ny * cy,
        shape[2] + 2 * nz * cz,
    )
    new_data = np.zeros(new_shape, dtype=data.dtype)

    # Place the original data in the centre of the supercell.
    x0 = nx * cx
    y0 = ny * cy
    z0 = nz * cz
    new_data[x0 : x0 + shape[0], y0 : y0 + shape[1], z0 : z0 + shape[2]] = data

    # Tile from centre outward in all directions.
    # For each cell index (cix, ciy, ciz), copy the entire original data
    # into the supercell at the translated position, clipping to bounds.
    for cix in range(-nx, nx + 1):
        for ciy in range(-ny, ny + 1):
            for ciz in range(-nz, nz + 1):
                dst_x = x0 + cix * cx
                dst_y = y0 + ciy * cy
                dst_z = z0 + ciz * cz

                # Compute overlap of the translated cell with the new grid.
                dx_start = max(0, dst_x)
                dx_end = min(new_shape[0], dst_x + shape[0])
                dy_start = max(0, dst_y)
                dy_end = min(new_shape[1], dst_y + shape[1])
                dz_start = max(0, dst_z)
                dz_end = min(new_shape[2], dst_z + shape[2])

                if dx_end <= dx_start or dy_end <= dy_start or dz_end <= dz_start:
                    continue

                # Corresponding source region in the original data.
                sx_start = dx_start - dst_x
                sx_end = dx_end - dst_x
                sy_start = dy_start - dst_y
                sy_end = dy_end - dst_y
                sz_start = dz_start - dst_z
                sz_end = dz_end - dst_z

                new_data[dx_start:dx_end, dy_start:dy_end, dz_start:dz_end] = data[
                    sx_start:sx_end, sy_start:sy_end, sz_start:sz_end
                ]

    # Shift origin to account for the cells added on the negative side.
    new_origin = origin - np.array([nx * cx, ny * cy, nz * cz]) * np.diag(voxel)

    from vibeview.qvf import GridData as _GridData

    new_grid = _GridData(
        origin=new_origin,
        voxel_vectors=voxel.copy(),
        shape=tuple(int(s) for s in new_shape),
    )

    return new_data, new_grid
