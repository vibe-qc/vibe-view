"""Fermi-surface renderer for the `fermi_surface` kind (QVF spec §4.12).

For a metallic periodic system the Fermi surface is rendered in **reciprocal
space**: for each band near E_F the archive stores E(k) − E_F (eV) on a
γ-centered Monkhorst-Pack mesh, and the Fermi sheet is the isosurface
E − E_F = 0. The reciprocal lattice b_i is built from the real-space lattice
a_i (crystallographer's convention b_i · a_j = 2π δ_ij) so the sheets sit in
Cartesian k-space (Å⁻¹); the γ point is rolled to the grid centre so each sheet
is centred in the view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    import pyvista as pv

    from vibeview.qvf import QVFReader, Section

_TWO_PI = 2.0 * np.pi


def reciprocal_lattice(lattice_vectors: np.ndarray) -> np.ndarray:
    """Reciprocal lattice (rows b1,b2,b3) from the real-space lattice (rows a1,a2,a3).

    b_i = 2π (a_j × a_k) / [a_i · (a_j × a_k)] — the crystallographer's
    convention b_i · a_j = 2π δ_ij. Real-space rows are in Å; the returned rows
    are in Å⁻¹.
    """
    a = np.asarray(lattice_vectors, dtype=float)
    vol = float(np.dot(a[0], np.cross(a[1], a[2])))
    if abs(vol) < 1e-30:
        raise ValueError("fermi_surface: singular lattice_vectors (zero cell volume)")
    b = np.empty((3, 3))
    b[0] = _TWO_PI * np.cross(a[1], a[2]) / vol
    b[1] = _TWO_PI * np.cross(a[2], a[0]) / vol
    b[2] = _TWO_PI * np.cross(a[0], a[1]) / vol
    return b


def build_fermi_sheets(
    energies: np.ndarray,
    lattice_vectors: np.ndarray,
    band_indices: list[int] | None = None,
) -> tuple[list[tuple[int, "pv.PolyData"]], np.ndarray]:
    """Build the Fermi sheets (E − E_F = 0 isosurfaces) in Cartesian k-space.

    ``energies`` is ``[nk1, nk2, nk3, n_bands]`` of E(k) − E_F (eV). Returns
    ``(sheets, recip)`` where ``sheets`` is a list of ``(band_label, PolyData)``
    for each band that actually crosses E_F, and ``recip`` is the 3×3 reciprocal
    lattice. The γ point is rolled to the grid centre so the surfaces are
    centred about the Cartesian origin.
    """
    import pyvista as pv

    e = np.asarray(energies, dtype=float)
    if e.ndim != 4:
        raise ValueError(f"fermi_surface: energies must be 4-D, got shape {e.shape}")
    nk1, nk2, nk3, nband = e.shape
    recip = reciprocal_lattice(lattice_vectors)

    # γ-centred fractional coords in [-0.5, 0.5); Cartesian k = Σ f_i b_i.
    f1 = (np.arange(nk1) - nk1 // 2) / nk1
    f2 = (np.arange(nk2) - nk2 // 2) / nk2
    f3 = (np.arange(nk3) - nk3 // 2) / nk3
    F1, F2, F3 = np.meshgrid(f1, f2, f3, indexing="ij")
    kx = F1 * recip[0, 0] + F2 * recip[1, 0] + F3 * recip[2, 0]
    ky = F1 * recip[0, 1] + F2 * recip[1, 1] + F3 * recip[2, 1]
    kz = F1 * recip[0, 2] + F2 * recip[1, 2] + F3 * recip[2, 2]
    sgrid = pv.StructuredGrid(kx, ky, kz)

    labels = list(band_indices) if band_indices is not None else list(range(nband))
    shift = (nk1 // 2, nk2 // 2, nk3 // 2)
    sheets: list[tuple[int, pv.PolyData]] = []
    for b in range(nband):
        # Roll γ (index 0) to the grid centre so it lands on the centred grid.
        eb = np.roll(e[..., b], shift=shift, axis=(0, 1, 2))
        sgrid.point_data["E"] = eb.ravel(order="F")
        contour = sgrid.contour(isosurfaces=[0.0], scalars="E")
        if contour.n_points > 0:
            sheets.append((labels[b] if b < len(labels) else b, contour))
    return sheets, recip


def reciprocal_cell_edges(recip: np.ndarray) -> "pv.PolyData":
    """Wireframe of the reciprocal cell parallelepiped, centred on γ.

    The 8 corners are Σ (±½) b_i; the 12 edges connect corners differing in
    exactly one sign. Drawn so the user can read the k-space scale (spec §4.12).
    """
    import pyvista as pv

    corners = np.array(
        [
            s1 * recip[0] + s2 * recip[1] + s3 * recip[2]
            for s1 in (-0.5, 0.5)
            for s2 in (-0.5, 0.5)
            for s3 in (-0.5, 0.5)
        ]
    )
    edges = [
        (i, j)
        for i in range(8)
        for j in range(i + 1, 8)
        if bin(i ^ j).count("1") == 1  # differ in exactly one sign bit
    ]
    lines = np.hstack([[2, i, j] for i, j in edges])
    return pv.PolyData(corners, lines=lines)


class FermiSurfaceRenderer(BaseRenderer):
    """Reciprocal-space Fermi-surface sheets for the fermi_surface kind."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._energies: np.ndarray | None = None
        self._meta: dict | None = None

    def load(self) -> tuple[np.ndarray, dict]:
        """Read the energy mesh (E − E_F) + the mesh metadata JSON."""
        if self._energies is None:
            self._meta = self.reader._read_json_member(self.section_id, "mesh")
            self._energies = self.reader._read_binary_member(self.section_id, "energies")
        return self._energies, self._meta  # type: ignore[return-value]

    def build_sheets(self) -> tuple[list[tuple[int, "pv.PolyData"]], np.ndarray, dict]:
        """Return (sheets, reciprocal-lattice, mesh-metadata)."""
        energies, meta = self.load()
        lattice = np.asarray(meta["lattice_vectors"], dtype=float)
        sheets, recip = build_fermi_sheets(energies, lattice, meta.get("band_indices"))
        return sheets, recip, meta
