"""Lightweight conversion factors shared by every viewer surface.

Length and Hartree factors retain the CODATA 2018 convention used by QVF
consumers; updating the CODATA edition is a separate numerical change.
"""

from __future__ import annotations

from typing import Final

# NIST, 2018 CODATA adjustment, atomic unit of length and Hartree/eV:
# https://physics.nist.gov/cuu/Constants/ArchiveASCII/allascii_2018.txt
BOHR_TO_ANGSTROM: Final[float] = 0.529177210903
ANGSTROM_TO_BOHR: Final[float] = 1.0 / BOHR_TO_ANGSTROM
HARTREE_TO_EV: Final[float] = 27.211386245988
# Thermochemical kcal = 4184 J; CODATA Hartree * Avogadro / 4184,
# rounded to the precision already used by the terminal energy plots.
HARTREE_TO_KCAL_MOL: Final[float] = 627.509474
# Derived from exact SI h, c and elementary charge (same NIST table).
EV_TO_CM1: Final[float] = 8065.543937
EV_NM: Final[float] = 1239.841984
# 1 eV / angstrom**3 = 160.2176634 GPa (exact SI elementary charge).
GPA_A3_PER_EV: Final[float] = 160.2176634
# Harmonic wavenumber of a mass-weighted Hessian eigenvalue in
# hartree/(bohr² · u): sqrt(E_h / (a0² · u)) / (2π c), from the same CODATA
# 2018 table (E_h = 4.3597447222071e-18 J, a0 = 5.29177210903e-11 m,
# u = 1.66053906660e-27 kg, c = 299792458 m/s), about 5140.48 cm⁻¹ (#159).
HESSIAN_EIGENVALUE_TO_CM1: Final[float] = (
    (4.3597447222071e-18 / (5.29177210903e-11**2 * 1.66053906660e-27)) ** 0.5
    / (2 * 3.141592653589793 * 2.99792458e10)
)
# 1 hartree / bohr³ in GPa: E_h / a0³ (same table), for a stress tensor (#159).
GPA_PER_HARTREE_BOHR3: Final[float] = 4.3597447222071e-18 / 5.29177210903e-11**3 / 1e9


# ── Display units ─────────────────────────────────────────────────────
# Stored data stays in its archive unit; these only change what a surface
# shows. Each entry is (label, factor from Hartree, signed-format digits).
ENERGY_UNITS: Final[dict[str, tuple[str, float, int]]] = {
    "hartree": ("Eh", 1.0, 3),
    "ev": ("eV", HARTREE_TO_EV, 2),
    "kcal_mol": ("kcal/mol", HARTREE_TO_KCAL_MOL, 1),
}
DEFAULT_ENERGY_UNIT: Final[str] = "hartree"


def energy_unit(unit: object) -> str:
    """Return ``unit`` if it names a display energy unit, else the default.

    Settings and browser state arrive as untyped JSON, so an unknown or
    missing value falls back to Hartree rather than raising.
    """
    return unit if isinstance(unit, str) and unit in ENERGY_UNITS else DEFAULT_ENERGY_UNIT


def energy_label(unit: object) -> str:
    """Short axis and picker label for a display energy unit, e.g. ``"eV"``."""
    return ENERGY_UNITS[energy_unit(unit)][0]


def energy_factor(unit: object) -> float:
    """Factor that turns a Hartree energy into the display ``unit``."""
    return ENERGY_UNITS[energy_unit(unit)][1]


def energy_from_hartree(value_eh: float, unit: object) -> float:
    """Convert an energy in Hartree to the display ``unit``."""
    return value_eh * energy_factor(unit)


def format_energy(value_eh: float, unit: object, *, extra_digits: int = 0) -> str:
    """Format a Hartree energy for display, e.g. ``"-0.314 Eh"`` or ``"-8.55 eV"``.

    The precision is fixed per unit so one orbital list reads alike in every
    unit; ``extra_digits`` adds places for hover text.
    """
    label, factor, digits = ENERGY_UNITS[energy_unit(unit)]
    return f"{value_eh * factor:+.{digits + extra_digits}f} {label}"


# Lengths: structures store Angstrom (QVF design § 1.3a). Each entry is
# (label, factor from Angstrom). The settings key is the existing
# ``measure_unit``.
LENGTH_UNITS: Final[dict[str, tuple[str, float]]] = {
    "angstrom": ("Å", 1.0),
    "bohr": ("bohr", ANGSTROM_TO_BOHR),
}
DEFAULT_LENGTH_UNIT: Final[str] = "angstrom"


def length_unit(unit: object) -> str:
    """Return ``unit`` if it names a display length unit, else Angstrom.

    Like :func:`energy_unit`, an unknown or missing value falls back to the
    default rather than raising.
    """
    return unit if isinstance(unit, str) and unit in LENGTH_UNITS else DEFAULT_LENGTH_UNIT


def length_label(unit: object) -> str:
    """Short label for a display length unit, e.g. ``"Å"`` or ``"bohr"``."""
    return LENGTH_UNITS[length_unit(unit)][0]


def length_from_angstrom(value_ang: float, unit: object) -> float:
    """Convert a length in Angstrom to the display ``unit``."""
    return value_ang * LENGTH_UNITS[length_unit(unit)][1]


def format_length(value_ang: float, unit: object, *, digits: int = 3) -> str:
    """Format an Angstrom length for display, e.g. ``"0.958 Å"`` or ``"1.810 bohr"``."""
    return f"{length_from_angstrom(value_ang, unit):.{digits}f} {length_label(unit)}"


# Band-structure and DOS energies are stored in eV (QVF §4.3), and their
# axes have their own setting, ``band_energy_unit``, with eV as the
# default: the solid-state convention, and what those panels always showed.
# Orbital energies keep ``energy_unit`` and its Hartree default.
DEFAULT_BAND_ENERGY_UNIT: Final[str] = "ev"


def band_energy_unit(unit: object) -> str:
    """Return ``unit`` if it names a display energy unit, else eV."""
    return unit if isinstance(unit, str) and unit in ENERGY_UNITS else DEFAULT_BAND_ENERGY_UNIT


def energy_from_ev(value_ev: float, unit: object) -> float:
    """Convert an energy in eV to the display ``unit`` (an ``ENERGY_UNITS`` key)."""
    if band_energy_unit(unit) == "ev":
        return value_ev
    return value_ev / HARTREE_TO_EV * energy_factor(unit)


def energy_to_ev(value: float, unit: object) -> float:
    """Convert an energy in the display ``unit`` back to eV."""
    if band_energy_unit(unit) == "ev":
        return value
    return value / energy_factor(unit) * HARTREE_TO_EV


def band_axis_unit(unit: object) -> tuple[str, float]:
    """``(label, factor from eV)`` for a band or DOS energy axis in ``unit``."""
    key = band_energy_unit(unit)
    return energy_label(key), energy_from_ev(1.0, key)
