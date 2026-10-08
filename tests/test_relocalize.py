"""Protocol and archive adapter contracts without a co-installed vibe-qc."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

from vibeview import relocalize as client
from vibeview.qvf import QVFReader

DATA = Path(__file__).parent / "data" / "relocalize"


def request_fixture():
    return json.loads((DATA / "h2.request.json").read_text())


def result_fixture():
    return json.loads((DATA / "h2.result.json").read_text())


def capabilities():
    return {
        "backend": "vibe-qc",
        "backend_version": "0.17.5.dev0",
        "ready": True,
        "native_core_ready": True,
        "fresh_rhf": {"ready": True},
        "methods": {
            method: {
                "ready": True,
                "molecular": True,
                "periodic": False,
                "coefficient_types": ["real"],
                "spins": ["restricted"],
                "occupations": [2],
                "kpoints": ["none"],
                "ao_convention": "qvf-gto-v1",
                "max_atomic_number": 86,
                "supplied_subspace": True,
                "fresh_rhf": True,
            }
            for method in client.METHODS
        },
    }


@pytest.fixture
def molecular_qvf(tmp_path):
    request = request_fixture()
    data = {
        "structure.json": {
            "atoms": [
                {
                    "symbol": "H",
                    "atomic_number": z,
                    "position": (np.array(p) * client.BOHR_ANGSTROM).tolist(),
                }
                for z, p in zip(
                    request["system"]["atomic_numbers"],
                    request["system"]["positions_bohr"],
                    strict=True,
                )
            ],
            "pbc": [False, False, False],
        },
        "basis.json": {
            "structure_ref": "structure",
            "pure": True,
            "n_ao": 2,
            "shells": request["basis"]["shells"],
        },
        "mo.json": {
            "spin": "restricted",
            "orbital_kind": "canonical",
            "n_mo": 1,
            "n_ao": 2,
            "occupations": [2],
            "energies": [-0.5],
        },
    }
    files = {name: json.dumps(value).encode() for name, value in data.items()}
    files["mo.bin"] = np.array(request["orbitals"]["coefficients"]["data"], dtype="<f8").tobytes()

    def member(path, binary=False):
        result = {
            "path": path,
            "format": "binary" if binary else "json",
            "sha256": hashlib.sha256(files[path]).hexdigest(),
        }
        if binary:
            result.update(dtype="float64", shape=[1, 2])
        return result

    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0.17.5.dev0", "calculation": "H2 fixture"},
        "provenance": {"charge": 0, "multiplicity": 1, "n_electrons": 2, "basis": "sto-3g"},
        "sections": [
            {
                "id": "structure",
                "kind": "structure",
                "members": {"structure": member("structure.json")},
            },
            {
                "id": "wf",
                "kind": "wavefunction.gto",
                "members": {
                    "basis": member("basis.json"),
                    "mo_metadata": member("mo.json"),
                    "mo_coefficients": member("mo.bin", True),
                },
            },
        ],
    }
    path = tmp_path / "h2.qvf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, content in files.items():
            archive.writestr(name, content)
    return path


@pytest.fixture
def reader(molecular_qvf):
    with QVFReader(molecular_qvf) as value:
        yield value


def fake_worker(tmp_path, body):
    path = tmp_path / "worker.py"
    path.write_text(
        "import json,sys,os,time\n"
        "r = None if '--probe' in sys.argv else json.loads(sys.stdin.readline())\n"
        "def event(kind, **payload):\n"
        "    return dict(protocol='vibeqc.relocalize',protocol_version=1,"
        "worker_version='1.0.0',id=None if r is None else r['id'],event=kind,**payload)\n"
        "def emit(kind, **payload):\n"
        "    print(json.dumps(event(kind, **payload)),flush=True)\n" + body
    )
    return [sys.executable, str(path)]


def test_request_exact_shells_and_nonleading_occupied_rows(reader, monkeypatch):
    wf = reader.read_wavefunction_gto("wf")
    occupied = wf.mo_coefficients.copy()
    wf.mo_coefficients = np.vstack([np.zeros_like(occupied), occupied])
    wf.occupations = np.array([0, 2])
    monkeypatch.setattr(reader, "read_wavefunction_gto", lambda _: wf)
    request = client.request_from_reader(reader, "boys")
    expected = request_fixture()
    assert request["basis"]["shells"] == expected["basis"]["shells"]
    assert "name" not in request["basis"]
    assert request["orbitals"] == expected["orbitals"]
    np.testing.assert_allclose(
        request["system"]["positions_bohr"], expected["system"]["positions_bohr"]
    )
    assert request["source"] == "supplied"


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda r, w: r.provenance.pop("charge"), "charge"),
        (lambda r, w: r.provenance.update(uses_ecp=True), "ECP"),
        (lambda r, w: setattr(w, "spin", "unrestricted"), "restricted"),
        (lambda r, w: setattr(w, "occupations", np.array([1.5])), "doubly"),
        (lambda r, w: setattr(w, "occupations", np.array([0.0])), "electron count"),
        (lambda r, w: setattr(w, "mo_coefficients", None), "no occupied coefficients"),
        (lambda r, w: setattr(w, "mo_coefficients", w.mo_coefficients.astype(complex)), "Complex"),
        (lambda r, w: setattr(w, "shells", []), "shells"),
        (lambda r, w: setattr(w, "orbital_kind", "natural"), "weights"),
    ],
)
def test_incomplete_or_unsupported_inputs_never_fall_back(reader, monkeypatch, change, reason):
    wf = reader.read_wavefunction_gto("wf")
    monkeypatch.setattr(reader, "read_wavefunction_gto", lambda _: wf)
    change(reader, wf)
    with pytest.raises(ValueError, match=reason):
        client.request_from_reader(reader, "ibo")


def test_edited_geometry_requires_explicit_fresh_rhf(reader):
    reader.set_edit_overlay([[0, 0, 0], [0, 0, 1]], ["H", "H"])
    with pytest.raises(ValueError, match="Geometry edits"):
        client.request_from_reader(reader, "ibo")
    request = client.request_from_reader(reader, "ibo", source="fresh_rhf")
    assert request["source"] == "fresh_rhf" and "orbitals" not in request
    assert request["system"]["positions_bohr"][1][2] == pytest.approx(1 / client.BOHR_ANGSTROM)


def test_capabilities_are_per_method_and_per_source(reader):
    cap = capabilities()
    cap["methods"]["boys"]["ready"] = False
    assert [o["value"] for o in client.method_options(reader, cap, "wf")[0]] == [
        "ibo",
        "pipek-mezey",
    ]
    cap["fresh_rhf"]["ready"] = False
    assert client.method_options(reader, cap, "wf", source="fresh_rhf")[0] == []
    cap["native_core_ready"] = False
    assert client.method_options(reader, cap, "wf")[0] == []


def test_result_overlay_preserves_basis_and_has_no_energy(reader):
    request = client.request_from_reader(reader, "ibo")
    canonical = reader.read_wavefunction_gto("wf")
    overlay = client.wavefunction_from_result(result_fixture(), canonical, request)
    assert overlay.shells is canonical.shells and overlay.energies is None
    assert overlay.relocalization["population_model"] == "iao-mini"
    np.testing.assert_array_equal(overlay.occupations, [2])
    sid = client.overlay_section_id("ibo")
    reader.set_wavefunction_overlay(sid, overlay)
    assert reader.read_wavefunction_gto(sid) is overlay
    reader.set_edit_overlay([[0, 0, 0], [0, 0, 1]], ["H", "H"])
    assert sid not in reader.wavefunction_overlay_ids
    np.testing.assert_array_equal(
        reader.read_wavefunction_gto("wf").mo_coefficients, canonical.mo_coefficients
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(spin="unrestricted"),
        lambda r: r.update(representation="finite_torus_ao"),
        lambda r: r.update(scf_performed=True),
        lambda r: r.update(occupations=[1]),
        lambda r: r.update(charges=[5, 5]),
        lambda r: r.update(population_model="lowdin_finite_torus"),
        lambda r: r.update(
            coefficients={"encoding": "complex_split_last_axis", "data": [[[1, 0], [0, 1]]]}
        ),
        lambda r: r["coefficients"]["data"][0].__setitem__(0, 0.0),
        lambda r: r["validation"].update(subspace_residual=1),
        lambda r: r.update(centroids_bohr=[[float("nan"), 0, 0]]),
    ],
)
def test_invalid_result_cannot_become_an_overlay(reader, change):
    result = result_fixture()
    change(result)
    with pytest.raises(ValueError):
        client.wavefunction_from_result(
            result, reader.read_wavefunction_gto("wf"), client.request_from_reader(reader, "ibo")
        )
    assert not reader.wavefunction_overlay_ids


def test_subprocess_protocol_drains_diagnostics(tmp_path):
    result = result_fixture()
    cmd = fake_worker(
        tmp_path,
        "os.write(2,b'diagnostic\\n'*20000)\nemit('started',stage='validating')\nemit('result',result="
        + repr(result)
        + ")\n",
    )
    actual = asyncio.run(client.run_relocalization(request_fixture(), worker_cmd=cmd))
    assert actual == result


@pytest.mark.parametrize(
    "body,message",
    [
        ("emit('started')\n", "without a result|sequence"),
        ("print('native noise')\n", "backend"),
        ("emit('result',result={})\nemit('result',result={})\n", "sequence"),
        ("r['id']='stale'\nemit('started')\nemit('result',result={})\n", "request ID"),
        ("emit('started')\nemit('result',result={})\nsys.exit(3)\n", "status 3"),
        ("e=event('capabilities')\ne['protocol_version']=2\nprint(json.dumps(e))\n", "protocol"),
        (
            "emit('error',error={'code':'invalid_source','message':'No orbitals',"
            "'field':'orbitals'})\nsys.exit(2)\n",
            "No orbitals",
        ),
    ],
)
def test_subprocess_refuses_invalid_or_unsuccessful_events(tmp_path, body, message):
    result = asyncio.run(
        client.run_relocalization(request_fixture(), worker_cmd=fake_worker(tmp_path, body))
    )
    assert re.search(message, result["error"])


def test_failed_probe_and_partial_readiness(tmp_path):
    cap = capabilities()
    cap["methods"]["boys"]["ready"] = False
    result = asyncio.run(
        client.probe_worker(
            worker_cmd=fake_worker(
                tmp_path, "emit('capabilities',capabilities=" + repr(cap) + ")\n"
            )
        )
    )
    assert result["available"] and not result["capabilities"]["methods"]["boys"]["ready"]
    missing = asyncio.run(client.probe_worker("/nonexistent/python"))
    assert not missing["available"] and missing["reason"]


@pytest.mark.parametrize("probe", [False, True])
def test_timeout_reaps_worker(tmp_path, probe):
    pidfile = tmp_path / "pid"
    cmd = fake_worker(
        tmp_path, f"open({str(pidfile)!r},'w').write(str(os.getpid()))\ntime.sleep(60)\n"
    )
    call = (
        client.probe_worker(worker_cmd=cmd, timeout_s=0.3)
        if probe
        else client.run_relocalization(request_fixture(), worker_cmd=cmd, timeout_s=0.3)
    )
    result = asyncio.run(call)
    assert "timed out" in result.get("error", result.get("reason", ""))
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_cancellation_reaps_worker(tmp_path):
    pidfile = tmp_path / "pid"
    cmd = fake_worker(
        tmp_path, f"open({str(pidfile)!r},'w').write(str(os.getpid()))\ntime.sleep(60)\n"
    )

    async def run():
        task = asyncio.create_task(client.run_relocalization(request_fixture(), worker_cmd=cmd))
        for _ in range(100):
            if pidfile.exists():
                break
            await asyncio.sleep(0.01)
        assert pidfile.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_backend_path_is_explicit_and_preserves_venv_symlink():
    assert client.backend_command(sys.executable) == [
        sys.executable,
        "-I",
        "-m",
        "vibeqc_relocalize",
    ]
    for path in ["", "python", "relative/python"]:
        with pytest.raises(ValueError):
            client.backend_command(path)


@pytest.mark.parametrize("method", client.METHODS)
@pytest.mark.parametrize("source", ["supplied", "fresh_rhf"])
def test_separately_installed_backend(reader, method, source):
    backend = os.environ.get("VIBEQC_RELOCALIZE_PYTHON")
    if not backend:
        pytest.skip("Set VIBEQC_RELOCALIZE_PYTHON for the separately installed native backend test")
    report = asyncio.run(client.probe_worker(backend))
    assert report["available"], report
    request = client.request_from_reader(reader, method, source=source)
    result = asyncio.run(client.run_relocalization(request, backend_python=backend))
    assert "error" not in result, result
    overlay = client.wavefunction_from_result(result, reader.read_wavefunction_gto("wf"), request)
    assert overlay.relocalization["scf_performed"] is (source == "fresh_rhf")


@pytest.fixture
def app_pair(molecular_qvf, monkeypatch, tmp_path):
    from tests.test_controller_smoke import _capture_plotters
    from vibeview.app import create_app

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "settings"))
    from vibeview.settings import set_setting

    set_setting("relocalize_backend_python", sys.executable)
    plotters = _capture_plotters(monkeypatch)
    readers = [QVFReader(molecular_qvf), QVFReader(molecular_qvf)]
    app = create_app(readers)
    app.controller.view_update = lambda *a, **kw: None
    assert app.state.relocalize_backend_python == sys.executable
    assert app.state.settings_relocalize_backend_python == sys.executable
    app.state.relocalize_capabilities = capabilities()
    app.state.relocalize_available = True
    app.controller.activate_section("wf")
    try:
        yield app, readers, plotters[0]
    finally:
        app.controller.cancel_relocalize()
        for reader in readers:
            reader.close()
        for plotter in plotters:
            plotter.close()


@pytest.mark.parametrize("source", ["supplied", "fresh_rhf"])
def test_controller_applies_and_clears_session_overlay(app_pair, monkeypatch, source):
    app, readers, _ = app_pair
    app.state.relocalize_source = source
    app.state.flush()

    async def result(request, **kwargs):
        assert request["source"] == source
        assert ("orbitals" in request) is (source == "supplied")
        result = result_fixture()
        result.update(source=source, scf_performed=(source == "fresh_rhf"))
        return result

    monkeypatch.setattr(client, "run_relocalization", result)

    async def run():
        app.controller.run_relocalize()
        await asyncio.sleep(0)

    asyncio.run(run())
    sid = client.overlay_section_id("ibo", source)
    assert readers[0].wavefunction_overlay_ids == [sid]
    assert app.state.wf_section_id == sid
    if source == "fresh_rhf":
        entry = next(e for e in app.state.sidebar_entries if e["id"] == sid)
        assert entry["title"] == "Re-localized (IBO, new RHF)"
    assert "iao-mini" in app.state.relocalize_result_summary
    label = "New RHF" if source == "fresh_rhf" else "Archived occupied subspace"
    assert label in app.state.relocalize_result_summary
    assert len(app.state.relocalize_charge_rows) == 2
    app.controller.clear_relocalization()
    assert not readers[0].wavefunction_overlay_ids
    assert app.state.wf_section_id == "wf"
    assert not app.state.relocalize_charge_rows


@pytest.mark.parametrize("action", ["file", "geometry", "cancel", "backend", "section"])
def test_controller_discards_late_results(app_pair, monkeypatch, action):
    app, readers, plotter = app_pair

    async def run():
        started, released = asyncio.Event(), asyncio.Event()

        async def delayed(request, **kwargs):
            started.set()
            # A response already queued at cancellation must still be stale.
            with contextlib.suppress(asyncio.CancelledError):
                await released.wait()
            return result_fixture()

        monkeypatch.setattr(client, "run_relocalization", delayed)
        app.controller.run_relocalize()
        await started.wait()
        if action == "file":
            app.controller.switch_file(1)
        elif action == "geometry":
            from vibeview.app import _rebuild_structure_from_positions

            _rebuild_structure_from_positions(
                readers[0], plotter, [[0, 0, 0], [0, 0, 1]], ["H", "H"]
            )
        elif action == "cancel":
            app.controller.cancel_relocalize()
        elif action == "backend":
            app.state.relocalize_backend_python = "/different/backend/python"
        else:
            app.controller.activate_section("structure")
        released.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())
    assert not any(r.wavefunction_overlay_ids for r in readers)
    assert not app.state.relocalize_running


def test_global_backend_setting_preserves_other_preferences(app_pair, monkeypatch):
    from vibeview.settings import load_settings, save_settings

    app, _, _ = app_pair
    save_settings({"recent_files": ["example.qvf"], "default_opacity": 0.3})
    app.state.settings_relocalize_backend_python = sys.executable
    app.controller.save_settings()
    stored = load_settings()
    assert stored["relocalize_backend_python"] == sys.executable
    assert stored["recent_files"] == ["example.qvf"]
    assert stored["default_opacity"] == 0.3
    assert not app.state.relocalize_capabilities


@pytest.mark.parametrize("bad", [None, [], {"ibo": None}])
def test_malformed_capability_payload_is_unavailable(tmp_path, bad):
    cap = capabilities()
    cap["methods"] = bad
    result = asyncio.run(
        client.probe_worker(
            worker_cmd=fake_worker(
                tmp_path, "emit('capabilities',capabilities=" + repr(cap) + ")\n"
            )
        )
    )
    assert not result["available"]
    assert "capabilities" in result["reason"]


def test_fresh_rhf_refuses_changed_atom_identities(reader):
    reader.set_edit_overlay([[0, 0, 0], [0, 0, 1]], ["H", "Li"])
    with pytest.raises(ValueError, match="atom identities"):
        client.request_from_reader(reader, "ibo", source="fresh_rhf")


def test_basis_label_is_required_only_for_fresh_rhf(reader):
    reader.provenance.pop("basis")
    assert "name" not in client.request_from_reader(reader, "ibo")["basis"]
    with pytest.raises(ValueError, match="basis name"):
        client.request_from_reader(reader, "ibo", source="fresh_rhf")


def test_probe_surfaces_missing_worker_diagnostics(tmp_path):
    cmd = fake_worker(tmp_path, "print('No module named vibeqc_relocalize',file=sys.stderr)\n")
    result = asyncio.run(client.probe_worker(worker_cmd=cmd))
    assert not result["available"]
    assert "No module named vibeqc_relocalize" in result["reason"]


def test_fresh_rhf_remains_available_after_position_edit(app_pair):
    from vibeview.app import _rebuild_structure_from_positions, _refresh_relocalize_state

    app, readers, plotter = app_pair
    app.state.relocalize_source = "fresh_rhf"
    app.state.flush()
    _rebuild_structure_from_positions(readers[0], plotter, [[0, 0, 0], [0, 0, 1]], ["H", "H"])
    assert app.state.relocalize_supported
    app.state.relocalize_source = "supplied"
    _refresh_relocalize_state(readers[0], app.state)
    assert not app.state.relocalize_supported
    assert "Geometry edits" in app.state.relocalize_reason


def test_stale_probe_cannot_replace_newer_readiness(app_pair, monkeypatch):
    app, _, _ = app_pair

    async def run():
        started, released = asyncio.Event(), asyncio.Event()
        calls = 0

        async def probe(path):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                with contextlib.suppress(asyncio.CancelledError):
                    await released.wait()
                return {"available": True, "capabilities": capabilities(), "reason": ""}
            return {"available": False, "capabilities": {}, "reason": "Native core unavailable"}

        monkeypatch.setattr(client, "probe_worker", probe)
        app.controller.check_relocalize_backend()
        await started.wait()
        app.controller.check_relocalize_backend()
        await asyncio.sleep(0)
        released.set()
        await asyncio.sleep(0)
        assert not app.state.relocalize_available
        assert not app.state.relocalize_supported
        assert app.state.relocalize_backend_status == "Native core unavailable"
        assert not app.state.relocalize_probe_running

    asyncio.run(run())
