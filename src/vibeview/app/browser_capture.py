"""Export screenshot from the browser's frame (#116).

The live viewport is drawn by vtk.js in the browser, and part of what it
draws exists only there: the silhouette pass of #112 outlines isosurfaces
and ribbons, and the server renderer has no such pass. A server render
(``plotter.screenshot``) therefore lost those outlines. This module lets
Export screenshot take the frame the browser draws instead:

1. the server publishes a request in :data:`REQUEST_KEY`;
2. the page script resizes the vtk.js render window to the export size,
   draws every render pass, copies the canvas and puts the size back, all in
   one task: vtk.js creates its context with ``preserveDrawingBuffer:
   false``, so the pixels are gone once the task yields, and the restored
   live frame is drawn before the browser paints;
3. it uploads the PNG through the :data:`TRIGGER` trigger, and the export
   controller downsamples it, applies the transparent background and
   delivers it like a server render.

The export is the viewport's CSS size times the scale, drawn at twice that
and downsampled where the GPU's limits and :data:`FRAME_BUDGET` allow; past
them it is drawn without supersampling, and past those too it shrinks to the
largest frame the GPU draws. The silhouette pass is told the frame's pixels
per CSS pixel, so its line keeps its width relative to the figure. A
transparent export is drawn on white and keyed out on the server, as the
server render is.

The server render stays: it is the only path without a browser (``vibe-view
capture``, scripts), the fallback when a capture fails or is not answered,
and the one that draws ambient occlusion and OSPRay ray tracing.
"""

from __future__ import annotations

import base64
import binascii
import io
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vibeview.app.state import _ViewerUIState

#: The trame state key the server sets to ask the browser for a frame:
#: ``{"id", "scale", "white"}``, or ``None``.
REQUEST_KEY = "screenshot_request"

#: The trame state key the page script sets once it has a view to capture.
READY_KEY = "browser_capture_ready"

#: The trigger the page script answers a request with:
#: ``(request_id, png_data_url_or_None, info)``.
TRIGGER = "save_browser_screenshot"

#: Pixels in one captured frame, at most: a 2x-supersampled 4K figure fits.
FRAME_BUDGET = 1 << 25

#: Trigger argument that sends the browser's camera with an export click.
#: ``_vvClientCamera`` is the page-script helper in ``layout.py``; a Vue
#: template reaches it only as a bare underscored global.
CLIENT_CAMERA_ARG = "typeof _vvClientCamera === 'function' ? _vvClientCamera() : null"

#: Seconds to wait for the browser before rendering on the server instead.
ANSWER_TIMEOUT = 30.0

#: The dialog's render choice (``screenshot_source``).
SOURCE_OPTIONS: list[dict[str, str]] = [
    {"title": "Automatic", "value": "auto"},
    {"title": "Browser view, as shown", "value": "browser"},
    {"title": "Server render", "value": "server"},
]

_PNG_PREFIX = "data:image/png;base64,"


def screenshot_source(state: _ViewerUIState) -> tuple[str, str]:
    """``("browser" | "server", why)`` for an Export screenshot now.

    ``why`` is empty unless an explicit choice of the browser view cannot be
    met. *Automatic* takes the browser view unless an effect only the server
    draws is on: ray tracing always, ambient occlusion unless the browser is
    outlining an isosurface or ribbon, which the user sees and the server
    cannot draw.
    """
    choice = str(state.screenshot_source or "auto")
    if choice == "server":
        return "server", ""
    if not state.browser_capture_ready:
        why = "no browser view to capture" if choice == "browser" else ""
        return "server", why
    if choice == "browser":
        return "browser", ""
    if state.raytrace_enabled:
        return "server", ""
    if state.figure_outlines and state.silhouette_targets:
        return "browser", ""
    return ("server", "") if state.ssao_enabled else ("browser", "")


