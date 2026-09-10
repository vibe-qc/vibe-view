"""Producer-supplied biomolecule metadata, and b-factors, on the reader side.

Cross-repo Ask B, viewer half. Two independent things are pinned here.

**Precedence.** vibe-view derives residues, chains and secondary structure
itself, from PDB records and from CA geometry. Where a producer supplies
them on the ``structure`` section object (QVF spec § 5.1), the supplied
values win: the CA-only assignment cannot tell an alpha- from a pi- from
a 3-10 helix, or a beta-bridge from a sheet, and a producer can. The
fallback to derivation has to survive intact, though, since almost every
archive supplies nothing.

**B-factors.** ``pdb_to_qvf`` preserves PDB cols 61-66 onto each atom, and
a producer may instead supply one parallel ``b_factors`` array. The
per-atom carrier wins, because it cannot desynchronize from its atom.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import numpy as np
import pytest

from vibeview.converters import pdb_to_qvf
from vibeview.qvf import ManifestValidationError, QVFReader

# An ideal right-handed alpha-helix CA trace, so the geometric assignment
# has something unambiguous to say and a supplied override is visibly
# different from it. Same parameters as tests/test_secondary_structure.py.
_HELIX_RISE = 1.5
_HELIX_TURN_DEG = 100.0
_HELIX_RADIUS = 2.3


def _helix_positions(n: int) -> list[list[float]]:
    t = np.arange(n)
    a = np.deg2rad(_HELIX_TURN_DEG) * t
    return np.stack(
        [
            _HELIX_RADIUS * np.cos(a),
            _HELIX_RADIUS * np.sin(a),
            _HELIX_RISE * t,
        ],
        axis=1,
    ).tolist()


def _make_qvf(payload: dict, **section_extras) -> io.BytesIO:
    """A minimal one-structure archive whose section carries ``section_extras``."""
    struct = json.dumps(payload).encode()
    section = {
        "id": "structure",
        "kind": "structure",
        "members": {
            "structure": {
                "path": "sections/structure.json",
                "format": "json",
                "sha256": hashlib.sha256(struct).hexdigest(),
            }
        },
    }
    section.update(section_extras)
    manifest = {
        "qvf_version": 1,
        "source": {"program": "t", "version": "0", "calculation": "bio"},
        "sections": [section],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("sections/structure.json", struct)
    buf.seek(0)
    return buf


def _ca_chain(n: int, chain: str = "A", first_seq: int = 1) -> list[dict]:
    """``n`` single-CA residues along one chain, positioned as a helix."""
    return [
        {
            "symbol": "C",
            "position": pos,
            "atomic_number": 6,
            "atom_name": "CA",
            "residue_name": "ALA",
            "residue_seq": first_seq + i,
            "chain_id": chain,
        }
        for i, pos in enumerate(_helix_positions(n))
    ]


def _payload(atoms: list[dict]) -> dict:
    return {"atoms": atoms, "pbc": [False, False, False], "lattice_vectors": None}


class TestPDBBFactor:
    def test_b_factor_preserved_from_columns_61_66(self) -> None:
        # cols:        13-16 18-20 22 23-26          31-38   39-46   47-54  55-60 61-66
        pdb = """ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 12.34           N
ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  7.80           C
END
"""
        struct = QVFReader(pdb_to_qvf(pdb.encode())).read_structure()
        assert [a.b_factor for a in struct.atoms] == [12.34, 7.80]

    def test_missing_b_factor_column_is_none_not_zero(self) -> None:
        # A b-factor of 0.00 is a real measurement; an absent column is not,
        # and conflating them would paint unmeasured atoms as the coldest.
        pdb = "ATOM      1  CA  ALA A   1       0.000   0.000   0.000\nEND\n"
        struct = QVFReader(pdb_to_qvf(pdb.encode())).read_structure()
        assert struct.atoms[0].b_factor is None

    def test_zero_b_factor_is_kept_as_zero(self) -> None:
        pdb = """ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C
END
"""
        struct = QVFReader(pdb_to_qvf(pdb.encode())).read_structure()
        assert struct.atoms[0].b_factor == 0.0

    def test_unparseable_b_factor_is_skipped_not_fatal(self) -> None:
        pdb = """ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  ????           C
