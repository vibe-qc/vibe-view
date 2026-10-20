"""SHA-valid malformed geometry and connectivity fail before reaching consumers."""

import copy
import hashlib
import io
import json
import zipfile

import pytest

from vibeview.qvf import QVFError, QVFReader

BASE = {
    "atoms": [{"symbol": "C", "atomic_number": 6, "position": [x, 0, 0]} for x in (0.0, 0.9, 1.8)],
    "pbc": [False, False, False],
}
CELL = [[6, 0, 0], [0, 6, 0], [0, 0, 6]]


def _archive(
    payload=None,
    *,
    bonds=None,
    orders=None,
    inline=False,
    standalone=False,
    structure_id="structure",
):
    blobs = {}

    def member(sid, name, raw):
        path = f"{sid}/{name}.json"
        data = json.dumps(raw).encode()
        blobs[path] = data
        return {"path": path, "format": "json", "sha256": hashlib.sha256(data).hexdigest()}

    structure = {
        "id": "structure",
        "kind": "structure",
        "members": {
            "structure": member("structure", "structure", BASE if payload is None else payload)
        },
    }
    structure["id"] = structure_id
    sections = [] if standalone else [structure]
    if bonds is not None:
        sid = structure_id if inline else "connectivity"
        spec = member(sid, "bonds", bonds)
        if inline:
            structure["members"]["bonds"] = spec
        else:
            sections.append({"id": sid, "kind": "bonds", "members": {"bonds": spec}})
    if orders is not None:
        sections.append(
            {
                "id": "orders",
                "kind": "bond_orders",
                "members": {
                    "bond_orders": member("orders", "bond_orders", orders),
                },
            }
        )
    manifest = {
        "qvf_version": 1,
        "source": {"program": "example", "version": "1", "calculation": "test"},
        "sections": sections,
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for path, data in blobs.items():
            archive.writestr(path, data)
    return out.getvalue()


@pytest.mark.parametrize(
    "patch",
    [
        {"atoms": [{}]},
        {"atoms": [{"symbol": "", "atomic_number": 1, "position": [0, 0, 0]}]},
        {"atoms": [{"symbol": 1, "atomic_number": 1, "position": [0, 0, 0]}]},
        {"atoms": [{"symbol": "H", "atomic_number": 1, "position": [0, 0]}]},
        {"atoms": [{"symbol": "H", "atomic_number": True, "position": [0, 0, 0]}]},
        {"atoms": [{"symbol": "H", "atomic_number": 1, "position": [float("nan"), 0, 0]}]},
        {"pbc": "xyz", "lattice_vectors": CELL},
        {"pbc": [True], "lattice_vectors": CELL},
        {"pbc": [1, 0, 0], "lattice_vectors": CELL},
        {"dimensionality": 0.5},
        {"dimensionality": "abc"},
        {"dimensionality": False},
        {"pbc": [True, False, False]},
        {"pbc": [True, False, False], "lattice_vectors": [[6, 0], [0, 6]]},
        {"pbc": [True, False, False], "lattice_vectors": [[6, 0, 0], [0, 0, 0], [0, 0, 0]]},
        {"lattice_vectors": [[float("inf"), 0, 0], [0, 6, 0], [0, 0, 6]]},
    ],
)
def test_invalid_structure_is_contextual_and_recorded(patch):
    with QVFReader(_archive({**BASE, **patch})) as reader:
        with pytest.raises(QVFError, match="structure member in section 'structure'"):
            reader.read_structure()
        assert reader.section_error("structure")
        assert reader.section_error_kind("structure") == "invalid payload"


@pytest.mark.parametrize(
    "pair",
    [
        {"i": -1, "j": 0},
        {"i": 0, "j": 3},
        {"i": 0.5, "j": 1},
        {"i": True, "j": 1},
        {"i": "0", "j": 1},
        {"j": 1},
        {"i": 0, "j": 1, "order": "bad"},
        {"i": 0, "j": 1, "order": True},
        {"i": 0, "j": 1, "order": float("nan")},
    ],
)
@pytest.mark.parametrize("inline", [False, True])
def test_bond_pairs_reject_coercion_and_bad_bounds(pair, inline):
    with QVFReader(_archive(bonds={"pairs": [pair]}, inline=inline)) as reader:
        sid = "structure" if inline else "connectivity"
        with pytest.raises(QVFError, match=f"section '{sid}'.*pair 0"):
            reader.read_structure()
        assert reader.section_error_kind(sid) == "invalid payload"


@pytest.mark.parametrize(
    "pairs",
    [
        [1],
        [{"i": -1, "j": 0}],
        [{"i": 0, "j": 99}],
        [{"i": True, "j": 1}],
        [{"i": 0.5, "j": 1}],
        [{"i": 0, "j": 1, "order": "bad"}],
        None,
    ],
)
def test_analysis_validation_is_shared_by_read_and_enrichment(pairs):
    raw = _archive(orders={"method": "mayer", "pairs": pairs})
    for enrich in (False, True):
        with QVFReader(raw) as reader:
            with pytest.raises(QVFError, match="bond_orders member in section 'orders'"):
                if enrich:
                    reader.connectivity(reader.read_structure())
                else:
                    reader.read_bond_orders("orders")
            assert reader.section_error_kind("orders") == "invalid payload"


def test_actual_geometry_consumers_fail_before_drawing_wrong_atoms():
    from vibeview.renderers.structure import StructureRenderer
    from vibeview.tui.scene import structure_scene

    for consume in (
        structure_scene,
        lambda r: StructureRenderer(r.get_section("structure"), r).load(),
    ):
        with (
            QVFReader(_archive(bonds={"pairs": [{"i": -1, "j": 0}]})) as reader,
            pytest.raises(QVFError, match="out of range"),
        ):
            consume(reader)


def test_actual_analysis_consumers_fail_before_mislabeling_atoms():
    from vibeview.renderers.bond_orders import BondOrdersRenderer
    from vibeview.tui.panes import bond_orders

    consumers = (
        lambda r: BondOrdersRenderer(r.get_section("orders"), r).render_to_html(),
        lambda r: bond_orders(r, "orders"),
    )
    for consume in consumers:
        with (
            QVFReader(_archive(orders={"pairs": [{"i": True, "j": 1, "order": 2}]})) as reader,
            pytest.raises(QVFError, match="must be an integer"),
        ):
            consume(reader)


@pytest.mark.parametrize("pbc,dim", [(None, 2), ([True, False, True], 2), ([False] * 3, 0)])
def test_legacy_and_nonleading_periodicity_still_work(pbc, dim):
    payload = {"atoms": BASE["atoms"], "dimensionality": dim, "lattice_vectors": CELL}
    if pbc is not None:
        payload["pbc"] = pbc
    with QVFReader(_archive(payload)) as reader:
        assert reader.read_structure().pbc == tuple(pbc if pbc is not None else [True, True, False])


def test_placeholders_metadata_integral_numbers_and_inline_order_survive():
    payload = copy.deepcopy(BASE)
    payload["atoms"][0].update(symbol="X", atomic_number=0.0, residue_seq="12", x_note="keep")
    with QVFReader(
        _archive(payload, bonds={"pairs": [{"i": 0.0, "j": 1.0, "order": 1.5}]}, inline=True)
    ) as reader:
        structure = reader.read_structure()
        assert structure.atoms[0].atomic_number == 0
        assert structure.atoms[0].residue_seq == 12
        assert structure.bonds == [(0, 1, 1.5, (0, 0, 0))]


def test_empty_molecule_and_omitted_explicit_order_survive():
    with QVFReader(_archive({"atoms": []})) as reader:
        assert reader.read_structure().atoms == []
    with QVFReader(_archive(bonds={"pairs": [{"i": 0, "j": 1}]})) as reader:
        assert reader.read_bonds("connectivity") == [(0, 1, 1.0, (0, 0, 0))]


def test_standalone_analysis_retains_metadata_and_finite_negative_orders():
    pairs = [
        {"i": 0, "j": 99, "order": -0.2, "symbol_i": "H", "distance_ang": 2.0},
        {"i": 1, "j": 2},
    ]
    with QVFReader(_archive(orders={"method": "mayer", "pairs": pairs}, standalone=True)) as reader:
        assert reader.read_bond_orders("orders").pairs == pairs


@pytest.mark.parametrize("n_edited", [1, 2, 4])
def test_analysis_bounds_use_original_file_not_edit_overlay(n_edited):
    with QVFReader(_archive(orders={"pairs": [{"i": 1, "j": 2, "order": 2}]})) as reader:
        reader.set_edit_overlay([[i * 0.9, 0, 0] for i in range(n_edited)], ["C"] * n_edited)
        assert reader.read_bond_orders("orders").pairs == [{"i": 1, "j": 2, "order": 2.0}]
        reader.connectivity(reader.read_structure())
        assert reader.section_error("orders") is None


def test_larger_overlay_cannot_make_invalid_file_analysis_valid():
    with QVFReader(_archive(orders={"pairs": [{"i": 0, "j": 3, "order": 2}]})) as reader:
        reader.set_edit_overlay([[i * 0.9, 0, 0] for i in range(4)], ["C"] * 4)
        with pytest.raises(QVFError, match="for 3 atoms"):
            reader.connectivity(reader.read_structure())


def test_validation_success_cache_rechecks_bytes_and_returns_fresh_geometry(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from vibeview import qvf

    validate = Mock(wraps=qvf._STRUCTURE_VALIDATORS[1].validate)
    monkeypatch.setitem(qvf._STRUCTURE_VALIDATORS, 1, SimpleNamespace(validate=validate))
    with QVFReader(_archive()) as reader:
        verify = Mock(wraps=reader._verify_and_read)
        monkeypatch.setattr(reader, "_verify_and_read", verify)
        first = reader.read_structure()
        first.atoms[0].position[0] = 100
        second = reader.read_structure()
        assert second.atoms[0].position[0] == 0
        assert validate.call_count == 1
        assert verify.call_count == 2


def test_failed_validation_is_not_cached(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from vibeview import qvf

    validate = Mock(wraps=qvf._STRUCTURE_VALIDATORS[1].validate)
    monkeypatch.setitem(qvf._STRUCTURE_VALIDATORS, 1, SimpleNamespace(validate=validate))
    with QVFReader(_archive({"atoms": [{}]})) as reader:
        for _ in range(2):
            with pytest.raises(QVFError):
                reader.read_structure()
        assert validate.call_count == 2


def test_inline_pairs_use_their_explicit_structure_id():
    raw = _archive(bonds={"pairs": [{"i": 0, "j": 3}]}, inline=True, structure_id="geometry")
    for direct in (False, True):
        with (
            QVFReader(raw) as reader,
            pytest.raises(QVFError, match="section 'geometry'.*for 3 atoms"),
        ):
            if direct:
                reader.read_bonds("geometry")
            else:
                reader.read_structure("geometry")


def test_unbound_analysis_is_checked_against_the_only_structure_whatever_its_id():
    # Section ids carry no meaning (QVF spec § 4.1, #90): the archive's only
    # structure bounds an unreferenced analysis whether or not it is called
    # "structure". With several structures it is not guessed
    # (tests/test_issue90_structures.py).
    with QVFReader(
        _archive(
            orders={"pairs": [{"i": 0, "j": 99, "order": 1}]},
            structure_id="geometry",
        )
    ) as reader:
        assert reader.read_structure("geometry").atoms
        with pytest.raises(QVFError, match="for 3 atoms"):
            reader.read_bond_orders("orders")


@pytest.mark.parametrize("symbol", ["Z999", "custom display label"])
def test_long_display_labels_preserve_consumer_compatibility(symbol):
    from vibeview.qvf import _MANIFEST_SCHEMA

    payload = copy.deepcopy(BASE)
    payload["atoms"][0]["symbol"] = symbol
    with QVFReader(_archive(payload)) as reader:
        assert reader.read_structure().atoms[0].symbol == symbol
    assert _MANIFEST_SCHEMA["$defs"]["StructureAtom"]["properties"]["symbol"]["maxLength"] == 3
