"""Tests for crystal_builder module."""

from __future__ import annotations

import numpy as np


class TestCrystalBuilder:
    def test_import(self):
        from vibeview.crystal_builder import cell_from_abc, search_space_groups

        assert callable(search_space_groups)
        assert callable(cell_from_abc)

    def test_search_space_groups(self):
        from vibeview.crystal_builder import search_space_groups

        results = search_space_groups("")
        assert len(results) >= 20
        fm3m = search_space_groups("Fm-3m")
        assert len(fm3m) == 1
        assert fm3m[0]["number"] == 225

    def test_search_by_number(self):
        from vibeview.crystal_builder import search_space_groups

        results = search_space_groups("225")
        assert len(results) >= 1
        assert any(r["symbol"] == "Fm-3m" for r in results)

    def test_cell_from_abc_cubic(self):
        from vibeview.crystal_builder import cell_from_abc

        cell = cell_from_abc(5.0, 5.0, 5.0, 90, 90, 90)
        assert cell.shape == (3, 3)
        assert abs(cell[0, 0] - 5.0) < 1e-6

    def test_abc_from_cell_roundtrip(self):
        from vibeview.crystal_builder import abc_from_cell, cell_from_abc

        original = (4.5, 5.2, 6.1, 88, 92, 90)
        cell = cell_from_abc(*original)
        a, b, c, alpha, beta, gamma = abc_from_cell(cell)
        assert abs(a - 4.5) < 1e-3
        assert abs(b - 5.2) < 1e-3
        assert abs(c - 6.1) < 1e-3
        # Angles are recovered from the computed cell geometry and should
        # round-trip to within a degree for these near-90° inputs.
        assert abs(alpha - 88) < 1
        assert abs(beta - 92) < 1
        assert abs(gamma - 90) < 1

    def test_cell_volume(self):
        from vibeview.crystal_builder import cell_from_abc, cell_volume

        cell = cell_from_abc(5.0, 5.0, 5.0, 90, 90, 90)
        vol = cell_volume(cell)
        assert abs(vol - 125.0) < 1e-6

    def test_fractional_conversion(self):
        from vibeview.crystal_builder import (
            cartesian_to_fractional,
            cell_from_abc,
            fractional_to_cartesian,
        )

        cell = cell_from_abc(10.0, 10.0, 10.0, 90, 90, 90)
        cart = np.array([[5.0, 5.0, 5.0]])
        frac = cartesian_to_fractional(cell, cart)
        assert abs(frac[0, 0] - 0.5) < 1e-6
        back = fractional_to_cartesian(cell, frac)
        assert abs(back[0, 0] - 5.0) < 1e-6

    def test_replicate_cell(self):
        from vibeview.crystal_builder import cell_from_abc, replicate_cell

        cell = cell_from_abc(5.0, 5.0, 5.0, 90, 90, 90)
        atoms = [{"symbol": "Si", "position": [0.0, 0.0, 0.0]}]
        new_cell, new_atoms = replicate_cell(cell, atoms, 2, 2, 2)
        assert len(new_atoms) == 8
        # supercell a vector has length 10.0
        assert abs(np.linalg.norm(new_cell[0]) - 10.0) < 1e-6

    def test_miller_slab(self):
        from vibeview.crystal_builder import cell_from_abc, miller_slab

        cell = cell_from_abc(5.0, 5.0, 5.0, 90, 90, 90)
        atoms = [
            {"symbol": "A", "position": [0.0, 0.0, 0.0]},
            {"symbol": "B", "position": [0.0, 0.0, 2.5]},
            {"symbol": "C", "position": [0.0, 0.0, 5.0]},
        ]
        new_cell, new_atoms = miller_slab(cell, atoms, (0, 0, 1), n_layers=2, vacuum=10.0)
        assert len(new_atoms) == 2 * 3  # each layer repeats the cell's atoms
        assert new_cell.shape == (3, 3)


