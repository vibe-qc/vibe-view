"""Vibrations renderer — per-mode displacement animation.

Select a mode from the frequencies table, see atoms displaced along
the normal mode vector with amplitude control. Loop or single-shot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from vibeview.renderers import BaseRenderer

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, Section, VibrationsData


class VibrationsRenderer(BaseRenderer):
    """Normal-mode displacement animation."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        super().__init__(section, reader)
        self._data: VibrationsData | None = None

    def load(self) -> VibrationsData:
        if self._data is None:
            self._data = self.reader.read_vibrations(self.section_id)
            self._ensure_equilibrium_geometry(self._data)
        return self._data

    def _ensure_equilibrium_geometry(self, data: VibrationsData) -> None:
        """Recover equilibrium atom positions from the ``structure`` section
        when the vibrations metadata shipped all-zero coordinates.

        The QVF writer historically hard-coded ``position: [0,0,0]`` for
        every atom in the vibrations section (audit finding A5-01), so the
        animation collapsed every atom onto the world origin. When we detect
        that, and the file carries a same-length ``structure`` section, we
        borrow its (index-aligned) geometry so the mode animates on the real
        molecule. Newer writers embed the real positions, in which case this
        is a no-op.
        """
        if not data.atoms:
            return
        pos = np.array([a.position for a in data.atoms], dtype=float)
        if not np.allclose(pos, 0.0):
            return
        try:
            structure = self.reader.read_structure()
        except Exception:  # noqa: BLE001 — best-effort recovery
            return
        if len(structure.atoms) != len(data.atoms):
            return
        for vib_atom, struct_atom in zip(data.atoms, structure.atoms):
            vib_atom.position = np.asarray(struct_atom.position, dtype=float)
            if not vib_atom.atomic_number:
                vib_atom.atomic_number = struct_atom.atomic_number

    @property
    def n_modes(self) -> int:
        data = self.load()
        return len(data.frequencies)

    def get_frequencies(self) -> np.ndarray:
        data = self.load()
        return data.frequencies

    def get_mode(self, mode_index: int) -> tuple[list[str], np.ndarray, np.ndarray]:
        """Return (symbols, equilibrium_positions, displacement_vectors).

        displacement_vectors is [n_atoms, 3] — the normal mode vector.
        """
        data = self.load()
        symbols = [a.symbol for a in data.atoms]
        positions = np.array([a.position for a in data.atoms])
        disp = data.displacements[mode_index]  # [n_atoms, 3]
        return symbols, positions, disp

    def displace_atoms(
        self,
        mode_index: int,
        amplitude: float,
        phase: float = 0.0,
    ) -> list[tuple[str, np.ndarray]]:
        """Return atom symbols + displaced positions for a given mode and amplitude.

        amplitude: scaling factor for the displacement vector.
        phase: in radians, for sinusoidal animation.
        """
        symbols, positions, disp = self.get_mode(mode_index)
        scale = amplitude * np.cos(phase)
        displaced = positions + scale * disp
        return [(symbols[i], displaced[i]) for i in range(len(symbols))]
