"""Re-localize a loaded wavefunction with an arbitrary criterion.

A QVF written by a recent vibe-qc already carries localized orbitals for the
criteria the *producer* chose (``wf_localized_ibo`` and friends). This module
covers the other case: the user is looking at a file and wants a criterion
that is not in it.

**vibe-view does not localize anything itself.** The maintainer's decision
(2026-08-05) is that the viewer shells out to vibe-qc rather than growing a
second copy of the maths -- see ``handovers/HANDOVER_IBO.md`` § Milestone 4.
Reimplementing the localizers here would mean reimplementing the integrals
too, and would put two versions of a published method in one repository.

Design mirrors :mod:`vibeview.live_opt`, which solved the same problem for
live geometry optimization:

* The work runs in a **subprocess** (``python -m vibeview.relocalize``) so
  vibe-qc's native core is never imported into the viewer process and a slow
  job can simply be killed.
* :func:`probe` reports why it is unavailable when vibe-qc is missing, and
  the viewer degrades to the sections already in the file.
* Wire protocol: one JSON request on the worker's stdin, one JSON event per
  line on stdout --
  ``{"note": "..."}`` for progress,
  ``{"done": true, "method": str, "n_occ": int, "n_ao": int,
  "coefficients": [[...]], "atom_populations": [[...]],
  "centroids_bohr": [[...]], "n_centres": [...], "charges": [...]}``
  on success, or ``{"error": "..."}`` on failure.

The request never carries serialised basis shells. It carries the basis-set
**name**, which the QVF manifest records under ``provenance.basis``, so the
worker rebuilds the basis with ``BasisSet(mol, name)``. That deliberately
sidesteps the one real trap in the alternative: shells serialised into a QVF
have had their contraction coefficients divided by the primitive norm
(``python/vibeqc/output/formats/qvf.py`` ``_basis_shell_payload``), so a
consumer that rebuilds "as-is" gets a silently wrong overlap unless it passes
``coefficients_pre_normalized=False``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

#: Criteria the worker will run. Mirrors ``vibeqc.iao.LOCALIZATION_METHODS``
#: but is duplicated deliberately: the viewer must be able to populate its
#: menu without importing vibeqc.
METHODS = ("ibo", "boys", "pipek-mezey")

METHOD_LABELS = {
    "ibo": "IBO (intrinsic bond orbitals)",
    "boys": "Foster-Boys",
    "pipek-mezey": "Pipek-Mezey",
}

_WORKER_TIMEOUT_S = 300.0


# ---------------------------------------------------------------------------
# Viewer side
# ---------------------------------------------------------------------------


def build_request(
    numbers: list[int],
    positions_bohr: list[list[float]],
    basis: str,
    method: str,
    *,
    charge: int = 0,
    multiplicity: int = 1,
    occupied_coefficients: Any = None,
) -> dict[str, Any]:
    """Assemble the worker request. Positions in **bohr**.

    Bohr rather than angstroms because that is what the QVF structure
    section converts to and what vibe-qc's ``Atom`` takes; converting twice
    is how a factor of 1.889 gets lost.

    ``occupied_coefficients`` is the canonical occupied block the viewer is
    already displaying, row-per-orbital. Supplying it is strongly preferred:
    it skips a redundant SCF, and it guarantees the localized orbitals are a
    rotation of *the ones on screen*. Without it the worker re-converges the
    SCF, which on a degenerate system can land on a different -- equally
    valid, but different -- set within the degenerate subspace.
    """
    request = {
        "numbers": [int(z) for z in numbers],
        "positions_bohr": [[float(c) for c in xyz] for xyz in positions_bohr],
        "basis": str(basis),
        "method": str(method),
        "charge": int(charge),
        "multiplicity": int(multiplicity),
    }
    if occupied_coefficients is not None:
        request["occupied_coefficients"] = [
            [float(c) for c in row] for row in occupied_coefficients
        ]
    return request


def request_from_reader(
    reader: Any, method: str, *, canonical_section_id: str = "wf"
) -> dict[str, Any] | None:
    """Build a request from an open :class:`~vibeview.qvf.QVFReader`.

    Returns None when the file does not record what a re-localization needs
    -- most often the basis-set name, which only lands in
    ``provenance.basis`` for files written by a vibe-qc that records it.

    When the canonical ``wavefunction.gto`` section is present its occupied
    block is passed through, so the worker localizes exactly those orbitals
    instead of re-converging its own.
    """
    provenance = reader.provenance or {}
    basis = provenance.get("basis")
    if not basis:
        return None
    try:
        structure = reader.read_structure()
    except Exception:
        return None
    if not structure.atoms:
        return None
    if any(getattr(structure, "pbc", ())):
        raise ValueError(
            "Periodic re-localization is not supported; "
            "generate localized orbitals in the producer"
        )

    bohr_per_angstrom = 1.8897261254578281
    numbers = [int(a.atomic_number) for a in structure.atoms]
    positions = [
        [float(c) * bohr_per_angstrom for c in a.position] for a in structure.atoms
    ]

    occupied = None
    try:
        wavefunction = reader.read_wavefunction_gto(canonical_section_id)
        coefficients = wavefunction.mo_coefficients
        occupations = wavefunction.occupations
        if coefficients is not None and occupations is not None:
            n_occ = int(sum(1 for o in occupations if float(o) > 1e-3))
            if n_occ:
                occupied = coefficients[:n_occ]
    except Exception:
        occupied = None

    if occupied is not None:
        import numpy as np

        if np.iscomplexobj(occupied):
            raise ValueError("Complex orbital re-localization is not supported")

    return build_request(
        numbers,
        positions,
        basis,
        method,
        charge=int(provenance.get("charge", 0) or 0),
        multiplicity=int(provenance.get("multiplicity", 1) or 1),
        occupied_coefficients=occupied,
    )


def overlay_section_id(method: str) -> str:
    """Section id a re-localized result is registered under.

    Deliberately distinct from the producer's ``wf_localized_<method>`` ids
    so a viewer-computed set never shadows one that came from the file --
    the user can tell which is which, and re-running does not silently
    replace archived data.
    """
    return f"wf_relocalized_{method.replace('-', '_')}"


def wavefunction_from_result(
    result: dict[str, Any], canonical: Any
) -> Any:
    """Build a ``WavefunctionGTOData`` from a worker result.

    ``canonical`` is the file's own canonical wavefunction, whose basis
    shells are reused verbatim: the worker localized *in that basis*, so
    copying the shells is correct and avoids re-serialising them across the
    wire. Only the coefficients and the localization descriptors differ.
    """
    import numpy as np

    from vibeview.qvf import WavefunctionGTOData

    coefficients = np.asarray(result["coefficients"], dtype=np.float64)
    n_mo = int(coefficients.shape[0])
    populations = result.get("atom_populations")
    centroids = result.get("centroids_bohr")
    centres = result.get("n_centres")

    return WavefunctionGTOData(
        structure_ref=canonical.structure_ref,
        pure=canonical.pure,
        n_ao=canonical.n_ao,
        shells=canonical.shells,
        spin="restricted",
        orbital_kind="localized",
        # A localized orbital has no eigenvalue; explicit zeros, never None
        # (the reader's np.asarray(None) yields a 0-d nan that passes its
        # own is-not-None guard).
        energies=np.zeros(n_mo, dtype=np.float64),
        occupations=np.full(n_mo, 2.0, dtype=np.float64),
        symmetry_labels=None,
        alpha_energies=None,
        alpha_occupations=None,
        beta_energies=None,
        beta_occupations=None,
        mo_coefficients=coefficients,
        mo_coefficients_alpha=None,
        mo_coefficients_beta=None,
        atom_populations=(
            np.asarray(populations, dtype=np.float64)
            if populations is not None
            else None
        ),
        centroids_bohr=(
            np.asarray(centroids, dtype=np.float64)
            if centroids is not None
            else None
        ),
        n_centres=(
            np.asarray(centres, dtype=np.int64) if centres is not None else None
        ),
        localization_method=str(result.get("method", "")) or None,
    )


def parse_event(line: str | bytes) -> dict[str, Any] | None:
    """Decode one worker stdout line; None for blanks / non-JSON noise."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    line = line.strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except (ValueError, TypeError):
        return None
    return event if isinstance(event, dict) else None


