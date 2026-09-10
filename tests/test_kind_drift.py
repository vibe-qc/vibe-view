"""Writer ↔ viewer kind-registry drift guard (audit finding A6-03).

The `bonds` gap (writer emits it, viewer's SUPPORTED_KINDS omitted it, banner
mislabeled it "skipped, unsupported") survived because no test tied the two
registries together. This test pins them: every kind the writer can emit must
be either renderable by the viewer or explicitly accounted for.
"""

from __future__ import annotations

import pytest

from vibeview.kinds import DEFERRED_KINDS, SUPPORTED_KINDS, classify_section

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


def test_no_viewer_kind_is_unwritable():
    qvf = pytest.importorskip("vibeqc.output.formats.qvf")
    impl = set(qvf._IMPLEMENTED_KINDS) | set(getattr(qvf, "_RESERVED_KINDS", set()))
    orphan = set(SUPPORTED_KINDS) - impl
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