def decode_capture(data_url: Any) -> Any:
    """The PIL image of a captured ``data:image/png;base64,...`` URL.

    Raises :class:`ValueError` for anything but a PNG within
    :data:`FRAME_BUDGET`; the page script never sends a larger frame.
    """
    from PIL import Image, UnidentifiedImageError

    if not isinstance(data_url, str) or not data_url.startswith(_PNG_PREFIX):
        raise ValueError("not a PNG data URL")
    try:
        raw = base64.b64decode(data_url[len(_PNG_PREFIX) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"bad base64: {exc}") from exc
    try:
        image = Image.open(io.BytesIO(raw))
    except UnidentifiedImageError as exc:
        raise ValueError("unreadable image") from exc
    if image.format != "PNG":
        raise ValueError(f"not a PNG: {image.format}")
    if image.width * image.height > FRAME_BUDGET:
        raise ValueError(f"frame too large: {image.width}x{image.height}")
    image.load()
    return image


# Runs in the page. It answers each capture request in REQUEST_KEY with the
# frame vtk.js draws, through the TRIGGER trigger, and reports in READY_KEY
# that a view exists. `window.vvCapture.stats` is read by the served-browser
# test. The view lookup is the silhouette pass's (silhouette.py).
BROWSER_CAPTURE_JS = (
    r"""
typeof document !== 'undefined' && (function () {
  if (window.vvCapture) return;
  var REQUEST_KEY = '"""
    + REQUEST_KEY
    + r"""', READY_KEY = '"""
    + READY_KEY
    + r"""';
  var TRIGGER = '"""
    + TRIGGER
    + r"""', FRAME_BUDGET = """
    + str(FRAME_BUDGET)
    + r""";
  var stats = { captures: 0, last: null, error: null };
  var answered = {};

  function findView() {
    var refs = window.trame && window.trame.refs;
    if (!refs) return null;
    var names = Object.keys(refs);
    for (var i = 0; i < names.length; i++) {
      try {
        var view = refs[names[i]].$.provides.view;
        if (view && view.openglRenderWindow && view.renderer) return view;
      } catch (e) { /* not a vtk view */ }
    }
    return null;
  }

  // The largest frame side the context draws, its depth texture included.
  function maxSide(gl) {
    var dims = gl.getParameter(gl.MAX_VIEWPORT_DIMS);
    return Math.min(dims[0], dims[1], gl.getParameter(gl.MAX_RENDERBUFFER_SIZE),
                    gl.getParameter(gl.MAX_TEXTURE_SIZE));
  }

  // The export size for a scale of the view's CSS size, and its
  // supersampling: 2 where the frame fits, else 1; a frame past the
  // limits shrinks, keeping its shape.
  function plan(css, scale, side) {
    var width = Math.max(1, Math.round(css[0] * scale));
    var height = Math.max(1, Math.round(css[1] * scale));
    var fit = Math.min(1, side / width, side / height,
                       Math.sqrt(FRAME_BUDGET / (width * height)));
    if (fit < 1) {
      width = Math.max(1, Math.floor(width * fit));
      height = Math.max(1, Math.floor(height * fit));
    }
    var ssaa = 2 * Math.max(width, height) <= side && 4 * width * height <= FRAME_BUDGET
      ? 2 : 1;
    return { width: width, height: height, ssaa: ssaa, reduced: fit < 1 };
  }

  // Draw one width x height frame through every vtk.js pass and copy it, in
  // this task; then restore the live size and draw the live frame again.
  function drawFrame(view, width, height, white, pixelRatio) {
    var ogl = view.openglRenderWindow, renderer = view.renderer;
    var gl = ogl.getContext();
    var live = ogl.getSize().slice();
    var background = renderer.getBackground().slice();
    var silhouette = window.vvSilhouette;
    var outlineFrames = silhouette ? silhouette.stats.frames : 0;
    var copy = null, outlineRadius = null;
    try {
      if (white) renderer.setBackground(1, 1, 1, 1);
      if (silhouette) silhouette.setPixelRatio(pixelRatio);
      ogl.setSize(width, height);
      if (gl.drawingBufferWidth !== width || gl.drawingBufferHeight !== height) {
        throw new Error('the graphics card drew ' + gl.drawingBufferWidth + 'x' +
          gl.drawingBufferHeight + ' for ' + width + 'x' + height);
      }
      ogl.traverseAllPasses();
      if (silhouette && silhouette.stats.frames > outlineFrames) {
        outlineRadius = silhouette.stats.radius;
      }
      copy = document.createElement('canvas');
      copy.width = width;
      copy.height = height;
      copy.getContext('2d').drawImage(ogl.getCanvas(), 0, 0);
    } finally {
      if (silhouette) silhouette.setPixelRatio(null);
      renderer.setBackground(background);
      ogl.setSize(live[0], live[1]);
      ogl.traverseAllPasses();
    }
    return { canvas: copy, outlineRadius: outlineRadius };
  }

  function encode(canvas) {
    return new Promise(function (resolve, reject) {
      canvas.toBlob(function (blob) {
        if (!blob) {
          reject(new Error('the browser could not encode the PNG'));
          return;
        }
        var reader = new FileReader();
        reader.onload = function () { resolve(reader.result); };
        reader.onerror = function () { reject(reader.error); };
        reader.readAsDataURL(blob);
      }, 'image/png');
    });
  }

  function capture(request) {
    var view = findView();
    if (!view) return Promise.reject(new Error('no vtk.js view'));
    var element = view.container || view.openglRenderWindow.getCanvas();
    var rect = element.getBoundingClientRect();
    var css = [rect.width, rect.height];
    if (!(css[0] > 0 && css[1] > 0)) return Promise.reject(new Error('the view is hidden'));
    var p = plan(css, Number(request.scale) || 1, maxSide(view.openglRenderWindow.getContext()));
    // A context can refuse a size its limits allow (memory): retry once
    // without supersampling.
    var factors = p.ssaa > 1 ? [p.ssaa, 1] : [1];
    var frame = null, ssaa = 1, failure = null;
    for (var i = 0; i < factors.length && !frame; i++) {
      try {
        frame = drawFrame(view, p.width * factors[i], p.height * factors[i],
                          Boolean(request.white), factors[i] * p.width / css[0]);
        ssaa = factors[i];
      } catch (error) {
        failure = error;
      }
    }
    if (!frame) return Promise.reject(failure);
    var info = {
      width: p.width, height: p.height, ssaa: ssaa, reduced: p.reduced,
      outlineRadius: frame.outlineRadius,
    };
    return encode(frame.canvas).then(function (dataUrl) {
      return { dataUrl: dataUrl, info: info };
    });
  }

  function answer(request) {
    if (!request || !request.id || answered[request.id]) return;
    answered[request.id] = true;
    var trame = window.trame;
    capture(request).then(function (result) {
      stats.captures++;
      stats.last = result.info;
      return trame.trigger(TRIGGER, [request.id, result.dataUrl, result.info]);
    }, function (error) {
      stats.error = String(error && error.message || error);
      return trame.trigger(TRIGGER, [request.id, null, { error: stats.error }]);
    }).catch(function (error) {
      stats.error = String(error && error.message || error);
    });
  }

  function connect() {
    var trame = window.trame;
    if (!trame || !trame.state || !findView()) return false;
    trame.state.watch([REQUEST_KEY], answer);
    if (!trame.state.get(READY_KEY)) trame.state.set(READY_KEY, true);
    return true;
  }

  // The view mounts after this script runs; look for it until it exists.
  var attempts = 0;
  var timer = setInterval(function () {
    if (connect() || ++attempts > 600) clearInterval(timer);
  }, 250);
  window.vvCapture = { stats: stats, capture: capture };
})();
"""
)
