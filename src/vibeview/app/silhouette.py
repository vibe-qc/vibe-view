"""Client-side silhouette pass: outlines for isosurfaces and ribbons (#112).

The figure outline of #68 is an inverted hull, geometry the server adds
around every atom and bond, so it reaches the vtk.js client and stays right
as the client rotates. A hull cannot outline an isosurface or a ribbon: their
silhouettes and folds change with the view. This module outlines those in the
browser, with an extra vtk.js render pass that runs after vtk.js's own:

1. it renders the depth of the outlined actors alone into an offscreen
   framebuffer, hiding everything else for that one pass;
2. a full-screen fragment shader marks a pixel as an edge where that depth
   meets the background (the silhouette) or has a large second difference
   (a fold in front of another part of the surface, or a crease), the
   screen-space approach of Saito and Takahashi, "Comprehensible rendering
   of 3-D shapes", SIGGRAPH 1990;
3. the edges are drawn black over the finished frame with the depth of the
   nearest outlined surface, depth-tested against the scene, so an atom in
   front of a lobe hides that part of its outline.

A translucent surface is drawn opaque for that depth render only, so it is
outlined too while the scene keeps its transparency.

The client cannot see the server's actor names, so the server publishes a
signature for every actor it wants outlined in :data:`STATE_KEY`: its point
count and bounds, which the client matches against its mapper inputs. The
pass runs only while ``figure_outlines`` is on and needs WebGL2 for depth
textures; without it the hulls remain the only outline. Headless captures
render on the server, which keeps the hulls only; the browser's screenshot
export draws its frame through this pass (:mod:`vibeview.app.browser_capture`),
at a pixel ratio it sets so the line scales with the export.
"""

from __future__ import annotations

from typing import Any

from vibeview.app.scene import _Actors

#: The trame state key holding the signatures of the actors to outline.
STATE_KEY = "silhouette_targets"

#: Actor-name prefixes the pass outlines: stored and computed isosurfaces,
#: Fermi surfaces, trajectory isosurfaces and cartoon ribbons.
TARGET_PREFIXES: tuple[str, ...] = (
    _Actors.VOLUME,
    _Actors.MO_SURFACE,
    _Actors.FERMI,
    _Actors.TRAJ_VOLUME,
    _Actors.CARTOON,
)

#: Prefixes inside those families that are not surfaces: a section drawn by
#: direct volume rendering, and the figure-outline hulls themselves.
_EXCLUDED_PREFIXES: tuple[str, ...] = (_Actors.VOLUME_RENDER, _Actors.OUTLINE)


def silhouette_targets(plotter: Any) -> list[list[float]]:
    """``[n_points, xmin, xmax, ymin, ymax, zmin, zmax]`` per outlined actor.

    Visible surface actors with any opacity: the pass draws a translucent one
    opaque in its own depth render, since vtk.js writes no depth for it in the
    scene. Sorted, so an unchanged scene gives an identical list and no state
    update.
    """
    targets: list[list[float]] = []
    for name, actor in list(getattr(plotter, "actors", {}).items()):
        label = str(name)
        if not label.startswith(TARGET_PREFIXES) or label.startswith(_EXCLUDED_PREFIXES):
            continue
        try:
            if not actor.GetVisibility() or actor.GetProperty().GetOpacity() <= 0.0:
                continue
            data = actor.GetMapper().GetInput()
        except AttributeError:  # a volume or an assembly: not a surface actor
            continue
        if data is None or not data.IsA("vtkPolyData") or data.GetNumberOfPoints() == 0:
            continue
        targets.append([float(data.GetNumberOfPoints()), *(float(b) for b in data.GetBounds())])
    return sorted(targets)


def publish_silhouette_targets(server_state: Any, plotter: Any) -> bool:
    """Publish the outline targets before a scene push; True if they changed.

    ``server_state`` is the trame state itself, not the UI-state protocol the
    module-level helpers take. Assigned inside ``with server_state`` so the
    update is flushed from any thread that pushes the view, not only from a
    controller callback.
    """
    targets = silhouette_targets(plotter)
    if server_state[STATE_KEY] == targets:
        return False
    with server_state:
        server_state[STATE_KEY] = targets
    return True


#: Run after every scene load the view component reports: installs the pass
#: as soon as the vtk view exists, without waiting for the polling timer.
SILHOUETTE_SCENE_HANDLER = "window.vvSilhouette && window.vvSilhouette.install()"


