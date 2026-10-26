"""Mode arrows and mode export follow the browser, not stale server state (#122).

Served-browser acceptance found two defects:

* the arrow controls triggered the redraw with no arguments, and the trigger
  reaches the server before the v-model state, so the redraw used the
  previous setting: the switch showed arrows when off and none when on;
* **Export selected mode** rendered the server's camera, which follows the
  browser only on vtk.js ``EndAnimation``. That event fires when the
  interactor's animation-request count returns to zero, so one request left
  open silenced every later drag and the GIF showed the view from before it.

The controls now send their values, and the export clicks send the browser
camera.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vibeview.qvf import QVFReader
from vibeview.vibration_arrows import ARROW_PREFIX

WATER = Path(__file__).parents[1] / "docs/examples/orca-water/water-frequencies.qvf"


@pytest.fixture
def water_app(monkeypatch):
    """The browser app on the water frequency archive, Vibrations shown."""
    import vibeview.app.helpers as helpers
    from vibeview.app import create_app

    made = []
    create = helpers.new_plotter

    def record(*args, **kwargs):
        plotter = create(*args, **kwargs)
        made.append(plotter)
        return plotter

    monkeypatch.setattr(helpers, "new_plotter", record)
    reader = QVFReader(WATER)
    server = create_app([reader])
    server.controller.view_update = lambda *a, **k: None
    server.controller.activate_section("orca_vibrations")
    yield server, made[-1]
    reader.close()
    for plotter in made:
        plotter.close()


def _arrows(plotter):
    return [name for name in plotter.actors if str(name).startswith(ARROW_PREFIX)]


def test_the_arrow_switch_draws_what_the_client_sent_not_stale_state(water_app):
    server, plotter = water_app
    state, ctrl = server.state, server.controller

    state.vibration_arrows = False  # the v-model update has not arrived yet
    ctrl.vibration_arrows_changed(True, "#ff0000", 0.0, 0.04)
    assert state.vibration_arrows is True
    assert state.vibration_arrow_color == "#ff0000"
    assert state.vibration_arrow_thickness == pytest.approx(0.04)
    assert len(_arrows(plotter)) == 3

    state.vibration_arrows = True  # stale again, the other way
    ctrl.vibration_arrows_changed(False, "#ff0000", 0.0, 0.04)
    assert state.vibration_arrows is False
    assert _arrows(plotter) == []


def test_a_junk_slider_value_keeps_the_previous_setting(water_app):
    server, _ = water_app
    server.state.vibration_arrow_cutoff = 0.02
    server.controller.vibration_arrows_changed(True, None, "not a number", None)
    assert server.state.vibration_arrow_cutoff == pytest.approx(0.02)


def test_mode_export_renders_the_camera_sent_with_the_click(water_app, monkeypatch):
    import vibeview.animation as animation

    server, plotter = water_app
    seen = {}

    def fake_render(reader, output, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(animation, "render_vibration_video", fake_render)
    before = plotter.camera_position
    client = [[0.5, -6.0, 1.0], [0.4, 0.0, 0.36], [0.0, 0.0, 1.0], 1.0]
    server.controller.export_selected_mode(client)

    sent = np.asarray([list(v) for v in seen["camera_position"]])
    np.testing.assert_allclose(sent, client[:3])
    assert not np.allclose(sent, [list(v) for v in before])


def test_an_export_without_a_browser_camera_keeps_the_server_camera(water_app, monkeypatch):
    import vibeview.animation as animation

    server, plotter = water_app
    seen = {}
    monkeypatch.setattr(
        animation, "render_vibration_video", lambda r, o, **kw: seen.update(kw)
    )
    before = [list(v) for v in plotter.camera_position]
    server.controller.export_selected_mode(None)
    np.testing.assert_allclose([list(v) for v in seen["camera_position"]], before)


def test_the_controls_send_their_values_and_the_camera():
    from trame.app import get_server
    from trame.widgets import html

    from vibeview.app.controls import _build_controls

    server = get_server("issue122-controls", client_type="vue3")
    with html.Div(trame_server=server) as root:
        _build_controls(server, server.controller)
    markup = root.html
    assert "[$event, vibration_arrow_color, vibration_arrow_cutoff, " in markup
    assert "[vibration_arrows, vibration_arrow_color, vibration_arrow_cutoff, $event]" in markup
    assert "_vvClientCamera()" in markup
