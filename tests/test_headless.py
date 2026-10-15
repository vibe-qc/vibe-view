"""vibeview.headless is the one off-screen scene every headless render uses."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vibeview import headless
from vibeview.qvf import QVFReader

pytestmark = pytest.mark.usefixtures("water_qvf")


def _background(plotter) -> tuple[float, float, float]:
    return tuple(round(float(c), 3) for c in plotter.background_color.float_rgb)


def test_new_plotter_is_off_screen_on_the_dark_theme() -> None:
    plotter = headless.new_plotter((320, 200))
    try:
        assert plotter.off_screen
        assert list(plotter.window_size) == [320, 200]
        assert _background(plotter) == (0.102, 0.102, 0.18)
    finally:
        plotter.close()


def test_headless_plotter_closes_itself_even_on_error() -> None:
    with pytest.raises(RuntimeError), headless.headless_plotter() as plotter:
        raise RuntimeError("boom")
    assert plotter._closed


def test_structure_section_and_add_structure(water_qvf: Path) -> None:
    with QVFReader(water_qvf) as reader:
        assert headless.structure_section(reader) is not None
        with headless.headless_plotter((200, 150)) as plotter:
            assert headless.add_structure(plotter, reader)
            assert plotter.renderer.actors


def test_add_structure_reports_a_missing_structure(tmp_path: Path) -> None:
    from .conftest import build_qvf

    path = build_qvf(tmp_path / "empty.qvf", [])
    with QVFReader(path) as reader, headless.headless_plotter((200, 150)) as plotter:
        assert headless.structure_section(reader) is None
        assert not headless.add_structure(plotter, reader)
        assert headless.render_structure_frame(reader, size=(200, 150)) is False


def test_render_structure_frame_writes_a_png_or_returns_an_array(
    water_qvf: Path, tmp_path: Path
) -> None:
    with QVFReader(water_qvf) as reader:
        image = headless.render_structure_frame(reader, size=(200, 150))
        assert isinstance(image, np.ndarray)
        assert image.shape[:2] == (150, 200)
        out = tmp_path / "water.png"
        assert headless.render_structure_frame(reader, out, size=(200, 150)) is True
        assert out.stat().st_size > 0


def test_displaced_positions_move_the_atoms(water_qvf: Path) -> None:
    with QVFReader(water_qvf) as reader:
        still = headless.render_structure_frame(reader, size=(200, 150))
        moved = headless.render_structure_frame(
            reader,
            size=(200, 150),
            positions=[[0.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, -3.0, 0.0]],
        )
    assert isinstance(still, np.ndarray) and isinstance(moved, np.ndarray)
    assert not np.array_equal(still, moved)


def test_thumbnails_share_the_capture_theme(water_qvf: Path) -> None:
    """A Jupyter image is the capture image: same background, same framing."""
    from vibeview.jupyter import qvf_to_image

    png = qvf_to_image(str(water_qvf), width=200, height=150)
    with QVFReader(water_qvf) as reader:
        array = headless.render_structure_frame(reader, size=(200, 150))
    assert isinstance(array, np.ndarray)
    import io

    from PIL import Image

    thumb = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
    assert thumb.shape == array.shape[:2] + (3,)
    # Corner pixel is the dark vibe-view background, not white.
    assert tuple(thumb[0, 0]) == (26, 26, 46)


def test_signed_isosurfaces_use_the_phase_colours() -> None:
    import pyvista as pv

    with headless.headless_plotter((100, 100)) as plotter:
        headless.add_signed_isosurfaces(plotter, pv.Sphere(), pv.Cube(), opacity=0.5, name="lobe")
        assert set(plotter.renderer.actors) == {"lobe_pos", "lobe_neg"}
        headless.add_signed_isosurfaces(plotter, None, pv.PolyData(), opacity=0.5, name="none")
        assert "none_pos" not in plotter.renderer.actors
        assert "none_neg" not in plotter.renderer.actors
