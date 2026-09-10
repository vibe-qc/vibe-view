"""Live geometry optimization (M3, B1/B2) tests.

Layers:

* wire protocol — ``build_request`` / ``parse_event`` / engine field
  (no deps);
* ``LiveOptService`` — debounce / stream / cancel / note-routing
  semantics against a canned-JSON fake worker subprocess (no vibe-qc
  needed, CI-safe), plus the no-event-loop guard;
* no-vibe-qc gating — the worker probe and a direct optimize request
  run in a subprocess whose ``vibeqc`` import is *blocked* by a
  PYTHONPATH shim, pinning the degrade path vibe-view ships to users
  who install it standalone;
* the real MSINDO worker — end-to-end on distorted water
  (``importorskip vibeqc``, skipped on CI where vibe-qc is absent);
* the MACE engine — mock-based (fake ``vibeqc.mlip`` + ``ase`` in
  ``sys.modules``; the torch stack is not a test dep and MACE caps at
  Python 3.13 while this repo runs 3.14): dispatch, harmonic-well
  convergence, unit conversion, note emission, element-coverage error;
* app wiring — toggle / edit hooks / engine picker drive
  ``LiveOptService.schedule``; the step callback applies geometry +
  pushes one undo entry (fake service, CI-safe).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("PYVISTA_OFF_SCREEN", "True")

showcase_qvf = (
    Path(__file__).resolve().parents[2] / "examples" / "vibe_view" / "output-nacl-showcase.qvf"
)


# ── Wire protocol ─────────────────────────────────────────────────────────


def test_build_request_shape():
    from vibeview.live_opt import DEFAULT_FMAX, build_request

    req = build_request(["O", "H"], [[0, 0, 0], [1.0, 0, 0]], charge=-1, frozen=[0])
    assert req["symbols"] == ["O", "H"]
    assert req["positions"] == [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    assert req["charge"] == -1
    assert req["frozen"] == [0]
    assert req["fmax"] == DEFAULT_FMAX
    assert req["method"] == "msindo"  # default engine
    json.dumps(req)  # must be JSON-serializable as-is

    assert build_request(["H"], [[0, 0, 0]], engine="mace")["method"] == "mace"
    with pytest.raises(ValueError, match="unknown live-opt engine"):
        build_request(["H"], [[0, 0, 0]], engine="dowsing-rod")


def test_parse_event_tolerates_noise():
    from vibeview.live_opt import parse_event

    assert parse_event("") is None
    assert parse_event("   \n") is None
    assert parse_event("not json") is None
    assert parse_event(b'["a", "list"]') is None
    assert parse_event(b'{"step": 1}') == {"step": 1}


# ── LiveOptService against a fake worker ─────────────────────────────────


def _fake_worker(tmp_path: Path, body: str) -> list[str]:
    """Write a stub worker script; returns the worker_cmd for the service."""
    script = tmp_path / "fake_worker.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


def _run_service(worker_cmd, request, *, debounce_s=0.01, timeout_s=10.0, actions=None):
    """Drive one schedule() through a fresh event loop; returns the log."""
    from vibeview.live_opt import LiveOptService

    log: list[tuple] = []

    async def _main():
        service = LiveOptService(
            on_step=lambda e: log.append(("step", e)),
            on_done=lambda c, s: log.append(("done", c, s)),
            on_error=lambda m: log.append(("error", m)),
            debounce_s=debounce_s,
            timeout_s=timeout_s,
            worker_cmd=worker_cmd,
        )
        service.schedule(request)
        if actions is not None:
            await actions(service)
        # Wait for the run to finish (or be cancelled).
        while service.running:
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.05)  # let final callbacks land

    asyncio.run(_main())
    return log


def test_service_streams_steps_and_done(tmp_path):
    cmd = _fake_worker(
        tmp_path,
        """
        import json, sys
        req = json.loads(sys.stdin.read())
        n = len(req["symbols"])
        for k in (1, 2):
            print(json.dumps({"step": k, "energy": -1.0 * k, "gmax": 0.1,
                              "positions": [[float(k)] * 3] * n}), flush=True)
        print(json.dumps({"done": True, "converged": True, "steps": 2}), flush=True)
        """,
    )
    from vibeview.live_opt import build_request

    log = _run_service(cmd, build_request(["H", "H"], [[0, 0, 0], [1, 0, 0]]))
    kinds = [e[0] for e in log]
    assert kinds == ["step", "step", "done"]
    assert log[0][1]["positions"] == [[1.0, 1.0, 1.0]] * 2
    assert log[-1] == ("done", True, 2)


def test_service_reports_worker_error(tmp_path):
    cmd = _fake_worker(
        tmp_path,
        """
        import json, sys
        sys.stdin.read()
        print(json.dumps({"error": "boom"}), flush=True)
        """,
    )
    from vibeview.live_opt import build_request

    log = _run_service(cmd, build_request(["H"], [[0, 0, 0]]))
    assert log == [("error", "boom")]


def test_service_cancel_on_reschedule_drops_first_run(tmp_path):
    """schedule() during a run must kill it: only the second request's
    events arrive (cancel-on-edit)."""
    cmd = _fake_worker(
        tmp_path,
        """
        import json, sys, time
        req = json.loads(sys.stdin.read())
        marker = req["positions"][0][0]  # echo which request this is
        n = len(req["symbols"])
        for k in range(1, 4):
            print(json.dumps({"step": k, "energy": 0.0, "gmax": 0.1,
                              "positions": [[marker] * 3] * n}), flush=True)
            time.sleep(0.2)
        print(json.dumps({"done": True, "converged": True, "steps": 3}), flush=True)
        """,
    )
    from vibeview.live_opt import build_request

    first = build_request(["H"], [[1.0, 0, 0]])
    second = build_request(["H"], [[2.0, 0, 0]])

    async def _actions(service):
        await asyncio.sleep(0.3)  # first run is mid-stream
        service.schedule(second)

    log = _run_service(cmd, first, actions=_actions)
    markers = {e[1]["positions"][0][0] for e in log if e[0] == "step"}
    assert 2.0 in markers, "second request's steps must arrive"
    assert log[-1][0] == "done"
    # No first-request step may arrive AFTER the reschedule; since the
    # second run's events all carry marker 2.0, any 1.0 events must
    # precede them — and the done event belongs to run two.
    tail = [e for e in log if e[0] == "step"][-1]
    assert tail[1]["positions"][0][0] == 2.0


def test_service_debounce_coalesces(tmp_path):
    """Rapid re-schedules within the debounce window run only once."""
    counter = tmp_path / "runs.txt"
    cmd = _fake_worker(
        tmp_path,
        f"""
        import json, sys
        with open({str(counter)!r}, "a") as fh:
            fh.write("run\\n")
        req = json.loads(sys.stdin.read())
        print(json.dumps({{"done": True, "converged": True, "steps": 0}}), flush=True)
        """,
    )
    from vibeview.live_opt import build_request

    req = build_request(["H"], [[0, 0, 0]])

    async def _actions(service):
        for _ in range(4):
            await asyncio.sleep(0.02)
            service.schedule(req)

    log = _run_service(cmd, req, debounce_s=0.15, actions=_actions)
    assert log and log[-1][0] == "done"
    assert counter.read_text().count("run") == 1


# ── Real MSINDO worker (needs vibe-qc; skipped on CI) ────────────────────


def test_worker_probe_reports_availability():
    """--probe must emit one JSON object with an ``available`` bool and an
    ``engines`` list, whether or not vibe-qc is installed."""
    out = subprocess.run(
        [sys.executable, "-m", "vibeview.live_opt", "--probe"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    payload = json.loads(out.stdout.strip().splitlines()[-1])
    assert isinstance(payload["available"], bool)
    assert isinstance(payload["engines"], list)
    if payload["available"]:
        assert "msindo" in payload["engines"]
    else:
        assert payload["reason"]
        assert payload["engines"] == []


def _blocked_vibeqc_env(tmp_path: Path) -> dict[str, str]:
    """An environment where ``import vibeqc`` fails even though the real
    package may be installed. A path-shadowing shim is NOT enough — the
    editable vibe-qc install registers a meta-path finder, which runs
    before any sys.path scan — so a ``sitecustomize`` (imported by
    ``site`` at interpreter startup, found via PYTHONPATH) installs a
    blocking finder at the head of ``sys.meta_path``."""
    shim = tmp_path / "shim"
    shim.mkdir(parents=True)
    (shim / "sitecustomize.py").write_text(
        textwrap.dedent(
            """
            import sys

            class _BlockVibeqc:
                def find_spec(self, name, path=None, target=None):
                    if name == "vibeqc" or name.startswith("vibeqc."):
                        raise ModuleNotFoundError("vibeqc blocked for gating test")
                    return None

            sys.meta_path.insert(0, _BlockVibeqc())
            """
        )
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(shim) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def test_probe_gates_cleanly_without_vibeqc(tmp_path):
    """vibe-view installed without vibe-qc: the probe must say so (with the
    reason), offer no engines, and never crash."""
    out = subprocess.run(
        [sys.executable, "-m", "vibeview.live_opt", "--probe"],
        capture_output=True,
        text=True,
        timeout=120,
        env=_blocked_vibeqc_env(tmp_path),
    )
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout.strip().splitlines()[-1])
    assert payload["available"] is False
    assert payload["engines"] == []
    assert "vibe-qc not importable" in payload["reason"]


def test_optimize_request_errors_cleanly_without_vibeqc(tmp_path):
    """A direct optimize request without vibe-qc must come back as one
    parseable error event (exit code 1), not a traceback on stdout."""
    pytest.importorskip("scipy")  # worker imports scipy before the engine build
    req = {"symbols": ["H", "H"], "positions": [[0, 0, 0], [0.8, 0, 0]], "charge": 0}
    out = subprocess.run(
        [sys.executable, "-m", "vibeview.live_opt"],
        input=json.dumps(req),
        capture_output=True,
        text=True,
        timeout=120,
        env=_blocked_vibeqc_env(tmp_path),
    )
    assert out.returncode == 1
    events = [json.loads(line) for line in out.stdout.splitlines() if line.strip()]
    assert len(events) == 1 and "error" in events[0]
    assert "vibeqc" in events[0]["error"]


def test_worker_relaxes_distorted_water():
    pytest.importorskip("vibeqc")
    req = {
        "symbols": ["O", "H", "H"],
        "positions": [[0, 0, 0], [1.3, 0, 0], [0, 1.4, 0.2]],
        "charge": 0,
    }
    out = subprocess.run(
        [sys.executable, "-m", "vibeview.live_opt"],
        input=json.dumps(req),
        capture_output=True,
        text=True,
        timeout=300,
    )
    events = [json.loads(line) for line in out.stdout.splitlines() if line.strip()]
    assert events, out.stderr
    assert events[-1].get("done") and events[-1]["converged"]
    final = np.asarray(events[-2]["positions"])
    r1 = np.linalg.norm(final[1] - final[0])
    r2 = np.linalg.norm(final[2] - final[0])
    # MSINDO water: r_OH ~ 0.96 A (the 1.3/1.4 A sketch must contract)
    assert 0.90 < r1 < 1.05 and 0.90 < r2 < 1.05


def test_worker_rejects_unknown_element():
    pytest.importorskip("vibeqc")
    out = subprocess.run(
        [sys.executable, "-m", "vibeview.live_opt"],
        input=json.dumps({"symbols": ["Xx"], "positions": [[0, 0, 0]], "charge": 0}),
        capture_output=True,
        text=True,
        timeout=120,
    )
    events = [json.loads(line) for line in out.stdout.splitlines() if line.strip()]
    assert len(events) == 1 and "unknown element" in events[0]["error"]


# ── MACE engine (mock-based: the [mace] torch stack is not a test dep,
#    and MACE caps at Python <= 3.13 while this repo runs 3.14) ──────────


def test_mace_engine_dispatch_with_fake_stack(monkeypatch):
    """engine="mace" drives the shared L-BFGS-B loop through vibeqc.mlip's
    documented surface (MLIPOptions / resolve_model / mace_calculator /
    ase.Atoms), emits the model-loading note, and relaxes on the fake
    calculator's harmonic well."""
    pytest.importorskip("scipy")
    import types

    from vibeview.live_opt import _HARTREE_EV, _run_optimization

    target = np.array([[0.0, 0.0, 0.0], [0.75, 0.0, 0.0]])

    class FakeAtoms:
        def __init__(self, numbers, positions):
            self.numbers = list(numbers)
            self.positions = np.asarray(positions, dtype=float)
            self.calc = None

        # Harmonic well pulling every atom to `target` (eV, eV/A).
        def get_potential_energy(self):
            return float(((self.positions - target) ** 2).sum())

        def get_forces(self):
            return -2.0 * (self.positions - target)

    class FakeInfo:
        key = "medium-mpa-0"

        @staticmethod
        def unsupported_elements(numbers):
            return [z for z in numbers if z > 89]

    fake_vibeqc = types.ModuleType("vibeqc")
    fake_vibeqc.__path__ = []
    fake_mlip = types.ModuleType("vibeqc.mlip")
    fake_mlip.MLIPOptions = lambda: types.SimpleNamespace(model=None)
    fake_models = types.ModuleType("vibeqc.mlip._mace_models")
    fake_models.resolve_model = lambda key: FakeInfo()
    fake_mace = types.ModuleType("vibeqc.mlip.mace")
    fake_mace.mace_calculator = lambda options: object()
    fake_ase = types.ModuleType("ase")
    fake_ase.Atoms = FakeAtoms

    monkeypatch.setitem(sys.modules, "vibeqc", fake_vibeqc)
    monkeypatch.setitem(sys.modules, "vibeqc.mlip", fake_mlip)
    monkeypatch.setitem(sys.modules, "vibeqc.mlip._mace_models", fake_models)
    monkeypatch.setitem(sys.modules, "vibeqc.mlip.mace", fake_mace)
    monkeypatch.setitem(sys.modules, "ase", fake_ase)

    events: list[dict] = []
    _run_optimization(
        {
            "symbols": ["H", "H"],
            "positions": [[0.3, 0.2, -0.1], [1.4, -0.3, 0.2]],
            "charge": 0,
            "method": "mace",
            "fmax": 1e-6,  # tight: the assert below wants the exact minimum
        },
        events.append,
    )

    notes = [e for e in events if "note" in e]
    assert notes and "medium-mpa-0" in notes[0]["note"]
    assert events[-1]["done"] and events[-1]["converged"]
    final = np.asarray(events[-2]["positions"])
    np.testing.assert_allclose(final, target, atol=1e-3)
    # Energy is reported in Hartree (converted from the calculator's eV).
    e_ha = events[-2]["energy"]
    assert abs(e_ha - ((final - target) ** 2).sum() / _HARTREE_EV) < 1e-8


