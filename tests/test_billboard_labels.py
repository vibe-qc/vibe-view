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


class TestFollowerArrays:
    """The mesh carries what the browser needs to re-face each glyph (#111)."""

    ANCHORS = [np.zeros(3), np.array([3.0, 1.0, -2.0])]
    CAM = ((0.0, -14.0, 3.0), (3.0, 0.0, 0.0), (0.0, 0.0, 1.0))

    def _arrays(self, mesh):
        from vibeview.renderers.structure import LABEL_ANCHOR_ARRAY, LABEL_LOCAL_ARRAY

        return (
            np.asarray(mesh.point_data[LABEL_ANCHOR_ARRAY]),
            np.asarray(mesh.point_data[LABEL_LOCAL_ARRAY]),
            np.asarray(mesh.points),
        )

    def test_arrays_are_plain_float32_point_data(self):
        mesh = build_label_mesh(self.ANCHORS, ["O1", "H2"], camera=self.CAM)
        anchors, local, points = self._arrays(mesh)
        assert anchors.dtype == np.float32 and local.dtype == np.float32
        assert anchors.shape == local.shape == points.shape
        # Not the active scalars: the mapper must not colour by them.
        assert mesh.point_data.active_scalars_name is None

    def test_each_glyph_has_one_anchor(self):
        mesh = build_label_mesh(self.ANCHORS, ["O1", "H2"], camera=self.CAM)
        anchors, _local, _points = self._arrays(mesh)
        distinct = np.unique(anchors, axis=0)
        assert len(distinct) == 2
        for anchor in self.ANCHORS:
            assert any(np.allclose(anchor, d) for d in distinct)

    def test_basis_times_local_rebuilds_the_billboarded_points(self):
        """What the client computes per camera change must equal the mesh."""
        mesh = build_label_mesh(self.ANCHORS, ["O1", "H2"], camera=self.CAM, lift=0.55)
        anchors, local, points = self._arrays(mesh)
        for anchor in np.unique(anchors, axis=0):
            sel = np.all(anchors == anchor, axis=1)
            right, up, forward = _billboard_basis(anchor.astype(float), self.CAM)
            rebuilt = anchor + local[sel] @ np.vstack((right, up, forward))
            assert np.allclose(rebuilt, points[sel], atol=1e-5)

    def test_lift_is_folded_into_local_y(self):
        mesh = build_label_mesh([ORIGIN], ["X"], camera=self.CAM, lift=5.0)
        _anchors, local, _points = self._arrays(mesh)
        assert local[:, 1].mean() == pytest.approx(5.0, abs=0.5)

    def test_unoriented_mesh_carries_the_same_frame(self):
        """The flat placement lifts along world z; the local frame still
        describes the glyph so the client can face it once it moves."""
        mesh = build_label_mesh(self.ANCHORS, ["O1", "H2"], camera=None, lift=0.55)
        anchors, local, points = self._arrays(mesh)
        flat = anchors + local - [0.0, 0.55, 0.0] + [0.0, 0.0, 0.55]
        assert np.allclose(flat, points, atol=1e-5)


class TestFollowerSerializer:
    """trame-vtk drops unused arrays; the hook sends the label arrays (#111)."""

    @pytest.fixture
    def registry(self):
        pytest.importorskip("trame_vtk")
        from trame_vtk.modules.vtk.serializers import initialize_serializers, registry

        initialize_serializers()
        yield registry
        initialize_serializers()  # drop the wrapper again

    def _fields(self, actor):
        from trame_vtk.modules.vtk.serializers import SynchronizationContext, serialize

        out = serialize(None, actor, "label-actor", SynchronizationContext(), 0)

        def walk(node):
            for dep in node.get("dependencies", []):
                if "fields" in dep.get("properties", {}):
                    return dep["properties"]["fields"]
                found = walk(dep)
                if found is not None:
                    return found
            return None

        return walk(out) or []

    def test_install_is_idempotent_and_marks_the_registry(self, registry):
        from vibeview.app.label_followers import install_label_array_serializer

        assert install_label_array_serializer() is True
        wrapper = registry.SERIALIZERS["vtkPolyData"]
        assert install_label_array_serializer() is True
        assert registry.SERIALIZERS["vtkPolyData"] is wrapper

    def test_install_accepts_trame_server_ready_arguments(self, registry):
        """trame calls ``on_server_ready`` with the whole state as kwargs."""
        from vibeview.app.label_followers import install_label_array_serializer

        assert install_label_array_serializer(trame__scripts=[], show_atom_labels=True)

    def test_install_is_a_no_op_on_an_empty_registry(self, registry):
        from vibeview.app.label_followers import install_label_array_serializer

        registry.SERIALIZERS.clear()
        assert install_label_array_serializer() is False
        assert "vtkPolyData" not in registry.SERIALIZERS

    def test_label_mesh_is_sent_with_both_arrays(self, registry):
        import pyvista as pv

        from vibeview.app.label_followers import install_label_array_serializer
        from vibeview.renderers.structure import LABEL_ANCHOR_ARRAY, LABEL_LOCAL_ARRAY

        install_label_array_serializer()
        p = pv.Plotter(off_screen=True)
        try:
            actor = p.add_mesh(
                build_label_mesh([ORIGIN], ["O1"], camera=CAM_X),
                color="white",
                lighting=False,
                name="atom_index_labels",
            )
            fields = {f["name"]: f for f in self._fields(actor)}
        finally:
            p.close()
        assert set(fields) == {LABEL_ANCHOR_ARRAY, LABEL_LOCAL_ARRAY}
        for field in fields.values():
            assert field["location"] == "pointData"
            assert field["registration"] == "addArray"
            assert field["numberOfComponents"] == 3

    def test_other_meshes_are_serialized_as_before(self, registry):
        import pyvista as pv

        from vibeview.app.label_followers import install_label_array_serializer

        install_label_array_serializer()
        p = pv.Plotter(off_screen=True)
        try:
            sphere = pv.Sphere()
            sphere.point_data.set_array(np.zeros(sphere.n_points, dtype=np.float32), "unused")
            actor = p.add_mesh(sphere, color="red", name="atom_0_0")
            names = [f["name"] for f in self._fields(actor)]
        finally:
            p.close()
        assert "unused" not in names


