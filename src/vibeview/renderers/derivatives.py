"""Renderers for the ``forces`` and ``hessian`` kinds (#159).

``forces`` (QVF spec § 5.10) stores the energy gradient ∂E/∂R per atom; the
force is its negative, F = −∇E, and every surface shows F. The browser draws
it as arrows from the atoms, the vibration arrows' geometry scaled so the
largest force is 0.6 Å long (the vibration-arrow convention), and lists each
atom's components.

``hessian`` (§ 5.11) stores the Cartesian second derivatives. Its harmonic
frequencies are the eigenvalues λ of the mass-weighted matrix
H_ij / √(m_a m_b), as wavenumbers ν̃ = sign(λ)·√|λ|·
:data:`~vibeview.units.HESSIAN_EIGENVALUE_TO_CM1` (Wilson, Decius & Cross,
*Molecular Vibrations*, 1955, ch. 2); a negative eigenvalue is an imaginary
mode, shown negative as in ``vibrations``. Masses come from the file's
``atom_masses_amu``, else from standard atomic masses when every element has
one; otherwise there are no frequencies, and the panel says why.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from vibeview.renderers import BaseRenderer
from vibeview.units import GPA_PER_HARTREE_BOHR3, HESSIAN_EIGENVALUE_TO_CM1

if TYPE_CHECKING:
    import pyvista as pv

    from vibeview.qvf import ForcesData, HessianData, QVFReader, Section

#: Actor-name prefix of the force arrows (``vibeview.app.scene._Actors.FORCE``).
FORCE_PREFIX = "force_arrow_"
FORCE_ARROW_COLOR = "#e53935"
_UNIT = "Eh/bohr"


# ── forces ──────────────────────────────────────────────────────────────────


def _label(symbols: list[str] | None, index: int) -> str:
    if symbols is not None and 0 <= index < len(symbols):
        return f"{symbols[index]}{index + 1}"
    return f"atom {index + 1}"


def pressure_gpa(data: ForcesData) -> float | None:
    """p = −tr σ / 3 in GPa, or ``None`` without a stress tensor (§ 5.10)."""
    if data.stress is None:
        return None
    return float(-np.trace(data.stress) / 3.0 * GPA_PER_HARTREE_BOHR3)


def forces_summary(data: ForcesData, symbols: list[str] | None = None) -> str:
    """``largest |F| 0.0025 Eh/bohr on O1, RMS 0.0017 Eh/bohr`` (+ pressure)."""
    magnitudes = np.linalg.norm(data.forces, axis=1)
    if not len(magnitudes):
        return "no atoms"
    strongest = int(np.argmax(magnitudes))
    rms = float(np.sqrt(np.mean(np.sum(data.forces**2, axis=1))))
    text = (
        f"largest |F| {magnitudes[strongest]:.4g} {_UNIT} on {_label(symbols, strongest)}, "
        f"RMS {rms:.4g} {_UNIT}"
    )
    pressure = pressure_gpa(data)
    if pressure is not None:
        text += f", pressure {pressure:.4g} GPa"
    return text


def forces_rows(data: ForcesData, symbols: list[str] | None = None) -> list[list[Any]]:
    """``[atom, element, fx, fy, fz, |F|]`` per atom, in hartree/bohr."""
    rows = []
    for index, force in enumerate(data.forces):
        element = symbols[index] if symbols is not None and index < len(symbols) else ""
        rows.append([
            index + 1, element,
            # + 0.0 turns the -0.0 that negating a zero gradient leaves into 0.0.
            *[round(float(v), 6) + 0.0 for v in force],
            round(float(np.linalg.norm(force)), 6),
        ])
    return rows


FORCE_COLUMNS = ["atom", "element", "fx_Eh/bohr", "fy_Eh/bohr", "fz_Eh/bohr", "|F|_Eh/bohr"]


def forces_html(data: ForcesData, symbols: list[str] | None = None) -> str:
    """The details panel: the summary and every atom's force."""
    from vibeview.renderers._html import data_table, note, panel_document

    rows = forces_rows(data, symbols)
    table = data_table(
        ["Atom", "Fx", "Fy", "Fz", "|F|"],
        [[_label(symbols, r[0] - 1), *r[2:]] for r in rows],
        caption=f"Forces F = −∇E ({_UNIT})",
        numeric_format=".5f",
    )
    method = f" Method: {data.method}." if data.method else ""
    return panel_document(
        note(f"{forces_summary(data, symbols)}.{method} The file stores the energy "
             "gradient; the forces shown are its negative.") + table
    )


