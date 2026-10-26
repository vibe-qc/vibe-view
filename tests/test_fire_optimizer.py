"""FIRE relaxation contract, convergence, frozen atoms and failure reporting."""

import numpy as np
import pytest

from vibeview import live_opt as lo
from vibeview.units import ANGSTROM_TO_BOHR


def test_fire_request_defaults_and_validation():
    request = lo.build_request(["H"], [[0, 0, 0]], optimizer="fire")
    assert request["optimizer"] == "fire"
    assert request["max_steps"] == 200
    assert lo.build_request(["H"], [[0, 0, 0]])["max_steps"] == 60
    with pytest.raises(ValueError, match="unknown optimizer"):
        lo.build_request(["H"], [[0, 0, 0]], optimizer="bad")


@pytest.mark.parametrize("steps,converged", [(0, False), (1, False), (200, True)])
def test_fire_convergence_step_limit_units_and_frozen_atoms(monkeypatch, steps, converged):
    def builder(numbers, charge, emit):
        def energy_gradient(coords):
            # An anisotropic harmonic potential in Ha, gradients in Ha/bohr.
            weights = np.array([1.0, 2.0, 3.0])
            return float((weights * coords**2).sum() / 2), weights * coords / ANGSTROM_TO_BOHR
        return energy_gradient

    monkeypatch.setitem(lo._ENGINE_BUILDERS, "msindo", builder)
    monkeypatch.setattr(lo, "_EMIT_MIN_INTERVAL_S", 0)
    request = lo.build_request(
        ["H", "H"], [[0.8, 0.2, -0.1], [1.5, -0.7, 0.3]],
        optimizer="fire", frozen=[0], max_steps=steps, fmax=1e-5,
    )
    events = []
    lo._run_optimization(request, events.append)
    assert events[-1]["converged"] is converged
    assert events[-1]["steps"] <= steps
    previous = np.array(request["positions"])
    for event in events:
        if "positions" not in event:
            continue
        coords = np.array(event["positions"])
        np.testing.assert_array_equal(coords[0], previous[0])
        assert np.linalg.norm(coords[1] - previous[1]) <= 0.10000000001
        expected_energy, grad = builder(None, None, None)(coords)
        assert event["energy"] == pytest.approx(expected_energy)
        assert event["gmax"] == pytest.approx(np.abs(grad[1]).max())
        previous = coords
    if converged:
        assert events[-2]["gmax"] <= 1e-5


def test_fire_already_relaxed_does_not_move(monkeypatch):
    monkeypatch.setitem(lo._ENGINE_BUILDERS, "msindo", lambda *args: (
        lambda coords: (0.0, np.zeros_like(coords))
    ))
    events = []
    lo._run_optimization(lo.build_request(["H"], [[0, 0, 0]], optimizer="fire"), events.append)
    assert events[-1] == {"done": True, "converged": True, "steps": 0}


def test_fire_rejects_nonfinite_forces(monkeypatch):
    monkeypatch.setitem(lo._ENGINE_BUILDERS, "msindo", lambda *args: (
        lambda coords: (0.0, np.full_like(coords, np.nan))
    ))
    events = []
    with pytest.raises(ValueError, match="invalid energy or gradient"):
        lo._run_optimization(lo.build_request(["H"], [[0, 0, 0]], optimizer="fire"), events.append)
    assert events == []
