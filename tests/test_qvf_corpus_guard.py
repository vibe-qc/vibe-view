"""The corpus guard must refuse to skip in a lane that promises the corpus.

Tests that read real producer archives skip when the QVF conformance corpus
is absent. That is right on a developer's machine and wrong in CI, where the
corpus is cloned on purpose: a rename in the clone step of .gitlab-ci.yml
would otherwise retire the coverage silently, in a green pipeline. This is
the guard on the guard.
"""

from __future__ import annotations

import pytest

from tests import _qvf_corpus

_STRICT = "VIBEVIEW_REQUIRE_QVF_CORPUS"


def test_absent_corpus_skips_when_the_lane_does_not_promise_one(monkeypatch):
    monkeypatch.delenv(_STRICT, raising=False)
    monkeypatch.setattr(_qvf_corpus, "qvf_corpus_dir", lambda: None)

    mark = _qvf_corpus.requires_qvf_corpus("structure_slab_2d.qvf")
    assert mark.args[0] is True, "an absent corpus must skip in an ordinary lane"


def test_absent_corpus_is_an_error_when_the_lane_promises_one(monkeypatch):
    monkeypatch.setenv(_STRICT, "1")
    monkeypatch.setattr(_qvf_corpus, "qvf_corpus_dir", lambda: None)

    with pytest.raises(RuntimeError, match="VIBEVIEW_REQUIRE_QVF_CORPUS"):
        _qvf_corpus.requires_qvf_corpus("structure_slab_2d.qvf")


def test_a_corpus_missing_one_archive_is_an_error_in_a_strict_lane(
    monkeypatch, tmp_path
):
    """A corpus directory that exists but lacks the archive is the subtler
    failure: the directory probe succeeds and only the member is gone."""
    monkeypatch.setenv(_STRICT, "1")
    monkeypatch.setattr(_qvf_corpus, "qvf_corpus_dir", lambda: tmp_path)

    with pytest.raises(RuntimeError, match="missing structure_slab_2d.qvf"):
        _qvf_corpus.requires_qvf_corpus("structure_slab_2d.qvf")


def test_a_usable_corpus_does_not_skip(monkeypatch, tmp_path):
    (tmp_path / "structure_slab_2d.qvf").write_bytes(b"stand-in")
    monkeypatch.setenv(_STRICT, "1")
    monkeypatch.setattr(_qvf_corpus, "qvf_corpus_dir", lambda: tmp_path)

    mark = _qvf_corpus.requires_qvf_corpus("structure_slab_2d.qvf")
    assert mark.args[0] is False, "a usable corpus must not skip"


def test_the_real_corpus_probe_returns_a_directory_or_none():
    """The probe itself must never raise, whatever the checkout layout is."""
    found = _qvf_corpus.qvf_corpus_dir()
    assert found is None or found.is_dir()
