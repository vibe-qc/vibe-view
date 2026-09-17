"""Client for vibe-qc's standalone relocalization protocol 1.

No numerical backend is imported into the viewer. Exact QVF shells and the
complete occupied subspace travel to a separately configured Python process.
See vibe-qc's docs/relocalization_worker.md (worker API >= 1.0.0, major 1).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from vibeview.qvf import QVFError

PROTOCOL = "vibeqc.relocalize"
METHODS = ("ibo", "boys", "pipek-mezey")
METHOD_LABELS = {
    "ibo": "IBO (intrinsic bond orbitals)",
    "boys": "Foster-Boys",
    "pipek-mezey": "Pipek-Mezey",
}
BOHR_ANGSTROM = 0.529177210903
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


def backend_command(backend_python: str) -> list[str]:
    """Preserve venv symlinks; resolving a Python symlink loses its venv."""
    if not isinstance(backend_python, str) or not backend_python.strip():
        raise ValueError("Set the vibe-qc Python path in Settings and check the backend")
    path = Path(backend_python.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError("The vibe-qc Python path must be absolute")
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("The configured vibe-qc Python is not an executable file")
    # Ignore viewer PYTHONPATH/user-site/cwd imports while keeping backend venv
    # site-packages (including an editable backend install).
    return [str(path), "-I", "-m", "vibeqc_relocalize"]


def _real_array(value: Any, shape: tuple | None, label: str) -> np.ndarray:
    try:
        raw = np.asarray(value, dtype=object)
        if not all(
            isinstance(x, (int, float, np.integer, np.floating))
            and not isinstance(x, (bool, np.bool_))
            for x in raw.flat
        ):
            raise ValueError()
        result = raw.astype(float)
        if not np.isfinite(result).all() or (shape is not None and result.shape != shape):
            raise ValueError()
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"Invalid {label}: expected finite numbers with shape {shape}") from exc
    return result


def request_from_reader(
    reader: Any,
    method: str,
    *,
    canonical_section_id: str = "wf",
    source: str = "supplied",
) -> dict[str, Any]:
    """Build a molecular request or explain exactly which archive data is missing.

    QVF shell/AO conventions are defined by the GTO format. No basis-name
    reconstruction, occupation inference, complex cast or SCF fallback occurs.
    """
    if method not in METHODS or source not in ("supplied", "fresh_rhf"):
        raise ValueError("Unsupported localization method or source")
    structure = reader.read_structure()
    if any(structure.pbc):
        raise ValueError(
            "Periodic re-localization needs S(k), complete finite-BvK provenance "
            "and a finite-torus overlay adapter; this viewer cannot supply them yet"
        )
    wf = reader.read_wavefunction_gto(canonical_section_id)
    structure = reader.read_structure(wf.structure_ref)
    if any(structure.pbc) or wf.k_point is not None:
        raise ValueError("Periodic re-localization is unavailable for this wavefunction")
    if source == "supplied" and reader.has_edit_overlay:
        raise ValueError(
            "Geometry edits invalidate archived orbitals; reopen the archive "
            "or explicitly calculate new RHF orbitals"
        )
    provenance = reader.provenance or {}
    charge, multiplicity = provenance.get("charge"), provenance.get("multiplicity")
    if type(charge) is not int or type(multiplicity) is not int:
        raise ValueError("The archive must record charge and multiplicity explicitly")
    if wf.spin != "restricted" or multiplicity != 1:
        raise ValueError("The backend supports restricted singlets only")
    if wf.orbital_kind == "natural" and wf.occupation_semantics != "electron_occupation":
        raise ValueError("Natural orbital weights are not confirmed electron occupations")
    if any(provenance.get(key) for key in ("uses_ecp", "ecp", "ecp_core_electrons")):
        raise ValueError("ECP re-localization is unsupported")
    numbers = [a.atomic_number for a in structure.atoms]
    if not numbers or any(type(z) is not int or not 1 <= z <= 86 for z in numbers):
        raise ValueError("Molecular localization needs all-electron atoms with Z from 1 to 86")
    electrons = sum(numbers) - charge
    if electrons <= 0 or electrons % 2:
        raise ValueError("A restricted singlet needs a positive even electron count")
    if not 0 < wf.n_ao <= 512 or not wf.shells:
        raise ValueError("Exact archived basis shells with at most 512 AOs are required")
    shells = []
    for shell in wf.shells:
        if not 0 <= shell.center < len(numbers):
            raise ValueError("The archived basis no longer matches the edited atoms")
        shells.append(
            {
                "center": shell.center,
                "l": shell.l,
                "pure": shell.pure,
                "exponents": shell.exponents.tolist(),
                "coefficients": shell.coefficients.tolist(),
            }
        )
    # A complete all-electron occupied manifold establishes the electron-count
    # provenance for older QVF files without an explicit uses_ecp flag.
    if wf.occupations is None:
        raise ValueError(
            "Explicit electron occupations are required to establish all-electron provenance"
        )
    occupations = _real_array(wf.occupations, None, "occupations")
    if occupations.ndim != 1 or not np.isin(occupations, [0, 2]).all():
        raise ValueError("Only complete doubly occupied restricted orbitals are supported")
    # Fresh RHF can use edited geometry, but its original archive must still
    # establish that the shells belong to an all-electron calculation.
    original_numbers = numbers
    if source == "fresh_rhf" and reader.has_edit_overlay:
        raw = reader._read_json_member(wf.structure_ref, "structure")
        original_numbers = [a["atomic_number"] for a in raw["atoms"]]
        if numbers != original_numbers:
            raise ValueError("Changed atom identities require a new basis and calculation archive")
    if int(occupations.sum()) != sum(original_numbers) - charge:
        raise ValueError(
            "Occupied electron count does not establish a complete all-electron "
            "subspace (ECP or missing orbitals)"
        )
    request = {
        "protocol": PROTOCOL,
        "protocol_version": 1,
        "id": uuid4().hex,
        "operation": "localize",
        "method": method,
        "source": source,
        "system": {
            "kind": "molecular",
            "atomic_numbers": numbers,
            "positions_bohr": (
                np.asarray([a.position for a in structure.atoms]) / BOHR_ANGSTROM
            ).tolist(),
            "charge": charge,
            "multiplicity": multiplicity,
            "spin": wf.spin,
        },
        "basis": {"ao_convention": "qvf-gto-v1", "uses_ecp": False, "shells": shells},
    }
    if source == "fresh_rhf":
        # Worker API 1.0.0 uses a named atomic SAD guess even with exact shells.
        # Preserve the archived label; never substitute a guessed basis name.
        name = provenance.get("basis")
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError("New RHF needs an archived basis name for the backend's initial guess")
        request["basis"]["name"] = name
    if source == "supplied":
        if wf.mo_coefficients is None:
            raise ValueError(
                "The archive has no occupied coefficients; "
                "a new RHF calculation must be chosen explicitly"
            )
        if np.iscomplexobj(wf.mo_coefficients):
            raise ValueError(
                "Complex molecular re-localization is unsupported; "
                "imaginary coefficients are preserved"
            )
        coefficients = _real_array(
            wf.mo_coefficients, (len(occupations), wf.n_ao), "orbital coefficients"
        )
        occupied = occupations == 2
        request["orbitals"] = {
            "coefficients": {"encoding": "real", "data": coefficients[occupied].tolist()},
            "occupations": occupations[occupied].tolist(),
        }
    return request


def request_fingerprint(request: dict) -> str:
    data = {k: v for k, v in request.items() if k != "id"}
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def method_options(reader, capabilities, section_id, *, source="supplied"):
    """Intersection of protocol support, actual readiness and archive data."""
    try:
        request = request_from_reader(reader, "ibo", canonical_section_id=section_id, source=source)
    except (QVFError, ValueError, KeyError, AttributeError, TypeError) as exc:
        return [], str(exc)
    if not isinstance(capabilities, dict) or capabilities.get("native_core_ready") is not True:
        return [], "Check the configured vibe-qc backend in Settings"
    fresh = capabilities.get("fresh_rhf")
    if source == "fresh_rhf" and (not isinstance(fresh, dict) or fresh.get("ready") is not True):
        return [], "The backend's fresh RHF readiness check did not pass"
    methods = capabilities.get("methods", {})
    if not isinstance(methods, dict):
        return [], "Invalid backend method capabilities; check the backend again"

    def supports(spec, key, value):
        return isinstance(spec.get(key), list) and value in spec[key]

    offered = []
    for name in METHODS:
        spec = methods.get(name, {})
        if not isinstance(spec, dict):
            continue

        if (
            spec.get("ready") is True
            and spec.get("molecular") is True
            and supports(spec, "coefficient_types", "real")
            and supports(spec, "spins", "restricted")
            and supports(spec, "occupations", 2)
            and supports(spec, "kpoints", "none")
            and spec.get("ao_convention") == "qvf-gto-v1"
            and spec.get("fresh_rhf" if source == "fresh_rhf" else "supplied_subspace") is True
        ):
            maximum = spec.get("max_atomic_number", 0)
            if type(maximum) is int and max(request["system"]["atomic_numbers"]) <= maximum:
                offered.append({"title": METHOD_LABELS[name], "value": name})
    return (
        offered,
        "" if offered else "No compatible localization method passed the backend readiness checks",
    )


def overlay_section_id(method: str, source: str = "supplied") -> str:
    suffix = "_fresh_rhf" if source == "fresh_rhf" else ""
    return f"wf_relocalized_{method.replace('-', '_')}{suffix}"


def wavefunction_from_result(result: dict, canonical: Any, request: dict) -> Any:
    """Validate a result before constructing a molecular session overlay."""
    from dataclasses import replace

    nocc = (sum(request["system"]["atomic_numbers"]) - request["system"]["charge"]) // 2
    natom, nao = len(request["system"]["atomic_numbers"]), canonical.n_ao
    if (
        result.get("representation") != "molecular_ao"
        or result.get("method") != request["method"]
        or result.get("source") != request["source"]
        or result.get("spin") != "restricted"
        or result.get("ao_convention") != "qvf-gto-v1"
        or result.get("scf_performed") is not (request["source"] == "fresh_rhf")
        or result.get("experimental") is not False
    ):
        raise ValueError("Backend result does not match the requested molecular calculation")

    def decode(value, shape, label):
        if not isinstance(value, dict) or value.get("encoding") != "real":
            raise ValueError(
                f"Unsupported {label} encoding; complex results cannot use the molecular adapter"
            )
        return _real_array(value.get("data"), shape, label)

    coefficients = decode(result.get("coefficients"), (nocc, nao), "coefficients")
    rotation = decode(result.get("rotation"), (nocc, nocc), "rotation")
    occupations = _real_array(result.get("occupations"), (nocc,), "occupations")
    populations = _real_array(result.get("atom_populations"), (nocc, natom), "populations")
    centroids = _real_array(result.get("centroids_bohr"), (nocc, 3), "centroids")
    charges = _real_array(result.get("charges"), (natom,), "charges")
    centres = _real_array(result.get("n_centres"), (nocc,), "centre counts")
    if (
        not np.all(occupations == 2)
        or (populations < -1e-8).any()
        or not np.allclose(populations.sum(axis=1), 1, atol=1e-7, rtol=0)
        or not np.allclose(
            charges,
            np.array(request["system"]["atomic_numbers"]) - 2 * populations.sum(axis=0),
            atol=1e-7,
            rtol=0,
        )
        or result.get("centre_threshold") != 0.1
        or not np.array_equal(centres, (populations > 0.1).sum(axis=1))
        or result.get("population_model") != "iao-mini"
        or result.get("charge_model") != "iao-mini"
        or result.get("centroid_model") != "position_expectation"
    ):
        raise ValueError("Inconsistent occupations or localization descriptors in backend result")
    audit = result.get("validation")
    if not isinstance(audit, dict):
        raise ValueError("Missing backend subspace validation")
    warnings = result.get("warnings")
    if not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings):
        raise ValueError("Invalid backend warnings")
    errors = _real_array(
        [
            audit.get(k)
            for k in (
                "input_orthonormality_error",
                "orthonormality_error",
                "subspace_residual",
                "unitary_error",
            )
        ],
        (4,),
        "subspace validation",
    )
    if (
        (errors < 0).any()
        or (errors >= 1e-7).any()
        or not np.allclose(rotation.T @ rotation, np.eye(nocc), atol=1e-7, rtol=0)
    ):
        raise ValueError("Backend result failed the subspace/orthonormality contract")
    if request["source"] == "supplied":
        before = np.asarray(request["orbitals"]["coefficients"]["data"])
        if not np.allclose(coefficients.T, before.T @ rotation, atol=1e-7, rtol=1e-7):
            raise ValueError("Returned coefficients do not preserve the supplied occupied subspace")
    metadata = {
        key: copy.deepcopy(result.get(key))
        for key in (
            "method",
            "source",
            "scf_performed",
            "population_model",
            "charge_model",
            "centroid_model",
            "charges",
            "validation",
            "warnings",
            "convergence",
            "backend_version",
        )
    }
    return replace(
        canonical,
        orbital_kind="localized",
        energies=None,
        occupations=occupations,
        symmetry_labels=None,
        mo_coefficients=coefficients,
        atom_populations=populations,
        centroids_bohr=centroids,
        n_centres=centres.astype(int),
        localization_method=result["method"],
        occupation_semantics="electron_occupation",
        relocalization=metadata,
    )


def parse_event(line: bytes | str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON event key")
            result[key] = value
        return result

    def nonfinite(_):
        raise ValueError("Non-finite JSON event")

    if isinstance(line, bytes):
        line = line.decode("utf-8")
    value = json.loads(line, object_pairs_hook=pairs, parse_constant=nonfinite)
    if not isinstance(value, dict):
        raise ValueError("Backend event must be an object")
    return value


async def _exchange(command, payload, timeout_s):
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def drain(stream, *, diagnostics=False):
        data = bytearray()
        while chunk := await stream.read(65536):
            data.extend(chunk)
            if diagnostics:
                del data[:-8192]
            elif len(data) > MAX_RESPONSE_BYTES:
                raise ValueError("Backend response exceeds 32 MiB")
        return bytes(data)

    async def send():
        try:
            if payload:
                process.stdin.write(payload)
                await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    tasks = [
        asyncio.create_task(send()),
        asyncio.create_task(drain(process.stdout)),
        asyncio.create_task(drain(process.stderr, diagnostics=True)),
        asyncio.create_task(process.wait()),
    ]
    try:
        _, stdout, stderr, code = await asyncio.wait_for(asyncio.gather(*tasks), timeout_s)
        return stdout, stderr.decode("utf-8", "replace"), code
    finally:
        # Cancellation and timeout must reap the worker, including on probe.
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _events(stdout, request_id):
    events = [parse_event(line) for line in stdout.splitlines() if line.strip()]
    if not events:
        raise ValueError("Backend exited without a result")
    for event in events:
        if (
            event.get("protocol") != PROTOCOL
            or type(event.get("protocol_version")) is not int
            or event["protocol_version"] != 1
            or event.get("id") != request_id
            or re.fullmatch(r"1\.\d+\.\d+", str(event.get("worker_version", ""))) is None
        ):
            raise ValueError(
                "Incompatible backend protocol/worker version or mismatched request ID"
            )
    return events


async def run_relocalization(
    request, *, backend_python="", timeout_s=300.0, worker_cmd=None, on_note=None
):
    """Return a result or a diagnostic error; propagate cancellation to owner."""
    diagnostics = ""
    try:
        payload = (json.dumps(request, allow_nan=False) + "\n").encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError("Request exceeds the backend's 16 MiB limit")
        command = worker_cmd if worker_cmd is not None else backend_command(backend_python)
        stdout, diagnostics, code = await _exchange(command, payload, timeout_s)
        events = _events(stdout, request["id"])
        kinds = [event.get("event") for event in events]
        if kinds not in (["started", "result"], ["started", "error"], ["error"]):
            raise ValueError("Invalid backend event sequence or duplicate terminal event")
        if on_note and kinds[0] == "started":
            on_note("Localizing occupied orbitals")
        terminal = events[-1]
        if terminal["event"] == "error":
            error = terminal.get("error", {})
            if not isinstance(error, dict):
                raise ValueError("Invalid backend error payload")
            return {
                "error": str(error.get("message", "Backend refused the request")),
                "code": error.get("code"),
                "field": error.get("field"),
                "diagnostics": diagnostics,
            }
        if code != 0:
            raise ValueError(f"Backend exited with status {code} despite a result")
        if not isinstance(terminal.get("result"), dict):
            raise ValueError("Backend result payload is missing")
        return terminal["result"]
    except TimeoutError:
        return {"error": f"Re-localization timed out after {timeout_s:g}s"}
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return {"error": f"Re-localization backend: {exc}", "diagnostics": diagnostics}


async def probe_worker(backend_python="", timeout_s=60.0, *, worker_cmd=None):
    diagnostics = ""
    try:
        command = worker_cmd if worker_cmd is not None else backend_command(backend_python)
        stdout, diagnostics, code = await _exchange([*command, "--probe"], b"", timeout_s)
        events = _events(stdout, None)
        if len(events) != 1 or events[0].get("event") != "capabilities":
            raise ValueError("Backend probe did not return one capability event")
        capabilities = events[0].get("capabilities")
        if (
            not isinstance(capabilities, dict)
            or capabilities.get("backend") != "vibe-qc"
            or not isinstance(capabilities.get("methods"), dict)
            or any(not isinstance(spec, dict) for spec in capabilities["methods"].values())
        ):
            raise ValueError("Invalid backend capabilities")
        available = (
            code == 0
            and capabilities.get("ready") is True
            and capabilities.get("native_core_ready") is True
        )
        error = capabilities.get("error")
        reason = "" if available else "No compatible native backend method is ready"
        if not available and isinstance(error, dict) and isinstance(error.get("message"), str):
            reason = error["message"]
        return {
            "available": available,
            "reason": reason,
            "capabilities": capabilities,
            "diagnostics": diagnostics,
        }
    except TimeoutError:
        reason = f"Backend probe timed out after {timeout_s:g}s"
    except (ValueError, TypeError, OSError) as exc:
        reason = str(exc)
    if diagnostics.strip():
        reason += ": " + diagnostics.strip().splitlines()[-1][-300:]
    return {"available": False, "reason": reason, "capabilities": {}, "diagnostics": diagnostics}
