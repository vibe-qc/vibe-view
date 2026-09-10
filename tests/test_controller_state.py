"""Controller / state-lifecycle tests.

The app controller (app.py) was essentially untested — create_app was only
ever *built*, never driven (audit finding A6-04). These tests exercise the
state-reset contract that the file-switch / section-switch staleness fixes
depend on (A1-01/A1-02/A1-06/A1-07/A5-03) and the vibrations
equilibrium-geometry fallback (A5-01).
"""

from __future__ import annotations

import ast
import base64
import hashlib
import importlib.util
import json
import os
import tempfile
import types
import zipfile
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

from tests._qvf_corpus import qvf_corpus_dir, requires_qvf_corpus

_CORPUS = qvf_corpus_dir()
_SLAB_2D_QVF = (
    _CORPUS / "structure_slab_2d.qvf" if _CORPUS else Path("/nonexistent.qvf")
)
_REQUIRES_CORPUS = requires_qvf_corpus("structure_slab_2d.qvf")


def _periodic_dimension(script: str) -> int:
    tree = ast.parse(script)
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "PeriodicSystem"
    )
    return int(ast.literal_eval(call.args[0]))


def _assert_slab_script_round_trip(
    script: str,
    expected_lattice: np.ndarray,
    tmp_path: Path,
    *,
    expected_charge: int = 0,
    expected_multiplicity: int = 1,
    expected_method: str = "rks",
) -> None:
    """Pin the generated script's 2-D semantics and restricted-parser path."""
    from vibeview.converters import py_to_qvf
    from vibeview.input_parser import parse_input_source
    from vibeview.qvf import QVFReader

    compile(script, "<generated-slab>", "exec")
    assert _periodic_dimension(script) == 2
    assert "Molecule([" not in script
    assert "cell = np.array([\n\n])" not in script
    parsed = parse_input_source(script)
    assert parsed.charge == expected_charge
    assert parsed.multiplicity == expected_multiplicity
    assert parsed.method == expected_method

    script_path = tmp_path / "generated_slab.py"
    script_path.write_text(script)
    converted = QVFReader(py_to_qvf(script_path))
    try:
        round_trip = converted.read_structure()
        provenance = (converted.manifest.model_extra or {}).get("provenance", {})
        assert provenance.get("charge") == expected_charge
        assert provenance.get("multiplicity") == expected_multiplicity
        assert round_trip.lattice_vectors is not None
        assert round_trip.pbc == (True, True, False)
        assert round_trip.dim == 2
        np.testing.assert_allclose(
            round_trip.lattice_vectors, expected_lattice, atol=1e-6
        )
    finally:
        converted.close()

def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ── _clear_output_panels: the per-switch reset contract ──────────────────


def test_no_template_bound_state_key_starts_with_underscore():
    """Vue 3 templates cannot access identifiers starting with ``_`` or
    ``$`` (reserved for Vue internals), so any state key bound into the
    template — ``v_model=("name",)``, ``items=("name",)``, etc. — must not
    start with ``_``.

    Regression for UI-01: the multi-file Files dropdown bound
    ``v_model=("_active_file_idx",)``, which threw
    ``ReferenceError: _active_file_idx is not defined`` on every render and
    wiped out the entire app bar (Files dropdown + screenshot/export/camera
    buttons) whenever more than one file was open. Headless tests missed it
    because they never render the Vue template bindings; only live-browser
    review caught it.
    """
    import pathlib
    import re

    import vibeview.app

    src = pathlib.Path(vibeview.app.__file__).read_text()
    # Trame binds state into templates as a 1-tuple: `=("state_key", ...)`.
    # Flag any such binding whose key begins with an underscore.
    offenders = re.findall(r'=\(\s*"(_[A-Za-z][A-Za-z0-9_]*)"', src)
    assert not offenders, (
        "state keys bound in the Vue template must not start with '_' "
        f"(Vue 3 won't expose them); found: {sorted(set(offenders))}"
    )


def test_clear_output_panels_resets_interaction_state():
    """Every field that goes stale across a section/file switch must be
    reset — especially vibration_playing (A1-01), wf_mo_rows (A1-06), and
    clip_enabled (A1-07), which the original implementation left dirty."""
    from vibeview.app import _clear_output_panels

    state = types.SimpleNamespace(
        active_volume_id="vol", volume_loaded=True, bands_html="x",
        phonon_html="x", eos_html="x",
        spectra_html="x", properties_html="x", chart_html="x",
        trajectory_energy_image="x",
        trajectory_n_frames=9, trajectory_frame=4, trajectory_playing=True,
        vibration_n_modes=12, vibration_playing=True, vibration_phase=1.2,
        vibration_frequencies=[1.0], vibration_mode_items=[{"x": 1}],
        vibration_ir_intensities=[2.0], wf_section_id="wf",
        wf_mo_rows=[{"value": "restricted:0"}], clip_enabled=True,
        wf_surface_kind="elf", wf_animating=True, mo_visible=True,
        mo_last_index=3, mo_last_spin="beta", wf_density_spin=True,
        reaction_current_label="TS", reaction_waypoints=[{"x": 1}],
        atom_properties_active=True, atom_properties_section_id="ap",
    )
    _clear_output_panels(state)

    assert state.vibration_playing is False   # A1-01 — stops the anim loop
    assert state.vibration_phase == 0.0
    assert state.vibration_n_modes == 0
    assert state.vibration_mode_items == []
    assert state.trajectory_playing is False
    assert state.trajectory_frame == 0
    assert state.wf_section_id is None
    assert state.wf_mo_rows == []            # A1-06 — no stale MO picker
    assert state.wf_surface_kind is None
    assert state.wf_animating is False
    assert state.mo_visible is False
    assert state.mo_last_index is None
    assert state.mo_last_spin is None
    assert state.wf_density_spin is False
    assert state.clip_enabled is False       # A1-07
    assert state.reaction_waypoints == []
    assert state.active_volume_id is None
    assert state.phonon_html is None         # phonon panel cleared on switch
    assert state.eos_html is None            # EOS panel cleared on switch
    assert state.atom_properties_active is False


@pytest.mark.parametrize(("enabled", "expected_calls"), [(False, 0), (True, 1)])
def test_rebuild_scene_preserves_ssao_choice(
    monkeypatch, enabled: bool, expected_calls: int
) -> None:
    """A rebuild must not make the renderer disagree with the SSAO switch."""
    from vibeview.app import _rebuild_scene

    calls: list[tuple[float, int]] = []

    def record_ssao(plotter, *, radius: float, samples: int) -> None:
        calls.append((radius, samples))
        plotter.renderer.SetPass("ssao")

    monkeypatch.setattr(
        "vibeview.material_presets.ambient_occlusion_pass",
        record_ssao,
    )

    class Renderer:
        def __init__(self) -> None:
            self.render_pass = "stale"

        def SetPass(self, render_pass) -> None:
            self.render_pass = render_pass

    class Plotter:
        def __init__(self) -> None:
            self.renderer = Renderer()

        def clear(self) -> None:
            pass

        def set_background(self, _color: str) -> None:
            pass

        def view_isometric(self) -> None:
            pass

        def show_grid(self) -> None:
            pass

        def render(self) -> None:
            pass

    state = types.SimpleNamespace(
        active_volume_id=None,
        raytrace_enabled=False,
        ssao_enabled=enabled,
        status_message="",
    )
    viewer_state = types.SimpleNamespace(replication=(1, 1, 1))
    plotter = Plotter()

    _rebuild_scene(
        types.SimpleNamespace(sections=[]),
        plotter,
        viewer_state,
        state,
    )

    assert state.ssao_enabled is enabled
    assert calls == [(2.0, 16)] * expected_calls
    assert plotter.renderer.render_pass == ("ssao" if enabled else None)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("mo", ("mo", 4, "beta")),
        ("density", ("density", False)),
        ("spin_density", ("density", True)),
        ("elf", ("elf",)),
        ("nci", ("nci",)),
        ("laplacian", ("laplacian",)),
    ],
)
def test_replay_wavefunction_surface_dispatches_exact_recipe(
    monkeypatch, kind: str, expected: tuple
) -> None:
    """A rebuild must never guess which computed surface occupied the scene."""
    import vibeview.app as appmod

    calls = []

    monkeypatch.setattr(
        appmod,
        "_render_mo_volume",
        lambda _r, _p, _v, _s, _section, index, spin: calls.append(
            ("mo", index, spin)
        ),
    )
    monkeypatch.setattr(
        appmod,
        "_render_wf_density",
        lambda _r, _p, _v, state, _section: calls.append(
            ("density", state.wf_density_spin)
        ),
    )
    monkeypatch.setattr(
        appmod,
        "_render_wf_elf",
        lambda *_args: calls.append(("elf",)),
    )
    monkeypatch.setattr(
        appmod,
        "_render_wf_nci",
        lambda *_args: calls.append(("nci",)),
    )
    monkeypatch.setattr(
        appmod,
        "_render_wf_laplacian",
        lambda *_args: calls.append(("laplacian",)),
    )

    section = types.SimpleNamespace(id="wf", kind="wavefunction.gto")
    reader = types.SimpleNamespace(sections=[section])
    state = types.SimpleNamespace(
        wf_surface_kind=kind,
        wf_section_id="wf",
        mo_last_index=4,
        mo_last_spin="beta",
        wf_density_spin=None,
    )

    assert appmod._replay_wavefunction_surface(
        reader, object(), object(), state
    ) is True
    assert calls == [expected]


