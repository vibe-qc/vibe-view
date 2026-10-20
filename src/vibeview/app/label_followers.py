"""Client-side label followers: labels face the camera *during* a drag (#111).

Atom labels and charge labels are polygonal text (see
:func:`vibeview.renderers.structure.build_label_mesh`), so facing the viewer
is geometry. In the browser they used to turn towards the camera only when an
interaction ended and the server had rebuilt them: during the drag the text
swung edge-on. The label mesh now carries two point-data arrays, the anchor a
glyph belongs to and the glyph's coordinates in its own reading frame, and the
page script below hands them to vtk.js as custom vertex attributes with a
vertex-shader replacement that places every glyph with the server's billboard
basis for the camera of the frame being drawn. The mesh itself never changes
on the client: no geometry is rewritten, re-uploaded or sent during an
interaction, so a drag costs what it cost with static labels. Headless capture
and the terminal keep the server-side billboard basis, which is unchanged.

trame-vtk sends a polydata with the arrays its mapper colours by plus normals
and texture coordinates, so the label arrays would never reach vtk.js.
:func:`install_label_array_serializer` wraps the registered ``vtkPolyData``
serializer so that a label mesh is sent with all of its arrays. trame-vtk
fills its registry when its protocol is configured at server start; the
layout registers the wrapper as a later configure step and re-installs it
before every scene push, both idempotently.
"""

from __future__ import annotations

from typing import Any

from vibeview.renderers.structure import LABEL_ANCHOR_ARRAY, LABEL_LOCAL_ARRAY

_WRAPPER_MARK = "_vibe_view_label_arrays"