def test_mace_engine_rejects_uncovered_element(monkeypatch):
    pytest.importorskip("scipy")
    import types

    from vibeview.live_opt import _run_optimization

    class FakeInfo:
        key = "medium-mpa-0"

        @staticmethod
        def unsupported_elements(numbers):
            return [z for z in numbers if z > 8]

    fake_vibeqc = types.ModuleType("vibeqc")
    fake_vibeqc.__path__ = []
    fake_mlip = types.ModuleType("vibeqc.mlip")
    fake_mlip.MLIPOptions = lambda: types.SimpleNamespace(model=None)
    fake_models = types.ModuleType("vibeqc.mlip._mace_models")
    fake_models.resolve_model = lambda key: FakeInfo()
    fake_mace = types.ModuleType("vibeqc.mlip.mace")
    fake_mace.mace_calculator = lambda options: object()

    monkeypatch.setitem(sys.modules, "vibeqc", fake_vibeqc)
    monkeypatch.setitem(sys.modules, "vibeqc.mlip", fake_mlip)
    monkeypatch.setitem(sys.modules, "vibeqc.mlip._mace_models", fake_models)
    monkeypatch.setitem(sys.modules, "vibeqc.mlip.mace", fake_mace)

    with pytest.raises(ValueError, match="does not cover"):
        _run_optimization(
            {"symbols": ["Fe"], "positions": [[0, 0, 0]], "charge": 0, "method": "mace"},
            lambda e: None,
        )