class TestCrystalBuilderEdgeCases:
    def test_search_empty_returns_all(self):
        from vibeview.crystal_builder import search_space_groups

        all_groups = search_space_groups("")
        assert len(all_groups) >= 23

    def test_search_by_system(self):
        from vibeview.crystal_builder import search_space_groups

        cubic = search_space_groups("cubic")
        assert len(cubic) >= 5
        assert all(g["crystal_system"] == "cubic" for g in cubic)

    def test_cell_from_abc_monoclinic(self):
        from vibeview.crystal_builder import abc_from_cell, cell_from_abc

        cell = cell_from_abc(5.0, 6.0, 7.0, 90, 105, 90)
        a, b, c, alpha, beta, gamma = abc_from_cell(cell)
        assert abs(beta - 105) < 0.1

    def test_cell_volume_triclinic(self):
        from vibeview.crystal_builder import cell_from_abc, cell_volume

        cell = cell_from_abc(5.0, 6.0, 7.0, 85, 92, 88)
        vol = cell_volume(cell)
        assert vol > 0

    def test_replicate_wrap_vs_unwrap(self):
        from vibeview.crystal_builder import cell_from_abc, replicate_cell

        cell = cell_from_abc(3.0, 3.0, 3.0, 90, 90, 90)
        atoms = [{"symbol": "X", "position": [0.5, 0.5, 0.5]}]
        _, wrapped = replicate_cell(cell, atoms, 2, 2, 2, wrap=True)
        _, unwrapped = replicate_cell(cell, atoms, 2, 2, 2, wrap=False)
        assert len(wrapped) == len(unwrapped)


class TestCrystalBuilderIntegration:
    """Test crystal builder with realistic structures."""

    def test_silicon_cubic_cell(self):
        from vibeview.crystal_builder import cell_from_abc, cell_volume, replicate_cell

        cell = cell_from_abc(5.43, 5.43, 5.43, 90, 90, 90)
        vol = cell_volume(cell)
        assert abs(vol - 5.43**3) < 0.01

        atoms = [
            {"symbol": "Si", "position": [0, 0, 0]},
            {"symbol": "Si", "position": [1.3575, 1.3575, 1.3575]},
            {"symbol": "Si", "position": [0, 2.715, 2.715]},
            {"symbol": "Si", "position": [2.715, 0, 2.715]},
            {"symbol": "Si", "position": [2.715, 2.715, 0]},
            {"symbol": "Si", "position": [1.3575, 4.0725, 4.0725]},
            {"symbol": "Si", "position": [4.0725, 1.3575, 4.0725]},
            {"symbol": "Si", "position": [4.0725, 4.0725, 1.3575]},
        ]
        new_cell, new_atoms = replicate_cell(cell, atoms, 2, 2, 2)
        assert len(new_atoms) == 64

    def test_slab_cut(self):
        from vibeview.crystal_builder import cell_from_abc, miller_slab

        cell = cell_from_abc(5.0, 5.0, 20.0, 90, 90, 90)
        atoms = []
        for i in range(8):
            atoms.append({"symbol": "X", "position": [0.0, 0.0, i * 2.5]})

        new_cell, new_atoms = miller_slab(cell, atoms, (0, 0, 1), n_layers=3, vacuum=10.0)
        assert len(new_atoms) == 3 * len(atoms)  # three repetitions of the cell
        assert abs(np.linalg.norm(new_cell[2]) - (3 * 20.0 + 2 * 10.0)) < 1e-9

    def test_fractional_roundtrip(self):
        from vibeview.crystal_builder import (
            cartesian_to_fractional,
            cell_from_abc,
            fractional_to_cartesian,
        )

        cell = cell_from_abc(8.0, 9.0, 10.0, 85, 92, 88)
        frac = np.array([[0.125, 0.25, 0.375]], dtype=float)
        cart = fractional_to_cartesian(cell, frac)
        frac2 = cartesian_to_fractional(cell, cart)
        assert np.allclose(frac, frac2, atol=1e-6)
