"""Natural transition orbitals read the same from either carrier (spec § 5.3).

vibe-qc wrote NTO sections with ``orbital_kind: "natural"`` and the
``excitation_index`` / ``excitation_energy_ev`` keys, which qvf v0.3.0
deprecates. Its successor writes ``orbital_kind: "nto"``, ``nto_role``,
``state_index`` and ``state_ref``, and the energy lives in the referenced
``excited_states`` section. vibe-view reads the canonical carriers first and
falls back to the deprecated keys, so both archives display as transition
weights of the same state.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from tests._qvf_builders import build_qvf
from tests._qvf_corpus import qvf_corpus_dir, requires_qvf_corpus
from vibeview.qvf import QVFReader
from vibeview.renderers.wavefunction import (
    TRANSITION_WEIGHT,
    describe_transition_orbitals,
    frontier_orbitals,
    occupation_problem,
)

_STRUCTURE = json.dumps(
    {
        "atoms": [
            {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.0, 0.0]},
            {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.0, 0.74]},
        ],
        "pbc": [False, False, False],
    }
).encode()
_BASIS = json.dumps(
    {
        "pure": True,
        "n_ao": 2,
        "shells": [
            {"center": 0, "l": 0, "exponents": [1.0], "coefficients": [1.0]},
            {"center": 1, "l": 0, "exponents": [1.0], "coefficients": [1.0]},
        ],
    }
).encode()
_COEFFS = np.eye(2, dtype=np.float64).tobytes()


def _archive(tmp_path, mo_metadata: dict, *, states: dict | None = None):
    meta = {
        "spin": "restricted",
        "energies": [0.0, 0.0],
        "occupations": [0.9, 0.1],
        **mo_metadata,
    }
    sections = [
        ("structure", "structure", {"structure": ("json", "s.json", _STRUCTURE)}),
        (
            "nto",
            "wavefunction.gto",
            {
                "basis": ("json", "nto/basis.json", _BASIS),
                "mo_metadata": ("json", "nto/mo.json", json.dumps(meta).encode()),
                "mo_coefficients": ("binary", "nto/mo.dat", _COEFFS, "float64", [2, 2]),
            },
        ),
    ]
    if states is not None:
        sections.append(
            ("excited_states", "excited_states",
             {"states": ("json", "es/states.json", json.dumps(states).encode())})
        )
    return build_qvf(tmp_path / "nto.qvf", sections)


_STATES = {
    "states": [
        {"index": 1, "excitation_energy": {"value": 10.25, "units": "ev"}},
        {"index": 2, "excitation_energy": {"value": 0.5, "units": "hartree"}},
    ]
}
_CANONICAL = {
    "orbital_kind": "nto",
    "occupation_semantics": "transition_weight",
    "nto_role": "hole",
    "state_index": 2,
    "state_ref": "excited_states",
}
_DEPRECATED = {"orbital_kind": "natural", "excitation_index": 2, "excitation_energy_ev": 13.6057}


def _read(path):
    with QVFReader(path) as reader:
        return reader.read_wavefunction_gto("nto")


def test_canonical_carriers_name_the_state_and_its_energy(tmp_path):
    wf = _read(_archive(tmp_path, _CANONICAL, states=_STATES))
    assert wf.orbital_kind == "natural"
    assert wf.occupation_semantics == TRANSITION_WEIGHT
    nto = wf.transition_orbitals
    assert (nto.role, nto.state_index, nto.states_ref) == ("hole", 2, "excited_states")
    assert nto.excitation_energy_ev == pytest.approx(0.5 * 27.211386245988)
    assert describe_transition_orbitals(wf) == "state 2, 13.606 eV, hole"


def test_deprecated_keys_read_the_same(tmp_path):
    """vibe-qc's archives so far: ``natural``, mostly without a declared
    occupation meaning; the deprecated keys say these are NTOs."""
    wf = _read(_archive(tmp_path, _DEPRECATED))
    assert wf.orbital_kind == "natural"
    assert wf.occupation_semantics == TRANSITION_WEIGHT
    assert describe_transition_orbitals(wf) == "state 2, 13.606 eV"
    # Shown as transition weights, as a canonical NTO set is.
    occupations = np.asarray(wf.occupations)
    assert frontier_orbitals(occupations, 2, wf.orbital_kind, None, wf.occupation_semantics)[2]
    assert occupation_problem(wf, wf.occupation_semantics, None)


def test_canonical_carriers_win_over_deprecated_keys(tmp_path):
    wf = _read(_archive(tmp_path, {**_DEPRECATED, **_CANONICAL, "state_index": 1},
                        states=_STATES))
    assert wf.transition_orbitals.state_index == 1
    assert wf.transition_orbitals.excitation_energy_ev == pytest.approx(10.25)


@pytest.mark.parametrize("states", [
    None,
    {"states": [{"index": 1, "excitation_energy": {"value": 10.25, "units": "ev"}}]},
    {"states": [{"index": 2, "excitation_energy": {"value": 1.0, "units": "furlong"}}]},
])
def test_a_missing_canonical_energy_falls_back_to_the_deprecated_one(tmp_path, states):
    wf = _read(_archive(tmp_path, {**_CANONICAL, "excitation_energy_ev": 13.6}, states=states))
    assert wf.transition_orbitals.excitation_energy_ev == pytest.approx(13.6)


def test_unlabelled_natural_orbitals_are_not_transition_orbitals(tmp_path):
    wf = _read(_archive(tmp_path, {"orbital_kind": "natural",
                                   "occupation_semantics": "electron_occupation"}))
    assert wf.transition_orbitals is None
    assert wf.occupation_semantics == "electron_occupation"
    assert describe_transition_orbitals(wf) is None


def test_malformed_labels_are_left_out(tmp_path):
    wf = _read(_archive(tmp_path, {"orbital_kind": "nto", "nto_role": "electron",
                                   "state_index": "2", "excitation_energy_ev": "x"}))
    assert describe_transition_orbitals(wf) == "natural transition orbitals"


@requires_qvf_corpus("tddft.qvf", "deprecated_spellings.qvf")
def test_the_qvf_corpus_carriers_read_alike():
    corpus = qvf_corpus_dir()
    assert corpus is not None
    with QVFReader(corpus / "tddft.qvf") as reader:
        hole = reader.read_wavefunction_gto("nto_s1_hole")
        particle = reader.read_wavefunction_gto("nto_s1_particle")
    with QVFReader(corpus / "deprecated_spellings.qvf") as reader:
        legacy = reader.read_wavefunction_gto("wf_nto_S1_hole")
    for wf in (hole, particle, legacy):
        assert wf.orbital_kind == "natural"
        assert wf.occupation_semantics == TRANSITION_WEIGHT
        assert wf.transition_orbitals.state_index == 1
        assert wf.transition_orbitals.excitation_energy_ev is not None
    assert (hole.transition_orbitals.role, particle.transition_orbitals.role) == (
        "hole", "particle",
    )
