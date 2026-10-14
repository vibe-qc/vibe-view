"""Browser-viewer correctness contracts (vibe-view#47, follow-up to audit #37).

Each test names the #37 finding id it pins. They drive the controllers
headlessly (off-screen PyVista, no served client), so a behaviour that only
exists in the browser DOM is checked at the state/controller level here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import struct
import tempfile
import zipfile
from pathlib import Path

import pytest


def _scene_hooks(plotter):
    """vibeview.app's hooks record for ``plotter``, looked up at call time:
    another test module reloads vibeview.app, and a name imported at
    collection would then read the old module's registry."""
    from vibeview.app import _scene_hooks as hooks

    return hooks(plotter)


os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

WATER = [
    {"symbol": "O", "position": [0.0, 0.0, 0.1173], "atomic_number": 8},
    {"symbol": "H", "position": [0.0, 0.7572, -0.4692], "atomic_number": 1},
    {"symbol": "H", "position": [0.0, -0.7572, -0.4692], "atomic_number": 1},
]


def _qvf(atoms: list[dict] | None = None, directory: Path | None = None) -> Path:
    structure = json.dumps({"atoms": atoms or WATER, "pbc": [False, False, False]}).encode()
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "correctness"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {
                    "structure": {
                        "path": "s.json",
                        "format": "json",
                        "sha256": hashlib.sha256(structure).hexdigest(),
                    }
                },
            }
        ],
    }
    fd, name = tempfile.mkstemp(suffix=".qvf", dir=directory)
    os.close(fd)
    with zipfile.ZipFile(name, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", structure)
    return Path(name)


@pytest.fixture(scope="module")
def viewer():
    import vibeview.app as appmod
    from vibeview.qvf import QVFReader

    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    workdir = Path(tempfile.mkdtemp(prefix="vv-correctness-"))
    path = _qvf(directory=workdir)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(appmod.pv, "Plotter", create_plotter)
        app = appmod.create_app(QVFReader(path))
    app.controller.activate_section("structure")
    app.plotter = created[0]
    app.workdir = workdir
    yield app
    shutil.rmtree(workdir, ignore_errors=True)


def _decode(data_url: str) -> bytes:
    assert data_url.startswith("data:"), data_url[:40]
    return base64.b64decode(data_url.split(",", 1)[1])


# ── #37-23: STL export ───────────────────────────────────────────────


def test_stl_export_writes_a_binary_stl_of_the_scene(viewer):
    """#37-23: ``export_geometry('stl')`` raised pyvista's ValueError
    (``filename must end with ".obj"``) because it called ``export_obj`` on
    a ``.stl`` temp name; before that it wrote OBJ bytes under ``.stl``."""
    state = viewer.state
    state.export_data = ""
    viewer.controller.export_geometry("stl")
    assert state.export_filename.endswith(".stl")
    assert state.export_data.startswith("data:model/stl;base64,")
    raw = _decode(state.export_data)
    # Binary STL: 80-byte header, uint32 triangle count, 50 bytes per facet.
    (n_tri,) = struct.unpack("<I", raw[80:84])
    assert n_tri > 0
    assert len(raw) == 84 + 50 * n_tri
    assert not raw.lstrip().startswith(b"#")  # not an OBJ text file


def test_stl_export_spans_the_whole_molecule(viewer):
    """The merged mesh is in world coordinates: every atom's sphere is in it."""
    import numpy as np

    viewer.controller.export_geometry("stl")
    raw = _decode(viewer.state.export_data)
    (n_tri,) = struct.unpack("<I", raw[80:84])
    facets = np.frombuffer(
        raw[84:],
        dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
        count=n_tri,
    )
    verts = facets["v"].reshape(-1, 3)
    # Both hydrogens (y = +-0.757 A) must be covered.
    assert verts[:, 1].max() > 0.75
    assert verts[:, 1].min() < -0.75


# ── #37-1 / #37-2: session file location ─────────────────────────────


def test_save_and_auto_save_share_the_default_beside_the_archive(viewer, monkeypatch, tmp_path):
    """#37-2: the explicit save wrote ``session.vibe-session`` into the cwd,
    the auto-save wrote ``<stem>.vibe-view-session.json`` beside the archive."""
    monkeypatch.chdir(tmp_path)
    state, ctrl = viewer.state, viewer.controller
    archive = next(viewer.workdir.glob("*.qvf"))
    expected = archive.parent / f"{archive.stem}.vibe-view-session.json"

    state.session_path = ""
    ctrl.save_session()
    assert Path(state.session_path) == expected
    assert expected.is_file()
    assert not (tmp_path / "session.vibe-session").exists()

    expected.unlink()
    state.session_path = ""
    ctrl.auto_save_session()
    assert Path(state.session_path) == expected
    assert expected.is_file()


def test_load_session_button_with_empty_path_loads_the_session(viewer, monkeypatch, tmp_path):
    """#37-1: the Load Session button sends ``''``; ``Path('')`` is ``.``, so
    the load reported ``Is a directory: '.'`` instead of loading."""
    monkeypatch.chdir(tmp_path)
    state, ctrl = viewer.state, viewer.controller
    state.session_path = ""
    ctrl.save_session()
    saved = state.session_path

    state.status_message = ""
    ctrl.load_session("")
    assert state.status_message.startswith("Session loaded"), state.status_message
    assert state.session_path == saved

    # With no path typed, the default location is what loads.
    state.session_path = ""
    ctrl.load_session("")
    assert state.status_message.startswith("Session loaded"), state.status_message


def test_load_session_on_a_directory_reports_it(viewer, tmp_path):
    state = viewer.state
    viewer.controller.load_session(str(tmp_path))
    assert "Is a directory" not in state.status_message
    assert state.status_message.startswith("Not a session file")


# ── #37-8: Center View Here ──────────────────────────────────────────


def test_center_view_here_pans_to_the_targeted_atom(viewer):
    """#37-8: "Center View Here" called the fit-all reset_camera and ignored
    the right-clicked target."""
    import numpy as np

    state, ctrl, plotter = viewer.state, viewer.controller, viewer.plotter
    cam = plotter.camera
    cam.position = (10.0, 0.0, 0.0)
    cam.focal_point = (0.0, 0.0, 0.0)
    offset = np.subtract(cam.position, cam.focal_point)

    ctrl.on_hover({"worldPosition": [0.0, 0.75, -0.46]})  # H, atom 1
    ctrl.context_menu_opened()
    assert state.context_menu_atom_idx == 1
    ctrl.context_center_view()

    np.testing.assert_allclose(cam.focal_point, WATER[1]["position"], atol=1e-6)
    # A pan, not a zoom-to-fit: direction and distance are kept.
    np.testing.assert_allclose(np.subtract(cam.position, cam.focal_point), offset, atol=1e-6)


def test_center_view_here_without_a_target_says_so(viewer, monkeypatch):
    import time

    state, ctrl, plotter = viewer.state, viewer.controller, viewer.plotter
    ctrl.on_hover({"worldPosition": [0.0, 0.0, 0.1]})
    # Open the menu long after the last hover, so it is untargeted.
    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + 3600.0)
    ctrl.context_menu_opened()
    monkeypatch.undo()
    before = tuple(plotter.camera.focal_point)
    ctrl.context_center_view()
    assert tuple(plotter.camera.focal_point) == before
    assert "right-click" in state.status_message


