"""What module-level scene code shares with ``create_app``: the per-plotter
hooks record, the actor-group names and the DOM-change trigger script.

Part of the ``vibeview.app`` package; see its ``__init__`` for ``create_app``.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable



# Run ``fn`` now and again after the DOM changes, instead of polling forever
# (#37-55). Vue re-renders replace the panels and themed components these
# scripts decorate, so they must re-run, but only when something changed:
# a MutationObserver over the document, coalesced to one call per frame.
# Browsers without MutationObserver keep the old 1 s poll.
_ON_DOM_CHANGE_JS = (
    "window._vvOnDomChange=window._vvOnDomChange||function(fn){fn();"
    "if(typeof MutationObserver==='undefined'){setInterval(fn,1000);return;}"
    "var queued=false;new MutationObserver(function(){if(queued)return;queued=true;"
    "requestAnimationFrame(function(){queued=false;fn();});})"
    ".observe(document.documentElement,{childList:true,subtree:true});};"
)

@dataclass
class _SceneHooks:
    """What ``create_app`` lends module-level scene code for one plotter.

    These were attributes stuck onto the pyvista ``Plotter`` itself, under two
    spellings (``_vibe_view_update``, ``_vibeview_wf_render_lock``, ...), and
    read back with ``getattr`` defaults at each use (#37-R3, #37-41). They live
    in one typed record per plotter instead, looked up by :func:`_scene_hooks`.
    Headless code and tests get the empty defaults: no client to push to.
    """

    #: Push the scene to the browser (``local_view.update``).
    update: Callable[[], None] | None = None
    #: Push only the camera (``local_view.push_camera``).
    push_camera: Callable[[], None] | None = None
    #: Bumped on every push, so a dispatcher can tell whether an activator
    #: already pushed (#37-21).
    push_generation: int = 0
    #: Geometry edits invalidate, then refresh, a relocalized orbital set.
    invalidate_relocalization: Callable[[], None] | None = None
    refresh_relocalization: Callable[[], None] | None = None
    #: Serializes scene replacement against worker-thread renders.
    wf_render_lock: Any = None
    #: The trame state, for display options (replication, representation,
    #: labels) a module-level redraw must honour.
    state: Any = None
    #: Computed wavefunction surfaces' own opacity at an orbital-opacity
    #: slider setting of 1, by actor name (#37-36). The orbital lobes are 1.
    surface_base_opacity: dict[str, float] = field(default_factory=dict)

_SCENE_HOOKS: weakref.WeakKeyDictionary[Any, _SceneHooks] = weakref.WeakKeyDictionary()

def _scene_hooks(plotter: Any) -> _SceneHooks:
    """The :class:`_SceneHooks` for ``plotter``, created empty on first use."""
    try:
        hooks = _SCENE_HOOKS.get(plotter)
        if hooks is None:
            hooks = _SCENE_HOOKS[plotter] = _SceneHooks()
        return hooks
    except TypeError:  # a test double that cannot be weakly referenced
        return _SceneHooks()

class _Actors:
    """Actor-name prefixes, one name per group of scene actors (#37-41).

    Every removal names its group here instead of retyping a string, so a
    typo is an AttributeError rather than a removal that silently matches
    nothing.
    """

    ATOM = "atom_"
    BOND = "bond_"
    BONDS = "bonds_"  # the batched large-system bond actors
    CARTOON = "cartoon_"
    HYDROGEN_BOND = "hydrogen_bond_"
    CELL = "cell_"
    EDITOR = "structure_"  # the editor's rebuilt structure
    VOLUME = "volume_"
    VOLUME_RENDER = "volume_render_"  # a section drawn as a volume; in the volume_ family
    MO_SURFACE = "mo_iso_"  # computed wavefunction surfaces
    QTAIM = "qtaim_"
    FERMI = "fermi_"
    COMPARE = "cmp_"
    PICK_MARKER = "pick_marker_"
    EDIT_HIGHLIGHT = "edit_highlight_"
    FROZEN_HIGHLIGHT = "frozen_highlight_"
    OUTLINE = "outline_"  # figure outline hulls, outline_<outlined actor> (#68)
    VIB_ATOM = "vib_atom_"
    TRAJ_ATOM = "traj_atom_"
    TRAJ_CELL = "traj_cell_"
    TRAJ_VOLUME = "traj_volume_"
    DIPOLE = "dipole_arrow"
    WANNIER = "wannier_center"
