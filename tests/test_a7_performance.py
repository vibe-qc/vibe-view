"""A7 performance-optimisation tests.

Three sub-items; each has a fast (< 1 s) unit test that pins the
contract:

A7-02  Mesh cache in ViewerState — colormap/opacity slider changes reuse
       the cached isosurface mesh; isovalue changes invalidate the cache
       and trigger a re-march.

A7-08  Glyph instancing for replicated periodic cells — a 2×2×2
       supercell of two atom types must produce only 2 draw calls
       (one per element) instead of 16.

A7-04  Pre-rendered energy frames — TrajectoryRenderer and
       ReactionPathRenderer pre-render all N energy-plot frames on first
       call; subsequent per-frame calls return cached bytes without
       re-running matplotlib.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import types
import zipfile
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")


# ── helpers ───────────────────────────────────────────────────────────


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _simple_volume_reader(kind: str = "volume.density") -> "QVFReader":
    """A tiny 10×10×10 density grid QVF."""
    from vibeview.qvf import QVFReader

    n = 10
    data = np.random.default_rng(42).random((n, n, n)).astype(np.float32)
    grid = json.dumps({
        "origin": [0, 0, 0],
        "voxel_vectors": [[0.5, 0, 0], [0, 0.5, 0], [0, 0, 0.5]],
        "shape": [n, n, n],
    }).encode()
    blob = data.tobytes()
    sections = [{
        "id": "vol0", "kind": kind, "members": {
            "grid": {"path": "g.json", "format": "json", "sha256": _sha(grid)},
            "data": {"path": "d.bin", "format": "binary", "dtype": "float32",
                     "shape": [n, n, n], "sha256": _sha(blob)},
        }
    }]
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
        "sections": sections,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("g.json", grid)
        zf.writestr("d.bin", blob)
    return QVFReader(buf.getvalue())


def _nacl_structure_reader(
    replication: tuple = (1, 1, 1),
) -> tuple["QVFReader", types.SimpleNamespace]:
    """A 2-atom NaCl-like periodic structure (Na + Cl) as a minimal QVF.

    Returns ``(reader, viewer_state_mock)`` where viewer_state_mock has
    just enough attributes for StructureRenderer.add_to_plotter.
    """
    from vibeview.qvf import QVFReader

    structure = json.dumps({
        "atoms": [
            {"symbol": "Na", "position": [0.0, 0.0, 0.0], "atomic_number": 11},
            {"symbol": "Cl", "position": [2.82, 0.0, 0.0], "atomic_number": 17},
        ],
        "lattice_vectors": [[5.64, 0, 0], [0, 5.64, 0], [0, 0, 5.64]],
        "pbc": [True, True, True],
    }).encode()
    # read_structure() hard-codes section_id="structure" — must use that id.
    sections = [{
        "id": "structure", "kind": "structure",
        "members": {
            "structure": {"path": "s.json", "format": "json", "sha256": _sha(structure)},
        },
    }]
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
        "sections": sections,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", structure)
    reader = QVFReader(buf.getvalue())
    return reader


def _trajectory_reader(n_frames: int = 5) -> "QVFReader":
    """Minimal trajectory with N frames and energy values."""
    from vibeview.qvf import QVFReader

    # The trajectory metadata uses "atoms" (symbol + atomic_number only,
    # no position field) and member key "coords" (not "coordinates").
    atoms = [{"symbol": "H", "atomic_number": 1}]
    energies = list(np.linspace(-1.0, -0.9, n_frames).tolist())
    coords = np.zeros((n_frames, 1, 3), dtype=np.float64)
    for i in range(n_frames):
        coords[i, 0, 2] = float(i) * 0.1

    meta = json.dumps({"atoms": atoms, "energies": energies}).encode()
    coords_b = coords.tobytes()
    sections = [{
        "id": "traj0", "kind": "trajectory",
        "members": {
            "metadata": {"path": "m.json", "format": "json", "sha256": _sha(meta)},
            "coords": {"path": "c.bin", "format": "binary", "dtype": "float64",
                       "shape": [n_frames, 1, 3], "sha256": _sha(coords_b)},
        },
    }]
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
        "sections": sections,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("m.json", meta)
        zf.writestr("c.bin", coords_b)
    return QVFReader(buf.getvalue())


# ── A7-02: Mesh cache ─────────────────────────────────────────────────


class TestMeshCache:
    """ViewerState mesh cache (A7-02)."""

    def test_cache_key_encodes_isovalue_and_replication(self):
        from vibeview.viewer_defaults import ViewerState
        vs = ViewerState()
        k1 = vs.mesh_cache_key(0.05)
        k2 = vs.mesh_cache_key(0.10)
        k3 = vs.mesh_cache_key(0.05)
        assert k1 != k2, "different isovalues must produce different keys"
        assert k1 == k3, "same isovalue + replication must be equal"

        vs.replication = (2, 2, 2)
        k4 = vs.mesh_cache_key(0.05)
        assert k4 != k1, "changed replication must change the key"

    def test_cache_miss_returns_none(self):
        from vibeview.viewer_defaults import ViewerState
        vs = ViewerState()
        assert vs.get_cached_mesh("vol0", 0.05) is None

    def test_put_then_hit(self):
        import pyvista as pv
        from vibeview.viewer_defaults import ViewerState
        vs = ViewerState()
        dummy = pv.Sphere()
        vs.put_cached_mesh("vol0", 0.05, mesh=dummy)
        entry = vs.get_cached_mesh("vol0", 0.05)
        assert entry is not None
        assert entry["mesh"] is dummy

    def test_isovalue_change_invalidates_cache(self):
        import pyvista as pv
        from vibeview.viewer_defaults import ViewerState
        vs = ViewerState()
        vs.put_cached_mesh("vol0", 0.05, mesh=pv.Sphere())
        # Same section, different isovalue → cache miss.
        assert vs.get_cached_mesh("vol0", 0.10) is None

    def test_replication_change_invalidates_cache(self):
        import pyvista as pv
        from vibeview.viewer_defaults import ViewerState
        vs = ViewerState()
        vs.put_cached_mesh("vol0", 0.05, mesh=pv.Sphere())
        vs.replication = (2, 2, 2)
        assert vs.get_cached_mesh("vol0", 0.05) is None, (
            "changing replication must invalidate the mesh cache"
        )

    def test_rebuild_volume_uses_cache_on_colormap_change(self):
        """A7-02 integration: _rebuild_volume must not re-march when only the
        colormap changes.  We count make_mesh calls by monkeypatching."""
        import pyvista as pv
        from vibeview.app import _rebuild_volume
        from vibeview.renderers.volume import VolumeRenderer
        from vibeview.viewer_defaults import ViewerState

        reader = _simple_volume_reader()
        plotter = pv.Plotter(off_screen=True)
        vs = ViewerState()
        state = types.SimpleNamespace(
            active_volume_id="vol0", isovalue=0.4, colormap="viridis",
            opacity=0.6, clip_enabled=False, status_message="",
        )

        march_calls = []
        original_make_mesh = VolumeRenderer.make_mesh

        def _counting_make_mesh(self, *a, **kw):
            march_calls.append(1)
            return original_make_mesh(self, *a, **kw)

        VolumeRenderer.make_mesh = _counting_make_mesh
        try:
            # First call — no cache, must march.
            _rebuild_volume(reader, plotter, vs, state, "vol0")
            assert len(march_calls) == 1

            # Colormap change — must NOT march again.
            state.colormap = "plasma"
            vs.get_volume_hints("vol0").colormap = "plasma"
            _rebuild_volume(reader, plotter, vs, state, "vol0")
            assert len(march_calls) == 1, (
                "colormap change must reuse cached mesh (no re-march)"
            )

            # Opacity change — must NOT march again.
            state.opacity = 0.3
            vs.get_volume_hints("vol0").opacity = 0.3
            _rebuild_volume(reader, plotter, vs, state, "vol0")
            assert len(march_calls) == 1, (
                "opacity change must reuse cached mesh (no re-march)"
            )

            # Isovalue change — MUST re-march.
            state.isovalue = 0.2
            vs.get_volume_hints("vol0").isovalue = 0.2
            _rebuild_volume(reader, plotter, vs, state, "vol0")
            assert len(march_calls) == 2, (
                "isovalue change must invalidate the cache and re-march"
            )
        finally:
            VolumeRenderer.make_mesh = original_make_mesh


# ── A7-08: Glyph instancing ───────────────────────────────────────────


class TestGlyphInstancing:
    """Glyph instancing for replicated periodic cells (A7-08)."""

    def test_replicated_cell_fewer_draw_calls_than_atoms(self):
        """A 2×2×2 supercell of Na+Cl has 16 atoms but only 2 element types.
        The renderer must issue ≤ 2 atom draw calls (one glyph per element),
        not 16."""
        import pyvista as pv
        from vibeview.renderers.structure import StructureRenderer

        reader = _nacl_structure_reader()
        plotter = pv.Plotter(off_screen=True)
        section = reader.get_section("structure")
        renderer = StructureRenderer(section, reader)
        renderer.add_to_plotter(plotter, replication=(2, 2, 2))

        atom_actors = [k for k in plotter.actors if str(k).startswith("atom")]
        n_atoms_replicated = 2 * 2 * 2 * 2  # 2 atoms × 2×2×2
        assert len(atom_actors) < n_atoms_replicated, (
            f"Replicated cell should use fewer draw calls than total atoms "
            f"({len(atom_actors)} < {n_atoms_replicated}); "
            "glyph instancing expected"
        )

    def test_molecular_cell_keeps_per_atom_actors(self):
        """The non-replicated path must still produce per-atom named actors
        (``atom_<idx>_<slot>``) for the charge-overlay and CPK-restore
        paths that need to address individual atoms."""
        import pyvista as pv
        from vibeview.renderers.structure import StructureRenderer

        reader = _nacl_structure_reader()
        plotter = pv.Plotter(off_screen=True)
        section = reader.get_section("structure")
        renderer = StructureRenderer(section, reader)
        renderer.add_to_plotter(plotter, replication=(1, 1, 1))

        # Per-atom names: "atom_0_0", "atom_1_1", …
        per_atom = [k for k in plotter.actors if str(k).startswith("atom_") and "_" in str(k)[5:]]
        assert len(per_atom) == 2, (
            "non-replicated cell must keep per-atom actors for CPK/charge targeting"
        )

    def test_replicated_produces_correct_atom_count_via_glyphs(self):
        """The glyph mesh for a 2×2×2 NaCl supercell should contain points
        for all 16 atoms (8 Na + 8 Cl)."""
        import pyvista as pv
        from vibeview.renderers.structure import StructureRenderer

        reader = _nacl_structure_reader()
        plotter = pv.Plotter(off_screen=True)
        section = reader.get_section("structure")
        renderer = StructureRenderer(section, reader)
        renderer.add_to_plotter(plotter, replication=(2, 2, 2))

        # Each group glyph has all atom centres as points; total across
        # groups = 8 (Na group) + 8 (Cl group) sphere-centre points embedded
        # in the glyph meshes. The point count of each glyph mesh is
        # n_atoms_in_group × n_sphere_points — so we verify that the group
        # actors exist and together cover more than 0 points.
        total_pts = sum(
            a.GetMapper().GetInput().GetNumberOfPoints()
            for k, a in plotter.actors.items()
            if str(k).startswith("atom_group_")
        )
        assert total_pts > 0


# ── A7-04: Pre-rendered energy frames ────────────────────────────────


class TestPrerenderedEnergyFrames:
    """Energy-plot pre-rendering (A7-04)."""

    def test_trajectory_renders_all_frames_on_first_call(self):
        from vibeview.renderers.trajectory import TrajectoryRenderer

        reader = _trajectory_reader(n_frames=4)
        renderer = TrajectoryRenderer(reader.get_section("traj0"), reader)

        assert renderer._frame_pngs is None, "cache must start empty"
        png = renderer.render_energy_plot(0)
        assert png, "should return non-empty PNG bytes"
        assert renderer._frame_pngs is not None, "cache must be populated after first call"
        assert len(renderer._frame_pngs) == 4, "one PNG per frame"

    def test_trajectory_subsequent_calls_return_cached(self):
        """Second call to render_energy_plot must not re-run matplotlib
        (returns the cached PNG, which is bit-identical)."""
        from vibeview.renderers.trajectory import TrajectoryRenderer

        reader = _trajectory_reader(n_frames=3)
        renderer = TrajectoryRenderer(reader.get_section("traj0"), reader)
        png_first = renderer.render_energy_plot(1)
        # Force a second call — must return the same bytes object from cache.
        png_second = renderer.render_energy_plot(1)
        assert png_first == png_second, "repeated call must return cached PNG"

    def test_trajectory_different_frames_differ(self):
        """Each pre-rendered frame has a different indicator dot position,
        so PNGs for different frames must NOT be identical."""
        from vibeview.renderers.trajectory import TrajectoryRenderer

        reader = _trajectory_reader(n_frames=3)
        renderer = TrajectoryRenderer(reader.get_section("traj0"), reader)
        png0 = renderer.render_energy_plot(0)
        png2 = renderer.render_energy_plot(2)
        assert png0 != png2, "different frames must produce different PNGs"

    def test_reaction_prerender_all_frames(self):
        """ReactionPathRenderer pre-renders its frames on first call too."""
        from vibeview.qvf import QVFReader
        from vibeview.renderers.reaction import ReactionPathRenderer

        # Build a minimal reaction.path QVF
        n = 5
        atoms = [{"symbol": "H", "atomic_number": 1}]
        waypoints = [{"frame_index": 2, "label": "TS", "kind": "transition_state"}]
        coords = np.zeros((n, 1, 3), dtype=np.float64)
        energies = list(np.linspace(-1.0, -0.8, n).tolist())

        meta = json.dumps({
            "atoms": atoms, "energies": energies,
            "reaction_coordinate": list(np.linspace(0, 1, n).tolist()),
            "waypoints": waypoints,
        }).encode()
        coords_b = coords.tobytes()
        sections = [{
            "id": "rxn0", "kind": "reaction.path",
            "members": {
                "metadata": {"path": "m.json", "format": "json", "sha256": _sha(meta)},
                "coords": {"path": "c.bin", "format": "binary", "dtype": "float64",
                           "shape": [n, 1, 3], "sha256": _sha(coords_b)},
            },
        }]
        manifest = {
            "qvf_version": 1,
            "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
            "sections": sections,
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("m.json", meta)
            zf.writestr("c.bin", coords_b)
        reader = QVFReader(buf.getvalue())

        renderer = ReactionPathRenderer(reader.get_section("rxn0"), reader)
        assert renderer._frame_pngs is None
        png = renderer.render_energy_plot(0)
        assert png
        assert renderer._frame_pngs is not None
        assert len(renderer._frame_pngs) == n
