"""Camera-plane atom dragging: ray geometry and vtk.js event bridge."""

from __future__ import annotations

import numpy as np


def ray_atom(positions: np.ndarray, radii: np.ndarray, ray: np.ndarray) -> int | None:
    """Return the frontmost atom sphere intersected by a near/far camera ray."""
    direction = ray[1] - ray[0]
    length = float(np.linalg.norm(direction))
    if length < 1e-12:
        return None
    direction /= length
    offset = positions - ray[0]
    along = offset @ direction
    perpendicular2 = np.sum(offset * offset, axis=1) - along**2
    discriminant = radii**2 - perpendicular2
    distance = along - np.sqrt(np.maximum(discriminant, 0))
    valid = (discriminant >= 0) & (along >= 0) & (distance <= length)
    if not valid.any():
        return None
    return int(np.argmin(np.where(valid, np.maximum(distance, 0), np.inf)))


def plane_point(ray: np.ndarray, origin: np.ndarray, normal: np.ndarray) -> np.ndarray | None:
    """Intersect a camera ray with the screen-parallel plane through origin."""
    direction = ray[1] - ray[0]
    denominator = float(direction @ normal)
    if abs(denominator) < 1e-12:
        return None
    return np.asarray(ray[0] + direction * float((origin - ray[0]) @ normal) / denominator)


# These are vtk-local-view's default controls, retained outside atom dragging.
CAMERA_CONTROLS = [
    {"button": 1, "action": "Rotate"},
    {"button": 2, "action": "Pan"},
    {"button": 3, "action": "Zoom", "scrollEnabled": True},
    {"button": 1, "action": "Pan", "alt": True},
    {"button": 1, "action": "Zoom", "control": True},
    {"button": 1, "action": "Select", "shift": True},
    {"button": 1, "action": "Roll", "alt": True, "shift": True},
]
DRAG_CONTROLS = [
    {"button": 2, "action": "Pan"},
    {"button": 3, "action": "Zoom", "scrollEnabled": True},
    {"button": 1, "action": "Rotate", "alt": True},
]

# vtk.js supplies display coordinates (including device-pixel scaling) and
# the actual client renderer. Unproject there: the server camera can lag a
# rotation or zoom. Only finite number arrays cross the websocket.
ATOM_DRAG_JS = r"""
typeof document !== 'undefined' && (function () {
  if (window.vvAtomDrag) return;
  let active = null, serial = 0, last = 0;
  function enabled() {
    const s = window.trame && window.trame.state;
    return s && s.get('edit_mode') && s.get('edit_drag_mode');
  }
  function send(phase, e) {
    if (!active) return;
    const r = e && e.pokedRenderer, p = e && e.position;
    let ray = null, normal = null;
    if (r && p) {
      const view = r.getRenderWindow().getViews()[0];
      ray = [Array.from(view.displayToWorld(p.x, p.y, 0, r)),
             Array.from(view.displayToWorld(p.x, p.y, 1, r))];
      normal = Array.from(r.getActiveCamera().getDirectionOfProjection());
    }
    window.trame.trigger('edit_atom_drag', [{
      phase: phase, token: active, ray: ray, normal: normal,
      shift: !!(e && e.shiftKey)
    }]);
  }
  window.vvAtomDrag = function (phase, e) {
    if (phase === 'begin') {
      if (!enabled() || e.altKey || e.controlKey) return;
      active = ++serial;
      last = 0;
      send(phase, e);
    } else if (phase === 'move') {
      if (!active || !enabled()) return;
      const now = performance.now();
      if (now - last < 50) return;
      last = now;
      send(phase, e);
    } else {
      send('end', e);
      active = null;
    }
  };
  // Also end when released outside vtk.js, focus is lost, or Escape is used.
  document.addEventListener('pointerup', () => {
    const token = active;
    setTimeout(() => { if (active === token) window.vvAtomDrag('end', null); }, 0);
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') window.vvAtomDrag('end', null);
  }, true);
  window.addEventListener('blur', () => window.vvAtomDrag('end', null));
})();
"""
