"""vibeview.bonds is the one bond rule every surface uses."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest

from vibeview import bonds
from vibeview.qvf import QVFReader

from .conftest import build_qvf

H2 = ([[0.0, 0.0, 0.0], [0.74, 0.0, 0.0]], [1, 1])


def _structure_bytes(atoms, pbc=(False, False, False), lattice=None) -> bytes:
    return json.dumps(
        {
            "atoms": [{"symbol": s, "atomic_number": z, "position": p} for s, z, p in atoms],
            "pbc": list(pbc),
            "lattice_vectors": lattice,
        }
    ).encode()


def _write(tmp_path: Path, name: str, structure: bytes, explicit: bytes | None = None) -> Path:
    members = {"structure": ("json", "structure.json", structure)}
    if explicit is not None:
        members["bonds"] = ("json", "bonds.json", explicit)
    return build_qvf(tmp_path / name, [("structure", "structure", members)])


ETHANOL = [
    ("C", 6, [-0.888, 0.166, 0.0]),
    ("C", 6, [0.478, -0.507, 0.0]),
    ("O", 8, [1.409, 0.583, 0.0]),
    ("H", 1, [-1.639, -0.627, 0.0]),
    ("H", 1, [-1.030, 0.786, 0.890]),
    ("H", 1, [-1.030, 0.786, -0.890]),
    ("H", 1, [0.620, -1.127, 0.890]),
    ("H", 1, [0.620, -1.127, -0.890]),
    ("H", 1, [2.283, 0.169, 0.0]),
]


class TestInferBonds:
    def test_h2_is_bonded_and_far_atoms_are_not(self) -> None:
        positions, numbers = H2
        assert bonds.infer_bonds(positions, numbers) == [(0, 1, 1.0, (0, 0, 0))]
        assert bonds.infer_bonds([[0, 0, 0], [10, 0, 0]], numbers) == []

    def test_accepts_lists_and_arrays_alike(self) -> None:
        positions, numbers = H2
        assert bonds.infer_bonds(np.asarray(positions), np.asarray(numbers)) == bonds.infer_bonds(
            positions, numbers
        )

    def test_fewer_than_two_atoms(self) -> None:
        assert bonds.infer_bonds([], []) == []
        assert bonds.infer_bonds([[0, 0, 0]], [6]) == []

    def test_periodic_bond_carries_its_image(self) -> None:
        """Two chlorines 1.0 Å apart across a 10 Å cell face are bonded."""
        lattice = np.eye(3) * 10.0
        found = bonds.infer_bonds(
            [[0.5, 5, 5], [9.5, 5, 5]], [17, 17], lattice=lattice, pbc=(True, True, True)
        )
        # Atom 1 reaches atom 0 through the cell to its left: image -1.
        assert found == [(0, 1, 1.0, (-1, 0, 0))]
        assert bonds.infer_bonds([[0.5, 5, 5], [9.5, 5, 5]], [17, 17]) == []

    def test_ionic_solid_keeps_no_metal_metal_bonds(self) -> None:
        """Rocksalt NaCl: six Cl neighbours per Na, no Na-Na sticks."""
        a = 5.64
        lattice = np.eye(3) * a
        atoms = []
        numbers = []
        for base, z in (((0, 0, 0), 11), ((0.5, 0, 0), 17)):
            for shift in ((0, 0, 0), (0.5, 0.5, 0), (0.5, 0, 0.5), (0, 0.5, 0.5)):
                atoms.append([(b + s) % 1.0 * a for b, s in zip(base, shift, strict=True)])
                numbers.append(z)
        found = bonds.infer_bonds(atoms, numbers, lattice=lattice, pbc=(True, True, True))
        assert all({numbers[i], numbers[j]} == {11, 17} for i, j, _o, _im in found)
        counts = bonds.neighbour_counts(len(numbers), found)
        assert counts == [6] * len(numbers)

    def test_pure_metal_keeps_its_contacts(self) -> None:
        assert bonds.metal_metal_pairs_are_bonds([29, 29])
        assert not bonds.metal_metal_pairs_are_bonds([11, 17])
        assert not bonds.metal_metal_pairs_are_bonds([])
        assert bonds.infer_bonds([[0, 0, 0], [2.55, 0, 0]], [29, 29]) == [(0, 1, 1.0, (0, 0, 0))]

    def test_unknown_element_uses_one_fallback_radius(self) -> None:
        assert bonds.covalent_radii([1, 6, 200]).tolist() == [
            bonds.COVALENT_RADII[1],
            bonds.COVALENT_RADII[6],
            bonds.UNKNOWN_RADIUS,
        ]


class TestStructureBonds:
    def test_explicit_bonds_win_with_their_orders(self, tmp_path: Path) -> None:
        explicit = json.dumps({"pairs": [{"i": 0, "j": 1, "order": 2.0}]}).encode()
        path = _write(tmp_path, "explicit.qvf", _structure_bytes(ETHANOL), explicit)
        with QVFReader(path) as reader:
            sdata = reader.read_structure()
        assert bonds.structure_bonds(sdata) == [(0, 1, 2.0, (0, 0, 0))]

    def test_inferred_when_the_archive_has_none(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "inferred.qvf", _structure_bytes(ETHANOL))
        with QVFReader(path) as reader:
            sdata = reader.read_structure()
        found = bonds.structure_bonds(sdata)
        assert len(found) == 8
        assert found == bonds.infer_bonds(
            [a.position for a in sdata.atoms], [a.atomic_number for a in sdata.atoms]
        )

    def test_duck_typed_structure_without_optional_fields(self) -> None:
        class Atom:
            def __init__(self, z, pos):
                self.atomic_number = z
                self.position = np.asarray(pos, dtype=float)

        class Struct:
            def __init__(self, atoms):
                self.atoms = atoms

        positions, numbers = H2
        struct = Struct([Atom(z, p) for z, p in zip(numbers, positions, strict=True)])
        assert bonds.structure_bonds(struct) == [(0, 1, 1.0, (0, 0, 0))]
        assert bonds.structure_bonds(Struct([])) == []


class TestNormalizeBond:
    def test_pair_triple_and_full_forms(self) -> None:
        assert bonds.normalize_bond((3, 1)) == (3, 1, 1.0, (0, 0, 0))
        assert bonds.normalize_bond([0, 2, 2]) == (0, 2, 2.0, (0, 0, 0))
        assert bonds.normalize_bond((0, 2, 1.5, [1, 0, -1])) == (0, 2, 1.5, (1, 0, -1))
        assert bonds.normalize_bond((0, 2, 1.0, None)) == (0, 2, 1.0, (0, 0, 0))


class TestGeometryHelpers:
    def test_bond_endpoints_follow_the_image(self) -> None:
        positions = np.array([[0.5, 5, 5], [9.5, 5, 5]])
        lattice = np.eye(3) * 10.0
        start, end = bonds.bond_endpoints(positions, (0, 1, 1.0, (1, 0, 0)), lattice)
        assert start.tolist() == [0.5, 5, 5]
        assert end.tolist() == [19.5, 5, 5]
        _start, end = bonds.bond_endpoints(positions, (0, 1, 1.0, (1, 0, 0)))
        assert end.tolist() == [9.5, 5, 5]

    def test_bond_lengths_and_in_cell(self) -> None:
        positions = np.array([[0.5, 5, 5], [9.5, 5, 5]])
        lattice = np.eye(3) * 10.0
        found = [(0, 1, 1.0, (1, 0, 0)), (0, 1, 1.0, (0, 0, 0))]
        assert bonds.bond_lengths(positions, found, lattice) == pytest.approx([19.0, 9.0])
        assert bonds.in_cell(found) == [(0, 1, 1.0, (0, 0, 0))]

    def test_neighbour_counts(self) -> None:
        assert bonds.neighbour_counts(3, [(0, 1, 1.0, (0, 0, 0)), (1, 2, 1.0, (0, 0, 0))]) == [
            1,
            2,
            1,
        ]


class TestEverySurfaceAgrees:
    """The exports carry exactly the bonds the viewer draws."""

    @pytest.fixture
    def ethanol(self, tmp_path: Path) -> Path:
        return _write(tmp_path, "ethanol.qvf", _structure_bytes(ETHANOL))

    def test_cml(self, ethanol: Path, tmp_path: Path) -> None:
        from vibeview.export_cml import export_cml

        with QVFReader(ethanol) as reader:
            expected = bonds.structure_bonds(reader.read_structure())
            export_cml(reader, str(tmp_path / "ethanol.cml"))
        text = (tmp_path / "ethanol.cml").read_text()
        refs = re.findall(r'atomRefs2="a(\d+) a(\d+)"', text)
        assert [(int(i) - 1, int(j) - 1) for i, j in refs] == [(i, j) for i, j, _o, _im in expected]
        assert 'order="1"' in text

    def test_cml_orders_come_from_explicit_bonds(self, tmp_path: Path) -> None:
        from vibeview.export_cml import export_cml

        explicit = json.dumps(
            {"pairs": [{"i": 0, "j": 1, "order": 2.0}, {"i": 1, "j": 2, "order": 1.5}]}
        ).encode()
        path = _write(tmp_path, "orders.qvf", _structure_bytes(ETHANOL), explicit)
        with QVFReader(path) as reader:
            export_cml(reader, str(tmp_path / "orders.cml"))
        text = (tmp_path / "orders.cml").read_text()
        assert 'order="2"' in text
        assert 'order="A"' in text

    def test_svg_and_html(self, ethanol: Path, tmp_path: Path) -> None:
        from vibeview.export_html import export_html
        from vibeview.export_svg import export_svg

        with QVFReader(ethanol) as reader:
            expected = bonds.structure_bonds(reader.read_structure())
            export_svg(reader, str(tmp_path / "ethanol.svg"))
            export_html(reader, tmp_path / "ethanol.html")
        svg = (tmp_path / "ethanol.svg").read_text()
        html = (tmp_path / "ethanol.html").read_text()
        assert svg.count("<line ") == len(expected)
        data = json.loads(re.search(r"const data = (\{.*?\});\n", html).group(1))
        assert data["bonds"] == [[i, j] for i, j, _o, _im in expected]

    def test_povray_draws_periodic_bonds_to_their_image(self, tmp_path: Path) -> None:
        from vibeview.povray_export import export_povray

        lattice = (np.eye(3) * 10.0).tolist()
        atoms = [("Cl", 17, [0.5, 5.0, 5.0]), ("Cl", 17, [9.5, 5.0, 5.0])]
        path = _write(tmp_path, "cl2.qvf", _structure_bytes(atoms, (True, True, True), lattice))
        with QVFReader(path) as reader:
            export_povray(reader, str(tmp_path / "cl2.pov"))
        text = (tmp_path / "cl2.pov").read_text()
        bond_block = text.split("// ── Bonds ──")[1].split("// ── Unit cell ──")[0]
        assert bond_block.count("cylinder") == 1
        vectors = re.findall(r"<\s*(-?[\d.]+),\s*(-?[\d.]+),\s*(-?[\d.]+)\s*>", bond_block)
        start, end = (np.array([float(v) for v in vec]) for vec in vectors[:2])
        # Drawn to the image across the face: 1.0 Å long, not 9.0 Å in-cell.
        assert float(np.linalg.norm(end - start)) == pytest.approx(1.0, abs=1e-3)

    def test_no_module_keeps_a_private_detector(self) -> None:
        from vibeview import blender_export, build_tools, export_cml, export_svg, povray_export

        for module in (blender_export, povray_export, export_cml, export_svg, build_tools):
            assert not hasattr(module, "_detect_bonds")


def test_large_binned_search_matches_independent_all_pairs():
    """The vectorised neighbourhoods must neither omit nor duplicate pairs."""
    from vibeview.bonds import BOND_TOLERANCE, NON_METALS, covalent_radii, infer_bonds

    rng = np.random.default_rng(731)
    positions = rng.uniform(-20, 20, (900, 3))
    numbers = rng.choice([1, 6, 8, 29], len(positions))
    radii = covalent_radii(numbers)
    expected = set()
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            if numbers[i] not in NON_METALS and numbers[j] not in NON_METALS:
                continue
            if np.linalg.norm(positions[i] - positions[j]) < radii[i] + radii[j] + BOND_TOLERANCE:
                expected.add((i, j))
    found = infer_bonds(positions, numbers)
    assert len(found) == len(expected)
    assert {(i, j) for i, j, _order, _image in found} == expected
