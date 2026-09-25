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