# ── #37-15: restoring a camera keeps the Orthographic switch honest ──


def test_restoring_a_perspective_view_turns_the_orthographic_switch_off(viewer, tmp_path):
    """#37-15: a perspective session camera applied with the switch on left
    camera.parallel_projection=False but orthographic_projection=True."""
    state, ctrl, plotter = viewer.state, viewer.controller, viewer.plotter
    ctrl.toggle_orthographic(False)
    session = tmp_path / "persp.json"
    ctrl.save_session(str(session))

    ctrl.toggle_orthographic(True)
    assert plotter.camera.parallel_projection
    ctrl.load_session(str(session))
    assert plotter.camera.parallel_projection is False
    assert state.orthographic_projection is False

    # And the other way round.
    ctrl.toggle_orthographic(True)
    ctrl.save_session(str(session))
    ctrl.toggle_orthographic(False)
    ctrl.load_session(str(session))
    assert plotter.camera.parallel_projection is True
    assert state.orthographic_projection is True
    ctrl.toggle_orthographic(False)


# ── #37-18: measure and edit are mutually exclusive ──────────────────


def test_measure_and_edit_modes_are_mutually_exclusive(viewer):
    """#37-18: measure on, then edit on -> both were active."""
    state, ctrl = viewer.state, viewer.controller
    state.edit_mode = False
    ctrl.toggle_measure_mode(True)
    ctrl.toggle_edit_mode()
    assert (state.measure_mode, state.edit_mode) == (False, True)
    ctrl.toggle_measure_mode(True)
    assert (state.measure_mode, state.edit_mode) == (True, False)
    ctrl.toggle_measure_mode(False)


