"""Tests for the terminal rasterizer and the braille canvas.

The rasterizer has no visual regression baseline to lean on, so these assert
the properties a picture must have: the braille bit layout matches the
Unicode dot numbering, the depth buffer resolves occlusion the right way
round regardless of draw order, a sphere is shaded rather than flat, and the
camera frames what it was asked to frame.
"""

from __future__ import annotations

import numpy as np
import pytest

from vibeview.tui import braille
from vibeview.tui import scene as scenelib
from vibeview.tui.raster import Canvas, shade

# ── braille packing ───────────────────────────────────────────────────────


def test_braille_bit_layout_matches_unicode_dot_numbering():
    """Each sub-cell must map to its historic braille dot bit.

    The fourth row is 0x40/0x80 rather than a continuation of the sequence;
    getting that wrong renders a plausible-looking but vertically scrambled
    image, which no smoke test would catch.
    """
    expected = {
        (0, 0): 0x01,
        (1, 0): 0x02,
        (2, 0): 0x04,
        (3, 0): 0x40,
        (0, 1): 0x08,
        (1, 1): 0x10,
        (2, 1): 0x20,
        (3, 1): 0x80,
    }
    for (row, col), bit in expected.items():
        pixels = np.zeros((4, 2, 3), dtype=np.uint8)
        covered = np.zeros((4, 2), dtype=bool)
        covered[row, col] = True
        pixels[row, col] = (255, 255, 255)
        codepoints, _fg, _bg, filled = braille.to_cells(pixels, covered)
        assert filled[0, 0]
        assert int(codepoints[0, 0]) == 0x2800 + bit


def test_braille_cell_colour_is_the_mean_of_covered_pixels_only():
    pixels = np.zeros((4, 2, 3), dtype=np.uint8)
    covered = np.zeros((4, 2), dtype=bool)
    pixels[0, 0] = (200, 0, 0)
    pixels[1, 0] = (100, 0, 0)
    covered[0, 0] = covered[1, 0] = True
    # An uncovered bright pixel must not drag the average.
    pixels[2, 1] = (255, 255, 255)
    _cp, fg, _bg, _filled = braille.to_cells(pixels, covered)
    assert tuple(fg[0, 0]) == (150, 0, 0)


def test_empty_cell_is_blank_not_a_dotless_braille_glyph():
    pixels = np.zeros((8, 4, 3), dtype=np.uint8)
    covered = np.zeros((8, 4), dtype=bool)
    text = braille.to_plain(pixels, covered)
    assert text.strip() == ""


def test_half_block_mode_carries_two_independent_colours():
    pixels = np.zeros((2, 1, 3), dtype=np.uint8)
    pixels[0, 0] = (10, 20, 30)
    pixels[1, 0] = (200, 210, 220)
    covered = np.ones((2, 1), dtype=bool)
    codepoints, fg, bg, _filled = braille.to_cells(pixels, covered, mode="half")
    assert int(codepoints[0, 0]) == 0x2580
    assert tuple(fg[0, 0]) == (10, 20, 30)
    assert tuple(bg[0, 0]) == (200, 210, 220)


# ── rasterizer ────────────────────────────────────────────────────────────


def test_depth_buffer_keeps_the_nearer_sphere():
    """Occlusion must not depend on draw order."""
    near = np.array([[0.0, 0.0, 5.0]])
    far = np.array([[0.0, 0.0, 9.0]])
    red = np.array([[255.0, 0.0, 0.0]])
    blue = np.array([[0.0, 0.0, 255.0]])

    def centre_colour(first, first_colour, second, second_colour):
        canvas = Canvas(64, 64)
        canvas.draw_spheres(first, np.array([1.0]), first_colour)
        canvas.draw_spheres(second, np.array([1.0]), second_colour)
        return canvas.pixels[32, 32].copy()

    far_first = centre_colour(far, blue, near, red)
    near_first = centre_colour(near, red, far, blue)
    assert far_first[0] > far_first[2], "near red sphere should win"
    np.testing.assert_array_equal(far_first, near_first)


def test_sphere_is_shaded_not_flat():
    canvas = Canvas(80, 80)
    canvas.draw_spheres(
        np.array([[0.0, 0.0, 6.0]]), np.array([1.5]), np.array([[200.0, 200.0, 200.0]])
    )
    lit = canvas.pixels[canvas.covered]
    assert len(lit) > 100
    # A flat fill would have a single luminance; Blinn-Phong must spread it.
    assert lit[:, 0].max() - lit[:, 0].min() > 60


