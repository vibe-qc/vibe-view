"""Toolbar molecule builder + IUPAC dashboard labels.

Both features consume the optional, standalone ``vibeqc_naming`` package.
It is not installed alongside vibe-view (see HANDOVER_IUPAC_NAMING.md § B5),
so these tests inject a fake module rather than depend on it, and assert that
each feature degrades cleanly when it is absent.
"""

from __future__ import annotations

import sys
import types

import pytest

from vibeview.converters import atoms_to_qvf
from vibeview.qvf import QVFReader

# Water, angstroms — (Z, x, y, z), the shape structure_from_name() returns.
_WATER_ATOMS = [
    (8, 0.0, 0.0, 0.1173),
    (1, 0.0, 0.7572, -0.4692),
    (1, 0.0, -0.7572, -0.4692),
]


# ── converters.atoms_to_qvf ────────────────────────────────────────────────


def test_atoms_to_qvf_round_trips_through_reader():
    reader = QVFReader(atoms_to_qvf(_WATER_ATOMS, label="water"))
    try:
        structure = reader.read_structure()
        assert [a.symbol for a in structure.atoms] == ["O", "H", "H"]
        assert [a.atomic_number for a in structure.atoms] == [8, 1, 1]
        assert structure.atoms[0].position[2] == pytest.approx(0.1173)
        assert structure.pbc == (False, False, False)
    finally:
        reader.close()


def test_atoms_to_qvf_tags_the_builder_label():
    """_job_name() reads the label back out of manifest.source.calculation."""
    reader = QVFReader(atoms_to_qvf(_WATER_ATOMS, label="pyridine"))
    try:
        assert reader.source.calculation == "builder:pyridine"
        assert reader.path is None
    finally:
        reader.close()


def test_atoms_to_qvf_rejects_empty_structure():
    with pytest.raises(ValueError, match="zero atoms"):
        atoms_to_qvf([], label="nothing")


def test_job_name_prefers_builder_label_over_in_memory_placeholder():
    from vibeview.app import _job_name

    reader = QVFReader(atoms_to_qvf(_WATER_ATOMS, label="benzene"))
    try:
        assert _job_name(reader) == "benzene"
    finally:
        reader.close()


def test_job_name_falls_back_for_unlabelled_in_memory_archives():
    from vibeview.app import _job_name

    reader = types.SimpleNamespace(
        path=None, source=types.SimpleNamespace(calculation="xyz:scratch")
    )
    assert _job_name(reader) == "<in-memory>"


# ── sidebar entry for built structures ─────────────────────────────────────


def test_sidebar_names_a_built_structure_without_reopening_it():
    """A builder archive has no path, so name_from_qvf cannot open it. The
    entry must still appear, carrying the name it was built from.
    """
    from vibeview.app import _iupac_name_entry

    reader = QVFReader(atoms_to_qvf(_WATER_ATOMS, label="pyridine"))
    try:
        entry = _iupac_name_entry(reader)
    finally:
        reader.close()
    assert entry is not None
    assert entry["title"] == "pyridine"
    assert entry["subtitle"] == "IUPAC — builder"
    assert entry["disabled"] is True


def test_sidebar_builder_entry_does_not_call_the_namer(monkeypatch):
    """Built structures bypass naming entirely — a built "thf" must not be
    relabelled "1-methyl methanal" by the systematic organic walker.
    """
    mod = types.ModuleType("vibeqc_naming")

    def _should_not_run(path):
        raise AssertionError("name_from_qvf must not be called for builder archives")

    mod.name_from_qvf = _should_not_run
    monkeypatch.setitem(sys.modules, "vibeqc_naming", mod)

    from vibeview.app import _iupac_name_entry

    reader = QVFReader(atoms_to_qvf(_WATER_ATOMS, label="thf"))
    try:
        assert _iupac_name_entry(reader)["title"] == "thf"
    finally:
        reader.close()


# ── _naming_available ──────────────────────────────────────────────────────


def test_naming_available_false_when_package_absent(monkeypatch):
    from vibeview.app import _naming_available

    monkeypatch.setitem(sys.modules, "vibeqc_naming", None)  # -> ImportError
    assert _naming_available() is False


# ── dashboard._iupac_label ─────────────────────────────────────────────────


def _fake_naming(name, confidence):
    """Stand-in whose name_from_atoms_detailed returns a NamedResult shape."""

    class _Enum:
        def __init__(self, value):
            self.value = value

    class _Named:
        def __init__(self):
            self.name = name
            self.confidence = _Enum(confidence)

    mod = types.ModuleType("vibeqc_naming")
    mod.name_from_atoms_detailed = lambda atoms: _Named()
    return mod


def _structure():
    reader = QVFReader(atoms_to_qvf(_WATER_ATOMS, label="water"))
    try:
        return reader.read_structure()
    finally:
        reader.close()


def test_iupac_label_absent_package_yields_none(monkeypatch):
    from vibeview.dashboard import _iupac_label

    monkeypatch.setitem(sys.modules, "vibeqc_naming", None)
    assert _iupac_label(_structure()) is None


@pytest.mark.parametrize("confidence", ["high", "medium"])
def test_iupac_label_accepts_trusted_confidence(monkeypatch, confidence):
    """"medium" must be accepted: the compositional inorganic rules report it
    for names as solid as "sodium chloride", so gating on "high" alone would
    silently drop every mineral.
    """
    monkeypatch.setitem(sys.modules, "vibeqc_naming", _fake_naming("water", confidence))
    from vibeview.dashboard import _iupac_label

    assert _iupac_label(_structure()) == "water"


def test_iupac_label_rejects_low_confidence_names(monkeypatch):
    """"low" is the namer's own signal that it could not assemble a well-formed
    name -- what thymine returns. The label falls back to the filename.
    """
    monkeypatch.setitem(
        sys.modules, "vibeqc_naming", _fake_naming("1-amino,1-methyl methanamino-", "low")
    )
    from vibeview.dashboard import _iupac_label

    assert _iupac_label(_structure()) is None


def test_iupac_label_rejects_empty_name(monkeypatch):
    monkeypatch.setitem(sys.modules, "vibeqc_naming", _fake_naming("", "high"))
    from vibeview.dashboard import _iupac_label

    assert _iupac_label(_structure()) is None


def test_iupac_label_survives_naming_exception(monkeypatch):
    mod = types.ModuleType("vibeqc_naming")

    def _boom(atoms):
        raise RuntimeError("naming exploded")

    mod.name_from_atoms_detailed = _boom
    monkeypatch.setitem(sys.modules, "vibeqc_naming", mod)
    from vibeview.dashboard import _iupac_label

    assert _iupac_label(_structure()) is None
