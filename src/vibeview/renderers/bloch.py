"""Bloch-orbital renderer for the ``wavefunction.bloch`` kind (#162).

QVF spec § 5.3.1 stores crystalline orbitals as complex coefficients over
Bloch sums of the cell's Gaussian basis. Orbital m of spin channel s at
k-point k is

    ψ_msk(r) = Σ_μ C_ksmμ φ_μk(r),   φ_μk(r) = N^(−½) Σ_T e^{2πi k·n} χ_μ(r − T),

with T = n₁a₁ + n₂a₂ + n₃a₃ running over the periodic axes only and k in
fractional reciprocal-lattice coordinates (Bloch, *Z. Phys.* **52**, 555,
1929). This module evaluates that sum on a grid on demand, as
:mod:`vibeview.renderers.periodic_wavefunction` does at Γ, and reuses its
Gaussian-tail image cutoff and the GTO evaluator's AO values.

* **Grid.** Along a periodic axis the grid spans one cell; along a vacuum
  axis it spans the atoms plus a padding, so a chain or slab is not cut.
* **N.** The number of cells in the Born–von Kármán supercell: the product
  of ``mesh`` along the periodic axes when the k-points come from a mesh,
  else 1, which only rescales the values.
* **Gauge.** An orbital times e^{iθ} is the same state, and the spec forbids
  assuming a gauge, so the real and imaginary parts of one orbital can mix
  from producer to producer; the modulus does not.
* Shells above f are skipped, as in the Γ evaluator; the dropped weight is
  reported.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import product
from typing import TYPE_CHECKING, Any

import numpy as np

from vibeview.qvf import GridData
from vibeview.renderers import BaseRenderer
from vibeview.units import BOHR_TO_ANGSTROM, HARTREE_TO_EV

if TYPE_CHECKING:
    from vibeview.qvf import BasisShell, BlochWavefunctionData, QVFReader, Section

_SPIN_NAMES = ("α", "β")


def bvk_cells(data: BlochWavefunctionData, pbc: list[bool]) -> int:
    """N of the Bloch sum: the mesh's cells along the periodic axes, else 1."""
    if data.mesh is None:
        return 1
    return int(np.prod([m for m, periodic in zip(data.mesh, pbc, strict=True) if periodic]))


def opening_orbital(data: BlochWavefunctionData, kpoint: int = 0, spin: int = 0) -> int:
    """The highest occupied orbital at ``kpoint``, else the first."""
    defined = data.n_mo_per_k[kpoint]
    occupations = data.occupations[kpoint, spin, :defined]
    occupied = np.flatnonzero(occupations > 0.5 * max(float(occupations.max(initial=0.0)), 1e-12))
    return int(occupied[-1]) if occupied.size else 0


def kpoint_label(data: BlochWavefunctionData, kpoint: int) -> str:
    """``X (0.5, 0, 0)``, or just the coordinates for an unlabelled point."""
    coords = "(" + ", ".join(f"{v:g}" for v in data.kpoints[kpoint]) + ")"
    label = data.labels[kpoint] if data.labels is not None else ""
    return f"{label} {coords}" if label else coords


def spin_name(data: BlochWavefunctionData, spin: int) -> str:
    return "" if data.spin != "unrestricted" else _SPIN_NAMES[spin]


def _shell_wavefunction(data: BlochWavefunctionData, shell: BasisShell, width: int) -> Any:
    """A one-shell :class:`~vibeview.qvf.WavefunctionGTOData` for the AO evaluator."""
    from vibeview.qvf import WavefunctionGTOData

    return WavefunctionGTOData(
        structure_ref=data.structure_id, pure=data.pure, n_ao=width,
        shells=[replace(shell, center=0)], spin="restricted", orbital_kind="canonical",
        energies=None, occupations=None, symmetry_labels=None, alpha_energies=None,
        alpha_occupations=None, beta_energies=None, beta_occupations=None,
        mo_coefficients=None, mo_coefficients_alpha=None, mo_coefficients_beta=None,
    )


