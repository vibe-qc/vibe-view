"""Axis frame, cut-aways and property colours for long structures (roadmap: nanotubes)."""

from __future__ import annotations

import numpy as np
import pytest

from vibeview import nanotube as nt
from vibeview import structure_views as views
from vibeview.qvf import Atom, StructureData


def _structure(symbols, positions) -> StructureData:
    atoms = [Atom(s, np.asarray(p, dtype=float), 6 if s == "C" else 0)
             for s, p in zip(symbols, positions, strict=True)]
    return StructureData(atoms, (False, False, False), None, None)


@pytest.fixture(scope="module")
def tube():
    t = nt.single_wall(10, 10, 30.0, end="open")
    # A tilted tube with a "guest" on its axis: the frame must find the tilt.
    tilt = np.array([[1, 0, 0], [0, np.cos(0.4), -np.sin(0.4)], [0, np.sin(0.4), np.cos(0.4)]])
    positions = (t.positions - t.positions.mean(axis=0)) @ tilt.T
    guest = np.array([[0.0, 0.0, 0.0]])
    return _structure(t.symbols + ["Ne"], np.vstack((positions, guest))), tilt


def test_axis_frame_follows_the_carbon_atoms(tube):
    structure, tilt = tube
    frame = views.axis_frame(structure)
    assert abs(frame.axis @ (tilt @ [0, 0, 1])) == pytest.approx(1.0, abs=1e-6)
    assert frame.radius == pytest.approx(nt.wall_info(10, 10).diameter / 2, abs=1e-3)
    assert abs(frame.normal @ frame.axis) < 1e-9


def test_half_cutaway_keeps_one_side_and_the_guest(tube):
    structure, _ = tube
    keep = views.cutaway_keep(structure, "half")
    n = len(structure.atoms)
    assert n - 1 in keep  # the guest on the axis
    assert 0.4 * n < len(keep) < 0.6 * n
    frame = views.axis_frame(structure)
    rel = np.array([a.position for a in structure.atoms]) - frame.center
    assert all(rel[i] @ frame.normal <= 1e-9 or i == n - 1 for i in keep)


def test_section_and_end_cutaways_follow_the_position(tube):
    structure, _ = tube
    frame = views.axis_frame(structure)
    along = (np.array([a.position for a in structure.atoms]) - frame.center) @ frame.axis
    section = views.cutaway_keep(structure, "section", 0.5)
    assert section and all(abs(along[i]) <= views.SECTION_HALF_WIDTH + 1e-9 for i in section)
    short = views.cutaway_keep(structure, "end", 0.25)
    long = views.cutaway_keep(structure, "end", 0.75)
    assert short < long
    assert views.cutaway_keep(structure, "off") == set(range(len(structure.atoms)))
    with pytest.raises(ValueError):
        views.cutaway_keep(structure, "quarter")


def test_axis_camera_views(tube):
    structure, _ = tube
    frame = views.axis_frame(structure)
    position, focal, up = views.axis_camera(structure, "down")
    assert np.allclose(np.cross(position - focal, frame.axis), 0.0, atol=1e-6)
    position, focal, up = views.axis_camera(structure, "side")
    assert (position - focal) @ frame.normal > 0  # facing the face a half cut opens
    assert abs(up @ frame.axis) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        views.axis_camera(structure, "top")


def test_coordination_and_axis_distance_values(tube):
    structure, _ = tube
    bonds = [(0, 1, 1.0, (0, 0, 0)), (1, 2, 1.0, (0, 0, 0))]
    counts = views.property_values(structure, bonds, "coordination")
    assert counts[:3].tolist() == [1, 2, 1]
    distance = views.property_values(structure, None, "axis_distance")
    assert distance[-1] == pytest.approx(0.0, abs=1e-6)
    assert np.median(distance[:-1]) == pytest.approx(views.axis_frame(structure).radius)


