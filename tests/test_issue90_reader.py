"""Structures are found by kind, never by the id ``structure`` (#90).

QVF spec § 4.1: section ids carry no meaning. vibe-sketch names the species
of a document ``structure_m1``, ``structure_m2``, ... and no section
``structure``, which vibe-view looked up by that id, so a sketch opened
empty and ``export`` failed with "section not found: 'structure'". A
consumer that shows one structure uses the *first structure*: the first
``structure`` section ``viewer_defaults.auto_open`` lists, else the first
``structure`` section. These rows pin the reader side: the first and the
shown structure, the chemical identity of § 5.1, edits that stay with their
structure, references by kind, the qvf 0.2 kinds ``reaction.scheme`` and
``preview``, and the archive writers that copy sections.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests._qvf_builders import write_archive
from tests._qvf_corpus import qvf_corpus_dir, requires_qvf_corpus
from vibeview.qvf import QVFError, QVFReader, SectionNotFoundError

WATER = [
    ("O", 8, [0.0, 0.0, 0.117]),
    ("H", 1, [0.0, 0.757, -0.469]),
    ("H", 1, [0.0, -0.757, -0.469]),
]
AMMONIUM = [
    ("N", 7, [0.0, 0.0, 0.0]),
    ("H", 1, [0.629, 0.629, 0.629]),
    ("H", 1, [-0.629, -0.629, 0.629]),
    ("H", 1, [-0.629, 0.629, -0.629]),
    ("H", 1, [0.629, -0.629, -0.629]),
]
HYDROXYL = [("O", 8, [0.0, 0.0, 0.0]), ("H", 1, [0.0, 0.0, 0.97])]

#: (section id, atoms, identity on the section object)
SPECIES = [
    ("structure_m1", WATER, {"label": "1", "name": "water", "charge": 0, "multiplicity": 1}),
    (
        "structure_m2",
        AMMONIUM,
        {"label": "2", "name": "ammonium", "charge": 1, "multiplicity": 1, "smiles": "[NH4+]"},
    ),
    ("structure_m3", HYDROXYL, {"label": "3", "name": "hydroxyl", "multiplicity": 2}),
]

SCHEME = {
    "steps": [
        {
            "id": "r1",
            "label": "a",
            "reactants": [{"structure_ref": "structure_m2", "coefficient": 2}],
            "products": [{"structure_ref": "structure_m1"}],
            "reagents": [{"structure_ref": "structure_m3"}],
            "arrow": "equilibrium",
            "conditions": ["THF", "2 h"],
            "yield_percent": 87,
            "yield_text": "87%",
            "x_vendor": {"ignored": True},
        },
        {
            "id": "r2",
            "reactants": [{"structure_ref": "structure_m3"}],
            "products": [{"structure_ref": "structure_m1"}],
            "arrow": "retrosynthetic",
        },
    ]
}
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 24
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'/>"


def _json_member(files: dict[str, bytes], path: str, payload: object) -> dict[str, str]:
    data = json.dumps(payload).encode()
    files[path] = data
    return {"path": path, "format": "json", "sha256": hashlib.sha256(data).hexdigest()}


def _opaque_member(files: dict[str, bytes], path: str, data: bytes, media_type: str) -> dict:
    files[path] = data
    return {
        "path": path,
        "format": "binary",
        "sha256": hashlib.sha256(data).hexdigest(),
        "media_type": media_type,
    }


def species_archive(
    dest: Path,
    *,
    identity: bool = True,
    species: list[tuple[str, list, dict]] | None = None,
    scheme: dict | None = None,
    preview: bool = False,
    extra_sections: list[dict] | None = None,
    **manifest_extra: object,
) -> Path:
    """An archive like a vibe-sketch document: one structure per species, none
    called ``structure``; optionally a scheme, a preview and further sections."""
    files: dict[str, bytes] = {}
    sections: list[dict] = []
    if preview:
        sections.append(
            {
                "id": "preview",
                "kind": "preview",
                "label": "scheme",
                "members": {
                    "thumbnail": _opaque_member(files, "preview/t.png", PNG, "image/png"),
                    "image": _opaque_member(files, "preview/i.svg", SVG, "image/svg+xml"),
                },
            }
        )
    for section_id, atoms, ident in species if species is not None else SPECIES:
        payload = {
            "atoms": [
                {"symbol": s, "atomic_number": z, "position": p} for s, z, p in atoms
            ],
            "pbc": [False, False, False],
        }
        section = {
            "id": section_id,
            "kind": "structure",
            "members": {
                "structure": _json_member(files, f"{section_id}/structure.json", payload)
            },
        }
        if identity:
            section.update(ident)
        sections.append(section)
    if scheme is not None:
        sections.append(
            {
                "id": "scheme",
                "kind": "reaction.scheme",
                "label": "Proton shuffle",
                "members": {"scheme": _json_member(files, "scheme/scheme.json", scheme)},
            }
        )
    for section in extra_sections or ():
        members = {
            name: _json_member(files, f"{section['id']}/{name}.json", payload)
            for name, payload in section.pop("_json").items()
        }
        sections.append({**section, "members": members})
    return write_archive(
        sections,
        files,
        dest=dest,
        source={"program": "vibe-sketch", "version": "0.1.0", "calculation": ""},
        **manifest_extra,
    )


@pytest.fixture
def sketch(tmp_path) -> Path:
    return species_archive(tmp_path / "sketch.qvf", identity=False)


@pytest.fixture
def scheme(tmp_path) -> Path:
    return species_archive(tmp_path / "scheme.qvf", scheme=SCHEME, preview=True)


# ── the first structure and the shown one ──────────────────────────────────


def test_an_archive_without_a_structure_id_opens_on_its_first_structure(sketch):
    with QVFReader(sketch) as reader:
        assert not reader.has_section("structure")
        assert reader.first_structure_id == "structure_m1"
        assert reader.shown_structure_id == "structure_m1"
        assert reader.shown_structure is not None
        assert [s.id for s in reader.structure_sections] == [
            "structure_m1", "structure_m2", "structure_m3",
        ]
        assert [a.symbol for a in reader.read_structure().atoms] == ["O", "H", "H"]


def test_auto_open_chooses_a_later_structure(tmp_path):
    path = species_archive(
        tmp_path / "a.qvf",
        # The first entry names no structure; an unknown id is ignored.
        viewer_defaults={"auto_open": ["missing", "structure_m3", "structure_m2"]},
    )
    with QVFReader(path) as reader:
        assert reader.first_structure_id == "structure_m3"
        assert len(reader.read_structure().atoms) == 2


def test_a_section_named_structure_first_keeps_the_old_behaviour(tmp_path):
    species = [("structure", WATER, {}), ("other", AMMONIUM, {})]
    with QVFReader(species_archive(tmp_path / "a.qvf", species=species)) as reader:
        assert reader.first_structure_id == "structure"
        assert len(reader.read_structure().atoms) == 3
        assert len(reader.read_structure("structure").atoms) == 3


def test_show_structure_changes_what_read_structure_returns(sketch):
    with QVFReader(sketch) as reader:
        revision = reader.geometry_revision
        reader.show_structure("structure_m2")
        assert reader.shown_structure_id == "structure_m2"
        assert len(reader.read_structure().atoms) == 5
        assert reader.geometry_revision > revision, "memoized structures must not go stale"
        reader.show_structure(None)
        assert reader.shown_structure_id == "structure_m1"


def test_show_structure_refuses_other_kinds_and_unknown_ids(scheme):
    with QVFReader(scheme) as reader:
        with pytest.raises(SectionNotFoundError):
            reader.show_structure("structure")
        with pytest.raises(QVFError, match="not a structure"):
            reader.show_structure("scheme")
        assert reader.shown_structure_id == "structure_m1"


def test_an_archive_without_structures_says_so(tmp_path):
    path = species_archive(tmp_path / "a.qvf", species=[], scheme=None, preview=True)
    with QVFReader(path) as reader:
        assert reader.first_structure_id is None
        assert reader.shown_structure is None
        with pytest.raises(SectionNotFoundError, match="no structure section"):
            reader.read_structure()


# ── chemical identity (spec § 5.1) ─────────────────────────────────────────


def test_identity_is_read_from_the_section_object(scheme):
    with QVFReader(scheme) as reader:
        ammonium = reader.structure_identity("structure_m2")
        assert (ammonium.label, ammonium.name, ammonium.charge) == ("2", "ammonium", 1)
        assert ammonium.smiles == "[NH4+]"
        assert ammonium.title == "2 · ammonium"
        assert reader.structure_identity().title == "1 · water"  # the shown one
        assert reader.structure_identity("scheme").title == ""


def test_declared_charge_takes_the_structure_before_provenance(tmp_path):
    species = [("s", HYDROXYL, {"charge": -1})]
    path = species_archive(
        tmp_path / "a.qvf", species=species, provenance={"charge": 0, "multiplicity": 1}
    )
    with QVFReader(path) as reader:
        # Charge from the structure, multiplicity (unstated there) from provenance.
        assert reader.declared_charge_multiplicity() == (-1, 1)


def test_provenance_speaks_for_no_structure_of_several(tmp_path):
    path = species_archive(
        tmp_path / "a.qvf", identity=False, provenance={"charge": 1, "multiplicity": 2}
    )
    with QVFReader(path) as reader:
        assert reader.declared_charge_multiplicity() == (None, None)
    with QVFReader(species_archive(tmp_path / "b.qvf")) as reader:
        reader.show_structure("structure_m2")
        assert reader.declared_charge_multiplicity() == (1, 1)


# ── edits stay with their structure ────────────────────────────────────────


def test_an_edit_belongs_to_the_structure_it_was_made_on(sketch):
    with QVFReader(sketch) as reader:
        reader.set_edit_overlay([[0.0, 0.0, 0.0]], ["He"])
        assert reader.edited_structure_ids == ["structure_m1"]
        reader.show_structure("structure_m2")
        assert len(reader.read_structure().atoms) == 5, "another structure shows the file"
        assert reader.has_edit_overlay
        reader.show_structure("structure_m1")
        assert [a.symbol for a in reader.read_structure().atoms] == ["He"]
        assert [a.symbol for a in reader.read_structure("structure_m1").atoms] == ["He"]
        reader.clear_edit_overlay("structure_m1")
        assert not reader.has_edit_overlay
        assert len(reader.read_structure().atoms) == 3


def test_edits_of_two_structures_coexist_and_clear_together(sketch):
    with QVFReader(sketch) as reader:
        reader.set_edit_overlay([[0.0, 0.0, 0.0]], ["He"])
        reader.set_edit_overlay([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]], ["H", "H"],
                                section_id="structure_m3")
        assert sorted(reader.edited_structure_ids) == ["structure_m1", "structure_m3"]
        assert len(reader.read_structure("structure_m3").atoms) == 2
        reader.clear_edit_overlay()
        assert reader.edited_structure_ids == []


def test_an_edit_needs_a_structure(tmp_path):
    path = species_archive(tmp_path / "a.qvf", species=[], preview=True)
    with QVFReader(path) as reader, pytest.raises(ValueError, match="no structure"):
        reader.set_edit_overlay([[0.0, 0.0, 0.0]], ["He"])


# ── references by kind ─────────────────────────────────────────────────────


def _bonds_section(section_id: str, pairs: list, **extra: object) -> dict:
    return {"id": section_id, "kind": "bonds", "_json": {"bonds": {"pairs": pairs}}, **extra}


def test_a_standalone_bonds_section_joins_the_structure_it_names(tmp_path):
    path = species_archive(
        tmp_path / "a.qvf",
        identity=False,
        extra_sections=[
            _bonds_section("b_m2", [{"i": 0, "j": 1, "order": 1}], structure_ref="structure_m2")
        ],
    )
    with QVFReader(path) as reader:
        assert reader.read_structure("structure_m1").bonds is None
        assert reader.read_structure("structure_m2").bonds == [(0, 1, 1, (0, 0, 0))]


def test_an_unreferenced_bonds_section_joins_the_first_structure(tmp_path):
    path = species_archive(
        tmp_path / "a.qvf",
        identity=False,
        extra_sections=[_bonds_section("b", [{"i": 0, "j": 2}])],
    )
    with QVFReader(path) as reader:
        assert reader.read_structure().bonds == [(0, 2, 1.0, (0, 0, 0))]
        assert reader.read_structure("structure_m2").bonds is None


def test_an_unreferenced_analysis_among_several_structures_is_not_bounded_by_a_guess(tmp_path):
    orders = {"id": "bo", "kind": "bond_orders",
              "_json": {"bond_orders": {"pairs": [{"i": 0, "j": 4, "order": 1.0}]}}}
    with QVFReader(species_archive(tmp_path / "a.qvf", extra_sections=[orders])) as reader:
        assert reader.read_bond_orders("bo").pairs[0]["j"] == 4
    named = {**orders, "structure_ref": "structure_m3",
             "_json": {"bond_orders": {"pairs": [{"i": 0, "j": 4, "order": 1.0}]}}}
    with (
        QVFReader(species_archive(tmp_path / "b.qvf", extra_sections=[named])) as reader,
        pytest.raises(QVFError, match="for 2 atoms"),
    ):
        reader.read_bond_orders("bo")


def test_structure_ref_of_follows_the_reference_else_the_first_structure(tmp_path):
    extra = [
        {"id": "named", "kind": "x_test.thing", "structure_ref": "structure_m2", "_json": {}},
        {"id": "plain", "kind": "x_test.thing", "_json": {}},
    ]
    with QVFReader(species_archive(tmp_path / "a.qvf", extra_sections=extra)) as reader:
        assert reader.structure_ref_of("named") == "structure_m2"
        assert reader.structure_ref_of("plain") == "structure_m1"
        assert reader.structure_ref_of("structure_m3") == "structure_m3"


@requires_qvf_corpus("bonds_structure_ref.qvf")
def test_corpus_bonds_join_the_structure_their_structure_ref_names():
    """qvf v0.3.0 (qvf#4, spec § 5.1): a standalone bonds section's indices
    address its ``structure_ref``, else the first structure (#43, 35-15)."""
    corpus = qvf_corpus_dir()
    assert corpus is not None
    with QVFReader(corpus / "bonds_structure_ref.qvf") as reader:
        assert reader.read_structure("structure_water").bonds == [
            (0, 1, 1.0, (0, 0, 0)), (0, 2, 1.0, (0, 0, 0)),
        ]
        assert reader.read_structure("structure_h2").bonds == [(0, 1, 1.0, (0, 0, 0))]


@requires_qvf_corpus("multi_structure.qvf")
def test_corpus_wavefunction_is_centred_on_the_structure_its_section_names():
    corpus = qvf_corpus_dir()
    assert corpus is not None
    with QVFReader(corpus / "multi_structure.qvf") as reader:
        assert reader.first_structure_id == "h2o"
        assert reader.read_wavefunction_gto("wf_nh3").structure_ref == "nh3"


# ── reaction.scheme and preview (qvf 0.2) ──────────────────────────────────


def test_reaction_scheme_is_read_with_roles_arrows_and_yield(scheme):
    with QVFReader(scheme) as reader:
        data = reader.read_reaction_scheme("scheme")
    first, second = data.steps
    assert [p.structure_ref for p in first.reactants] == ["structure_m2"]
    assert first.reactants[0].coefficient == 2.0
    assert [p.structure_ref for p in first.reagents] == ["structure_m3"]
    assert (first.arrow, first.conditions, first.yield_percent, first.yield_text) == (
        "equilibrium", ["THF", "2 h"], 87.0, "87%",
    )
    assert (second.arrow, second.label, second.reagents) == ("retrosynthetic", None, [])


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([], "non-empty"),
        ([{"id": "r", "reactants": [{"structure_ref": "nope"}],
           "products": [{"structure_ref": "structure_m1"}]}], "names no structure"),
        ([{"id": "r", "reactants": [{"structure_ref": "scheme"}],
           "products": [{"structure_ref": "structure_m1"}]}], "names no structure"),
        ([{"id": "r", "reactants": [{"structure_ref": "structure_m1"}],
           "products": [{"structure_ref": "structure_m2"}]}] * 2, "occurs twice"),
        ([{"id": "r", "reactants": [{"structure_ref": "structure_m1"}],
           "products": [{"structure_ref": "structure_m2"}], "arrow": "curly"}], "arrow"),
        ([{"id": "r", "reactants": [{"structure_ref": "structure_m1", "coefficient": 0}],
           "products": [{"structure_ref": "structure_m2"}]}], "coefficient"),
    ],
)
def test_a_malformed_scheme_is_a_section_error(tmp_path, steps, message):
    path = species_archive(tmp_path / "a.qvf", scheme={"steps": steps})
    with QVFReader(path) as reader:
        with pytest.raises(QVFError, match=message):
            reader.read_reaction_scheme("scheme")
        assert reader.section_error("scheme")


