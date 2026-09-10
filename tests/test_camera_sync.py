"""Client -> server camera readback.

VtkLocalView renders client-side and the client owns the camera;
``push_camera`` only goes server -> client. Without a readback every
feature that persists "the current view" — Save View, Save Session, and
any server-side render — stored whatever the last server-side
``reset_camera`` left, not what the user was looking at.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest


def _app():
    from vibeview.app import create_app
    from vibeview.converters import xyz_to_qvf
    from vibeview.qvf import QVFReader

    p = Path(tempfile.mktemp(suffix=".qvf"))
    p.write_bytes(
        xyz_to_qvf(b"3\nw\nO 0 0 0\nH 0.76 0.59 0\nH -0.76 0.59 0\n").getvalue()
    )
    try:
        return create_app(QVFReader(p))
    finally:
        p.unlink(missing_ok=True)


POS = [11.0, 22.0, 33.0]
FOC = [1.0, 2.0, 3.0]
UP = [0.0, 0.0, 1.0]


def _camera(app, name="probe"):
    """Read the server camera the only way a user can: save a bookmark.

    create_app does not expose the plotter, and going through the bookmark
    is the contract that actually matters anyway.
    """
    app.controller.save_user_bookmark(name)
    return next(b for b in app.state.user_bookmarks if b["name"] == name)["camera"]


class TestSync:
    def test_moves_the_server_camera(self):
        app = _app()
        app.controller.sync_client_camera(POS, FOC, UP, 5.0)
        cam = _camera(app)
        assert np.allclose(cam["position"], POS)
        assert np.allclose(cam["focal_point"], FOC)
        assert np.allclose(cam["view_up"], UP)

    def test_a_bookmark_then_records_the_synced_view(self):
        """The end-to-end point: Save View must capture where the user is."""
        app = _app()
        app.controller.sync_client_camera(POS, FOC, UP, 5.0)
        app.controller.save_user_bookmark("v")
        saved = next(b for b in app.state.user_bookmarks if b["name"] == "v")
        assert np.allclose(saved["camera"]["position"], POS)

    def test_two_syncs_give_two_different_bookmarks(self):
        """Regression: before the readback these came out byte-identical
        across a rotation that visibly changed 18% of the viewport."""
        app = _app()
        app.controller.sync_client_camera(POS, FOC, UP, 5.0)
        app.controller.save_user_bookmark("a")
        app.controller.sync_client_camera([-9.0, -8.0, -7.0], FOC, UP, 5.0)
        app.controller.save_user_bookmark("b")
        bms = {b["name"]: b["camera"] for b in app.state.user_bookmarks}
        assert bms["a"]["position"] != bms["b"]["position"]

    def test_parallel_scale_is_applied_when_given(self):
        app = _app()
        app.controller.sync_client_camera(POS, FOC, UP, 7.5)
        cam = _camera(app)
        if "parallel_scale" in cam:
            assert cam["parallel_scale"] == pytest.approx(7.5)


class TestMalformedPayloadNeverBreaksInteraction:
    """This runs on every interaction end, so it must never raise and must
    never leave the camera somewhere nonsensical."""

    @pytest.mark.parametrize(
        "args",
        [
            (None, None, None, None),
            ([1, 2], FOC, UP, None),          # too few components
            (POS, FOC, [0, 0], None),         # ragged
            ("nope", FOC, UP, None),          # wrong type
            ([1, 2, "x"], FOC, UP, None),     # non-numeric
        ],
    )
    def test_bad_payload_is_ignored(self, args):
        app = _app()
        before = _camera(app, "before")
        app.controller.sync_client_camera(*args)
        after = _camera(app, "after")
        assert np.allclose(after["position"], before["position"])
        assert np.allclose(after["focal_point"], before["focal_point"])

    def test_bad_parallel_scale_still_applies_the_pose(self):
        """A junk scale must not discard a good position."""
        app = _app()
        app.controller.sync_client_camera(POS, FOC, UP, "not-a-number")
        assert np.allclose(_camera(app)["position"], POS)


class TestBookmarkListSyncsToTheClient:
    def test_saving_marks_user_bookmarks_dirty(self):
        """`.append` on a declared state list is invisible to trame — the
        pushed snapshot aliases the same list. Measured: the names list
        showed both bookmarks while the client's user_bookmarks was []."""
        app = _app()
        seen = []
        state_cls = type(app.state)
        original = state_cls.dirty

        def spy(self, *keys):
            seen.extend(keys)
            return original(self, *keys)

        state_cls.dirty = spy
        try:
            app.controller.save_user_bookmark("x")
        finally:
            state_cls.dirty = original
        assert "user_bookmarks" in seen


class TestNewFileGetsItsCameraPushed:
    """Regression: activating a file set the server camera and pushed only
    the geometry. The client owns the camera in VtkLocalView, so the browser
    kept the previous file's zoom — building phenol while water was open put
    the ring far outside the viewport."""

    def test_push_camera_is_a_safe_no_op_headless(self):
        import pyvista as pv

        from vibeview.app import _push_camera

        _push_camera(pv.Plotter(off_screen=True))  # must not raise

    def test_a_failing_push_does_not_propagate(self):
        import pyvista as pv

        from vibeview.app import _push_camera

        p = pv.Plotter(off_screen=True)

        def boom():
            raise RuntimeError("client gone")

        p._vibe_view_push_camera = boom
        _push_camera(p)  # best-effort: a dead client must not break a reload

    def test_switching_files_pushes_the_camera(self, tmp_path):
        """The geometry push alone is what left the stale zoom on screen."""
        from vibeview.app import create_app
        from vibeview.converters import xyz_to_qvf
        from vibeview.qvf import QVFReader

        a = tmp_path / "a.qvf"
        a.write_bytes(xyz_to_qvf(b"2\nc\nO 0 0 0\nH 0 0 1\n").getvalue())
        b = tmp_path / "b.qvf"
        b.write_bytes(
            xyz_to_qvf(b"3\nc\nC 0 0 0\nC 1.5 0 0\nC 3.0 0 0\n").getvalue()
        )
        app = create_app([QVFReader(a), QVFReader(b)])

        calls = []
        import vibeview.app as appmod

        original = appmod._push_camera
        appmod._push_camera = lambda plotter: calls.append(1)
        try:
            app.controller.switch_file(1)
        finally:
            appmod._push_camera = original
        assert calls, "switching files never pushed a camera to the client"
