"""Viewer-side re-localization (shelling out to vibe-qc).

Split the same way ``test_live_opt.py`` splits: the viewer half is tested
against a canned-JSON fake worker so it needs no vibe-qc, and the worker half
is tested for real but skipped when vibe-qc is not importable.

The load-bearing test here is
``test_worker_refuses_orbitals_that_are_not_orthonormal``. Passing canonical
coefficients straight from a QVF into a basis rebuilt by name assumes the two
share an AO ordering. That is true today and pinned on the vibe-qc side, but
if it ever stops being true the failure mode is plausible, wrong orbitals
rather than a crash -- so the worker checks rather than trusts.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from vibeview.relocalize import (
    METHODS,
    build_request,
    parse_event,
    probe,
    request_from_reader,
    run_relocalization,
)

_HAS_VIBEQC = probe().get("available", False)
_needs_vibeqc = pytest.mark.skipif(
    not _HAS_VIBEQC, reason="vibe-qc not importable in this interpreter"
)


def _localized_qvf(tmp_path: Path) -> str:
    """A small real QVF with a canonical wavefunction and provenance.basis.

    Built by running vibe-qc rather than hand-assembled: the whole feature
    turns on ``provenance.basis`` actually being written, so a fixture that
    fabricates it would test the wrong thing.
    """
    import os

    import vibeqc as vq

    angstrom = 1.8897261254578281
    molecule = vq.Molecule(
        [
            vq.Atom(8, [0.0, 0.0, 0.0]),
            vq.Atom(1, [0.0, 0.7933 * angstrom, -0.6135 * angstrom]),
            vq.Atom(1, [0.0, -0.7933 * angstrom, -0.6135 * angstrom]),
        ],
        charge=0,
        multiplicity=1,
    )
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        vq.run_job(molecule=molecule, basis="sto-3g", method="rhf")
    finally:
        os.chdir(cwd)
    return str(next(Path(tmp_path).glob("*.qvf")))


# ── viewer half: no vibe-qc needed ───────────────────────────────────────


def _fake_worker(tmp_path: Path, body: str) -> list[str]:
    """Write a stub worker script; returns a worker_cmd for the runner."""
    script = tmp_path / "fake_relocalize_worker.py"
    script.write_text(body)
    return [sys.executable, str(script)]


def test_build_request_round_trips_through_json():
    request = build_request(
        [6, 1], [[0.0, 0.0, 0.0], [0.0, 0.0, 2.0]], "def2-svp", "ibo"
    )
    decoded = json.loads(json.dumps(request))
    assert decoded["numbers"] == [6, 1]
    assert decoded["basis"] == "def2-svp"
    assert decoded["method"] == "ibo"
    assert decoded["charge"] == 0
    assert "occupied_coefficients" not in decoded


def test_build_request_carries_supplied_orbitals():
    request = build_request(
        [1], [[0.0, 0.0, 0.0]], "sto-3g", "boys",
        occupied_coefficients=np.eye(2),
    )
    assert request["occupied_coefficients"] == [[1.0, 0.0], [0.0, 1.0]]


@pytest.mark.parametrize("line", ["", "   ", "not json", "[1, 2]", "null"])
def test_parse_event_rejects_noise(line):
    assert parse_event(line) is None


def test_parse_event_accepts_bytes_and_dicts():
    assert parse_event(b'{"note": "hi"}\n') == {"note": "hi"}


def test_run_relocalization_returns_the_done_event(tmp_path):
    cmd = _fake_worker(
        tmp_path,
        "import sys, json\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'note': 'working'}), flush=True)\n"
        "print(json.dumps({'done': True, 'method': 'ibo', 'n_occ': 1,\n"
        "                  'n_ao': 2, 'coefficients': [[1.0, 0.0]]}), flush=True)\n",
    )
    notes: list[str] = []
    result = asyncio.run(
        run_relocalization({"method": "ibo"}, worker_cmd=cmd, on_note=notes.append)
    )
    assert result["done"] is True
    assert result["coefficients"] == [[1.0, 0.0]]
    assert notes == ["working"]


def test_run_relocalization_surfaces_a_worker_error(tmp_path):
    cmd = _fake_worker(
        tmp_path,
        "import sys, json\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'error': 'basis not found'}), flush=True)\n",
    )
    result = asyncio.run(run_relocalization({}, worker_cmd=cmd))
    assert result["error"] == "basis not found"


def test_run_relocalization_reports_a_silent_worker(tmp_path):
    cmd = _fake_worker(tmp_path, "import sys\nsys.stdin.read()\n")
    result = asyncio.run(run_relocalization({}, worker_cmd=cmd))
    assert "without a result" in result["error"]


def test_run_relocalization_times_out_rather_than_hanging(tmp_path):
    cmd = _fake_worker(tmp_path, "import sys, time\nsys.stdin.read()\ntime.sleep(30)\n")
    result = asyncio.run(
        run_relocalization({}, worker_cmd=cmd, timeout_s=1.0)
    )
    assert "timed out" in result["error"]


def test_run_relocalization_reports_an_unlaunchable_worker():
    result = asyncio.run(
        run_relocalization({}, worker_cmd=["/nonexistent/interpreter"])
    )
    assert "could not start worker" in result["error"]


class _FakeReader:
    """Minimal QVFReader stand-in for request_from_reader."""

    def __init__(self, provenance, atoms=None):
        self.provenance = provenance
        self._atoms = atoms

    def read_structure(self):
        if self._atoms is None:
            raise RuntimeError("no structure")

        class _S:
            atoms = self._atoms

        return _S()

    def read_wavefunction_gto(self, section_id):
        raise RuntimeError("no wavefunction")


class _FakeAtom:
    def __init__(self, z, position):
        self.atomic_number = z
        self.position = position


def test_request_from_reader_needs_the_basis_name():
    """A file that does not record provenance.basis cannot be re-localized;
    the caller must be able to tell that apart from a failure."""
    reader = _FakeReader({}, [_FakeAtom(1, [0.0, 0.0, 0.0])])
    assert request_from_reader(reader, "ibo") is None


def test_request_from_reader_converts_angstrom_to_bohr():
    reader = _FakeReader(
        {"basis": "sto-3g", "charge": 0, "multiplicity": 1},
        [_FakeAtom(1, [1.0, 0.0, 0.0])],
    )
    request = request_from_reader(reader, "ibo")
    assert request is not None
    assert request["positions_bohr"][0][0] == pytest.approx(1.8897261254578281)


def test_request_from_reader_survives_a_missing_wavefunction():
    """No canonical section is not fatal -- the worker falls back to an SCF."""
    reader = _FakeReader(
        {"basis": "sto-3g"}, [_FakeAtom(1, [0.0, 0.0, 0.0])]
    )
    request = request_from_reader(reader, "ibo")
    assert request is not None
    assert "occupied_coefficients" not in request


# ── worker half: needs vibe-qc ───────────────────────────────────────────


@_needs_vibeqc
def test_probe_lists_the_criteria_the_viewer_offers():
    result = probe()
    assert result["available"] is True
    assert set(result["methods"]) == set(METHODS)


@_needs_vibeqc
def test_worker_localizes_h2o_and_finds_the_textbook_pattern():
    """H2O/STO-3G: one O core, two O-H bonds, two lone pairs."""
    from vibeview.relocalize import _run_relocalization

    angstrom = 1.8897261254578281
    request = build_request(
        [8, 1, 1],
        [
            [0.0, 0.0, 0.0],
            [0.0, 0.7933 * angstrom, -0.6135 * angstrom],
            [0.0, -0.7933 * angstrom, -0.6135 * angstrom],
        ],
        "sto-3g",
        "ibo",
    )
    events: list[dict] = []
    _run_relocalization(request, events.append)
    done = [e for e in events if e.get("done")]
    assert done, [e for e in events if "error" in e]
    result = done[0]
    assert result["n_occ"] == 5
    assert len(result["coefficients"]) == 5
    # One single-centre orbital is the oxygen core; the lone pairs sit on
    # oxygen too, so what must hold is that no orbital is spread over more
    # than two centres and the O-H bonds are two-centre.
    assert max(result["n_centres"]) <= 2
    assert sum(1 for c in result["n_centres"] if c == 2) == 2


@_needs_vibeqc
def test_worker_refuses_orbitals_that_are_not_orthonormal():
    """The safety net for the one assumption the fast path makes.

    Supplied coefficients are contracted against a basis rebuilt by name.
    If the AO conventions ever diverge the arithmetic still runs and returns
    plausible garbage, so the worker checks C^T S C = I first.
    """
    from vibeview.relocalize import _run_relocalization

    request = build_request(
        [1, 1],
        [[0.0, 0.0, 0.0], [0.0, 0.0, 1.4]],
        "sto-3g",
        "ibo",
        occupied_coefficients=[[1.0, 1.0]],  # deliberately unnormalised
    )
    events: list[dict] = []
    _run_relocalization(request, events.append)
    errors = [e["error"] for e in events if "error" in e]
    assert errors and "not orthonormal" in errors[0]


@_needs_vibeqc
def test_worker_rejects_an_ao_count_mismatch():
    from vibeview.relocalize import _run_relocalization

    request = build_request(
        [1, 1],
        [[0.0, 0.0, 0.0], [0.0, 0.0, 1.4]],
        "sto-3g",
        "ibo",
        occupied_coefficients=[[1.0, 0.0, 0.0, 0.0]],  # 4 AOs, basis has 2
    )
    events: list[dict] = []
    _run_relocalization(request, events.append)
    errors = [e["error"] for e in events if "error" in e]
    assert errors and "AO rows" in errors[0]


@_needs_vibeqc
def test_overlay_makes_a_computed_wavefunction_behave_like_an_archived_one(
    tmp_path,
):
    """The whole point of the reader overlay: once registered, every existing
    consumer -- sidebar, picker, isosurface evaluator -- works unchanged."""
    import asyncio as _asyncio

    from vibeview.app import _sidebar_section_title
    from vibeview.qvf import QVFReader
    from vibeview.relocalize import (
        overlay_section_id,
        run_relocalization,
        wavefunction_from_result,
    )
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    source = _localized_qvf(tmp_path)
    reader = QVFReader(source)
    before = {s.id for s in reader.sections}

    request = request_from_reader(reader, "boys")
    result = _asyncio.run(run_relocalization(request))
    assert "error" not in result, result

    section_id = overlay_section_id("boys")
    reader.set_wavefunction_overlay(
        section_id,
        wavefunction_from_result(result, reader.read_wavefunction_gto("wf")),
    )

    assert section_id in {s.id for s in reader.sections}
    assert _sidebar_section_title(
        {"id": section_id, "kind": "wavefunction.gto"}
    ) == "Re-localized (Boys)"

    section = next(s for s in reader.sections if s.id == section_id)
    renderer = WavefunctionRenderer(section, reader)
    wavefunction = renderer.load()
    assert wavefunction.orbital_kind == "localized"
    assert wavefunction.localization_method == "boys"
    # No fabricated eigenvalue spectrum.
    assert np.allclose(wavefunction.energies, 0.0)
    # The picker labels by composition, not by a meaningless energy.
    titles = [row["title"] for row in renderer.mo_table()]
    assert titles and all("Eh" not in t for t in titles)
    # And it actually renders on a grid.
    _grid, values = renderer.evaluate_mo(0)
    assert values.shape == (60, 60, 60)
    assert np.abs(values).max() > 0

    reader.clear_wavefunction_overlays()
    assert {s.id for s in reader.sections} == before


@_needs_vibeqc
def test_overlay_replaces_rather_than_accumulates(tmp_path):
    """Re-running a criterion must not add a second sidebar row."""
    from vibeview.qvf import QVFReader, WavefunctionGTOData

    reader = QVFReader(_localized_qvf(tmp_path))
    canonical = reader.read_wavefunction_gto("wf")

    def _stub(n_mo):
        return WavefunctionGTOData(
            structure_ref=canonical.structure_ref,
            pure=canonical.pure,
            n_ao=canonical.n_ao,
            shells=canonical.shells,
            spin="restricted",
            orbital_kind="localized",
            energies=np.zeros(n_mo),
            occupations=np.full(n_mo, 2.0),
            symmetry_labels=None,
            alpha_energies=None,
            alpha_occupations=None,
            beta_energies=None,
            beta_occupations=None,
            mo_coefficients=np.zeros((n_mo, canonical.n_ao)),
            mo_coefficients_alpha=None,
            mo_coefficients_beta=None,
        )

    reader.set_wavefunction_overlay("wf_relocalized_ibo", _stub(3))
    count = sum(1 for s in reader.sections if s.id == "wf_relocalized_ibo")
    reader.set_wavefunction_overlay("wf_relocalized_ibo", _stub(5))
    assert sum(1 for s in reader.sections if s.id == "wf_relocalized_ibo") == count == 1
    # The payload is replaced, not merged.
    assert reader.read_wavefunction_gto("wf_relocalized_ibo").mo_coefficients.shape[0] == 5


@_needs_vibeqc
def test_worker_refuses_open_shell():
    from vibeview.relocalize import _run_relocalization

    request = build_request(
        [1], [[0.0, 0.0, 0.0]], "sto-3g", "ibo", multiplicity=2
    )
    events: list[dict] = []
    _run_relocalization(request, events.append)
    errors = [e["error"] for e in events if "error" in e]
    assert errors and "closed-shell" in errors[0]
