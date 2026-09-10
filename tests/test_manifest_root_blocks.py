"""Manifest-root blocks: ``dipole_moment`` and ``thermochemistry``.

QVF spec § 4.7 puts both at the **manifest root**, and the schema declares
them as root properties. vibe-view read them off ``provenance`` instead, with
key names that did not match the producer either, so the dipole arrow and
every thermochemistry line were dead code for every file ever written.

Nothing caught it because no test round-tripped a real run: the viewer-side
tests fabricated their own manifests, and a fabricated manifest agreed with
whatever the reader happened to expect. So these tests deliberately go
through ``vibeqc.run_job`` rather than construct a manifest by hand.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from vibeview.qvf import QVFReader

vibeqc = pytest.importorskip("vibeqc", reason="needs vibe-qc to produce a real QVF")

ANGSTROM_TO_BOHR = 1.8897261254578281


@pytest.fixture(scope="module")
def water_with_thermo(tmp_path_factory) -> str:
    """H2O/STO-3G with a Hessian, so thermochemistry is actually present."""
    tmp_path = tmp_path_factory.mktemp("thermo")
    molecule = vibeqc.Molecule(
        [
            vibeqc.Atom(8, [0.0, 0.0, 0.0]),
            vibeqc.Atom(1, [0.0, 0.7576 * ANGSTROM_TO_BOHR, 0.5865 * ANGSTROM_TO_BOHR]),
            vibeqc.Atom(1, [0.0, -0.7576 * ANGSTROM_TO_BOHR, 0.5865 * ANGSTROM_TO_BOHR]),
        ],
        charge=0,
        multiplicity=1,
    )
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        vibeqc.run_job(
            molecule=molecule,
            basis="sto-3g",
            method="rhf",
            hessian=True,
            localize=False,
        )
    finally:
        os.chdir(cwd)
    return str(next(Path(tmp_path).glob("*.qvf")))


def test_producer_writes_these_at_root_not_in_provenance(water_with_thermo):
    """Pin the contract both sides must agree on."""
    with zipfile.ZipFile(water_with_thermo) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert "dipole_moment" in manifest
    assert "thermochemistry" in manifest
    provenance = manifest.get("provenance") or {}
    assert "dipole_moment" not in provenance
    assert "thermochemistry" not in provenance


def test_reader_finds_the_dipole(water_with_thermo):
    dipole = QVFReader(water_with_thermo).dipole_moment
    assert dipole, "dipole_moment came back empty -- reading the wrong level?"
    # RHF/STO-3G water is around 1.7 D (experiment 1.85).
    assert 1.0 < float(dipole["total_debye"]) < 2.5
    assert len(dipole["vector_debye"]) == 3


def test_reader_finds_thermochemistry_with_the_producer_key_names(water_with_thermo):
    thermo = QVFReader(water_with_thermo).thermochemistry
    assert thermo, "thermochemistry came back empty -- reading the wrong level?"
    for key in (
        "zpve_eh",
        "enthalpy_eh",
        "entropy_cal_mol_k",
        "gibbs_free_energy_eh",
        "temperature_k",
    ):
        assert key in thermo, f"producer key {key!r} missing"
    # The keys vibe-view used to look for never existed.
    assert "entropy_eh_per_k" not in thermo
    assert "gibbs_eh" not in thermo


def test_entropy_is_cal_per_mol_per_kelvin_not_hartree(water_with_thermo):
    """The unit label was wrong as well as the key.

    Water's standard molar entropy is ~45 cal/mol/K. In Eh/K the same number
    would be ~7e-5, so the magnitude alone settles which unit this is -- and
    a mislabelled axis here is worth catching, since it is off by a factor of
    roughly 630,000.
    """
    thermo = QVFReader(water_with_thermo).thermochemistry
    entropy = float(thermo["entropy_cal_mol_k"])
    assert 40.0 < entropy < 55.0, entropy


def test_the_info_panel_actually_renders_them(water_with_thermo):
    """The end the user sees. Every one of these lines was previously absent."""
    from vibeview.app import _run_info_text

    text = _run_info_text(QVFReader(water_with_thermo))
    assert "Dipole" in text
    assert "ZPVE" in text
    assert "cal/mol/K" in text
    # Thermochemistry is meaningless without the state it was evaluated at.
    assert "298.15 K" in text


def test_blocks_are_empty_not_raising_when_absent(tmp_path):
    """A structure-only archive has neither block; the accessors must return
    {} rather than blow up the info panel."""
    molecule = vibeqc.Molecule(
        [vibeqc.Atom(1, [0.0, 0.0, 0.0]), vibeqc.Atom(1, [0.0, 0.0, 1.4])],
        charge=0,
        multiplicity=1,
    )
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        vibeqc.run_job(
            molecule=molecule, basis="sto-3g", method="rhf", localize=False
        )
    finally:
        os.chdir(cwd)
    reader = QVFReader(str(next(Path(tmp_path).glob("*.qvf"))))
    # H2 has no dipole by symmetry and no Hessian was run.
    assert reader.thermochemistry == {}
    assert isinstance(reader.dipole_moment, dict)
