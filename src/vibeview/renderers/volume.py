"""Volume renderer — isosurface rendering for density and orbitals.

Lazy extraction: the grid descriptor is read eagerly, but the .dat
blob is read from the zip only when the user activates the section
in the UI. Supports adjustable isovalue, colormap, opacity, and
component selection for orbital sections.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

import numpy as np
import pyvista as pv

from vibeview.renderers import BaseRenderer
from vibeview.units import BOHR_TO_ANGSTROM as _BOHR_TO_ANGSTROM
from vibeview.viewer_defaults import VolumeHints

if TYPE_CHECKING:
    from vibeview.qvf import GridData, QVFReader, Section

# QVF v1 contract (design § 1.3a): grid origin + voxel_vectors are in
# bohr; structure atoms + lattice_vectors are in Å. PyVista renders
# everything in one world-coordinate system, so we convert grid
# geometry to Å at the renderer boundary using the shared CODATA 2018 factor.


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
    grid: GridData,
    pbc: Sequence[bool],
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
    shifts = _grid_center_shifts(
        data,
        grid,
        pbc,
        lattice_vectors,
        voxel_scale=_BOHR_TO_ANGSTROM,
        min_resultant=min_resultant,
    )
    return (np.roll(data, shifts, axis=(0, 1, 2)), True) if any(shifts) else (data, False)


def _grid_center_shifts(
    data: np.ndarray,
    grid: GridData,
    pbc: Sequence[bool],
    lattice_vectors: np.ndarray,
    *,
    voxel_scale: float,
    min_resultant: float = 0.05,
) -> tuple[int, int, int]:
    """Index shifts chosen from the primary field, with cell lengths in Å."""
    vox_ang = grid.voxel_vectors * voxel_scale
    absd = np.abs(data)
    shifts = [0, 0, 0]
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
            absd = np.roll(absd, shift, axis=ax)
            shifts[ax] = shift
    return shifts[0], shifts[1], shifts[2]


def build_isosurface_mesh(
    data: np.ndarray,
    grid: GridData,
    isovalue: float,
    *,
    grid_units: str = "bohr",
    replication: tuple[int, int, int] = (1, 1, 1),
    lattice_vectors: np.ndarray | None = None,
    wrap_to_center: bool = False,
    pbc: Sequence[bool] | None = None,
    extra_point_arrays: Mapping[str, np.ndarray] | None = None,
) -> pv.PolyData | None:
    """Marching-cubes isosurface for a scalar field on a grid.

    Shared by :meth:`VolumeRenderer.make_mesh` and the per-frame
    reaction-path volume animation (W1). Stored QVF volumes carry origin +
    voxel_vectors in **bohr**, while fields sampled on demand by
    :class:`~vibeview.renderers.wavefunction.WavefunctionRenderer` already
    use angstroms. ``grid_units`` makes that boundary explicit so both land
    in the same Å world frame as the atoms. Returns the contour PolyData, or
    None if ``data`` is None. ``extra_point_arrays`` carries scalar fields on
    the same grid through contour interpolation, orientation and replication.
    Each must match ``grid.shape``; ``values`` is reserved for the primary
    field. Input arrays and the mapping are not modified.
    """
    if data is None:
        return None

    if grid_units not in {"bohr", "angstrom"}:
        raise ValueError("grid_units must be 'bohr' or 'angstrom'")
    # Stored volumes need bohr -> Å; evaluated wavefunction fields have
    # already crossed that boundary. Lattice and camera coordinates are Å.
    scale = _BOHR_TO_ANGSTROM if grid_units == "bohr" else 1.0
    point_arrays = {"values": data}
    for name, array in (extra_point_arrays or {}).items():
        if name == "values":
            raise ValueError("extra_point_arrays cannot replace the primary 'values' field")
        if array.shape != tuple(grid.shape):
            raise ValueError(f"extra point array {name!r} must have shape {grid.shape}")
        point_arrays[name] = array

    # Choose one periodic permutation from the primary field, then apply it
    # to every array so sampled colours still refer to the same grid points.
    if wrap_to_center and pbc is not None and lattice_vectors is not None:
        shifts = _grid_center_shifts(data, grid, pbc, lattice_vectors, voxel_scale=scale)
        if any(shifts):
            point_arrays = {
                name: np.roll(array, shifts, axis=(0, 1, 2)) for name, array in point_arrays.items()
            }

    nx, ny, nz = grid.shape
    voxel_vecs = grid.voxel_vectors * scale
    origin = grid.origin * scale

    is_orthogonal = True
    for i in range(3):
        for j in range(3):
            if i != j and abs(voxel_vecs[i, j]) > 1e-10:
                is_orthogonal = False
                break

    mesh: pv.ImageData | pv.StructuredGrid
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
        if flip_axes:
            point_arrays = {
                name: np.flip(array, axis=tuple(flip_axes)) for name, array in point_arrays.items()
            }
        # PyVista renamed UniformGrid → ImageData and dims →
        # dimensions in 0.40. The project pins pyvista>=0.44, so we
        # use the current names. Dimensions match the data shape
        # (point-centred values); pv.contour() reads point_data.
        mesh = pv.ImageData(
            dimensions=dims,
            spacing=tuple(spacing),
            origin=tuple(origin_xyz),
        )
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
        mesh = pv.StructuredGrid(pts_x, pts_y, pts_z)

    for name, array in point_arrays.items():
        mesh.point_data[name] = array.ravel(order="F")
    contour: pv.PolyData = mesh.contour(isosurfaces=[isovalue], scalars="values")

    # ── Periodic replication ────────────────────────────────────
    # Slab and polymer lattices can carry synthesized bookkeeping vectors on
    # non-periodic axes. Replicating a surface along one of those directions
    # detaches it from the atoms into vacuum, while structure rendering stays
    # in the physical dimensions. Apply the same PBC mask when it is known.
    counts = [max(1, int(r)) for r in replication]
    if pbc is not None:
        counts = [count if bool(flag) else 1 for count, flag in zip(counts, pbc, strict=True)]
    effective_replication = (counts[0], counts[1], counts[2])
    if lattice_vectors is not None and any(r > 1 for r in effective_replication):
        contour = _replicate_mesh(contour, lattice_vectors, effective_replication)

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
        pbc: Sequence[bool] | None = None,
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
    merged: pv.PolyData = replicas[0].merge(replicas[1:])
    return merged


class PeriodicGridAlignmentError(ValueError):
    """The sampled field cannot tile exactly on its existing voxel grid."""


def replicate_mesh_for_periodic(
    mesh: pv.PolyData | None,
    lattice_vectors: np.ndarray,
    replication: tuple[int, int, int],
) -> pv.PolyData | None:
    """Copy a contoured surface by exact lattice translations, centred on zero.

    Counts are additional cells on each side, matching the periodic grid
    helper. Coordinates and lattice vectors are both in angstroms. This
    preserves the original surface but does not join its boundary seams.
    """
    if mesh is None or mesh.n_points == 0:
        return mesh
    counts = tuple(max(0, count) for count in replication)
    if not any(counts):
        return mesh
    total = (2 * counts[0] + 1, 2 * counts[1] + 1, 2 * counts[2] + 1)
    copies = _replicate_mesh(mesh, lattice_vectors, total)
    # _replicate_mesh may share its input in the no-op case; never alter it.
    centred = copies.copy()
    centred.points -= np.asarray(counts) @ lattice_vectors
    return centred


def replicate_volume_for_periodic(
    data: np.ndarray,
    grid: GridData,
    lattice_vectors: np.ndarray,
    replication: tuple[int, int, int] = (1, 1, 1),
) -> tuple[np.ndarray, GridData]:
    """Tile exact periodic samples without changing their world coordinates.

    Grid coordinates are in bohr and lattice vectors in angstroms. Each
    replication count adds that many cells on *both* sides of the original.
    Active lattice vectors must translate by an integral number of samples
    along their corresponding grid axis. Grids may be skewed or reversed.
    Overlapping copies must agree exactly; gaps cannot be invented.

    Raises :class:`PeriodicGridAlignmentError` if exact tiling is impossible.
    Callers can contour the original field and use
    :func:`replicate_mesh_for_periodic` instead, retaining physical lattice
    translations without claiming continuous field samples across seams.
    Neither the input scalar array nor the grid descriptor is modified.
    """
    counts = np.maximum(replication, 0)
    if not np.any(counts):
        return data, grid

    lat_bohr = np.asarray(lattice_vectors, dtype=float) / _BOHR_TO_ANGSTROM
    voxel = np.asarray(grid.voxel_vectors, dtype=float)
    shape = np.asarray(grid.shape, dtype=int)
    origin = np.asarray(grid.origin, dtype=float)
    try:
        translations = lat_bohr @ np.linalg.inv(voxel)
    except np.linalg.LinAlgError as exc:
        raise PeriodicGridAlignmentError("singular voxel grid") from exc
    steps = np.zeros(3, dtype=int)
    for axis in range(3):
        if counts[axis] == 0:
            continue
        row = translations[axis]
        if not np.all(np.isfinite(row)):
            raise PeriodicGridAlignmentError("non-finite lattice translation")
        rounded = round(float(row[axis]))
        expected = np.zeros(3)
        expected[axis] = rounded
        # The tolerance only absorbs coordinate conversion roundoff, in
        # sample-index units. It must not round fractional physical periods.
        if not np.allclose(row, expected, atol=1e-8, rtol=0):
            raise PeriodicGridAlignmentError("lattice is not aligned to integral voxel steps")
        step = abs(rounded)
        steps[axis] = step
        if step == 0:
            counts[axis] = 0
        elif step > shape[axis]:
            raise PeriodicGridAlignmentError("lattice translation leaves unsampled gaps")
        elif step < shape[axis]:
            lower = [slice(None)] * 3
            upper = [slice(None)] * 3
            lower[axis] = slice(None, -step)
            upper[axis] = slice(step, None)
            if not np.array_equal(data[tuple(lower)], data[tuple(upper)]):
                raise PeriodicGridAlignmentError("overlapping periodic samples disagree")

    if not np.any(counts):
        return data, grid
    offsets = counts * steps
    new_shape = shape + 2 * offsets
    # Validated steps cover the complete output; consistent overlaps can be
    # copied in any order without overwriting an original sample's value.
    new_data = np.empty(tuple(new_shape), dtype=data.dtype)
    for ix in range(2 * counts[0] + 1):
        for iy in range(2 * counts[1] + 1):
            for iz in range(2 * counts[2] + 1):
                start = np.array([ix, iy, iz]) * steps
                region = tuple(slice(int(a), int(a + n)) for a, n in zip(start, shape, strict=True))
                new_data[region] = data

    from vibeview.qvf import GridData as _GridData

    new_grid = _GridData(
        origin=origin - offsets @ voxel,
        voxel_vectors=voxel.copy(),
        shape=(int(new_shape[0]), int(new_shape[1]), int(new_shape[2])),
    )
    return new_data, new_grid