# Runs in the page. ``window.vvLabelFollow(event, rescan)`` takes a vtk.js
# interactor event whose ``pokedRenderer`` is the live renderer and configures
# every mapper that draws a label mesh: the two arrays become the vertex
# attributes ``vv_label_anchorMC`` and ``vv_label_localMC``, the vertex shader
# computes the glyph's position from them and the camera, and a shader
# callback sets the camera uniforms on every draw. The basis is the server's
# ``_billboard_basis``: forward from the anchor to the camera, right =
# viewUp x forward, up = forward x right; a degenerate basis keeps the point
# the server sent. vtk.js may store points shifted and scaled (far from the
# origin); the shader works in that frame through the buffer's inverse matrix.
#
# A mapper is configured once and stays safe if trame later reuses it for
# other data: the attribute list and the shader switch both follow its current
# input, so a mesh without the arrays is drawn as sent. ``stats`` is read by
# the bench and the served-browser test. Setting ``window.__vvLabelFollowOff``
# before the page loads leaves the labels as plain geometry, the pre-#111
# client, which is the bench's baseline.
LABEL_FOLLOWER_JS = r"""
typeof document !== 'undefined' && (function () {
  if (window.vvLabelFollow || window.__vvLabelFollowOff) return;
  var ANCHOR = 'vv_label_anchor', LOCAL = 'vv_label_local', NAMES = [ANCHOR, LOCAL];
  var DECLARATIONS = [
    '//VTK::PositionVC::Dec',
    'attribute vec3 vv_label_anchorMC;',
    'attribute vec3 vv_label_localMC;',
    'uniform float vvLabelOn;',
    'uniform vec3 vvLabelEye;',
    'uniform vec3 vvLabelUp;',
    'uniform vec3 vvLabelShift;',
    'uniform vec3 vvLabelScale;',
  ].join('\n');
  var POSITION = [
    'vec4 vvLabelMC = vertexMC;',
    'if (vvLabelOn > 0.5) {',
    '  vec3 vvF = vvLabelEye - vv_label_anchorMC;',
    '  vec3 vvR = cross(vvLabelUp, vvF);',
    '  float vvFn = length(vvF);',
    '  float vvRn = length(vvR);',
    '  if (vvFn > 1e-6 && vvRn > 1e-6 * vvFn) {',
    '    vvF /= vvFn;',
    '    vvR /= vvRn;',
    '    vec3 vvU = cross(vvF, vvR);',
    '    vec3 vvL = vv_label_localMC;',
    '    vec3 vvW = vv_label_anchorMC + vvR * vvL.x + vvU * vvL.y + vvF * vvL.z;',
    '    vvLabelMC = vec4((vvW - vvLabelShift) * vvLabelScale, 1.0);',
    '  }',
    '}',
    '//VTK::PositionVC::Impl',
  ].join('\n');
  // Ours run before vtk.js expands its markers (replaceFirst), then the
  // matrices it writes are pointed at the label position (replaceFirst false).
  var REPLACEMENTS = [
    { shaderType: 'Vertex', originalValue: '//VTK::PositionVC::Dec',
      replacementValue: DECLARATIONS, replaceFirst: true, replaceAll: false },
    { shaderType: 'Vertex', originalValue: '//VTK::PositionVC::Impl',
      replacementValue: POSITION, replaceFirst: true, replaceAll: false },
    { shaderType: 'Vertex', originalValue: 'MCPCMatrix \\* vertexMC',
      replacementValue: 'MCPCMatrix * vvLabelMC', replaceFirst: false, replaceAll: true },
    { shaderType: 'Vertex', originalValue: 'MCVCMatrix \\* vertexMC',
      replacementValue: 'MCVCMatrix * vvLabelMC', replaceFirst: false, replaceAll: true },
  ];
  var configured = new WeakMap(), checked = new WeakMap();
  var renderer = null;
  var stats = { configured: 0, draws: 0, scans: 0, enabled: true };

  function hasArrays(polydata) {
    var pointData = polydata && polydata.getPointData && polydata.getPointData();
    return Boolean(pointData && pointData.getArrayByName(ANCHOR) &&
                   pointData.getArrayByName(LOCAL));
  }

  function setUniforms(_userData, cellBO, ren, actor) {
    var program = cellBO.getProgram();
    if (!program || !program.isUniformUsed('vvLabelOn')) return;
    var mapper = actor.getMapper && actor.getMapper();
    var on = stats.enabled && actor.getIsIdentity() &&
      hasArrays(mapper && mapper.getInputData());
    program.setUniformf('vvLabelOn', on ? 1 : 0);
    if (!on) return;
    var camera = ren.getActiveCamera();
    var eye = camera.getPosition(), up = camera.getViewUp();
    program.setUniform3f('vvLabelEye', eye[0], eye[1], eye[2]);
    program.setUniform3f('vvLabelUp', up[0], up[1], up[2]);
    var cabo = cellBO.getCABO();
    var inverse = cabo.getCoordShiftAndScaleEnabled() ? cabo.getInverseShiftAndScaleMatrix() : null;
    if (inverse) {
      // inverse = translate(shift) * scale(1 / scale), column-major.
      program.setUniform3f('vvLabelShift', inverse[12], inverse[13], inverse[14]);
      program.setUniform3f('vvLabelScale', 1 / inverse[0], 1 / inverse[5], 1 / inverse[10]);
    } else {
      program.setUniform3f('vvLabelShift', 0, 0, 0);
      program.setUniform3f('vvLabelScale', 1, 1, 1);
    }
    stats.draws++;
  }

  // vtk.js builds the vertex buffer from the attribute names on every
  // change and fails on a name its input lacks, and its objects are frozen,
  // so the list is kept in step with the mapper's input from the outside: on
  // a new input (the mapper is modified) and on a changed one.
  function sync(mapper) {
    var record = configured.get(mapper);
    var input = mapper.getInputData();
    if (record.input !== input) {
      if (record.inputSubscription) record.inputSubscription.unsubscribe();
      record.input = input;
      record.inputSubscription = input && input.onModified
        ? input.onModified(function () { sync(mapper); })
        : null;
    }
    var want = hasArrays(input);
    var have = mapper.getCustomShaderAttributes().length === NAMES.length;
    if (want !== have) mapper.setCustomShaderAttributes(want ? NAMES : []);
  }

  function configure(mapper) {
    configured.set(mapper, { input: null, inputSubscription: null });
    var properties = mapper.getViewSpecificProperties();
    properties.OpenGL = properties.OpenGL || {};
    properties.OpenGL.ShaderReplacements =
      (properties.OpenGL.ShaderReplacements || []).concat(REPLACEMENTS);
    properties.ShadersCallbacks =
      (properties.ShadersCallbacks || []).concat([{ userData: null, callback: setUniforms }]);
    mapper.onModified(function () { sync(mapper); });
    sync(mapper);
    mapper.modified();
    stats.configured++;
  }

  // Looks for label meshes not yet on the shader. vtk.js rebuilds the actor
  // list on every getActors() call, so this runs once per interaction start,
  // not per frame: a label mesh pushed while the view is idle was built by
  // the server for the current camera, and is right until the camera moves.
  function scan() {
    if (!renderer) return;
    stats.scans++;
    var actors = renderer.getActors();
    for (var k = 0; k < actors.length; k++) {
      var mapper = actors[k].getMapper && actors[k].getMapper();
      if (!mapper || configured.has(mapper) || !mapper.getViewSpecificProperties) continue;
      var input = mapper.getInputData && mapper.getInputData();
      if (!input || !input.getPointData) continue;
      var stamp = input.getMTime() + ':' + input.getPointData().getMTime();
      var last = checked.get(mapper);
      if (last && last.input === input && last.stamp === stamp) continue;
      checked.set(mapper, { input: input, stamp: stamp });
      if (hasArrays(input)) configure(mapper);
    }
  }

  window.vvLabelFollow = function (event, rescan) {
    var candidate = event && event.pokedRenderer;
    if (candidate && candidate !== renderer) {
      renderer = candidate;
      scan();
    } else if (rescan) {
      scan();
    }
  };
  window.vvLabelFollow.stats = stats;
  window.vvLabelFollow.renderer = function () { return renderer; };
})();
"""

