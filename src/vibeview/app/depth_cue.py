"""Client-only depth cue for axial figures, using vtk.js shader replacement.

Depth fog mixes the lit fragment with the renderer background as window
depth increases. It is a visual cue, never a change to property values.
"""

DEPTH_CUE_JS = r"""
(function () {
  if (window.vvDepthCue) return;
  let enabled = false;
  const original = new WeakMap();
  function view() {
    for (const ref of Object.values(window.trame.refs || {})) {
      try { if (ref.$.provides.view?.renderer) return ref.$.provides.view; }
      catch (_) { /* other widget */ }
    }
    return null;
  }
  function apply() {
    const v = view();
    if (!v) return;
    const bg = v.renderer.getBackground().slice(0, 3).map(x => Number(x).toFixed(6));
    for (const actor of v.renderer.getViewProps()) {
      const mapper = actor.getMapper && actor.getMapper();
      if (!mapper?.setViewSpecificProperties || !mapper.isA('vtkMapper')) continue;
      if (!enabled) {
        if (original.has(mapper)) {
          mapper.setViewSpecificProperties(original.get(mapper));
          original.delete(mapper);
        }
        continue;
      }
      if (!original.has(mapper)) original.set(mapper, mapper.getViewSpecificProperties());
      const saved = original.get(mapper) || {};
      const gl = saved.OpenGL || {};
      mapper.setViewSpecificProperties({...saved, OpenGL: {...gl,
        ShaderReplacements: [...(gl.ShaderReplacements || []), {
          shaderType: 'Fragment', replaceFirst: true, replaceAll: false,
          originalValue: '//VTK::Light::Impl',
          replacementValue: '//VTK::Light::Impl\n' +
            'gl_FragData[0].rgb = mix(gl_FragData[0].rgb, vec3(' + bg.join(',') +
            '), 0.65 * smoothstep(0.0, 1.0, gl_FragCoord.z));'
        }]
      }});
    }
    v.renderWindow.render();
  }
  window.vvDepthCue = {set: value => { enabled = Boolean(value); apply(); },
                       refresh: () => { if (enabled) apply(); }};
})();
"""

DEPTH_CUE_SCENE_HANDLER = "window.vvDepthCue && window.vvDepthCue.refresh()"
