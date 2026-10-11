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

A volume section's optional `component` (`real`, `imag`, `abs`, `density`) is
a **label only**: it says what the stored array already holds, and vibe-view
never transforms the stored data by it. A producer writes |ψ| for `abs` and ψ²
for `density`, so the browser (open, clip, 2D slice, cross-fade), the terminal
and every capture contour the stored array as it is. An `abs` or `density`
section shows only a positive surface because its values are non-negative; an
`imag` section shows both lobes of the real values it stores. QVF v1 volume
data are real (`float32`/`float64`); should a complex array arrive anyway,
vibe-view reduces it by the label, or to its real part when there is none, so
it can still be drawn. Picking a component is a viewer action only for
orbitals vibe-view evaluates itself from a `wavefunction.gto` section (the
browser's **Orbital component** control).

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

## What vibe-view refuses to open

Three acceptance limits are vibe-view's own, not the specification's. They keep
damaged or hostile input away from recursive code, numeric conversions and
text output, and no producer archive comes near any of them.

* **Nesting depth: 256 levels.** Each JSON object or array counts as one
  level. A `manifest.json` nested deeper than that is refused with
  `QVFOpenError` ("nests deeper than 256 levels"). A JSON member nested that
  deeply is recorded as a section error, and the rest of the file still
  opens. For comparison, the deepest nesting across 3802 real producer
  archives (manifests and members) was 6.
* **Numbers must fit a double.** `NaN`, `Infinity`, a literal such as `1e400`
  that overflows to infinity, and integers outside the IEEE-754 double range
  are not JSON (RFC 8259 § 6), although Python's parser accepts them. A
  manifest that contains one is refused with `ManifestValidationError`, which
  names where the number is.
* **Strings must be Unicode text.** JSON can spell an unpaired UTF-16
  surrogate as an escape (`"\ud800"`), and Python's parser accepts it, but
  the result has no UTF-8 encoding, so nothing can print, write or serve it.
  RFC 8259 § 8.2 leaves its meaning undefined and I-JSON (RFC 7493 § 2.1)
  forbids it. A manifest with one, as a value or a key, is refused with
  `ManifestValidationError`. A JSON member with one is a section error that
  names the member and the location. A paired escape such as
  `"\ud83d\ude00"` is an ordinary character and is accepted. No local
  producer archive (94,776 archives, 475,555 JSON members) contains one.

Four more checks apply the specification where the schema does not reach:

* **Every section has `members`** (spec § 4.1). The schema enforces this for
  the kinds it knows; a section of a kind it does not know is otherwise only
  reported as skipped, so an unknown section without a `members` object is
  refused at open with `ManifestValidationError`. `"members": {}` is still
  accepted.
* **`atomic_number` fits a 64-bit integer.** The schema sets only the
  minimum 0. A structure, vibration, scan or trajectory atom above
  `2**63 - 1` is a section error. Numbers beyond the periodic table below that
  stay readable on purpose (a converter labels them `Z999`); local producer
  archives use 1 through 80.
* **A periodic cell must be usable.** Finite, invertible lattice vectors can
  still have a length, a volume or an interplanar spacing that overflows or
  underflows in double precision: vectors of length 1e200, 1e-300 or the
  largest double, or one component that large. Atoms can also lie so many
  cells apart that the periodic bond search would have to visit more than
  100,000 cell images. Either makes the structure a
  section error, "degenerate lattice". Across 2760 local periodic archives
  the spacings lie between 1.9 and 50 Å and the bond search visits at most
  342 images.
* **The bond search needs bounded work.** A lattice much smaller than the
  structure it holds, such as a supercell written with its unit-cell vectors,
  lays hundreds of image copies over every atom; atoms packed far closer than
  in any solid do the same without a lattice. Either makes the structure a
  section error, "structure too dense or lattice inconsistent for bond
  search", as soon as it is read. The limits are about 64 image atoms and
  2048 candidate pairs per atom, and 32 bonds per atom. Real crystals, slabs,
  liquids and biomolecules stay far below them; the largest-radius metals
  come closest, at about 700 candidate pairs per atom.
* **Band energies can be drawn.** A `bands` section's eigenvalues must be
  finite, `kpath.fermi` must be a finite number when present, and the
  energies relative to it must span well inside double precision. `inf`,
  `nan` or values near `±1.8e308` are a section error rather than a chart
  that fails to draw.
* **Atom properties are one value per atom.** Every array in an
  `atom_properties` section (Mulliken, Löwdin, Hirshfeld and IAO charges,
  spin populations) is one-dimensional, and all of them have the same
  length. Otherwise the section is a section error.
* **Vibrations have the shape of their modes.** `frequencies` is a flat list,
  every atom has three coordinates, and `displacements` has the shape
  `(n_modes, n_atoms, 3)`. Anything else is a section error. All 512 local
  vibrations sections have this shape.
* **Spectra are one intensity per transition.** A `spectra.*` section's
  `frequencies` and `intensities` are flat lists of finite numbers with one
  shared length. Otherwise the section is a section error, instead of a
  plot that fails while broadening.
* **Basis shells are well-formed.** Each `wavefunction.gto` shell has a
  non-negative integer `center` and `l`, and `exponents` and `coefficients`
  that are flat lists of finite numbers with one shared length, at least
  one primitive and positive exponents. Together the shells span no more
  atomic orbitals than `n_ao`, or than the coefficient matrix is wide when
  `n_ao` is absent. Otherwise the section is a section error, instead of an
  orbital evaluation that fails on its first shell.
* **Orbital metadata cannot outnumber the coefficients.** `energies` and
  `occupations` (per spin for an unrestricted set) list at most as many
  orbitals as the matching coefficient matrix has rows; a shorter prefix is
  allowed, as for trajectory energies. Otherwise the section is a section
  error, instead of an orbital animation that indexes past the matrix.

A manifest can also parse and still have a shape that no specific check
expects. That too fails the open as `ManifestValidationError`, or as
`QVFOpenError` when `manifest.json` could not be read at all. The error names
the original error. So every command reports a malformed manifest as a bad
file (`validate` prints `INVALID`, the others exit 1) instead of printing a
traceback.

Whatever still goes wrong in a command after the archive has opened is
reported as one `Error:` line with exit status 1, never as a traceback; see
[Errors and debugging](cli.md#errors-and-debugging).

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
* The Python 3.13 `test` CI job runs it before pytest, reusing the same fresh
  clone of `qvf` at a pinned tag and the installed package.
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
