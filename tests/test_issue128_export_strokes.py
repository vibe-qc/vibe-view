"""Server screenshots keep wireframe maps and meshes visible (#128).

Served-browser acceptance exported a mesh-style experimental map from the
screenshot dialog (scale 2, server render): the map was absent. The export
renders at scale x 2 (supersampling) times the window and downsamples, and
line width is in render pixels, so a 1 px wireframe shrank to a quarter
pixel and faded out. Lines and points are now widened by the magnification
for the capture.
"""

from __future__ import annotations

import base64
import io

import numpy as np
import pytest
from PIL import Image

from vibeview.app.exports import _magnified_strokes
from vibeview.converters import xyz_to_qvf
from vibeview.experimental_map import map_to_qvf
from vibeview.qvf import QVFReader

WATER = b"3\nwater\nO 0 0 0\nH 0.757 0.586 0\nH -0.757 0.586 0\n"


def _write_map(path):
    """A Gaussian density on the water atoms, true header statistics."""
    atoms = [(8, 0, 0, 0), (1, 0.757, 0.586, 0), (1, -0.757, 0.586, 0)]
    n, h = 40, 0.25
    o = np.array([-5.0, -5.0, -5.0])
    z, y, x = np.meshgrid(*(o[i] + h * np.arange(n) for i in (2, 1, 0)), indexing="ij")
    rho = sum(zn * np.exp(-((x - ax) ** 2 + (y - ay) ** 2 + (z - az) ** 2) / 0.245)
              for zn, ax, ay, az in atoms).astype("<f4")
    header = bytearray(1024)
    ints, floats = np.frombuffer(header, "<i4"), np.frombuffer(header, "<f4")
    ints[0:3], ints[3], ints[7:10], ints[16:19] = n, 2, n, [1, 2, 3]
    floats[10:13], floats[13:16] = n * h, 90
    floats[19:22] = rho.min(), rho.max(), rho.mean()
    floats[49:52], floats[54] = o, rho.std()
    header[208:212], header[212:214] = b"MAP ", b"DA"
    path.write_bytes(bytes(header) + rho.tobytes())


def _steel_blue(image):
    a = np.asarray(image.convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return float(((b > r + 35) & (g > r + 15) & (b > 80)).mean())


@pytest.fixture
def map_app(tmp_path, monkeypatch):
    import vibeview.app.helpers as helpers
    from vibeview.app import create_app

    made = []
    create = helpers.new_plotter

    def record(*args, **kwargs):
        plotter = create(*args, **kwargs)
        made.append(plotter)
        return plotter

    monkeypatch.setattr(helpers, "new_plotter", record)
    monkeypatch.setattr(helpers, "_save_screenshot_disk", lambda *a, **k: None)
    model, density = tmp_path / "water.qvf", tmp_path / "water-map.qvf"
    model.write_bytes(xyz_to_qvf(WATER).getvalue())
    _write_map(tmp_path / "water.mrc")
    density.write_bytes(map_to_qvf(tmp_path / "water.mrc").getvalue())
    readers = [QVFReader(model), QVFReader(density)]
    server = create_app(readers)
    server.controller.view_update = lambda *a, **k: None
    yield server, made[-1]
    for reader in readers:
        reader.close()
    for plotter in made:
        plotter.close()


def _server_shot(server, scale):
    state = server.state
    state.screenshot_source, state.screenshot_scale = "server", scale
    state.screenshot_transparent = False
    server.controller.save_screenshot()
    data = state.screenshot_data.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(data)))


def test_a_mesh_map_survives_a_magnified_server_screenshot(map_app):
    server, plotter = map_app
    state = server.state
    server.controller.expmap_refresh()
    state.expmap_style, state.expmap_level = "mesh", 3.0
    state.expmap_enabled = True
    server.controller.expmap_apply()
    assert "experimental-map" in plotter.actors

    small = _steel_blue(_server_shot(server, 1))   # rendered at 2x
    large = _steel_blue(_server_shot(server, 2))   # rendered at 4x
    assert small > 0.002
    # The same picture at twice the size covers the same share of it; the
    # old code lost about nine tenths of the map between these two.
    assert large == pytest.approx(small, rel=0.35)


def test_stroke_widths_are_restored_after_the_capture(map_app):
    _, plotter = map_app
    actor = next(a for a in plotter.renderer.actors.values() if a.IsA("vtkActor"))
    prop = actor.GetProperty()
    prop.SetLineWidth(1.5)
    prop.SetPointSize(3.0)
    with _magnified_strokes(plotter, 4):
        assert prop.GetLineWidth() == pytest.approx(6.0)
        assert prop.GetPointSize() == pytest.approx(12.0)
    assert prop.GetLineWidth() == pytest.approx(1.5)
    assert prop.GetPointSize() == pytest.approx(3.0)
