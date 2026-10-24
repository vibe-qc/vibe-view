"""Memory-mapped scalar MRC/CCP4 grids with explicit physical geometry.

Header conventions: https://www.ccpem.ac.uk/mrc-format/mrc2014/ .
No handedness guess or crystallographic symmetry expansion is performed.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from vibeview.crystal_builder import cell_from_abc

if TYPE_CHECKING:
    import pyvista as pv

_MODES = {0: 'i1', 1: 'i2', 2: 'f4', 6: 'u2', 12: 'f2'}


@dataclass(frozen=True)
class ExperimentalMap:
    """Data axes are columns, rows, sections; geometry rows match those axes."""

    data: np.ndarray
    origin: np.ndarray
    voxel_vectors: np.ndarray
    mean: float
    rms: float
    minimum: float
    maximum: float
    axis_order: tuple[int, ...]
    origin_convention: str

    def sigma_level(self, sigma: float) -> float:
        """Convert using original header statistics, never subsampled statistics."""
        if not np.isfinite(sigma) or not np.isfinite([self.mean, self.rms]).all() or self.rms <= 0:
            raise ValueError('Sigma levels require finite header mean and positive RMS')
        return self.mean + float(sigma) * self.rms

    def sampled(self, max_voxels: int = 2_000_000) -> tuple[np.ndarray, np.ndarray, int]:
        """Bound interactive allocation; retain origin and original header statistics."""
        if max_voxels < 1:
            raise ValueError('max_voxels must be positive')
        stride = max(1, int(np.ceil((self.data.size / max_voxels) ** (1 / 3))))
        while np.prod([(n + stride - 1) // stride for n in self.data.shape]) > max_voxels:
            stride += 1
        data = np.asarray(self.data[::stride, ::stride, ::stride], dtype=np.float32)
        if not np.isfinite(data).all():
            raise ValueError('Map contains nonfinite sampled values')
        return data, self.voxel_vectors * stride, stride

    def histogram(self, bins: int = 64) -> tuple[np.ndarray, np.ndarray]:
        """Histogram of the bounded interaction sample (not full-map statistics)."""
        data, _, _ = self.sampled()
        return np.histogram(data, bins=bins)


def open_map(path: str | Path) -> ExperimentalMap:
    """Read the header, validate storage, and map scalar values without copying.

    Nonzero ORIGIN is an explicit Cartesian angstrom origin. Otherwise use
    NCSTART/NRSTART/NSSTART through the permuted cell vectors. Both header
    conventions are preserved in ``origin_convention`` for inspection.
    Complex Fourier maps, packed modes and image/volume stacks are refused.
    """
    path = Path(path)
    with path.open('rb') as stream:
        header = stream.read(1024)
    if len(header) != 1024 or header[208:212] != b'MAP ':
        raise ValueError('Expected a 1024-byte MRC/CCP4 header with MAP signature')
    stamp = header[212:214]
    if stamp in (b'DA', b'DD'):
        endian = '<'
    elif stamp == b'\x11\x11':
        endian = '>'
    else:
        raise ValueError('Unknown MRC machine stamp; byte order is ambiguous')
    ints = np.frombuffer(header, dtype=endian + 'i4')
    floats = np.frombuffer(header, dtype=endian + 'f4')
    shape = tuple(int(n) for n in ints[:3])
    mode = int(ints[3])
    if mode not in _MODES:
        raise ValueError(f'Unsupported MRC mode {mode}; only real scalar maps are supported')
    if any(n <= 0 for n in shape):
        raise ValueError('MRC dimensions must be positive')
    sampling = ints[7:10].astype(float)
    if np.any(sampling <= 0):
        raise ValueError('MRC cell sampling must be positive')
    order = tuple(int(n) for n in ints[16:19])
    if sorted(order) != [1, 2, 3]:
        raise ValueError('MRC MAPC/MAPR/MAPS must be a permutation of 1,2,3')
    spacegroup = int(ints[22])
    if spacegroup >= 401 or (spacegroup == 0 and shape[2] > 1 and sampling[2] == 1):
        raise ValueError('MRC image/volume stacks require explicit per-volume selection')
    params = floats[10:16].astype(float)
    if not np.isfinite(params).all():
        raise ValueError('MRC cell parameters must be finite')
    cell = cell_from_abc(*params)
    vectors = (cell / sampling[:, None])[np.array(order) - 1]
    origin = floats[49:52].astype(float)
    if not np.isfinite(origin).all():
        raise ValueError('MRC origin must be finite')
    convention = 'ORIGIN (Cartesian angstrom)'
    if not np.any(origin):
        origin = ints[4:7] @ vectors
        convention = 'NCSTART/NRSTART/NSSTART (permuted grid indices)'
    extended = int(ints[23])
    if extended < 0:
        raise ValueError('MRC extended-header length must be nonnegative')
    dtype = np.dtype(endian + _MODES[mode])
    expected = 1024 + extended + int(np.prod(shape, dtype=object)) * dtype.itemsize
    if path.stat().st_size < expected:
        raise ValueError('Truncated MRC density data')
    storage = np.memmap(path, dtype=dtype, mode='r', offset=1024 + extended, shape=shape[::-1])
    return ExperimentalMap(storage.transpose(2, 1, 0), origin, vectors,
                           float(floats[21]), float(floats[54]), float(floats[19]),
                           float(floats[20]), order, convention)


def map_surface(
    density: ExperimentalMap,
    level: float,
    *,
    sigma: bool = True,
    max_voxels: int = 2_000_000,
    selection: np.ndarray | None = None,
    radius: float = 3.0,
) -> pv.PolyData:
    """Contour a bounded map and optionally clip it to a selection's zone.

    Selection positions and radius are Cartesian angstrom. The zone is the
    union of radius spheres around selected atoms; no periodic image or rigid
    fitting is inferred. Recompute after selection changes. Clipped contour
    edges are left open, not capped with invented density surfaces.
    """
    import pyvista as pv
    from scipy.spatial import cKDTree

    from vibeview.qvf import GridData
    from vibeview.renderers.volume import build_isosurface_mesh

    absolute = density.sigma_level(level) if sigma else float(level)
    if not np.isfinite(absolute):
        raise ValueError('Map level must be finite')
    data, vectors, _ = density.sampled(max_voxels)
    grid = GridData(density.origin, vectors, tuple(data.shape), units='angstrom')
    mesh = build_isosurface_mesh(data, grid, absolute)
    if mesh is None:
        return pv.PolyData()
    if selection is not None:
        positions = np.asarray(selection, dtype=float)
        if positions.size == 0:
            return pv.PolyData()
        if positions.ndim != 2 or positions.shape[1] != 3 or not np.isfinite(positions).all():
            raise ValueError('Zone selection must contain finite Cartesian positions')
        if not np.isfinite(radius) or radius <= 0:
            raise ValueError('Zone radius must be positive and finite')
        if mesh.n_points:
            mesh['zone_distance'] = cKDTree(positions).query(mesh.points)[0]
            mesh = mesh.clip_scalar(scalars='zone_distance', value=radius, invert=True)
    return mesh


def map_to_qvf(path: str | Path, *, max_voxels: int = 2_000_000) -> io.BytesIO:
    """Portable interaction snapshot; full source stays unchanged on disk.

    Subsampling is explicit provenance, not a full-resolution archive claim.
    Pass a larger budget to retain more detail, or the source voxel count for
    full resolution. Only the selected grid is materialized in memory.
    """
    from vibeview.qvf_writer import QVFArchiveBuilder
    from vibeview.units import BOHR_TO_ANGSTROM

    density = open_map(path)
    data, vectors, stride = density.sampled(max_voxels)
    builder = QVFArchiveBuilder(calculation="MRC/CCP4 interaction map")
    builder.add_json("map", "volume.generic", "grid", {
        "origin": (density.origin / BOHR_TO_ANGSTROM).tolist(),
        "voxel_vectors": (vectors / BOHR_TO_ANGSTROM).tolist(),
        "shape": list(data.shape),
    })
    builder.add_binary("map", "volume.generic", "data", data, "float32")
    statistics = {k: float(v) if np.isfinite(v) else None for k, v in {
        "mean": density.mean, "rms": density.rms,
        "minimum": density.minimum, "maximum": density.maximum,
    }.items()}
    builder.set_provenance({"experimental_map": {
        "source_format": "MRC/CCP4", "source_shape": list(density.data.shape),
        "axis_order": list(density.axis_order), "origin_convention": density.origin_convention,
        "stride": stride, "full_resolution": stride == 1,
        "header_statistics": statistics,
        "sampling": "stride subsampling; header statistics retain original resolution",
    }})
    return builder.to_bytesio()
