"""The build tools: fragment library, hydrogen addition and supercells."""

from __future__ import annotations

import numpy as np


class TestBuildToolsEdgeCases:
    def test_add_hydrogens_to_empty(self):
        from vibeview.build_tools import add_hydrogens

        result = add_hydrogens([])
        assert result == []

    def test_add_hydrogens_to_water(self):
        from vibeview.build_tools import add_hydrogens

        atoms = [
            {"symbol": "O", "position": [0, 0, 0], "atomic_number": 8},
            {"symbol": "H", "position": [0, 0.757, 0.586], "atomic_number": 1},
            {"symbol": "H", "position": [0, -0.757, 0.586], "atomic_number": 1},
        ]
        result = add_hydrogens(atoms)
        assert len(result) == 3  # Water is already saturated

    def test_fragment_ch3_valid(self):
        from vibeview.build_tools import FRAGMENTS

        ch3 = FRAGMENTS["CH3"]
        # C at origin, 3 H atoms at ~1.09 Angstrom
        assert ch3[0][0] == "C"
        for i in range(1, 4):
            assert ch3[i][0] == "H"
            # Distance from center to H
            pos = np.array(ch3[i][1])
            dist = np.linalg.norm(pos)
            assert 0.9 < dist < 1.3  # ~1.09 Angstrom CH bond

    def test_supercell_small(self):
        from vibeview.build_tools import build_supercell

        atoms = [{"symbol": "Li", "position": [0, 0, 0], "atomic_number": 3}]
        lattice = [[5.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0, 5.0]]
        result = build_supercell(atoms, lattice, (1, 1, 1))
        assert len(result) == 1

    def test_supercell_large(self):
        from vibeview.build_tools import build_supercell

        atoms = [{"symbol": "Au", "position": [0, 0, 0], "atomic_number": 79}]
        lattice = [[5.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0, 5.0]]
        result = build_supercell(atoms, lattice, (3, 3, 3))
        assert len(result) == 27


class TestBuildToolsIntegration:
    """Test molecular build tools with realistic fragments."""

    def test_fragment_attachment(self):
        """Attach a fragment to a molecule."""
        from vibeview.build_tools import FRAGMENTS

        assert "CH3" in FRAGMENTS
        chunk = FRAGMENTS["CH3"]
        assert len(chunk) == 4  # C + 3H
        assert chunk[0][0] == "C"

    def test_add_hydrogens(self):
        """Add hydrogens to a carbon atom."""
        from vibeview.build_tools import add_hydrogens

        atoms = [{"symbol": "C", "position": [0.0, 0.0, 0.0], "atomic_number": 6}]
        result = add_hydrogens(atoms)
        assert len(result) == 5  # C + 4H

    def test_add_hydrogens_never_places_coincident_atoms(self):
        """Partially saturated atoms must not receive overlapping hydrogens.

        The bare-C case above always worked; the bug was in *partial*
        saturation. Each candidate direction was only checked against
        pre-existing atoms, never against hydrogens placed earlier in the
        same call, so on C-H the redirected first hydrogen and the third
        one landed on the same tetrahedral vertex — two H at the identical
        position, which downstream became a zero-length bond and a NaN
        render transform that killed the whole scene rebuild.
        """
        import numpy as np

        from vibeview.build_tools import add_hydrogens

        cases = {
            "C-H": [
                {"symbol": "C", "position": [0.0, 0.0, 0.0], "atomic_number": 6},
                {"symbol": "H", "position": [1.09, 0.0, 0.0], "atomic_number": 1},
            ],
            "C-C": [
                {"symbol": "C", "position": [0.0, 0.0, 0.0], "atomic_number": 6},
                {"symbol": "C", "position": [1.4, 0.0, 0.0], "atomic_number": 6},
            ],
            "O": [{"symbol": "O", "position": [0.0, 0.0, 0.0], "atomic_number": 8}],
        }
        for label, atoms in cases.items():
            out = add_hydrogens(atoms)
            pos = np.array([a["position"] for a in out])
            n = len(pos)
            dmin = min(
                float(np.linalg.norm(pos[i] - pos[j]))
                for i in range(n)
                for j in range(i + 1, n)
            )
            assert dmin > 0.4, f"{label}: coincident atoms placed (min dist {dmin:.3f})"

        # Valence counts still respected on the partially saturated cases.
        assert len(add_hydrogens(cases["C-H"])) == 5  # 3 more H
        assert len(add_hydrogens(cases["C-C"])) == 8  # ethane-like, 3 H each

    def test_build_supercell_nonperiodic(self):
        """Build supercell for non-periodic molecule."""
        from vibeview.build_tools import build_supercell

        atoms = [{"symbol": "H", "position": [0.0, 0.0, 0.0], "atomic_number": 1}]
        lattice = [[10.0, 0.0, 0.0], [0.0, 10.0, 0.0], [0.0, 0.0, 10.0]]
        result = build_supercell(atoms, lattice, (2, 2, 2))
        assert len(result) == 8