class _Reader:
    """Just enough of QVFReader for the table-backed modes."""

    def __init__(self, charges=None, pairs=None):
        from types import SimpleNamespace

        self.sections = []
        if charges is not None:
            self.sections.append(SimpleNamespace(id="q", kind="atom_properties"))
        if pairs is not None:
            self.sections.append(SimpleNamespace(id="b", kind="bond_orders"))
        self._charges, self._pairs = charges, pairs

    def read_atom_properties(self, _id):
        from vibeview.qvf import AtomPropertiesData

        return AtomPropertiesData(mulliken_charges=np.array([9.0] * len(self._charges)),
                                  loewdin_charges=None, iao_charges=np.asarray(self._charges))

    def read_bond_orders(self, _id):
        from types import SimpleNamespace

        return SimpleNamespace(pairs=self._pairs)


def test_charge_mode_prefers_iao_and_checks_the_atom_count():
    structure = _structure(["C", "Cu", "C"], [[0, 0, 0], [2, 0, 0], [4, 0, 0]])
    values = views.property_values(structure, None, "charge", _Reader(charges=[-0.2, 0.3, 0.1]))
    assert values.tolist() == [-0.2, 0.3, 0.1]
    colors, (low, high) = views.property_colors(values, "charge")
    assert (low, high) == (-0.3, 0.3)
    assert colors[1][0] > colors[1][2]  # positive charge: red end
    with pytest.raises(ValueError, match="atoms"):
        views.property_values(structure, None, "charge", _Reader(charges=[0.1]))
    with pytest.raises(ValueError, match="atom_properties"):
        views.property_values(structure, None, "charge", _Reader())


def test_metal_bond_order_marks_interface_carbons():
    structure = _structure(["C", "Cu", "C", "Au"], [[0, 0, 0], [2, 0, 0], [4, 0, 0], [6, 0, 0]])
    pairs = [{"i": 0, "j": 1, "order": 0.62}, {"i": 0, "j": 2, "order": 1.4},
             {"i": 3, "j": 2, "order": 0.35}, {"i": 1, "j": 3, "order": 0.2}]
    values = views.property_values(structure, None, "metal_bond_order", _Reader(pairs=pairs))
    assert values.tolist() == pytest.approx([0.62, 0.0, 0.35, 0.0])
    with pytest.raises(ValueError, match="bond_orders"):
        views.property_values(structure, None, "metal_bond_order", _Reader())


def test_sequential_ramp_spans_the_range():
    colors, (low, high) = views.property_colors(np.array([2.0, 3.0, 2.5]), "coordination")
    assert (low, high) == (2.0, 3.0)
    assert colors[0] != colors[1]
    assert "2 to 3" in views.describe("coordination", low, high)


def test_bond_stretch_is_signed_and_uses_reference_length():
    s = _structure(["C", "C", "O"], [[0, 0, 0], [1.42 * 1.1, 0, 0], [3, 0, 0]])
    bonds = [(0, 1, 1., (0, 0, 0)), (1, 2, 1., (0, 0, 0))]
    values = views.property_values(s, bonds, "local_strain")
    np.testing.assert_allclose(values, [10, 10, 0], atol=1e-8)
    _, (lo, hi) = views.property_colors(values, "local_strain")
    assert lo == -hi


def test_carbon_sites_identify_open_rims_and_heavy_adducts():
    from vibeview.bonds import structure_bonds

    tube = nt.single_wall(5, 5, length=20, end="open")
    s = _structure(["C"] * len(tube.positions), tube.positions)
    bonds = structure_bonds(s)
    values = views.property_values(s, bonds, "carbon_sites")
    assert 1 in values and 0 in values
    rim = int(np.flatnonzero(values == 1)[0])
    s.atoms.append(Atom("O", s.atoms[rim].position + [0, 0, -1.4], 8))
    bonds.append((rim, len(s.atoms) - 1, 1., (0, 0, 0)))
    values = views.property_values(s, bonds, "carbon_sites")
    assert values[rim] == values[-1] == 3
    colors, _ = views.property_colors(values, "carbon_sites")
    assert colors[rim] != colors[0] or values[0] == 3