# ── B3 freeze-atom constraints ────────────────────────────────────────


def test_frozen_atoms_held_fixed(monkeypatch):
    """Atoms listed in ``frozen`` keep their exact input coordinates through
    the relax while the free atoms move. The constraint is applied in the
    shared L-BFGS-B loop (only free-atom coords are varied), so it holds for
    every engine — here a trivial harmonic-to-origin builder stands in."""
    pytest.importorskip("scipy")
    import vibeview.live_opt as lo
    from vibeview.live_opt import _run_optimization

    def fake_builder(numbers, charge, emit):
        # Harmonic well pulling every atom to the origin (Ha, Ha/bohr).
        def energy_and_gradient(coords):
            coords = np.asarray(coords, dtype=float)
            return float((coords**2).sum()), 2.0 * coords

        return energy_and_gradient

    monkeypatch.setitem(lo._ENGINE_BUILDERS, "msindo", fake_builder)

    frozen_pos = [1.5, 0.0, 0.0]
    events: list[dict] = []
    _run_optimization(
        {
            "symbols": ["H", "H"],
            "positions": [frozen_pos, [2.0, 0.0, 0.0]],
            "method": "msindo",
            "frozen": [0],
            "fmax": 1e-6,
        },
        events.append,
    )

    # Every emitted geometry keeps atom 0 exactly where it started ...
    for e in events:
        if "positions" in e:
            np.testing.assert_allclose(e["positions"][0], frozen_pos, atol=1e-12)
    # ... while the free atom is pulled from x=2 toward the origin.
    final = np.asarray(events[-2]["positions"])
    np.testing.assert_allclose(final[0], frozen_pos, atol=1e-9)
    assert abs(final[1][0]) < 1e-2


