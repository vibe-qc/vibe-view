"""Which vq program and runtime pin a QVF-container submission carries (#86).

vq refuses a single ``.qvf`` without ``program`` and a ``program_runtime_pin``
holding the runtime's git SHA. `vq submit --program` computes the pin; the
viewer calls the API directly, so it has to choose the program and supply it.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from vibeview.app.queue import QVF_PROGRAM_ENV, _qvf_submit_program

_PIN = types.SimpleNamespace(expected_git_sha="b" * 40)


def _cfg(**pythons: str) -> types.SimpleNamespace:
    programs = {
        name: types.SimpleNamespace(kind="venv", python=python)
        for name, python in pythons.items()
    }
    programs["orca"] = types.SimpleNamespace(kind="binary", binary="/opt/orca/orca")
    return types.SimpleNamespace(programs=programs)


def _pin_for(calls: list[str]):
    def pin_for(cfg, name):
        calls.append(name)
        return _PIN

    return pin_for


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv(QVF_PROGRAM_ENV, raising=False)


def test_the_only_vibeqc_program_is_used_with_its_pin():
    calls: list[str] = []
    cfg = _cfg(**{"vibeqc-dev": "/opt/vibeqc/.venv/bin/python"})
    assert _qvf_submit_program(cfg, _pin_for(calls)) == {
        "program": "vibeqc-dev",
        "program_runtime_pin": _PIN,
    }
    assert calls == ["vibeqc-dev"]


def test_the_viewers_own_environment_wins_over_name_matching():
    own = str(Path(sys.prefix) / "bin" / "python")
    cfg = _cfg(**{"vibeqc-dev": "/opt/a/.venv/bin/python", "vibeqc-release": own})
    assert _qvf_submit_program(cfg, _pin_for([]))["program"] == "vibeqc-release"


def test_the_environment_variable_names_the_program(monkeypatch):
    monkeypatch.setenv(QVF_PROGRAM_ENV, "vibeqc-release")
    cfg = _cfg(
        **{"vibeqc-dev": "/opt/a/.venv/bin/python", "vibeqc-release": "/opt/b/.venv/bin/python"}
    )
    assert _qvf_submit_program(cfg, _pin_for([]))["program"] == "vibeqc-release"


def test_an_unregistered_environment_variable_is_reported(monkeypatch):
    monkeypatch.setenv(QVF_PROGRAM_ENV, "orca")
    with pytest.raises(ValueError, match="not a registered vq venv program"):
        _qvf_submit_program(_cfg(**{"vibeqc-dev": "/x/bin/python"}), _pin_for([]))


@pytest.mark.parametrize(
    "pythons",
    [{}, {"vibeqc-dev": "/opt/a/.venv/bin/python", "vibeqc-release": "/opt/b/.venv/bin/python"}],
)
def test_no_or_ambiguous_program_is_reported_not_guessed(pythons):
    with pytest.raises(ValueError, match=QVF_PROGRAM_ENV):
        _qvf_submit_program(_cfg(**pythons), _pin_for([]))


def test_a_vq_without_a_pin_builder_is_reported():
    with pytest.raises(ValueError, match="0.26.10"):
        _qvf_submit_program(_cfg(**{"vibeqc-dev": "/x/bin/python"}), None)


@pytest.mark.parametrize("pin", [None, types.SimpleNamespace(expected_git_sha=None)])
def test_a_runtime_without_git_identity_is_reported(pin):
    with pytest.raises(ValueError, match="git identity"):
        _qvf_submit_program(_cfg(**{"vibeqc-dev": "/x/bin/python"}), lambda c, n: pin)


def test_the_viewer_submits_a_container_with_program_and_pin(water_qvf, monkeypatch, tmp_path):
    """The controller path end to end, without vibeqc: the pending container
    is stubbed, the submit call is the real one with vq stubbed."""
    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.app import helpers as _helpers
    from vibeview.qvf import QVFReader

    seen: dict = {}

    def build(sdata, params, stem):
        path = Path(f"{stem}.qvf")
        path.write_bytes(b"pending")
        return path

    def submit_local(*, host, input_file, job_name, tags, **kwargs):
        seen.update(kwargs, input_file=input_file, tags=tags)
        return "0123456789ab"

    monkeypatch.setattr(_helpers, "_vibeqc_importable", lambda: True)
    monkeypatch.setattr(_helpers, "build_pending_container", build)
    submit_mod = types.ModuleType("vq.submit")
    submit_mod.submit_local = submit_local
    submit_mod._program_runtime_pin_for_submit = lambda cfg, name: _PIN
    config_mod = types.ModuleType("vq.config")
    config_mod.load_config = lambda: _cfg(**{"vibeqc-dev": "/opt/vibeqc/.venv/bin/python"})
    listing_mod = types.ModuleType("vq.listing")
    listing_mod.list_jobs = lambda h, *a, **k: []
    for module in (submit_mod, config_mod, listing_mod):
        monkeypatch.setitem(sys.modules, module.__name__, module)

    reader = QVFReader(water_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        server.state.calc_engine = "vibe-qc"
        server.state.calc_method = "rhf"
        server.state.calc_basis = "sto-3g"
        server.state.vq_submit_container = True
        server.controller.vq_submit_job()
    finally:
        reader.close()

    assert seen, server.state.status_message
    assert str(seen["input_file"]).endswith(".qvf")
    assert "qvf-container" in seen["tags"]
    assert seen["program"] == "vibeqc-dev"
    assert seen["program_runtime_pin"] is _PIN
    assert "python" not in seen