def _default_worker_cmd() -> list[str]:
    return [sys.executable, "-m", "vibeview.relocalize"]


async def run_relocalization(
    request: dict[str, Any],
    *,
    timeout_s: float = _WORKER_TIMEOUT_S,
    worker_cmd: list[str] | None = None,
    on_note: Any = None,
) -> dict[str, Any]:
    """Run one re-localization in a subprocess and return its result event.

    Always returns a dict. On any failure that dict has an ``"error"`` key
    rather than raising, so a viewer callback can surface the reason without
    a try/except at every call site.
    """
    command = worker_cmd or _default_worker_cmd()
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:  # pragma: no cover - OS-level launch failure
        return {"error": f"could not start worker: {exc}"}

    payload = (json.dumps(request) + "\n").encode()
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(payload), timeout=timeout_s
        )
    except TimeoutError:
        process.kill()
        return {"error": f"re-localization timed out after {timeout_s:.0f}s"}

    result: dict[str, Any] | None = None
    for raw in stdout.splitlines():
        event = parse_event(raw)
        if event is None:
            continue
        if "note" in event and on_note is not None:
            on_note(str(event["note"]))
        if "error" in event or event.get("done"):
            result = event
    if result is not None:
        return result
    detail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()
    return {
        "error": "re-localization worker exited without a result"
        + (f": {detail[-1]}" if detail else "")
    }