def test_run_optimization_rejects_all_frozen():
    """Freezing every atom is a clean error, not a crash (surfaced to the
    viewer as a live-opt error). Raised before any engine is built."""
    pytest.importorskip("scipy")  # _run_optimization imports scipy up front
    from vibeview.live_opt import _run_optimization

    with pytest.raises(ValueError, match="all atoms frozen"):
        _run_optimization(
            {
                "symbols": ["H", "H"],
                "positions": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
                "method": "msindo",
                "frozen": [0, 1],
            },
            lambda e: None,
        )


def test_service_routes_note_events(tmp_path):
    cmd = _fake_worker(
        tmp_path,
        """
        import json, sys
        sys.stdin.read()
        print(json.dumps({"note": "loading model ..."}), flush=True)
        print(json.dumps({"done": True, "converged": True, "steps": 0}), flush=True)
        """,
    )
    from vibeview.live_opt import LiveOptService, build_request

    log: list[tuple] = []

    async def _main():
        service = LiveOptService(
            on_step=lambda e: log.append(("step", e)),
            on_done=lambda c, s: log.append(("done", c, s)),
            on_error=lambda m: log.append(("error", m)),
            on_note=lambda m: log.append(("note", m)),
            debounce_s=0.01,
            worker_cmd=cmd,
        )
        service.schedule(build_request(["H"], [[0, 0, 0]]))
        while service.running:
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.05)

    asyncio.run(_main())
    assert log == [("note", "loading model ..."), ("done", True, 0)]


def test_service_routes_callbacks_bound_to_one_schedule(tmp_path):
    """Per-run callbacks override defaults without changing wire behavior."""
    from vibeview.live_opt import LiveOptService, build_request

    cmd = _fake_worker(
        tmp_path,
        """
        import json, sys
        req = json.loads(sys.stdin.read())
        print(json.dumps({"step": 1, "energy": -1.0, "gmax": 0.1,
                          "positions": req["positions"]}), flush=True)
        print(json.dumps({"done": True, "converged": True, "steps": 1}), flush=True)
        """,
    )
    defaults: list[tuple] = []
    run: list[tuple] = []

    async def _main():
        service = LiveOptService(
            on_step=lambda event: defaults.append(("step", event)),
            on_done=lambda converged, steps: defaults.append(
                ("done", converged, steps)
            ),
            on_error=lambda message: defaults.append(("error", message)),
            worker_cmd=cmd,
            debounce_s=0.01,
        )
        service.schedule(
            build_request(["H", "H"], [[0, 0, 0], [1, 0, 0]]),
            on_step=lambda event: run.append(("step", event)),
            on_done=lambda converged, steps: run.append(
                ("done", converged, steps)
            ),
            on_error=lambda message: run.append(("error", message)),
        )
        while service.running:
            await asyncio.sleep(0.02)

    asyncio.run(_main())
    assert defaults == []
    assert [entry[0] for entry in run] == ["step", "done"]


def test_schedule_without_event_loop_reports_error():
    """Headless misuse (no running loop) must surface via on_error, not
    crash the caller."""
    from vibeview.live_opt import LiveOptService, build_request

    errors: list[str] = []
    service = LiveOptService(
        on_step=lambda e: None,
        on_done=lambda c, s: None,
        on_error=errors.append,
    )
    service.schedule(build_request(["H"], [[0, 0, 0]]))
    assert errors and "event loop" in errors[0]


# ── App wiring (fake service, CI-safe) ────────────────────────────────────


def _install_recording_live_opt(monkeypatch):
    """Install a callback-recording service and tracked off-screen plotters."""
    import vibeview.app as appmod
    import vibeview.live_opt as lo_mod

    class RecordingService:
        instances: list = []

        def __init__(self, **kwargs):
            self.defaults = kwargs
            self.scheduled: list = []
            self.callbacks: list[dict] = []
            self.cancelled = 0
            RecordingService.instances.append(self)

        def schedule(self, request, **callbacks):
            self.scheduled.append(request)
            self.callbacks.append(callbacks)

        def cancel(self):
            self.cancelled += 1

        @property
        def running(self):
            return False

    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    monkeypatch.setattr(lo_mod, "LiveOptService", RecordingService)
    monkeypatch.setattr(appmod.pv, "Plotter", create_plotter)
    return RecordingService, created


def test_reader_switch_reschedule_rejects_every_late_prior_run_callback(
    showcase_qvf,
    monkeypatch,
):
    """Scheduling B must not re-authorize callbacks captured for run A."""
    from trame.app import get_server

    import vibeview.app as appmod
    import vibeview.live_opt as lo_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    class FakeService:
        instances: list = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.scheduled: list = []
            self.callbacks: list[dict] = []
            self.cancelled = 0
            FakeService.instances.append(self)

        def schedule(self, request, **callbacks):
            self.scheduled.append(request)
            self.callbacks.append(callbacks)

        def cancel(self):
            self.cancelled += 1

        @property
        def running(self):
            return False

    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    monkeypatch.setattr(lo_mod, "LiveOptService", FakeService)
    monkeypatch.setattr(appmod.pv, "Plotter", create_plotter)

    readers = [QVFReader(showcase_qvf), QVFReader(showcase_qvf)]
    try:
        create_app(readers)
        server = get_server(client_type="vue3")
        state, ctrl = server.state, server.controller
        ctrl.view_update = lambda *_args, **_kwargs: None
        service = FakeService.instances[-1]

        state.live_opt_available = True
        state.live_opt_enabled = False
        ctrl.toggle_live_opt(True)
        assert service.scheduled
        run_a = service.callbacks[-1]
        n_atoms = len(service.scheduled[-1]["symbols"])
        incoming_before = np.asarray(
            [atom.position for atom in readers[1].read_structure().atoms]
        )
        assert len(incoming_before) == n_atoms  # defeat the old length-only guard

        cancellations_before = service.cancelled
        ctrl.switch_file(1)
        assert service.cancelled == cancellations_before + 1
        ctrl.set_live_opt_engine("msindo")
        assert len(service.scheduled) == 2
        run_b = service.callbacks[-1]
        status_before = state.live_opt_status

        foreign = [[8.0 + i, 8.0, 8.0] for i in range(n_atoms)]
        run_a["on_step"](
            {
                "step": 99,
                "energy": -1.0,
                "gmax": 0.1,
                "positions": foreign,
            }
        )
        run_a["on_done"](True, 99)
        run_a["on_error"]("stale run A")
        run_a["on_note"]("stale run A note")
        incoming_after = np.asarray(
            [atom.position for atom in readers[1].read_structure().atoms]
        )
        np.testing.assert_allclose(incoming_after, incoming_before)
        assert state.edit_history == []
        assert not readers[1].has_edit_overlay
        assert state.live_opt_status == status_before

        current = [[7.0 + i, 7.0, 7.0] for i in range(n_atoms)]
        run_b["on_step"](
            {
                "step": 1,
                "energy": -2.0,
                "gmax": 0.01,
                "positions": current,
            }
        )
        np.testing.assert_allclose(
            [atom.position for atom in readers[1].read_structure().atoms],
            current,
        )
        assert len(state.edit_history) == 1
        assert readers[1].has_edit_overlay
    finally:
        for reader in readers:
            reader.close()
        for plotter in created:
            plotter.close()


