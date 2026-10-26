"""Geometry and real exported frames for the shared Cartesian mode arrows."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from vibeview.animation import render_vibration_video
from vibeview.headless import headless_plotter
from vibeview.qvf import QVFReader
from vibeview.vibration_arrows import add_mode_arrows, arrow_vectors


def test_direction_amplitude_cutoff_and_periodic_origins():
    origins = np.array([[8., 0, 0], [0, 3, 0], [0, 0, 0]])
    displacement = np.array([[-2., 0, 0], [0, 1, 0], [0, 0, 0]])
    before = displacement.copy()
    indices, starts, vectors = arrow_vectors(origins, displacement, 2, .7)
    assert indices.tolist() == [0]
    np.testing.assert_allclose(starts, [[8, 0, 0]])
    np.testing.assert_allclose(vectors, [[-1.2, 0, 0]])
    np.testing.assert_array_equal(displacement, before)
    assert arrow_vectors(origins, displacement, 0)[0].size == 0
    with pytest.raises(ValueError, match="non-negative"):
        arrow_vectors(origins, displacement, cutoff=-1)


def test_mesh_endpoints_colour_and_thickness():
    with headless_plotter((240, 180)) as plotter:
        names = add_mode_arrows(plotter, np.array([[2., 3, 4]]), np.array([[-1., 0, 0]]),
                                amplitude=2, color="#ff0000", thickness=.03)
        actor = plotter.actors[names[0]]
        assert actor.prop.color.float_rgb == pytest.approx((1, 0, 0))
        bounds = actor.mapper.dataset.bounds
        assert bounds.x_min == pytest.approx(.8, abs=1e-6)
        assert bounds.x_max == pytest.approx(2, abs=1e-6)
        assert bounds.y_max - bounds.y_min == pytest.approx(.12, abs=1e-6)
        assert actor.mapper.dataset.n_cells > 0


def test_real_water_gif_contains_arrows_and_obeys_frames_fps_loop(tmp_path):
    fixture = Path(__file__).parents[1] / "docs/examples/orca-water/water-frequencies.qvf"
    with QVFReader(fixture) as reader:
        out = render_vibration_video(reader, tmp_path / "bend.gif", mode=6, n_frames=8,
                                     fps=10, size=(240, 180), format="gif", arrows=True,
                                     arrow_color="#ffb300", loop=False)
    assert out is not None
    with Image.open(out) as image:
        assert image.n_frames == 8
        assert image.info["duration"] == 100
        assert "loop" not in image.info
        pixels = np.asarray(image.convert("RGB"))
        gold = (pixels[:, :, 0] > 150) & (pixels[:, :, 1] > 70) & (pixels[:, :, 2] < 65)
        assert gold.sum() > 10
        first = pixels.copy()
        image.seek(2)
        assert np.any(np.asarray(image.convert("RGB")) != first)


def test_surface_anchor_preserves_vector_length_and_input_positions():
    positions = np.array([[2., 3, 4]])
    original = positions.copy()
    with headless_plotter((240, 180)) as plotter:
        names = add_mode_arrows(plotter, positions, np.array([[-1., 0, 0]]),
                                amplitude=.25, thickness=.02, atom_radii=np.array([.6]))
        bounds = plotter.actors[names[0]].mapper.dataset.bounds
        assert bounds.x_max == pytest.approx(1.38, abs=1e-6)
        assert bounds.x_min == pytest.approx(1.23, abs=1e-6)
        np.testing.assert_array_equal(positions, original)
        for radii in (np.array([np.nan]), np.array([-.1]), np.array([.1, .2])):
            with pytest.raises(ValueError, match="radii"):
                add_mode_arrows(plotter, positions, np.array([[-1., 0, 0]]), atom_radii=radii)


def test_display_radius_matches_representation():
    from vibeview.renderers.structure import atom_display_radius, cpk_radius
    assert atom_display_radius(6) == cpk_radius(6, .35)
    assert atom_display_radius(6, "space_filling") > atom_display_radius(6)
    assert atom_display_radius(6, "sticks_only") == 0
    assert atom_display_radius(6, "wireframe") == 0
