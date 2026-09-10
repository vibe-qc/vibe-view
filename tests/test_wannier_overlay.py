"""x_ccm.wannier_centers overlay: parse + glyph building."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import numpy as np

from vibeview.qvf import QVFReader
from vibeview.renderers.wannier import (
    build_wannier_glyphs,
    find_wannier_section,
    read_wannier_centres,
)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _qvf_with_centres(centres_payload) -> io.BytesIO:
    """Minimal QVF: a structure plus an x_ccm.wannier_centers vendor section."""
    struct = json.dumps(
        {"atoms": [{"symbol": "H", "position": [0, 0, 0], "atomic_number": 1}],
         "pbc": [False, False, False]}
    ).encode()
    centres = json.dumps(centres_payload).encode()
    manifest = {
        "qvf_version": 1,
        "source": {"program": "test", "version": "0", "calculation": "ccm"},
        "sections": [
            {"id": "structure", "kind": "structure",
             "members": {"structure": {"path": "s.json", "format": "json", "sha256": _sha(struct)}}},
            {"id": "wan0", "kind": "x_ccm.wannier_centers",
             "members": {"centers": {"path": "w.json", "format": "json", "sha256": _sha(centres)}}},
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("s.json", struct)
        zf.writestr("w.json", centres)
    buf.seek(0)
    return buf


_CENTRES = [
    {"center": [1.0, 0.0, 0.0], "spread": 1.0, "label": "H1-H2", "orbital_ref": "co_06"},
    {"center": [4.0, 0.0, 0.0], "spread": 4.0},
    {"bad": "no center here"},  # malformed, must be skipped
]


def test_read_wannier_centres_parses_and_skips_malformed():
    r = QVFReader(_qvf_with_centres(_CENTRES))
    try:
        assert find_wannier_section(r) is not None
        cs = read_wannier_centres(r)
        assert len(cs) == 2  # the malformed row is skipped
        assert np.allclose(cs[0].center, [1, 0, 0])
        assert cs[0].label == "H1-H2" and cs[0].orbital_ref == "co_06"
        assert cs[1].spread == 4.0 and cs[1].label is None
    finally:
        r.close()


def test_read_wannier_accepts_wrapped_object_form():
    r = QVFReader(_qvf_with_centres({"centers": _CENTRES}))
    try:
        assert len(read_wannier_centres(r)) == 2
    finally:
        r.close()


def test_no_section_returns_empty(sample_qvf):
    r = QVFReader(sample_qvf)
    try:
        if find_wannier_section(r) is None:
            assert read_wannier_centres(r) == []
    finally:
        r.close()


def test_build_glyphs_scales_by_spread():
    from vibeview.renderers.wannier import WannierCentre

    cs = [
        WannierCentre(center=np.array([0.0, 0, 0]), spread=1.0),
        WannierCentre(center=np.array([5.0, 0, 0]), spread=9.0),  # 3x the radius
    ]
    mesh = build_wannier_glyphs(cs, base_radius=0.3)
    assert mesh is not None and mesh.n_points > 0
    # Two spheres: the high-spread one occupies a wider x-span than the other.
    xs = mesh.points[:, 0]
    lo = xs[xs < 2.5]
    hi = xs[xs > 2.5]
    span_lo = lo.max() - lo.min()
    span_hi = hi.max() - hi.min()
    assert span_hi > 2.0 * span_lo  # sqrt(9)/sqrt(1) = 3x radius


def test_build_glyphs_empty_returns_none():
    assert build_wannier_glyphs([]) is None


def test_orbital_is_wrappable_gate():
    """Canonical / delocalized crystalline orbitals are never recentred;
    localized (Wannier) and unmarked sections are."""
    import types

    from vibeview.app import _orbital_is_wrappable

    def _sec(kind):
        return types.SimpleNamespace(model_extra=({"orbital_kind": kind} if kind else {}))

    assert _orbital_is_wrappable(_sec("localized")) is True
    assert _orbital_is_wrappable(_sec("wannier")) is True
    assert _orbital_is_wrappable(_sec(None)) is True  # unmarked -> R-heuristic
    assert _orbital_is_wrappable(_sec("canonical")) is False
    assert _orbital_is_wrappable(_sec("delocalized")) is False
