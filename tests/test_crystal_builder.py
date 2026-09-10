"""Tests for crystal_builder module."""

from __future__ import annotations

import numpy as np
import pytest


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
        assert len(new_atoms) <= 3
        assert new_cell.shape == (3, 3)
