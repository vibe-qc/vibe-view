#!/usr/bin/env python3
"""Check vibe-view against the qvf release it vendors from.

The qvf repository is the single master of the QVF format (qvf#11).
vibe-view vendors **byte-identical copies of files from the pinned qvf
release only** -- the release named by ``QVF_TAG`` in ``.gitlab-ci.yml`` --
and never edits or derives one. A field vibe-view needs goes into qvf first
and arrives here by re-vendoring a qvf release.

This script is the cross-repository half of that rule. Given a checkout of
qvf at ``QVF_TAG`` it checks that:

  1. every vendored file is byte-identical to its source in that checkout,
     and matches its pin below,
  2. the viewer's own reader opens every archive in ``conformance/corpus``,
  3. and every archive in ``conformance/legacy``, reporting the warnings its
     ``.expected.json`` lists. The legacy archive is stamped with the
     withdrawn ``qvf_version`` 2, which vibe-view reads and checks as
     version 1 with the warning ``W_VERSION_WITHDRAWN`` (QVF spec § 3.1).
  4. every ``negative`` archive goes through the public reference-validation adapter.
     Unexpected exceptions fail; accepted negatives are reported as validation
     gaps, not passes. ``--strict-negatives`` also fails on those gaps.

Any difference fails. There is no v2 schema to compare: vibe-view carries
none, because § 3.1 checks a version-2 archive against the v1 schema.

Requires the qvf Python package from the same release as the corpus.

Usage:  check_qvf_conformance.py --qvf-root /path/to/qvf/checkout
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import TypedDict

_PKG = pathlib.Path(__file__).resolve().parent.parent / "src" / "vibeview"

# Everything vibe-view vendors from qvf: the packaged file, its source path in
# the qvf release at QVF_TAG, its digest and the $id the reader resolves.
#
# The digests are the offline half of the drift guard: they need no sibling
# checkout, so they hold in every lane, including the ones that cannot clone
# qvf. Re-vendoring is a deliberate act -- copy the file from the new qvf
# release, move its pin here and QVF_TAG in .gitlab-ci.yml, in one commit. A
# pin that fails is not a licence to update the constant: find out which side
# moved first.
#
# schema.json is qvf v0.5.0's spec/qvf_manifest.schema.json, vendored on
# 2026-10-16.
class _SchemaPin(TypedDict):
    version: int
    sha256: str
    id: str
    normative: str


PINNED_SCHEMAS: dict[str, _SchemaPin] = {
    "schema.json": {
        "version": 1,
        "sha256": (
            "06c6a13c4ae261247c447e813eec2f9b994ebc06088623f458e3240b2b604b1f"
        ),
        "id": "https://vibe-qc.org/spec/qvf/1/manifest.schema.json",
        "normative": "spec/qvf_manifest.schema.json",
    },
}


def check_pinned_digests(pkg: pathlib.Path = _PKG) -> list[str]:
    """Verify the vendored bytes against their pins.

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
                f"{path} is a symlink; vendored files must be real files so "
                "the built wheel is self-contained"
            )
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != entry["sha256"]:
            failures.append(
                f"{name} has drifted from its pin: pinned {entry['sha256']}, "
                f"actual {actual}"
            )
    return failures


def check_vendored_against_qvf(
    qvf_root: pathlib.Path, pkg: pathlib.Path = _PKG
) -> list[str]:
    """Compare every vendored file with its source in the qvf checkout.

    Byte for byte; a source missing from the checkout is a failure too.
    Returns a list of human-readable failures.
    """
    failures: list[str] = []
    for name, entry in PINNED_SCHEMAS.items():
        vendored = pkg / name
        source = qvf_root / entry["normative"]
        if not source.is_file():
            failures.append(f"{source} is missing from the qvf checkout")
            continue
        if not vendored.is_file():
            failures.append(f"missing {vendored}")
            continue
        hs = hashlib.sha256(source.read_bytes()).hexdigest()
        hv = hashlib.sha256(vendored.read_bytes()).hexdigest()
        print(f"{name}: qvf      {hs}\n{name}: vendored {hv}")
        if hs != hv:
            failures.append(
                f"{vendored} differs from {entry['normative']} in the qvf "
                "checkout. vibe-view vendors byte-identical copies of the "
                "pinned qvf release only: re-vendor it from the release at "
                "QVF_TAG, or move QVF_TAG and the pin together."
            )
        else:
            print(f"{name}: identical to {entry['normative']}")
    return failures


def _expected_warnings(archive: pathlib.Path) -> list[str]:
    """The warning codes an archive's ``.expected.json`` lists, if any."""
    expected = archive.with_name(archive.stem + ".expected.json")
    if not expected.is_file():
        return []
    data = json.loads(expected.read_text(encoding="utf-8"))
    return [
        item["code"]
        for item in data.get("expect", [])
        if item.get("severity") == "warning"
    ]


