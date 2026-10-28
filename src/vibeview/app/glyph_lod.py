"""Level of detail for instanced glyphs while the camera moves.

An instanced atom or bond actor of at least :data:`LOD_INSTANCES` instances
is sent to the browser with a third input next to its instance points and
its prototype: a coarse prototype (an octahedron-like sphere, a three-sided
cylinder). While the user drags, zooms or spins the view, the page script
:data:`GLYPH_LOD_JS` draws every such actor with the coarse prototype and
puts the full one back when the interaction ends, so a scene of 10^6 atoms
turns at the cost of a tenth of its triangles and still looks round at
rest. The instance arrays are untouched, only the mesh each instance
repeats changes, and nothing goes to the server.

All of it runs in vtk.js: server render passes never reach the client.
"""

from __future__ import annotations

from typing import Any

#: Instances at or above which a glyph actor gets a coarse prototype.
LOD_INSTANCES = 10_000

_COARSE: dict[str, Any] = {}


def coarse_prototype(oriented: bool) -> Any:
    """The coarse stand-in for a sphere (``oriented=False``) or a bond cylinder."""
    key = "cylinder" if oriented else "sphere"
    if key not in _COARSE:
        import pyvista as pv

        if oriented:
            _COARSE[key] = pv.Cylinder(center=(0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0),
                                       radius=1.0, height=1.0, resolution=3, capping=False)
        else:
            _COARSE[key] = pv.Sphere(radius=1.0, theta_resolution=4, phi_resolution=3)
    return _COARSE[key]


GLYPH_LOD_JS = """
(function () {
  if (window.vvGlyphLod) return;
  var stats = { scans: 0, actors: 0, coarse: 0, restored: 0 };
  var subscribed = null;
  var full = new WeakMap();
  var coarseOn = false;

  function findView() {
    var refs = window.trame && window.trame.refs;
    if (!refs) return null;
    for (var key in refs) {
      try {
        var view = refs[key].$.provides.view;
        if (view && view.renderer) return view;
      } catch (error) { /* not a vtk view */ }
    }
    return null;
  }

  function glyphMappers(view) {
    var out = [];
    view.renderer.getViewProps().forEach(function (prop) {
      var mapper = prop.getMapper && prop.getMapper();
      if (mapper && mapper.isA && mapper.isA('vtkGlyph3DMapper')
          && mapper.getInputData(2)) {
        out.push(mapper);
      }
    });
    return out;
  }

  function swap(view, coarse) {
    if (coarse === coarseOn) return;
    coarseOn = coarse;
    glyphMappers(view).forEach(function (mapper) {
      if (coarse) {
        full.set(mapper, mapper.getInputData(1));
        mapper.setInputData(mapper.getInputData(2), 1);
        stats.coarse++;
      } else if (full.has(mapper)) {
        mapper.setInputData(full.get(mapper), 1);
        full.delete(mapper);
        stats.restored++;
      }
      // The instance matrices do not depend on the prototype, and both
      // prototypes span the same unit box: mark them current so the swap
      // does not recompute one matrix per instance on the CPU.
      var built = mapper.getBuildTime && mapper.getBuildTime();
      if (built) built.modified();
    });
    if (!coarse) view.renderWindow.render();
  }

  function scan() {
    var view = findView();
    if (!view) return;
    stats.scans++;
    stats.actors = glyphMappers(view).length;
    var interactor = view.interactor
      || (view.renderWindow.getInteractor && view.renderWindow.getInteractor());
    if (!interactor || subscribed === interactor) return;
    subscribed = interactor;
    interactor.onStartAnimation(function () { swap(view, true); });
    interactor.onEndAnimation(function () { swap(view, false); });
  }

  window.vvGlyphLod = { rescan: scan, stats: stats };
})();
"""

#: Run after every scene load the view component reports.
GLYPH_LOD_SCENE_HANDLER = "window.vvGlyphLod && window.vvGlyphLod.rescan()"
