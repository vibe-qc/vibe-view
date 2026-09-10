# The QVF format, from the consumer's side

QVF is the archive format vibe-view reads. This page describes what a
*consumer* needs to know. The **normative specification** — the schemas, the
registry of kinds, the conformance corpus — lives in the
[qvf repository](https://github.com/vibe-qc/qvf), and it, not this
page, is the contract.

## The shape of a file

A `.qvf` file is a zip archive containing:

* **`manifest.json`** — the index. It declares a `qvf_version`, a `source`
  block naming the producing program, its version and the calculation, and a
  list of **sections**.
* **the members** — one or more files per section, each either JSON or a raw
  binary blob (`.dat`) with a declared dtype and shape.

Each section carries an `id` (unique within the file), a `kind` (what it is —
see [What vibe-view can show you](capabilities.md)), and its members. Every
member declares a **`sha256`**.

That digest is not decorative. vibe-view verifies it **before use**, on every
member, every time. A section whose payload does not match its declared digest
is reported as `error, sha256 mismatch` and is not rendered — it is never
drawn from bytes that failed their own integrity check. `vibe-view validate`
runs that check across the whole archive and nothing else.

## Why an archive and not a directory of files

Three properties that matter in practice:

* **Everything about one calculation travels together.** The structure, the
  orbitals, the bands, the spectra and the provenance are one file you can
  scp, attach or archive.
* **Sections are independent and lazily read.** Volumetric payloads are
  extracted from the zip on first activation, not at open time, so a file with
  twenty orbitals in it opens as fast as one with none.
* **Unknown content degrades honestly.** A section whose kind vibe-view does
  not know is reported as skipped, by name, in the open banner. It is never
  silently dropped, and it never stops the rest of the file rendering.

## Vendor extensions

A section whose kind starts with `x_<vendor>.` is a vendor extension: content
one producer wanted to carry that is not part of the portable format.
vibe-view reports these as `skipped, vendor namespace (vendor)` — naming the
vendor, so you can see whose extension it is — and renders everything else.

This is by design. A producer can round-trip its own richer data through QVF
without either breaking other consumers or pretending the data is portable.

## Which schema a file is checked against

vibe-view **vendors** its own copies of the manifest schemas, at
`src/vibeview/schema.json` (v1) and `src/vibeview/schema_v2.json` (v2), and
validates each manifest against the copy matching its `qvf_version`.

Before the 2026-09 split these were symlinks into the producer's tree inside
the monorepo, so they could not diverge. They are real data files now, shipped
in the wheel, and drift is prevented by an explicit guard instead:

* `scripts/check_qvf_conformance.py` pins both schemas by sha256 and `$id`,
  compares the v1 copy against the normative one in a checkout of the `qvf`
  repository, and then reads every archive in the conformance corpus.
* The `qvf-conformance` CI job runs it on a fresh clone of `qvf` at a pinned
  tag, without pytest.
* `tests/test_qvf_schema_identity.py` imports the same pins, so the check runs
  in the test suite too.

Both lanes go **red** on drift — that is the point. The guard that came out of
the monorepo skipped whenever a sibling checkout was missing, which after the
split would have meant skipping forever.

`qvf_version: 2` is recorded as withdrawn in the qvf registry, retained as a
frozen artifact so its `$id` still resolves. vibe-view keeps validating
against it because files that declare it exist; the pinned bytes are what
governs it until qvf publishes the frozen copy.

## Producing QVF files

vibe-view is a consumer, not a producer, with one exception:
`vibe-view import` converts a loose structure or volume file into a QVF
archive, and `vibe-view demo` writes a bundled one.

[vibe-qc](https://vibe-qc.com/docs/) is the reference producer, but nothing
about the viewer assumes it. Any program that emits a conforming archive can
be read here, which is what the conformance corpus in the qvf repository is
for.
