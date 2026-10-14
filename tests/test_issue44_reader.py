"""Regressions for the reader, kinds and viewer-defaults rows in #44.

Each test names the audit row it pins (#35-N in the issue body).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np

from vibeview import kinds, qvf
from vibeview.qvf import Atom, QVFReader, StructureData
from vibeview.qvf_writer import structure_archive

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src" / "vibeview"
_WATER = [
    {"symbol": "O", "atomic_number": 8, "position": [0.0, 0.0, 0.1173]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, 0.7572, -0.4692]},
    {"symbol": "H", "atomic_number": 1, "position": [0.0, -0.7572, -0.4692]},
]


def _water(tmp_path: Path) -> Path:
    return structure_archive(_WATER, (False, False, False), calculation="w").write(
        tmp_path / "w.qvf"
    )


def _protein(n_res: int, chains: str = "AB") -> StructureData:
    atoms = []
    for c, chain in enumerate(chains):
        for r in range(n_res):
            for name in ("N", "CA", "C"):
                atoms.append(
                    Atom(
                        symbol=name[0], position=np.array([r * 3.8, c * 10.0, 0.0]),
                        atomic_number={"N": 7, "C": 6}[name[0]], atom_name=name,
                        residue_name="ALA", residue_seq=r + 1, chain_id=chain,
                    )
                )
    return StructureData(atoms=atoms, pbc=(False, False, False), lattice_vectors=None, bonds=None)


# ── #35-14: connectivity says what it does ──────────────────────────────


def test_connectivity_is_named_for_what_it_returns(tmp_path):
    assert not hasattr(QVFReader, "infer_bonds")
    source = inspect.getsource(QVFReader.connectivity)
    assert "except AttributeError" not in source  # the dead manifest guard
    with QVFReader(_water(tmp_path)) as reader:
        assert len(reader.connectivity(reader.read_structure())) == 2


# ── #35-17 / #35-18 / #35-19 / #35-21: kinds ────────────────────────────


def test_via_structure_rule_is_one_constant():
    assert frozenset({"bonds"}) == kinds.RENDERED_VIA_STRUCTURE
    assert "RENDERED_VIA_STRUCTURE" in inspect.getsource(qvf._consumer_usable_kinds)
    assert kinds.classify_section("bonds") == ("rendered", "via structure")
    doc = kinds.classify_section.__doc__ or ""
    assert "via structure" in doc and "by the caller" in doc


def test_unused_lazy_kind_helpers_and_unknown_ids_are_gone(tmp_path):
    assert not hasattr(kinds, "LAZY_KINDS") and not hasattr(kinds, "is_lazy_kind")
    with QVFReader(_water(tmp_path)) as reader:
        assert not hasattr(reader, "_unknown_kind_ids")
        assert not hasattr(reader, "unknown_kind_ids")


# ── #35-20 / #35-47: the lazy loader is gone ────────────────────────────


def test_lazy_loader_is_gone():
    assert not (_SRC / "lazy_loader.py").exists()
    for path in _SRC.rglob("*.py"):
        text = path.read_text()
        assert "QVFLazyReader" not in text and "lazy_loader" not in text, path.name


# ── #35-22 / #35-23 / #35-24 / #35-25 / #35-32: reader idioms ───────────


def test_reader_idioms_are_single_helpers():
    source = inspect.getsource(qvf)
    assert source.count("Atom(\n") <= 2  # the helper and the edit overlay's symbol path
    assert "_atom_from_json(" in inspect.getsource(QVFReader.read_structure)
    assert "_atom_from_json(" in inspect.getsource(QVFReader.read_vibrations)
    assert "getinfo(member.path).file_size" not in source.replace(
        inspect.getsource(QVFReader.stored_size), ""
    )
    assert '.members.get("lattice") is not None' not in source
    assert 'extras.get("trajectory_ref")' not in source
    assert qvf._str_or_none(None) is None and qvf._str_or_none(3) == "3"


# ── #35-28: the relocalized prefix ──────────────────────────────────────


def test_relocalized_prefix_is_named_once():
    from vibeview import relocalize

    assert relocalize.overlay_section_id("ibo").startswith(qvf.RELOCALIZED_PREFIX)
    from tests._sources import read_source

    for name in ("qvf.py", "relocalize.py", "app.py"):
        assert '"wf_relocalized_"' not in read_source(name).replace(
            'RELOCALIZED_PREFIX = "wf_relocalized_"', ""
        ), name


# ── #35-30 / #35-31 / #35-39: bonds ─────────────────────────────────────


def test_bond_types_and_field_access():
    from vibeview import bonds

    assert "list[Bond] | None" in inspect.getsource(StructureData)
    # #35-31: the getattr is the documented duck-typed protocol (a light
    # structure without lattice or pbc is accepted, see test_bonds.py).
    assert "protocol" in (bonds.structure_bonds.__doc__ or "")


def test_periodic_inference_searches_from_real_atoms_only():
    from vibeview import bonds

    source = inspect.getsource(bonds.infer_bonds)
    assert "n_real" in source
    # NaCl rocksalt: every ion keeps its six neighbours through the faces.
    a = 5.64
    positions = np.array([[0, 0, 0], [a / 2, 0, 0], [0, a / 2, 0], [0, 0, a / 2],
                          [a / 2, a / 2, 0], [a / 2, 0, a / 2], [0, a / 2, a / 2],
                          [a / 2, a / 2, a / 2]])
    numbers = [11, 17, 17, 17, 11, 11, 11, 17]
    found = bonds.infer_bonds(positions, numbers, lattice=np.eye(3) * a, pbc=(True, True, True),
                              tolerance=0.9)
    counts = bonds.neighbour_counts(len(numbers), found)
    assert set(counts) == {6}


# ── #35-33 / #35-34: viewer defaults ────────────────────────────────────


def test_hints_merge_once_and_energy_window_is_not_a_renderer():
    from vibeview import viewer_defaults
    from vibeview.qvf import ViewerDefaults

    assert not hasattr(viewer_defaults.ViewerState(), "_provided_hint_keys")
    assert "vibeview.renderers" not in (_SRC / "viewer_defaults.py").read_text()
    assert (_SRC / "energy_window.py").exists()
    assert not (_SRC / "renderers" / "energy_window.py").exists()
    defaults = ViewerDefaults.model_validate({"d": {"opacity": 0.8}})
    state = viewer_defaults.ViewerState.from_manifest(defaults, {"d": "volume.spin"})
    hints = state.volume_hints["d"]  # resolved at construction, not on lookup
    assert (hints.isovalue, hints.colormap, hints.opacity) == (0.005, "RdBu", 0.8)


# ── #35-35: grid and lattice conventions ────────────────────────────────


def test_grid_units_and_reaction_lattice_helper():
    from vibeview.qvf import GridData
    from vibeview.renderers.cell import reaction_lattice_rows_angstrom

    assert GridData(np.zeros(3), np.eye(3), (2, 2, 2)).units == "bohr"
    assert callable(reaction_lattice_rows_angstrom)


# ── #35-37 / #35-40: biomolecular identity is derived once ──────────────


def test_chain_map_residue_flag_and_ca_list_are_cached():
    structure = _protein(20)
    first = structure.chains()
    assert structure.chains() is first
    assert structure.has_residues and structure._has_residues is True
    all_ca = structure.ca_residues()
    assert len(all_ca) == 40
    assert structure.ca_residues("A") == [e for e in all_ca if e[0] == "A"]
    assert structure._ca_all is not None


# ── #35-38: structure payloads parse once; an overlay builds no file atoms ─


def test_structure_payload_is_parsed_once(tmp_path, monkeypatch):
    with QVFReader(_water(tmp_path)) as reader:
        reader.read_structure()
        calls = []
        original = reader._read_json_member
        monkeypatch.setattr(
            reader, "_read_json_member",
            lambda sid, name: calls.append((sid, name)) or original(sid, name),
        )
        reader.read_structure()
        reader.read_structure()
        # Bytes are still re-verified per read (test_reader_payloads pins
        # that); the JSON parse of those verified bytes is not repeated.
        assert ("structure", "structure") not in calls
        reader.set_edit_overlay(np.zeros((1, 3)), ["He"])
        edited = reader.read_structure()
        assert [a.symbol for a in edited.atoms] == ["He"]
        assert edited.supplied_residues is None and edited.bonds is None


# ── #35-41 / #35-42 / #35-43 / #35-45 / #35-46 / #35-48: text ───────────


def test_docstrings_and_manual_rows():
    source = (_SRC / "qvf.py").read_text()
    assert "design doc" not in source and "periodic.hpp" not in source
    assert "lattice columns" not in source
    assert "(cached, per version)" not in source
    assert "[n_modes] km/mol\n" not in source
    assert "design doc" not in (_SRC / "kinds.py").read_text()
    assert "float32 ndarray" not in (_ROOT / "docs" / "python_api.md").read_text()
    editing = (_ROOT / "docs" / "editing.md").read_text()
    assert "biomolecular metadata does not" in editing
    assert "secondary structure" in (QVFReader.read_structure.__doc__ or "")
