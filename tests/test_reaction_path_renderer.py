"""QVF Inc B — ReactionPathRenderer periodic cell + atom-wrap tests.

The renderer-level surface for periodic reaction.path archives (QVF
v2): ``has_cell``, ``dim``, ``cell_for_frame``, ``cell_edges_for_frame``,
and the periodic wrap inside ``get_frame``. The app layer
(``vibe-view/src/vibeview/app.py``) consumes these methods to draw the
unit-cell wireframe + render wrapped atom positions per frame; the
app integration is visually verifiable but not unit-testable here
(PyVista isn't loaded in the QVF-only test path).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest

from vibeview.qvf import QVFReader
from vibeview.renderers.reaction import (
    ReactionPathRenderer,
    _cell_edges,
    _wrap_positions,
)

_BOHR_TO_ANGSTROM = 0.529177210903


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _molecular_rxn_path_qvf() -> tuple[Path, str]:
    """v1 reaction.path — no lattice, no dim."""
    meta = json.dumps(
        {
            "atoms": [{"symbol": "H", "atomic_number": 1}],
            "energies": [0.0, -0.5, -1.0],
            "waypoints": [],
        }
    ).encode()
    coords = np.zeros((3, 1, 3), dtype=np.float64).tobytes()
    sections = [
        {
            "id": "rxn",
            "kind": "reaction.path",
            "members": {
                "metadata": {
                    "path": "rxn/meta.json",
                    "format": "json",
                    "sha256": _sha256(meta),
                },
                "coords": {
                    "path": "rxn/coords.dat",
                    "format": "binary",
                    "dtype": "float64",
                    "shape": [3, 1, 3],
                    "sha256": _sha256(coords),
                },
            },
        }
    ]
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "t"},
        "sections": sections,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("rxn/meta.json", meta)
        zf.writestr("rxn/coords.dat", coords)
    return Path(tmp.name), "rxn"


def _periodic_rxn_path_qvf(
    dim: int = 3,
    coords_override: np.ndarray | None = None,
    dim_per_frame: list[int] | None = None,
) -> tuple[Path, str]:
    """v2 reaction.path with a shared cubic lattice.

    The lattice is a 4-bohr cubic cell; atoms drift outside it across
    frames so the renderer's wrap logic has something to act on.
    Atom positions are stored in Å (per the writer's convention) —
    here we just emit them as raw float64s; the renderer doesn't
    inspect units, the test does.
    """
    # Two atoms moving across cell boundaries (positions in Å — what
    # the writer would have emitted after bohr→Å conversion).
    if coords_override is not None:
        coords_arr = np.asarray(coords_override, dtype=np.float64)
    else:
        coords_arr = np.array(
            [
                [[0.5, 0.5, 0.5], [3.0, 0.5, 0.5]],
                [[0.5, 0.5, 0.5], [3.2, 0.5, 0.5]],  # atom 1 crossing edge
                [[0.5, 0.5, 0.5], [-0.5, 0.5, 0.5]],
            ],
            dtype=np.float64,
        )
    # Lattice in bohr (per QVF v2 contract); 4 Å ≈ 7.56 bohr is overkill;
    # we instead use a 4-bohr cell whose Å-equivalent (~2.117 Å) keeps
    # the test arithmetic legible.
    L_bohr = np.diag([4.0, 4.0, 4.0]).astype(np.float64)
    coords_bytes = coords_arr.tobytes()
    lat_bytes = L_bohr.tobytes()
    meta_dict: dict = {
        "atoms": [
            {"symbol": "H", "atomic_number": 1},
            {"symbol": "H", "atomic_number": 1},
        ],
        "energies": [0.0, -0.1, -0.2][: coords_arr.shape[0]],
        "waypoints": [],
    }
    # A variable-dim path carries dim_per_frame instead of a scalar dim.
    if dim_per_frame is not None:
        meta_dict["dim_per_frame"] = [int(d) for d in dim_per_frame]
    else:
        meta_dict["dim"] = int(dim)
    meta = json.dumps(meta_dict).encode()
    sections = [
        {
            "id": "rxn",
            "kind": "reaction.path",
            "members": {
                "metadata": {
                    "path": "rxn/meta.json",
                    "format": "json",
                    "sha256": _sha256(meta),
                },
                "coords": {
                    "path": "rxn/coords.dat",
                    "format": "binary",
                    "dtype": "float64",
                    "shape": list(coords_arr.shape),
                    "sha256": _sha256(coords_bytes),
                },
                "lattice": {
                    "path": "rxn/lattice.dat",
                    "format": "binary",
                    "dtype": "float64",
                    "shape": [3, 3],
                    "sha256": _sha256(lat_bytes),
                },
            },
        }
    ]
    manifest = {
        "qvf_version": 2,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "t"},
        "sections": sections,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("rxn/meta.json", meta)
        zf.writestr("rxn/coords.dat", coords_bytes)
        zf.writestr("rxn/lattice.dat", lat_bytes)
    return Path(tmp.name), "rxn"


# ---- Pure helpers ---------------------------------------------------------


class TestWrapPositionsHelper:
    def test_wrap_axes_zero_is_identity(self):
        L = np.diag([4.0, 4.0, 4.0])
        coords = np.array([[5.0, 5.0, 5.0], [-1.0, 0.0, 0.0]])
        out = _wrap_positions(coords, L, wrap_axes=0)
        np.testing.assert_allclose(out, coords)

    def test_wrap_axes_three_folds_into_central_cell(self):
        L = np.diag([4.0, 4.0, 4.0])
        coords = np.array(
            [
                [5.0, 0.5, 0.5],     # x=5 → wraps to 1 (frac 1.25 → 0.25)
                [-0.5, -0.5, -0.5],  # all negative → wraps to 3.5
                [4.0, 4.0, 4.0],     # exactly on edge → wraps to 0
            ]
        )
        out = _wrap_positions(coords, L, wrap_axes=3)
        np.testing.assert_allclose(
            out,
            np.array(
                [
                    [1.0, 0.5, 0.5],
                    [3.5, 3.5, 3.5],
                    [0.0, 0.0, 0.0],
                ]
            ),
            atol=1e-12,
        )

    def test_wrap_axes_two_leaves_z_open(self):
        """Slab convention — dim=2 wraps a + b in-plane; the vacuum
        direction c is left untouched."""
        L = np.diag([4.0, 4.0, 20.0])
        coords = np.array(
            [
                [5.0, 5.0, 15.0],   # x, y wrap; z stays 15
                [-1.0, 0.0, -2.0],  # x wraps; z stays -2
            ]
        )
        out = _wrap_positions(coords, L, wrap_axes=2)
        np.testing.assert_allclose(
            out,
            np.array(
                [
                    [1.0, 1.0, 15.0],
                    [3.0, 0.0, -2.0],
                ]
            ),
            atol=1e-12,
        )

    def test_wrap_axes_one_only_along_a(self):
        L = np.diag([4.0, 4.0, 4.0])
        coords = np.array([[5.0, 5.0, 5.0]])
        out = _wrap_positions(coords, L, wrap_axes=1)
        np.testing.assert_allclose(out, np.array([[1.0, 5.0, 5.0]]))


class TestCellEdgesHelper:
    def test_orthorhombic_cell_emits_12_edges(self):
        L = np.diag([1.0, 2.0, 3.0])
        edges = _cell_edges(L)
        assert len(edges) == 12
        # Origin is a corner.
        starts = {tuple(p1) for p1, _ in edges}
        assert (0.0, 0.0, 0.0) in starts
        # Far corner a + b + c = (1, 2, 3) is also a corner.
        ends = {tuple(p2) for _, p2 in edges}
        assert (1.0, 2.0, 3.0) in ends

    def test_edge_lengths_match_lattice_vectors(self):
        L = np.diag([2.0, 3.0, 5.0])
        edges = _cell_edges(L)
        lengths = sorted(
            float(np.linalg.norm(p2 - p1)) for p1, p2 in edges
        )
        # Each cell vector is replicated four times (four parallel edges).
        np.testing.assert_allclose(lengths, [2, 2, 2, 2, 3, 3, 3, 3, 5, 5, 5, 5])


# ---- Renderer methods on a molecular (v1) archive ------------------------


class TestRendererOnMolecularArchive:
    def test_has_cell_false_for_molecular(self):
        path, sid = _molecular_rxn_path_qvf()
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            assert renderer.has_cell is False
            assert renderer.dim is None
            assert renderer.cell_for_frame(0) is None
            assert renderer.cell_edges_for_frame(0) is None
        finally:
            path.unlink()

    def test_get_frame_unchanged_for_molecular(self):
        path, sid = _molecular_rxn_path_qvf()
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            atoms = renderer.get_frame(0)
            assert len(atoms) == 1
            np.testing.assert_allclose(atoms[0][1], np.zeros(3))
        finally:
            path.unlink()


# ---- Renderer methods on a periodic (v2) archive -------------------------


class TestRendererOnPeriodicArchive:
    def test_has_cell_true_and_dim_3(self):
        path, sid = _periodic_rxn_path_qvf(dim=3)
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            assert renderer.has_cell is True
            assert renderer.dim == 3
        finally:
            path.unlink()

    def test_cell_for_frame_returns_angstrom_lattice(self):
        path, sid = _periodic_rxn_path_qvf(dim=3)
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            L = renderer.cell_for_frame(0)
            assert L is not None
            assert L.shape == (3, 3)
            # Stored as 4-bohr cubic; renderer emits Å.
            np.testing.assert_allclose(
                L,
                np.diag([4.0, 4.0, 4.0]) * _BOHR_TO_ANGSTROM,
                atol=1e-12,
            )
        finally:
            path.unlink()

    def test_cell_edges_for_frame_yields_12_edges(self):
        path, sid = _periodic_rxn_path_qvf(dim=3)
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            edges = renderer.cell_edges_for_frame(0)
            assert edges is not None
            assert len(edges) == 12
        finally:
            path.unlink()

    def test_get_frame_wraps_atoms_into_cell_for_dim_3(self):
        path, sid = _periodic_rxn_path_qvf(dim=3)
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            L_ang = np.diag([4.0, 4.0, 4.0]) * _BOHR_TO_ANGSTROM
            # Frame 2's atom-1 is at (-0.5, 0.5, 0.5) Å; the renderer
            # must wrap it back to L_x + (-0.5) ≈ 1.6168 Å.
            atoms = renderer.get_frame(2)
            assert len(atoms) == 2
            x_wrapped = atoms[1][1][0]
            assert x_wrapped > 0.0
            assert x_wrapped < L_ang[0, 0]
        finally:
            path.unlink()

    def test_get_frame_slab_leaves_vacuum_axis_open(self):
        """A dim=2 reaction.path must wrap a + b only; positions
        along c are returned verbatim even when out of the cell.

        The fixture's atom 1 is placed at z=10 Å — well outside the
        cubic-cell extent — so the renderer's wrap behaviour along
        the non-physical column c is observable.
        """
        coords = np.array(
            [
                [[0.5, 0.5, 0.5], [0.5, 0.5, 10.0]],
            ],
            dtype=np.float64,
        )
        path, sid = _periodic_rxn_path_qvf(dim=2, coords_override=coords)
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            atoms = renderer.get_frame(0)
            z = atoms[1][1][2]
            # dim=2 → vacuum axis c is NOT wrapped; z=10 survives.
            assert z == pytest.approx(10.0)
        finally:
            path.unlink()


# ---- V3: variable-dim (dim_per_frame) wrapping ---------------------------


class TestVariableDimWrapping:
    """A path carrying ``dim_per_frame`` must wrap each frame by *its*
    dimensionality — the scalar ``dim`` is None here, and the old code
    fell back to dim=3 for every frame, silently mis-wrapping the slab
    frame's vacuum axis.
    """

    def test_dim_for_frame_reads_per_frame_values(self):
        coords = np.array(
            [
                [[0.5, 0.5, 0.5], [0.5, 0.5, 10.0]],  # frame 0
                [[0.5, 0.5, 0.5], [0.5, 0.5, 10.0]],  # frame 1
            ],
            dtype=np.float64,
        )
        path, sid = _periodic_rxn_path_qvf(
            coords_override=coords, dim_per_frame=[2, 3]
        )
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            assert renderer.dim is None  # scalar dim absent
            assert renderer.dim_for_frame(0) == 2
            assert renderer.dim_for_frame(1) == 3
        finally:
            path.unlink()

    def test_per_frame_dim_drives_wrapping(self):
        L_ang = np.diag([4.0, 4.0, 4.0]) * _BOHR_TO_ANGSTROM
        coords = np.array(
            [
                [[0.5, 0.5, 0.5], [0.5, 0.5, 10.0]],  # frame 0 (dim=2)
                [[0.5, 0.5, 0.5], [0.5, 0.5, 10.0]],  # frame 1 (dim=3)
            ],
            dtype=np.float64,
        )
        path, sid = _periodic_rxn_path_qvf(
            coords_override=coords, dim_per_frame=[2, 3]
        )
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            # Frame 0 is a slab (dim=2): vacuum axis c not wrapped → z=10.
            z0 = renderer.get_frame(0)[1][1][2]
            assert z0 == pytest.approx(10.0)
            # Frame 1 is dim=3: z=10 Å IS wrapped back into the cell.
            z1 = renderer.get_frame(1)[1][1][2]
            assert 0.0 <= z1 < L_ang[2, 2]
        finally:
            path.unlink()


# ---- V0: reaction-coordinate axis label/unit -----------------------------


def _labelled_rxn_path_qvf(
    label: str | None,
    unit: str | None,
) -> tuple[Path, str]:
    """v1 molecular reaction.path carrying optional coordinate
    label/unit metadata + a reaction_coordinate array."""
    meta_dict: dict = {
        "atoms": [{"symbol": "H", "atomic_number": 1}],
        "energies": [0.0, -0.5, -1.0],
        "reaction_coordinate": [1.6, 1.8, 2.0],
        "waypoints": [],
    }
    if label is not None:
        meta_dict["reaction_coordinate_label"] = label
    if unit is not None:
        meta_dict["reaction_coordinate_unit"] = unit
    meta = json.dumps(meta_dict).encode()
    coords = np.zeros((3, 1, 3), dtype=np.float64).tobytes()
    sections = [
        {
            "id": "rxn",
            "kind": "reaction.path",
            "members": {
                "metadata": {
                    "path": "rxn/meta.json",
                    "format": "json",
                    "sha256": _sha256(meta),
                },
                "coords": {
                    "path": "rxn/coords.dat",
                    "format": "binary",
                    "dtype": "float64",
                    "shape": [3, 1, 3],
                    "sha256": _sha256(coords),
                },
            },
        }
    ]
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "t"},
        "sections": sections,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("rxn/meta.json", meta)
        zf.writestr("rxn/coords.dat", coords)
    return Path(tmp.name), "rxn"


def _rxn_path_with_volumes_qvf(
    volume_frame_index: list[int],
    n_frames: int = 4,
    grid_shape: tuple[int, int, int] = (3, 3, 3),
) -> tuple[Path, str]:
    """v1 molecular reaction.path carrying per-frame volumes (W1).

    Each emitted slab is filled with a constant = its slab index, so a
    test can tell which slab `volume_data_for_frame` returned.
    """
    n_emit = len(volume_frame_index)
    nx, ny, nz = grid_shape
    vols = np.stack(
        [np.full(grid_shape, float(k), dtype=np.float32) for k in range(n_emit)],
        axis=0,
    )
    grid = json.dumps(
        {
            "origin": [0.0, 0.0, 0.0],
            "voxel_vectors": [[0.5, 0, 0], [0, 0.5, 0], [0, 0, 0.5]],
            "shape": [nx, ny, nz],
        }
    ).encode()
    meta = json.dumps(
        {
            "atoms": [{"symbol": "H", "atomic_number": 1}],
            "energies": [float(-i) for i in range(n_frames)],
            "waypoints": [],
            "volume_frame_index": volume_frame_index,
            "volume_label": "Electron density",
        }
    ).encode()
    coords = np.zeros((n_frames, 1, 3), dtype=np.float64).tobytes()
    vols_bytes = vols.tobytes()
    sections = [
        {
            "id": "rxn",
            "kind": "reaction.path",
            "members": {
                "metadata": {
                    "path": "rxn/meta.json",
                    "format": "json",
                    "sha256": _sha256(meta),
                },
                "coords": {
                    "path": "rxn/coords.dat",
                    "format": "binary",
                    "dtype": "float64",
                    "shape": [n_frames, 1, 3],
                    "sha256": _sha256(coords),
                },
                "frame_volumes": {
                    "path": "rxn/vols.dat",
                    "format": "binary",
                    "dtype": "float32",
                    "shape": [n_emit, nx, ny, nz],
                    "sha256": _sha256(vols_bytes),
                },
                "volume_grid": {
                    "path": "rxn/grid.json",
                    "format": "json",
                    "sha256": _sha256(grid),
                },
            },
        }
    ]
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "t"},
        "sections": sections,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("rxn/meta.json", meta)
        zf.writestr("rxn/coords.dat", coords)
        zf.writestr("rxn/vols.dat", vols_bytes)
        zf.writestr("rxn/grid.json", grid)
    return Path(tmp.name), "rxn"


class TestPerFrameVolumes:
    def test_no_volumes_on_plain_path(self):
        path, sid = _molecular_rxn_path_qvf()
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            assert renderer.has_volumes is False
            assert renderer.volume_grid() is None
            assert renderer.volume_data_for_frame(0) is None
        finally:
            path.unlink()

    def test_volume_grid_and_label_surface(self):
        path, sid = _rxn_path_with_volumes_qvf([0, 1, 2, 3])
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            assert renderer.has_volumes is True
            assert renderer.volume_label() == "Electron density"
            grid = renderer.volume_grid()
            assert grid is not None
            assert tuple(grid.shape) == (3, 3, 3)
        finally:
            path.unlink()

    def test_every_frame_emitted_returns_matching_slab(self):
        path, sid = _rxn_path_with_volumes_qvf([0, 1, 2, 3])
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            for frame in range(4):
                vol = renderer.volume_data_for_frame(frame)
                assert vol is not None
                # Slab k is filled with constant k; frame==slab here.
                assert float(vol.flat[0]) == pytest.approx(float(frame))
        finally:
            path.unlink()

    def test_volume_mesh_for_frame_returns_polydata(self):
        # A 6×6×6 grid with a high-value core contours to a non-empty
        # isosurface at a mid isovalue.
        import numpy as _np

        path, sid = _rxn_path_with_volumes_qvf([0, 1], n_frames=2, grid_shape=(6, 6, 6))
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            # Overwrite slab 0 with a Gaussian bump so contouring yields
            # a surface (the fixture's constant-fill slabs don't).
            data = renderer.load()
            vols = _np.array(data.frame_volumes, dtype=_np.float32)  # writable copy
            vols[0] = 0.0
            vols[0, 2:4, 2:4, 2:4] = 1.0
            data.frame_volumes = vols
            mesh = renderer.volume_mesh_for_frame(0, isovalue=0.5)
            assert mesh is not None
            assert mesh.n_points > 0
        finally:
            path.unlink()

    def test_volume_mesh_none_without_volumes(self):
        path, sid = _molecular_rxn_path_qvf()
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            assert renderer.volume_mesh_for_frame(0) is None
        finally:
            path.unlink()

    def test_decimated_volumes_hold_last_emitted(self):
        # Emit slabs at frames 0 and 2 only (every-2nd). Frame 1 holds
        # slab 0; frame 3 holds slab 1 (the frame-2 emission).
        path, sid = _rxn_path_with_volumes_qvf([0, 2], n_frames=4)
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            assert float(renderer.volume_data_for_frame(0).flat[0]) == 0.0
            assert float(renderer.volume_data_for_frame(1).flat[0]) == 0.0
            assert float(renderer.volume_data_for_frame(2).flat[0]) == 1.0
            assert float(renderer.volume_data_for_frame(3).flat[0]) == 1.0
        finally:
            path.unlink()


class TestEnergyPlotAxisLabel:
    def test_label_and_unit_combine(self):
        path, sid = _labelled_rxn_path_qvf("O–H distance", "bohr")
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            assert renderer.energy_plot_x_label() == "O–H distance (bohr)"
        finally:
            path.unlink()

    def test_label_without_unit(self):
        path, sid = _labelled_rxn_path_qvf("dihedral 0–1–2–3", None)
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            assert renderer.energy_plot_x_label() == "dihedral 0–1–2–3"
        finally:
            path.unlink()

    def test_falls_back_to_generic_when_absent(self):
        path, sid = _labelled_rxn_path_qvf(None, None)
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            # reaction_coordinate present but no label → generic.
            assert renderer.energy_plot_x_label() == "Reaction coordinate"
        finally:
            path.unlink()

    def test_render_energy_plot_still_produces_png(self):
        path, sid = _labelled_rxn_path_qvf("O–H distance", "bohr")
        try:
            reader = QVFReader(path)
            renderer = ReactionPathRenderer(reader.get_section(sid), reader)
            png = renderer.render_energy_plot(0)
            assert png.startswith(b"\x89PNG")
        finally:
            path.unlink()