def add_force_arrows(
    plotter: pv.Plotter,
    positions: np.ndarray,
    data: ForcesData,
    *,
    length: float = 1.0,
    cutoff: float = 0.01,
    atom_radii: np.ndarray | None = None,
) -> list[str]:
    """Arrows along F from each atom; the largest is 0.6 Å × ``length``."""
    from vibeview.vibration_arrows import add_mode_arrows

    if not np.any(data.forces):
        return []
    return add_mode_arrows(
        plotter, np.asarray(positions, dtype=float), data.forces, amplitude=length,
        cutoff=cutoff, color=FORCE_ARROW_COLOR, thickness=0.03, atom_radii=atom_radii,
        prefix=FORCE_PREFIX,
    )


# ── hessian ─────────────────────────────────────────────────────────────────


def hessian_masses(
    data: HessianData, atomic_numbers: list[int]
) -> tuple[np.ndarray | None, str]:
    """The masses in amu for the frequencies, and where they came from.

    ``(None, reason)`` when the file gives none and an element has no
    tabulated mass.
    """
    from vibeview.symmetry import standard_mass

    if data.atom_masses_amu is not None:
        return np.asarray(data.atom_masses_amu, dtype=float), "the file's atom masses"
    masses = [standard_mass(z) for z in atomic_numbers]
    missing = sorted({z for z, m in zip(atomic_numbers, masses, strict=True) if m is None})
    if missing:
        return None, (
            "the file gives no atom_masses_amu and atomic number(s) "
            f"{', '.join(map(str, missing))} have no tabulated mass"
        )
    return np.asarray(masses, dtype=float), "standard atomic masses"


def hessian_frequencies(
    data: HessianData, atomic_numbers: list[int]
) -> tuple[np.ndarray | None, np.ndarray | None, str]:
    """``(wavenumbers cm⁻¹, eigenvalues, note)``, ascending; ``None`` without masses."""
    masses, source = hessian_masses(data, atomic_numbers)
    if masses is None:
        return None, None, source
    matrix = np.asarray(data.matrix, dtype=float)
    if not data.mass_weighted:
        weights = np.repeat(masses, 3)
        matrix = matrix / np.sqrt(np.outer(weights, weights))
    eigenvalues = np.linalg.eigvalsh(0.5 * (matrix + matrix.T))
    wavenumbers = np.sign(eigenvalues) * np.sqrt(np.abs(eigenvalues)) * HESSIAN_EIGENVALUE_TO_CM1
    return wavenumbers, eigenvalues, source


HESSIAN_COLUMNS = ["mode", "frequency_cm-1", "eigenvalue_Eh/(bohr2·amu)"]


def hessian_rows(data: HessianData, atomic_numbers: list[int]) -> list[list[Any]]:
    """``[mode, frequency, eigenvalue]`` per mode; empty without masses."""
    wavenumbers, eigenvalues, _source = hessian_frequencies(data, atomic_numbers)
    if wavenumbers is None or eigenvalues is None:
        return []
    return [
        [i + 1, round(float(w), 2) + 0.0, float(f"{e:.6g}") + 0.0]
        for i, (w, e) in enumerate(zip(wavenumbers, eigenvalues, strict=True))
    ]


def hessian_html(
    data: HessianData, atomic_numbers: list[int], vibrations_ids: list[str] | None = None,
) -> str:
    """The details panel: the matrix, its flags, and the frequencies."""
    from vibeview.renderers._html import data_table, kv_table, note, panel_document

    n = data.matrix.shape[0]
    wavenumbers, _eigenvalues, source = hessian_frequencies(data, atomic_numbers)
    facts = kv_table([
        ("Matrix", f"{n} × {n} ({n // 3} atoms of {data.structure_id})"),
        ("Mass-weighted", "yes" if data.mass_weighted else "no"),
        ("Rigid-body motion projected out", "yes" if data.projected else "no"),
        ("Masses", source if wavenumbers is not None else "none"),
        ("Named by vibrations", ", ".join(vibrations_ids) if vibrations_ids else None),
    ])
    if wavenumbers is None:
        return panel_document(facts + note(f"No frequencies: {source}."))
    caveat = "" if data.projected else note(
        "The matrix is not projected, so the translations and rotations appear "
        "as modes near zero rather than exactly zero."
    )
    table = data_table(
        ["Mode", "ν̃ (cm⁻¹)", "Eigenvalue (Eh/(bohr²·amu))"],
        hessian_rows(data, atomic_numbers),
        caption="Harmonic frequencies from the Hessian (negative: imaginary)",
        numeric_format=".4g",
    )
    return panel_document(facts + caveat + table)


# ── renderer classes ────────────────────────────────────────────────────────


class ForcesRenderer(BaseRenderer):
    """Force arrows and table for a ``forces`` section."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: ForcesData | None = None

    def load(self) -> ForcesData:
        if self._data is None:
            self._data = self.reader.read_forces(self.section_id)
        return self._data


class HessianRenderer(BaseRenderer):
    """Frequency table for a ``hessian`` section."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: HessianData | None = None

    def load(self) -> HessianData:
        if self._data is None:
            self._data = self.reader.read_hessian(self.section_id)
        return self._data
