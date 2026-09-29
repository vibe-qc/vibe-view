"""Miller-plane slab cutting.

Two things a slab builder must get right, and both went wrong once here. The
interplanar spacing needs the reciprocal lattice: ``1/|(h,k,l)|`` is
dimensionless and reported 1.0 A for the (001) plane of a cubic 5 A cell
(audit 2026-07-02, deferred item 2). And the surface cell needs two lattice
vectors that lie in the plane: keeping a and b and stacking vacuum along c is
a slab only when the normal is along c, so until the general surface cell
landed every other orientation was refused (roadmap, "Not yet placed").
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from vibeview.crystal_builder import cell_from_abc, miller_slab, surface_cell_basis

CUBIC = cell_from_abc(5.0, 5.0, 5.0, 90, 90, 90)
ORTHO = cell_from_abc(3.0, 4.0, 7.0, 90, 90, 90)
HEX = cell_from_abc(3.21, 3.21, 5.21, 90, 90, 120)
TRICLINIC = cell_from_abc(4.0, 5.0, 6.0, 80, 95, 110)
CELLS = {"cubic": CUBIC, "ortho": ORTHO, "hex": HEX, "triclinic": TRICLINIC}

# fcc aluminium, conventional cell: four atoms, a = 4.05 A.
FCC_A = 4.05
FCC = cell_from_abc(FCC_A, FCC_A, FCC_A, 90, 90, 90)
FCC_ATOMS = [
    {"symbol": "Al", "position": (np.array(f) * FCC_A).tolist()}
    for f in [(0, 0, 0), (0.5, 0.5, 0), (0.5, 0, 0.5), (0, 0.5, 0.5)]
]


def _atoms(*z):
    return [{"symbol": "A", "position": [0.0, 0.0, float(v)]} for v in z]


def _d(cell, hkl):
    """Reference spacing, straight from the definition."""
    return 1.0 / np.linalg.norm(np.linalg.inv(cell) @ np.asarray(hkl, float))


def _normal(cell, hkl):
    g = np.linalg.inv(cell) @ np.asarray(hkl, float)
    return g / np.linalg.norm(g)


def _heights(cell, atoms, hkl):
    """Every atom's distance along the (hkl) normal, sorted."""
    return np.sort(np.asarray([a["position"] for a in atoms]) @ _normal(cell, hkl))


class TestSpacingIsALength:
    @pytest.mark.parametrize(
        ("cell", "hkl", "expected"),
        [
            (CUBIC, (0, 0, 1), 5.0),
            (CUBIC, (1, 1, 1), 5.0 / np.sqrt(3)),
            (CUBIC, (1, 1, 0), 5.0 / np.sqrt(2)),
            (ORTHO, (0, 0, 1), 7.0),
            (ORTHO, (1, 0, 0), 3.0),
            (ORTHO, (0, 1, 0), 4.0),
        ],
    )
    def test_reference_spacing(self, cell, hkl, expected):
        """Pins the textbook values the implementation must reproduce."""
        assert _d(cell, hkl) == pytest.approx(expected, rel=1e-9)

    def test_slab_thickness_uses_the_real_spacing(self):
        """c = n_layers * d + 2 * vacuum, in angstroms.

        Under the old dimensionless spacing this came out 2*1 + 2*10 = 22 A
        for a cell whose two (001) layers are 10 A thick.
        """
        cell, _ = miller_slab(CUBIC, _atoms(0.0, 2.5), (0, 0, 1), n_layers=2, vacuum=10.0)
        assert np.linalg.norm(cell[2]) == pytest.approx(2 * 5.0 + 2 * 10.0)

    def test_thickness_tracks_the_cell(self):
        cell, _ = miller_slab(ORTHO, _atoms(0.0), (0, 0, 1), n_layers=3, vacuum=5.0)
        assert np.linalg.norm(cell[2]) == pytest.approx(3 * 7.0 + 2 * 5.0)

    def test_more_layers_is_a_thicker_slab(self):
        thin, _ = miller_slab(CUBIC, _atoms(0.0), (0, 0, 1), n_layers=1, vacuum=1.0)
        thick, _ = miller_slab(CUBIC, _atoms(0.0), (0, 0, 1), n_layers=4, vacuum=1.0)
        assert np.linalg.norm(thick[2]) - np.linalg.norm(thin[2]) == pytest.approx(3 * 5.0)

    def test_vacuum_extends_the_cell_without_centering_atoms(self):
        a, atoms_a = miller_slab(CUBIC, _atoms(0.0, 2.5), (0, 0, 1), n_layers=3, vacuum=0.0)
        b, atoms_b = miller_slab(CUBIC, _atoms(0.0, 2.5), (0, 0, 1), n_layers=3, vacuum=4.0)
        assert np.linalg.norm(b[2]) - np.linalg.norm(a[2]) == pytest.approx(8.0)
        assert np.allclose([atom["position"] for atom in atoms_a],
                           [atom["position"] for atom in atoms_b])
        heights = _heights(CUBIC, atoms_b, (0, 0, 1))
        assert heights.min() == pytest.approx(0.0)
        assert np.linalg.norm(b[2]) - heights.max() == pytest.approx(10.5)

    @pytest.mark.parametrize("hkl", [(1, 1, 1), (1, 1, 0), (2, 1, 0), (-1, 1, 2)])
    def test_thickness_for_any_orientation(self, hkl):
        cell, _ = miller_slab(FCC, FCC_ATOMS, hkl, n_layers=3, vacuum=8.0)
        assert np.linalg.norm(cell[2]) == pytest.approx(3 * _d(FCC, hkl) + 16.0)


