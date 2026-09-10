"""Reader resilience to forward-compat / partial / flagged QVF archives.

Audit findings:
* A3-01 — a section whose kind the schema doesn't know (a reserved /
  forward-compat canonical kind from a newer producer) must NOT cause the
  reader to reject the whole archive; it opens, and that section is
  surfaced as skipped/unsupported.
* A3-02 — a section (or root extension) flagged ``critical: true`` whose
  kind the viewer cannot render MUST block the open.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from vibeview.kinds import classify_section
from vibeview.qvf import (
    _MANIFEST_SCHEMA,
    CriticalSectionUnsupportedError,
    QVFReader,
    _schema_known_kinds,
)

# A *canonical* (non-vendor) kind this viewer's schema has no branch for — i.e.
# one a newer producer might emit that an older viewer predates. A3-01 requires
# the reader to open the archive and skip such a section, not reject the whole
# file. Chosen from the producer's reserved kinds and asserted unknown to the
# schema so it can't silently go stale: the original hard-coded examples here
# (fermi_surface / phonon_bands) broke when producer milestones M15–M19 promoted
# them to fully-schema'd kinds, at which point the reader validated them and the
# empty-``members`` test sections failed validation.
_SCHEMA_KNOWN = _schema_known_kinds(_MANIFEST_SCHEMA)
_UNKNOWN_CANONICAL_KIND = next(
    k
    for k in (
        "topology.qtaim",
        "topology.elf_basins",
        "projections.lcao",
        "volume.orbital_projection",
        "some.future.kind",
    )
    if k not in _SCHEMA_KNOWN and not k.startswith("x_")
)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


_STRUCT = json.dumps(
    {"atoms": [{"symbol": "H", "position": [0, 0, 0], "atomic_number": 1}],
     "pbc": [False, False, False]}
).encode()


def _archive(extra_sections: list[dict], extensions: dict | None = None) -> bytes:
    sections = [
        {"id": "structure", "kind": "structure",
         "members": {"structure": {"path": "s.json", "format": "json", "sha256": _sha(_STRUCT)}}},
        *extra_sections,
    ]
    manifest = {
        "qvf_version": 1,
        "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
        "sections": sections,
    }
    if extensions is not None:
        manifest["extensions"] = extensions
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", _STRUCT)
    return buf.getvalue()


def test_unknown_canonical_kind_opens_and_is_skipped():
    data = _archive([{"id": "fs", "kind": _UNKNOWN_CANONICAL_KIND, "members": {}}])
    reader = QVFReader(data)
    kinds = [s.kind for s in reader.sections]
    assert "structure" in kinds and _UNKNOWN_CANONICAL_KIND in kinds  # opened
    assert "fs" in reader._unknown_kind_ids
    # surfaced as skipped, not rendered
    assert classify_section(_UNKNOWN_CANONICAL_KIND) == ("skipped", "unsupported")


def test_structure_still_readable_despite_unknown_sibling():
    data = _archive([{"id": "ph", "kind": _UNKNOWN_CANONICAL_KIND, "members": {}}])
    reader = QVFReader(data)
    s = reader.read_structure()  # must not raise — the rest of the file works
    assert len(s.atoms) == 1


def test_critical_unsupported_kind_refuses_open():
    data = _archive(
        [{"id": "fs", "kind": _UNKNOWN_CANONICAL_KIND, "members": {}, "critical": True}]
    )
    with pytest.raises(CriticalSectionUnsupportedError):
        QVFReader(data)


def test_critical_root_extension_refuses_open():
    data = _archive([], extensions={"x_acme_thing": {"critical": True, "version": "1"}})
    with pytest.raises(CriticalSectionUnsupportedError):
        QVFReader(data)


def test_critical_vendor_section_refuses_open():
    """M3: a vendor ``x_<vendor>.*`` section flagged ``critical: true`` is NOT
    exempt. The viewer can never render a vendor kind, so spec §5.3/§5.5/§7
    require refusing the open rather than silently skipping it (the old reader
    carved vendor kinds out of the per-section critical check)."""
    data = _archive([{"id": "v", "kind": "x_acme.special", "members": {}, "critical": True}])
    with pytest.raises(CriticalSectionUnsupportedError):
        QVFReader(data)


def test_non_critical_unknown_kind_does_not_refuse():
    data = _archive(
        [{"id": "fs", "kind": _UNKNOWN_CANONICAL_KIND, "members": {}, "critical": False}]
    )
    reader = QVFReader(data)  # must open
    assert reader.has_section("fs")


def test_critical_on_supported_kind_opens():
    """A critical flag on a kind the viewer CAN render must not block open."""
    # Mark the (always-present, always-usable) structure section critical.
    sections = [
        {"id": "structure", "kind": "structure", "critical": True,
         "members": {"structure": {"path": "s.json", "format": "json", "sha256": _sha(_STRUCT)}}},
    ]
    manifest = {"qvf_version": 1,
                "source": {"program": "vibe-qc", "version": "0", "calculation": "t"},
                "sections": sections}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", _STRUCT)
    reader = QVFReader(buf.getvalue())
    assert reader.has_section("structure")
