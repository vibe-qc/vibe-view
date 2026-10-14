"""MO playback terminates and keeps scheduled VTK/state work on its event loop."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from vibeview import app as appmod
from vibeview.qvf import QVFReader


@pytest.fixture
def animation_app(tables_qvf, monkeypatch):
    reader = QVFReader(tables_qvf)
    plotters = []
    real_plotter = appmod.pv.Plotter

    def capture_plotter(*args, **kwargs):
        plotter = real_plotter(*args, **kwargs)
        plotters.append(plotter)
        return plotter

    monkeypatch.setattr(appmod.pv, "Plotter", capture_plotter)
    app = appmod.create_app(reader)
    app.controller.activate_section("wf")
    app.controller.view_update = lambda *args, **kwargs: None
    app.state.wf_mo_rows = [{"value": f"restricted:{i}"} for i in range(3)]
    app.state.wf_selected_mo = "restricted:0"
    app.state.wf_anim_speed = 0.01
    calls = []
    flushes = []

    def render(*args, **kwargs):
        calls.append((threading.get_ident(), args[5]))
        return "frame"

    monkeypatch.setattr(appmod._helpers, "_render_mo_volume", render)
    monkeypatch.setattr(
        appmod._helpers, "_finalize_wavefunction_surface_appearance", lambda *a: None
    )
    monkeypatch.setattr(
        type(app.state), "flush", lambda state: flushes.append(threading.get_ident())
    )
    try:
        yield app, calls, flushes
    finally:
        if app.state.wf_animating:
            app.controller.toggle_mo_animation()
        for plotter in plotters:
            plotter.close()
        reader.close()


@pytest.mark.parametrize("contract", ["termination", "thread", "flush"])
def test_scheduled_frames_obey_playback_contract(animation_app, contract):
    app, calls, flushes = animation_app
    owner = threading.get_ident()

    async def scenario():
        app.controller.toggle_mo_animation()
        await asyncio.sleep(0.12)
        if contract == "termination":
            assert app.state.wf_animating is False
            assert [index for _, index in calls] == [1, 2]
            assert app.state.wf_selected_mo == "restricted:2"
        elif contract == "thread":
            assert len(calls) >= 2
            assert all(thread == owner for thread, _ in calls)
        else:
            assert len(flushes) >= 2
            assert all(thread == owner for thread in flushes)

    asyncio.run(scenario())


def test_manual_step_still_clamps_at_either_end():
    state = SimpleNamespace(wf_mo_rows=[{"value": "alpha:0"}, {"value": "beta:1"}],
                            wf_selected_mo="alpha:0")
    assert appmod._step_mo_selection(state, -1) == "alpha:0"
    assert appmod._step_mo_selection(state, 1) == "beta:1"
    assert appmod._step_mo_selection(state, 1) == "beta:1"


def test_starting_at_final_orbital_renders_once(animation_app):
    app, calls, flushes = animation_app
    app.state.wf_selected_mo = "restricted:2"
    async def scenario():
        app.controller.toggle_mo_animation()
        await asyncio.sleep(0.04)
        assert app.state.wf_animating is False
        assert [index for _, index in calls] == [2]
        assert flushes
    asyncio.run(scenario())


def test_section_switch_cancels_pending_frame(animation_app):
    app, calls, _ = animation_app
    app.state.wf_anim_speed = 0.05
    async def scenario():
        app.controller.toggle_mo_animation()
        assert len(calls) == 1
        app.controller.activate_section("structure")
        await asyncio.sleep(0.1)
        assert len(calls) == 1
        assert app.state.wf_animating is False
    asyncio.run(scenario())


def test_missing_event_loop_stops_with_truthful_status(animation_app):
    app, calls, flushes = animation_app
    app.controller.toggle_mo_animation()
    assert [index for _, index in calls] == [1]
    assert app.state.wf_animating is False
    assert "no running event loop" in app.state.status_message
    assert flushes


def test_empty_picker_stops_and_flushes(animation_app):
    app, calls, flushes = animation_app
    app.state.wf_mo_rows = []
    app.controller.toggle_mo_animation()
    assert app.state.wf_animating is False
    assert app.state.status_message == "MO animation stopped: no orbitals"
    assert calls == []
    assert flushes


def test_render_failure_stops_and_flushes(animation_app, monkeypatch):
    app, _, flushes = animation_app

    def fail(*args, **kwargs):
        raise ValueError("orbital evaluation failed")

    monkeypatch.setattr(appmod._helpers, "_render_mo_volume", fail)
    app.controller.toggle_mo_animation()
    assert app.state.wf_animating is False
    assert "orbital evaluation failed" in app.state.status_message
    assert flushes
