"""IUPAC molecule-name sidebar header (optional, via standalone vibeqc_naming).

vibeqc_naming is a separate pure-Python package that may or may not be
installed alongside vibe-view, so these tests inject a fake module rather
than depend on it. The feature must degrade to "no entry" when it is absent.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

water_qvf = (
    Path(__file__).resolve().parents[2]
    / "examples" / "vibe_view" / "runs" / "qvf_showcase" / "water.qvf"
)


def _fake_naming(name, source, confidence):
    """A stand-in vibeqc_naming module whose naming functions return a
    NamedResult-shaped object (.name, .source.value, .confidence.value) and
    record which route was called (name_from_qvf vs the atom routes)."""

    class _Enum:
        def __init__(self, value):
            self.value = value

    class _Named:
        def __init__(self):
            self.name = name
            self.source = _Enum(source)
            self.confidence = _Enum(confidence)

    calls = []
    mod = types.ModuleType("vibeqc_naming")
    mod.name_from_qvf = lambda path: calls.append(("qvf", path)) or _Named()
    mod.name_from_atoms_detailed = (
        lambda atoms: calls.append(("atoms", atoms)) or _Named()
    )
    mod.name_from_atoms_with_lattice = (
        lambda atoms, lattice: calls.append(("lattice", atoms, lattice)) or _Named()
    )
    mod._calls = calls
    return mod


def test_absent_package_yields_no_entry(monkeypatch):
    """The current state (vibeqc_naming not installed) must produce None,
    leaving the sidebar untouched."""
    monkeypatch.setitem(sys.modules, "vibeqc_naming", None)  # -> ImportError
    from vibeview.app import _iupac_name_entry

    assert _iupac_name_entry(types.SimpleNamespace(path="x.qvf")) is None


@pytest.mark.parametrize(
    "confidence,icon",
    [
        ("high", "mdi-check-circle"),
        ("medium", "mdi-information"),
        ("low", "mdi-alert-circle"),
        ("unexpected", "mdi-help-circle"),
    ],
)
def test_entry_fields_and_confidence_icon(monkeypatch, confidence, icon):
    monkeypatch.setitem(
        sys.modules, "vibeqc_naming", _fake_naming("water", "trivial_iupac", confidence)
    )
    from vibeview.app import _iupac_name_entry

    entry = _iupac_name_entry(types.SimpleNamespace(path="water.qvf"))
    assert entry["id"] == "__iupac_name__"
    assert entry["title"] == "water"
    assert entry["subtitle"] == "IUPAC — trivial_iupac"
    assert entry["icon"] == icon
    assert entry["disabled"] is True
    assert entry["kind"] == "iupac_name"


def test_slab_name_source(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "vibeqc_naming",
        _fake_naming("benzene on magnesia", "inorganic_compositional", "medium"),
    )
    from vibeview.app import _iupac_name_entry

    entry = _iupac_name_entry(types.SimpleNamespace(path="slab.qvf"))
    assert entry["title"] == "benzene on magnesia"
    assert entry["subtitle"] == "IUPAC — inorganic_compositional"
    assert entry["icon"] == "mdi-information"


def test_empty_name_yields_none(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "vibeqc_naming", _fake_naming("", "trivial_iupac", "high")
    )
    from vibeview.app import _iupac_name_entry

    assert _iupac_name_entry(types.SimpleNamespace(path="x.qvf")) is None


def test_in_memory_periodic_reader_is_named_from_structure(monkeypatch):
    """Loose-file conversions (opened .py inputs, ...) arrive as in-memory
    QVFs with no path; the periodic namer route must be used from the
    structure atoms + lattice rows."""
    mod = _fake_naming("carbon", "inorganic_compositional", "high")
    monkeypatch.setitem(sys.modules, "vibeqc_naming", mod)
    from vibeview.app import _iupac_name_entry

    class _Atom:
        def __init__(self, z, x, y, zz):
            self.atomic_number = z
            self.position = [x, y, zz]

    class _Structure:
        atoms = [_Atom(6, 0.0, 0.0, 0.0)]
        lattice_vectors = [[3.553, 0, 0], [0, 3.553, 0], [0, 0, 3.553]]
        pbc = (True, True, True)

    entry = _iupac_name_entry(
        types.SimpleNamespace(path=None, read_structure=lambda: _Structure())
    )
    assert entry is not None
    assert entry["title"] == "carbon"
    assert entry["subtitle"] == "IUPAC — inorganic_compositional"
    assert mod._calls[0][0] == "lattice"
    assert mod._calls[0][1][0][0] == 6  # atoms passed through


def test_in_memory_molecular_reader_uses_atoms_namer(monkeypatch):
    mod = _fake_naming("water", "trivial_iupac", "high")
    monkeypatch.setitem(sys.modules, "vibeqc_naming", mod)
    from vibeview.app import _iupac_name_entry

    class _Atom:
        def __init__(self, z, x, y, zz):
            self.atomic_number = z
            self.position = [x, y, zz]

    class _Structure:
        atoms = [_Atom(8, 0.0, 0.0, 0.0)]
        lattice_vectors = None
        pbc = (False, False, False)

    entry = _iupac_name_entry(
        types.SimpleNamespace(path=None, read_structure=lambda: _Structure())
    )
    assert entry is not None
    assert entry["title"] == "water"
    assert mod._calls[0][0] == "atoms"


def test_in_memory_reader_with_unreadable_structure_yields_none(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "vibeqc_naming", _fake_naming("water", "trivial_iupac", "high")
    )
    from vibeview.app import _iupac_name_entry

    def boom():
        raise ValueError("no structure")

    assert (
        _iupac_name_entry(types.SimpleNamespace(path=None, read_structure=boom))
        is None
    )


def test_in_memory_bulk_crystal_falls_back_to_atoms_namer(monkeypatch):
    """The periodic slab route returns no name for a pure bulk crystal;
    the entry must fall back to the structure route instead of hiding."""

    class _Enum:
        def __init__(self, value):
            self.value = value

    class _Named:
        def __init__(self, name, source, confidence):
            self.name = name
            self.source = _Enum(source)
            self.confidence = _Enum(confidence)

    calls = []
    mod = types.ModuleType("vibeqc_naming")
    mod.name_from_qvf = lambda path: _Named("unused", "trivial_iupac", "high")
    mod.name_from_atoms_detailed = (
        lambda atoms: calls.append("atoms")
        or _Named("octac", "inorganic_compositional", "medium")
    )
    mod.name_from_atoms_with_lattice = (
        lambda atoms, lattice: calls.append("lattice")
        or _Named("", "inorganic_compositional", "low")
    )
    monkeypatch.setitem(sys.modules, "vibeqc_naming", mod)
    from vibeview.app import _iupac_name_entry

    class _Atom:
        def __init__(self, z, x, y, zz):
            self.atomic_number = z
            self.position = [x, y, zz]

    class _Structure:
        atoms = [_Atom(6, 0.0, 0.0, 0.0)]
        lattice_vectors = [[3.553, 0, 0], [0, 3.553, 0], [0, 0, 3.553]]
        pbc = (True, True, True)

    entry = _iupac_name_entry(
        types.SimpleNamespace(path=None, read_structure=lambda: _Structure())
    )
    assert entry is not None
    assert entry["title"] == "octac"
    assert calls == ["lattice", "atoms"]


def test_iupac_entry_is_first_in_built_sidebar(water_qvf, monkeypatch):
    """Integration: the name header is the first sidebar entry, before the
    Structure section, in the actual create_app-built state."""
    monkeypatch.setitem(
        sys.modules, "vibeqc_naming", _fake_naming("water", "trivial_iupac", "high")
    )
    from trame.app import get_server

    from vibeview.app import create_app
    from vibeview.qvf import QVFReader

    reader = QVFReader(water_qvf)
    try:
        create_app(reader)
        server = get_server(client_type="vue3")
        entries = list(server.state.sidebar_entries)
        assert entries[0]["id"] == "__iupac_name__"
        assert entries[0]["title"] == "water"
        assert entries[0]["subtitle"] == "IUPAC — trivial_iupac"
        assert entries[0]["disabled"] is True
        # a real section (Structure, ...) still follows the name header
        assert any(e["id"] != "__iupac_name__" for e in entries[1:])
    finally:
        reader.close()
