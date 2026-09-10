"""Genuine 2D slabs render as flat sheets, not sheets inside a phantom box.

vibe-qc no longer represents a slab as a dim=3 cell with a big vacuum gap. A
slab is now ``dim=2``: two in-plane lattice vectors, atoms at real Cartesian z,
and a third lattice column that is **auto-synthesized bookkeeping** so AO
integrals and spglib keep a full-rank 3x3 matrix. The SCF energy is provably
invariant to that column's length.

The trap this module guards: the synthesized ``a3`` is numerically
indistinguishable from the old vacuum gap. No heuristic on the lattice matrix
can tell them apart — only ``pbc`` (and ``dim``) can. So a viewer that keys cell
drawing or replication off the lattice will keep drawing a vacuum box forever.

See handovers/HANDOVER_VIBE_VIEW_2D_SLABS.md.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile

import numpy as np
import pytest

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

# Graphene: hexagonal in-plane cell, both atoms at z = 0 (real Cartesian z).
GRAPHENE_A1 = [2.4674, 0.0, 0.0]
GRAPHENE_A2 = [1.2337, 2.1368, 0.0]
GRAPHENE_ATOMS = [
    {"symbol": "C", "position": [0.0, 0.0, 0.0], "atomic_number": 6},
    {"symbol": "C", "position": [1.2337, 0.7123, 0.0], "atomic_number": 6},
]


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _reader(structure: dict):
    """Wrap a structure payload in a minimal single-section QVF archive."""
    from vibeview.qvf import QVFReader

    blob = json.dumps(structure).encode()
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {"path": "s.json", "format": "json", "sha256": _sha(blob)}
                },
            }
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", blob)
    return QVFReader(buf.getvalue())


def _slab(a3_len: float = 30.0, **overrides) -> dict:
    """A dim=2 graphene slab whose synthesized a3 has the given length."""
    payload = {
        "atoms": GRAPHENE_ATOMS,
        "pbc": [True, True, False],
        "dimensionality": 2,
        "lattice_vectors": [GRAPHENE_A1, GRAPHENE_A2, [0.0, 0.0, a3_len]],
    }
    payload.update(overrides)
    return payload


def _render(structure: dict, replication=(1, 1, 1)):
    """Render into an off-screen plotter; return the plotter."""
    import pyvista as pv

    from vibeview.renderers.structure import StructureRenderer

    reader = _reader(structure)
    sr = StructureRenderer(reader.get_section("structure"), reader)
    plotter = pv.Plotter(off_screen=True)
    sr.add_to_plotter(plotter, replication=replication)
    return plotter


def _cell_actor_names(plotter) -> list[str]:
    return sorted(str(k) for k in plotter.actors if str(k).startswith("cell_"))


def _scene_points(plotter) -> np.ndarray:
    """Every point of every actor, sorted — a render fingerprint."""
    pts = []
    for name in sorted(str(k) for k in plotter.actors):
        mesh = plotter.actors[name].GetMapper().GetInput()
        pts.append(np.asarray(mesh.GetPoints().GetData()))
    stacked = np.vstack(pts)
    order = np.lexsort((stacked[:, 2], stacked[:, 1], stacked[:, 0]))
    return np.round(stacked[order], 6)


# ── Cell drawing: only periodic axes are cell edges ──────────────────────


def test_slab_draws_only_in_plane_parallelogram():
    """dim=2 -> 4 edges (a, b parallelogram). Never the 12-edge box."""
    plotter = _render(_slab())
    assert len(_cell_actor_names(plotter)) == 4


def test_bulk_crystal_still_draws_full_parallelepiped():
    """dim=3 is unchanged: 8 corners, 12 edges."""
    bulk = {
        "atoms": [{"symbol": "Na", "position": [0.0, 0.0, 0.0], "atomic_number": 11}],
        "pbc": [True, True, True],
        "dimensionality": 3,
        "lattice_vectors": [[5.0, 0, 0], [0, 5.0, 0], [0, 0, 5.0]],
    }
    plotter = _render(bulk)
    assert len(_cell_actor_names(plotter)) == 12


def test_polymer_draws_single_chain_axis_edge():
    """dim=1 -> exactly one edge, along the chain axis."""
    polymer = {
        "atoms": [{"symbol": "C", "position": [0.0, 0.0, 0.0], "atomic_number": 6}],
        "pbc": [True, False, False],
        "dimensionality": 1,
        "lattice_vectors": [[2.5, 0, 0], [0, 30.0, 0], [0, 0, 30.0]],
    }
    plotter = _render(polymer)
    assert len(_cell_actor_names(plotter)) == 1


def test_slab_cell_never_extends_along_the_normal():
    """No drawn cell geometry may reach out to the synthesized a3."""
    plotter = _render(_slab(a3_len=30.0))
    for name in _cell_actor_names(plotter):
        bounds = plotter.actors[name].GetMapper().GetInput().GetBounds()
        assert abs(bounds[5]) < 1e-6, f"{name} extends to z={bounds[5]} (drew the a3 column)"


# ── The regression guard from the handover's acceptance criteria ─────────


def test_slab_renders_identically_regardless_of_synthesized_a3_length():
    """|a3| = 30 vs 80 bohr must be pixel-for-pixel the same scene.

    The SCF total is bit-identical across this change, so any visual difference
    means the viewer is still reading a3 as geometry.
    """
    thin = _render(_slab(a3_len=30.0))
    thick = _render(_slab(a3_len=80.0))

    assert _cell_actor_names(thin) == _cell_actor_names(thick)
    np.testing.assert_allclose(_scene_points(thin), _scene_points(thick), atol=1e-9)


def test_old_vacuum_convention_file_renders_like_the_new_one():
    """An old-convention archive (real 50 Å vacuum, atoms flush at z=0) carries
    the same pbc=[T,T,F], so it must now render as a flat sheet too."""
    legacy = _render(_slab(a3_len=94.4863))  # the old graphene a3, 50 Å in bohr
    modern = _render(_slab(a3_len=30.0))
    np.testing.assert_allclose(_scene_points(legacy), _scene_points(modern), atol=1e-9)


# ── Replication is gated per axis ────────────────────────────────────────


def test_slab_never_replicates_along_the_normal():
    """Nz is inert for a slab: the scene is identical whatever Nz the user types."""
    single = _render(_slab(), replication=(1, 1, 1))
    stacked = _render(_slab(), replication=(1, 1, 4))
    np.testing.assert_allclose(_scene_points(single), _scene_points(stacked), atol=1e-9)

    # ...and in-plane replication is unaffected by a stray Nz.
    in_plane = _render(_slab(), replication=(2, 2, 1))
    with_nz = _render(_slab(), replication=(2, 2, 4))
    np.testing.assert_allclose(_scene_points(in_plane), _scene_points(with_nz), atol=1e-9)
    # The 2x2 tiling really did happen (more geometry than the 1x1 cell).
    assert _scene_points(in_plane).shape[0] > _scene_points(single).shape[0]


def test_builder_supercell_scales_only_real_slab_lattice_rows():
    """The builder must not turn a synthesized slab normal into a cell edge."""
    from vibeview.app import create_app

    reader = _reader(_slab(a3_len=30.0))
    try:
        server = create_app(reader)
        state, ctrl = server.state, server.controller
        state.edit_history = []
        state.edit_future = []
        state.build_supercell_nx = 2
        state.build_supercell_ny = 3
        state.build_supercell_nz = 4  # hidden/non-periodic input is ignored

        ctrl.build_supercell()
        built = reader.read_structure()
        assert len(built.atoms) == len(GRAPHENE_ATOMS) * 2 * 3
        assert built.pbc == (True, True, False)
        assert built.dim == 2
        np.testing.assert_allclose(
            built.lattice_vectors[0], np.asarray(GRAPHENE_A1) * 2
        )
        np.testing.assert_allclose(
            built.lattice_vectors[1], np.asarray(GRAPHENE_A2) * 3
        )
        np.testing.assert_allclose(built.lattice_vectors[2], [0.0, 0.0, 30.0])
        fractional = np.asarray(
            [atom.position for atom in built.atoms]
        ) @ np.linalg.inv(built.lattice_vectors)
        assert np.all(fractional[:, :2] >= -1.0e-12)
        assert np.all(fractional[:, :2] < 1.0 + 1.0e-12)
        assert "Supercell 2x3x1" in state.status_message

        ctrl.edit_undo()
        restored = reader.read_structure()
        assert len(restored.atoms) == len(GRAPHENE_ATOMS)
        np.testing.assert_allclose(restored.lattice_vectors[0], GRAPHENE_A1)
        np.testing.assert_allclose(restored.lattice_vectors[1], GRAPHENE_A2)
        np.testing.assert_allclose(restored.lattice_vectors[2], [0.0, 0.0, 30.0])
    finally:
        reader.close()


def test_bulk_crystal_still_replicates_in_three_dimensions():
    """The clamp must not regress 3D crystals."""
    bulk = {
        "atoms": [{"symbol": "Na", "position": [0.0, 0.0, 0.0], "atomic_number": 11}],
        "pbc": [True, True, True],
        "dimensionality": 3,
        "lattice_vectors": [[5.0, 0, 0], [0, 5.0, 0], [0, 0, 5.0]],
    }
    flat = _render(bulk, replication=(2, 2, 1))
    cube = _render(bulk, replication=(2, 2, 2))
    assert _scene_points(cube).shape[0] > _scene_points(flat).shape[0]


def test_clamp_replication_forces_non_periodic_axes_to_one():
    from vibeview.qvf import clamp_replication

    assert clamp_replication((3, 4, 5), (True, True, False)) == (3, 4, 1)
    assert clamp_replication((3, 4, 5), (True, False, False)) == (3, 1, 1)
    assert clamp_replication((3, 4, 5), (True, True, True)) == (3, 4, 5)
    assert clamp_replication((0, -2, 5), (True, True, True)) == (1, 1, 5)


# ── pbc / dim resolution ─────────────────────────────────────────────────


def test_pbc_is_normative_and_dim_is_carried():
    from vibeview.qvf import _resolve_pbc

    pbc, dim = _resolve_pbc({"pbc": [True, True, False], "dimensionality": 2})
    assert pbc == (True, True, False)
    assert dim == 2


def test_dim_alone_implies_the_first_dim_axes_are_periodic():
    """The vibe-qc core convention: lattice sums run over the first `dim` axes."""
    from vibeview.qvf import _resolve_pbc

    assert _resolve_pbc({"dimensionality": 2})[0] == (True, True, False)
    assert _resolve_pbc({"dimensionality": 1})[0] == (True, False, False)
    assert _resolve_pbc({"dimensionality": 3})[0] == (True, True, True)


def test_molecule_has_no_periodic_axes():
    from vibeview.qvf import _resolve_pbc

    assert _resolve_pbc({}) == ((False, False, False), 0)


def test_dim_is_derived_from_pbc_not_read_alongside_it():
    """`dimensionality` is a derived count, so `pbc` alone fixes it."""
    from vibeview.qvf import _resolve_pbc

    assert _resolve_pbc({"pbc": [True, True, False]})[1] == 2
    assert _resolve_pbc({"pbc": [True, False, False]})[1] == 1
    assert _resolve_pbc({"pbc": [False, False, False]})[1] == 0


def test_contradictory_pbc_and_dim_is_rejected():
    """Guessing which field is the lie is how a slab got a phantom vacuum box.

    Nothing in the payload can adjudicate, so refuse the archive instead.
    """
    from vibeview.qvf import QVFError, _resolve_pbc

    with pytest.raises(QVFError, match="dimensionality == sum"):
        _resolve_pbc({"pbc": [True, True, False], "dimensionality": 3})


def test_non_contiguous_pbc_is_preserved():
    """[T,F,T] is a legal slab periodic in x and z. `dim` counts the axes but
    cannot name them, so it must never be used to reconstruct them."""
    from vibeview.qvf import _resolve_pbc

    pbc, dim = _resolve_pbc({"pbc": [True, False, True]})
    assert pbc == (True, False, True)
    assert dim == 2


def test_non_contiguous_pbc_draws_the_right_axes():
    """Edge drawing gates per axis, so [T,F,T] spans a and c — not a and b.

    Deriving the axes from ``dim=2`` would have picked a and b.
    """
    import numpy as np

    from vibeview.povray_export import _unit_cell_edges

    origin = np.zeros(3)
    lattice = np.diag([1.0, 2.0, 3.0])

    edges = _unit_cell_edges(origin, lattice, (True, False, True))
    assert len(edges) == 4  # a parallelogram, one per periodic-axis pair

    spans = {tuple(np.abs(np.asarray(b) - np.asarray(a)).round(6)) for a, b in edges}
    assert (2.0, 0.0, 0.0) not in spans, "drew the non-periodic b axis"
    assert (1.0, 0.0, 0.0) in spans and (0.0, 0.0, 3.0) in spans


def test_structure_data_exposes_dim():
    sdata = _reader(_slab()).read_structure()
    assert sdata.dim == 2
    assert sdata.pbc == (True, True, False)


# ── Minimum image must not wrap along a non-periodic axis ────────────────


def test_minimum_image_does_not_wrap_along_the_slab_normal():
    """An atom 12 Å above the plane stays there; it must not be pulled to the
    other side of a synthesized 30 Å column."""
    from vibeview.renderers.structure import _minimum_image

    lattice = np.array([GRAPHENE_A1, GRAPHENE_A2, [0.0, 0.0, 30.0]])
    inv = np.linalg.inv(lattice)
    p1 = np.array([0.0, 0.0, 0.0])
    p2 = np.array([0.0, 0.0, 20.0])  # frac_z = 0.667 -> would round-wrap to -10

    slab_img = _minimum_image(p1, p2, lattice, inv, (True, True, False))
    assert slab_img[2] == pytest.approx(20.0)

    bulk_img = _minimum_image(p1, p2, lattice, inv, (True, True, True))
    assert bulk_img[2] == pytest.approx(-10.0)


# ── Volume/orbital tiling obeys the same per-axis rule ───────────────────


def test_volume_tiling_does_not_repeat_into_the_vacuum():
    """A slab's isosurface tiles in-plane only. `replicate_volume_for_periodic`
    counts ADDITIONAL cells per axis, so a non-periodic axis takes 0."""
    from vibeview.qvf import GridData
    from vibeview.renderers.volume import replicate_volume_for_periodic

    shape = (4, 4, 4)
    data = np.arange(np.prod(shape), dtype=float).reshape(shape)
    grid = GridData(
        origin=np.zeros(3),
        voxel_vectors=np.eye(3) * 0.5,
        shape=shape,
    )
    lattice = np.array([GRAPHENE_A1, GRAPHENE_A2, [0.0, 0.0, 30.0]])

    slab_rep = tuple(1 if p else 0 for p in (True, True, False))
    tiled, _ = replicate_volume_for_periodic(data, grid, lattice, replication=slab_rep)
    # (2*1+1) x (2*1+1) x (2*0+1) cells => z depth unchanged.
    assert tiled.shape[2] == shape[2], "isosurface was tiled along the slab normal"
    assert tiled.shape[0] > shape[0] and tiled.shape[1] > shape[1]

    # Control: the old isotropic (n, n, n) call is exactly the bug being guarded.
    isotropic, _ = replicate_volume_for_periodic(data, grid, lattice, replication=(1, 1, 1))
    assert isotropic.shape[2] > shape[2]


# ── Blender export mirrors the same rule ─────────────────────────────────


def test_povray_export_draws_only_the_in_plane_cell():
    """POV-Ray export is a toolbar button; it drew the full 12-edge box."""
    from vibeview.povray_export import _unit_cell_edges

    lattice = np.array([GRAPHENE_A1, GRAPHENE_A2, [0.0, 0.0, 30.0]])
    origin = np.zeros(3)

    assert len(_unit_cell_edges(origin, lattice, (True, True, True))) == 12
    assert len(_unit_cell_edges(origin, lattice, (True, False, False))) == 1

    slab_edges = _unit_cell_edges(origin, lattice, (True, True, False))
    assert len(slab_edges) == 4
    for start, end in slab_edges:
        assert abs(start[2]) < 1e-9 and abs(end[2]) < 1e-9


def test_povray_slab_export_is_independent_of_a3_length(tmp_path):
    from vibeview.povray_export import export_povray

    thin = export_povray(_reader(_slab(a3_len=30.0)), str(tmp_path / "a.pov"))
    thick = export_povray(_reader(_slab(a3_len=80.0)), str(tmp_path / "b.pov"))
    assert thin == thick


def test_qvf_replication_hint_cannot_stack_a_slab_at_load():
    """A `replication: [2,2,2]` viewer hint must not z-stack a slab before the
    user touches any control."""
    from vibeview.app import _clamp_viewer_replication
    from vibeview.viewer_defaults import ViewerState

    vs = ViewerState()
    vs.replication = (2, 2, 2)
    _clamp_viewer_replication(_reader(_slab()), vs)
    assert vs.replication == (2, 2, 1)

    bulk = {
        "atoms": [{"symbol": "Na", "position": [0.0, 0.0, 0.0], "atomic_number": 11}],
        "pbc": [True, True, True],
        "dimensionality": 3,
        "lattice_vectors": [[5.0, 0, 0], [0, 5.0, 0], [0, 0, 5.0]],
    }
    vs3 = ViewerState()
    vs3.replication = (2, 2, 2)
    _clamp_viewer_replication(_reader(bulk), vs3)
    assert vs3.replication == (2, 2, 2)


def test_blender_export_draws_no_cell_edges_along_the_normal(tmp_path):
    from vibeview.blender_export import export_blender_script

    script = export_blender_script(_reader(_slab()), str(tmp_path / "scene.py"))
    # Per-axis flags travel with the (still full 3x3) lattice.
    embedded = script.split("HAS_PBC = ")[1].split("\n")[0].strip().strip("'\"")
    assert json.loads(embedded) == [True, True, False]
    # The generated script must gate on any(pbc), not on a truthy 3-list.
    # (`[False, False, False]` is truthy, so the old scalar gate always fired.)
    assert "if lattice_vectors and any(pbc):" in script
    assert "if has_pbc and lattice_vectors:" not in script
    # The emitted file has to be valid Python — see blender_export's own note.
    compile(script, "generated_blender.py", "exec")
