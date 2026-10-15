"""Schema identity guard — viewer side.

The qvf repository is the single master of the QVF format (qvf#11), and
vibe-view vendors **byte-identical copies of the pinned qvf release only**:
the release named by ``QVF_TAG`` in ``.gitlab-ci.yml``. A field vibe-view
needs goes into qvf first and arrives by re-vendoring a qvf release; a
vendored file is never edited or derived here.

What vibe-view vendors today is one file:

* ``src/vibeview/schema.json`` — qvf v0.3.0's
  ``spec/qvf_manifest.schema.json``, the v1 manifest schema.

There is no v2 schema. ``qvf_version`` 2 was withdrawn; an archive stamped
with it is read and checked as version 1 against this same schema, with the
warning ``W_VERSION_WITHDRAWN`` (QVF spec § 3.1).

The schema was a **symlink** into ``python/vibeqc/output/formats/`` while
vibe-view lived in the monorepo, so this test used to compare a symlink
against its own target and pass trivially. After the 2026-09 split it is a
real file, and the old guard skipped on a path that can no longer exist —
which is the same as deleting it.

The guard now has two halves that do not overlap:

* **This test** pins the vendored bytes. It needs no sibling checkout, no
  network and no environment, so it runs in every lane and can never skip.
  The pins themselves live in ``scripts/check_qvf_conformance.py`` so the
  CI step that runs before pytest checks them too; re-vendoring is a
  deliberate act and must move a pin there in the same commit.
* **``scripts/check_qvf_conformance.py``**, run by the candidate ``test``
  job, compares every vendored file byte for byte with its source in the
  ``qvf`` checkout at ``QVF_TAG`` and fails on any difference. That is the
  cross-repo authority.

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

    The candidate ``test`` job runs that script before pytest, so the pins
    live there and this test consumes them. One constant, two lanes.
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
        "QVF_TAG in .gitlab-ci.yml to the qvf release you copied it from."
    )


def test_vendored_schemas_declare_the_expected_ids() -> None:
    """A digest pin alone cannot tell you *which* schema you pinned."""
    for name, entry in _PINNED.items():
        schema = json.loads((_VIEWER_PKG / name).read_text(encoding="utf-8"))
        assert schema["$id"] == entry["id"], (
            f"{name} declares $id {schema['$id']!r}, expected {entry['id']!r}"
        )


def test_the_package_vendors_nothing_unpinned() -> None:
    """Every JSON data file in the package is a pinned qvf copy (#88).

    The withdrawn v2 schema was a derived file, qvf v0.1.0's v1 with its
    version changed, which no qvf release published; it is gone, and a new
    unpinned copy would bring the same drift back.
    """
    shipped = {path.name for path in _VIEWER_PKG.glob("*.json")}
    assert shipped == set(_PINNED)


def test_the_reader_loads_exactly_the_vendored_schemas() -> None:
    """The pins are worthless if the reader validates against something else."""
    from vibeview import qvf as qvf_mod

    assert qvf_mod._SCHEMA_PATH == _VIEWER_PKG / "schema.json"
    # Version 2 is read as 1 (spec § 3.1), so 1 is the only schema.
    assert set(qvf_mod._SCHEMAS_BY_VERSION) == {1}
    for name, entry in _PINNED.items():
        loaded = qvf_mod._SCHEMAS_BY_VERSION[entry["version"]]
        assert loaded["$id"] == entry["id"], (
            f"the reader validates qvf_version {entry['version']} against "
            f"{loaded['$id']!r}, but {name} is pinned to {entry['id']!r}"
        )


def test_the_qvf_comparison_fails_on_any_difference(tmp_path) -> None:
    """One changed byte, or a missing source, fails the cross-repo check."""
    module = _conformance_module()
    for name, entry in _PINNED.items():
        source = tmp_path / entry["normative"]
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes((_VIEWER_PKG / name).read_bytes())
    assert module.check_vendored_against_qvf(tmp_path, _VIEWER_PKG) == []

    for entry in _PINNED.values():
        source = tmp_path / entry["normative"]
        source.write_bytes(source.read_bytes() + b"\n")
        assert module.check_vendored_against_qvf(tmp_path, _VIEWER_PKG)
        source.unlink()
        assert module.check_vendored_against_qvf(tmp_path, _VIEWER_PKG)