class TestLayersRepeatTheCell:
    """``n_layers`` fills the slab by repeating the parent cell along the
    normal, the way ASE's ``surface()`` and pymatgen's ``SlabGenerator`` count
    layers. The previous builder selected planes from the atoms it was given
    and, handed one cell with ``n_layers=3``, returned one filled layer under
    two empty ones while claiming the full thickness."""

    def test_each_layer_holds_one_copy_of_the_cell(self):
        atoms = _atoms(0.0, 2.5)  # two (001) planes per 5 A cell
        _, kept = miller_slab(CUBIC, atoms, (0, 0, 1), n_layers=2, vacuum=1.0)
        assert len(kept) == 4
        assert _heights(CUBIC, kept, (0, 0, 1)).tolist() == pytest.approx([0.0, 2.5, 5.0, 7.5])

    def test_one_layer_is_the_cell_itself(self):
        atoms = _atoms(0.0, 1.0, 4.0)
        _, kept = miller_slab(CUBIC, atoms, (0, 0, 1), n_layers=1, vacuum=1.0)
        assert np.allclose([a["position"] for a in kept], [a["position"] for a in atoms])

    def test_an_atom_outside_the_cell_is_its_image_inside(self):
        """The atom list is one cell's content; z = 5.0 in a 5 A cell is the
        z = 0 site, so it lands there rather than starting a fourth plane."""
        _, kept = miller_slab(CUBIC, _atoms(5.0), (0, 0, 1), n_layers=1, vacuum=1.0)
        assert kept[0]["position"] == pytest.approx([0.0, 0.0, 0.0])

    def test_atoms_keep_their_symbols_and_extra_keys(self):
        atoms = [
            {"symbol": "Na", "position": [0.0, 0.0, 0.0], "label": "cation"},
            {"symbol": "Cl", "position": [0.0, 0.0, 2.5]},
        ]
        _, kept = miller_slab(CUBIC, atoms, (0, 0, 1), n_layers=2, vacuum=5.0)
        assert [a["symbol"] for a in kept] == ["Na", "Cl", "Na", "Cl"]
        assert kept[0]["label"] == "cation"

    def test_fcc_111_is_one_plane_per_layer(self):
        """The conventional fcc cell holds one (111) plane per d_111, so a
        three-layer slab is three equally spaced planes of four atoms."""
        _, kept = miller_slab(FCC, FCC_ATOMS, (1, 1, 1), n_layers=3, vacuum=8.0)
        heights = _heights(FCC, kept, (1, 1, 1))
        planes = np.unique(np.round(heights, 6))
        assert len(kept) == 12
        assert planes.tolist() == pytest.approx([0.0, _d(FCC, (1, 1, 1)), 2 * _d(FCC, (1, 1, 1))])

    def test_fcc_001_is_two_planes_per_layer(self):
        _, kept = miller_slab(FCC, FCC_ATOMS, (0, 0, 1), n_layers=2, vacuum=8.0)
        planes = np.unique(np.round(_heights(FCC, kept, (0, 0, 1)), 6))
        assert planes.tolist() == pytest.approx([0.0, 2.025, 4.05, 6.075])

    def test_atoms_lie_inside_the_slab(self):
        for hkl in [(1, 1, 1), (2, 1, 0), (-1, 1, 2), (0, 1, 1)]:
            _, kept = miller_slab(FCC, FCC_ATOMS, hkl, n_layers=3, vacuum=8.0)
            heights = _heights(FCC, kept, hkl)
            assert heights.min() >= -1e-9
            assert heights.max() < 3 * _d(FCC, hkl) - 1e-9

    def test_zero_layers_is_refused(self):
        with pytest.raises(ValueError, match="at least one layer"):
            miller_slab(CUBIC, _atoms(0.0), (0, 0, 1), n_layers=0, vacuum=1.0)


