"""Export screenshot from the browser's frame (#116): which render the export
takes, and how the server handles the browser's answer. The capture itself
runs in vtk.js; the served-browser lane in ``test_design_refresh.py`` reads
back the exported image.
"""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from vibeview.app import browser_capture
from vibeview.app.browser_capture import (
    READY_KEY,
    REQUEST_KEY,
    TRIGGER,
    decode_capture,
    screenshot_source,
)

H2 = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"


def _png_url(image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _image(data_url: str):
    from PIL import Image

    return Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))


def _state(**overrides):
    values = {
        "screenshot_source": "auto",
        READY_KEY: True,
        "raytrace_enabled": False,
        "figure_outlines": False,
        "silhouette_targets": [],
        "ssao_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestSource:
    def test_without_a_browser_the_server_renders(self):
        assert screenshot_source(_state(**{READY_KEY: False})) == ("server", "")

    def test_an_explicit_browser_choice_says_why_it_cannot(self):
        source, why = screenshot_source(
            _state(**{READY_KEY: False, "screenshot_source": "browser"})
        )
        assert source == "server" and "no browser view" in why

    def test_explicit_choices_win(self):
        assert screenshot_source(_state(screenshot_source="server"))[0] == "server"
        assert screenshot_source(_state(screenshot_source="browser", ssao_enabled=True)) == (
            "browser",
            "",
        )

    def test_auto_takes_the_browser_for_surface_outlines(self):
        outlined = {"figure_outlines": True, "silhouette_targets": [[10, 0, 1, 0, 1, 0, 1]]}
        assert screenshot_source(_state(**outlined))[0] == "browser"
        # Outlines the user sees win over occlusion only an export shows.
        assert screenshot_source(_state(**outlined, ssao_enabled=True))[0] == "browser"
        # Ray tracing exists only on the server.
        assert screenshot_source(_state(**outlined, raytrace_enabled=True))[0] == "server"

    def test_auto_keeps_ambient_occlusion_on_the_server(self):
        assert screenshot_source(_state(ssao_enabled=True))[0] == "server"
        assert screenshot_source(_state(figure_outlines=True, ssao_enabled=True))[0] == "server"
        assert screenshot_source(_state())[0] == "browser"


class TestDecode:
    def test_a_png_round_trips(self):
        from PIL import Image

        image = decode_capture(_png_url(Image.new("RGB", (7, 5), "red")))
        assert image.size == (7, 5) and image.format == "PNG"

    @pytest.mark.parametrize(
        "data_url",
        [None, "", "data:image/jpeg;base64,AAAA", "data:image/png;base64,***", "plain"],
    )
    def test_anything_else_is_refused(self, data_url):
        with pytest.raises(ValueError):
            decode_capture(data_url)

    def test_a_jpeg_behind_a_png_prefix_is_refused(self):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buf, format="JPEG")
        with pytest.raises(ValueError, match="not a PNG"):
            decode_capture("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())

    def test_a_frame_past_the_budget_is_refused(self, monkeypatch):
        from PIL import Image

        monkeypatch.setattr(browser_capture, "FRAME_BUDGET", 10)
        with pytest.raises(ValueError, match="too large"):
            decode_capture(_png_url(Image.new("RGB", (4, 4))))


class TestViewer:
    @pytest.fixture
    def app(self, tmp_path):
        """The orbital in the Publication look: outlines on, a lobe to outline.
        A copy of the archive, so the export saved beside it is cleaned up."""
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        path = tmp_path / H2.name
        path.write_bytes(H2.read_bytes())
        app = create_app(QVFReader(path))
        app.controller.activate_section("wf")
        app.state.wf_selected_mo = app.state.wf_mo_rows[0]["value"]
        app.controller.render_mo(app.state.wf_selected_mo)
        app.controller.apply_view_preset("publication")
        assert app.state.silhouette_targets
        return app

    @staticmethod
    def _frame(width, height):
        """A white frame with a black line, as the browser draws it."""
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (width, height), "white")
        ImageDraw.Draw(image).line([(0, height // 2), (width, height // 2)], fill="black", width=4)
        return image

    def _answer(self, app, request_id, data_url, info=None):
        app.controller.trigger_fn(TRIGGER)(request_id, data_url, info)

    def test_the_answer_trigger_is_registered(self, app):
        assert TRIGGER in app.controller._triggers

    def test_without_a_browser_the_export_renders_on_the_server(self, app):
        app.controller.save_screenshot()
        assert app.state.screenshot_data and app.state[REQUEST_KEY] is None
        assert "server render" in app.state.status_message

    def test_a_browser_frame_is_downsampled_and_delivered(self, app, tmp_path):
        state = app.state
        state[READY_KEY] = True
        state.screenshot_scale = 2
        app.controller.save_screenshot()
        request = state[REQUEST_KEY]
        assert request["scale"] == 2 and request["white"] is False
        assert state.screenshot_data is None, "nothing is exported before the browser answers"
        self._answer(
            app,
            request["id"],
            _png_url(self._frame(400, 300)),
            {"width": 200, "height": 150, "ssaa": 2},
        )
        assert state[REQUEST_KEY] is None
        image = _image(state.screenshot_data)
        assert image.size == (200, 150)
        assert "browser view, SSAA 2x" in state.status_message
        assert list(tmp_path.glob("*_screenshot_*.png")), "not saved beside the archive"
        history = state.download_history
        assert history and history[0]["name"] == "Screenshot"

    def test_a_transparent_export_keys_out_the_white(self, app):
        state = app.state
        state[READY_KEY] = True
        state.screenshot_transparent = True
        app.controller.save_screenshot()
        request = state[REQUEST_KEY]
        assert request["white"] is True
        self._answer(app, request["id"], _png_url(self._frame(60, 40)), {"width": 60, "height": 40})
        image = _image(state.screenshot_data)
        assert image.mode == "RGBA" and image.size == (60, 40)
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((30, 20))[3] == 255

    def test_a_stale_answer_is_dropped(self, app):
        state = app.state
        state[READY_KEY] = True
        app.controller.save_screenshot()
        first = state[REQUEST_KEY]["id"]
        app.controller.save_screenshot()
        second = state[REQUEST_KEY]["id"]
        assert first != second
        self._answer(app, first, _png_url(self._frame(20, 20)), {"width": 20, "height": 20})
        assert state.screenshot_data is None and state[REQUEST_KEY]["id"] == second
        self._answer(app, "never-asked", _png_url(self._frame(20, 20)))
        assert state.screenshot_data is None

    def test_a_failed_capture_falls_back_to_the_server(self, app):
        state = app.state
        state[READY_KEY] = True
        app.controller.save_screenshot()
        self._answer(app, state[REQUEST_KEY]["id"], None, {"error": "context lost"})
        assert state.screenshot_data and state[REQUEST_KEY] is None
        assert "server render" in state.status_message
        assert "context lost" in state.status_message

    def test_an_unanswered_request_falls_back_to_the_server(self, app, monkeypatch):
        monkeypatch.setattr(browser_capture, "ANSWER_TIMEOUT", 0.01)
        state = app.state
        state[READY_KEY] = True

        async def drive():
            app.controller.save_screenshot()
            request_id = state[REQUEST_KEY]["id"]
            await asyncio.sleep(0.2)
            return request_id

        request_id = asyncio.run(drive())
        assert state.screenshot_data and state[REQUEST_KEY] is None
        assert "did not answer" in state.status_message
        # The browser's late answer changes nothing.
        state.screenshot_data = None
        self._answer(app, request_id, _png_url(self._frame(20, 20)))
        assert state.screenshot_data is None

    def test_the_server_choice_skips_the_browser(self, app):
        state = app.state
        state[READY_KEY] = True
        state.screenshot_source = "server"
        app.controller.save_screenshot()
        assert state[REQUEST_KEY] is None and state.screenshot_data
        assert "server render" in state.status_message


@pytest.fixture(scope="module")
def served():
    pytest.importorskip("trame")
    from tests._served import served_viewer

    return served_viewer()


class TestServedPage:
    def test_the_capture_script_ships_in_the_page(self, served):
        assert "window.vvCapture = {" in served
        assert f"var REQUEST_KEY = '{REQUEST_KEY}'" in served
        assert "ogl.traverseAllPasses();" in served

    def test_the_silhouette_pass_takes_the_frame_pixel_ratio(self, served):
        assert "setPixelRatio: function (ratio)" in served
        assert "var dpr = pixelRatio || window.devicePixelRatio || 1;" in served

    def test_the_dialog_offers_the_render_choice(self, served):
        assert 'v-model="screenshot_source"' in served
