"""Live geometry optimization while building (Avogadro-parity M3, B1).

A debounced background service that, when the editor pauses, relaxes the
current structure with a vibe-qc engine and streams each optimizer step
back so the viewer eases atoms toward the relaxed geometry.

Two engines, both provided by vibe-qc (locked decision 2 — dogfood
vibe-qc, no new hard dependency):

* ``"msindo"`` (default) — semi-empirical MSINDO via
  ``vibeqc.semiempirical.methods.msindo``. The 2026-07-08 survey (see
  ROADMAP_AVOGADRO_PARITY §4) picked it as the only molecular
  semi-empirical model in vibe-qc with an *analytic* nuclear gradient
  (``msindo_gradient_analytic``; benzene energy+gradient ≈ 110 ms), a
  correct PES minimum (water r_OH ≈ 0.96 Å), and Z = 1–54 (H–Xe)
  coverage. Rejected alternatives, as surveyed then: SCC-DFTB's default
  parameter set had **no repulsive wall** (water dissociated downhill)
  and its analytic gradient disagreed with finite differences — both
  root-caused and fixed on main 2026-07-09 (wrong-sign SCC screening
  plus core electrons counted into the valence band; see CHANGELOG
  [Unreleased]. DFTB remains a screening-grade in-house parameter set,
  so MSINDO stays the live-opt engine); GFN2/PM6 have correct minima
  but only finite-difference gradients (6N SCFs per step).
  Needs only ``vibeqc`` (which brings scipy) — no ASE.
* ``"mace"`` — the MACE pre-trained MLIP via ``vibeqc.mlip`` (the
  MLIP exception to vibe-qc's own-implementation rule),
  offered when the ``[mace]`` stack (torch + mace + ase) is importable
  next to vibeqc. Uses the default **MIT-licensed MACE-MPA-0** model on
  CPU — the ASL-gated academic models are deliberately not selectable
  here (no acknowledgment plumbing in a builder aid). Weights are
  fetched on first use; the worker emits a ``note`` event so the UI can
  say so. Attribution: the energy shown is the MACE model's
  reference-shifted DFT-surface value, not a vibe-qc total energy — the
  status line and note name the model. NOTE: MACE caps at Python ≤ 3.13
  today (matscipy ships no 3.14 wheel), so on a 3.14 venv the probe
  simply doesn't offer it.

Both engines drive the same scipy L-BFGS-B loop (mirroring vibe-qc's
``msindo_optimize``, same gtol convention) with a per-iteration
streaming callback.

Design (roadmap §4, locked decision 2):

* The optimization runs in a **subprocess** (``python -m vibeview.live_opt``)
  so neither vibe-qc's native core nor torch is ever imported into the
  viewer process, and a hung/slow evaluation can simply be killed
  (cancel-on-edit).
* When vibe-qc is missing entirely, :func:`probe` reports why, the
  viewer's switch snaps back off with that reason, and the viewer works
  exactly as before — vibe-view must stay fully usable without vibe-qc.
* Wire protocol: one JSON request on the worker's stdin, one JSON event
  per line on stdout —
  ``{"step": k, "energy": E_hartree, "gmax": g_ha_bohr, "positions": [[x,y,z], ...]}``
  per optimizer iteration (positions in angstroms),
  ``{"note": "..."}`` for progress that is not a geometry step (model
  loading / weight download),
  ``{"done": true, "converged": bool, "steps": k}`` on completion, or
  ``{"error": "..."}`` on failure. Step events are rate-limited
  worker-side so a fast small-molecule evaluation cannot flood the
  viewer.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Callable

# Viewer-side defaults (also the worker's fallbacks).
DEFAULT_ENGINE = "msindo"
ENGINES = ("msindo", "mace")  # everything build_request accepts
DEFAULT_FMAX = 2e-3  # Ha/bohr — matches vibeqc's msindo_optimize default
DEFAULT_MAX_STEPS = 60
DEBOUNCE_S = 0.8  # edit-pause before an optimization starts
MAX_LIVE_OPT_ATOMS = 80  # keep the per-step evaluation interactive
_EMIT_MIN_INTERVAL_S = 0.15  # worker-side step-event rate limit
# Hartree/eV and bohr/angstrom (CODATA); used for the MACE (ASE-units)
# engine so the shared loop always works in Ha + angstrom.
_HARTREE_EV = 27.211386245988
_BOHR_PER_ANGSTROM = 1.8897259886


def build_request(
    symbols: list[str],
    positions: list[list[float]],
    *,
    charge: int = 0,
    fmax: float = DEFAULT_FMAX,
    max_steps: int = DEFAULT_MAX_STEPS,
    frozen: list[int] | None = None,
    engine: str = DEFAULT_ENGINE,
) -> dict[str, Any]:
    """Assemble the worker request. Positions in angstroms."""
    if engine not in ENGINES:
        raise ValueError(f"unknown live-opt engine {engine!r}; expected one of {ENGINES}")
    return {
        "symbols": list(symbols),
        "positions": [[float(c) for c in p] for p in positions],
        "charge": int(charge),
        "fmax": float(fmax),
        "max_steps": int(max_steps),
        "frozen": sorted(int(i) for i in (frozen or [])),
        "method": engine,
    }


def parse_event(line: str | bytes) -> dict[str, Any] | None:
    """Decode one worker stdout line; None for blanks / non-JSON noise."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    line = line.strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