@pytest.fixture(scope="module")
def served():
    pytest.importorskip("trame")
    from tests._served import served_viewer

    return served_viewer()


class TestServedFollower:
    """The page the browser receives carries the follower (#111)."""

    def test_script_and_arrays_are_in_the_page(self, served):
        assert "window.vvLabelFollow = function" in served
        assert "'vv_label_anchor'" in served and "'vv_label_local'" in served

    def test_glyphs_are_placed_in_the_vertex_shader(self, served):
        """The arrays become vertex attributes and every vtk.js position
        product is pointed at the camera-facing position."""
        assert "attribute vec3 vv_label_anchorMC;" in served
        assert "attribute vec3 vv_label_localMC;" in served
        assert "'MCPCMatrix * vvLabelMC'" in served
        assert "'MCVCMatrix * vvLabelMC'" in served
        assert "setCustomShaderAttributes" in served
        assert "ShadersCallbacks" in served

    def test_no_geometry_is_rewritten_on_the_client(self, served):
        """The CPU follower rewrote point coordinates every camera change;
        the shader follower never touches the mesh."""
        script = served.split("window.vvLabelFollow = function", 1)[0]
        assert "getPoints()" not in script.split("var ANCHOR", 1)[1]

    def test_view_hands_the_follower_its_renderer(self, served):
        handlers = {
            "StartAnimation": "window.vvLabelFollow($event, true)",
            "MouseEnter": "window.vvLabelFollow($event)",
            "MouseMove": "window.vvLabelFollow($event)",
        }
        for event, call in handlers.items():
            assert f'@{event}="' in served, event
            tag = served.split(f'@{event}="', 1)[1].split('"', 1)[0]
            assert call in tag, (event, tag)
        # After each scene load, new label meshes go onto the shader without
        # waiting for an interaction (#111, found by the #112 lane).
        loaded = served.split('@afterSceneLoaded="', 1)[1].split('"', 1)[0]
        assert "window.vvLabelFollow.rescan()" in loaded
        # Nothing runs on every animation frame: the GPU follows the camera.
        animation = served.split('@Animation="', 1)
        assert len(animation) == 1 or "vvLabelFollow" not in animation[1].split('"', 1)[0]


class TestInteractionEndKeepsTheClientCopy:
    """The client faced its labels throughout the drag (#111), so the
    server rebuilds its own copy for captures and pushes nothing back."""

    def test_sync_rebuilds_server_labels_without_a_push(self, monkeypatch):
        import tempfile
        from pathlib import Path

        import vibeview.app.helpers as helpers
        import vibeview.renderers.structure as structure
        from vibeview.app import create_app
        from vibeview.converters import xyz_to_qvf
        from vibeview.qvf import QVFReader

        q = Path(tempfile.mktemp(suffix=".qvf"))
        q.write_bytes(xyz_to_qvf(b"2\nc\nO 0 0 0\nH 0 0 1\n").getvalue())
        try:
            app = create_app(QVFReader(q))
        finally:
            q.unlink(missing_ok=True)

        rebuilt: list[object] = []
        pushes: list[object] = []
        monkeypatch.setattr(
            structure, "reorient_labels", lambda plotter: rebuilt.append(plotter) or 1
        )
        monkeypatch.setattr(helpers, "_push_view", lambda plotter: pushes.append(plotter))
        app.controller.sync_client_camera(
            [5.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 1.0], 3.0
        )
        assert len(rebuilt) == 1
        assert pushes == []
