"""Writer ↔ viewer kind-registry drift guard (audit finding A6-03).

The `bonds` gap (writer emits it, viewer's SUPPORTED_KINDS omitted it, banner
mislabeled it "skipped, unsupported") survived because no test tied the two
registries together. This test pins them: every kind the writer can emit must
be either renderable by the viewer or explicitly accounted for.
"""

from __future__ import annotations

import pytest

from vibeview.kinds import (
    DEFERRED_KINDS,
    DESCRIPTIVE_KINDS,
    SUPPORTED_KINDS,
    classify_section,
)

# Kinds the writer emits that the viewer does NOT render as a standalone
# panel, but handles another way. `bonds` connectivity is folded into the
# structure renderer (see read_structure / classify_section).
_RENDERED_ELSEWHERE = {"bonds"}


def test_no_writer_kind_is_silently_unsupported():
    impl = pytest.importorskip(
        "vibeqc.output.formats.qvf", reason="vibeqc not importable in this env"
    )._IMPLEMENTED_KINDS
    # A writer kind is "accounted for" if the viewer renders it (SUPPORTED_KINDS),
    # folds it into another panel (_RENDERED_ELSEWHERE), or explicitly defers its
    # renderer (DEFERRED_KINDS → surfaced as "skipped, not yet rendered"). Anything
    # left over would silently reach users as "skipped, unsupported" — the drift
    # this guard exists to catch.
    missing = (
        set(impl) - set(SUPPORTED_KINDS) - _RENDERED_ELSEWHERE - set(DEFERRED_KINDS)
    )
    assert not missing, (
        f"writer emits {sorted(missing)} but the viewer neither renders them "
        "(SUPPORTED_KINDS), folds them in (_RENDERED_ELSEWHERE), nor explicitly "
        "defers them (vibeview.kinds.DEFERRED_KINDS) — they would be reported to "
        "users as 'skipped, unsupported'. Add a renderer or list them as deferred."
    )


def test_deferred_kinds_are_surfaced_as_not_yet_rendered():
    """A deferred kind (writer emits + schema validates, viewer renderer pending)
    must classify as a distinct, honest 'skipped, not yet rendered' — never the
    generic 'unsupported' (which implies the viewer can never handle it) and never
    falsely claimed as rendered. Needs no producer import (registry-only)."""
    assert DEFERRED_KINDS.isdisjoint(SUPPORTED_KINDS), (
        "a kind cannot be both rendered and deferred"
    )
    for kind in DEFERRED_KINDS:
        assert classify_section(kind) == ("skipped", "not yet rendered")


def test_excited_state_table_is_rendered():
    """#109 rendered the state table, so the kind left DEFERRED_KINDS (#96)."""
    assert "excited_states" in SUPPORTED_KINDS
    assert "excited_states" not in DEFERRED_KINDS
    assert classify_section("excited_states") == ("rendered", None)


def test_no_viewer_kind_is_unwritable():
    qvf = pytest.importorskip("vibeqc.output.formats.qvf")
    impl = set(qvf._IMPLEMENTED_KINDS) | set(getattr(qvf, "_RESERVED_KINDS", set()))
    # reaction.scheme and preview describe an archive (a scheme, a picture);
    # the qvf reference writers and vibe-sketch emit them, vibe-qc need not.
    orphan = set(SUPPORTED_KINDS) - impl - set(DESCRIPTIVE_KINDS)
    assert not orphan, (
        f"viewer claims to support {sorted(orphan)} but the writer can neither "
        "emit nor reserve them — a renderer with no producer."
    )


def test_bonds_classified_as_rendered_via_structure():
    status, detail = classify_section("bonds")
    assert status == "rendered"
    assert detail and "structure" in detail


def test_every_supported_kind_has_a_friendly_sidebar_title():
    """Every renderable kind needs a `_KIND_TITLES` entry, else the sidebar
    shows the raw kind string (e.g. "dos.projected"). Live-review finding:
    dos.projected, the ECD/VCD/generic spectra, and the spin/elf/difference
    volumes were missing, and `spectra.uvvis` was mis-keyed as `spectra.uv`."""
    from vibeview.app import _KIND_TITLES

    missing = sorted(k for k in SUPPORTED_KINDS if k not in _KIND_TITLES)
    assert not missing, (
        f"kinds with no friendly sidebar title (would show the raw kind): {missing}"
    )


def _schema_section_kinds() -> set[str]:
    """Every section kind the vendored qvf schema defines (``Section.oneOf``)."""
    import json
    from importlib.resources import files

    schema = json.loads(files("vibeview").joinpath("schema.json").read_text())
    defs = schema["$defs"]
    kinds = set()
    for ref in defs["Section"]["oneOf"]:
        kind = defs[ref["$ref"].rsplit("/", 1)[-1]].get("properties", {}).get("kind", {})
        if "const" in kind:  # the vendor entry is an x_<vendor> pattern instead
            kinds.add(kind["const"])
    return kinds


def test_every_schema_kind_is_accounted_for():
    """#153: qvf 0.6 kinds reached users as "skipped, unsupported".

    Unlike the writer guard above this needs no producer: the schema is
    vendored, so a re-vendor that adds a kind fails here until the kind is
    rendered or deferred.
    """
    from vibeview.kinds import RENDERED_VIA_STRUCTURE

    kinds = _schema_section_kinds()
    assert len(kinds) > 40, "Section.oneOf no longer lists the kinds"
    missing = kinds - SUPPORTED_KINDS - RENDERED_VIA_STRUCTURE - DEFERRED_KINDS
    assert not missing, (
        f"the schema defines {sorted(missing)} but vibeview.kinds neither renders "
        "nor defers them, so the banner would call them 'unsupported'"
    )
    assert kinds >= DEFERRED_KINDS, "a deferred kind should be one the schema defines"


@pytest.mark.parametrize(
    "kind", ["forces", "hessian", "properties.electric", "volume.vector", "wavefunction.bloch"]
)
def test_qvf_06_kinds_are_rendered(kind: str):
    """#153 deferred the qvf 0.6 kinds; #154–#162 rendered every one of them."""
    assert classify_section(kind) == ("rendered", None)
    assert not DEFERRED_KINDS