def test_preview_members_come_with_their_declared_type(scheme):
    with QVFReader(scheme) as reader:
        thumbnail = reader.read_preview("preview")
        image = reader.read_preview("preview", "image")
    assert thumbnail is not None and image is not None
    assert (thumbnail.media_type, thumbnail.data) == ("image/png", PNG)
    assert image.media_type == "image/svg+xml"


def test_descriptive_kinds_are_known_not_unsupported(scheme):
    from vibeview.banner import format_banner
    from vibeview.kinds import classify_section

    assert classify_section("reaction.scheme") == ("rendered", None)
    assert classify_section("preview") == ("rendered", None)
    with QVFReader(scheme) as reader:
        assert "unsupported" not in format_banner(reader)


# ── archive writers keep references by kind ────────────────────────────────


def test_slice_and_merge_need_no_structure_id(tmp_path, sketch):
    from vibeview.api import slice_qvf
    from vibeview.archive import merge_archives

    merged = merge_archives([sketch, sketch], tmp_path / "merged.qvf")
    with QVFReader(merged) as reader:
        assert len(reader.structure_sections) == 6
        assert reader.first_structure_id == "structure_m1"
    sliced = slice_qvf(sketch, tmp_path / "sliced.qvf", keep=["structure_m2"])
    with QVFReader(sliced) as reader:
        assert len(reader.read_structure().atoms) == 5


def test_merge_renames_scheme_participants_and_section_references(tmp_path, scheme):
    from vibeview.archive import merge_archives

    extra = [{"id": "q", "kind": "x_test.thing", "structure_ref": "structure_m2", "_json": {}}]
    second = species_archive(tmp_path / "second.qvf", scheme=SCHEME, extra_sections=extra)
    merged = merge_archives([scheme, second], tmp_path / "merged.qvf")
    with QVFReader(merged) as reader:
        renamed = reader.read_reaction_scheme("scheme_1")
        assert renamed.steps[0].reactants[0].structure_ref == "structure_m2_1"
        assert reader.get_section("q").model_extra["structure_ref"] == "structure_m2_1"
        assert reader.read_reaction_scheme("scheme").steps[0].products[0].structure_ref == (
            "structure_m1"
        )


def test_slice_refuses_to_strand_a_scheme_participant(tmp_path, scheme):
    from vibeview.api import slice_qvf

    with pytest.raises(ValueError, match="Invalid slice.*names no structure"):
        slice_qvf(scheme, tmp_path / "out.qvf", drop=["structure_m3"])
    assert not (tmp_path / "out.qvf").exists()