def read_archives(qvf_root: pathlib.Path) -> tuple[int, int, list[str]]:
    """Open every corpus and legacy archive with vibe-view's reader.

    Returns the number of archives read as expected, the number tried and a
    list of failures. An empty corpus is a failure: the checkout is not what
    this script expects.
    """
    from vibeview import validate
    from vibeview.qvf import QVFReader  # imported late: needs the package
    from vibeview.viewer_defaults import ViewerState

    conformance = qvf_root / "conformance"
    corpus = sorted((conformance / "corpus").glob("*.qvf"))
    legacy = sorted((conformance / "legacy").glob("*.qvf"))
    if not corpus:
        return 0, 0, [f"{conformance / 'corpus'} holds no archives"]

    failures: list[str] = []
    n_ok = 0
    for archive in corpus + legacy:
        label = f"{archive.parent.name}/{archive.name}"
        try:
            reader = QVFReader(str(archive))
            try:
                report = validate(reader)
                warnings = report["warnings"]
                if not report["valid"]:
                    raise ValueError(report["error"])
                ViewerState.from_manifest(reader.viewer_defaults)
                # Opening the manifest alone misses payload-reader bugs such
                # as optional vibration atoms and localized orbital carriers.
                for section in reader.sections:
                    if section.kind == "vibrations":
                        reader.read_vibrations(section.id)
                    elif section.kind == "wavefunction.gto":
                        reader.read_wavefunction_gto(section.id)
            finally:
                reader.close()
        except Exception as exc:  # noqa: BLE001 - report, do not raise
            failures.append(f"{label}: {type(exc).__name__}: {exc}")
            continue
        missing = [
            code
            for code in _expected_warnings(archive)
            if not any(warning.startswith(f"{code}:") for warning in warnings)
        ]
        if missing:
            failures.append(f"{label}: expected the warning(s) {missing}, got {warnings}")
        else:
            n_ok += 1
    return n_ok, len(corpus) + len(legacy), failures


def read_negatives(qvf_root: pathlib.Path) -> tuple[int, int, list[str], list[str]]:
    """Check acceptance and exact expected finding codes, including fixture limits."""
    from vibeview import validate
    from vibeview.qvf import QVFOpenError

    root = qvf_root / "conformance" / "negative"
    archives = sorted(root.glob("*.qvf"))
    if not archives:
        return 0, 0, [], [f"{root} holds no archives"]
    matched = 0
    accepted: list[str] = []
    failures: list[str] = []
    for archive in archives:
        expected = archive.with_suffix(".expected.json")
        if not expected.is_file():
            failures.append(f"negative/{archive.name}: missing expected findings")
            continue
        spec = json.loads(expected.read_text(encoding="utf-8"))
        findings = spec.get("expect", [])
        must_reject = any(item.get("severity") == "error" for item in findings)
        try:
            try:
                report = validate(archive, limits=spec.get("limits"))
            except QVFOpenError as exc:
                if exc.validation_report is None:
                    raise
                report = exc.validation_report
        except Exception as exc:  # noqa: BLE001 - a crash is a failed audit
            failures.append(f"negative/{archive.name}: {type(exc).__name__}: {exc}")
            continue
        if report["valid"] == must_reject:
            if must_reject:
                accepted.append(f"{archive.name}: accepted a must-fail fixture")
            else:
                failures.append(f"negative/{archive.name}: rejected warning-only fixture")
            continue
        actual = {(f["severity"], f["code"]) for f in report["findings"]}
        missing = [f"{f['severity']} {f['code']}" for f in findings
                   if (f["severity"], f["code"]) not in actual]
        if missing:
            accepted.append(f"{archive.name}: missing expected finding(s) {missing}")
        else:
            matched += 1
    return matched, len(archives), accepted, failures


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qvf-root", required=True, type=pathlib.Path)
    ap.add_argument(
        "--strict-negatives", action="store_true",
        help="fail on wrong acceptance or missing expected findings, as well as exceptions",
    )
    args = ap.parse_args(argv)

    failures = check_vendored_against_qvf(args.qvf_root)
    failures += check_pinned_digests()
    if not failures:
        print("vendored files: identical to the qvf checkout and to their pins")

    n_ok, n_tried, read_failures = read_archives(args.qvf_root)
    print(f"archives: {n_ok}/{n_tried} read as expected")
    failures += read_failures

    matched, tried, accepted, negative_failures = read_negatives(args.qvf_root)
    print(f"negatives: {tried} exercised, {matched} expectations met, "
          f"{len(accepted)} validation gaps, {len(negative_failures)} failures")
    failures += negative_failures
    for name in accepted:
        message = f"negative/{name}"
        if args.strict_negatives:
            failures.append(message)
        else:
            print(f"validation gap: {message}")

    for failure in failures:
        print(f"error: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
