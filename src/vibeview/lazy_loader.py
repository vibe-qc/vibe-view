"""Lazy section data loading for faster startup.

Instead of reading all section data when opening a QVF, sections
defer their data reads until they are first accessed.  This cuts
startup time for large QVFs from seconds to milliseconds.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def lazy(fn: Callable[..., T]) -> Callable[..., T]:
    """Decorator: memoize a method call, deferring work to first access.

    The decorated method is only called once; subsequent calls return
    the cached result.
    """
    attr_name = f"_lazy_{fn.__name__}"

    @functools.wraps(fn)
    def wrapper(self, *args: Any, **kwargs: Any) -> T:
        if not hasattr(self, attr_name):
            setattr(self, attr_name, fn(self, *args, **kwargs))
        return getattr(self, attr_name)

    return wrapper


class LazySection:
    """A section whose data is loaded only on first access.

    Wraps a QVF section and defers reading its member data until
    the first call to ``read()``.
    """

    def __init__(self, section, reader):
        self._section = section
        self._reader = reader  # QVFLazyReader (has read_section_data)
        self._data: Any = None
        self._loaded = False

    @property
    def id(self) -> str:
        return self._section.id

    @property
    def kind(self) -> str:
        return self._section.kind

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def read(self) -> Any:
        """Read and cache the section data."""
        if not self._loaded:
            self._data = self._reader.read_section_data(self._section)
            self._loaded = True
        return self._data

    def unload(self) -> None:
        """Drop cached data to free memory."""
        self._data = None
        self._loaded = False


class QVFLazyReader:
    """QVF reader that lazily loads section data.

    Wraps a standard QVFReader and returns LazySection objects.
    Section metadata (id, kind) is available immediately; data
    is only read when accessed.
    """

    # Mapping of section kind → (reader_method_name, needs_section_id).
    # ``structure`` is special: its read method takes no section_id.
    _DISPATCH: dict[str, tuple[str, bool]] = {
        "structure": ("read_structure", False),
        "bands": ("read_bands", True),
        "phonon_bands": ("read_phonon_bands", True),
        "phonon_dos": ("read_phonon_dos", True),
        "equation_of_state": ("read_equation_of_state", True),
        "trajectory": ("read_trajectory", True),
        "vibrations": ("read_vibrations", True),
        "wavefunction.gto": ("read_wavefunction_gto", True),
        "reaction.path": ("read_reaction_path", True),
        "reaction.waypoints": ("read_reaction_waypoints", True),
        "scan.surface": ("read_scan_surface", True),
        "atom_properties": ("read_atom_properties", True),
        "spectra.nmr": ("read_nmr", True),
        "structure.symmetry": ("read_symmetry", True),
        "scf_history": ("read_scf_history", True),
        "citations": ("read_citations", True),
        "run.record": ("read_run_record", True),
        "bond_orders": ("read_bond_orders", True),
        "topology.qtaim": ("read_topology_qtaim", True),
        "dos.coop": ("read_dos_coop", True),
        "dos.cohp": ("read_dos_coop", True),
    }

    def __init__(self, qvf_path: str):
        from vibeview.qvf import QVFReader

        self._raw_reader = QVFReader(qvf_path)
        self._sections: list[LazySection] = []
        self._preload_kinds: set[str] = {"structure"}  # Always preload

        for section in self._raw_reader.sections:
            lazy_sec = LazySection(section, self)
            self._sections.append(lazy_sec)

            # Preload critical sections
            if section.kind in self._preload_kinds:
                lazy_sec.read()

    @property
    def sections(self) -> list[LazySection]:
        return self._sections

    @property
    def manifest(self):
        return self._raw_reader.manifest

    @property
    def path(self):
        return self._raw_reader.path

    def read_section_data(self, section) -> Any:
        """Dispatch to the appropriate QVFReader method.

        Volume kinds (``volume.*``, ``basis.*``, ``fermi_surface``)
        return a ``(grid, data)`` tuple; spectrum kinds return the
        parsed data object.  ``structure`` returns a
        ``StructureData``.  Unknown kinds are read via the generic
        ``_read_json_member`` fallback.
        """
        kind = section.kind
        sid = section.id
        r = self._raw_reader

        # Volume-like kinds: grid metadata + raw data
        if kind.startswith("volume.") or kind.startswith("basis.") or kind == "fermi_surface":
            return r.read_volume_grid(sid), r.read_volume_data(sid)

        # Spectrum-like kinds
        if kind.startswith("spectra."):
            if kind == "spectra.nmr":
                return r.read_nmr(sid)
            return r.read_spectra(sid)

        # Electronic DOS has no dedicated QVFReader method — read the
        # members per the QVF spec (§4.8/§4.9), mirroring DOSRenderer.
        # (These kinds used to dispatch to read_phonon_dos, which reads
        # a 'frequencies' member DOS sections don't have — every lazy
        # DOS read failed.)
        if kind == "dos.total":
            return {
                "energies": r._read_binary_member(sid, "energies"),
                "dos": r._read_binary_member(sid, "dos"),
            }
        if kind == "dos.projected":
            return {
                "energies": r._read_binary_member(sid, "energies"),
                "projections": r._read_binary_member(sid, "projections"),
            }

        entry = self._DISPATCH.get(kind)
        if entry is not None:
            method_name, needs_id = entry
            method = getattr(r, method_name)
            if needs_id:
                return method(sid)
            return method()

        # Generic fallback: read the first member as text
        return r._read_json_member(sid, "data")

    def preload(self, kind: str) -> None:
        """Eagerly load all sections of a given kind."""
        for sec in self._sections:
            if sec.kind == kind:
                sec.read()

    def unload_all_except(self, keep_kinds: set[str] | None = None) -> int:
        """Unload cached data for all sections except those in keep_kinds.

        Returns number of sections unloaded.
        """
        keep = keep_kinds or {"structure"}
        count = 0
        for sec in self._sections:
            if sec.kind not in keep and sec.is_loaded:
                sec.unload()
                count += 1
        return count

    def get_memory_usage(self) -> dict[str, int]:
        """Estimate memory usage per section kind."""
        import sys

        usage: dict[str, int] = {}
        for sec in self._sections:
            if sec.is_loaded:
                size = sys.getsizeof(sec._data)
                usage[sec.kind] = usage.get(sec.kind, 0) + size
        return usage

    def close(self) -> None:
        self._raw_reader.close()