@pytest.mark.parametrize(
    (
        "probe_result",
        "switch_before_release",
        "expected_available",
        "expected_enabled",
        "expected_schedules",
        "expected_status",
    ),
    [
        (
            {"available": True, "engines": ["msindo"]},
            True,
            None,
            True,
            0,
            "Switched to",
        ),
        (
            {"available": False, "engines": [], "reason": "missing worker"},
            True,
            None,
            True,
            0,
            "Switched to",
        ),
        (
            {"available": False, "engines": [], "reason": "missing worker"},
            False,
            False,
            False,
            0,
            "live-opt unavailable: missing worker",
        ),
        (
            {"available": True, "engines": ["msindo"]},
            False,
            True,
            True,
            1,
            "optimizing after edit pause",
        ),
    ],
)
def test_live_opt_probe_respects_reader_epoch_and_unavailable_state(
    showcase_qvf,
    monkeypatch,
    probe_result,
    switch_before_release,
    expected_available,
    expected_enabled,
    expected_schedules,
    expected_status,
):
    """Delayed probe results update capability without crossing reader state."""
    from trame.app import get_server

    import vibeview.app as appmod
    import vibeview.live_opt as lo_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    class FakeService:
        instances: list = []

        def __init__(self, **kwargs):
            self.scheduled: list = []
            self.callbacks: list[dict] = []
            self.cancelled = 0
            FakeService.instances.append(self)

        def schedule(self, request, **callbacks):
            self.scheduled.append(request)
            self.callbacks.append(callbacks)

        def cancel(self):
            self.cancelled += 1

        @property
        def running(self):
            return False

    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    monkeypatch.setattr(lo_mod, "LiveOptService", FakeService)
    monkeypatch.setattr(appmod.pv, "Plotter", create_plotter)

    readers = [QVFReader(showcase_qvf), QVFReader(showcase_qvf)]

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_probe():
            started.set()
            await release.wait()
            return probe_result

        monkeypatch.setattr(lo_mod, "probe_worker", delayed_probe)
        create_app(readers)
        server = get_server(client_type="vue3")
        state, ctrl = server.state, server.controller
        ctrl.view_update = lambda *_args, **_kwargs: None
        service = FakeService.instances[-1]

        state.live_opt_available = None
        state.live_opt_enabled = False
        ctrl.toggle_live_opt(True)
        await started.wait()
        assert service.scheduled == []

        if switch_before_release:
            ctrl.switch_file(1)
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert state.live_opt_available is expected_available
        assert state.live_opt_enabled is expected_enabled
        assert len(service.scheduled) == expected_schedules
        assert expected_status in state.live_opt_status or expected_status in state.status_message
        active_reader = readers[1] if switch_before_release else readers[0]
        assert not active_reader.has_edit_overlay
        assert state.edit_history == []

    try:
        asyncio.run(exercise())
    finally:
        for reader in readers:
            reader.close()
        for plotter in created:
            plotter.close()


def test_stale_reader_probe_cannot_disable_rescheduled_current_run(
    showcase_qvf,
    monkeypatch,
):
    """An unavailable probe from reader A must not disable reader B's run."""
    from trame.app import get_server

    import vibeview.live_opt as lo_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    Service, created = _install_recording_live_opt(monkeypatch)
    readers = [QVFReader(showcase_qvf), QVFReader(showcase_qvf)]

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_probe():
            started.set()
            await release.wait()
            return {"available": False, "engines": [], "reason": "stale A"}

        monkeypatch.setattr(lo_mod, "probe_worker", delayed_probe)
        create_app(readers)
        server = get_server(client_type="vue3")
        state, ctrl = server.state, server.controller
        ctrl.view_update = lambda *_args, **_kwargs: None
        service = Service.instances[-1]

        state.live_opt_available = None
        state.live_opt_enabled = False
        ctrl.toggle_live_opt(True)
        await started.wait()

        ctrl.switch_file(1)
        ctrl.set_live_opt_engine("msindo")
        assert len(service.scheduled) == 1
        run_b = service.callbacks[-1]
        status_before = state.live_opt_status
        cancellations_before = service.cancelled
        incoming_before = np.asarray(
            [atom.position for atom in readers[1].read_structure().atoms]
        )

        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert state.live_opt_available is None
        assert state.live_opt_enabled is True
        assert service.cancelled == cancellations_before
        assert state.live_opt_status == status_before

        current = (incoming_before + 0.25).tolist()
        run_b["on_step"](
            {
                "step": 1,
                "energy": -2.0,
                "gmax": 0.01,
                "positions": current,
            }
        )
        np.testing.assert_allclose(
            [atom.position for atom in readers[1].read_structure().atoms],
            current,
        )
        assert len(state.edit_history) == 1

    try:
        asyncio.run(exercise())
    finally:
        for active_reader in readers:
            active_reader.close()
        for plotter in created:
            plotter.close()