# Runs in the page. It finds the vtk.js view through the trame ref of the
# local view (the object the view component provides to its children), adds
# the pass after vtk.js's forward pass, and leaves it idle unless outlines are
# on and a target is in the scene. `window.vvSilhouette.stats` is read by the
# served-browser test and the bench; `window.__vvSilhouetteOff` set before
# load keeps the pass out, the bench's baseline.
SILHOUETTE_JS = r"""
typeof document !== 'undefined' && (function () {
  if (window.vvSilhouette || window.__vvSilhouetteOff) return;
  var STATE_KEY = 'silhouette_targets', FLAG = 'figure_outlines';
  var stats = {
    installed: false, unsupported: null, frames: 0, targets: 0, error: null, enabled: true,
    radius: null,
  };
  var outlinedNow = [];
  // Frame pixels per CSS pixel while an export draws at another size;
  // null for the live view, which has the device pixel ratio.
  var pixelRatio = null;
  var VS = [
    '#version 300 es',
    'void main() {',
    '  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));',
    '  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);',
    '}',
  ].join('\n');
  var FS = [
    '#version 300 es',
    'precision highp float;',
    'uniform highp sampler2D uDepth;',
    'uniform vec2 uClip;',
    'uniform float uParallel;',
    'uniform float uRadius;',
    'uniform float uPixelWorld;',
    'uniform float uJump;',
    'uniform vec4 uColor;',
    'out vec4 outColor;',
    'float lin(float d) {',
    '  if (uParallel > 0.5) return uClip.x + d * (uClip.y - uClip.x);',
    '  float z = d * 2.0 - 1.0;',
    '  return 2.0 * uClip.x * uClip.y / (uClip.y + uClip.x - z * (uClip.y - uClip.x));',
    '}',
    'void main() {',
    '  ivec2 size = textureSize(uDepth, 0);',
    '  ivec2 p = ivec2(gl_FragCoord.xy);',
    '  float d0 = texelFetch(uDepth, p, 0).r;',
    '  bool in0 = d0 < 1.0;',
    '  float z0 = in0 ? lin(d0) : 0.0;',
    '  float footprint = uParallel > 0.5 ? uPixelWorld : uPixelWorld * z0;',
    '  float dmin = d0;',
    '  bool edge = false;',
    '  for (int i = 0; i < 4; i++) {',
    '    float a = float(i) * 0.7853982;',
    '    ivec2 o = ivec2(round(vec2(cos(a), sin(a)) * uRadius));',
    '    float da = texelFetch(uDepth, clamp(p + o, ivec2(0), size - 1), 0).r;',
    '    float db = texelFetch(uDepth, clamp(p - o, ivec2(0), size - 1), 0).r;',
    '    dmin = min(dmin, min(da, db));',
    '    if ((da < 1.0) != in0 || (db < 1.0) != in0) { edge = true; continue; }',
    '    if (!in0) continue;',
    // A plane has no second difference along any axis, whatever its slope:
    // a large one is a fold in depth or a crease.
    '    if (abs(lin(da) + lin(db) - 2.0 * z0) > uJump * footprint * uRadius) edge = true;',
    '  }',
    '  if (!edge) discard;',
    '  gl_FragDepth = dmin;',
    '  outColor = uColor;',
    '}',
  ].join('\n');

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

  function signatureMatches(spec, polydata) {
    var points = polydata.getPoints && polydata.getPoints();
    if (!points || points.getNumberOfPoints() !== spec[0]) return false;
    var bounds = polydata.getBounds();
    for (var k = 0; k < 6; k++) {
      var expected = spec[k + 1];
      if (Math.abs(bounds[k] - expected) > 1e-4 * Math.max(1, Math.abs(expected))) return false;
    }
    return true;
  }

  function makePass(view, ogl, forward) {
    var gl = ogl.getContext();
    var program = null, vao = null, uniforms = {};
    var fbo = null, depthTexture = null, colorBuffer = null, fbSize = [0, 0];
    var cache = { specs: null, mtime: -1, targets: [] };
    var disabled = false;

    function compile(type, source) {
      var shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        throw new Error('silhouette shader: ' + gl.getShaderInfoLog(shader));
      }
      return shader;
    }

    function ensureProgram() {
      if (program) return;
      program = gl.createProgram();
      gl.attachShader(program, compile(gl.VERTEX_SHADER, VS));
      gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FS));
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        throw new Error('silhouette program: ' + gl.getProgramInfoLog(program));
      }
      ['uDepth', 'uClip', 'uParallel', 'uRadius', 'uPixelWorld', 'uJump', 'uColor']
        .forEach(function (name) { uniforms[name] = gl.getUniformLocation(program, name); });
      vao = gl.createVertexArray();
    }

    function ensureFramebuffer(w, h) {
      if (fbo && fbSize[0] === w && fbSize[1] === h) return;
      if (fbo) {
        gl.deleteFramebuffer(fbo);
        gl.deleteTexture(depthTexture);
        gl.deleteRenderbuffer(colorBuffer);
      }
      var previousTexture = gl.getParameter(gl.TEXTURE_BINDING_2D);
      depthTexture = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, depthTexture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.DEPTH_COMPONENT24, w, h, 0,
                    gl.DEPTH_COMPONENT, gl.UNSIGNED_INT, null);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.bindTexture(gl.TEXTURE_2D, previousTexture);
      colorBuffer = gl.createRenderbuffer();
      gl.bindRenderbuffer(gl.RENDERBUFFER, colorBuffer);
      gl.renderbufferStorage(gl.RENDERBUFFER, gl.RGBA8, w, h);
      gl.bindRenderbuffer(gl.RENDERBUFFER, null);
      fbo = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
      gl.framebufferRenderbuffer(
        gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, colorBuffer);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.TEXTURE_2D, depthTexture, 0);
      var status = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      if (status !== gl.FRAMEBUFFER_COMPLETE) throw new Error('silhouette framebuffer ' + status);
      fbSize = [w, h];
    }

    // The props to outline: matched to the server's signatures, re-matched
    // when the signatures or the renderer's prop list change.
    function targets(renderer, specs) {
      var mtime = renderer.getMTime();
      if (cache.specs === specs && cache.mtime === mtime) return cache.targets;
      var found = [];
      var props = renderer.getViewProps();
      for (var i = 0; i < props.length; i++) {
        var prop = props[i];
        var mapper = prop.getMapper && prop.getMapper();
        var input = mapper && mapper.getInputData && mapper.getInputData();
        if (!input || !input.getPoints) continue;
        for (var j = 0; j < specs.length; j++) {
          if (signatureMatches(specs[j], input)) { found.push(prop); break; }
        }
      }
      cache = { specs: specs, mtime: mtime, targets: found };
      return found;
    }

    function drawEdges(renderer, w, h) {
      var saved = {
        program: gl.getParameter(gl.CURRENT_PROGRAM),
        vao: gl.getParameter(gl.VERTEX_ARRAY_BINDING),
        unit: gl.getParameter(gl.ACTIVE_TEXTURE),
        viewport: gl.getParameter(gl.VIEWPORT),
        depthTest: gl.isEnabled(gl.DEPTH_TEST),
        depthFunc: gl.getParameter(gl.DEPTH_FUNC),
        depthMask: gl.getParameter(gl.DEPTH_WRITEMASK),
        colorMask: gl.getParameter(gl.COLOR_WRITEMASK),
        blend: gl.isEnabled(gl.BLEND),
        cull: gl.isEnabled(gl.CULL_FACE),
        scissor: gl.isEnabled(gl.SCISSOR_TEST),
      };
      var unit = gl.getParameter(gl.MAX_COMBINED_TEXTURE_IMAGE_UNITS) - 1;
      // vtk.js remembers the program it bound last and skips rebinding it;
      // unbind it and clear that record, so its next draw binds it again.
      // (The cache's releaseCurrentShaderProgram() deletes the program.)
      var shaderCache = ogl.getShaderCache();
      var lastBound = shaderCache.getLastShaderProgramBound();
      if (lastBound) lastBound.release();
      shaderCache.setLastShaderProgramBound(null);
      gl.useProgram(program);
      gl.bindVertexArray(vao);
      gl.activeTexture(gl.TEXTURE0 + unit);
      var previousTexture = gl.getParameter(gl.TEXTURE_BINDING_2D);
      gl.bindTexture(gl.TEXTURE_2D, depthTexture);
      var camera = renderer.getActiveCamera();
      var clip = camera.getClippingRange();
      var parallel = camera.getParallelProjection();
      var dpr = pixelRatio || window.devicePixelRatio || 1;
      stats.radius = Math.max(1, Math.round(1.5 * dpr));
      gl.uniform1i(uniforms.uDepth, unit);
      gl.uniform2f(uniforms.uClip, clip[0], clip[1]);
      gl.uniform1f(uniforms.uParallel, parallel ? 1 : 0);
      gl.uniform1f(uniforms.uRadius, stats.radius);
      gl.uniform1f(uniforms.uPixelWorld, parallel
        ? 2 * camera.getParallelScale() / h
        : 2 * Math.tan(camera.getViewAngle() * Math.PI / 360) / h);
      gl.uniform1f(uniforms.uJump, 4.0);
      gl.uniform4f(uniforms.uColor, 0, 0, 0, 1);
      gl.viewport(0, 0, w, h);
      gl.disable(gl.SCISSOR_TEST);
      gl.disable(gl.CULL_FACE);
      gl.disable(gl.BLEND);
      gl.colorMask(true, true, true, true);
      gl.enable(gl.DEPTH_TEST);
      gl.depthFunc(gl.LEQUAL);
      gl.depthMask(false);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      gl.bindTexture(gl.TEXTURE_2D, previousTexture);
      gl.activeTexture(saved.unit);
      gl.bindVertexArray(saved.vao);
      gl.useProgram(null);
      gl.viewport(saved.viewport[0], saved.viewport[1], saved.viewport[2], saved.viewport[3]);
      gl.colorMask(saved.colorMask[0], saved.colorMask[1], saved.colorMask[2], saved.colorMask[3]);
      gl.depthMask(saved.depthMask);
      gl.depthFunc(saved.depthFunc);
      if (!saved.depthTest) gl.disable(gl.DEPTH_TEST);
      if (saved.blend) gl.enable(gl.BLEND);
      if (saved.cull) gl.enable(gl.CULL_FACE);
      if (saved.scissor) gl.enable(gl.SCISSOR_TEST);
    }

    function run(renderer) {
      outlinedNow = [];
      if (!stats.enabled) return;
      var state = window.trame && window.trame.state;
      if (!state || !state.get(FLAG)) return;
      var specs = state.get(STATE_KEY);
      if (!specs || !specs.length) return;
      // A bound framebuffer means hardware picking or another offscreen
      // pass is rendering: leave its output alone.
      if (gl.getParameter(gl.FRAMEBUFFER_BINDING) !== null) return;
      var outlined = targets(renderer, specs);
      if (!outlined.length) return;
      var size = ogl.getFramebufferSize();
      ensureProgram();
      ensureFramebuffer(size[0], size[1]);
      var props = renderer.getViewProps(), hidden = [];
      for (var i = 0; i < props.length; i++) {
        if (props[i].getVisibility() && outlined.indexOf(props[i]) < 0) hidden.push(props[i]);
      }
      var shown = outlined.filter(function (prop) { return prop.getVisibility(); });
      if (!shown.length) return;
      // A translucent surface writes no depth in vtk.js; draw it opaque for
      // this one render so its outline has a depth to come from.
      var translucent = [];
      shown.forEach(function (prop) {
        var property = prop.getProperty && prop.getProperty();
        if (property && property.getOpacity() < 1) {
          translucent.push([property, property.getOpacity()]);
          property.setOpacity(1);
        }
      });
      hidden.forEach(function (prop) { prop.setVisibility(false); });
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
      try {
        forward.traverse(ogl, null);
      } finally {
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        hidden.forEach(function (prop) { prop.setVisibility(true); });
        translucent.forEach(function (entry) { entry[0].setOpacity(entry[1]); });
      }
      drawEdges(renderer, size[0], size[1]);
      stats.frames++;
      stats.targets = shown.length;
      outlinedNow = shown;
    }

    return {
      getClassName: function () { return 'vvSilhouettePass'; },
      traverse: function () {
        if (disabled) return;
        try {
          run(view.renderer);
        } catch (error) {
          // One failure turns the pass off rather than repeating every frame.
          disabled = true;
          stats.error = String(error && error.message || error);
        }
      },
    };
  }

  function install() {
    var view = findView();
    if (!view) return false;
    var ogl = view.openglRenderWindow;
    if (!ogl.getWebgl2 || !ogl.getWebgl2()) {
      stats.unsupported = 'webgl1';
      return true;
    }
    var passes = ogl.getRenderPasses();
    if (passes.some(function (pass) { return pass.getClassName() === 'vvSilhouettePass'; })) {
      return true;
    }
    ogl.setRenderPasses(passes.concat([makePass(view, ogl, passes[0])]));
    stats.installed = true;
    view.renderWindow.render();
    return true;
  }

  // The view mounts after this script runs; look for it until it exists.
  var attempts = 0;
  var timer = setInterval(function () {
    if (install() || ++attempts > 600) clearInterval(timer);
  }, 250);
  window.vvSilhouette = {
    stats: stats,
    install: install,
    // The props the last frame outlined, for the served-browser test.
    outlined: function () { return outlinedNow.slice(); },
    // An export frame's pixels per CSS pixel (null: back to the device's).
    setPixelRatio: function (ratio) { pixelRatio = ratio || null; },
  };
})();
"""
