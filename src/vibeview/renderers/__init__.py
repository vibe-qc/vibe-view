"""Renderer dispatch — maps section kind to renderer class.

Each renderer receives a Section + QVFReader reference and returns
PyVista/Trame objects that the app composites into the viewport.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, Section

# Import renderers lazily to avoid loading VTK at import time.
# The app imports them on first use.


class BaseRenderer:
    """Base class for all section renderers."""

    def __init__(self, section: Section, reader: QVFReader) -> None:
        self.section = section
        self.reader = reader
        self._data: object = None  # cached lazy-loaded data

    @property
    def section_id(self) -> str:
        return self.section.id

    @property
    def kind(self) -> str:
        return self.section.kind


def get_renderer(section: Section, reader: QVFReader) -> BaseRenderer | None:
    """Dispatch a section to its renderer class.

    Returns None if the kind is not in SUPPORTED_KINDS (caller should
    handle as skipped).
    """
    from vibeview.renderers.atom_properties import AtomPropertiesRenderer
    from vibeview.renderers.bands import BandsRenderer
    from vibeview.renderers.bond_orders import BondOrdersRenderer
    from vibeview.renderers.citations import CitationsRenderer
    from vibeview.renderers.coop import COOPRenderer
    from vibeview.renderers.dos import DOSRenderer
    from vibeview.renderers.eos import EquationOfStateRenderer
    from vibeview.renderers.fermi import FermiSurfaceRenderer
    from vibeview.renderers.epr import EPRRenderer
    from vibeview.renderers.nmr import NMRRenderer
    from vibeview.renderers.phonon import PhononBandsRenderer, PhononDOSRenderer
    from vibeview.renderers.qtaim import QTAIMRenderer
    from vibeview.renderers.reaction import ReactionPathRenderer, ReactionWaypointsRenderer
    from vibeview.renderers.job_spec import JobSpecRenderer
    from vibeview.renderers.run_record import RunRecordRenderer
    from vibeview.renderers.scan_surface import ScanSurfaceRenderer
    from vibeview.renderers.scf_history import SCFHistoryRenderer
    from vibeview.renderers.spectra import SpectraRenderer
    from vibeview.renderers.structure import StructureRenderer
    from vibeview.renderers.symmetry import SymmetryRenderer
    from vibeview.renderers.trajectory import TrajectoryRenderer
    from vibeview.renderers.vibrations import VibrationsRenderer
    from vibeview.renderers.volume import VolumeRenderer
    from vibeview.renderers.wavefunction import WavefunctionRenderer

    _KIND_RENDERER: dict[str, type[BaseRenderer]] = {
        "structure": StructureRenderer,
        "volume.density": VolumeRenderer,
        "volume.orbital": VolumeRenderer,
        "volume.spin": VolumeRenderer,
        "volume.elf": VolumeRenderer,
        "volume.difference": VolumeRenderer,
        "volume.generic": VolumeRenderer,
        "volume.potential": VolumeRenderer,
        "volume.rdg": VolumeRenderer,
        "basis.ao": VolumeRenderer,
        "bands": BandsRenderer,
        "dos.total": DOSRenderer,
        "dos.projected": DOSRenderer,
        "phonon_bands": PhononBandsRenderer,
        "phonon_dos": PhononDOSRenderer,
        "equation_of_state": EquationOfStateRenderer,
        "fermi_surface": FermiSurfaceRenderer,
        "spectra.ir": SpectraRenderer,
        "spectra.uvvis": SpectraRenderer,
        "spectra.raman": SpectraRenderer,
        "spectra.ecd": SpectraRenderer,
        "spectra.vcd": SpectraRenderer,
        "spectra.generic": SpectraRenderer,
        "trajectory": TrajectoryRenderer,
        "reaction.path": ReactionPathRenderer,
        "reaction.waypoints": ReactionWaypointsRenderer,
        "scan.surface": ScanSurfaceRenderer,
        "vibrations": VibrationsRenderer,
        "atom_properties": AtomPropertiesRenderer,
        "wavefunction.gto": WavefunctionRenderer,
        "citations": CitationsRenderer,
        "run.record": RunRecordRenderer,
        "job.spec": JobSpecRenderer,
        "scf_history": SCFHistoryRenderer,
        "structure.symmetry": SymmetryRenderer,
        "spectra.nmr": NMRRenderer,
        "spectra.epr": EPRRenderer,
        "bond_orders": BondOrdersRenderer,
        "topology.qtaim": QTAIMRenderer,
        "dos.coop": COOPRenderer,
        "dos.cohp": COOPRenderer,
    }

    cls = _KIND_RENDERER.get(section.kind)
    if cls is None:
        return None
    return cls(section, reader)
