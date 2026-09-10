"""Explicit bond rendering, including periodic minimum-image (A5-04).

Periodic structures used to render no bonds at all (`if self._bonds and
lattice is None`), so a crystal shipping explicit connectivity showed none.
Explicit `bonds`-section pairs are now drawn for periodic cells too, with the
second endpoint taken at its minimum image so a bond across a cell face
renders short (to the nearest neighbour) rather than as a long line across
the cell. Inferred periodic bonds use the same cached reader path.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _periodic_bonded_reader():
    from vibeview.qvf import QVFReader

    # 5 Å cubic cell; Na at x=0.4, Cl at x=4.6 → direct 4.2 Å, min-image 0.8 Å.
    struct = json.dumps(
        {
            "atoms": [
                {"symbol": "Na", "position": [0.4, 2.5, 2.5], "atomic_number": 11},
                {"symbol": "Cl", "position": [4.6, 2.5, 2.5], "atomic_number": 17},
            ],
            "pbc": [True, True, True],
            "lattice_vectors": [[5.0, 0, 0], [0, 5.0, 0], [0, 0, 5.0]],
        }
    ).encode()
    bonds = json.dumps({"pairs": [{"i": 0, "j": 1}]}).encode()
    sections = [
        {
            "id": "structure",
            "kind": "structure",
            "members": {"structure": {"path": "s.json", "format": "json", "sha256": _sha(struct)}},
        },
        {
            "id": "bonds0",
            "kind": "bonds",
            "members": {"bonds": {"path": "b.json", "format": "json", "sha256": _sha(bonds)}},
        },
    ]
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
        "sections": sections,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", struct)
        zf.writestr("b.json", bonds)
    return QVFReader(buf.getvalue())


def _renderer_for_structure(structure):
    from types import SimpleNamespace

    from vibeview.renderers.structure import StructureRenderer

    class Reader:
        @staticmethod
        def read_structure():
            return structure

        @staticmethod
        def infer_bonds(loaded_structure):
            assert loaded_structure is structure
            return structure.bonds

    return StructureRenderer(
        SimpleNamespace(id="structure", kind="structure"), Reader()
    )


def test_periodic_explicit_bond_is_drawn_at_minimum_image():
    import pyvista as pv

    from vibeview.renderers.structure import StructureRenderer

    reader = _periodic_bonded_reader()
    sr = StructureRenderer(reader.get_section("structure"), reader)
    assert sr.load().bonds == [(0, 1, 1.0, (0, 0, 0))]  # explicit bond parsed (i, j, order)
    plotter = pv.Plotter(off_screen=True)
    sr.add_to_plotter(plotter)

    bond_actors = [k for k in plotter.actors if str(k).startswith("bond_")]
    assert bond_actors, "periodic explicit bond was not drawn (A5-04 regression)"
    # x-extent ~0.8 Å (min-image), NOT ~4.2 Å (cross-cell).
    bounds = plotter.actors[bond_actors[0]].GetMapper().GetInput().GetBounds()
    assert (bounds[1] - bounds[0]) < 2.0


def test_double_bond_renders_two_cylinders():
    """A6-02: the producer's bond `order` was discarded so double/aromatic
    bonds rendered as single. A double bond must now draw two parallel
    cylinders (named bond_i_j_0 / bond_i_j_1)."""
    import pyvista as pv

    from vibeview.qvf import QVFReader
    from vibeview.renderers.structure import StructureRenderer

    struct = json.dumps(
        {
            "atoms": [
                {"symbol": "C", "position": [0.0, 0.0, 0.0], "atomic_number": 6},
                {"symbol": "O", "position": [1.2, 0.0, 0.0], "atomic_number": 8},
            ],
            "pbc": [False, False, False],
        }
    ).encode()
    bonds = json.dumps({"pairs": [{"i": 0, "j": 1, "order": 2.0}]}).encode()
    sections = [
        {
            "id": "structure",
            "kind": "structure",
            "members": {"structure": {"path": "s.json", "format": "json", "sha256": _sha(struct)}},
        },
        {
            "id": "bonds0",
            "kind": "bonds",
            "members": {"bonds": {"path": "b.json", "format": "json", "sha256": _sha(bonds)}},
        },
    ]
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
        "sections": sections,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", struct)
        zf.writestr("b.json", bonds)
    reader = QVFReader(buf.getvalue())
    sr = StructureRenderer(reader.get_section("structure"), reader)
    assert sr.load().bonds == [(0, 1, 2.0, (0, 0, 0))]  # order threaded through
    plotter = pv.Plotter(off_screen=True)
    sr.add_to_plotter(plotter)
    bond_actors = sorted(k for k in plotter.actors if str(k).startswith("bond_"))
    assert bond_actors == ["bond_0_1_0", "bond_0_1_1"]  # two parallel cylinders


def test_outer_batch_preserves_double_bond_multiplicity_and_colour(monkeypatch):
    """Two bonds over a patched threshold reproduce the 51-over-50 path."""
    import numpy as np
    import pyvista as pv

    from vibeview.qvf import Atom, StructureData
    from vibeview.renderers import structure as structure_renderer

    monkeypatch.setattr(structure_renderer, "_BATCH_BOND_THRESHOLD", 1)
    atoms = [
        Atom("C", np.array([0.0, 0.0, 0.0]), 6),
        Atom("O", np.array([1.2, 0.0, 0.0]), 8),
        Atom("C", np.array([0.0, 3.0, 0.0]), 6),
        Atom("O", np.array([1.2, 3.0, 0.0]), 8),
    ]

    # Equality stays on the established small-system path: the production
    # boundary is 50 per-bond actors versus batching at 51.
    small_structure = StructureData(
        atoms=atoms[:2],
        pbc=(False, False, False),
        lattice_vectors=None,
        bonds=[(0, 1, 2.0, (0, 0, 0))],
    )
    small_plotter = pv.Plotter(off_screen=True)
    try:
        _renderer_for_structure(small_structure).add_to_plotter(
            small_plotter, representation="sticks_only"
        )
        assert {name for name in small_plotter.actors if name.startswith("bond")} == {
            "bond_0_1_0",
            "bond_0_1_1",
        }
    finally:
        small_plotter.close()

    structure = StructureData(
        atoms=atoms,
        pbc=(False, False, False),
        lattice_vectors=None,
        bonds=[
            (0, 1, 2.0, (0, 0, 0)),
            (2, 3, 2.0, (0, 0, 0)),
        ],
    )
    plotter = pv.Plotter(off_screen=True)
    try:
        _renderer_for_structure(structure).add_to_plotter(
            plotter, representation="sticks_only"
        )

        bond_actors = {
            name: actor
            for name, actor in plotter.actors.items()
            if name.startswith("bond")
        }
        assert set(bond_actors) == {"bonds_batched_0"}
        actor = bond_actors["bonds_batched_0"]
        mapper = actor.GetMapper()
        mapper.Update()
        mesh = pv.wrap(mapper.GetInput())

        # Two logical double bonds remain four disconnected cylinders in one
        # actor, not two single sticks. A homogeneous batch keeps its visible
        # double-bond blue on the actor property, like the small path.
        connected = mesh.connectivity()
        assert len(np.unique(connected.cell_data["RegionId"])) == 4
        assert not mapper.GetScalarVisibility()
        assert "bond_rgb" not in mesh.cell_data
        np.testing.assert_allclose(actor.GetProperty().GetColor(), (0.2, 0.4, 0.8))
    finally:
        plotter.close()


def test_outer_batch_preserves_mixed_order_colours(monkeypatch):
    """A merged actor retains every order colour and material override."""
    import numpy as np
    import pyvista as pv

    from vibeview.material_presets import apply_material_to_actor, get_preset
    from vibeview.qvf import Atom, StructureData
    from vibeview.renderers import structure as structure_renderer

    monkeypatch.setattr(structure_renderer, "_BATCH_BOND_THRESHOLD", 1)
    atoms = []
    bonds = []
    for bond_idx, order in enumerate((1.0, 2.0, 3.0)):
        y = float(bond_idx * 3)
        atom_idx = len(atoms)
        atoms.extend(
            [
                Atom("C", np.array([0.0, y, 0.0]), 6),
                Atom("C", np.array([1.2, y, 0.0]), 6),
            ]
        )
        bonds.append((atom_idx, atom_idx + 1, order, (0, 0, 0)))
    structure = StructureData(
        atoms=atoms,
        pbc=(False, False, False),
        lattice_vectors=None,
        bonds=bonds,
    )

    plotter = pv.Plotter(off_screen=True)
    try:
        _renderer_for_structure(structure).add_to_plotter(
            plotter, representation="sticks_only"
        )
        actor = plotter.actors["bonds_batched_0"]
        mapper = actor.GetMapper()
        mapper.Update()
        mesh = pv.wrap(mapper.GetInput())

        assert len(np.unique(mesh.connectivity().cell_data["RegionId"])) == 6
        assert mapper.GetScalarVisibility()
        assert mapper.GetArrayName() == "bond_rgb"
        assert mapper.GetScalarModeAsString() == "UseCellData"
        assert np.unique(mesh.cell_data["bond_rgb"], axis=0).tolist() == [
            [51, 102, 204],
            [136, 136, 136],
            [204, 51, 51],
        ]

        # Material presets deliberately make bonds uniform. They must still
        # override a mixed-order scalar map as they do per-bond actor colours.
        apply_material_to_actor(actor, get_preset("metallic"), is_bond=True)
        assert not mapper.GetScalarVisibility()
        np.testing.assert_allclose(
            actor.GetProperty().GetColor(), (0.7, 0.7, 0.75)
        )
    finally:
        plotter.close()


def test_outer_batch_owns_actor_names_across_replicas(monkeypatch):
    """One bond over two cells reproduces the 51-replica overwrite path."""
    import numpy as np
    import pyvista as pv

    from vibeview.qvf import Atom, StructureData
    from vibeview.renderers import structure as structure_renderer

    monkeypatch.setattr(structure_renderer, "_BATCH_BOND_THRESHOLD", 1)
    structure = StructureData(
        atoms=[
            Atom("C", np.array([0.0, 0.0, 0.0]), 6),
            Atom("C", np.array([1.2, 0.0, 0.0]), 6),
        ],
        pbc=(True, False, False),
        lattice_vectors=np.diag([3.0, 10.0, 10.0]),
        bonds=[(0, 1, 1.0, (0, 0, 0))],
        dim=1,
    )
    plotter = pv.Plotter(off_screen=True)
    try:
        _renderer_for_structure(structure).add_to_plotter(
            plotter,
            representation="sticks_only",
            replication=(2, 1, 1),
        )

        bond_actors = {
            name: actor
            for name, actor in plotter.actors.items()
            if name.startswith("bond")
        }
        assert set(bond_actors) == {"bonds_batched_0", "bonds_batched_1"}
        x_centers = []
        for cell_idx in range(2):
            mapper = bond_actors[f"bonds_batched_{cell_idx}"].GetMapper()
            mapper.Update()
            mesh = pv.wrap(mapper.GetInput())
            x_centers.append((mesh.bounds[0] + mesh.bounds[1]) / 2.0)
            assert not mapper.GetScalarVisibility()
            assert "bond_rgb" not in mesh.cell_data
            np.testing.assert_allclose(
                bond_actors[f"bonds_batched_{cell_idx}"].GetProperty().GetColor(),
                (136 / 255, 136 / 255, 136 / 255),
            )
        np.testing.assert_allclose(x_centers, (0.6, 3.6))
    finally:
        plotter.close()


def test_periodic_inferred_bonds_now_work(monkeypatch):
    """Periodic covalent-radius bond inference uses minimum-image distances.

    Na at [0,0,0] and Cl at [2.8,0,0] in a 5.6 Å cubic cell —
    the minimum-image distance is 2.8 Å, which is within the
    covalent-radius cutoff (Na 1.54 + Cl 1.02 + 0.45 = 3.01 Å).
    """
    import pyvista as pv

    from vibeview.qvf import QVFReader
    from vibeview.renderers.structure import StructureRenderer

    struct = json.dumps(
        {
            "atoms": [
                {"symbol": "Na", "position": [0.0, 0.0, 0.0], "atomic_number": 11},
                {"symbol": "Cl", "position": [2.8, 0.0, 0.0], "atomic_number": 17},
            ],
            "pbc": [True, True, True],
            "lattice_vectors": [[5.6, 0, 0], [0, 5.6, 0], [0, 0, 5.6]],
        }
    ).encode()
    sections = [
        {
            "id": "structure",
            "kind": "structure",
            "members": {"structure": {"path": "s.json", "format": "json", "sha256": _sha(struct)}},
        }
    ]
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
        "sections": sections,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", struct)
    reader = QVFReader(buf.getvalue())
    inference_calls = []
    infer_bonds = reader.infer_bonds

    def counted_inference(structure):
        inference_calls.append(structure)
        return infer_bonds(structure)

    monkeypatch.setattr(reader, "infer_bonds", counted_inference)
    sr = StructureRenderer(reader.get_section("structure"), reader)
    assert sr.load().bonds is None  # no explicit bonds → inferred
    plotter = pv.Plotter(off_screen=True)
    sr.add_to_plotter(plotter)
    sr.add_to_plotter(plotter)
    bond_actors = [k for k in plotter.actors if str(k).startswith("bond_")]
    assert bond_actors  # periodic inference should find the Na–Cl bond
    assert len(inference_calls) == 1


def test_periodic_crystal_draws_every_bond_at_the_true_distance(tmp_path):
    """Rocksalt has six neighbours per ion, each 2.82 A away in a 5.64 A cell.

    Both halves of that used to be wrong. Bonds through cell walls were not
    found at all, and once they were, a pair could hold only one image, so the
    opposite-direction neighbour -- the same atom reached the other way round
    -- collapsed onto the first and the coordination read 3.
    """
    import hashlib
    import json
    import zipfile

    import numpy as np

    from vibeview.qvf import QVFReader, _infer_bonds_by_radii

    a = 5.64
    na = [(0, 0, 0), (0, 0.5, 0.5), (0.5, 0, 0.5), (0.5, 0.5, 0)]
    cl = [(0.5, 0.5, 0.5), (0.5, 0, 0), (0, 0.5, 0), (0, 0, 0.5)]
    atoms = [
        {"symbol": "Na", "position": (np.array(f) * a).tolist(), "atomic_number": 11}
        for f in na
    ] + [
        {"symbol": "Cl", "position": (np.array(f) * a).tolist(), "atomic_number": 17}
        for f in cl
    ]
    struct = json.dumps(
        {
            "atoms": atoms,
            "pbc": [True, True, True],
            "dim": 3,
            "lattice_vectors": (np.eye(3) * a).tolist(),
        }
    ).encode()
    manifest = {
        "qvf_version": 1,
        "source": {"program": "t", "version": "0", "calculation": "x"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "s.json",
                        "format": "json",
                        "sha256": hashlib.sha256(struct).hexdigest(),
                    }
                },
            }
        ],
    }
    path = tmp_path / "nacl.qvf"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("s.json", struct)

    structure = QVFReader(path).read_structure()
    bonds = _infer_bonds_by_radii(structure)

    degree = {i: 0 for i in range(8)}
    for i, j, _order, _image in bonds:
        degree[i] += 1
        degree[j] += 1
    assert set(degree.values()) == {6}, degree

    # Every bond must be a real nearest-neighbour contact, not a stick
    # stretched across the box.
    lattice = np.asarray(structure.lattice_vectors)
    for i, j, _order, image in bonds:
        p1 = np.asarray(structure.atoms[i].position)
        p2 = np.asarray(structure.atoms[j].position) + np.asarray(image) @ lattice
        assert abs(float(np.linalg.norm(p2 - p1)) - 2.82) < 1e-3