def bloch_grid(
    lattice_bohr: np.ndarray, pbc: list[bool], positions_bohr: np.ndarray, n: int,
    padding_bohr: float,
) -> tuple[GridData, np.ndarray]:
    """The grid (in Å) and its points (in bohr): one cell along periodic axes."""
    inverse = np.linalg.inv(lattice_bohr)
    fractional = positions_bohr @ inverse
    lo = np.zeros(3)
    span = np.ones(3)
    for axis, periodic in enumerate(pbc):
        if periodic:
            continue
        pad = padding_bohr / float(np.linalg.norm(lattice_bohr[axis]))
        lo[axis] = float(fractional[:, axis].min()) - pad
        span[axis] = float(fractional[:, axis].max()) - lo[axis] + pad
    axes = [lo[i] + np.linspace(0.0, span[i], n) for i in range(3)]
    frac = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1)
    points = (frac @ lattice_bohr).reshape(-1, 3)
    grid = GridData(
        origin=(lo @ lattice_bohr) * BOHR_TO_ANGSTROM,
        voxel_vectors=(lattice_bohr * (span / (n - 1))[:, None]) * BOHR_TO_ANGSTROM,
        shape=(n, n, n),
        units="angstrom",
    )
    return grid, points


def evaluate_bloch_orbital(
    reader: QVFReader,
    section: Section,
    data: BlochWavefunctionData,
    kpoint: int,
    spin: int,
    orbital: int,
    *,
    n_per_dim: int = 40,
    padding_bohr: float = 4.0,
) -> tuple[GridData, np.ndarray, float]:
    """``(grid in Å, complex ψ on it, dropped weight fraction)`` for one orbital."""
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    if not 0 <= kpoint < len(data.kpoints):
        raise ValueError(f"k-point {kpoint} is out of range: there are {len(data.kpoints)}")
    if not 0 <= spin < data.n_spin:
        raise ValueError(f"spin channel {spin} is out of range: there are {data.n_spin}")
    if not 0 <= orbital < data.n_mo_per_k[kpoint]:
        raise ValueError(
            f"orbital {orbital} is out of range: k-point {kpoint} has {data.n_mo_per_k[kpoint]}"
        )
    n = int(n_per_dim)
    if not 2 <= n <= 120:
        raise ValueError("Bloch orbital grid must have 2–120 points per axis")
    structure = reader.read_structure(data.structure_id, apply_edits=False)
    pbc = [bool(p) for p in structure.pbc]
    lattice = np.asarray(structure.lattice_vectors, dtype=float) / BOHR_TO_ANGSTROM
    inverse = np.linalg.inv(lattice)
    positions = np.asarray([a.position for a in structure.atoms], dtype=float) / BOHR_TO_ANGSTROM
    grid, points = bloch_grid(lattice, pbc, positions, n, padding_bohr)
    lo, hi = points.min(axis=0), points.max(axis=0)
    coefficients = data.coefficients[kpoint, spin, orbital]
    kvec = np.asarray(data.kpoints[kpoint], dtype=float)
    evaluator = WavefunctionRenderer(section, reader)
    values = np.zeros(len(points), dtype=complex)
    weight = float(np.sum(np.abs(coefficients) ** 2)) or 1.0
    dropped = 0.0
    index = 0
    for shell in data.shells:
        width = evaluator._n_ao_per_shell(shell)
        block = coefficients[index : index + width]
        index += width
        if len(block) != width:
            raise ValueError("Bloch basis / coefficient-width mismatch")
        if shell.l > 3:
            dropped += float(np.sum(np.abs(block) ** 2))
            continue
        if not np.any(block):
            continue
        if not 0 <= shell.center < len(positions):
            raise ValueError("Bloch shell references an atom outside the structure")
        # The Γ evaluator's conservative Gaussian-tail radius.
        radius = np.sqrt((40.0 + 2 * shell.l) / float(np.min(shell.exponents)))
        center = positions[shell.center]
        extent = radius * np.linalg.norm(inverse, axis=0)
        centre_frac = center @ inverse
        point_frac = points @ inverse
        ranges = []
        for axis in range(3):
            if not pbc[axis]:
                ranges.append(range(0, 1))
                continue
            first = int(np.floor(point_frac[:, axis].min() - centre_frac[axis] - extent[axis]))
            last = int(np.ceil(point_frac[:, axis].max() - centre_frac[axis] + extent[axis]))
            ranges.append(range(first, last + 1))
        if np.prod([len(r) for r in ranges]) > 100_000:
            raise ValueError("Basis is too diffuse for interactive lattice sums")
        single = _shell_wavefunction(data, shell, width)
        for cell in product(*ranges):
            translated = center + np.asarray(cell, dtype=float) @ lattice
            outside = np.maximum(np.maximum(lo - translated, translated - hi), 0)
            if outside @ outside > radius * radius:
                continue
            delta = points - translated
            mask = np.einsum("ij,ij->i", delta, delta) <= radius * radius
            if not np.any(mask):
                continue
            xyz = points[mask]
            phase = np.exp(2j * np.pi * float(kvec @ np.asarray(cell, dtype=float)))
            values[mask] += phase * evaluator._evaluate_at_points(
                single, block, translated[None, :], xyz[:, 0], xyz[:, 1], xyz[:, 2]
            )
    values /= np.sqrt(bvk_cells(data, pbc))
    return grid, values.reshape(grid.shape), dropped / weight


