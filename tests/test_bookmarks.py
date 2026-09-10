"""Test interactive bookmarks and session save/restore (Phase K3/K5)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import pytest


def _make_minimal_qvf() -> Path:
    """Create a .qvf with just a structure section."""
    structure = json.dumps(
        {
            "atoms": [{"symbol": "He", "position": [0, 0, 0], "atomic_number": 2}],
            "pbc": [False, False, False],
        }
    ).encode()
    sections = [
        {
            "id": "structure",
            "kind": "structure",
            "members": {
                "structure": {
                    "path": "sections/structure.json",
                    "format": "json",
                    "sha256": hashlib.sha256(structure).hexdigest(),
                }
            },
        }
    ]
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.9.0", "calculation": "test"},
        "sections": sections,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("sections/structure.json", structure)
    return Path(tmp.name)


class TestBookmarkState:
    """Verify bookmark state variables are initialised correctly."""

    def test_user_bookmarks_initial_empty(self) -> None:
        """User bookmarks start as empty list."""
        import os

        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        path = _make_minimal_qvf()
        try:
            reader = QVFReader(path)
            app = create_app(reader)
            state = app.state
            assert state.user_bookmarks == []
            assert state.user_bookmark_names == []
            assert state.session_dirty is False
            assert state.session_path == ""
            reader.close()
        finally:
            path.unlink()

    def test_session_serialization_roundtrip(self) -> None:
        """Session JSON can be round-tripped without data loss."""
        session = {
            "version": 1,
            "active_section": "vol_dens_0",
            "isovalue": 0.05,
            "colormap": "viridis",
            "opacity": 0.6,
            "camera": {"position": [1, 2, 3]},
            "replication": [2, 2, 2],
            "user_bookmarks": [
                {
                    "name": "top_view",
                    "camera": {"position": [0, 0, 10]},
                    "section_id": "structure",
                    "isovalue": 0.03,
                    "colormap": "plasma",
                    "opacity": 0.7,
                }
            ],
        }
        with tempfile.NamedTemporaryFile(suffix=".vibe-session", mode="w", delete=False) as f:
            json.dump(session, f)
            session_path = f.name

        try:
            loaded = json.loads(Path(session_path).read_text())
            assert loaded["version"] == 1
            assert loaded["active_section"] == "vol_dens_0"
            assert len(loaded["user_bookmarks"]) == 1
            assert loaded["user_bookmarks"][0]["name"] == "top_view"
            assert loaded["user_bookmarks"][0]["isovalue"] == 0.03
        finally:
            Path(session_path).unlink()


class TestBookmarkRoundTrip:
    """Drive the real controllers, end to end.

    The tests above only checked initial state and round-tripped a
    hand-written dict through ``json`` — which exercises the json module, not
    vibe-view. Nothing ever called ``save_user_bookmark`` /
    ``apply_user_bookmark``, which is why two breakages sat here unnoticed:

    * ``save_user_bookmark`` called ``plotter.camera.to_dict()``; PyVista's
      ``Camera`` has no such method, and the ``hasattr(plotter, "camera")``
      guard checked the wrong object — so *every* save raised AttributeError
      and no bookmark was ever stored.
    * Both save and restore used ``state.active_section``, a key that is never
      declared and that nothing renders from, so a bookmark recorded
      ``section_id: ""`` and restoring one never re-opened the section.
    """

    def _app(self):
        import os

        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        path = _make_minimal_qvf()
        app = create_app(QVFReader(path))
        return app, path

    @staticmethod
    def _volume_internals(ctrl):
        """Expose the render state hidden behind the controller closures."""
        import inspect

        closed = inspect.getclosurevars(ctrl.update_isovalue.func).nonlocals
        return closed["viewer_state"], closed["plotter"]

    @staticmethod
    def _replication_internals(ctrl):
        """Expose the periodic render state behind the replication controller."""
        import inspect

        closed = inspect.getclosurevars(ctrl.update_replication.func).nonlocals
        return closed["viewer_state"], closed["plotter"]

    @staticmethod
    def _record_restore_order(monkeypatch, app_module) -> list[object]:
        """Record the section rebuild and saved-camera application order."""
        events: list[object] = []
        original_activate = app_module._activate_volume
        original_camera = app_module._apply_camera

        def record_activate(*args, **kwargs):
            events.append("activate")
            return original_activate(*args, **kwargs)

        def record_camera(*args, **kwargs):
            camera = args[1] if len(args) > 1 else kwargs.get("camera")
            events.append(("camera", camera))
            return original_camera(*args, **kwargs)

        monkeypatch.setattr(app_module, "_activate_volume", record_activate)
        monkeypatch.setattr(app_module, "_apply_camera", record_camera)
        return events

    def test_save_records_the_open_section_and_a_usable_camera(self) -> None:
        app, path = self._app()
        try:
            state, ctrl = app.state, app.controller
            ctrl.activate_section("structure")
            ctrl.save_user_bookmark("view-a")

            assert len(state.user_bookmarks) == 1, "save raised or stored nothing"
            bm = state.user_bookmarks[0]
            assert bm["section_id"] == "structure", "recorded the phantom key again"
            # camera must carry what _apply_camera reads back
            assert bm["camera"], "camera serialised empty"
            assert {"position", "focal_point", "view_up"} <= set(bm["camera"])
        finally:
            path.unlink()

    def test_restore_reopens_the_section(self) -> None:
        app, path = self._app()
        try:
            state, ctrl = app.state, app.controller
            ctrl.activate_section("structure")
            ctrl.save_user_bookmark("view-a")
            # move somewhere else, then restore
            state.selected_section = "elsewhere"
            assert state.selected_section != "structure"

            ctrl.apply_user_bookmark("view-a")
            assert state.selected_section == "structure", "restore did not re-open"
        finally:
            path.unlink()

    def test_restore_volume_bookmark_rebuilds_saved_render_state(
        self, sample_qvf, monkeypatch
    ) -> None:
        """The bookmark picker must restore the scene, not only its controls."""
        import vibeview.app as app_module
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        saved = (0.123456, "magma", 0.314159)
        reader = QVFReader(sample_qvf)
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        try:
            ctrl.activate_section("density")
            ctrl.update_isovalue(saved[0])
            ctrl.update_colormap(saved[1])
            ctrl.update_opacity(saved[2])
            ctrl.save_user_bookmark("saved-volume")
            saved_camera = state.user_bookmarks[0]["camera"]

            ctrl.update_isovalue(0.05)
            ctrl.update_colormap("viridis")
            ctrl.update_opacity(0.8)

            events = self._record_restore_order(monkeypatch, app_module)
            ctrl.apply_user_bookmark("saved-volume")

            viewer_state, plotter = self._volume_internals(ctrl)
            hints = viewer_state.get_volume_hints(
                "density", kind="volume.density"
            )
            actor = plotter.actors["volume_density"]
            actual = {
                "ui": (state.isovalue, state.colormap, state.opacity),
                "hints": (hints.isovalue, hints.colormap, hints.opacity),
                "mesh_cache_key": viewer_state._mesh_cache["density"]["key"],
                "actor_opacity": actor.GetProperty().GetOpacity(),
                "restore_order": events,
            }
            expected = {
                "ui": saved,
                "hints": saved,
                "mesh_cache_key": viewer_state.mesh_cache_key(saved[0]),
                "actor_opacity": saved[2],
                "restore_order": ["activate", ("camera", saved_camera)],
            }
            assert actual == expected
        finally:
            reader.close()

    def test_load_session_rebuilds_saved_volume_render_state(
        self, sample_qvf, tmp_path, monkeypatch
    ) -> None:
        """Version-1 sessions restore volume hints, mesh, actor, then camera."""
        import vibeview.app as app_module
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        saved = (0.234567, "plasma", 0.271828)
        camera = {
            "position": [8.0, 3.0, 2.0],
            "focal_point": [0.0, 0.0, 0.0],
            "view_up": [0.0, 1.0, 0.0],
            "view_angle": 27.0,
        }
        session_path = tmp_path / "saved-volume.vibe-session"
        session_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "active_section": "density",
                    "isovalue": saved[0],
                    "colormap": saved[1],
                    "opacity": saved[2],
                    "camera": camera,
                    "replication": [1, 1, 1],
                    "user_bookmarks": [],
                }
            )
        )

        reader = QVFReader(sample_qvf)
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        try:
            ctrl.activate_section("density")
            ctrl.update_isovalue(0.05)
            ctrl.update_colormap("viridis")
            ctrl.update_opacity(0.8)

            events = self._record_restore_order(monkeypatch, app_module)
            ctrl.load_session(str(session_path))

            viewer_state, plotter = self._volume_internals(ctrl)
            hints = viewer_state.get_volume_hints(
                "density", kind="volume.density"
            )
            actor = plotter.actors["volume_density"]
            actual = {
                "ui": (state.isovalue, state.colormap, state.opacity),
                "hints": (hints.isovalue, hints.colormap, hints.opacity),
                "mesh_cache_key": viewer_state._mesh_cache["density"]["key"],
                "actor_opacity": actor.GetProperty().GetOpacity(),
                "restore_order": events,
            }
            expected = {
                "ui": saved,
                "hints": saved,
                "mesh_cache_key": viewer_state.mesh_cache_key(saved[0]),
                "actor_opacity": saved[2],
                "restore_order": ["activate", ("camera", camera)],
            }
            assert actual == expected
        finally:
            reader.close()

    def test_load_session_restores_periodic_replication_scene_and_camera(
        self, showcase_qvf, tmp_path, monkeypatch
    ) -> None:
        """A periodic session restores UI, render state, geometry, then camera."""
        import vibeview.app as app_module
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        reader = QVFReader(showcase_qvf)
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        try:
            ctrl.activate_section("structure")
            ctrl.update_replication(2, 3, 4)
            ctrl.activate_section("vol_dens_0")
            viewer_state, plotter = self._replication_internals(ctrl)
            expanded_bounds = tuple(plotter.bounds)

            session_path = tmp_path / "periodic.vibe-session"
            ctrl.save_session(str(session_path))
            saved_document = json.loads(session_path.read_text())
            saved_camera = saved_document["camera"]

            ctrl.update_replication(1, 1, 1)
            assert tuple(plotter.bounds) != expanded_bounds

            events: list[tuple[str, object]] = []
            original_rebuild = app_module._rebuild_scene
            original_activate = app_module._activate_volume
            original_camera = app_module._apply_camera

            def record_rebuild(*args, **kwargs):
                viewer = args[2]
                events.append(("rebuild", tuple(viewer.replication)))
                return original_rebuild(*args, **kwargs)

            def record_activate(*args, **kwargs):
                section = args[4] if len(args) > 4 else kwargs.get("section")
                events.append(("activate", section.id))
                return original_activate(*args, **kwargs)

            def record_camera(*args, **kwargs):
                camera = args[1] if len(args) > 1 else kwargs.get("camera")
                events.append(("camera", camera))
                return original_camera(*args, **kwargs)

            monkeypatch.setattr(app_module, "_rebuild_scene", record_rebuild)
            monkeypatch.setattr(app_module, "_activate_volume", record_activate)
            monkeypatch.setattr(app_module, "_apply_camera", record_camera)
            ctrl.load_session(str(session_path))

            assert saved_document["replication"] == [2, 3, 4]
            assert (
                state.replication_nx,
                state.replication_ny,
                state.replication_nz,
            ) == (2, 3, 4)
            assert viewer_state.replication == (2, 3, 4)
            assert tuple(plotter.bounds) == pytest.approx(expanded_bounds)
            assert state.selected_section == "vol_dens_0"
            assert state.active_volume_id == "vol_dens_0"
            assert "volume_vol_dens_0" in plotter.actors
            assert events[0] == ("rebuild", (2, 3, 4))
            assert ("activate", "vol_dens_0") in events[1:]
            assert events[-1] == ("camera", saved_camera)
        finally:
            reader.close()

    @pytest.mark.parametrize(
        "session_patch",
        [
            pytest.param({}, id="missing"),
            pytest.param({"replication": [2, 2, 2]}, id="already-current"),
            pytest.param({"replication": [2, 3]}, id="wrong-length"),
            pytest.param({"replication": [2, "bad", 4]}, id="non-numeric"),
            pytest.param({"replication": [2, 10**400, 4]}, id="overflow"),
        ],
    )
    def test_load_session_preserves_replication_without_a_valid_change(
        self, showcase_qvf, tmp_path, session_patch, monkeypatch
    ) -> None:
        """Legacy, malformed, or unchanged replication cannot rebuild the scene."""
        import vibeview.app as app_module
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        reader = QVFReader(showcase_qvf)
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        try:
            ctrl.activate_section("structure")
            ctrl.update_replication(2, 2, 2)
            viewer_state, plotter = self._replication_internals(ctrl)
            current_bounds = tuple(plotter.bounds)

            session = {
                "version": 1,
                "active_section": None,
                "camera": None,
                "user_bookmarks": [],
            }
            session.update(session_patch)
            session_path = tmp_path / "invalid-replication.vibe-session"
            session_path.write_text(json.dumps(session))

            def unexpected_rebuild(*_args, **_kwargs):
                pytest.fail("session replication unexpectedly rebuilt the scene")

            monkeypatch.setattr(app_module, "_rebuild_scene", unexpected_rebuild)
            ctrl.load_session(str(session_path))

            assert "loaded" in state.status_message.lower()
            assert (
                state.replication_nx,
                state.replication_ny,
                state.replication_nz,
            ) == (2, 2, 2)
            assert viewer_state.replication == (2, 2, 2)
            assert tuple(plotter.bounds) == pytest.approx(current_bounds)
        finally:
            reader.close()

    def test_restore_ignores_invalid_saved_volume_numbers(self, sample_qvf) -> None:
        """Malformed saved values cannot poison volume hints or actors."""
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        valid = (0.08, "viridis", 0.4)
        invalid_settings = [
            (float("nan"), "not-a-colormap", float("inf")),
            (0.0, "still-not-a-colormap", -0.1),
            (-0.1, "invalid-again", 1.1),
        ]
        try:
            ctrl.activate_section("density")
            ctrl.update_isovalue(valid[0])
            ctrl.update_colormap(valid[1])
            ctrl.update_opacity(valid[2])
            viewer_state, plotter = self._volume_internals(ctrl)

            for isovalue, colormap, opacity in invalid_settings:
                state.user_bookmarks = [
                    {
                        "name": "malformed",
                        "camera": None,
                        "section_id": "density",
                        "isovalue": isovalue,
                        "colormap": colormap,
                        "opacity": opacity,
                    }
                ]

                ctrl.apply_user_bookmark("malformed")

                hints = viewer_state.get_volume_hints(
                    "density", kind="volume.density"
                )
                assert (state.isovalue, state.colormap, state.opacity) == valid
                assert (hints.isovalue, hints.colormap, hints.opacity) == valid
                assert viewer_state._mesh_cache["density"]["key"] == (
                    viewer_state.mesh_cache_key(valid[0])
                )
                assert plotter.actors["volume_density"].GetProperty().GetOpacity() == (
                    valid[2]
                )
        finally:
            reader.close()

    def test_widget_contract_round_trip(self) -> None:
        """Drive the exact calls the widgets make, end to end.

        The Save View button is bound as
        ``click=(ctrl.save_user_bookmark, "[new_bookmark_name]")`` and the
        bookmark picker as
        ``update_modelValue=(ctrl.apply_user_bookmark, "[$event]")``. This
        reproduces those two calls against the state key the text field writes,
        which is the part a browser click exercises beyond the controller
        tests above. (The UI itself is not drivable in the headless preview —
        its viewport collapses to 0x0 — so this pins the contract instead.)
        """
        app, path = self._app()
        try:
            state, ctrl = app.state, app.controller
            ctrl.activate_section("structure")

            state.new_bookmark_name = "view-a"          # typing into the field
            ctrl.save_user_bookmark(state.new_bookmark_name)   # the button
            assert [b["name"] for b in state.user_bookmarks] == ["view-a"]
            assert state.user_bookmarks[0]["section_id"] == "structure"

            state.selected_section = "elsewhere"
            ctrl.apply_user_bookmark("view-a")          # the picker
            assert state.selected_section == "structure"
        finally:
            path.unlink()

    def test_session_save_and_load_round_trip(self) -> None:
        """Save a session, load it back, and land on the same section.

        Four separate breakages lived on this path, none reachable by the
        json-only tests above:

        * ``_session_payload`` built ``list(getattr(state, "replication", ...))``
          — but ``replication`` is not a declared state key, and a trame state
          returns None for an unknown name instead of the getattr default, so
          ``list(None)`` raised TypeError and every save died.
        * ``@ctrl.set("save_session")`` was attached to ``_session_payload``,
          the private helper, so the Save Session button called that and the
          real ``save_session`` was unreachable from the UI.
        * ``save_session`` and ``load_session`` both used ``Path`` without
          importing it into their scope — NameError on every call.
        * ``save_session()`` with no argument left ``sp`` a plain str taken
          from ``state.session_path``, so ``sp.write_text`` would fail.
        """
        import shutil
        import tempfile

        app, path = self._app()
        tmp = Path(tempfile.mkdtemp())
        try:
            state, ctrl = app.state, app.controller
            ctrl.activate_section("structure")

            target = tmp / "explicit.json"
            ctrl.save_session(str(target))
            assert target.exists(), "save_session wrote nothing"
            doc = json.loads(target.read_text())
            assert doc["version"] == 1, "loader only accepts version 1"
            assert doc["replication"] == [1, 1, 1]

            state.selected_section = "elsewhere"
            ctrl.load_session(str(target))
            assert state.selected_section == "structure", "load did not restore"

            # the no-argument form the Save Session button uses
            state.status_message = ""
            ctrl.save_session()
            assert "saved" in state.status_message.lower()

            # autosave must produce a document the loader accepts
            ctrl.auto_save_session()
            auto = Path(state.session_path)
            assert auto.exists()
            state.selected_section = "elsewhere"
            ctrl.load_session(str(auto))
            assert state.selected_section == "structure", "autosave not loadable"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            path.unlink()


class TestPresentationMode:
    """Presentation mode must be a faithful, controllable bookmark slideshow."""

    @staticmethod
    def _app():
        import os

        os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
        reader = QVFReader(path)
        return create_app(reader), reader

    @staticmethod
    def _seed_slides(state) -> None:
        state.user_bookmarks = [
            {
                "name": "Structure view",
                "camera": None,
                "section_id": "structure",
                "isovalue": 0.02,
                "colormap": "viridis",
                "opacity": 0.4,
            },
            {
                "name": "Wavefunction view",
                "camera": None,
                "section_id": "wf",
                "isovalue": 0.07,
                "colormap": "plasma",
                "opacity": 0.8,
            },
        ]

    def test_slides_preserve_settings_activate_sections_and_wrap(self) -> None:
        app, reader = self._app()
        state, ctrl = app.state, app.controller
        try:
            self._seed_slides(state)
            state.selected_section = None

            ctrl.toggle_presentation()

            assert state.presentation_mode is True
            assert state.presentation_slides == state.user_bookmarks
            assert state.presentation_slides is not state.user_bookmarks
            assert state.selected_section == "structure"
            assert (state.isovalue, state.colormap, state.opacity) == (
                0.02,
                "viridis",
                0.4,
            )

            ctrl.presentation_next()
            assert state.presentation_slide == 1
            assert state.selected_section == "wf"
            assert (state.isovalue, state.colormap, state.opacity) == (
                0.07,
                "plasma",
                0.8,
            )

            ctrl.presentation_next()
            assert state.presentation_slide == 0
            assert state.selected_section == "structure"
            ctrl.presentation_prev()
            assert state.presentation_slide == 1
            assert state.selected_section == "wf"

            ctrl.toggle_presentation()
            assert state.presentation_mode is False
            assert state.presentation_auto_advance is False
        finally:
            reader.close()

    def test_navigation_is_a_noop_outside_a_populated_presentation(self) -> None:
        app, reader = self._app()
        state, ctrl = app.state, app.controller
        try:
            state.status_message = "unchanged"
            ctrl.presentation_next()
            ctrl.presentation_prev()
            assert state.presentation_slide == 0
            assert state.status_message == "unchanged"

            state.presentation_mode = True
            state.presentation_slides = []
            ctrl.presentation_next()
            ctrl.presentation_prev()
            assert state.status_message == "unchanged"

            state.presentation_mode = False
            ctrl.toggle_presentation()
            assert state.presentation_mode is False
            assert state.status_message == "Create bookmarks first to use as slides"
        finally:
            reader.close()

    def test_entering_presentation_closes_edit_mode_and_clears_selection(self) -> None:
        app, reader = self._app()
        state, ctrl = app.state, app.controller
        try:
            self._seed_slides(state)
            state.edit_mode = True
            state.edit_selected = [0]

            ctrl.toggle_presentation()

            assert state.presentation_mode is True
            assert state.edit_mode is False
            assert state.edit_selected == []
        finally:
            reader.close()

    def test_volume_slide_updates_render_hints_before_activation(self, sample_qvf) -> None:
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        reader = QVFReader(sample_qvf)
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        try:
            state.user_bookmarks = [
                {
                    "name": "Density view",
                    "camera": None,
                    "section_id": "density",
                    "isovalue": 0.123456,
                    "colormap": "magma",
                    "opacity": 0.314159,
                }
            ]

            ctrl.toggle_presentation()

            # _activate_volume copies ViewerState hints back into these state
            # keys. Defaults here mean the slide changed labels but rendered
            # the wrong surface; the saved values prove hints were updated
            # before activation rebuilt the mesh.
            assert state.selected_section == "density"
            assert (state.isovalue, state.colormap, state.opacity) == (
                0.123456,
                "magma",
                0.314159,
            )
        finally:
            reader.close()

    def test_panel_slide_uses_the_full_presentation_surface(self, showcase_qvf) -> None:
        from vibeview.app import create_app
        from vibeview.qvf import QVFReader

        reader = QVFReader(showcase_qvf)
        app = create_app(reader)
        state, ctrl = app.state, app.controller
        try:
            state.user_bookmarks = [
                {
                    "name": "SCF convergence",
                    "camera": None,
                    "section_id": "scf_history",
                },
                {
                    "name": "Structure view",
                    "camera": None,
                    "section_id": "structure",
                },
            ]

            ctrl.toggle_presentation()
            assert state.selected_section == "scf_history"
            assert state.presentation_panel_slide is True
            assert state.chart_html

            ctrl.presentation_next()
            assert state.selected_section == "structure"
            assert state.presentation_panel_slide is False
        finally:
            reader.close()

    def test_each_slide_applies_its_saved_camera(self, monkeypatch) -> None:
        import vibeview.app as app_module

        app, reader = self._app()
        state, ctrl = app.state, app.controller
        applied = []
        original = app_module._apply_camera

        def record(plotter, camera) -> None:
            applied.append(camera)
            original(plotter, camera)

        try:
            self._seed_slides(state)
            state.user_bookmarks[0]["camera"] = {
                "position": [0.0, 0.0, 8.0],
                "focal_point": [0.0, 0.0, 0.0],
                "view_up": [0.0, 1.0, 0.0],
            }
            state.user_bookmarks[1]["camera"] = {
                "position": [8.0, 0.0, 0.0],
                "focal_point": [0.0, 0.0, 0.0],
                "view_up": [0.0, 0.0, 1.0],
            }
            monkeypatch.setattr(app_module, "_apply_camera", record)

            ctrl.toggle_presentation()
            ctrl.presentation_next()

            assert [camera["position"] for camera in applied] == [
                [0.0, 0.0, 8.0],
                [8.0, 0.0, 0.0],
            ]
        finally:
            reader.close()

    def test_auto_advance_runs_and_cancels_on_the_viewer_loop(self) -> None:
        import asyncio

        async def drive() -> None:
            app, reader = self._app()
            state, ctrl = app.state, app.controller
            try:
                self._seed_slides(state)
                state.presentation_slide_duration = 0.1
                ctrl.toggle_presentation()
                ctrl.toggle_presentation_auto_advance()
                assert state.presentation_auto_advance is True

                for _ in range(30):
                    await asyncio.sleep(0.02)
                    if state.presentation_slide == 1:
                        break
                assert state.presentation_slide == 1

                ctrl.toggle_presentation_auto_advance()
                assert state.presentation_auto_advance is False
                stopped_at = state.presentation_slide
                await asyncio.sleep(0.15)
                assert state.presentation_slide == stopped_at

                ctrl.toggle_presentation_auto_advance()
                assert state.presentation_auto_advance is True
                ctrl.toggle_presentation()
                assert state.presentation_mode is False
                assert state.presentation_auto_advance is False
                stopped_at = state.presentation_slide
                await asyncio.sleep(0.15)
                assert state.presentation_slide == stopped_at
            finally:
                reader.close()

        asyncio.run(drive())

    def test_template_and_help_expose_the_real_presentation_contract(self) -> None:
        app, reader = self._app()
        try:
            template = app.state["trame__template_main"]
            assert '<VOverlay role="region" aria-label="Presentation mode"' in template
            assert 'v-model="presentation_mode"' in template
            assert ':scrim="false"' in template
            assert ':retainFocus="false"' in template
            assert 'contentClass="vv-presentation-overlay-content"' in template
            assert 'v-show="!presentation_mode || !presentation_panel_slide"' in template
            assert (
                'v-if="selected_section && '
                '(!presentation_mode || presentation_panel_slide)"' in template
            )
            assert ":class=\"presentation_mode ? 'vv-presentation-panel' : ''\"" in template
            for label in (
                "Previous slide",
                "Start automatic slide advance",
                "Next slide",
                "Exit presentation",
            ):
                assert label in template

            registered = set(app.controller._triggers)
            assert {"toggle_presentation", "presentation_prev", "presentation_next"} <= (
                registered
            )
            assert "f" not in app.state.shortcut_keys
            assert app.state.shortcut_keys["Ctrl/Cmd+K"] == "Command palette"
            assert "Ctrl/Cmd+Z" in app.state.shortcut_keys
            assert "Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y" in app.state.shortcut_keys
        finally:
            reader.close()

    def test_shortcut_handler_routes_only_the_modes_and_modifiers_it_advertises(
        self,
    ) -> None:
        import inspect

        import vibeview.app as app_module

        source = inspect.getsource(app_module.create_app)
        for contract in (
            "var command=e.ctrlKey||e.metaKey;",
            "target.isContentEditable",
            "if(document.querySelector('[role=dialog]'))return;",
            "if(k==='escape'){if(e.repeat)return;",
            "t.state.get('presentation_mode')",
            "t.state.get('edit_mode')",
            "k==='arrowleft'||k==='arrowright'",
            "'presentation_prev' : 'presentation_next'",
            "t.state.get('presentation_mode')&&(k==='e'||k==='delete'||",
            "k==='backspace'||(command&&(k==='z'||k==='y'))",
            "t.trigger(e.shiftKey?'edit_redo':'edit_undo')",
            "if(command||e.altKey||e.repeat)return;",
        ):
            assert contract in source
        assert "if(k==='escape'){t.trigger('toggle_edit_mode')" not in source
