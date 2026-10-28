"""Nanotube generator and tube–metal interface builder (roadmap: carbon nanotubes)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from vibeview import interface_builder as ib
from vibeview import nanotube as nt


def _coordination(positions: np.ndarray, cutoff: float = 1.7) -> np.ndarray:
    d = np.linalg.norm(positions[:, None] - positions[None], axis=-1)
    np.fill_diagonal(d, np.inf)
    return (d < cutoff).sum(axis=1)


def _bond_lengths(positions: np.ndarray, cutoff: float = 1.7) -> np.ndarray:
    d = np.linalg.norm(positions[:, None] - positions[None], axis=-1)
    np.fill_diagonal(d, np.inf)
    return d[d < cutoff]


# ── (n,m) metrics ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("n", "m", "diameter", "angle", "atoms", "kind"),
    [
        (10, 10, 13.56, 30.0, 40, "armchair"),
        (17, 0, 13.31, 0.0, 68, "zigzag"),
        (10, 5, 10.36, 19.11, 140, "chiral"),
        (6, 5, 7.47, 27.0, 364, "chiral"),
    ],
)
def test_wall_info_matches_zone_folding_metrics(n, m, diameter, angle, atoms, kind):
    info = nt.wall_info(n, m)
    assert info.diameter == pytest.approx(diameter, abs=0.01)
    assert info.chiral_angle == pytest.approx(angle, abs=0.01)
    assert info.atoms_per_cell == atoms
    assert info.kind == kind
    assert info.metallic == ((n - m) % 3 == 0)


def test_indices_are_normalised_and_validated():
    assert nt.wall_info(5, 10).n == 10
    with pytest.raises(ValueError):
        nt.wall_info(0, 0)
    with pytest.raises(ValueError):
        nt.wall_info(-1, 3)


# ── Single-wall tubes ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("n", "m"), [(10, 10), (17, 0), (10, 5)])
def test_periodic_tube_is_a_rolled_graphene_sheet(n, m):
    tube = nt.single_wall(n, m, cells=2, end="periodic")
    info = nt.wall_info(n, m)
    assert tube.n_atoms == 2 * info.atoms_per_cell
    assert tube.pbc == (False, False, True)
    assert tube.lattice is not None
    assert tube.lattice[2, 2] == pytest.approx(2 * info.period)
    xy = tube.positions[:, :2] - tube.positions[:, :2].mean(axis=0)
    assert np.allclose(np.linalg.norm(xy, axis=1), info.diameter / 2, atol=1e-6)
    # Every atom has three neighbours once the periodic images are counted.
    images = np.vstack([tube.positions + [0, 0, k * tube.lattice[2, 2]] for k in (-1, 0, 1)])
    d = np.linalg.norm(tube.positions[:, None] - images[None], axis=-1)
    counts = ((d > 1e-6) & (d < 1.6)).sum(axis=1)
    assert np.all(counts == 3)


def test_open_tube_has_no_dangling_atoms_and_h_terminates_its_edge():
    tube = nt.single_wall(10, 5, 30.0, end="open")
    counts = _coordination(tube.positions)
    assert set(np.unique(counts)) <= {2, 3}
    edge = int((counts == 2).sum())
    capped = nt.single_wall(10, 5, 30.0, end="hydrogen")
    assert capped.symbols.count("H") == edge
    hydrogens = capped.positions[np.array(capped.symbols) == "H"]
    carbons = capped.positions[np.array(capped.symbols) == "C"]
    d = np.linalg.norm(hydrogens[:, None] - carbons[None], axis=-1)
    assert np.allclose(d.min(axis=1), nt.CH_BOND, atol=1e-6)


@pytest.mark.parametrize(("n", "m"), [(5, 5), (9, 0)])
def test_c60_caps_close_both_ends(n, m):
    tube = nt.single_wall(n, m, 20.0, end="cap")
    assert np.all(_coordination(tube.positions) == 3)
    lengths = _bond_lengths(tube.positions)
    assert lengths.min() > 1.25
    assert lengths.max() < 1.7


def test_cap_refuses_a_rim_no_c60_half_fits():
    with pytest.raises(ValueError, match="no C60 cap"):
        nt.single_wall(10, 10, 20.0, end="cap")


def test_unknown_end_is_refused():
    with pytest.raises(ValueError, match="end must be"):
        nt.single_wall(5, 5, 10.0, end="closed")


# ── Multi-wall tubes and assemblies ─────────────────────────────────────────


def test_armchair_wall_series_is_spaced_like_graphite():
    assert nt.wall_series(5, 5, 4) == [(5, 5), (10, 10), (15, 15), (20, 20)]
    tube = nt.multi_wall(5, 5, 3, 15.0)
    assert len(tube.walls) == 3
    assert tube.wall_spacings == pytest.approx([3.39, 3.39], abs=0.01)
    assert [w.chiral_angle for w in tube.walls] == pytest.approx([30.0] * 3)


def test_multi_wall_refuses_caps():
    with pytest.raises(ValueError):
        nt.multi_wall(5, 5, 2, 10.0, end="cap")


def test_bundle_packs_tubes_a_graphite_gap_apart():
    tube = nt.single_wall(10, 10, 10.0)
    rope = nt.bundle(tube, 7)
    assert rope.n_atoms == 7 * tube.n_atoms
    assert len(rope.tube_starts) == 7
    centres = [rope.positions[s:s + tube.n_atoms, :2].mean(axis=0) for s in rope.tube_starts]
    pitch = min(np.linalg.norm(a - b) for i, a in enumerate(centres) for b in centres[i + 1:])
    assert pitch == pytest.approx(tube.walls[0].diameter + nt.BUNDLE_GAP, abs=1e-6)


def test_forest_rejects_overlapping_tubes():
    tube = nt.single_wall(10, 10, 10.0)
    with pytest.raises(ValueError, match="pitch"):
        nt.forest(tube, 2, 2, 5.0)
    assert nt.forest(tube, 2, 3, 30.0).n_atoms == 6 * tube.n_atoms


@pytest.mark.parametrize("kind", nt.SCENES)
def test_scale_scenes_reach_their_target(kind):
    scene = nt.scene(kind, 20_000)
    assert 20_000 <= scene.n_atoms < 30_000


def test_atoms_are_qvf_structure_atoms():
    atom = nt.single_wall(5, 5, 5.0, end="hydrogen").atoms()[-1]
    assert atom["symbol"] == "H"
    assert atom["atomic_number"] == 1
    assert len(atom["position"]) == 3


# ── Interfaces ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def tube():
    return nt.single_wall(10, 10, 25.0, end="open")


def test_tube_frame_finds_axis_radius_and_open_ends(tube):
    frame = ib.tube_frame(tube.symbols, tube.positions)
    assert abs(frame.axis @ [0, 0, 1]) == pytest.approx(1.0)
    assert frame.radius == pytest.approx(tube.walls[0].diameter / 2, abs=0.01)
    assert len(frame.ends[0]) == len(frame.ends[1]) == 20


@pytest.mark.parametrize("facet", ib.FACETS)
def test_fcc_slab_layers_are_the_facet_spacing(facet):
    slab = ib.fcc_slab("Cu", facet, 8.0, 3)
    a = ib.FCC_METALS["Cu"]
    spacing = {"111": a / math.sqrt(3), "100": a / 2, "110": a / (2 * math.sqrt(2))}[facet]
    planes = np.unique(np.round(slab[:, 2], 3))
    assert len(planes) == 3
    assert np.diff(planes) == pytest.approx([spacing, spacing], abs=1e-3)
    d = np.linalg.norm(slab[:, None] - slab[None], axis=-1)
    np.fill_diagonal(d, np.inf)
    assert d.min() == pytest.approx(a / math.sqrt(2), abs=1e-6)


@pytest.mark.parametrize("site", ib.SITES)
@pytest.mark.parametrize("registry", ib.REGISTRIES)
def test_slab_contact_distance_and_registry(tube, site, registry):
    out = ib.add_slab(tube.symbols, tube.positions, metal="Cu", facet="111", site=site,
                      registry=registry, distance=2.4)
    metal = out.positions[out.partner]
    anchor = out.positions[out.contact[0]]
    d = np.linalg.norm(metal - anchor, axis=1)
    assert d.min() == pytest.approx(2.4, abs=1e-6)
    # How many metal atoms share that shortest distance names the site.
    expected = {"top": 1, "bridge": 2, "hollow": 3}[registry]
    assert int(np.sum(np.isclose(d, d.min(), atol=1e-3))) == expected
    carbons = out.positions[: len(tube.symbols)]
    assert np.linalg.norm(metal[:, None] - carbons[None], axis=-1).min() > 1.5
    assert out.symbols[out.partner[0]] == "Cu"


def test_default_distance_is_the_covalent_radius_sum(tube):
    out = ib.add_slab(tube.symbols, tube.positions, metal="Au")
    assert out.contact[2] == pytest.approx(ib.bond_distance("C", "Au"))


def test_too_short_a_hollow_distance_is_refused(tube):
    with pytest.raises(ValueError, match="distance"):
        ib.add_slab(tube.symbols, tube.positions, registry="hollow", distance=1.0)
    # Close enough to the site, but the rest of the end ring would touch the metal.
    with pytest.raises(ValueError, match="another tube atom"):
        ib.add_slab(tube.symbols, tube.positions, registry="hollow", distance=2.0)


def test_particle_touches_the_open_end(tube):
    out = ib.add_particle(tube.symbols, tube.positions, metal="Au", radius=6.0)
    assert len(out.partner) > 20
    assert out.contact[2] == pytest.approx(ib.bond_distance("C", "Au"), abs=1e-6)


def test_capped_tube_has_no_open_end_to_attach_to():
    capped = nt.single_wall(5, 5, 20.0, end="cap")
    with pytest.raises(ValueError, match="no open end"):
        ib.add_slab(capped.symbols, capped.positions)
    # The sidewall is still available.
    assert ib.add_slab(capped.symbols, capped.positions, site="sidewall").partner


def test_linker_is_an_aminophenyl_amide(tube):
    out = ib.add_linker(tube.symbols, tube.positions, site="end")
    symbols = [out.symbols[i] for i in out.partner]
    assert sorted(symbols) == sorted(["C"] * 7 + ["O", "N", "H"] + ["H"] * 4)
    lengths = _bond_lengths(out.positions[out.partner], 1.6)
    assert lengths.min() > 0.99


def test_linker_with_metal_bonds_its_aryl_carbon(tube):
    out = ib.add_linker(tube.symbols, tube.positions, site="sidewall", metal="Au")
    carbon, metal, distance = out.contact
    assert out.symbols[carbon] == "C"
    assert out.symbols[metal] == "Au"
    assert np.linalg.norm(out.positions[carbon] - out.positions[metal]) == pytest.approx(distance)


def test_unknown_options_are_refused(tube):
    with pytest.raises(ValueError):
        ib.add_slab(tube.symbols, tube.positions, metal="Fe")
    with pytest.raises(ValueError):
        ib.add_slab(tube.symbols, tube.positions, facet="211")
    with pytest.raises(ValueError):
        ib.build_partner(tube.symbols, tube.positions, "cluster")
    with pytest.raises(ValueError, match="no nanotube"):
        ib.tube_frame(["C"] * 3, np.zeros((3, 3)))
