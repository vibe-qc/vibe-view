"""D5: camera-facing (billboard) labels.

Labels are polygonal text, not 2D annotations — vtk.js does not render
VTK's 2D label actors — so facing the viewer is geometry, and the mesh
has to be rebuilt when the viewpoint moves.
"""

from __future__ import annotations

import numpy as np
import pytest

from vibeview.renderers.structure import (
    _billboard_basis,
    build_label_mesh,
    forget_label_actors,
    register_label_actor,
    reorient_labels,
)

ORIGIN = np.zeros(3)
CAM_X = ((10.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
CAM_Z = ((0.0, 0.0, 10.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0))


def _span(mesh):
    p = np.asarray(mesh.points)
    return p.max(axis=0) - p.min(axis=0)


class TestBasis:
    def test_forward_points_at_the_camera(self):
        right, up, forward = _billboard_basis(ORIGIN, CAM_X)
        assert np.allclose(forward, [1, 0, 0])
        assert np.allclose(up, [0, 0, 1])
        assert np.allclose(np.cross(right, up), forward, atol=1e-9)

    def test_basis_is_orthonormal(self):
        right, up, forward = _billboard_basis(np.array([3.0, -2.0, 7.0]), CAM_X)
        for v in (right, up, forward):
            assert np.linalg.norm(v) == pytest.approx(1.0)
        assert np.dot(right, up) == pytest.approx(0.0, abs=1e-9)
        assert np.dot(up, forward) == pytest.approx(0.0, abs=1e-9)

    @pytest.mark.parametrize(
        "camera",
        [
            ((0, 0, 10), (0, 0, 0), (0, 0, 1)),   # view-up parallel to view dir
            ((0, 0, 0), (0, 0, 0), (0, 0, 1)),    # anchor sits at the camera
        ],
    )
    def test_degenerate_returns_none(self, camera):
        assert _billboard_basis(ORIGIN, camera) is None


class TestMeshOrientation:
    def test_unoriented_lies_in_the_xy_plane(self):
        span = _span(build_label_mesh([ORIGIN], ["Xy"], camera=None))
        assert span[0] > 0.1 and span[1] > 0.1
        assert span[2] < 0.2, "text should be flat in z"

    def test_billboarded_turns_to_face_the_camera(self):
        """From +X the text must lie in the YZ plane, not XY."""
        span = _span(build_label_mesh([ORIGIN], ["Xy"], camera=CAM_X))
        assert span[1] > 0.1 and span[2] > 0.1
        assert span[0] < 0.2, "text should be flat along the view direction"

    def test_a_camera_on_z_reproduces_the_flat_placement(self):
        """Looking down +Z is the one view the old behaviour was right for."""
        span = _span(build_label_mesh([ORIGIN], ["Xy"], camera=CAM_Z))
        assert span[0] > 0.1 and span[1] > 0.1
        assert span[2] < 0.2

    def test_degenerate_camera_still_produces_a_finite_mesh(self):
        mesh = build_label_mesh([ORIGIN], ["Xy"], camera=((0, 0, 10), (0, 0, 0), (0, 0, 1)))
        assert mesh is not None
        assert np.isfinite(np.asarray(mesh.points)).all()

    def test_lift_follows_the_camera_up_not_world_z(self):
        """Lifting along world +z would push the label sideways on screen
        for any camera that is not looking down z."""
        cam = ((10.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0))  # up = +Y
        centre = np.asarray(
            build_label_mesh([ORIGIN], ["X"], camera=cam, lift=5.0).points
        ).mean(axis=0)
        assert centre[1] > 3.0, centre     # lifted along camera up (+Y)
        assert abs(centre[2]) < 1.0, centre  # not along world +z

    def test_empty_input_is_none(self):
        assert build_label_mesh([], [], camera=CAM_X) is None

    def test_blank_strings_are_skipped(self):
        assert build_label_mesh([ORIGIN, ORIGIN], ["", ""], camera=CAM_X) is None


class TestReorientRegistry:
    def _plotter(self):
        import pyvista as pv

        return pv.Plotter(off_screen=True)

    def test_nothing_registered_is_a_no_op(self):
        assert reorient_labels(self._plotter()) == 0

    def test_registered_actor_is_rebuilt(self):
        p = self._plotter()
        mesh = build_label_mesh([ORIGIN], ["Xy"], camera=None)
        p.add_mesh(mesh, name="atom_index_labels")
        register_label_actor(p, "atom_index_labels", [ORIGIN], ["Xy"])
        p.camera.position = (10.0, 0.0, 0.0)
        p.camera.focal_point = (0.0, 0.0, 0.0)
        p.camera.up = (0.0, 0.0, 1.0)
        assert reorient_labels(p) == 1
        span = _span(p.actors["atom_index_labels"].mapper.dataset)
        assert span[0] < 0.3, "actor did not turn to face the camera"

    def test_forget_stops_rebuilding(self):
        p = self._plotter()
        p.add_mesh(build_label_mesh([ORIGIN], ["Xy"]), name="atom_index_labels")
        register_label_actor(p, "atom_index_labels", [ORIGIN], ["Xy"])
        forget_label_actors(p)
        assert reorient_labels(p) == 0

    def test_registry_is_per_plotter(self):
        a, b = self._plotter(), self._plotter()
        a.add_mesh(build_label_mesh([ORIGIN], ["Xy"]), name="atom_index_labels")
        register_label_actor(a, "atom_index_labels", [ORIGIN], ["Xy"])
        assert reorient_labels(b) == 0

    @pytest.mark.parametrize("remove", ["actor", "scene"])
    def test_removed_actor_is_not_recreated(self, remove):
        p = self._plotter()
        try:
            mesh = build_label_mesh([ORIGIN], ["Xy"], camera=None)
            p.add_mesh(mesh, name="atom_index_labels")
            register_label_actor(p, "atom_index_labels", [ORIGIN], ["Xy"])

            if remove == "actor":
                p.remove_actor("atom_index_labels")
            else:
                p.clear()

            stale = p._vibe_view_label_specs["atom_index_labels"]
            assert stale.actor_ref.Get() is None
            assert reorient_labels(p) == 0
            assert "atom_index_labels" not in p.actors
            assert p._vibe_view_label_specs == {}
        finally:
            p.close()

    def test_only_live_actor_is_rebuilt_and_stale_spec_is_pruned(self):
        import pyvista as pv

        p = self._plotter()
        try:
            mesh = build_label_mesh([ORIGIN], ["Xy"], camera=None)
            for name in ("atom_index_labels", "atom_charge_labels"):
                p.add_mesh(mesh, name=name)
                register_label_actor(p, name, [ORIGIN], ["Xy"])
            p.remove_actor("atom_index_labels")
            stale = p._vibe_view_label_specs["atom_index_labels"]
            assert stale.actor_ref.Get() is None

            # Reuse the removed name before the registry gets a chance to
            # prune it. Name equality alone must not transfer label ownership.
            replacement = pv.Sphere(radius=0.25)
            p.add_mesh(replacement, name="atom_index_labels")
            replacement_actor = p.actors["atom_index_labels"]
            assert reorient_labels(p) == 1
            assert p.actors["atom_index_labels"] is replacement_actor
            assert "atom_charge_labels" in p.actors
            assert set(p._vibe_view_label_specs) == {
                "atom_charge_labels"
            }

            # Later camera moves continue to rebuild only the live label.
            assert reorient_labels(p) == 1
            assert p.actors["atom_index_labels"] is replacement_actor
        finally:
            p.close()


class TestCameraSyncRebuildsLabels:
    def test_sync_client_camera_reorients(self):
        """The end-to-end point: rotating in the browser must leave the
        labels readable, which means the sync handler has to rebuild them."""
        import tempfile
        from pathlib import Path

        from vibeview.app import create_app
        from vibeview.converters import xyz_to_qvf
        from vibeview.qvf import QVFReader

        q = Path(tempfile.mktemp(suffix=".qvf"))
        q.write_bytes(xyz_to_qvf(b"2\nc\nO 0 0 0\nH 0 0 1\n").getvalue())
        try:
            app = create_app(QVFReader(q))
        finally:
            q.unlink(missing_ok=True)
        # Must not raise even with no labels registered.
        app.controller.sync_client_camera(
            [5.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 1.0], 3.0
        )
