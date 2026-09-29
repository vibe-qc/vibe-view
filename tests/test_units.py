"""Independent physical reference checks for shared conversion factors."""

from __future__ import annotations

import subprocess
import sys

import pytest

from vibeview.units import (
    ANGSTROM_TO_BOHR,
    BOHR_TO_ANGSTROM,
    EV_NM,
    EV_TO_CM1,
    GPA_A3_PER_EV,
    HARTREE_TO_EV,
    HARTREE_TO_KCAL_MOL,
)


def test_codata_2018_length_and_energy() -> None:
    # NIST 2018: a0 in metres, Eh in joules, exact SI elementary charge.
    assert pytest.approx(5.29177210903e-11, rel=1e-12, abs=0) == BOHR_TO_ANGSTROM * 1e-10
    assert pytest.approx(1.8897261246257702, rel=1e-12, abs=0) == ANGSTROM_TO_BOHR
    assert pytest.approx(4.3597447222071e-18, rel=1e-12, abs=0) == HARTREE_TO_EV * 1.602176634e-19
    # Thermochemical calorie, exact Avogadro constant and CODATA Eh.
    expected_kcal = 4.3597447222071e-18 * 6.02214076e23 / 4184
    assert pytest.approx(expected_kcal, rel=2e-10, abs=0) == HARTREE_TO_KCAL_MOL


def test_spectral_and_pressure_factors_from_exact_si() -> None:
    hc = 6.62607015e-34 * 299792458
    assert pytest.approx(1.602176634e-19 / hc / 100, rel=1e-10, abs=0) == EV_TO_CM1
    assert pytest.approx(hc / 1.602176634e-19 * 1e9, rel=1e-9, abs=0) == EV_NM
    assert pytest.approx(1.602176634e-19 / 1e-30 / 1e9, rel=1e-14, abs=0) == GPA_A3_PER_EV


def test_units_import_needs_no_rendering_or_optional_dependencies() -> None:
    result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; import vibeview.units; "
            "assert not {'vtk', 'pyvista', 'trame', 'matplotlib', 'vibeqc'} & sys.modules.keys()"
        )],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
