"""Schema identity guard — viewer side.

vibe-view vendors the two QVF manifest schemas it validates against:

* ``src/vibeview/schema.json`` — the v1 contract. The normative copy lives in
  the ``qvf`` repository (``spec/qvf_manifest.schema.json``) and vibe-qc
  vendors a third copy. There is no dependency edge between the three.
* ``src/vibeview/schema_v2.json`` — the withdrawn v2 contract, retained as a
  *frozen* artifact. ``qvf``'s ``registry.json`` records the decision:
  producers MUST NOT emit ``qvf_version: 2``, consumers SHOULD still accept
  archives already in the wild, and the schema is kept only to resolve its
  ``$id``. A frozen artifact has exactly one correct digest, forever.

Both files were **symlinks** into ``python/vibeqc/output/formats/`` while
vibe-view lived in the monorepo, so this test used to compare a symlink
against its own target and pass trivially. After the 2026-09 split they are
real files, and the old guard skipped on a path that can no longer exist —
which is the same as deleting it.

The guard now has two halves that do not overlap:

* **This test** pins the vendored bytes. It needs no sibling checkout, no
  network and no environment, so it runs in every lane and can never skip.
  The pins themselves live in ``scripts/check_qvf_conformance.py`` so the
  CI job that has no pytest checks them too; re-vendoring a schema is a
  deliberate act and must move a pin there in the same commit.
* **``scripts/check_qvf_conformance.py``**, run by the ``qvf-conformance`` CI
  job, compares the vendored v1 against the ``qvf`` checkout at ``QVF_TAG``.
  That is the cross-repo authority, and it fails the pipeline on drift.

A pin that fails here is not a licence to update the constant. Find out which
side moved first.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_VIEWER_DIR = Path(__file__).resolve().parent.parent
_VIEWER_PKG = _VIEWER_DIR / "src" / "vibeview"
_CONFORMANCE = _VIEWER_DIR / "scripts" / "check_qvf_conformance.py"


def _conformance_module():
    """Load the conformance script, which owns the pins.

    The CI ``qvf-conformance`` job runs that script without pytest, so the
    pins live there and this test consumes them. One constant, two lanes.
    """
    spec = importlib.util.spec_from_file_location(
        "vibeview_qvf_conformance", _CONFORMANCE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PINNED = _conformance_module().PINNED_SCHEMAS


def test_vendored_schemas_are_real_files_not_symlinks() -> None:
    """The wheel must carry data files, not links into a sibling checkout.

    A symlink survives an editable install and a source checkout, and only
    breaks once someone installs the wheel somewhere else — which is exactly
    the failure the split was supposed to end.
    """
    for name in _PINNED:
        path = _VIEWER_PKG / name
        assert path.is_file(), f"{path} is missing"
        assert not path.is_symlink(), (
            f"{path} is a symlink. The viewer's schemas must be real files so "
            "the built wheel is self-contained; re-vendor the bytes instead."
        )


def test_vendored_schemas_match_their_pinned_digests() -> None:
    """Byte-level drift guard, independent of any sibling repository."""
    drifted = _conformance_module().check_pinned_digests(_VIEWER_PKG)
    assert not drifted, (
        "vendored QVF schema bytes have drifted from their pins:\n  "
        + "\n  ".join(drifted)
        + "\n\nIf you re-vendored a schema on purpose, move the pin in "
        "scripts/check_qvf_conformance.py in the same commit, and bump "
        "QVF_TAG in .gitlab-ci.yml if the normative copy in the qvf "
        "repository moved too."
    )


def test_vendored_schemas_declare_the_expected_ids() -> None:
    """A digest pin alone cannot tell you *which* schema you pinned."""
    for name, entry in _PINNED.items():
        schema = json.loads((_VIEWER_PKG / name).read_text(encoding="utf-8"))
        assert schema["$id"] == entry["id"], (
            f"{name} declares $id {schema['$id']!r}, expected {entry['id']!r}"
        )


def test_the_reader_loads_exactly_the_vendored_schemas() -> None:
    """The pins are worthless if the reader validates against something else."""
    from vibeview import qvf as qvf_mod

    assert qvf_mod._SCHEMA_PATH == _VIEWER_PKG / "schema.json"
    assert qvf_mod._SCHEMA_PATH_V2 == _VIEWER_PKG / "schema_v2.json"
    assert set(qvf_mod._SCHEMAS_BY_VERSION) == {1, 2}
    for name, entry in _PINNED.items():
        loaded = qvf_mod._SCHEMAS_BY_VERSION[entry["version"]]
        assert loaded["$id"] == entry["id"], (
            f"the reader validates qvf_version {entry['version']} against "
            f"{loaded['$id']!r}, but {name} is pinned to {entry['id']!r}"
        )