def test_replay_wavefunction_surface_rejects_incomplete_or_unknown_recipe() -> None:
    import vibeview.app as appmod

    section = types.SimpleNamespace(id="wf", kind="wavefunction.gto")
    reader = types.SimpleNamespace(sections=[section])
    state = types.SimpleNamespace(
        wf_surface_kind="mo",
        wf_section_id="wf",
        mo_last_index=None,
        mo_last_spin="restricted",
    )
    assert appmod._replay_wavefunction_surface(
        reader, object(), object(), state
    ) is False
    state.wf_surface_kind = "unknown"
    assert appmod._replay_wavefunction_surface(
        reader, object(), object(), state
    ) is False


def test_rebuild_scene_replays_overlays_before_appearance(monkeypatch) -> None:
    """Every replacement overlay must exist before material/toon replay."""
    import vibeview.app as appmod

    events = []

    monkeypatch.setattr(
        appmod,
        "_replay_wavefunction_surface",
        lambda *_args: events.append("wavefunction") or True,
        raising=False,
    )
    monkeypatch.setattr(
        appmod,
        "_remove_atom_index_labels",
        lambda _plotter: events.append("remove_index_labels"),
    )
    monkeypatch.setattr(
        appmod,
        "_render_atom_properties_overlay",
        lambda *_args, **_kwargs: events.append("atom_properties"),
    )
    monkeypatch.setattr(
        appmod,
        "_apply_scene_appearance",
        lambda *_args: events.append("appearance"),
    )

    class Renderer:
        def SetPass(self, _render_pass) -> None:
            pass

    class Plotter:
        renderer = Renderer()

        def __init__(self) -> None:
            self.actors = {}

        def clear(self) -> None:
            self.actors.clear()
            events.append("clear")

        def remove_actor(self, name) -> None:
            self.actors.pop(name, None)

        def set_background(self, _color) -> None:
            pass

        def show_grid(self) -> None:
            events.append("grid")

        def view_isometric(self) -> None:
            pass

        def render(self) -> None:
            pass

    state = types.SimpleNamespace(
        active_volume_id=None,
        raytrace_enabled=False,
        ssao_enabled=False,
        toon_mode=False,
        status_message="",
        wf_surface_kind="elf",
        wf_section_id="wf",
        mo_visible=True,
        atom_properties_active=True,
        atom_properties_section_id="props",
    )
    viewer_state = types.SimpleNamespace(replication=(1, 1, 1))

    appmod._rebuild_scene(
        types.SimpleNamespace(sections=[]), Plotter(), viewer_state, state
    )

    assert events == [
        "clear",
        "wavefunction",
        "remove_index_labels",
        "atom_properties",
        "grid",
        "appearance",
    ]

    def fail_replay(_reader, current_plotter, _viewer_state, _state):
        current_plotter.actors["mo_iso_partial"] = object()
        raise RuntimeError("grid evaluation failed")

    monkeypatch.setattr(appmod, "_replay_wavefunction_surface", fail_replay)
    events.clear()
    state.atom_properties_active = False
    state.mo_visible = True
    failing_plotter = Plotter()
    appmod._rebuild_scene(
        types.SimpleNamespace(sections=[]), failing_plotter, viewer_state, state
    )
    assert state.mo_visible is False
    assert state.status_message == (
        "Computed surface rebuild error: grid evaluation failed"
    )
    assert not any(
        name.startswith("mo_iso_") for name in failing_plotter.actors
    )


def test_rebuild_scene_preserves_structure_error_after_successful_replay(
    monkeypatch,
) -> None:
    """A later computed-surface success must not mask a partial rebuild."""
    import vibeview.app as appmod
    import vibeview.renderers.structure as structure_mod

    class FailingStructureRenderer:
        def __init__(self, *_args) -> None:
            pass

        def add_to_plotter(self, *_args, **_kwargs) -> None:
            raise RuntimeError("structure unavailable")

    monkeypatch.setattr(
        structure_mod, "StructureRenderer", FailingStructureRenderer
    )

    def replay_success(_reader, _plotter, _viewer_state, state) -> bool:
        state.status_message = "Computed surface rendered"
        return True

    monkeypatch.setattr(appmod, "_replay_wavefunction_surface", replay_success)
    monkeypatch.setattr(appmod, "_apply_scene_appearance", lambda *_args: None)

    class Renderer:
        def SetPass(self, _render_pass) -> None:
            pass

    class Plotter:
        renderer = Renderer()

        def clear(self) -> None:
            pass

        def set_background(self, _color) -> None:
            pass

        def show_grid(self) -> None:
            pass

        def view_isometric(self) -> None:
            pass

        def render(self) -> None:
            pass

    state = types.SimpleNamespace(
        active_volume_id=None,
        raytrace_enabled=False,
        ssao_enabled=False,
        toon_mode=False,
        status_message="",
        show_atom_labels=False,
        wf_surface_kind="elf",
        wf_section_id="wf",
        mo_visible=True,
        atom_properties_active=False,
        atom_properties_section_id=None,
    )
    reader = types.SimpleNamespace(
        sections=[types.SimpleNamespace(id="structure", kind="structure")]
    )

    error = appmod._rebuild_scene(
        reader,
        Plotter(),
        types.SimpleNamespace(replication=(1, 1, 1)),
        state,
    )

    assert error == "Structure rebuild error: structure unavailable"
    assert state.status_message == error


def test_rebuild_scene_keeps_hidden_computed_surface_hidden(monkeypatch) -> None:
    import vibeview.app as appmod

    calls = []
    monkeypatch.setattr(
        appmod,
        "_replay_wavefunction_surface",
        lambda *_args: calls.append("wavefunction") or True,
        raising=False,
    )
    monkeypatch.setattr(appmod, "_apply_scene_appearance", lambda *_args: None)

    class Renderer:
        def SetPass(self, _render_pass) -> None:
            pass

    class Plotter:
        renderer = Renderer()

        def clear(self) -> None:
            pass

        def set_background(self, _color) -> None:
            pass

        def show_grid(self) -> None:
            pass

        def view_isometric(self) -> None:
            pass

        def render(self) -> None:
            pass

    state = types.SimpleNamespace(
        active_volume_id=None,
        raytrace_enabled=False,
        ssao_enabled=False,
        toon_mode=False,
        status_message="",
        wf_surface_kind="nci",
        wf_section_id="wf",
        mo_visible=False,
        atom_properties_active=False,
        atom_properties_section_id=None,
    )
    appmod._rebuild_scene(
        types.SimpleNamespace(sections=[]),
        Plotter(),
        types.SimpleNamespace(replication=(1, 1, 1)),
        state,
    )

    assert calls == []

    monkeypatch.setattr(
        appmod,
        "_rebuild_volume",
        lambda *_args: calls.append("stored_volume"),
    )
    state.active_volume_id = "stored"
    state.mo_visible = True
    appmod._rebuild_scene(
        types.SimpleNamespace(sections=[]),
        Plotter(),
        types.SimpleNamespace(replication=(1, 1, 1)),
        state,
    )
    assert calls == ["stored_volume"]
    assert state.mo_visible is False
    assert state.wf_surface_kind is None
    assert state.wf_section_id is None
    assert state.wf_animating is False
    assert state.mo_last_index is None
    assert state.mo_last_spin is None
    assert state.wf_density_spin is False


# ── vibrations equilibrium-geometry fallback (A5-01, viewer side) ─────────


def _vibrations_qvf_zero_positions() -> Path:
    """structure (real coords) + vibrations (all-zero coords, like the old
    writer). The renderer must recover geometry from the structure section."""
    structure = json.dumps({
        "atoms": [
            {"symbol": "O", "position": [0.0, 0.0, 0.062], "atomic_number": 8},
            {"symbol": "H", "position": [0.0, 0.757, -0.49], "atomic_number": 1},
            {"symbol": "H", "position": [0.0, -0.757, -0.49], "atomic_number": 1},
        ],
        "pbc": [False, False, False],
    }).encode()
    meta = json.dumps({
        "frequencies": [1600.0, 3700.0, 3800.0],
        "atoms": [
            {"symbol": "O", "position": [0.0, 0.0, 0.0], "atomic_number": 0},
            {"symbol": "H", "position": [0.0, 0.0, 0.0], "atomic_number": 0},
            {"symbol": "H", "position": [0.0, 0.0, 0.0], "atomic_number": 0},
        ],
    }).encode()
    disp = np.zeros((3, 3, 3), dtype=np.float64)
    disp[0, 0, 2] = 1.0  # mode 0 moves O in z
    disp_b = disp.tobytes()
    sections = [
        {"id": "structure", "kind": "structure",
         "members": {"structure": {"path": "s.json", "format": "json", "sha256": _sha(structure)}}},
        {"id": "vib0", "kind": "vibrations", "members": {
            "metadata": {"path": "v/m.json", "format": "json", "sha256": _sha(meta)},
            "displacements": {"path": "v/d.bin", "format": "binary", "dtype": "float64",
                              "shape": [3, 3, 3], "sha256": _sha(disp_b)},
        }},
    ]
    manifest = {"qvf_version": 1, "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
                "sections": sections}
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for p, d in {"s.json": structure, "v/m.json": meta, "v/d.bin": disp_b}.items():
            zf.writestr(p, d)
    return Path(tmp.name)


def test_vibrations_recovers_equilibrium_from_structure():
    from vibeview.qvf import QVFReader
    from vibeview.renderers.vibrations import VibrationsRenderer

    path = _vibrations_qvf_zero_positions()
    try:
        reader = QVFReader(path)
        r = VibrationsRenderer(reader.get_section("vib0"), reader)
        # At zero amplitude the displaced atoms ARE the equilibrium geometry;
        # it must come from the structure section, not the origin.
        atoms = r.displace_atoms(0, amplitude=0.0)
        positions = np.array([p for _, p in atoms])
        assert not np.allclose(positions, 0.0)
        assert positions[1][1] == pytest.approx(0.757, abs=1e-6)   # H y-coord
        # atomic numbers should also be recovered from the structure.
        assert r.load().atoms[0].atomic_number == 8
    finally:
        path.unlink()