def test_geometry_behind_the_camera_is_dropped_not_smeared():
    canvas = Canvas(48, 48)
    canvas.draw_spheres(
        np.array([[0.0, 0.0, -3.0]]), np.array([1.0]), np.array([[255.0, 0.0, 0.0]])
    )
    assert not canvas.covered.any()


def test_shade_expects_camera_facing_normals():
    facing = shade(np.array([[0.0, 0.0, -1.0]]), np.array([[200.0, 200.0, 200.0]]))
    away = shade(np.array([[0.0, 0.0, 1.0]]), np.array([[200.0, 200.0, 200.0]]))
    assert facing[0, 0] > away[0, 0]


def test_mesh_is_two_sided():
    """A back-facing triangle still renders — an isosurface may be an open sheet."""
    vertices = np.array([[-1.0, -1.0, 6.0], [1.0, -1.0, 6.0], [0.0, 1.0, 6.0]])
    for winding in ([[0, 1, 2]], [[0, 2, 1]]):
        canvas = Canvas(64, 64)
        canvas.draw_mesh(
            vertices, np.array(winding), np.tile([0.0, 0.0, -1.0], (3, 1)), (255, 140, 0)
        )
        assert canvas.covered.sum() > 20, f"winding {winding} vanished"


def test_composite_is_order_independent_within_a_batch():
    canvas = Canvas(16, 16)
    px = np.array([8, 8, 8])
    py = np.array([8, 8, 8])
    pz = np.array([9.0, 2.0, 5.0])
    rgb = np.array([[10, 10, 10], [90, 90, 90], [50, 50, 50]], dtype=np.uint8)
    canvas.composite(px, py, pz, rgb)
    assert tuple(canvas.pixels[8, 8]) == (90, 90, 90)
    assert canvas.zbuf[8, 8] == pytest.approx(2.0)


# ── camera ────────────────────────────────────────────────────────────────


def _fit_and_project(scene, cols=80, rows=24):
    canvas = Canvas(*braille.pixel_size(cols, rows))
    camera = scenelib.fit_camera(scene, canvas)
    xy, z = canvas.project(camera.to_camera(scene.positions), camera.fov)
    return canvas, xy, z


def test_fit_camera_frames_a_flat_sheet_without_clipping_or_wasting_space():
    """A bounding-sphere fit would leave a flat system at half size."""
    grid = np.mgrid[0:6, 0:6].reshape(2, -1).T.astype(float)
    positions = np.column_stack([grid[:, 0], grid[:, 1], np.zeros(len(grid))])
    scene = scenelib.Scene(
        positions=positions,
        radii=np.full(len(positions), 0.3),
        colors=np.tile([200.0, 200.0, 200.0], (len(positions), 1)),
    )
    canvas, xy, z = _fit_and_project(scene)
    assert (z > 0).all()
    assert (xy[:, 0] >= 0).all() and (xy[:, 0] < canvas.width).all()
    assert (xy[:, 1] >= 0).all() and (xy[:, 1] < canvas.height).all()
    # And it must actually use the viewport, not float in the middle of it.
    span = xy[:, 1].max() - xy[:, 1].min()
    assert span > 0.55 * canvas.height


def test_fit_camera_handles_a_single_atom():
    scene = scenelib.Scene(
        positions=np.zeros((1, 3)),
        radii=np.array([1.0]),
        colors=np.array([[200.0, 200.0, 200.0]]),
    )
    canvas, xy, z = _fit_and_project(scene)
    assert np.isfinite(xy).all()
    assert z[0] > 0


# ── cell grid ─────────────────────────────────────────────────────────────


def test_text_overwrites_blitted_pixels():
    grid = braille.CellGrid(20, 4)
    pixels = np.full((16, 40, 3), 200, dtype=np.uint8)
    grid.blit(pixels, np.ones((16, 40), dtype=bool))
    grid.text(1, 2, "C12")
    assert grid.to_plain().splitlines()[1][2:5] == "C12"


def test_row_runs_compress_constant_style():
    grid = braille.CellGrid(30, 1)
    grid.text(0, 0, "aaaa", (10, 20, 30))
    runs = grid.row_runs(0)
    assert runs[0][0] == "aaaa"
    assert runs[0][1] == (10, 20, 30)


