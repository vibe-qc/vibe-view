"""Covalent-radius bond inference over a cell list.

This replaced an O(N^2) Python double loop. The contract is that the
result is *identical* to that loop, so the tests compare against a
literal transcription of it rather than against recorded expectations.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from vibeview.qvf import (
    _BOND_TOLERANCE,
    _COVALENT_RADII,
    _infer_bonds_by_radii,
)


class _Atom:
    def __init__(self, z, pos):
        self.atomic_number = z
        self.position = np.asarray(pos, dtype=float)


class _Struct:
    def __init__(self, atoms):
        self.atoms = atoms


def _all_pairs(structure):
    """The superseded implementation, verbatim, as the oracle.

    Non-periodic, so every bond carries the zero image.
    """
    bonds = []
    positions = [a.position for a in structure.atoms]
    nums = [a.atomic_number for a in structure.atoms]
    n = len(positions)
    for i in range(n):
        ri = _COVALENT_RADII.get(nums[i], 1.5)
        for j in range(i + 1, n):
            rj = _COVALENT_RADII.get(nums[j], 1.5)
            if float(np.linalg.norm(positions[i] - positions[j])) < (
                ri + rj + _BOND_TOLERANCE
            ):
                bonds.append((i, j, 1.0, (0, 0, 0)))
    return bonds


def _random(n, box, seed):
    rng = np.random.default_rng(seed)
    zs = rng.choice([1, 6, 7, 8, 16], size=n)
    ps = rng.uniform(0, box, (n, 3))
    return _Struct([_Atom(int(z), p) for z, p in zip(zs, ps, strict=True)])


class TestMatchesAllPairs:
    @pytest.mark.parametrize(
        ("n", "box"),
        [
            (2, 3.0),
            (50, 8.0),
            (511, 14.0),  # dense path (n <= 512)
            (513, 15.0),  # cell-list path
            (1500, 26.0),
            (900, 3.0),  # pathologically dense: most pairs bond
        ],
    )
    def test_identical_to_the_loop_it_replaced(self, n, box):
        s = _random(n, box, seed=n)
        assert _infer_bonds_by_radii(s) == _all_pairs(s)

    def test_pairs_are_ordered_and_unique(self):
        """A cell list visits each bin pair twice; emitting i<j only is
        what keeps a bond from being drawn on top of itself."""
        bonds = _infer_bonds_by_radii(_random(800, 16.0, seed=3))
        assert all(i < j for i, j, _order, _image in bonds)
        assert len({(i, j, im) for i, j, _o, im in bonds}) == len(bonds)
        assert bonds == sorted(bonds, key=lambda b: (b[0], b[1]))


class TestDegenerateGeometry:
    def test_empty(self):
        assert _infer_bonds_by_radii(_Struct([])) == []

    def test_single_atom(self):
        assert _infer_bonds_by_radii(_Struct([_Atom(6, (0, 0, 0))])) == []

    def test_coincident_atoms_do_not_crash(self):
        s = _Struct([_Atom(6, (0, 0, 0))] * 3)
        assert len(_infer_bonds_by_radii(s)) == 3

    def test_far_apart_atoms_bond_to_nothing(self):
        s = _Struct([_Atom(6, (0, 0, 0)), _Atom(6, (500.0, 0, 0))])
        assert _infer_bonds_by_radii(s) == []

    def test_unknown_element_uses_the_fallback_radius(self):
        """Z=0 placeholders must behave exactly as the old dict .get did."""
        s = _Struct([_Atom(0, (0, 0, 0)), _Atom(0, (2.9, 0, 0))])
        assert _infer_bonds_by_radii(s) == _all_pairs(s)


class TestScales:
    def test_ten_thousand_atoms_is_not_quadratic(self):
        """Regression: 13,772 atoms took 158.7 s all-pairs, which the
        Trame server pays before it binds its port. Budget is generous
        against CI jitter; the quadratic form cannot come close."""
        s = _random(10_000, 60.0, seed=11)
        t0 = time.perf_counter()
        bonds = _infer_bonds_by_radii(s)
        elapsed = time.perf_counter() - t0
        assert elapsed < 15.0, f"{elapsed:.1f}s — quadratic behaviour is back"
        assert bonds, "sanity: a 10k-atom box should have bonds"


class _PeriodicStruct(_Struct):
    def __init__(self, atoms, lattice=None, pbc=(True, True, True)):
        super().__init__(atoms)
        self.lattice_vectors = lattice
        self.pbc = pbc
        self.bonds = None


CUBIC10 = np.eye(3) * 10.0


class TestBondsThroughPeriodicBoundaries:
    """A periodic structure bonds through its own cell walls. Searching raw
    Cartesian space misses every one: two chlorines 1.0 A apart across a
    10 A boundary are 9.0 A apart in those coordinates."""

    def test_a_bond_across_a_face_is_found(self):
        """The image is recorded, not just the pair: atom 1 is reached one
        cell over in +x, which is what lets the renderer draw it short
        instead of stretched across the box."""
        s = _PeriodicStruct([_Atom(17, (9.5, 0, 0)), _Atom(17, (0.5, 0, 0))], CUBIC10)
        assert _infer_bonds_by_radii(s) == [(0, 1, 1.0, (1, 0, 0))]

    def test_the_same_pair_inside_the_cell_is_unchanged(self):
        s = _PeriodicStruct([_Atom(17, (4.5, 0, 0)), _Atom(17, (5.5, 0, 0))], CUBIC10)
        assert _infer_bonds_by_radii(s) == [(0, 1, 1.0, (0, 0, 0))]

    def test_without_pbc_the_wrapped_pair_stays_unbonded(self):
        """Not periodic means 9.0 A apart really is 9.0 A apart."""
        s = _PeriodicStruct(
            [_Atom(17, (9.5, 0, 0)), _Atom(17, (0.5, 0, 0))],
            CUBIC10,
            pbc=(False, False, False),
        )
        assert _infer_bonds_by_radii(s) == []

    def test_no_lattice_means_no_wrapping(self):
        s = _PeriodicStruct([_Atom(17, (9.5, 0, 0)), _Atom(17, (0.5, 0, 0))], None)
        assert _infer_bonds_by_radii(s) == []

    def test_only_periodic_axes_wrap(self):
        """A slab is periodic in a and b but not c; a pair separated along c
        must not bond through the vacuum."""
        s = _PeriodicStruct(
            [_Atom(17, (0, 0, 9.5)), _Atom(17, (0, 0, 0.5))],
            CUBIC10,
            pbc=(True, True, False),
        )
        assert _infer_bonds_by_radii(s) == []

    def test_far_apart_atoms_still_do_not_bond(self):
        s = _PeriodicStruct([_Atom(17, (0, 0, 0)), _Atom(17, (5, 0, 0))], CUBIC10)
        assert _infer_bonds_by_radii(s) == []

    def test_an_atom_does_not_bond_to_its_own_image(self):
        """A lone atom in a cell narrower than the cutoff would otherwise
        pair with itself, which is not a bond anything can draw."""
        s = _PeriodicStruct([_Atom(17, (0, 0, 0))], np.eye(3) * 2.0)
        assert _infer_bonds_by_radii(s) == []

    def test_each_bond_is_reported_once(self):
        """The same physical bond is reachable from both sides of a wall."""
        s = _PeriodicStruct([_Atom(17, (9.5, 0, 0)), _Atom(17, (0.5, 0, 0))], CUBIC10)
        bonds = _infer_bonds_by_radii(s)
        assert len(bonds) == len({(i, j) for i, j, _order, _image in bonds})
        assert all(i < j for i, j, _order, _image in bonds)

    def test_a_chain_closes_on_itself_through_the_boundary(self):
        """Four atoms evenly spaced along a whose spacing equals the wrap
        distance form a ring, so every atom has two neighbours."""
        lattice = np.eye(3) * 6.0
        atoms = [_Atom(6, (x, 0, 0)) for x in (0.0, 1.5, 3.0, 4.5)]
        bonds = _infer_bonds_by_radii(_PeriodicStruct(atoms, lattice))
        assert len(bonds) == 4, bonds
        degree = {i: 0 for i in range(4)}
        for i, j, _order, _image in bonds:
            degree[i] += 1
            degree[j] += 1
        assert set(degree.values()) == {2}, degree


class TestPeriodicSearchStaysCheap:
    def test_images_do_not_multiply_the_search(self):
        """Appending all 26 translations wholesale searched 27x the atoms:
        measured 8.05 s on 20,000 atoms, against 0.23 s once the images were
        trimmed to the shell that can actually reach the cell."""
        rng = np.random.default_rng(3)
        n = 20_000
        edge = (n / 0.05) ** (1 / 3)
        atoms = [_Atom(6, p) for p in rng.uniform(0, edge, (n, 3))]
        s = _PeriodicStruct(atoms, np.eye(3) * edge)
        t0 = time.perf_counter()
        bonds = _infer_bonds_by_radii(s)
        elapsed = time.perf_counter() - t0
        assert elapsed < 8.0, f"{elapsed:.1f}s — the image shell is not being trimmed"
        assert bonds


class TestImagesAreCarriedPerBond:
    """A bond is ``(i, j, order, image)``. The image is part of its identity:
    two atoms in a dense crystal are bonded through several images at once,
    and keying only on the pair collapsed them, halving the coordination."""

    def test_a_pair_bonded_through_two_images_records_both(self):
        lattice = np.diag([3.0, 30.0, 30.0])
        s = _PeriodicStruct(
            [_Atom(6, (0.0, 0, 0)), _Atom(6, (1.5, 0, 0))],
            lattice,
            pbc=(True, False, False),
        )
        bonds = _infer_bonds_by_radii(s)
        # Each atom has two neighbours: the other atom at +1.5 and its image
        # at -1.5. Both are now representable.
        assert len(bonds) == 2, bonds
        assert {b[3] for b in bonds} == {(0, 0, 0), (-1, 0, 0)}, bonds

    def test_distinct_pairs_are_all_kept(self):
        lattice = np.diag([6.0, 30.0, 30.0])
        atoms = [_Atom(6, (x, 0, 0)) for x in (0.0, 1.5, 3.0, 4.5)]
        bonds = _infer_bonds_by_radii(
            _PeriodicStruct(atoms, lattice, pbc=(True, False, False))
        )
        degree = {i: 0 for i in range(4)}
        for i, j, _order, _image in bonds:
            degree[i] += 1
            degree[j] += 1
        assert set(degree.values()) == {2}

    def test_a_bond_and_its_reverse_are_one_bond(self):
        """(i, j, +image) and (j, i, -image) describe the same stick."""
        lattice = np.diag([3.0, 30.0, 30.0])
        s = _PeriodicStruct(
            [_Atom(6, (0.0, 0, 0)), _Atom(6, (1.5, 0, 0))],
            lattice,
            pbc=(True, False, False),
        )
        bonds = _infer_bonds_by_radii(s)
        keys = {(b[0], b[1], b[3]) for b in bonds}
        assert len(keys) == len(bonds)
        for i, j, _order, _image in bonds:
            assert i <= j


class TestKnownChemistryCoordination:
    """Coordination numbers against textbook values. These are what the
    missing image offset used to halve."""

    def _coordination(self, atoms, lattice):
        bonds = _infer_bonds_by_radii(_PeriodicStruct(atoms, lattice))
        degree = {i: 0 for i in range(len(atoms))}
        for i, j, _order, _image in bonds:
            degree[i] += 1
            degree[j] += 1
        return sorted(set(degree.values()))

    def test_diamond_is_four_coordinate(self):
        a = 3.567
        frac = [
            (0, 0, 0), (0, 0.5, 0.5), (0.5, 0, 0.5), (0.5, 0.5, 0),
            (0.25, 0.25, 0.25), (0.25, 0.75, 0.75),
            (0.75, 0.25, 0.75), (0.75, 0.75, 0.25),
        ]
        atoms = [_Atom(6, np.array(f) * a) for f in frac]
        assert self._coordination(atoms, np.eye(3) * a) == [4]

    def test_rocksalt_is_six_coordinate(self):
        """Was 3 while a pair could hold only one image."""
        a = 5.64
        na = [(0, 0, 0), (0, 0.5, 0.5), (0.5, 0, 0.5), (0.5, 0.5, 0)]
        cl = [(0.5, 0.5, 0.5), (0.5, 0, 0), (0, 0.5, 0), (0, 0, 0.5)]
        atoms = [_Atom(11, np.array(f) * a) for f in na]
        atoms += [_Atom(17, np.array(f) * a) for f in cl]
        assert self._coordination(atoms, np.eye(3) * a) == [6]

    def test_fcc_copper_is_twelve_coordinate(self):
        """Was 3."""
        a = 3.615
        frac = [(0, 0, 0), (0, 0.5, 0.5), (0.5, 0, 0.5), (0.5, 0.5, 0)]
        atoms = [_Atom(29, np.array(f) * a) for f in frac]
        assert self._coordination(atoms, np.eye(3) * a) == [12]


class TestMetalMetalPairs:
    """Neutral-atom radii over-bond a cation. Mg is 1.41 A against 0.72 for
    Mg(2+), so in an ionic solid the metal-metal separation falls inside a
    cutoff built from neutral radii and a metal sublattice appears that is
    not there. Measured before the rule: MgO gave 24 spurious Mg-Mg bonds and
    read 18-coordinate instead of 6; CsCl gave 3 spurious Cs-Cs, 14 instead
    of 8."""

    def _audit(self, atoms, lattice):
        bonds = _infer_bonds_by_radii(_PeriodicStruct(atoms, lattice))
        degree = {i: 0 for i in range(len(atoms))}
        kinds = set()
        for i, j, _order, _image in bonds:
            degree[i] += 1
            degree[j] += 1
            kinds.add(tuple(sorted((atoms[i].atomic_number, atoms[j].atomic_number))))
        return sorted(set(degree.values())), kinds

    def test_rocksalt_mgo_has_no_metal_sublattice(self):
        a = 4.21
        mg = [(0, 0, 0), (0, 0.5, 0.5), (0.5, 0, 0.5), (0.5, 0.5, 0)]
        ox = [(0.5, 0.5, 0.5), (0.5, 0, 0), (0, 0.5, 0), (0, 0, 0.5)]
        atoms = [_Atom(12, np.array(f) * a) for f in mg]
        atoms += [_Atom(8, np.array(f) * a) for f in ox]
        coord, kinds = self._audit(atoms, np.eye(3) * a)
        assert coord == [6], coord
        assert kinds == {(8, 12)}, kinds

    def test_cscl_is_eight_coordinate(self):
        a = 4.11
        atoms = [_Atom(55, (0, 0, 0)), _Atom(17, (a / 2, a / 2, a / 2))]
        coord, kinds = self._audit(atoms, np.eye(3) * a)
        assert coord == [8], coord
        assert kinds == {(17, 55)}, kinds

    def test_a_real_metal_keeps_its_metal_bonds(self):
        """In an actual metal those contacts are the bonding."""
        a = 3.615
        frac = [(0, 0, 0), (0, 0.5, 0.5), (0.5, 0, 0.5), (0.5, 0.5, 0)]
        atoms = [_Atom(29, np.array(f) * a) for f in frac]
        coord, kinds = self._audit(atoms, np.eye(3) * a)
        assert coord == [12], coord
        assert kinds == {(29, 29)}

    def test_an_alloy_is_still_all_metal(self):
        """Two metal species with no non-metal present is still a metal."""
        a = 3.6
        atoms = [
            _Atom(29, (0, 0, 0)),
            _Atom(28, (a / 2, a / 2, 0)),
            _Atom(29, (a / 2, 0, a / 2)),
            _Atom(28, (0, a / 2, a / 2)),
        ]
        coord, _kinds = self._audit(atoms, np.eye(3) * a)
        assert coord == [12], coord

    def test_a_metal_carbonyl_keeps_its_metal_ligand_bonds(self):
        """The rule drops metal-metal pairs, never metal-nonmetal."""
        atoms = [_Atom(28, (0, 0, 0)), _Atom(6, (1.8, 0, 0)), _Atom(8, (2.95, 0, 0))]
        bonds = _infer_bonds_by_radii(_Struct(atoms))
        pairs = {tuple(sorted((atoms[i].atomic_number, atoms[j].atomic_number)))
                 for i, j, _o, _im in bonds}
        assert (6, 28) in pairs, pairs

    def test_molecules_are_untouched(self):
        s = _Struct([_Atom(8, (0, 0, 0)), _Atom(1, (0.96, 0, 0))])
        assert len(_infer_bonds_by_radii(s)) == 1