class TestSurfaceCellBasis:
    """The integer basis behind every orientation: two rows in the plane, the
    third stepping one plane along the normal, determinant one."""

    ORIENTATIONS = [
        (0, 0, 1), (1, 0, 0), (0, 1, 0), (1, 1, 1), (1, 1, 0), (0, 1, 1),
        (2, 1, 0), (3, 1, 1), (1, -1, 0), (-1, 1, 2), (2, -1, 3), (-2, 0, 1),
        (0, 0, -1), (-1, -1, -1),
    ]

    @pytest.mark.parametrize("name", list(CELLS))
    @pytest.mark.parametrize("hkl", ORIENTATIONS)
    def test_in_plane_rows_are_perpendicular_to_the_normal(self, name, hkl):
        cell = CELLS[name]
        rows = surface_cell_basis(cell, hkl).astype(float) @ cell
        n = _normal(cell, hkl)
        assert abs(rows[0] @ n) < 1e-9
        assert abs(rows[1] @ n) < 1e-9

    @pytest.mark.parametrize("name", list(CELLS))
    @pytest.mark.parametrize("hkl", ORIENTATIONS)
    def test_third_row_steps_one_plane_towards_the_surface(self, name, hkl):
        cell = CELLS[name]
        rows = surface_cell_basis(cell, hkl).astype(float) @ cell
        assert rows[2] @ _normal(cell, hkl) == pytest.approx(_d(cell, hkl))

    @pytest.mark.parametrize("name", list(CELLS))
    @pytest.mark.parametrize("hkl", ORIENTATIONS)
    def test_basis_preserves_right_handed_input(self, name, hkl):
        basis = surface_cell_basis(CELLS[name], hkl)
        assert basis.dtype.kind == "i"
        assert round(float(np.linalg.det(basis))) == 1
        rows = basis.astype(float) @ CELLS[name]
        assert np.cross(rows[0], rows[1]) @ rows[2] > 0

    @pytest.mark.parametrize("hkl", ORIENTATIONS)
    def test_basis_preserves_left_handed_input(self, hkl):
        left_handed = TRICLINIC.copy()
        left_handed[0] *= -1
        basis = surface_cell_basis(left_handed, hkl)
        rows = basis.astype(float) @ left_handed
        assert round(float(np.linalg.det(basis))) == 1
        assert np.linalg.det(rows) == pytest.approx(np.linalg.det(left_handed))
        assert np.cross(rows[0], rows[1]) @ rows[2] < 0

    @pytest.mark.parametrize(("hkl", "reduced"), [((0, 0, 2), (0, 0, 1)), ((2, 2, 0), (1, 1, 0)),
                                                  ((3, 3, 3), (1, 1, 1)), ((4, 2, 0), (2, 1, 0))])
    def test_a_common_factor_names_the_same_plane_family(self, hkl, reduced):
        """(002) is the (001) family; the surface cell and the layer step are
        those of the reduced indices, so the step is gcd * d_hkl."""
        assert (surface_cell_basis(FCC, hkl) == surface_cell_basis(FCC, reduced)).all()
        cell, kept = miller_slab(FCC, FCC_ATOMS, hkl, n_layers=2, vacuum=0.0)
        g = math.gcd(*hkl)
        assert np.linalg.norm(cell[2]) == pytest.approx(2 * g * _d(FCC, hkl))
        assert len(kept) == 8

    def test_surface_cell_keeps_the_parent_volume(self):
        for hkl in self.ORIENTATIONS:
            rows = surface_cell_basis(TRICLINIC, hkl).astype(float) @ TRICLINIC
            assert abs(np.linalg.det(rows)) == pytest.approx(abs(np.linalg.det(TRICLINIC)))

    def test_slab_stays_in_the_parent_frame(self):
        """No rotation: the in-plane vectors are lattice vectors of the parent
        and every atom is a lattice translate of a parent atom, so the slab
        overlays the bulk it was cut from."""
        cell, kept = miller_slab(FCC, FCC_ATOMS, (1, 1, 1), n_layers=1, vacuum=0.0)
        inv = np.linalg.inv(FCC)
        for row in cell[:2]:
            assert np.allclose(row @ inv, np.round(row @ inv))
        parent = np.asarray([a["position"] for a in FCC_ATOMS]) @ inv
        for atom in kept:
            frac = np.asarray(atom["position"]) @ inv
            assert any(np.allclose(frac - p, np.round(frac - p)) for p in parent)

    def test_zero_indices_are_refused(self):
        with pytest.raises(ValueError, match="do not define a plane"):
            miller_slab(CUBIC, _atoms(0.0), (0, 0, 0), n_layers=2, vacuum=5.0)

    def test_negative_l_still_works(self):
        """(00-1) is the same family as (001) and must not be rejected."""
        cell, _ = miller_slab(CUBIC, _atoms(0.0), (0, 0, -1), n_layers=2, vacuum=5.0)
        assert np.linalg.norm(cell[2]) == pytest.approx(2 * 5.0 + 2 * 5.0)