def test_current_unavailable_probe_cancels_interim_run(
    showcase_qvf,
    monkeypatch,
):
    """A current probe failure must disable and invalidate a newer run."""
    from trame.app import get_server

    import vibeview.live_opt as lo_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    Service, created = _install_recording_live_opt(monkeypatch)
    reader = QVFReader(showcase_qvf)

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_probe():
            started.set()
            await release.wait()
            return {"available": False, "engines": [], "reason": "missing worker"}

        monkeypatch.setattr(lo_mod, "probe_worker", delayed_probe)
        create_app(reader)
        server = get_server(client_type="vue3")
        state, ctrl = server.state, server.controller
        ctrl.view_update = lambda *_args, **_kwargs: None
        service = Service.instances[-1]

        state.live_opt_available = None
        state.live_opt_enabled = False
        ctrl.toggle_live_opt(True)
        await started.wait()
        ctrl.set_live_opt_engine("msindo")
        assert len(service.scheduled) == 1
        interim_run = service.callbacks[-1]
        cancellations_before = service.cancelled
        before = np.asarray([atom.position for atom in reader.read_structure().atoms])

        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert state.live_opt_available is False
        assert state.live_opt_enabled is False
        assert service.cancelled == cancellations_before + 1
        assert "live-opt unavailable: missing worker" in state.live_opt_status
        status_before = state.live_opt_status

        interim_run["on_step"](
            {
                "step": 7,
                "energy": -7.0,
                "gmax": 0.7,
                "positions": (before + 7.0).tolist(),
            }
        )
        interim_run["on_done"](True, 7)
        interim_run["on_error"]("late")
        interim_run["on_note"]("late")
        np.testing.assert_allclose(
            [atom.position for atom in reader.read_structure().atoms], before
        )
        assert state.edit_history == []
        assert not reader.has_edit_overlay
        assert state.live_opt_status == status_before

    try:
        asyncio.run(exercise())
    finally:
        reader.close()
        for plotter in created:
            plotter.close()


def test_probe_generation_is_last_writer_wins(showcase_qvf, monkeypatch):
    """Late P1 failure cannot overwrite a newer successful P2 and its run."""
    from trame.app import get_server

    import vibeview.live_opt as lo_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    Service, created = _install_recording_live_opt(monkeypatch)
    reader = QVFReader(showcase_qvf)

    async def exercise() -> None:
        started = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        results = [
            {"available": False, "engines": [], "reason": "older failure"},
            {"available": True, "engines": ["msindo"]},
        ]
        probe_index = 0

        async def delayed_probe():
            nonlocal probe_index
            index = probe_index
            probe_index += 1
            started[index].set()
            await release[index].wait()
            return results[index]

        monkeypatch.setattr(lo_mod, "probe_worker", delayed_probe)
        create_app(reader)
        server = get_server(client_type="vue3")
        state, ctrl = server.state, server.controller
        ctrl.view_update = lambda *_args, **_kwargs: None
        service = Service.instances[-1]

        state.live_opt_available = None
        state.live_opt_enabled = False
        ctrl.toggle_live_opt(True)  # P1
        await started[0].wait()
        ctrl.toggle_live_opt(False)
        ctrl.toggle_live_opt(True)  # P2
        await started[1].wait()

        release[1].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert state.live_opt_available is True
        assert state.live_opt_enabled is True
        assert len(service.scheduled) == 1
        run_p2 = service.callbacks[-1]
        cancellations_before = service.cancelled
        status_before = state.live_opt_status

        release[0].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert state.live_opt_available is True
        assert state.live_opt_enabled is True
        assert len(service.scheduled) == 1
        assert service.cancelled == cancellations_before
        assert state.live_opt_status == status_before

        before = np.asarray([atom.position for atom in reader.read_structure().atoms])
        current = (before + 0.5).tolist()
        run_p2["on_step"](
            {
                "step": 2,
                "energy": -3.0,
                "gmax": 0.01,
                "positions": current,
            }
        )
        np.testing.assert_allclose(
            [atom.position for atom in reader.read_structure().atoms], current
        )

    try:
        asyncio.run(exercise())
    finally:
        reader.close()
        for plotter in created:
            plotter.close()


def test_edit_exit_invalidates_delayed_probe_and_current_run(
    showcase_qvf,
    monkeypatch,
):
    """Closing Edit mode cannot resurrect a probe or accept run callbacks."""
    from trame.app import get_server

    import vibeview.live_opt as lo_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    Service, created = _install_recording_live_opt(monkeypatch)
    reader = QVFReader(showcase_qvf)

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_probe():
            started.set()
            await release.wait()
            return {"available": True, "engines": ["msindo"]}

        monkeypatch.setattr(lo_mod, "probe_worker", delayed_probe)
        create_app(reader)
        server = get_server(client_type="vue3")
        state, ctrl = server.state, server.controller
        ctrl.view_update = lambda *_args, **_kwargs: None
        service = Service.instances[-1]

        state.edit_mode = True
        state.live_opt_available = None
        state.live_opt_enabled = False
        ctrl.toggle_live_opt(True)
        await started.wait()
        cancellations_before = service.cancelled
        ctrl.toggle_edit_mode()
        assert state.edit_mode is False
        assert state.live_opt_enabled is False
        assert service.cancelled == cancellations_before + 1

        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert state.live_opt_available is None
        assert state.live_opt_enabled is False
        assert service.scheduled == []

        # Repeat with an already scheduled run: every callback saved before
        # the mode boundary must be inert afterward.
        state.edit_mode = True
        state.live_opt_available = True
        ctrl.toggle_live_opt(True)
        run = service.callbacks[-1]
        before = np.asarray([atom.position for atom in reader.read_structure().atoms])
        ctrl.toggle_edit_mode()
        status_before = state.live_opt_status
        foreign = (before + 5.0).tolist()
        run["on_step"](
            {
                "step": 9,
                "energy": -9.0,
                "gmax": 0.9,
                "positions": foreign,
            }
        )
        run["on_done"](True, 9)
        run["on_error"]("late")
        run["on_note"]("late")
        np.testing.assert_allclose(
            [atom.position for atom in reader.read_structure().atoms], before
        )
        assert state.edit_history == []
        assert not reader.has_edit_overlay
        assert state.live_opt_status == status_before

    try:
        asyncio.run(exercise())
    finally:
        reader.close()
        for plotter in created:
            plotter.close()