async def probe_worker(timeout_s: float = 60.0) -> dict[str, Any]:
    """Run the worker's ``--probe`` in a subprocess (async, viewer side)."""
    try:
        process = await asyncio.create_subprocess_exec(
            *_default_worker_cmd(),
            "--probe",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except Exception as exc:
        return {"available": False, "reason": str(exc), "methods": []}
    for raw in (stdout or b"").splitlines():
        event = parse_event(raw)
        if event is not None and "available" in event:
            return event
    return {
        "available": False,
        "reason": "probe produced no result",
        "methods": [],
    }


# ---------------------------------------------------------------------------
# Worker side
# ---------------------------------------------------------------------------


def probe() -> dict[str, Any]:
    """Report whether this interpreter can re-localize, and with which criteria.

    ``find_spec``-free: the localizers are pure Python but ``analyse_localization``
    needs the compiled core for the integrals, so an import is the only honest
    test. Runs in the worker, never in the viewer process.
    """
    try:
        from vibeqc.iao import LOCALIZATION_METHODS  # noqa: F401
    except Exception as exc:
        return {
            "available": False,
            "reason": f"vibe-qc not importable: {exc}",
            "methods": [],
        }
    return {"available": True, "reason": "", "methods": list(LOCALIZATION_METHODS)}


def _run_relocalization(request: dict[str, Any], emit: Any) -> None:
    import numpy as np
    from vibeqc import Atom, BasisSet, Molecule, RHFOptions, run_rhf
    from vibeqc.iao import analyse_localization, iao_unsupported_reason

    method = str(request.get("method", "ibo"))
    molecule = Molecule(
        [
            Atom(int(z), [float(c) for c in xyz])
            # strict: a length mismatch here would silently drop atoms and
            # return a localization of a different molecule.
            for z, xyz in zip(
                request["numbers"], request["positions_bohr"], strict=True
            )
        ],
        charge=int(request.get("charge", 0)),
        multiplicity=int(request.get("multiplicity", 1)),
    )

    blocked = iao_unsupported_reason(molecule)
    if blocked is not None:
        emit({"error": blocked})
        return
    if int(request.get("multiplicity", 1)) != 1:
        emit({"error": "re-localization is closed-shell only"})
        return

    # Rebuild the basis by NAME, never from serialised QVF shells -- see the
    # module docstring for why that distinction matters.
    basis = BasisSet(molecule, str(request["basis"]))

    supplied = request.get("occupied_coefficients")
    if supplied:
        # Row-per-orbital on the wire (QVF convention), column-per-orbital
        # in vibe-qc.
        occupied = np.ascontiguousarray(np.asarray(supplied, dtype=float).T)
        if occupied.shape[0] != basis.nbasis:
            emit(
                {
                    "error": (
                        f"supplied orbitals have {occupied.shape[0]} AO rows but "
                        f"basis '{basis.name}' has {basis.nbasis}"
                    )
                }
            )
            return
        # Guard the one assumption this path makes: that the AO ordering of
        # the supplied coefficients matches what BasisSet(mol, name) builds.
        # If a producer ever changes convention this catches it here rather
        # than returning plausible, wrong orbitals.
        from vibeqc import compute_overlap

        overlap = np.asarray(compute_overlap(basis))
        deviation = np.abs(
            occupied.T @ overlap @ occupied - np.eye(occupied.shape[1])
        ).max()
        if deviation > 1e-6:
            emit(
                {
                    "error": (
                        "supplied orbitals are not orthonormal in this basis "
                        f"(max deviation {deviation:.2e}); the AO convention of "
                        "the file does not match this vibe-qc build"
                    )
                }
            )
            return
        n_occ = int(occupied.shape[1])
    else:
        emit({"note": f"no orbitals supplied; re-running SCF in {basis.name}"})
        options = RHFOptions()
        options.max_iter = 100
        scf = run_rhf(molecule, basis, options)
        if not scf.converged:
            emit({"error": "SCF did not converge in the re-localization worker"})
            return
        n_occ = molecule.n_electrons() // 2
        coefficients = np.asarray(scf.mo_coeffs)
        if coefficients.shape[0] != basis.nbasis:
            coefficients = coefficients.T
        occupied = coefficients[:, :n_occ]

    emit({"note": f"localizing {n_occ} occupied orbitals ({method})"})
    analysis = analyse_localization(molecule, basis, occupied, method=method)

    emit(
        {
            "done": True,
            "method": method,
            "n_occ": int(n_occ),
            "n_ao": int(basis.nbasis),
            # Row-per-orbital, matching the QVF wavefunction.gto convention.
            "coefficients": np.ascontiguousarray(analysis.coefficients.T).tolist(),
            "atom_populations": analysis.atom_populations.tolist(),
            "centroids_bohr": analysis.centroids.tolist(),
            "n_centres": [int(v) for v in analysis.n_centres],
            "charges": analysis.charges.tolist(),
            "reference_basis": analysis.reference_basis,
        }
    )


def _main(argv: list[str]) -> int:
    def emit(event: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()

    if "--probe" in argv:
        emit(probe())
        return 0

    raw = sys.stdin.read()
    try:
        request = json.loads(raw)
    except (ValueError, TypeError) as exc:
        emit({"error": f"malformed request: {exc}"})
        return 1

    try:
        _run_relocalization(request, emit)
    except Exception as exc:
        emit({"error": f"{type(exc).__name__}: {exc}"})
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(_main(sys.argv[1:]))