class LiveOptService:
    """Debounced, cancellable live-optimization driver (viewer side).

    ``schedule()`` is called after an editor operation; it cancels any
    pending debounce *and* any running worker, then starts a fresh
    debounce. When the debounce expires the requested geometry goes to
    the worker subprocess, which streams relaxed steps to ``on_step``;
    ``on_done(converged, steps)`` / ``on_error(msg)`` fire at the end.
    Everything runs on the asyncio event loop — callbacks are safe to
    touch trame state / the plotter.
    """

    def __init__(
        self,
        *,
        on_step: Callable[[dict[str, Any]], None],
        on_done: Callable[[bool, int], None],
        on_error: Callable[[str], None],
        on_note: Callable[[str], None] | None = None,
        debounce_s: float = DEBOUNCE_S,
        # Hang guard only (cancel-on-edit handles the common case); generous
        # enough for a first-use MACE weight download.
        timeout_s: float = 600.0,
        worker_cmd: list[str] | None = None,
    ) -> None:
        self._on_step = on_step
        self._on_done = on_done
        self._on_error = on_error
        self._on_note = on_note
        self._debounce_s = debounce_s
        self._timeout_s = timeout_s
        # Injectable for tests (a canned-JSON fake worker script).
        self._worker_cmd = worker_cmd or _default_worker_cmd()
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def schedule(
        self,
        request: dict[str, Any],
        *,
        on_step: Callable[[dict[str, Any]], None] | None = None,
        on_done: Callable[[bool, int], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_note: Callable[[str], None] | None = None,
    ) -> None:
        """Debounce-start an optimization of ``request`` (cancel-on-edit).

        Callbacks may be supplied per schedule.  The viewer uses that surface
        to bind every callback to one immutable run token; constructor-level
        callbacks remain the default for standalone callers.
        """
        self.cancel()
        step_callback = self._on_step if on_step is None else on_step
        done_callback = self._on_done if on_done is None else on_done
        error_callback = self._on_error if on_error is None else on_error
        note_callback = self._on_note if on_note is None else on_note
        coro = self._debounced_run(
            request,
            step_callback,
            done_callback,
            error_callback,
            note_callback,
        )
        try:
            self._task = asyncio.ensure_future(coro)
        except RuntimeError as exc:  # no running event loop (headless misuse)
            coro.close()
            error_callback(f"live-opt needs a running event loop: {exc}")

    def cancel(self) -> None:
        """Cancel the pending debounce and kill a running worker."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        self._kill_proc()

    def _kill_proc(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def _debounced_run(
        self,
        request: dict[str, Any],
        on_step: Callable[[dict[str, Any]], None],
        on_done: Callable[[bool, int], None],
        on_error: Callable[[str], None],
        on_note: Callable[[str], None] | None,
    ) -> None:
        try:
            await asyncio.sleep(self._debounce_s)
            await self._run(request, on_step, on_done, on_error, on_note)
        except asyncio.CancelledError:
            self._kill_proc()
            raise

    async def _run(
        self,
        request: dict[str, Any],
        on_step: Callable[[dict[str, Any]], None],
        on_done: Callable[[bool, int], None],
        on_error: Callable[[str], None],
        on_note: Callable[[str], None] | None,
    ) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._worker_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            on_error(f"could not start worker: {exc}")
            return
        self._proc = proc

        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps(request).encode() + b"\n")
        proc.stdin.write_eof()

        finished = False
        try:
            async with asyncio.timeout(self._timeout_s):
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    event = parse_event(line)
                    if event is None:
                        continue
                    if "error" in event:
                        on_error(str(event["error"]))
                        finished = True
                        break
                    if "note" in event:
                        if on_note is not None:
                            on_note(str(event["note"]))
                        continue
                    if event.get("done"):
                        on_done(
                            bool(event.get("converged")), int(event.get("steps", 0))
                        )
                        finished = True
                        break
                    on_step(event)
        except TimeoutError:
            on_error(f"optimization timed out after {self._timeout_s:.0f}s")
            finished = True
        finally:
            self._kill_proc()
        if not finished:
            on_error("optimization worker exited without a result")


def _default_worker_cmd() -> list[str]:
    return [sys.executable, "-m", "vibeview.live_opt"]


async def probe_worker(timeout_s: float = 60.0) -> dict[str, Any]:
    """Run the worker's ``--probe`` in a subprocess (async, viewer side).

    Never raises: any failure comes back as ``{"available": False,
    "reason": ...}``. The generous timeout covers a cold vibe-qc import.
    """
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *_default_worker_cmd(),
            "--probe",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async with asyncio.timeout(timeout_s):
            out, _ = await proc.communicate()
    except Exception as exc:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        return {"available": False, "reason": f"probe failed: {exc}"}
    for line in out.splitlines():
        event = parse_event(line)
        if event is not None and "available" in event:
            return event
    return {"available": False, "reason": "probe produced no result"}


# ── Worker (subprocess) side ──────────────────────────────────────────────


def probe() -> dict[str, Any]:
    """Report whether this interpreter can run a live optimization, and
    with which engines. ``engines`` lists what :func:`build_request` may
    ask for: ``msindo`` needs vibe-qc; ``mace`` additionally needs the
    ``[mace]`` stack (torch + mace + ase) — availability is checked by
    ``find_spec`` only, so probing never loads torch or downloads weights.
    """
    try:
        from vibeqc.semiempirical.methods.msindo import run_msindo  # noqa: F401
    except Exception as exc:
        return {
            "available": False,
            "reason": f"vibe-qc not importable: {exc}",
            "engines": [],
        }
    engines = ["msindo"]
    import importlib.util

    if all(
        importlib.util.find_spec(mod) is not None for mod in ("mace", "torch", "ase")
    ):
        engines.append("mace")
    return {"available": True, "reason": "", "engines": engines}


def _msindo_engine(numbers: list[int], charge: int, emit: Callable[[dict], None]):
    """Return ``fun(coords_A) -> (E_hartree, grad_ha_bohr[n,3])`` on the
    MSINDO surface (analytic gradient)."""
    import numpy as np

    from vibeqc.semiempirical.methods.msindo import eff_core_charge, run_msindo
    from vibeqc.semiempirical.methods.msindo_gradient_analytic import (
        msindo_gradient_analytic,
    )

    # Closed shell when the valence-electron count is even, doublet
    # otherwise (MSINDO counts effective core charges, not full Z).
    n_electrons = sum(eff_core_charge(z) for z in numbers) - charge
    multiplicity = 1 if n_electrons % 2 == 0 else 2

    def fun(coords_a):
        result = run_msindo(numbers, coords_a, charge=charge, multiplicity=multiplicity)
        grad = msindo_gradient_analytic(
            numbers, coords_a, charge=charge, multiplicity=multiplicity
        )
        return float(result.total_energy), np.asarray(grad, dtype=float)

    return fun


def _mace_engine(numbers: list[int], charge: int, emit: Callable[[dict], None]):
    """Return ``fun(coords_A) -> (E_hartree, grad_ha_bohr[n,3])`` on the
    default MIT-licensed MACE model (vibeqc.mlip; vibe-qc's MLIP
    exception). The energy is the model's reference-shifted DFT-surface
    value — the note event names the model for attribution. ASL-gated
    academic models are deliberately not reachable from live-opt.
    """
    import numpy as np

    from vibeqc.mlip import MLIPOptions
    from vibeqc.mlip._mace_models import resolve_model
    from vibeqc.mlip.mace import mace_calculator

    options = MLIPOptions()  # default: MIT MACE-MPA-0, CPU, float64
    info = resolve_model(options.model)
    bad = info.unsupported_elements(numbers)
    if bad:
        raise ValueError(
            f"MACE model {info.key!r} does not cover element(s) Z={sorted(bad)}"
        )
    emit(
        {
            "note": (
                f"loading MACE model {info.key!r} "
                "(first use may download weights) …"
            )
        }
    )
    calc = mace_calculator(options)
    from ase import Atoms

    atoms = Atoms(numbers=numbers, positions=[[0.0, 0.0, 0.0]] * len(numbers))
    atoms.calc = calc

    def fun(coords_a):
        atoms.positions = np.asarray(coords_a, dtype=float)
        e_ha = float(atoms.get_potential_energy()) / _HARTREE_EV
        forces = np.asarray(atoms.get_forces(), dtype=float)  # eV/A
        # g[Ha/bohr] = -F[eV/A] / (Hartree[eV/Ha] * bohr_per_A[bohr/A])
        grad = -forces / (_HARTREE_EV * _BOHR_PER_ANGSTROM)
        return e_ha, grad

    return fun


_ENGINE_BUILDERS = {"msindo": _msindo_engine, "mace": _mace_engine}


def _run_optimization(request: dict[str, Any], emit: Callable[[dict], None]) -> None:
    """Optimize the requested geometry, emitting one event per iteration.

    Mirrors ``vibeqc.semiempirical.methods.msindo.msindo_optimize``
    (L-BFGS-B, same gtol convention) over the engine selected by the
    request's ``method`` field, streaming every accepted iterate. Runs
    inside the worker subprocess; exceptions propagate to main().
    """
    import time
    import warnings

    import numpy as np
    from scipy.optimize import minimize

    from vibeview.converters import _SYMBOL_TO_Z

    warnings.filterwarnings("ignore")  # stdout is a JSON protocol — keep it clean

    symbols = [str(s) for s in request["symbols"]]
    positions = np.asarray(request["positions"], dtype=float)
    charge = int(request.get("charge", 0))
    fmax = float(request.get("fmax", DEFAULT_FMAX))
    max_steps = int(request.get("max_steps", DEFAULT_MAX_STEPS))
    frozen = set(int(i) for i in request.get("frozen", []))
    engine = str(request.get("method", DEFAULT_ENGINE))

    builder = _ENGINE_BUILDERS.get(engine)
    if builder is None:
        raise ValueError(f"unknown live-opt engine {engine!r}; expected one of {ENGINES}")

    numbers = [_SYMBOL_TO_Z.get(s, 0) for s in symbols]
    if 0 in numbers:
        unknown = sorted({s for s, z in zip(symbols, numbers) if z == 0})
        raise ValueError(f"unknown element symbol(s): {', '.join(unknown)}")

    free = [i for i in range(len(numbers)) if i not in frozen]
    if not free:
        raise ValueError("all atoms frozen — nothing to optimize")

    energy_and_gradient = builder(numbers, charge, emit)

    def _coords(x: np.ndarray) -> np.ndarray:
        c = positions.copy()
        c[free] = x.reshape(-1, 3)
        return c

    state: dict[str, Any] = {"energy": 0.0, "gmax": 0.0, "steps": 0, "last_emit": 0.0}

    def fun(x: np.ndarray) -> tuple[float, np.ndarray]:
        energy, grad = energy_and_gradient(_coords(x))
        state["energy"] = energy
        state["gmax"] = float(np.abs(grad[free]).max())
        # scipy works in angstrom -> return Ha/angstrom (grad is Ha/bohr).
        return energy, (grad[free] * _BOHR_PER_ANGSTROM).ravel()

    def _on_iterate(xk: np.ndarray) -> None:
        state["steps"] += 1
        now = time.monotonic()
        if now - state["last_emit"] < _EMIT_MIN_INTERVAL_S:
            return  # rate-limit: a fast evaluation must not flood the viewer
        state["last_emit"] = now
        emit(
            {
                "step": state["steps"],
                "energy": state["energy"],
                "gmax": state["gmax"],
                "positions": _coords(xk).tolist(),
            }
        )

    res = minimize(
        fun,
        positions[free].ravel(),
        jac=True,
        method="L-BFGS-B",
        callback=_on_iterate,
        options={"gtol": fmax * _BOHR_PER_ANGSTROM, "maxiter": max_steps},
    )

    final = _coords(res.x)
    emit(
        {
            "step": state["steps"],
            "energy": state["energy"],
            "gmax": state["gmax"],
            "positions": final.tolist(),
        }
    )
    emit({"done": True, "converged": bool(res.success), "steps": state["steps"]})


def main(argv: list[str] | None = None) -> int:
    """Worker entry: ``python -m vibeview.live_opt [--probe]``."""
    argv = sys.argv[1:] if argv is None else argv

    def emit(event: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()

    if "--probe" in argv:
        emit(probe())
        return 0

    try:
        request = json.loads(sys.stdin.read())
        _run_optimization(request, emit)
    except Exception as exc:  # anything: the viewer needs one parseable line
        emit({"error": f"{type(exc).__name__}: {exc}"})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
