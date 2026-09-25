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

That digest is not decorative. vibe-view verifies each member **before use**.
A section whose payload does not match its declared digest is reported as
`error, sha256 mismatch`. Missing or corrupt ZIP members are reported as
`error, member read error`; invalid JSON, text encoding or binary shape as
`error, invalid payload`. These failures stop the member read and are recorded for the section,
including when its data is first requested lazily.
Phonon DOS metadata is required, and a present bond-order section must be
read successfully before its values can enrich structure bonds; neither
failure is silently replaced by defaults. `vibe-view validate` checks member
integrity across the whole archive; it does not validate every payload's
scientific contents.

Structure reads also validate the atom and periodicity payload against the
vendored `StructurePayload` schema, retaining nonempty display labels longer
than three characters for compatibility (such as `Z999`). Coordinates and lattice vectors must be
finite, and periodic cells must be invertible. Pre-conformance archives with
only `dimensionality` retain the documented leading-axis fallback; explicit
`pbc` flags must be three booleans and agree with `dimensionality` when present.

Explicit bonds and bond-order analyses reject malformed pair indices and
present orders instead of coercing them or drawing a different atom. Indices
must be nonnegative integers and lie within the original file structure when
that context exists. Errors identify the section and pair and are recorded as
`invalid payload`. Omitted explicit bond orders still default to 1.0;
standalone analysis tables remain readable without an atom-count upper bound.
Edit overlays do not change the bounds of archived analyses. This validation
does not remap those analyses after atom edits or add explicit bond-image
extensions.

Volume grids require a finite three-coordinate origin, three finite voxel step
vectors, and three nonnegative integer dimensions matching the binary field
exactly. This also applies to reaction-path volume grids and direct volume-data
reads. Negative axes, skew grids, empty dimensions and singleton dimensions
remain supported; validation does not change the grid's bohr units or reject
nonfinite scalar samples.

Trajectory and reaction-path coordinates must be finite arrays shaped
`[n_frames, n_atoms, 3]`, with one metadata identity per atom. Zero-frame
archives remain readable. Optional energies may be absent, null, empty or a
shorter prefix, but supplied values must be finite numbers. Waypoint indices
must identify an existing frame: booleans, fractional indices and numeric
strings are rejected instead of moving the label to a different frame.
Integral JSON numbers such as `1.0` remain valid indices. These failures name
the owning section and member and are recorded as `invalid payload`.

Reaction lattice/dimension synchronization, reaction-coordinate lengths and
decimated volume-frame mappings retain their existing behavior; these checks
do not normalize reaction lattice units or remap frame metadata.

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