END
"""
        struct = QVFReader(pdb_to_qvf(pdb.encode())).read_structure()
        assert len(struct.atoms) == 1
        assert struct.atoms[0].b_factor is None


class TestSectionLevelBFactors:
    def test_supplied_array_populates_atoms(self) -> None:
        buf = _make_qvf(_payload(_ca_chain(3)), b_factors=[5.0, 6.0, 7.0])
        struct = QVFReader(buf).read_structure()
        assert [a.b_factor for a in struct.atoms] == [5.0, 6.0, 7.0]

    def test_per_atom_value_wins_over_the_array(self) -> None:
        atoms = _ca_chain(3)
        atoms[1]["b_factor"] = 99.0
        buf = _make_qvf(_payload(atoms), b_factors=[5.0, 6.0, 7.0])
        struct = QVFReader(buf).read_structure()
        assert [a.b_factor for a in struct.atoms] == [5.0, 99.0, 7.0]

    def test_length_mismatch_ignores_the_array_entirely(self) -> None:
        # Applying the prefix would mislabel every atom past the divergence.
        buf = _make_qvf(_payload(_ca_chain(3)), b_factors=[5.0, 6.0])
        struct = QVFReader(buf).read_structure()
        assert [a.b_factor for a in struct.atoms] == [None, None, None]

    def test_absent_array_leaves_b_factors_none(self) -> None:
        struct = QVFReader(_make_qvf(_payload(_ca_chain(2)))).read_structure()
        assert [a.b_factor for a in struct.atoms] == [None, None]


class TestSuppliedResiduesPrecedence:
    def test_supplied_residues_win_over_derivation(self) -> None:
        # The atoms say two residues on chain A; the producer says one
        # residue on chain Z covering both. The producer wins.
        buf = _make_qvf(
            _payload(_ca_chain(2)),
            residues=[
                {"name": "GLY", "seq": 40, "chain": "Z", "atom_indices": [0, 1]}
            ],
        )
        struct = QVFReader(buf).read_structure()
        assert list(struct.chains()) == ["Z"]
        assert struct.chains()["Z"] == [(40, [0, 1])]

    def test_derivation_survives_when_nothing_is_supplied(self) -> None:
        struct = QVFReader(_make_qvf(_payload(_ca_chain(2)))).read_structure()
        assert struct.chains() == {"A": [(1, [0]), (2, [1])]}

    def test_out_of_range_atom_indices_fall_back_to_derivation(self) -> None:
        # A producer bug must not blank the ribbon.
        buf = _make_qvf(
            _payload(_ca_chain(2)),
            residues=[
                {"name": "GLY", "seq": 1, "chain": "Z", "atom_indices": [7, 8]}
            ],
        )
        struct = QVFReader(buf).read_structure()
        assert struct.chains() == {"A": [(1, [0]), (2, [1])]}

    def test_has_residues_true_from_supplied_alone(self) -> None:
        # Atoms with no per-atom residue identity, residues supplied.
        atoms = [
            {"symbol": "C", "position": p, "atomic_number": 6, "atom_name": "CA"}
            for p in _helix_positions(2)
        ]
        buf = _make_qvf(
            _payload(atoms),
            residues=[
                {"name": "GLY", "seq": 1, "chain": "A", "atom_indices": [0, 1]}
            ],
        )
        struct = QVFReader(buf).read_structure()
        assert struct.has_residues is True

    def test_backbone_trace_follows_supplied_residues(self) -> None:
        buf = _make_qvf(
            _payload(_ca_chain(3)),
            residues=[
                {"name": "ALA", "seq": 1, "chain": "A", "atom_indices": [0]},
                {"name": "ALA", "seq": 2, "chain": "A", "atom_indices": [1]},
            ],
        )
        struct = QVFReader(buf).read_structure()
        # The third atom is in no supplied residue, so it is off the spine.
        assert struct.backbone_trace().shape == (2, 3)


class TestSuppliedChains:
    def test_chain_ids_uses_supplied_order(self) -> None:
        buf = _make_qvf(_payload(_ca_chain(2)), chains=["Q", "A"])
        struct = QVFReader(buf).read_structure()
        assert struct.chain_ids() == ["Q", "A"]

    def test_chain_ids_falls_back_to_derived_chains(self) -> None:
        struct = QVFReader(_make_qvf(_payload(_ca_chain(2)))).read_structure()
        assert struct.chain_ids() == ["A"]


class TestSuppliedSecondaryStructurePrecedence:
    def _geometric_helix(self, n: int = 12):
        struct = QVFReader(_make_qvf(_payload(_ca_chain(n)))).read_structure()
        return struct.secondary_structure()

    def test_geometric_assignment_still_calls_an_ideal_helix_a_helix(self) -> None:
        labels = self._geometric_helix()
        assert labels.count("H") >= 8, labels

    def test_supplied_ranges_override_the_geometric_call(self) -> None:
        # Same ideal helix geometry, but the producer says sheet. The
        # producer wins: it knows things CA geometry does not.
        buf = _make_qvf(
            _payload(_ca_chain(12)),
            secondary_structure=[
                {"type": "sheet", "chain": "A", "start_seq": 1, "end_seq": 12}
            ],
        )
        struct = QVFReader(buf).read_structure()
        assert struct.secondary_structure() == ["E"] * 12

    def test_uncovered_residues_are_coil(self) -> None:
        buf = _make_qvf(
            _payload(_ca_chain(6)),
            secondary_structure=[
                {"type": "helix", "chain": "A", "start_seq": 2, "end_seq": 4}
            ],
        )
        struct = QVFReader(buf).read_structure()
        assert struct.secondary_structure() == ["C", "H", "H", "H", "C", "C"]

    def test_labels_line_up_with_the_trace(self) -> None:
        buf = _make_qvf(
            _payload(_ca_chain(9)),
            secondary_structure=[
                {"type": "helix", "chain": "A", "start_seq": 1, "end_seq": 9}
            ],
        )
        struct = QVFReader(buf).read_structure()
        assert len(struct.secondary_structure("A")) == len(struct.backbone_trace("A"))

    def test_precedence_is_per_chain(self) -> None:
        # Chain A annotated, chain B not: B keeps its geometric assignment
        # rather than being flattened to coil.
        atoms = _ca_chain(12, chain="A") + _ca_chain(12, chain="B")
        buf = _make_qvf(
            _payload(atoms),
            secondary_structure=[
                {"type": "coil", "chain": "A", "start_seq": 1, "end_seq": 12}
            ],
        )
        struct = QVFReader(buf).read_structure()
        assert struct.secondary_structure("A") == ["C"] * 12
        assert struct.secondary_structure("B").count("H") >= 8

    def test_empty_supplied_list_falls_back_to_geometry(self) -> None:
        buf = _make_qvf(_payload(_ca_chain(12)), secondary_structure=[])
        struct = QVFReader(buf).read_structure()
        assert struct.secondary_structure().count("H") >= 8

    @pytest.mark.parametrize("bad", [{"type": "helix", "chain": "A"}, "nonsense", 7])
    def test_malformed_range_in_an_archive_is_refused_at_open(self, bad) -> None:
        # Since 2026-07-26 the manifest schema names these shapes, so a
        # malformed range is caught by the open-time validation gate rather
        # than reaching the reader at all.
        buf = _make_qvf(_payload(_ca_chain(12)), secondary_structure=[bad])
        with pytest.raises(ManifestValidationError):
            QVFReader(buf)

    @pytest.mark.parametrize("bad", [{"type": "helix", "chain": "A"}, "nonsense", 7])
    def test_malformed_range_in_memory_falls_back_to_geometry(self, bad) -> None:
        # A StructureData can also be built directly, bypassing the schema
        # gate. One unusable entry must not raise out of a ribbon draw.
        struct = QVFReader(_make_qvf(_payload(_ca_chain(12)))).read_structure()
        struct.supplied_secondary_structure = [bad]
        assert len(struct.secondary_structure()) == 12


class TestPlainStructureUnaffected:
    def test_a_structure_with_no_biomolecule_fields_reads_as_before(self) -> None:
        atoms = [
            {"symbol": "O", "position": [0.0, 0.0, 0.117], "atomic_number": 8},
            {"symbol": "H", "position": [0.0, 0.757, -0.469], "atomic_number": 1},
        ]
        struct = QVFReader(_make_qvf(_payload(atoms))).read_structure()
        assert struct.has_residues is False
        assert struct.chains() == {}
        assert struct.chain_ids() == []
        assert struct.backbone_trace().shape == (0, 3)
        assert struct.secondary_structure() == []
        assert struct.supplied_residues is None
        assert struct.supplied_chains is None
        assert struct.supplied_secondary_structure is None
        assert all(a.b_factor is None for a in struct.atoms)

    def test_edit_overlay_drops_supplied_metadata(self) -> None:
        # Supplied atom_indices index the file's atom list, which the
        # overlay has replaced, so they are as stale as the bond indices.
        buf = _make_qvf(
            _payload(_ca_chain(2)),
            residues=[
                {"name": "GLY", "seq": 1, "chain": "A", "atom_indices": [0, 1]}
            ],
            chains=["A"],
            secondary_structure=[
                {"type": "helix", "chain": "A", "start_seq": 1, "end_seq": 2}
            ],
        )
        reader = QVFReader(buf)
        reader.set_edit_overlay([[0.0, 0.0, 0.0]], ["C"])
        struct = reader.read_structure()
        assert struct.supplied_residues is None
        assert struct.supplied_chains is None
        assert struct.supplied_secondary_structure is None