ORBITAL_COLUMNS = ["kpoint", "label", "spin", "orbital", "energy_eV", "occupation"]


def orbital_rows(data: BlochWavefunctionData) -> list[list[Any]]:
    """One row per defined orbital at every k-point and spin channel."""
    rows = []
    for k in range(len(data.kpoints)):
        label = data.labels[k] if data.labels is not None else ""
        for s in range(data.n_spin):
            for m in range(data.n_mo_per_k[k]):
                rows.append([
                    k + 1, label, spin_name(data, s) or "—", m + 1,
                    round(float(data.energies[k, s, m]) * HARTREE_TO_EV, 4),
                    round(float(data.occupations[k, s, m]), 4),
                ])
    return rows


def bloch_html(data: BlochWavefunctionData, pbc: list[bool]) -> str:
    """The details panel: the k-point set and every orbital."""
    from vibeview.renderers._html import data_table, kv_table, note, panel_document

    facts = kv_table([
        ("k-points", len(data.kpoints)),
        ("Mesh", " × ".join(map(str, data.mesh)) if data.mesh else "not stated"),
        ("Spin", data.spin),
        ("Orbitals per k-point", max(data.n_mo_per_k) if data.n_mo_per_k else 0),
        ("Fermi energy (eV)", None if data.fermi_energy_eh is None
         else f"{data.fermi_energy_eh * HARTREE_TO_EV:.4f}"),
        ("Bloch-sum N", bvk_cells(data, pbc)),
    ])
    caveat = note(
        "An orbital's overall phase is arbitrary, so its real and imaginary parts "
        "depend on the producer's gauge; the modulus |ψ| does not."
    )
    table = data_table(
        ["k", "Label", "Spin", "Orbital", "Energy (eV)", "Occupation"],
        orbital_rows(data), caption="Orbitals at each k-point", numeric_format=".4g",
    )
    return panel_document(facts + caveat + table)


class BlochWavefunctionRenderer(BaseRenderer):
    """Orbitals of a ``wavefunction.bloch`` section, evaluated on demand."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: BlochWavefunctionData | None = None

    def load(self) -> BlochWavefunctionData:
        if self._data is None:
            self._data = self.reader.read_wavefunction_bloch(self.section_id)
        return self._data

    def evaluate(
        self, kpoint: int, spin: int, orbital: int, *, n_per_dim: int = 40,
    ) -> tuple[GridData, np.ndarray, float]:
        return evaluate_bloch_orbital(
            self.reader, self.section, self.load(), kpoint, spin, orbital, n_per_dim=n_per_dim,
        )