def test_escape_leaves_measure_mode():
    """#37-18: the Esc handler knew presentation and edit mode only."""
    from tests._served import served_viewer

    src = served_viewer()
    assert "if(t.state.get('measure_mode')){e.preventDefault();" in src
    assert "t.trigger('toggle_measure_mode',[false])" in src


def test_measure_toggle_accepts_the_trigger_argument_list(viewer):
    """The Esc trigger sends ``[false]``; it must switch measuring off."""
    state, ctrl = viewer.state, viewer.controller
    ctrl.toggle_measure_mode(True)
    ctrl.toggle_measure_mode([False])
    assert state.measure_mode is False


# ── #37-22: HOMO/LUMO buttons on natural orbitals ────────────────────


def test_frontier_buttons_find_natural_orbital_markers(viewer):
    """#37-22: rows marked HONO/LUNO gave "No HOMO in this wavefunction's MO list"."""
    import vibeview.app as appmod

    state, ctrl = viewer.state, viewer.controller

    rows = [
        {"title": "α #1  -0.500 Eh  occ 1.98  HONO", "value": "restricted:0"},
        {"title": "α #2  -0.100 Eh  occ 0.02  LUNO", "value": "restricted:1"},
    ]
    assert appmod._frontier_row(rows, "HOMO")["value"] == "restricted:0"
    assert appmod._frontier_row(rows, "LUMO")["value"] == "restricted:1"
    state.wf_mo_rows = rows
    state.status_message = ""
    ctrl.render_frontier("HOMO")
    assert "No HOMO" not in (state.status_message or "")
    assert state.wf_selected_mo == "restricted:0"


def test_frontier_marker_matches_whole_tokens():
    import vibeview.app as appmod

    rows = [
        {"title": "#1  -0.9 Eh  occ 2.00  (HOMO-1 like label)", "value": "a"},
        {"title": "#2  -0.5 Eh  occ 2.00  HOMO", "value": "b"},
    ]
    assert appmod._frontier_row(rows, "HOMO")["value"] == "b"


# ── #37-21: every viewport push goes through the counted _push_view ──


@pytest.mark.parametrize(
    ("name", "args", "setup"),
    [
        ("set_material_preset", ("matte",), {}),
        ("toggle_clip", (True,), {}),
        ("toggle_show_slice", (), {}),
        ("update_clip", ("x", 0.4), {"clip_enabled": True}),
        ("toggle_background", (), {}),
        ("camera_preset", ("xy",), {}),
    ],
)
def test_viewport_pushes_are_counted(viewer, name, args, setup):
    """#37-21: these controllers called ``ctrl.view_update()`` directly,
    bypassing ``_push_view``'s push-generation counter (which the section
    dispatcher reads to know whether a push already happened)."""
    state, plotter = viewer.state, viewer.plotter
    for key, value in setup.items():
        setattr(state, key, value)
    before = _scene_hooks(plotter).push_generation
    getattr(viewer.controller, name)(*args)
    assert _scene_hooks(plotter).push_generation > before
    state.clip_enabled = False
