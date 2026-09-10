#!/usr/bin/env python3
"""Check vibe-view against the QVF specification and its conformance corpus.

vibe-view carries its own copy of the manifest schema. The normative copy
lives in the qvf repository. In the monorepo the viewer's copy was a symlink
into vibe-qc's tree, so the two could not diverge; the guards that compared
them skipped silently whenever a sibling checkout was missing, which after
the repository split would mean they never fail again.

This replaces that guard, from the consumer side:

  1. the vendored v1 schema is byte-identical to the normative one,
  2. the vendored v2 schema is well-formed and declares the $id the reader
     resolves (and is byte-identical to the normative copy when the qvf
     checkout ships one), and
  3. the viewer's own reader opens every archive in the corpus.

qvf_version 2 was withdrawn (see qvf's registry.json): producers must not
emit it, consumers still accept archives already in the wild, and the schema
is retained frozen only to resolve its $id. qvf v0.1.0 does not publish that
frozen artifact, so its bytes are pinned by
``tests/test_qvf_schema_identity.py`` instead -- which runs in every lane and
cannot skip. Once qvf publishes it, this script compares it here too, with no
further change.

Build-time only. qvf is a reference, not a runtime dependency.

Usage:  check_qvf_conformance.py --qvf-root /path/to/qvf/checkout
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

_PKG = pathlib.Path(__file__).resolve().parent.parent / "src" / "vibeview"

# The vendored schemas, pinned by digest and by the $id the reader resolves.
#
# The digests are the offline half of the drift guard: they need no sibling
# checkout, so they hold in every lane, including the ones that cannot clone
# qvf. Re-vendoring a schema must move the pin here in the same commit --
# a pin that fails is not a licence to update the constant, so find out which
# side moved first.
#
# v1 was verified identical to qvf v0.1.0 spec/qvf_manifest.schema.json and to
# vibe-qc's python/vibeqc/output/formats/qvf_manifest.schema.json on
# 2026-09-08. v2 is the withdrawn, frozen artifact.
PINNED_SCHEMAS = {
    "schema.json": {
        "version": 1,
        "sha256": (
            "709b181453c62635d6661717c0dbd8d20a10aeef744db89513fa9d9af56b7ea9"
        ),
        "id": "https://vibe-qc.org/spec/qvf/1/manifest.schema.json",
        "normative": "spec/qvf_manifest.schema.json",
    },
    "schema_v2.json": {
        "version": 2,
        "sha256": (
            "bf1f361a0dfa27fda1a4003c37c1f5393620cf844bc7ddeec133bb70116aafce"
        ),
        "id": "https://vibe-qc.org/spec/qvf/2/manifest.schema.json",
        # qvf v0.1.0 does not publish the frozen v2 artifact. When it does,
        # this path starts being compared with no further change here.
        "normative": "spec/qvf_manifest_v2.schema.json",
    },
}

_EXPECTED_IDS = {
    entry["version"]: entry["id"] for entry in PINNED_SCHEMAS.values()
}


def check_pinned_digests(pkg: pathlib.Path = _PKG) -> list[str]:
    """Verify the vendored schema bytes against their pins.

    Offline and unconditional: this is what keeps the guard alive in a lane
    that has no qvf checkout. Returns a list of human-readable failures.
    """
    failures: list[str] = []
    for name, entry in PINNED_SCHEMAS.items():
        path = pkg / name
        if not path.is_file():
            failures.append(f"missing {path}")
            continue
        if path.is_symlink():
            failures.append(
                f"{path} is a symlink; the viewer's schemas must be real "
                "files so the built wheel is self-contained"
            )
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != entry["sha256"]:
            failures.append(
                f"{name} has drifted from its pin: pinned {entry['sha256']}, "
                f"actual {actual}"
            )
    return failures


def _check_v2(vendored_v2: pathlib.Path, qvf_root: pathlib.Path) -> list[str]:
    """Verify the frozen v2 schema. Returns a list of failures."""
    failures: list[str] = []
    if not vendored_v2.is_file():
        return [f"missing {vendored_v2}"]
    try:
        parsed = json.loads(vendored_v2.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{vendored_v2} is not valid JSON: {exc}"]
    if parsed.get("$id") != _EXPECTED_IDS[2]:
        failures.append(
            f"{vendored_v2} declares $id {parsed.get('$id')!r}, "
            f"expected {_EXPECTED_IDS[2]!r}"
        )

    normative_v2 = qvf_root / "spec" / "qvf_manifest_v2.schema.json"
    if not normative_v2.is_file():
        print(
            "schema v2: no normative copy in this qvf checkout (v2 is "
            "withdrawn); frozen bytes pinned by "
            "tests/test_qvf_schema_identity.py"
        )
        return failures

    hv = hashlib.sha256(vendored_v2.read_bytes()).hexdigest()
    hn = hashlib.sha256(normative_v2.read_bytes()).hexdigest()
    print(f"normative v2 {hn}\nvendored  v2 {hv}")
    if hv != hn:
        failures.append(
            f"{vendored_v2} has diverged from {normative_v2}. v2 is a frozen "
            "artifact -- neither side should have moved."
        )
    else:
        print("schema v2: identical")
    return failures


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qvf-root", required=True, type=pathlib.Path)
    ap.add_argument(
        "--vendored",
        type=pathlib.Path,
        default=_PKG / "schema.json",
    )
    ap.add_argument(
        "--vendored-v2",
        type=pathlib.Path,
        default=_PKG / "schema_v2.json",
    )
    args = ap.parse_args(argv)

    normative = args.qvf_root / "spec" / "qvf_manifest.schema.json"
    for p in (normative, args.vendored):
        if not p.is_file():
            print(f"error: missing {p}", file=sys.stderr)
            return 2

    ha = hashlib.sha256(normative.read_bytes()).hexdigest()
    hb = hashlib.sha256(args.vendored.read_bytes()).hexdigest()
    print(f"normative {ha}\nvendored  {hb}")
    if ha != hb:
        print(
            f"error: {args.vendored} has diverged from the QVF specification.\n"
            "       Re-vendor it, or bump the pinned qvf tag deliberately if\n"
            "       the format changed.",
            file=sys.stderr,
        )
        return 1
    print("schema: identical")

    v2_failures = _check_v2(args.vendored_v2, args.qvf_root)
    v2_failures += check_pinned_digests()
    for f in v2_failures:
        print(f"error: {f}", file=sys.stderr)
    if not v2_failures:
        print("schema: vendored bytes match their pins")

    from vibeview.qvf import QVFReader  # imported late: needs the package

    corpus = sorted((args.qvf_root / "conformance" / "corpus").glob("*.qvf"))
    if not corpus:
        print("error: conformance corpus is empty", file=sys.stderr)
        return 2

    failures: list[str] = []
    for archive in corpus:
        try:
            reader = QVFReader(str(archive))
            try:
                reader.manifest  # noqa: B018 - force the manifest to parse
            finally:
                reader.close()
        except Exception as exc:  # noqa: BLE001 - report, do not raise
            failures.append(f"{archive.name}: {type(exc).__name__}: {exc}")

    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    print(f"corpus: {len(corpus) - len(failures)}/{len(corpus)} archives read")
    return 1 if (failures or v2_failures) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