@pytest.mark.parametrize("boundary", ["undo", "redo", "symmetrize"])
def test_cancel_only_geometry_boundary_invalidates_delayed_probe(
    showcase_qvf,
    monkeypatch,
    boundary,
):
    """A no-reschedule edit boundary must also retire its pending probe."""
    import types

    from trame.app import get_server

    import vibeview.live_opt as lo_mod
    import vibeview.symmetry as symmetry_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    Service, created = _install_recording_live_opt(monkeypatch)
    reader = QVFReader(showcase_qvf)

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_probe():
            started.set()
            await release.wait()
            return {"available": True, "engines": ["msindo"]}

        monkeypatch.setattr(lo_mod, "probe_worker", delayed_probe)
        create_app(reader)
        server = get_server(client_type="vue3")
        state, ctrl = server.state, server.controller
        ctrl.view_update = lambda *_args, **_kwargs: None
        service = Service.instances[-1]

        if boundary in {"undo", "redo"}:
            state.edit_selected = [0]
            ctrl.edit_change_element("C")
            if boundary == "redo":
                ctrl.edit_undo()
        else:
            structure = reader.read_structure()
            monkeypatch.setattr(
                symmetry_mod,
                "symmetrize_to_group",
                lambda *_args, **_kwargs: types.SimpleNamespace(
                    ok=True,
                    positions=np.asarray(
                        [atom.position for atom in structure.atoms], dtype=float
                    ),
                    symbol_before="C1",
                    symbol_after="C1",
                    n_operations=1,
                    max_shift=0.0,
                ),
            )

        state.live_opt_available = None
        state.live_opt_enabled = False
        ctrl.toggle_live_opt(True)
        await started.wait()
        cancellations_before = service.cancelled

        if boundary == "undo":
            ctrl.edit_undo()
        elif boundary == "redo":
            ctrl.edit_redo()
        else:
            ctrl.edit_symmetrize()
        assert service.cancelled == cancellations_before + 1

        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert state.live_opt_available is None
        assert state.live_opt_enabled is True
        assert service.scheduled == []

    try:
        asyncio.run(exercise())
    finally:
        reader.close()
        for plotter in created:
            plotter.close()