# ── vq Job Manager (M1/A1) ───────────────────────────────────────────────


def test_vq_refresh_jobs_shapes_rows_and_survives_errors(water_qvf, monkeypatch):
    """vq_refresh_jobs must build rich rows from JobSpecs and never raise —
    the shipped version crashed on a nonexistent `created_at` field and
    listed with host 'local' (rejected by vq.host.is_local_host)."""
    import sys
    import types

    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    class _State:
        def __init__(self, value):
            self.value = value

    class _FakeSpec:
        def __init__(self, jid, state, name, started, finished):
            self.id = jid
            self.state = _State(state)
            self.job_name = name
            self.started_at = started
            self.finished_at = finished
            self.submitted_at = "2026-07-02T10:00:00+00:00"
            self.tags = ["pr-request"]

    fake_specs = [
        _FakeSpec("aaaaaaaaaaaa0000", "completed", "opt-water",
                  "2026-07-02T10:00:00+00:00", "2026-07-02T10:02:30+00:00"),
        _FakeSpec("bbbbbbbbbbbb1111", "running", "scf-benzene",
                  "2026-07-02T10:05:00+00:00", None),
        _FakeSpec("cccccccccccc2222", "failed", "bad-basis",
                  "2026-07-02T09:00:00+00:00", "2026-07-02T09:00:10+00:00"),
    ]

    captured = {}

    def _fake_list_jobs(host, *a, **k):
        captured["host"] = host
        return list(fake_specs)

    fake_mod = types.ModuleType("vq.listing")
    fake_mod.list_jobs = _fake_list_jobs
    monkeypatch.setitem(sys.modules, "vq.listing", fake_mod)

    reader = QVFReader(water_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.state.vq_monitor_active = True
        server.controller.vq_refresh_jobs()

        # host must be a recognized local alias, not "local"
        assert captured["host"] == "localhost"
        rows = server.state.vq_jobs_list
        assert server.state.vq_jobs_error == ""
        assert len(rows) == 3
        by_state = {r["state"]: r for r in rows}
        assert by_state["completed"]["color"] == "success"
        assert by_state["completed"]["openable"] is True
        assert by_state["running"]["color"] == "info"
        assert by_state["running"]["openable"] is False
        assert by_state["failed"]["color"] == "error"
        # elapsed computed for a terminal job (2m 30s)
        assert by_state["completed"]["elapsed"] == "2m 30s"

        # completed-only view drops the running/failed rows
        server.state.vq_monitor_active = False
        server.controller.vq_refresh_jobs()
        assert [r["state"] for r in server.state.vq_jobs_list] == ["completed"]

        # queue errors surface as a string, never an exception
        def _boom(host, *a, **k):
            raise RuntimeError("daemon down")

        fake_mod.list_jobs = _boom
        server.controller.vq_refresh_jobs()
        assert "daemon down" in server.state.vq_jobs_error
        assert server.state.vq_jobs_list == []
    finally:
        reader.close()


def test_vq_show_job_status_detail_and_toggle(water_qvf, monkeypatch):
    """A3: vq_show_job_status pulls state + log tail via show_status_json into
    the detail pane, clicking the same job again collapses it, and status
    errors surface as text rather than raising."""
    import json as _json
    import sys
    import types

    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    def _fake_status(host, jobid, *, tail=50, **k):
        assert host == "localhost"
        return _json.dumps(
            {"state": "running", "stdout": "SCF iter 7  E=-76.01\n", "stderr": ""}
        )

    fake_mod = types.ModuleType("vq.status")
    fake_mod.show_status_json = _fake_status
    monkeypatch.setitem(sys.modules, "vq.status", fake_mod)

    reader = QVFReader(water_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")

        server.controller.vq_show_job_status("abc123def456", "opt-water")
        assert server.state.vq_detail_job_id == "abc123def456"
        assert server.state.vq_detail_name == "opt-water"
        assert server.state.vq_detail_state == "running"
        assert "SCF iter 7" in server.state.vq_detail_log

        # clicking the same job again collapses the detail
        server.controller.vq_show_job_status("abc123def456", "opt-water")
        assert server.state.vq_detail_job_id == ""

        # a status error is shown, not raised
        def _boom(host, jobid, **k):
            raise RuntimeError("no daemon")

        fake_mod.show_status_json = _boom
        server.controller.vq_show_job_status("zzz", "zzz")
        assert "no daemon" in server.state.vq_detail_log
    finally:
        reader.close()


def test_vq_overview_strip_populated_on_refresh(water_qvf, monkeypatch):
    """A5: refreshing the Job Manager also fills the queue-overview strip
    (host/version, daemon health, capacity, load) via vq.overview; a broken
    overview clears the fields rather than raising."""
    import sys
    import types

    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    # Fake the *parent* package too: the app does `from vq import config`,
    # which imports `vq` itself — pre-seeding only the submodules leaves the
    # test green solely where a real vq happens to be pip-installed.
    vq_pkg = types.ModuleType("vq")
    cfg_mod = types.ModuleType("vq.config")
    cfg_mod.load = lambda: object()
    vq_pkg.config = cfg_mod
    monkeypatch.setitem(sys.modules, "vq", vq_pkg)
    monkeypatch.setitem(sys.modules, "vq.config", cfg_mod)
    ov_mod = types.ModuleType("vq.overview")
    ov_mod.gather_overview_local = lambda host, cfg: {"host": host}
    ov_mod.format_overview_json = lambda ov: {
        "host": "localhost",
        "vq_version": "0.15.9",
        "daemon_health": {"ok": True},
        "max_cpus": 18,
        "max_jobs": 4,
        "running_cpus": 5,
        "pending_cpus": 2,
    }
    monkeypatch.setitem(sys.modules, "vq.overview", ov_mod)
    listing_mod = types.ModuleType("vq.listing")
    listing_mod.list_jobs = lambda h, *a, **k: []
    monkeypatch.setitem(sys.modules, "vq.listing", listing_mod)

    reader = QVFReader(water_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.vq_refresh_jobs()
        assert server.state.vq_ov_host == "localhost · vq 0.15.9"
        assert server.state.vq_ov_daemon is True
        assert server.state.vq_ov_capacity == "max 18 CPU · 4 jobs"
        assert server.state.vq_ov_load == "running 5 · pending 2 CPU"

        # a broken overview clears the strip, doesn't raise
        def _boom(*a, **k):
            raise RuntimeError("no daemon")

        ov_mod.gather_overview_local = _boom
        server.controller.vq_refresh_jobs()
        assert server.state.vq_ov_host == ""
    finally:
        reader.close()


@_REQUIRES_CORPUS


def test_structure_calculation_params_rejects_non_prefix_pbc() -> None:
    """The current exporter must not silently reorder arbitrary PBC axes."""
    from dataclasses import replace

    from vibeview.input_generator import _structure_calculation_params
    from vibeview.qvf import QVFReader

    reader = QVFReader(_SLAB_2D_QVF)
    try:
        non_prefix = replace(reader.read_structure(), pbc=(True, False, True), dim=2)
        with pytest.raises(ValueError, match=r"(?i)(?=.*pbc)(?=.*(?:prefix|leading))"):
            _structure_calculation_params(non_prefix)
    finally:
        reader.close()


@_REQUIRES_CORPUS


def test_export_py_preserves_conformance_slab_state_and_cell(
    tmp_path: Path,
) -> None:
    """A charged 2-D QVF export must keep its cell, dimension, and spin state."""
    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(_SLAB_2D_QVF)
    try:
        expected_lattice = reader.read_structure().lattice_vectors
        assert expected_lattice is not None
        create_app(reader)
        server = get_server(client_type="vue3")
        # The structure, not a stale/default panel choice, determines that the
        # generated calculation is periodic.
        server.state.calc_template = "single_point"
        server.state.calc_method = "uks"
        server.state.calc_functional = "pbe"
        server.state.calc_charge = -1
        server.state.calc_multiplicity = 2
        server.state.export_data = ""

        server.controller.export_py()

        data = server.state.export_data or ""
        assert data.startswith("data:text/plain;base64,"), server.state.status_message
        script = base64.b64decode(data.split(",", 1)[1]).decode()
        _assert_slab_script_round_trip(
            script,
            expected_lattice,
            tmp_path,
            expected_charge=-1,
            expected_multiplicity=2,
            expected_method="uks",
        )
    finally:
        reader.close()


def test_vq_submit_job_uses_queue_with_name_and_tag(water_qvf, monkeypatch):
    """A2: vq_submit_job generates the input and submits it through vq with a
    derived job name + 'vibe-view' tag, then opens the Job Manager to track
    it. Falls back / errors cleanly when neither vq nor vibe-qc is present."""
    import sys
    import types

    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    seen = {}

    def _fake_submit(host, *, input_file, python, job_name, tags, **k):
        # the generated input must be a real, unit-correct vibe-qc script
        src = open(input_file).read()
        seen.update(host=host, job_name=job_name, tags=tags, script=src)
        return "abcdef012345"

    submit_mod = types.ModuleType("vq.submit")
    submit_mod.submit_local = _fake_submit
    monkeypatch.setitem(sys.modules, "vq.submit", submit_mod)
    listing_mod = types.ModuleType("vq.listing")
    listing_mod.list_jobs = lambda h, *a, **k: []
    monkeypatch.setitem(sys.modules, "vq.listing", listing_mod)

    reader = QVFReader(water_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.state.calc_method = "rhf"
        server.state.calc_basis = "def2-tzvp"
        server.state.calc_template = "single_point"
        # This test pins the *generated-script* contract: an interpreter is
        # passed and the payload is a runnable .py. Container mode is on by
        # default and satisfies neither (a .qvf resolves its own runtime),
        # so opt out explicitly rather than depending on whether vibeqc
        # happens to be importable in the test environment.
        server.state.vq_submit_container = False

        server.controller.vq_submit_job()

        assert seen["host"] == "localhost"
        assert seen["tags"] == ["vibe-view"]
        assert seen["job_name"].startswith("vibeview-")
        assert len(seen["job_name"]) <= 50
        # generated script is a runnable vibe-qc input (unit-correct: bohr)
        assert "run_job" in seen["script"] and "bohr" in seen["script"]
        assert "mol = Molecule([" in seen["script"]
        assert "PeriodicSystem(" not in seen["script"]
        from vibeview.input_parser import parse_input_source

        parsed = parse_input_source(seen["script"])
        assert parsed.charge == 0
        assert parsed.multiplicity == 1
        # tracked in the Job Manager
        assert server.state.vq_panel_open is True
        assert server.state.vq_monitor_active is True
        assert "abcdef012345"[:12] in server.state.status_message
    finally:
        reader.close()


@_REQUIRES_CORPUS


def test_vq_submit_job_script_preserves_conformance_slab(monkeypatch, tmp_path: Path) -> None:
    """The non-container queue fallback must share periodic export semantics."""
    import sys
    import types

    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    seen = {}

    def _fake_submit(host, *, input_file, python, job_name, tags, **kwargs):
        seen.update(
            host=host,
            python=python,
            job_name=job_name,
            tags=tags,
            extra=kwargs,
            script=Path(input_file).read_text(),
        )
        return "2dfeedface01"

    submit_mod = types.ModuleType("vq.submit")
    submit_mod.submit_local = _fake_submit
    monkeypatch.setitem(sys.modules, "vq.submit", submit_mod)
    listing_mod = types.ModuleType("vq.listing")
    listing_mod.list_jobs = lambda host, *args, **kwargs: []
    monkeypatch.setitem(sys.modules, "vq.listing", listing_mod)

    reader = QVFReader(_SLAB_2D_QVF)
    try:
        expected_lattice = reader.read_structure().lattice_vectors
        assert expected_lattice is not None
        create_app(reader)
        server = get_server(client_type="vue3")
        server.state.calc_template = "single_point"
        server.state.calc_method = "uks"
        server.state.calc_functional = "pbe"
        server.state.calc_charge = -1
        server.state.calc_multiplicity = 2
        server.state.vq_submit_container = False
        server.state.vq_submit_live_checkpoint = False

        server.controller.vq_submit_job()

        assert seen, server.state.status_message
        assert seen["host"] == "localhost"
        assert seen["tags"] == ["vibe-view"]
        _assert_slab_script_round_trip(
            seen["script"],
            expected_lattice,
            tmp_path,
            expected_charge=-1,
            expected_multiplicity=2,
            expected_method="uks",
        )
    finally:
        reader.close()


@pytest.mark.skipif(
    importlib.util.find_spec("vibeqc") is None,
    reason="vibeqc (the QVF producer) not installed",
)
def test_vq_submit_job_container_mode_sends_a_pending_qvf(water_qvf, monkeypatch):
    """Container mode submits one pending .qvf and no interpreter.

    A .qvf payload resolves its own runtime from the program pin, so passing
    ``python=`` is wrong for it -- and the stub here takes ``**kwargs``
    deliberately so that a regression shows up as a failed assertion rather
    than a TypeError swallowed by the submit path's broad except.
    """
    import json
    import sys
    import types
    import zipfile

    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    seen = {}

    def _fake_submit(host, *, input_file, job_name, tags, **kwargs):
        with zipfile.ZipFile(input_file) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        seen.update(
            host=host,
            job_name=job_name,
            tags=tags,
            payload=str(input_file),
            kinds=sorted({sec["kind"] for sec in manifest["sections"]}),
            run_status=manifest.get("provenance", {}).get("run_status"),
            extra=sorted(kwargs),
        )
        return "fedcba543210"

    submit_mod = types.ModuleType("vq.submit")
    submit_mod.submit_local = _fake_submit
    monkeypatch.setitem(sys.modules, "vq.submit", submit_mod)
    listing_mod = types.ModuleType("vq.listing")
    listing_mod.list_jobs = lambda h, *a, **k: []
    monkeypatch.setitem(sys.modules, "vq.listing", listing_mod)

    reader = QVFReader(water_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.state.calc_method = "rhf"
        server.state.calc_basis = "sto-3g"
        server.state.calc_template = "single_point"
        server.state.vq_submit_container = True

        server.controller.vq_submit_job()

        assert seen, f"submit never happened: {server.state.status_message}"
        assert seen["payload"].endswith(".qvf"), seen["payload"]
        # A pending container is a request: structure + spec, no results.
        assert seen["kinds"] == ["job.spec", "structure"], seen["kinds"]
        assert seen["run_status"] == "pending"
        assert "qvf-container" in seen["tags"]
        # No interpreter may be passed for a container payload.
        assert "python" not in seen["extra"], seen["extra"]
        assert "container" in server.state.status_message
    finally:
        reader.close()


# ── controller smoke on the committed multi-section showcase ─────────────


def test_keyboard_shortcut_triggers_registered(showcase_qvf):
    """The keyboard-shortcut JS calls trame.trigger('<name>') for these
    actions; @ctrl.set alone does not register a client trigger, so all
    shortcuts (e/m/r/s/p, Ctrl+Z/Y, Delete) were dead until the 2026-07-02
    audit. Pin the registrations."""
    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(showcase_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        registered = set(server.controller._triggers)
        needed = {
            "toggle_edit_mode",
            "toggle_measure_mode",
            "camera_preset",
            "save_screenshot",
            "toggle_presentation",
            "presentation_prev",
            "presentation_next",
            "edit_undo",
            "edit_redo",
            "edit_delete_selected",
        }
        missing = needed - registered
        assert not missing, f"shortcut triggers not registered: {sorted(missing)}"
    finally:
        reader.close()


def test_activate_sections_smoke_and_mo_picker_staleness(showcase_qvf):
    """Drive activate_section across a real multi-section file and assert no
    handler crashes, and that the MO picker rows clear when leaving the
    wavefunction section (no stale orbitals — A1-06/A5-03 restore path)."""
    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(showcase_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        activate = server.controller.activate_section

        kinds = {s.id: s.kind for s in reader.sections}
        wf_id = next((sid for sid, k in kinds.items() if k == "wavefunction.gto"), None)
        struct_id = next((sid for sid, k in kinds.items() if k == "structure"), None)

        # Every supported section should activate without raising.
        from vibeview.kinds import SUPPORTED_KINDS
        for sid, kind in kinds.items():
            if kind in SUPPORTED_KINDS:
                activate(sid)  # must not raise

        if wf_id and struct_id:
            activate(wf_id)
            assert len(server.state.wf_mo_rows) > 0           # picker populated
            activate(struct_id)
            assert server.state.wf_mo_rows == []              # cleared on leave
            assert server.state.structure_hidden is False     # structure restored
    finally:
        reader.close()


def test_every_panel_kind_has_a_click_dispatch_branch():
    """A panel kind with no branch in ``_activate_section_impl`` is dead:
    clicking it in the sidebar renders nothing and says nothing.

    ``dos.coop``, ``dos.cohp`` and ``bond_orders`` were in that state —
    they had activators, and the viewer_defaults auto-open path called
    them, but the click dispatch never did. The smoke test above only
    asserts that activation doesn't *raise*, which a missing branch
    trivially satisfies.
    """
    import inspect
    import re

    import vibeview.app as appmod

    src = inspect.getsource(appmod.create_app)
    # Only the section of create_app that dispatches a user click.
    start = src.index("def _activate_section_impl")
    impl = src[start : src.index("@ctrl.set(\"activate_section\")", start)]

    # Spectra kinds are matched via a set membership, not a literal, and
    # volume.* via startswith — both are covered by their own branches.
    literal_kinds = appmod._two_d_panel_kinds() - appmod._plot_spectra_kinds()
    missing = [k for k in sorted(literal_kinds) if not re.search(rf'"{k}"', impl)]
    assert not missing, (
        "panel kinds with no branch in the click dispatch (clicking them "
        f"does nothing): {missing}"
    )


def test_two_d_panel_switch_pushes_cleaned_scene(showcase_qvf, monkeypatch):
    """UI-OBS-F: leaving atom_properties for a 2D side panel must PUSH the
    cleaned 3D scene to the client.

    ``activate_section`` removes the charge-label overlay server-side on
    every switch, but the 2D-panel branches (citations / scf_history /
    symmetry / bands / DOS / spectra / NMR) don't render the 3D viewport in
    their activate helper. With VtkLocalView the client renders its own
    local geometry copy, so without an explicit push the atom_properties
    charge billboards lingered until a 3D section forced a re-sync. Assert
    the switch now triggers a viewport push.
    """
    from trame.app import get_server

    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(showcase_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        activate = server.controller.activate_section

        kinds = {s.id: s.kind for s in reader.sections}
        ap_id = next((sid for sid, k in kinds.items() if k == "atom_properties"), None)
        two_d = appmod._two_d_panel_kinds()
        panel_id = next((sid for sid, k in kinds.items() if k in two_d), None)
        # showcase_qvf is synthesized by this suite, so a missing section is a
        # broken fixture, not an environment we should tiptoe around. Skipping
        # here would retire the regression silently.
        assert ap_id is not None and panel_id is not None, (
            "showcase_qvf must carry an atom_properties section and a 2D-panel "
            f"section for this regression; it has kinds {sorted(kinds.values())}"
        )

        activate(ap_id)  # draw the charge-label overlay

        pushes: list = []
        monkeypatch.setattr(appmod, "_push_view", lambda p: pushes.append(p))
        activate(panel_id)  # 2D panel — must sync the cleaned scene
        assert pushes, (
            "switching atom_properties → a 2D panel must push the cleaned "
            "3D scene to the client (UI-OBS-F); the charge billboards "
            "otherwise linger client-side"
        )
    finally:
        reader.close()


def test_toggle_clip_accepts_switch_event_arg(showcase_qvf):
    """UI-OBS-G: the clip Enable switch wires
    ``update_modelValue=(ctrl.toggle_clip, "[$event]")``, so the handler is
    called with the switch's new boolean.

    A zero-arg ``toggle_clip()`` raised ``TypeError`` server-side on every
    toggle (``clip_enabled`` still flipped via ``v_model``, so the switch
    *looked* toggled but the clip never applied because the handler died before
    ``_rebuild_clip``). The handler must accept the arg and *set* (not flip)
    from it; a no-arg call still flips for an icon/keyboard trigger.
    """
    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(showcase_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        # Isolate from the client view-push layer.
        server.controller.view_update = lambda *a, **k: None
        toggle_clip = server.controller.toggle_clip

        server.state.clip_enabled = False
        toggle_clip(True)  # switch → ON: must not raise, must SET True
        assert server.state.clip_enabled is True
        toggle_clip(False)  # switch → OFF: SET False (not flip)
        assert server.state.clip_enabled is False
        toggle_clip()  # no-arg → flip
        assert server.state.clip_enabled is True
    finally:
        reader.close()


def test_update_clip_accepts_slider_event_arg(showcase_qvf, monkeypatch):
    """Each clip slider sends its axis and value to one shared callback."""
    from trame.app import get_server

    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(showcase_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        rebuilt_positions = []
        view_updates = []

        def record_rebuild(_reader, _plotter, _viewer_state, state):
            rebuilt_positions.append((state.clip_x, state.clip_y, state.clip_z))

        monkeypatch.setattr(appmod, "_rebuild_clip", record_rebuild)
        server.controller.view_update = lambda *a, **k: view_updates.append(True)
        server.state.clip_enabled = True
        server.state.clip_x = 0.5
        server.state.status_message = "2D slice enabled"

        server.controller.update_clip("x", 0.55)

        assert rebuilt_positions == [(0.55, 0.5, 0.5)]
        assert view_updates == [True]
        assert server.state.status_message == "2D slice enabled"
    finally:
        reader.close()


def test_reload_active_file_rebuilds_scene(showcase_qvf, monkeypatch):
    """``_reload_active_file`` must rebuild the 3D scene after clearing it.

    Regression for the aec246e5 indentation slip: an SSAO try/except was
    inserted mid-function at the enclosing indent level, which *ended*
    ``_reload_active_file`` right after ``plotter.clear()`` and stranded the
    entire rebuild half (``_build_structure_scene``, QA validation, camera
    apply, crossfade re-bootstrap) inside the except handler as dead code.
    Every caller — file switch, open-file, from-vq open, the file watcher's
    auto-reload — then blanked the viewport and never redrew it.
    """
    from trame.app import get_server

    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(showcase_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None

        builds: list = []
        real_build = appmod._build_structure_scene

        def _spy(*a, **k):
            builds.append(a)
            return real_build(*a, **k)

        monkeypatch.setattr(appmod, "_build_structure_scene", _spy)
        server.controller.switch_file(0)  # drives _reload_active_file
        assert builds, (
            "_reload_active_file cleared the plotter but never called "
            "_build_structure_scene — the scene-rebuild half of the function "
            "is unreachable (aec246e5 regression)"
        )
        # The rebuild half also re-derives the periodic flag; NaCl showcase
        # is periodic, so it must survive a reload.
        assert server.state.is_periodic is True
    finally:
        reader.close()


def test_file_switch_preserves_material_and_toon_appearance(water_qvf, monkeypatch):
    """A newly selected file must realize the appearance flags it inherits."""
    from trame.app import get_server

    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.material_presets import get_preset
    from vibeview.qvf import QVFReader

    second_path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    monkeypatch.setattr(appmod.pv, "Plotter", create_plotter)
    readers = [QVFReader(water_qvf), QVFReader(second_path)]
    try:
        create_app(readers)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *_args, **_kwargs: None
        plotter = created[0]

        server.controller.set_material_preset("matte")
        server.controller.toggle_toon(True)
        old_actor = next(
            actor
            for name, actor in plotter.actors.items()
            if str(name).startswith("atom_") and "labels" not in str(name)
        )
        server.controller.switch_file(1)

        preset = get_preset("matte")
        new_actor = next(
            actor
            for name, actor in plotter.actors.items()
            if str(name).startswith("atom_") and "labels" not in str(name)
        )
        prop = new_actor.GetProperty()
        assert new_actor is not old_actor
        assert server.state.material_preset == "matte"
        assert server.state.toon_mode is True
        assert plotter.renderer.GetBackground() == pytest.approx(
            preset.background_color, abs=1 / 255
        )
        assert prop.GetSpecular() == pytest.approx(0.1)
        assert prop.GetSpecularPower() == pytest.approx(10.0)
        assert prop.GetInterpolation() == 0
        assert prop.GetEdgeVisibility() == 1
    finally:
        if created:
            created[0].close()
        for reader in readers:
            reader.close()


def test_file_switch_rebinds_lazy_reader_to_the_active_qvf(water_qvf, monkeypatch):
    """Symmetry and the lazy archive must both follow the selected file."""

    from trame.app import get_server

    import vibeview.symmetry as symmetry
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    second_path = Path(__file__).parent / "data" / "h2_natural_orbitals.qvf"
    seen = []
    detect_from_qvf = symmetry.detect_from_qvf

    def _capture(active_reader):
        seen.append(active_reader)
        return detect_from_qvf(active_reader)

    monkeypatch.setattr(symmetry, "detect_from_qvf", _capture)

    readers = [QVFReader(water_qvf), QVFReader(second_path)]
    try:
        create_app(readers)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None

        server.controller.detect_symmetry()
        first_lazy = seen[-1]
        assert server.state.point_group == "C2v"
        assert Path(first_lazy.path).resolve() == water_qvf.resolve()

        server.controller.switch_file(1)
        server.controller.detect_symmetry()
        second_lazy = seen[-1]

        assert server.state.point_group == "D∞h"
        assert second_lazy is not first_lazy
        assert Path(second_lazy.path).resolve() == second_path.resolve()
        assert first_lazy._raw_reader._zf.fp is None
    finally:
        for active_reader in readers:
            active_reader.close()


def test_symmetry_detection_uses_the_active_edit_overlay(water_qvf):
    """Point-group detection must analyze the geometry shown in the editor."""
    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader
    from vibeview.symmetry import detect_from_qvf

    reader = QVFReader(water_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None
        server.controller.detect_symmetry()
        assert server.state.point_group == "C2v"

        structure = reader.read_structure()
        positions = [atom.position.copy() for atom in structure.atoms]
        positions[0] += np.array([0.13, 0.07, 0.21])
        reader.set_edit_overlay(positions, [atom.symbol for atom in structure.atoms])
        assert detect_from_qvf(reader).symbol == "C1"

        server.controller.detect_symmetry()
        assert server.state.point_group == "C1"
    finally:
        reader.close()


# ── Watcher hot-reload integration (M2: content-hash + per-section diff) ──


def _rewrite_qvf(path, mutate_manifest_and_members):
    """Rewrite a QVF zip in place through ``mutate(manifest, members)`` —
    the test-side stand-in for a producer updating its checkpoint QVF."""
    with zipfile.ZipFile(path) as zin:
        manifest = json.loads(zin.read("manifest.json"))
        members = {i.filename: zin.read(i.filename) for i in zin.infolist()}
    mutate_manifest_and_members(manifest, members)
    members["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, "w") as zout:
        for name, blob in members.items():
            zout.writestr(name, blob)


def _append_scf_iteration(path, energy_eh):
    """Append one SCF iteration, keeping the member sha256 consistent —
    exactly what the producer's checkpoint writer does as SCF converges."""

    def _mutate(manifest, members):
        sec = next(s for s in manifest["sections"] if s["kind"] == "scf_history")
        mpath = sec["members"]["iterations"]["path"]
        data = json.loads(members[mpath])
        data["iterations"].append(
            {"iter": len(data["iterations"]) + 1, "energy_eh": energy_eh, "delta_e": -1e-3}
        )
        blob = json.dumps(data).encode()
        members[mpath] = blob
        sec["members"]["iterations"]["sha256"] = _sha(blob)

    _rewrite_qvf(path, _mutate)


def test_watcher_panel_only_hot_reload(monkeypatch, tmp_path, showcase_qvf):
    """A checkpoint that only grows scf_history must hot-reload the active
    panel from a *fresh* reader without rebuilding the 3D scene (the old
    reader holds an open zip handle on the replaced archive, so reusing it
    renders stale data — the pre-M2 auto-reload bug). Viewer-side geometry
    and its undo transaction remain one coherent logical document."""
    import shutil

    from trame.app import get_server

    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.file_watcher import QVFChangeTracker
    from vibeview.qvf import QVFReader

    live = tmp_path / "job.qvf"
    # Was examples/vibe_view/output-nacl-showcase.qvf from the co-located
    # vibe-qc tree. That archive is gitignored even in vibe-qc, so the guard
    # here skipped in CI and, after the 2026-09 split, everywhere -- while
    # asserting on a real hot-reload bug. showcase_qvf synthesizes the same
    # shape in-process: periodic NaCl, a volume kind, and an scf_history the
    # checkpoint writer can grow.
    shutil.copy(showcase_qvf, live)

    reader = QVFReader(live)
    try:
        scf_id = next(s.id for s in reader.sections if s.kind == "scf_history")
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None
        server.controller.activate_section(scf_id)
        baseline_html = server.state.chart_html
        assert baseline_html

        original = reader.read_structure()
        original_lattice = np.asarray(original.lattice_vectors, dtype=float)
        expected_lattice = original_lattice.copy()
        expected_lattice[0] *= 2
        server.state.edit_history = []
        server.state.edit_future = []
        server.state.build_supercell_nx = 2
        server.state.build_supercell_ny = 1
        server.state.build_supercell_nz = 1
        server.controller.build_supercell()
        built = reader.read_structure()
        assert len(built.atoms) == 2 * len(original.atoms)
        np.testing.assert_allclose(built.lattice_vectors, expected_lattice)
        history_before = json.loads(json.dumps(server.state.edit_history))
        server.state.edit_selected = [0]
        server.state.frozen_atoms = [0]

        tracker = QVFChangeTracker(live, settle_delay=0.0)
        _append_scf_iteration(live, -99.9)
        tracker.poll()  # movement detected
        event = tracker.poll()  # settled → fingerprint + diff
        assert event is not None
        assert event.changed_sections == (scf_id,)
        assert not event.added_sections and not event.removed_sections

        builds = []
        monkeypatch.setattr(
            appmod, "_build_structure_scene", lambda *a, **k: builds.append(a)
        )
        server.controller.apply_watcher_event(event)

        assert not builds, "panel-only change must not rebuild the 3D scene"
        assert server.state.selected_section == scf_id  # selection kept
        assert server.state.chart_html != baseline_html, (
            "panel must re-render from the fresh reader — identical HTML "
            "means the stale pre-rewrite zip handle was reused"
        )
        assert server.state.edit_history == history_before
        assert server.state.edit_future == []
        assert server.state.edit_selected == [0]
        assert server.state.frozen_atoms == [0]
        assert scf_id in server.state.status_message

        def close_deferred(coroutine):
            coroutine.close()

        monkeypatch.setattr(appmod.asyncio, "ensure_future", close_deferred)

        # The scene was intentionally kept. Export and all later edit reads
        # must therefore see the same expanded atoms *and* expanded cell.
        server.controller.export_geometry("xyz")
        xyz = base64.b64decode(server.state.export_data.split(",", 1)[1]).decode()
        assert int(xyz.splitlines()[0]) == len(built.atoms)
        server.controller.export_geometry("cif")
        cif = base64.b64decode(server.state.export_data.split(",", 1)[1]).decode()
        assert f"_cell_length_a {np.linalg.norm(expected_lattice[0]):.4f}" in cif

        # History belongs to this same logical file and remains usable on the
        # fresh reader after the overlay migration.
        server.controller.edit_undo()
        server.controller.export_geometry("xyz")
        xyz = base64.b64decode(server.state.export_data.split(",", 1)[1]).decode()
        assert int(xyz.splitlines()[0]) == len(original.atoms)
        server.controller.export_geometry("cif")
        cif = base64.b64decode(server.state.export_data.split(",", 1)[1]).decode()
        assert f"_cell_length_a {np.linalg.norm(original_lattice[0]):.4f}" in cif

        server.controller.edit_redo()
        server.controller.export_geometry("xyz")
        xyz = base64.b64decode(server.state.export_data.split(",", 1)[1]).decode()
        assert int(xyz.splitlines()[0]) == len(built.atoms)
        server.controller.export_geometry("cif")
        cif = base64.b64decode(server.state.export_data.split(",", 1)[1]).decode()
        assert f"_cell_length_a {np.linalg.norm(expected_lattice[0]):.4f}" in cif
    finally:
        reader.close()


def test_watcher_repeated_panel_only_reload_closes_superseded_readers(
    water_qvf, monkeypatch, tmp_path
):
    """Each light reload must retire its previous primary reader immediately."""
    import shutil

    from trame.app import get_server

    import vibeview.app as appmod
    import vibeview.lazy_loader as lazy_loader
    import vibeview.qvf as qvfmod
    from vibeview.app import create_app
    from vibeview.file_watcher import QVFChangeTracker
    from vibeview.qvf import QVFReader

    live = tmp_path / "job.qvf"
    shutil.copy2(water_qvf, live)

    reader = QVFReader(live)
    replacements = []
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None
        server.controller.activate_section("scf_hist0")

        class TrackingQVFReader(QVFReader):
            def __init__(self, source):
                super().__init__(source)
                replacements.append(self)

        class LazyReaderStub:
            def __init__(self, path):
                self.path = path

            def close(self):
                pass

        # Track primary watcher replacements without counting the separate
        # raw reader owned by the lazy-controller wrapper.
        monkeypatch.setattr(qvfmod, "QVFReader", TrackingQVFReader)
        monkeypatch.setattr(lazy_loader, "QVFLazyReader", LazyReaderStub)
        monkeypatch.setattr(
            appmod, "_build_structure_scene", lambda *a, **k: pytest.fail(
                "panel-only change must not rebuild the 3D scene"
            )
        )

        tracker = QVFChangeTracker(live, settle_delay=0.0)
        previous_html = server.state.chart_html
        baseline_count = len(reader.read_scf_history("scf_hist0").iterations)
        for offset in range(1, 5):
            energy = -99.0 - offset / 10
            _append_scf_iteration(live, energy)
            tracker.poll()
            event = tracker.poll()
            assert event is not None
            assert event.changed_sections == ("scf_hist0",)

            server.controller.apply_watcher_event(event)

            assert len(replacements) == offset
            superseded = [reader, *replacements[:-1]]
            assert all(item._zf.fp is None for item in superseded)
            current = replacements[-1]
            assert current._zf.fp is not None
            history = current.read_scf_history("scf_hist0").iterations
            assert len(history) == baseline_count + offset
            assert history[-1]["energy_eh"] == energy
            assert server.state.chart_html != previous_html
            previous_html = server.state.chart_html
    finally:
        reader.close()
        for replacement in replacements:
            replacement.close()


def test_enabled_watcher_retargets_once_when_active_file_changes(
    water_qvf, monkeypatch, tmp_path
):
    """A file switch must cancel the old-path watcher and start the new one."""
    import shutil

    from trame.app import get_server

    import vibeview.app as appmod
    import vibeview.file_watcher as watcher_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    first_path = tmp_path / "first.qvf"
    second_path = tmp_path / "second.qvf"
    shutil.copy2(water_qvf, first_path)
    shutil.copy2(water_qvf, second_path)
    readers = [QVFReader(first_path), QVFReader(second_path)]

    class TaskSpy:
        def __init__(self, coroutine):
            self.coroutine = coroutine
            self.cancelled = False

        def cancel(self):
            self.cancelled = True
            self.coroutine.close()

    tracker_paths: list[Path] = []
    tasks: list[TaskSpy] = []

    class TrackerSpy:
        def __init__(self, path):
            tracker_paths.append(Path(path))
            self.has_pending = False

        def poll(self):
            return None

    try:
        create_app(readers)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None
        monkeypatch.setattr(watcher_mod, "QVFChangeTracker", TrackerSpy)
        monkeypatch.setattr(
            appmod.asyncio,
            "ensure_future",
            lambda coroutine: tasks.append(TaskSpy(coroutine)) or tasks[-1],
        )

        server.controller.toggle_file_watcher(True)
        assert tracker_paths == [first_path]
        assert len(tasks) == 1 and not tasks[0].cancelled

        server.controller.switch_file(1)

        assert tracker_paths == [first_path, second_path]
        assert tasks[0].cancelled is True
        assert len(tasks) == 2 and not tasks[1].cancelled
        assert server.state.file_watcher_enabled is True
    finally:
        for task in tasks:
            if not task.cancelled:
                task.cancel()
        for item in readers:
            item.close()


def test_enabled_watcher_disables_when_upload_selects_pathless_reader(
    water_qvf, monkeypatch, tmp_path
):
    """An in-memory upload cannot remain labelled as actively watched."""
    import shutil

    from trame.app import get_server

    import vibeview.app as appmod
    import vibeview.file_watcher as watcher_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    watched_path = tmp_path / "watched.qvf"
    shutil.copy2(water_qvf, watched_path)
    reader = QVFReader(watched_path)

    class TaskSpy:
        def __init__(self, coroutine):
            self.coroutine = coroutine
            self.cancelled = False

        def cancel(self):
            self.cancelled = True
            self.coroutine.close()

    tracker_paths: list[Path] = []
    tasks: list[TaskSpy] = []

    class TrackerSpy:
        def __init__(self, path):
            tracker_paths.append(Path(path))
            self.has_pending = False

        def poll(self):
            return None

    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None
        monkeypatch.setattr(watcher_mod, "QVFChangeTracker", TrackerSpy)
        monkeypatch.setattr(
            appmod.asyncio,
            "ensure_future",
            lambda coroutine: tasks.append(TaskSpy(coroutine)) or tasks[-1],
        )

        server.controller.toggle_file_watcher(True)
        assert tracker_paths == [watched_path]
        assert len(tasks) == 1 and not tasks[0].cancelled

        server.state.load_file_name = "uploaded.qvf"
        server.state.load_file_bytes = base64.b64encode(watched_path.read_bytes()).decode()
        server.controller.trigger_fn("load_file_from_bytes")()

        assert tracker_paths == [watched_path]
        assert tasks[0].cancelled is True
        assert server.state.file_watcher_enabled is False
        assert "Loaded uploaded.qvf" in server.state.status_message
    finally:
        for task in tasks:
            if not task.cancelled:
                task.cancel()
        reader.close()


def test_watcher_panel_only_reload_replaces_the_lazy_reader(
    water_qvf, monkeypatch, tmp_path
):
    """A fresh raw reader must be paired with a fresh lazy wrapper too."""
    import shutil
    import types

    from trame.app import get_server

    import vibeview.symmetry as symmetry
    from vibeview.app import create_app
    from vibeview.file_watcher import QVFChangeTracker
    from vibeview.qvf import QVFReader

    live = tmp_path / "job.qvf"
    shutil.copy2(water_qvf, live)
    seen = []

    def _capture(active_reader):
        seen.append(active_reader)
        return types.SimpleNamespace(symbol="captured")

    monkeypatch.setattr(symmetry, "detect_from_qvf", _capture)
    monkeypatch.setattr(symmetry, "pg_summary", lambda point_group: point_group.symbol)

    reader = QVFReader(live)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None
        server.controller.detect_symmetry()
        first_lazy = seen[-1]

        tracker = QVFChangeTracker(live, settle_delay=0.0)
        _append_scf_iteration(live, -99.9)
        tracker.poll()
        event = tracker.poll()
        assert event is not None and event.changed_sections == ("scf_hist0",)

        server.controller.apply_watcher_event(event)
        server.controller.detect_symmetry()
        second_lazy = seen[-1]

        assert second_lazy is not first_lazy
        assert Path(second_lazy.path) == live
        assert first_lazy._raw_reader._zf.fp is None
    finally:
        reader.close()


def test_watcher_added_section_rebuilds_scene_and_restores_selection(
    water_qvf, monkeypatch, tmp_path
):
    """A new section appearing (a streaming job emitting its next
    checkpoint section) must run the full reload — sidebar + scene, camera
    preserved — and restore the previously-active section."""
    import shutil

    from trame.app import get_server

    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.file_watcher import QVFChangeTracker
    from vibeview.qvf import QVFReader

    live = tmp_path / "job.qvf"
    shutil.copy(water_qvf, live)

    reader = QVFReader(live)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None
        server.controller.activate_section("scf_hist0")
        stale = {
            "positions": [[9.0, 9.0, 9.0]],
            "symbols": ["He"],
            "lattice_vectors": [[8.0, 0.0, 0.0], [0.0, 8.0, 0.0], [0.0, 0.0, 8.0]],
        }
        server.state.edit_history = [stale]
        server.state.edit_future = [stale]
        server.state.edit_selected = [0]
        server.state.frozen_atoms = [0]

        tracker = QVFChangeTracker(live, settle_delay=0.0)

        def _clone_scf(manifest, members):
            src = next(s for s in manifest["sections"] if s["id"] == "scf_hist0")
            clone = json.loads(json.dumps(src))
            clone["id"] = "scf_hist1"
            manifest["sections"].append(clone)

        _rewrite_qvf(live, _clone_scf)
        tracker.poll()
        event = tracker.poll()
        assert event is not None and event.added_sections == ("scf_hist1",)

        builds = []
        real_build = appmod._build_structure_scene

        def _spy(*a, **k):
            builds.append(a)
            return real_build(*a, **k)

        monkeypatch.setattr(appmod, "_build_structure_scene", _spy)
        marked: list[str] = []
        state_cls = type(server.state)
        real_dirty = state_cls.dirty
        state_cls.dirty = lambda self, *keys: (
            marked.extend(keys),
            real_dirty(self, *keys),
        )[1]
        try:
            server.controller.apply_watcher_event(event)
        finally:
            state_cls.dirty = real_dirty

        assert builds, "added section must trigger the full scene reload"
        ids = {s["id"] for s in server.state.section_list}
        assert "scf_hist1" in ids  # sidebar caught the new section
        assert server.state.selected_section == "scf_hist0"  # restored
        assert server.state.edit_history == []
        assert server.state.edit_future == []
        assert {"edit_history", "edit_future"} <= set(marked)
        assert server.state.edit_selected == []
        assert server.state.frozen_atoms == []
        server.controller.edit_undo()
        assert server.state.status_message == "Nothing to undo"
        server.controller.edit_redo()
        assert server.state.status_message == "Nothing to redo"
    finally:
        reader.close()


# ── M4: live streaming — provenance surface + watch-live wiring ──────────


def _set_provenance(path, run_status, checkpoint=None, partial_ids=()):
    """Stamp streaming-checkpoint provenance onto a QVF, as the producer's
    QvfCheckpointer does (provenance.run_status/.checkpoint + per-section
    partial flags)."""

    def _mutate(manifest, members):
        prov = manifest.setdefault("provenance", {})
        prov["run_status"] = run_status
        if checkpoint is not None:
            prov["checkpoint"] = checkpoint
        for sec in manifest["sections"]:
            if sec["id"] in partial_ids:
                sec["partial"] = True

    _rewrite_qvf(path, _mutate)


def test_reader_streaming_provenance_surface(water_qvf, tmp_path):
    """QVFReader must expose run_status / checkpoint_info / per-section
    partial from a live checkpoint, and default them off for ordinary
    files (consumer_qvf_reference.md § streaming)."""
    import shutil

    from vibeview.qvf import QVFReader

    plain = tmp_path / "plain.qvf"
    shutil.copy(water_qvf, plain)
    with QVFReader(plain) as r:
        assert r.run_status is None
        assert r.checkpoint_info == {}
        assert r.section_is_partial("traj0") is False

    live = tmp_path / "live.qvf"
    shutil.copy(water_qvf, live)
    _set_provenance(
        live, "running",
        checkpoint={"seq": 7, "scf_iteration": 4, "energy_eh": -75.98},
        partial_ids=("traj0",),
    )
    with QVFReader(live) as r:
        assert r.run_status == "running"
        assert r.checkpoint_info["seq"] == 7
        assert r.section_is_partial("traj0") is True
        assert r.section_is_partial("structure") is False


def test_checkpoint_summary_formats_chip_text():
    from vibeview.app import _checkpoint_summary

    class _R:
        run_status = "running"
        checkpoint_info = {"seq": 7, "scf_iteration": 4, "energy_eh": -75.981234567}

    status, text = _checkpoint_summary(_R())
    assert status == "running"
    assert text == "#7 · iter 4 · E -75.981235 Eh"

    class _Plain:
        run_status = None
        checkpoint_info = {}

    assert _checkpoint_summary(_Plain()) == ("", "")


def test_watcher_reload_announces_terminal_status(water_qvf, tmp_path):
    """When a watched checkpoint flips run_status running → converged, the
    reload must update the app-bar chip state and announce the finish."""
    import shutil

    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.file_watcher import QVFChangeTracker
    from vibeview.qvf import QVFReader

    live = tmp_path / "job.qvf"
    shutil.copy(water_qvf, live)
    _set_provenance(live, "running", checkpoint={"seq": 1})

    reader = QVFReader(live)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None
        assert server.state.live_run_status == "running"

        tracker = QVFChangeTracker(live, settle_delay=0.0)
        _set_provenance(live, "converged", checkpoint={"seq": 2})
        _append_scf_iteration(live, -75.99)  # sections also settle
        tracker.poll()
        event = tracker.poll()
        assert event is not None and event.manifest_meta_changed

        server.controller.apply_watcher_event(event)
        assert server.state.live_run_status == "converged"
        assert "converged" in server.state.status_message
    finally:
        reader.close()


def test_watcher_follows_trajectory_head(water_qvf, tmp_path):
    """A user parked on the last trajectory frame keeps following the head
    across a hot-reload (the watch-it-grow demo behaviour)."""
    import shutil

    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.file_watcher import QVFChangeTracker
    from vibeview.qvf import QVFReader

    live = tmp_path / "job.qvf"
    shutil.copy(water_qvf, live)

    reader = QVFReader(live)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None
        server.controller.activate_section("traj0")
        n = server.state.trajectory_n_frames
        assert n > 0
        server.controller.trajectory_frame(n - 1)  # park on the head

        tracker = QVFChangeTracker(live, settle_delay=0.0)
        # structure content change → full-reload path (re-activation resets
        # the frame to 0; head-follow must jump back to the last frame)
        def _touch_structure(manifest, members):
            sec = next(s for s in manifest["sections"] if s["id"] == "structure")
            member = sec["members"][next(iter(sec["members"]))]
            blob = members[member["path"]]
            members[member["path"]] = blob
            sec["x_test_touch"] = "1"  # metadata-only change to the section

        _rewrite_qvf(live, _touch_structure)
        tracker.poll()
        event = tracker.poll()
        assert event is not None and "structure" in event.changed_sections

        server.controller.apply_watcher_event(event)
        assert server.state.selected_section == "traj0"
        assert server.state.trajectory_frame == server.state.trajectory_n_frames - 1
    finally:
        reader.close()


@pytest.mark.parametrize("watch_error", [None, RuntimeError("poll failed")])
def test_vq_watch_live_reuses_and_recovers_watcher(
    water_qvf, monkeypatch, tmp_path, watch_error
):
    """Watch-live reuses a running task and recovers after it stops."""
    import shutil
    import sys
    import types

    from trame.app import get_server

    import vibeview.app as appmod
    import vibeview.file_watcher as watcher_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    ckpt = tmp_path / "checkpoint.qvf"
    shutil.copy(water_qvf, ckpt)
    _set_provenance(ckpt, "running", checkpoint={"seq": 3})

    status_mod = types.ModuleType("vq.status")
    status_mod.show_status_json = lambda host, jid, tail=50: json.dumps(
        {
            "job_name": "opt-live",
            "checkpoint_qvf_path": str(ckpt),
            "checkpoint_qvf_exists": True,
        }
    )
    monkeypatch.setitem(sys.modules, "vq.status", status_mod)

    class TaskSpy:
        def __init__(self, coroutine):
            self.coroutine = coroutine
            self._callbacks = []
            self._cancelled = False
            self._done = False
            self._error = None

        def add_done_callback(self, callback):
            self._callbacks.append(callback)

        def cancel(self):
            if self._done:
                return
            self._cancelled = True
            self._finish()

        def cancelled(self):
            return self._cancelled

        def done(self):
            return self._done

        def exception(self):
            return self._error

        def finish(self, error=None):
            self._error = error
            self._finish()

        def _finish(self):
            if self._done:
                return
            self._done = True
            self.coroutine.close()
            for callback in self._callbacks:
                callback(self)

    tracker_paths: list[Path] = []
    tasks: list[TaskSpy] = []

    class TrackerSpy:
        def __init__(self, path):
            tracker_paths.append(Path(path))
            self.has_pending = False

        def poll(self):
            return None

    monkeypatch.setattr(watcher_mod, "QVFChangeTracker", TrackerSpy)
    monkeypatch.setattr(
        appmod.asyncio,
        "ensure_future",
        lambda coroutine: tasks.append(TaskSpy(coroutine)) or tasks[-1],
    )

    reader = QVFReader(water_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None
        n_before = len(server.state.file_names)
        watcher_flushes = []
        monkeypatch.setattr(
            type(server.state),
            "flush",
            lambda state: watcher_flushes.append(state),
        )

        server.controller.vq_watch_live("abcdef123456")

        assert len(server.state.file_names) == n_before + 1
        assert server.state.file_watcher_enabled is True
        assert server.state.live_run_status == "running"
        assert "Watching opt-live live" in server.state.status_message

        # Repeating the same action while polling must neither duplicate the
        # reader nor allocate a second tracker/task for the same path.
        server.controller.vq_watch_live("abcdef123456")
        assert len(server.state.file_names) == n_before + 1
        assert tracker_paths == [ckpt]
        assert len(tasks) == 1

        flushes_before_stop = len(watcher_flushes)
        tasks[0].finish(watch_error)
        assert server.state.file_watcher_enabled is False
        assert "Auto-reload stopped" in server.state.status_message
        assert len(watcher_flushes) == flushes_before_stop + 1

        # A completed or failed task relinquishes ownership, so the next
        # Watch-live action starts one fresh watcher for the same archive.
        server.controller.vq_watch_live("abcdef123456")
        assert len(server.state.file_names) == n_before + 1
        assert tracker_paths == [ckpt, ckpt]
        assert len(tasks) == 2
        assert server.state.file_watcher_enabled is True
        assert "Watching opt-live live" in server.state.status_message
    finally:
        for task in tasks:
            task.cancel()
        reader.close()


def test_file_watcher_schedule_failure_disables_truthfully(
    water_qvf, monkeypatch
):
    """A missing scheduler must not leave auto-reload labelled as active."""
    from trame.app import get_server

    import vibeview.app as appmod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    def reject_schedule(coroutine):
        raise RuntimeError("event loop is unavailable")

    reader = QVFReader(water_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None
        monkeypatch.setattr(appmod.asyncio, "ensure_future", reject_schedule)

        server.controller.toggle_file_watcher(True)

        assert server.state.file_watcher_enabled is False
        assert server.state.status_message == (
            "Auto-reload unavailable: event loop is unavailable"
        )
    finally:
        reader.close()


def test_generate_input_live_checkpoint_kwargs():
    """live_checkpoint=True must add checkpoint_qvf/$VQ_WORKDIR wiring to
    every molecular template and stay valid Python; off by default."""
    from vibeview.input_generator import generate_input

    atoms = [{"symbol": "H", "atomic_number": 1, "position": [0.0, 0.0, 0.0]}]
    for template in ("single_point", "optimization", "frequencies", "full"):
        plain = generate_input(atoms, template=template)
        assert "checkpoint_qvf" not in plain
        live = generate_input(atoms, template=template, live_checkpoint=True)
        assert 'os.environ.get("VQ_WORKDIR"' in live
        assert "checkpoint_every=1" in live
        assert "import os" in live
        compile(live, "<generated>", "exec")  # stays valid Python


def test_toggle_wrap_periodic_flips_and_rebuilds(showcase_qvf, monkeypatch):
    """The cyclic-cluster wrap toggle flips state, invalidates the mesh
    cache, and rebuilds without raising on a periodic file (NaCl showcase).
    """
    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(showcase_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None
        assert "toggle_wrap_periodic" in server.controller._triggers or hasattr(
            server.controller, "toggle_wrap_periodic"
        )
        assert server.state.wrap_periodic_orbital is False
        server.controller.toggle_wrap_periodic(True)
        assert server.state.wrap_periodic_orbital is True
        server.controller.toggle_wrap_periodic(False)
        assert server.state.wrap_periodic_orbital is False
    finally:
        reader.close()


def test_wannier_overlay_detected_and_toggles(monkeypatch, tmp_path):
    """create_app detects an x_ccm.wannier_centers section (has_wannier_centers)
    and the overlay toggle draws N markers without raising."""
    import io as _io
    import zipfile as _zip

    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    struct = json.dumps({
        "atoms": [
            {"symbol": "H", "position": [0, 0, 0], "atomic_number": 1},
            {"symbol": "H", "position": [1.0, 0, 0], "atomic_number": 1},
            {"symbol": "H", "position": [3.0, 0, 0], "atomic_number": 1},
        ],
        "pbc": [False, False, False],
    }).encode()
    centres = json.dumps([
        {"center": [0.5, 0, 0], "spread": 1.2, "label": "H1-H2"},
        {"center": [3.0, 0, 0], "spread": 2.5},
    ]).encode()
    manifest = {
        "qvf_version": 1,
        "source": {"program": "test", "version": "0", "calculation": "ccm"},
        "sections": [
            {"id": "structure", "kind": "structure",
             "members": {"structure": {"path": "s.json", "format": "json", "sha256": _sha(struct)}}},
            {"id": "wan0", "kind": "x_ccm.wannier_centers",
             "members": {"centers": {"path": "w.json", "format": "json", "sha256": _sha(centres)}}},
        ],
    }
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", struct)
        zf.writestr("w.json", centres)
    buf.seek(0)

    reader = QVFReader(buf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.controller.view_update = lambda *a, **k: None
        assert server.state.has_wannier_centers is True
        assert server.state.show_wannier_centers is False
        server.controller.toggle_wannier_centers(True)
        assert server.state.show_wannier_centers is True
        assert "2" in server.state.status_message  # 2 centres shown
        server.controller.toggle_wannier_centers(False)
        assert server.state.show_wannier_centers is False
    finally:
        reader.close()


def _structure_plus_volume_qvf() -> Path:
    """In-memory QVF: a water structure + one volume.density section."""
    def sha(b: bytes) -> str:
        return hashlib.sha256(b).hexdigest()

    structure = json.dumps({
        "atoms": [
            {"symbol": "O", "position": [0.0, 0.0, 0.117], "atomic_number": 8},
            {"symbol": "H", "position": [0.0, 0.757, -0.469], "atomic_number": 1},
            {"symbol": "H", "position": [0.0, -0.757, -0.469], "atomic_number": 1},
        ],
        "pbc": [False, False, False], "lattice_vectors": None,
    }).encode()
    grid = json.dumps({
        "origin": [-4, -4, -4],
        "voxel_vectors": [[0.4, 0, 0], [0, 0.4, 0], [0, 0, 0.4]],
        "shape": [20, 20, 20],
    }).encode()
    data = (np.random.default_rng(0).standard_normal((20, 20, 20)).astype(np.float32) * 0.05)
    data[10, 10, 11] = 1.0
    db = data.tobytes()
    sections = [
        {"id": "structure", "kind": "structure", "members": {
            "structure": {"path": "s.json", "format": "json", "sha256": sha(structure)}}},
        {"id": "dens", "kind": "volume.density", "members": {
            "grid": {"path": "g.json", "format": "json", "sha256": sha(grid)},
            "data": {"path": "d.dat", "format": "binary", "dtype": "float32",
                     "shape": [20, 20, 20], "sha256": sha(db)}}},
    ]
    manifest = {"qvf_version": 1,
                "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
                "sections": sections}
    tmp = tempfile.NamedTemporaryFile(suffix=".qvf", delete=False)  # noqa: SIM115 — must outlive this fn
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for p, d in {"s.json": structure, "g.json": grid, "d.dat": db}.items():
            zf.writestr(p, d)
    return Path(tmp.name)


def test_activate_section_survives_a_renderer_exception(monkeypatch):
    """Stability: a renderer that raises while switching sections must not
    crash or freeze the viewer. `activate_section` catches it, surfaces a
    status message, and stays usable so the next section still activates.
    """
    pytest.importorskip("pyvista")
    pytest.importorskip("trame.ui.vuetify3")

    from trame.app import get_server

    import vibeview.app as app_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    path = _structure_plus_volume_qvf()
    try:
        reader = QVFReader(path)

        def _boom(*a, **k):
            raise RuntimeError("simulated renderer failure")

        # Make the volume renderer explode on activation.
        monkeypatch.setattr(app_mod, "_activate_volume", _boom)

        create_app(reader)
        server = get_server(client_type="vue3")
        ctrl = server.controller

        # Switching to the volume must NOT propagate the exception ...
        ctrl.activate_section("dens")
        assert "Could not open section 'dens'" in (server.state.status_message or "")

        # ... and the app is still usable: another section activates fine.
        server.state.status_message = ""
        ctrl.activate_section("structure")
        assert server.state.selected_section == "structure"
        assert "Could not open" not in (server.state.status_message or "")
    finally:
        path.unlink(missing_ok=True)
