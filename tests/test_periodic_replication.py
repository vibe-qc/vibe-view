"""Tests for periodic volume-grid replication.

The ``replicate_volume_for_periodic`` function tiles a scalar-field
volume across lattice translations so a single marching-cubes pass
produces a seamless supercell isosurface — complementary to the
existing mesh-level ``_replicate_mesh`` in ``volume.py``.
"""

from __future__ import annotations

import numpy as np


class TestPeriodicReplication:
    """Unit tests for replicate_volume_for_periodic."""

    def test_import(self) -> None:
        from vibeview.renderers.volume import replicate_volume_for_periodic

        assert callable(replicate_volume_for_periodic)

    def test_replicate_noop(self) -> None:
        """Replication with (0, 0, 0) returns the original data + grid."""
        from vibeview.qvf import GridData
        from vibeview.renderers.volume import replicate_volume_for_periodic

        data = np.ones((10, 10, 10), dtype=np.float32)
        grid = GridData(
            origin=np.array([0.0, 0.0, 0.0]),
            voxel_vectors=np.diag(np.array([0.5, 0.5, 0.5], dtype=float)),
            shape=(10, 10, 10),
        )
        lat = np.array([[5.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0, 5.0]])
        new_data, new_grid = replicate_volume_for_periodic(data, grid, lat, replication=(0, 0, 0))
        assert new_grid.shape == (10, 10, 10)
        assert np.allclose(new_data, data)

    def test_replicate_1x1x1_increases_grid(self) -> None:
        """A single-cell replication (1,1,1) produces a 3x3x3 supercell."""
        from vibeview.qvf import GridData
        from vibeview.renderers.volume import replicate_volume_for_periodic

        # One cell contains exactly n point samples at 0.5 bohr spacing.
        n = 20
        data = np.ones((n, n, n), dtype=np.float32)
        grid = GridData(
            origin=np.array([0.0, 0.0, 0.0]),
            voxel_vectors=np.diag(np.array([0.5, 0.5, 0.5], dtype=float)),
            shape=(n, n, n),
        )
        lat = np.eye(3) * n * 0.5 * 0.529177210903
        new_data, new_grid = replicate_volume_for_periodic(data, grid, lat, replication=(1, 1, 1))
        # Each axis contains three complete cells.
        for s in new_grid.shape:
            assert s > n, f"Shape {new_grid.shape} should be larger than {n}"

    def test_replicate_preserves_center_data(self) -> None:
        """Original data appears at the centre of the replicated grid."""
        from vibeview.qvf import GridData
        from vibeview.renderers.volume import replicate_volume_for_periodic

        # Exact integer lattice periods preserve every centre sample.
        n = 8
        rng = np.random.RandomState(42)
        data = rng.rand(n, n, n).astype(np.float32)
        grid = GridData(
            origin=np.array([0.0, 0.0, 0.0]),
            voxel_vectors=np.diag(np.array([0.5, 0.5, 0.5], dtype=float)),
            shape=(n, n, n),
        )
        lat = np.eye(3) * n * 0.5 * 0.529177210903
        new_data, new_grid = replicate_volume_for_periodic(data, grid, lat, replication=(1, 1, 1))
        # The original data is placed at x0 = nx * cell_extent_x.
        # Extract the centre block by computing the offset from shape expansion.
        offsets = [(new_grid.shape[i] - n) // 2 for i in range(3)]
        center = new_data[
            offsets[0] : offsets[0] + n,
            offsets[1] : offsets[1] + n,
            offsets[2] : offsets[2] + n,
        ]
        assert np.allclose(center, data), "Centre region must match original data"

    def test_replicate_origin_shift(self) -> None:
        """Grid origin is shifted to account for negative-side cells."""
        from vibeview.qvf import GridData
        from vibeview.renderers.volume import replicate_volume_for_periodic

        n = 8
        data = np.ones((n, n, n), dtype=np.float32)
        grid = GridData(
            origin=np.array([0.0, 0.0, 0.0]),
            voxel_vectors=np.diag(np.array([0.5, 0.5, 0.5], dtype=float)),
            shape=(n, n, n),
        )
        lat = np.eye(3) * n * 0.5 * 0.529177210903
        _, new_grid = replicate_volume_for_periodic(data, grid, lat, replication=(1, 1, 1))
        # Origin should shift negative by the cell extent in bohr.
        assert np.all(new_grid.origin < 0.0), (
            "Origin must shift to accommodate added cells on the negative side"
        )

    def test_replicate_tiles_periodically(self) -> None:
        """Data in replicated cells should match the original."""
        from vibeview.qvf import GridData
        from vibeview.renderers.volume import replicate_volume_for_periodic

        n = 10
        # Distinct periodic point samples: no duplicated endpoint.
        data = np.ones((n, n, n), dtype=np.float32)
        data[0, 0, 0] = 42.0
        grid = GridData(
            origin=np.array([0.0, 0.0, 0.0]),
            voxel_vectors=np.diag(np.array([0.5, 0.5, 0.5], dtype=float)),
            shape=(n, n, n),
        )
        lat = np.eye(3) * n * 0.5 * 0.529177210903
        new_data, new_grid = replicate_volume_for_periodic(data, grid, lat, replication=(1, 1, 1))
        # The marker should appear once per replicated cell: 3³ = 27 times.
        vals = new_data.flatten()
        assert np.any(vals == 42.0), "Marker value 42.0 should appear in replicated data"
        assert len(vals[vals == 42.0]) == 27, (
            "Marker should appear once per cell (3x3x3 = 27 times)"
        )

    def test_replicate_zero_lattice_noop(self) -> None:
        """A lattice of zeroes (degenerate cell) should not crash."""
        from vibeview.qvf import GridData
        from vibeview.renderers.volume import replicate_volume_for_periodic

        data = np.ones((10, 10, 10), dtype=np.float32)
        grid = GridData(
            origin=np.array([0.0, 0.0, 0.0]),
            voxel_vectors=np.diag(np.array([0.5, 0.5, 0.5], dtype=float)),
            shape=(10, 10, 10),
        )
        lat = np.zeros((3, 3), dtype=float)
        # Should not crash; the cell shape will be 0 or 1.
        new_data, _ = replicate_volume_for_periodic(data, grid, lat, replication=(1, 1, 1))
        # With zero-size cells, no extra grid points are added.
        assert new_data.shape == (10, 10, 10)


class TestCacheIntegration:
    """Tests that the mesh cache correctly separates periodic vs non-periodic."""

    def test_cache_key_differs_by_periodic_replication(self) -> None:
        from vibeview.viewer_defaults import ViewerState

        vs = ViewerState()
        k1 = vs.mesh_cache_key(0.05, periodic_replication=0)
        k2 = vs.mesh_cache_key(0.05, periodic_replication=1)
        assert k1 != k2, "Different periodic_replication values must produce different cache keys"

    def test_cache_hit_miss_by_periodic_replication(self) -> None:
        import pyvista as pv

        from vibeview.viewer_defaults import ViewerState

        vs = ViewerState()
        vs.put_cached_mesh("vol0", 0.05, mesh=pv.Sphere())
        # Same section, same iso, different periodic_replication → miss.
        assert vs.get_cached_mesh("vol0", 0.05, periodic_replication=1) is None
        # Same section, same iso, same periodic_replication → hit.
        assert vs.get_cached_mesh("vol0", 0.05, periodic_replication=0) is not None

    def test_put_periodic_cache(self) -> None:
        import pyvista as pv

        from vibeview.viewer_defaults import ViewerState

        vs = ViewerState()
        dummy = pv.Sphere()
        vs.put_cached_mesh("vol0", 0.05, periodic_replication=2, mesh=dummy)
        assert vs.get_cached_mesh("vol0", 0.05, periodic_replication=2) is not None
        # Standard (non-periodic) lookup should miss.
        assert vs.get_cached_mesh("vol0", 0.05, periodic_replication=0) is None
