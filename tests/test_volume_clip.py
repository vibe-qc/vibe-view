"""Clip-plane behaviour for signed (difference / spin) volumes.

Regression for audit finding A2-05: `_rebuild_clip` contoured only the
+isovalue via `make_mesh`, so clipping a `volume.difference` / `volume.spin`
isosurface silently dropped the entire negative lobe. Both lobes must now be
clipped and shown (blue = positive, red = negative).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import types
import zipfile

import numpy as np
import pytest

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _difference_volume_reader():
    from vibeview.qvf import QVFReader

    n = 20
    ii, jj, kk = np.meshgrid(np.arange(n), np.arange(n), np.arange(n), indexing="ij")
    data = np.zeros((n, n, n), dtype=np.float32)
    data += np.exp(-((ii - 14) ** 2 + (jj - 14) ** 2 + (kk - 14) ** 2) / 3.0)   # + lobe
    data -= np.exp(-((ii - 17) ** 2 + (jj - 14) ** 2 + (kk - 14) ** 2) / 3.0)   # - lobe
    grid = json.dumps({
        "origin": [0, 0, 0],
        "voxel_vectors": [[0.4, 0, 0], [0, 0.4, 0], [0, 0, 0.4]],
        "shape": [n, n, n],
    }).encode()
    blob = data.tobytes()
    sections = [{
        "id": "diff", "kind": "volume.difference", "members": {
            "grid": {"path": "g.json", "format": "json", "sha256": _sha(grid)},
            "data": {"path": "d.bin", "format": "binary", "dtype": "float32",
                     "shape": [n, n, n], "sha256": _sha(blob)},
        }}]
    manifest = {"qvf_version": 1,
                "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
                "sections": sections}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("g.json", grid)
        zf.writestr("d.bin", blob)
    return QVFReader(buf.getvalue())


def _clip_state(**kw):
    base = dict(clip_enabled=True, active_volume_id="diff", clip_x=0.3, clip_y=0.3,
                clip_z=0.3, isovalue=0.1, opacity=0.6, colormap="RdBu", status_message="")
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_signed_volume_clip_keeps_both_lobes():
    import pyvista as pv

    from vibeview.app import _rebuild_clip
    from vibeview.viewer_defaults import ViewerState

    reader = _difference_volume_reader()
    plotter = pv.Plotter(off_screen=True)
    state = _clip_state()
    _rebuild_clip(reader, plotter, ViewerState(), state)

    names = set(plotter.actors.keys())
    assert "_volume_clipped_pos" in names, "positive lobe missing"
    assert "_volume_clipped_neg" in names, "negative lobe dropped (A2-05 regression)"
    assert state.status_message == "" or "error" not in state.status_message.lower()


def test_default_clip_rebuild_updates_actor_geometry():
    """Slider-driven replacements update the ordinary three-plane clip."""
    import pyvista as pv

    from vibeview.app import _rebuild_clip
    from vibeview.viewer_defaults import ViewerState

    reader = _difference_volume_reader()
    plotter = pv.Plotter(off_screen=True)
    state = _clip_state(clip_x=0.2, clip_y=0.2, clip_z=0.2)
    viewer_state = ViewerState()
    _rebuild_clip(reader, plotter, viewer_state, state)
    before = {
        name: (actor.mapper.dataset.n_points, tuple(actor.mapper.dataset.bounds))
        for name, actor in plotter.actors.items()
        if "clipped" in str(name)
    }

    state.clip_x = 0.8
    _rebuild_clip(reader, plotter, viewer_state, state)
    after = {
        name: (actor.mapper.dataset.n_points, tuple(actor.mapper.dataset.bounds))
        for name, actor in plotter.actors.items()
        if "clipped" in str(name)
    }

    assert "_volume_clipped_pos" in before
    assert "_volume_clipped_pos" not in after
    assert before["_volume_clipped_neg"] != after["_volume_clipped_neg"]


def test_clip_hides_full_volume_and_restores_on_disable():
    """UI-OBS-E: enabling clip must HIDE the full isosurface (else it sits on
    top and clipping looks like a no-op); disabling must bring it back."""
    import pyvista as pv

    from vibeview.app import _rebuild_clip, _rebuild_volume
    from vibeview.viewer_defaults import ViewerState

    reader = _difference_volume_reader()
    plotter = pv.Plotter(off_screen=True)
    vs = ViewerState()
    state = _clip_state(clip_enabled=False)

    # Draw the full isosurface first (as activating the volume does).
    _rebuild_volume(reader, plotter, vs, state, "diff")
    assert [k for k in plotter.actors if str(k).startswith("volume_diff")]

    # Enable clip → full volume hidden, clipped slice shown.
    state.clip_enabled = True
    _rebuild_clip(reader, plotter, vs, state)
    assert not [k for k in plotter.actors if str(k).startswith("volume_diff")]
    assert [k for k in plotter.actors if "clipped" in str(k)]

    # Disable clip → full volume restored, clipped actors gone.
    state.clip_enabled = False
    _rebuild_clip(reader, plotter, vs, state)
    assert [k for k in plotter.actors if str(k).startswith("volume_diff")]
    assert not [k for k in plotter.actors if "clipped" in str(k)]


def test_disabling_clip_removes_both_signed_actors():
    import pyvista as pv

    from vibeview.app import _rebuild_clip
    from vibeview.viewer_defaults import ViewerState

    reader = _difference_volume_reader()
    plotter = pv.Plotter(off_screen=True)
    vs = ViewerState()
    _rebuild_clip(reader, plotter, vs, _clip_state())
    _rebuild_clip(reader, plotter, vs, _clip_state(clip_enabled=False))
    assert not [k for k in plotter.actors if "clipped" in str(k)]


def test_slice_position_feedback_is_visual_but_does_not_replace_live_status():
    """Slider ticks update visual position text without changing live status."""
    import pyvista as pv

    from vibeview.app import _rebuild_clip
    from vibeview.viewer_defaults import ViewerState

    reader = _difference_volume_reader()
    plotter = pv.Plotter(off_screen=True)
    state = _clip_state(
        show_slice=True,
        clip_x=0.6,
        clip_y=0.5,
        clip_z=0.5,
        clip_position_message="",
        status_message="2D slice enabled",
    )
    _rebuild_clip(reader, plotter, ViewerState(), state)

    assert "_volume_slice" in plotter.actors
    assert state.clip_position_message == "2D slice at x=0.60"
    assert state.status_message == "2D slice enabled"


def test_clip_rebuild_retains_indirectly_removed_actor_until_replacement(monkeypatch):
    """An unprefixed scalar bar must outlive its volume's replacement graph."""
    import gc
    import weakref

    import vibeview.app as appmod

    class Actor:
        pass

    class Plotter:
        def __init__(self, actor):
            self.actors = {"vtkScalarBarActor(Addr=0x1)": actor}

        def remove_actor(self, name):
            if name not in self.actors:
                raise KeyError(name)
            del self.actors[name]

    actor = Actor()
    old_actor = weakref.ref(actor)
    plotter = Plotter(actor)
    del actor
    reader = types.SimpleNamespace(sections=[types.SimpleNamespace(id="diff")])
    retained_during_rebuild = []

    def observe_retired_actor(*_args, **_kwargs):
        del plotter.actors["vtkScalarBarActor(Addr=0x1)"]
        gc.collect()
        retained_during_rebuild.append(old_actor() is not None)

    monkeypatch.setattr(appmod, "_rebuild_volume", observe_retired_actor)
    state = _clip_state(clip_enabled=False, clip_position_message="")
    appmod._rebuild_clip(reader, plotter, object(), state)

    assert retained_during_rebuild == [True]