def test_all_frozen_edit_cancels_and_invalidates_prior_run(
    showcase_qvf,
    monkeypatch,
):
    """An ineligible same-reader edit must cancel before its refusal guard."""
    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    Service, created = _install_recording_live_opt(monkeypatch)
    reader = QVFReader(showcase_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        state, ctrl = server.state, server.controller
        ctrl.view_update = lambda *_args, **_kwargs: None
        service = Service.instances[-1]

        state.live_opt_available = True
        state.live_opt_enabled = False
        ctrl.toggle_live_opt(True)
        assert len(service.scheduled) == 1
        prior_run = service.callbacks[-1]
        before = np.asarray([atom.position for atom in reader.read_structure().atoms])

        cancellations_before = service.cancelled
        state.edit_selected = list(range(len(before)))
        ctrl.edit_freeze_selected()
        assert service.cancelled == cancellations_before + 1
        assert len(service.scheduled) == 1
        assert "all atoms frozen" in state.live_opt_status
        status_before = state.live_opt_status

        foreign = (before + 4.0).tolist()
        prior_run["on_step"](
            {
                "step": 4,
                "energy": -4.0,
                "gmax": 0.4,
                "positions": foreign,
            }
        )
        prior_run["on_done"](True, 4)
        prior_run["on_error"]("late")
        prior_run["on_note"]("late")
        np.testing.assert_allclose(
            [atom.position for atom in reader.read_structure().atoms], before
        )
        assert state.edit_history == []
        assert not reader.has_edit_overlay
        assert state.live_opt_status == status_before
    finally:
        reader.close()
        for plotter in created:
            plotter.close()


def test_undo_and_redo_invalidate_same_reader_run_callbacks(
    showcase_qvf,
    monkeypatch,
):
    """History restoration is a run boundary even when atom count is equal."""
    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    Service, created = _install_recording_live_opt(monkeypatch)
    reader = QVFReader(showcase_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        state, ctrl = server.state, server.controller
        ctrl.view_update = lambda *_args, **_kwargs: None
        service = Service.instances[-1]

        state.live_opt_available = True
        state.live_opt_enabled = False
        state.edit_selected = [0]
        ctrl.toggle_live_opt(True)
        ctrl.edit_change_element("C")
        edited_run = service.callbacks[-1]

        ctrl.edit_undo()
        undo_positions = np.asarray(
            [atom.position for atom in reader.read_structure().atoms]
        )
        undo_symbols = [atom.symbol for atom in reader.read_structure().atoms]
        undo_history = list(state.edit_history)
        undo_status = state.live_opt_status
        edited_run["on_step"](
            {
                "step": 5,
                "energy": -5.0,
                "gmax": 0.5,
                "positions": (undo_positions + 5.0).tolist(),
            }
        )
        edited_run["on_done"](True, 5)
        edited_run["on_error"]("late undo")
        edited_run["on_note"]("late undo")
        np.testing.assert_allclose(
            [atom.position for atom in reader.read_structure().atoms],
            undo_positions,
        )
        assert [atom.symbol for atom in reader.read_structure().atoms] == undo_symbols
        assert state.edit_history == undo_history
        assert state.live_opt_status == undo_status

        ctrl.set_live_opt_engine("msindo")
        undo_run = service.callbacks[-1]
        ctrl.edit_redo()
        redo_positions = np.asarray(
            [atom.position for atom in reader.read_structure().atoms]
        )
        redo_symbols = [atom.symbol for atom in reader.read_structure().atoms]
        redo_history = list(state.edit_history)
        redo_status = state.live_opt_status
        undo_run["on_step"](
            {
                "step": 6,
                "energy": -6.0,
                "gmax": 0.6,
                "positions": (redo_positions + 6.0).tolist(),
            }
        )
        undo_run["on_done"](True, 6)
        undo_run["on_error"]("late redo")
        undo_run["on_note"]("late redo")
        np.testing.assert_allclose(
            [atom.position for atom in reader.read_structure().atoms],
            redo_positions,
        )
        assert [atom.symbol for atom in reader.read_structure().atoms] == redo_symbols
        assert state.edit_history == redo_history
        assert state.live_opt_status == redo_status
    finally:
        reader.close()
        for plotter in created:
            plotter.close()


def test_smiles_builder_schedules_first_relax_before_availability_probe(
    showcase_qvf,
    monkeypatch,
):
    """The builder's required first relaxation works from fresh app state."""
    from trame.app import get_server

    import vibeview.app as appmod
    import vibeview.converters as converters
    import vibeview.live_opt as lo_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    class FakeService:
        instances: list = []

        def __init__(self, **kwargs):
            self.scheduled: list = []
            FakeService.instances.append(self)

        def schedule(self, request, **_callbacks):
            self.scheduled.append(request)

        def cancel(self):
            pass

        @property
        def running(self):
            return False

    created = []
    plotter_type = appmod.pv.Plotter

    def create_plotter(*args, **kwargs):
        plotter = plotter_type(*args, **kwargs)
        created.append(plotter)
        return plotter

    monkeypatch.setattr(lo_mod, "LiveOptService", FakeService)
    monkeypatch.setattr(appmod.pv, "Plotter", create_plotter)
    # The controller contract, not RDKit embedding, is under test here.
    monkeypatch.setattr(converters, "smiles_to_qvf", lambda _smiles: showcase_qvf)

    reader = QVFReader(showcase_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        state, ctrl = server.state, server.controller
        ctrl.view_update = lambda *_args, **_kwargs: None
        service = FakeService.instances[-1]

        assert state.live_opt_available is None
        state.builder_smiles = "CCO"
        ctrl.build_from_smiles()

        assert state.live_opt_enabled is True
        assert len(service.scheduled) == 1
        assert service.scheduled[0]["symbols"]
        assert "relaxing with vibe-qc" in state.status_message
    finally:
        reader.close()
        for plotter in created:
            plotter.close()


def test_toggle_and_edit_hooks_drive_service(showcase_qvf, monkeypatch):
    from trame.app import get_server

    import vibeview.live_opt as lo_mod
    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    class FakeService:
        instances: list = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.scheduled: list = []
            self.callbacks: list[dict] = []
            self.cancelled = 0
            FakeService.instances.append(self)

        def schedule(self, request, **callbacks):
            self.scheduled.append(request)
            self.callbacks.append(callbacks)

        def cancel(self):
            self.cancelled += 1

        @property
        def running(self):
            return False

    monkeypatch.setattr(lo_mod, "LiveOptService", FakeService)

    reader = QVFReader(showcase_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        ctrl = server.controller
        service = FakeService.instances[-1]

        n0 = len(reader.read_structure().atoms)
        state = server.state
        state.edit_mode = True
        state.edit_selected = []
        state.edit_history = []
        state.edit_future = []
        state.edit_new_element = "C"
        state.live_opt_available = True  # skip the probe subprocess
        state.live_opt_enabled = False

        # Enabling the switch relaxes the current sketch right away.
        ctrl.toggle_live_opt(True)
        assert state.live_opt_enabled is True
        assert len(service.scheduled) == 1
        assert len(service.scheduled[0]["symbols"]) == n0
        assert service.scheduled[0]["method"] == "msindo"  # default engine

        # An edit triggers a re-schedule with the edited geometry.
        ctrl.on_pick({"worldPosition": [50.0, 50.0, 50.0]})
        assert len(service.scheduled) == 2
        assert len(service.scheduled[1]["symbols"]) == n0 + 1

        # A step event applies the relaxed geometry (visible via the edit
        # overlay) and pushes exactly one undo entry per relax run.
        undo_before = len(state.edit_history)
        run = service.callbacks[-1]
        on_step = run["on_step"]
        relaxed = [[float(i), 0.0, 0.0] for i in range(n0 + 1)]
        on_step({"step": 1, "energy": -1.0, "gmax": 0.05, "positions": relaxed})
        on_step({"step": 2, "energy": -1.1, "gmax": 0.01, "positions": relaxed})
        assert len(state.edit_history) == undo_before + 1  # one entry, not two
        got = [a.position.tolist() for a in reader.read_structure().atoms]
        assert got == relaxed
        assert "step 2" in state.live_opt_status

        # done / error callbacks surface in the status line.
        run["on_done"](True, 7)
        assert "7 steps" in state.live_opt_status
        run["on_error"]("boom")
        assert "boom" in state.live_opt_status

        # Switching off cancels and clears the status.
        ctrl.toggle_live_opt(False)
        assert service.cancelled >= 1
        assert state.live_opt_status == ""

        # A stale event (atom-count mismatch) must be ignored.
        before = [a.position.tolist() for a in reader.read_structure().atoms]
        on_step({"step": 3, "energy": -1.0, "gmax": 0.1, "positions": [[9.0, 9.0, 9.0]]})
        after = [a.position.tolist() for a in reader.read_structure().atoms]
        assert after == before

        # Switching engine while enabled re-schedules with the new engine.
        ctrl.toggle_live_opt(True)
        n_before = len(service.scheduled)
        ctrl.set_live_opt_engine("mace")
        assert len(service.scheduled) == n_before + 1
        assert service.scheduled[-1]["method"] == "mace"

        # With live-opt unavailable, the switch snaps back off.
        ctrl.toggle_live_opt(False)
        state.live_opt_available = False
        ctrl.toggle_live_opt(True)
        assert state.live_opt_enabled is False
    finally:
        reader.close()