# vtk.js interactor events that hand the follower its renderer, and the
# handler each one runs. Interaction start rescans for label meshes before
# its first frame; nothing of the follower's runs per frame but the uniforms.
LABEL_FOLLOWER_HANDLER = "window.vvLabelFollow && window.vvLabelFollow($event)"
LABEL_FOLLOWER_HANDLERS = {
    "StartAnimation": "window.vvLabelFollow && window.vvLabelFollow($event, true)",
    "MouseEnter": LABEL_FOLLOWER_HANDLER,
}
LABEL_FOLLOWER_EVENTS = tuple(LABEL_FOLLOWER_HANDLERS)


def install_label_array_serializer(**_kwargs: Any) -> bool:
    """Send label meshes to vtk.js with their follower arrays.

    Wraps trame-vtk's ``vtkPolyData`` serializer so that a polydata carrying
    ``LABEL_ANCHOR_ARRAY`` is serialised with every array it holds, which is
    the two label arrays only; any other polydata goes through untouched.
    Idempotent, and a no-op (returning False) until trame-vtk has filled its
    registry, which happens when the local-rendering protocol starts. Takes
    and ignores keyword arguments so it can sit on ``ctrl.on_server_ready``,
    which trame calls with its own.
    """
    try:
        from trame_vtk.modules.vtk.serializers import registry
    except Exception:  # noqa: BLE001 — no viewer extra, nothing to wrap
        return False
    current = registry.SERIALIZERS.get("vtkPolyData")
    if current is None:
        return False
    if getattr(current, _WRAPPER_MARK, False):
        return True

    def polydata_with_label_arrays(
        parent: Any,
        dataset: Any,
        dataset_id: Any,
        context: Any,
        depth: Any,
        requested_fields: Any = None,
    ) -> Any:
        if (
            dataset is not None
            and dataset.GetPointData().GetArray(LABEL_ANCHOR_ARRAY) is not None
            and dataset.GetPointData().GetArray(LABEL_LOCAL_ARRAY) is not None
        ):
            requested_fields = "*"
        return current(parent, dataset, dataset_id, context, depth, requested_fields)

    setattr(polydata_with_label_arrays, _WRAPPER_MARK, True)
    registry.register_instance_serializer("vtkPolyData", polydata_with_label_arrays)
    return True