class TestAgainstASE:
    """ASE's ``surface()`` uses the same construction. Atom count and
    surface-cell area must agree for every orientation; plane heights agree
    when both stack along +G, which for negative indices ASE does not always
    do (it then cuts the same family from the other side)."""

    CASES = [
        ("Al", "fcc", 4.05, (1, 1, 1)),
        ("Al", "fcc", 4.05, (1, 1, 0)),
        ("Al", "fcc", 4.05, (2, 1, 0)),
        ("Al", "fcc", 4.05, (3, 1, 1)),
        ("Fe", "bcc", 2.87, (1, 1, 0)),
        ("Fe", "bcc", 2.87, (2, 1, 1)),
        ("Si", "diamond", 5.43, (1, 1, 1)),
        ("NaCl", "rocksalt", 5.64, (1, 1, 0)),
    ]

    @pytest.fixture(autouse=True)
    def _ase(self):
        pytest.importorskip("ase")

    @staticmethod
    def _bulk(symbol, structure, a):
        from ase.build import bulk

        crystal = bulk(symbol, structure, a=a, cubic=True)
        atoms = [
            {"symbol": s, "position": p.tolist()}
            for s, p in zip(crystal.get_chemical_symbols(), crystal.positions, strict=True)
        ]
        return crystal, np.asarray(crystal.cell), atoms

    @pytest.mark.parametrize(("symbol", "structure", "a", "hkl"), CASES)
    def test_count_area_and_plane_heights(self, symbol, structure, a, hkl):
        from ase.build import surface

        crystal, cell, atoms = self._bulk(symbol, structure, a)
        ours_cell, ours = miller_slab(cell, atoms, hkl, n_layers=3, vacuum=10.0)
        ref = surface(crystal, hkl, 3, vacuum=10.0)
        assert len(ours) == len(ref)
        area = np.linalg.norm(np.cross(ours_cell[0], ours_cell[1]))
        assert area == pytest.approx(np.linalg.norm(np.cross(ref.cell[0], ref.cell[1])))
        ours_h = _heights(cell, ours, hkl)
        ref_h = np.sort(ref.positions[:, 2])
        assert np.diff(ours_h) == pytest.approx(np.diff(ref_h), abs=1e-6)

    def test_hexagonal_and_triclinic_counts_and_areas(self):
        from ase import Atoms
        from ase.build import bulk, surface

        mg = bulk("Mg", "hcp", a=3.21, c=5.21)
        tri = Atoms(
            "CN", positions=[[0, 0, 0], (np.array([0.3, 0.6, 0.2]) @ TRICLINIC).tolist()],
            cell=TRICLINIC, pbc=True,
        )
        for crystal, orientations in [
            (mg, [(1, 0, 0), (1, 0, 1), (1, 1, 0), (1, -1, 0)]),
            (tri, [(1, 2, 1), (2, -1, 3), (0, 1, 1), (-2, 0, 1)]),
        ]:
            cell = np.asarray(crystal.cell)
            atoms = [
                {"symbol": s, "position": p.tolist()}
                for s, p in zip(crystal.get_chemical_symbols(), crystal.positions, strict=True)
            ]
            for hkl in orientations:
                ours_cell, ours = miller_slab(cell, atoms, hkl, n_layers=3, vacuum=5.0)
                ref = surface(crystal, hkl, 3, vacuum=5.0)
                assert len(ours) == len(ref), hkl
                area = np.linalg.norm(np.cross(ours_cell[0], ours_cell[1]))
                ref_area = np.linalg.norm(np.cross(ref.cell[0], ref.cell[1]))
                assert area == pytest.approx(ref_area), hkl
