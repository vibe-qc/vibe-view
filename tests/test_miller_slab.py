"""Miller-plane slab cutting (audit 2026-07-02, deferred item 2).

The interplanar spacing needs the reciprocal lattice. The previous
expression, 1/|(h,k,l)|, was dimensionless: it reported 1.0 A for the (001)
plane of a cubic 5 A cell whose real spacing is 5.0 A. Slab thickness and
layer selection both inherited that, so a "3-layer slab" was neither three
layers nor the thickness it claimed.
"""

from __future__ import annotations

import numpy as np
import pytest

from vibeview.crystal_builder import cell_from_abc, miller_slab

CUBIC = cell_from_abc(5.0, 5.0, 5.0, 90, 90, 90)
ORTHO = cell_from_abc(3.0, 4.0, 7.0, 90, 90, 90)


def _atoms(*z):
    return [{"symbol": "A", "position": [0.0, 0.0, float(v)]} for v in z]


def _d(cell, hkl):
    """Reference spacing, straight from the definition."""
    return 1.0 / np.linalg.norm(np.linalg.inv(cell) @ np.asarray(hkl, float))


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
        cell, _ = miller_slab(CUBIC, _atoms(0.0, 2.5), (0, 0, 1),
                              n_layers=2, vacuum=10.0)
        assert np.linalg.norm(cell[2]) == pytest.approx(2 * 5.0 + 2 * 10.0)

    def test_thickness_tracks_the_cell(self):
        cell, _ = miller_slab(ORTHO, _atoms(0.0), (0, 0, 1),
                              n_layers=3, vacuum=5.0)
        assert np.linalg.norm(cell[2]) == pytest.approx(3 * 7.0 + 2 * 5.0)

    def test_more_layers_is_a_thicker_slab(self):
        thin, _ = miller_slab(CUBIC, _atoms(0.0), (0, 0, 1), n_layers=1, vacuum=1.0)
        thick, _ = miller_slab(CUBIC, _atoms(0.0), (0, 0, 1), n_layers=4, vacuum=1.0)
        assert np.linalg.norm(thick[2]) - np.linalg.norm(thin[2]) == pytest.approx(
            3 * 5.0
        )

    def test_vacuum_is_added_on_both_sides(self):
        a, _ = miller_slab(CUBIC, _atoms(0.0), (0, 0, 1), n_layers=1, vacuum=0.0)
        b, _ = miller_slab(CUBIC, _atoms(0.0), (0, 0, 1), n_layers=1, vacuum=6.0)
        assert np.linalg.norm(b[2]) - np.linalg.norm(a[2]) == pytest.approx(12.0)


class TestLayerSelection:
    def test_window_counts_planes_not_angstroms(self):
        """h*x+k*y+l*z is the plane index, so the window is n_layers wide in
        those units. Multiplying it by a spacing mixed counts with lengths."""
        atoms = _atoms(0.0, 5.0, 10.0, 15.0, 20.0)   # planes 0,1,2,3,4
        _, kept = miller_slab(CUBIC, atoms, (0, 0, 1), n_layers=2, vacuum=1.0)
        assert len(kept) == 3, [a["position"] for a in kept]  # indices 0,1,2

    def test_one_layer_keeps_the_surface_plane(self):
        atoms = _atoms(0.0, 5.0, 10.0)
        _, kept = miller_slab(CUBIC, atoms, (0, 0, 1), n_layers=1, vacuum=1.0)
        assert len(kept) == 2

    def test_atoms_keep_their_symbols(self):
        atoms = [
            {"symbol": "Na", "position": [0.0, 0.0, 0.0]},
            {"symbol": "Cl", "position": [0.0, 0.0, 2.5]},
        ]
        _, kept = miller_slab(CUBIC, atoms, (0, 0, 1), n_layers=2, vacuum=5.0)
        assert {a["symbol"] for a in kept} == {"Na", "Cl"}


class TestRefusesWhatItCannotBuild:
    """Returning a mislabelled box is worse than returning nothing: a slab
    is an input to a calculation, and a wrong one becomes wrong science."""

    @pytest.mark.parametrize("hkl", [(1, 1, 1), (1, 1, 0), (1, 0, 0), (0, 1, 1)])
    def test_non_c_normals_are_refused(self, hkl):
        with pytest.raises(ValueError, match="do not lie in the"):
            miller_slab(CUBIC, _atoms(0.0), hkl, n_layers=2, vacuum=5.0)

    def test_the_message_says_why(self):
        with pytest.raises(ValueError) as exc:
            miller_slab(CUBIC, _atoms(0.0), (1, 1, 1), n_layers=2, vacuum=5.0)
        assert "perpendicular to the surface" in str(exc.value)

    def test_zero_indices_are_refused(self):
        with pytest.raises(ValueError, match="do not define a plane"):
            miller_slab(CUBIC, _atoms(0.0), (0, 0, 0), n_layers=2, vacuum=5.0)

    def test_negative_l_still_works(self):
        """(00-1) is the same family as (001) and must not be rejected."""
        cell, _ = miller_slab(CUBIC, _atoms(0.0), (0, 0, -1), n_layers=2, vacuum=5.0)
        assert np.linalg.norm(cell[2]) == pytest.approx(2 * 5.0 + 2 * 5.0)
