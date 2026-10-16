"""Renderer dispatch: section kind to renderer class.

A renderer is constructed with a ``Section`` and the ``QVFReader`` that
owns it, loads its payload lazily through the reader's typed ``read_*``
methods, and exposes one or more of

* ``add_to_plotter(plotter, ...)`` for kinds drawn in the 3-D viewport,
* ``render_to_html(...)`` for chart and table panels (a complete document
  for the details iframe, or a Plotly fragment), and
* ``render_energy_plot(frame)`` / ``make_mesh(hints)`` for the animated and
  volumetric kinds.

The app and the terminal viewer pick the method by kind; nothing here
returns Trame objects. :func:`get_renderer` is the one place the kind to
class table lives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibeview.qvf import QVFReader, Section

# Renderer modules import VTK and Plotly, so the registry is built on the
# first dispatch rather than at package import, and then kept.
_REGISTRY: dict[str, type[BaseRenderer]] | None = None


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


def renderer_registry() -> dict[str, type[BaseRenderer]]:
    """The kind to renderer-class table, imported on first use and kept."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def get_renderer(section: Section, reader: QVFReader) -> BaseRenderer | None:
    """Dispatch a section to its renderer class.

    Returns None for a kind without a renderer (the caller reports it as
    skipped).
    """
    cls = renderer_registry().get(section.kind)
    if cls is None:
        return None
    return cls(section, reader)


def _build_registry() -> dict[str, type[BaseRenderer]]:
    from vibeview.renderers.atom_properties import AtomPropertiesRenderer
    from vibeview.renderers.bands import BandsRenderer
    from vibeview.renderers.bond_orders import BondOrdersRenderer
    from vibeview.renderers.citations import CitationsRenderer
    from vibeview.renderers.coop import COOPRenderer
    from vibeview.renderers.dos import DOSRenderer
    from vibeview.renderers.eos import EquationOfStateRenderer
    from vibeview.renderers.epr import EPRRenderer
    from vibeview.renderers.fermi import FermiSurfaceRenderer
    from vibeview.renderers.job_spec import JobSpecRenderer
    from vibeview.renderers.nmr import NMRRenderer
    from vibeview.renderers.phonon import PhononBandsRenderer, PhononDOSRenderer
    from vibeview.renderers.preview import PreviewRenderer
    from vibeview.renderers.qtaim import QTAIMRenderer
    from vibeview.renderers.reaction import ReactionPathRenderer, ReactionWaypointsRenderer
    from vibeview.renderers.reaction_scheme import ReactionSchemeRenderer
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

    return {
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
        "reaction.scheme": ReactionSchemeRenderer,
        "preview": PreviewRenderer,
    }
