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

## Optional geometry, orbital kinds and camera hints

A `vibrations` payload may omit `atoms`. In that case vibe-view takes the
reference geometry from the section's `structure_ref`, or the archive's first
structure when no reference is supplied. It uses archived coordinates even
when another structure is displayed or the geometry has been edited. Optional
`symbols` must agree with that reference, and the mode array must match its
atom count. This also supplies the equilibrium geometry for animation and
[normal-mode displacement](editing.md#displace-along-a-normal-mode).

The orbital kinds `ibo`, `iao`, `boys`, `pipek_mezey` and `nbo` use the same
localized-orbital display as `localized`: atom-composition labels where
available, no canonical HOMO/LUMO labels and no eigenvalue diagram. The
reader preserves the stored kind; it does not reinterpret NTO transition
weights as localized electron occupations.

`viewer_defaults.camera` is advisory. The viewer checks it against the
unchanged `$defs/Camera` definition from the pinned qvf schema and ignores
malformed or non-finite values. A usable camera has `position`, `focal_point`
and `view_up` three-vectors, plus exactly one of `view_angle` (strictly between
0 and 180 degrees) or positive `parallel_scale`. A valid first bookmark can
supply the initial camera when the hint is ignored. Other section hints are
preserved.

## Several structures, and which one is shown

Section ids carry no meaning (spec § 4.1). `structure` is only the id the
reference writers use by default; an archive may hold any number of
`structure` sections under any ids, as a vibe-sketch document does with one
section per species (`structure_m1`, `structure_m2`, ...). vibe-view finds
sections by their `kind` and by the references of the format, never by an id:

* **The first structure** is the first `structure` section that
  `viewer_defaults.auto_open` lists, else the first section of kind
  `structure` in `sections` order. It is what the viewers open on and what
  every command that needs one structure without being told which takes:
  `export`, `capture`, `animate`, `h-add`, `supercell`, `dashboard`, the
  Python API and the notebook helpers. `export -s ID`, `capture -s ID` and
  `get_structure(source, section_id)` take another.
* **The shown structure** is the first structure until another is selected:
  in the browser and the TUI, selecting a structure section shows it, and
  in Python `QVFReader.show_structure(id)` makes `read_structure()` return
  it. In the browser, selecting a section that names a structure in
  `structure_ref`, such as a density computed for one species, shows that
  structure first and draws the section over it.
* **References.** A section that names a structure in `structure_ref`
  describes that one; the section's reference wins over one inside its
  payload, such as the `basis` of a `wavefunction.gto`. A section without a
  reference makes no statement; vibe-view relates it to the first structure
  for display (a standalone `bonds` section, `bond_orders`), draws it over
  whichever structure is on show, and checks the atom indices of an
  analysis against a structure only when the reference or the archive's
  only structure says which one it is. `QVFReader.named_structure_ref(id)`
  returns only what a section names; `structure_ref_of(id)` falls back to
  the first structure.
* **Edits** made in the browser belong to the structure they were made on
  and stay with it while another structure is shown.
* **Saved views.** A bookmark, a presentation slide and a session record
  the structure on show (`shown_structure`) and restore it before the saved
  section, so a view saved on the second species comes back on it. A
  session file from before this field restores as it did.
* `slice` and `merge` keep references by kind: `merge` renames section ids
  that collide and every reference to them (`structure_ref`, `run_ref`,
  `trajectory_ref`, the structure of a wavefunction's basis, the
  participants of a reaction scheme), and `slice` refuses to drop a
  structure that something still names.

**Chemical identity** (spec § 5.1). A `structure` section object may say
which species it holds: `charge`, `multiplicity`, `name`, `smiles`, `inchi`
and `inchikey`, with its generic `label` as the compound label (`"1"`,
`"3a"`). vibe-view shows the label and name in the sidebar, the terminal and
`vibe-view info` (`--json` carries every field), and seeds the Calculation
Parameters panel from the shown structure's charge and multiplicity. Where
the structure states neither, an archive with exactly one structure falls
back to `provenance`; with several, the provenance describes none of them in
particular and is not used for that.

**Descriptive kinds.** `reaction.scheme` (spec § 5.14) relates the
archive's structures as the reactants, reagents and products of reaction
steps; vibe-view lists its steps as equations in the details panel and
checks that every participant names a structure section. `preview`
(§ 5.15) is a picture of the archive for file managers; vibe-view shows its
raster thumbnail, as the manifest's `media_type` declares it, in the details
panel, never draws it in the 3D view and reads no chemistry from it. An SVG
`image` member is listed, not rendered.

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

The qvf repository is the single master of the format (qvf#11). vibe-view
**vendors byte-identical copies of files from the pinned qvf release only**,
the release that `QVF_TAG` in `.gitlab-ci.yml` names, and never edits or
derives one. A field vibe-view needs goes into qvf first and reaches the
viewer when a qvf release that has it is vendored.

What vibe-view vendors is one file, the manifest schema at
`src/vibeview/schema.json`. It is qvf v0.4.0's
`spec/qvf_manifest.schema.json`, and every manifest is validated against it.
It accepts the root `byte_order` and `metadata` blocks and the `size` of each
member, which qvf-writer 0.2 and later always write. Archives from
qvf-writer, and so everything [vibe-sketch](editing.md) saves or sends to the
viewer, therefore open. Since qvf v0.2.0 the schema is also stricter in
places: each `dos.projected` channel must be an object such as
`{"label": "Fe d"}`, so an archive that lists channels as plain strings no
longer opens. qvf v0.3.0 adds `localization_reference_basis` to the orbital
metadata, lets a `job.spec` say `null` for its `functional` or `basis`, and
keeps the keys it deprecates valid (below). qvf v0.4.0 clarifies the paired
hole/particle and alpha/beta metadata for unrestricted natural transition
orbitals while retaining legacy unrestricted metadata and format version 1.

The `excited_states` table has no dedicated viewer panel yet and is reported
as "skipped, not yet rendered". Its referenced excitation energies are still
read for natural transition orbitals, as described below.

### What vibe-view reads where the format offers two carriers

* **Atoms of a trajectory or reaction path** (spec § 5.5) are named by
  `atoms`, or by `symbols` and/or `atomic_numbers`; when both lists are
  present they must name the same atoms.
* **A standalone `bonds` section** joins the structure its `structure_ref`
  names, else the archive's first structure (spec § 5.1), so a second
  structure gets its own bonds.
* **Natural transition orbitals** (spec § 5.3) are `orbital_kind: "nto"`
  sections with `nto_role`, `state_index` and `state_ref`, whose excitation
  energy is that of the state in the referenced `excited_states` section.
  Archives that vibe-qc wrote before carry `orbital_kind: "natural"` with the
  deprecated `excitation_index` and `excitation_energy_ev`. vibe-view reads
  the canonical keys first and falls back to the deprecated ones, so both
  show as transition weights of the same state. The terminal viewer lists it
  under "transition orbitals of" (such as `state 1, 5.913 eV, hole`), and
  the browser names it when the section is loaded.
* **IAO charges** are read from the `iao_charge` member of an
  `atom_properties` section. The deprecated `iao_charges` list in the orbital
  metadata is not read: in all 26,685 local vibe-qc orbital sets that carry
  it, the archive also has the `atom_properties` member.

### Archives stamped `qvf_version: 2`

vibe-qc 0.10 to 0.15 stamped `qvf_version: 2` on archives holding a periodic
`reaction.path`. The bump was withdrawn: such an archive is version 1 in
every respect, and a path is periodic because it has a `lattice` member, not
because of the version (spec § 3.1). vibe-view reads it as version 1 and
checks it against the same v1 schema, as qvf's own validators do, and opens
it with the warning `W_VERSION_WITHDRAWN`. The warning leads the **Run Info**
card in the browser and is listed under **Manifest warnings** in the
terminal viewer's overview, beside lifecycle warnings; in Python it is in
`QVFReader.manifest_warnings()`. The archive is valid. The warning records
that its producer wrote a version producers must not write, and `merge` and
`slice` write `qvf_version: 1`. Every version other than 1 and 2 is refused.

### Strict validation

`vibe-view validate calculation.qvf` and `vibeview.validate(...)` use qvf's
reference validator for container safety, manifest and payload schemas,
checksums, binary layouts, references and semantic consistency. The viewer
contains an adapter, not a fork of the format rules. Normal opening remains
lazy and does not require the optional validator.

Install qvf's Python package from the release named by `QVF_TAG` in
`.gitlab-ci.yml`, then enable the viewer's `validation` extra. The current
pin is v0.4.0. `qvf-writer` is not yet on PyPI, so the extra alone cannot
fetch it; use a local checkout of that release:

```sh
python -m pip install '/path/to/qvf/python[validate]'
# In your vibe-view source checkout:
python -m pip install -e '.[validation]'
vibe-view validate calculation.qvf another.qvf
```

A packaged installation can install the same extra from its original wheel.
If validation support is absent, the command reports each file as
`NOT CHECKED` rather than invalid, gives the installation hint once and
exits unsuccessfully; it never reports a partial check as a strict pass.
In Python, `vibeview.validate()` then raises `ValidationUnavailableError`,
a `QVFError`, with the same hint.

The CLI prints QVF finding codes and warnings and exits 1 if any archive is
invalid. An orphan member or withdrawn version 2 produces a warning and can
still pass. Python returns `valid`, `errors`, `warnings` and `findings`, each
finding retaining qvf's `severity`, `code`, `where` and `message`. Invalid
archives include `error`; valid ones include section/member counts. After
reference validation passes, the viewer checks that its reader can open the
manifest. A consumer-specific failure is reported separately as `reader_error`
and makes `valid` false without inventing a QVF finding code.

```python
from vibeview import validate

report = validate("calculation.qvf", limits={"max_total_bytes": 1024**3})
for finding in report["findings"]:
    print(finding["severity"], finding["code"], finding["where"], finding["message"])
```

Limits are passed to qvf's `Limits.from_dict`; its defaults apply otherwise.
Caller-owned readers and streams stay open, and stream positions are restored.
Validating an open reader checks its original archive, not unsaved edits.
An unreadable container or manifest raises `QVFOpenError`; its
`validation_report` attribute retains the reference validator's structured
findings. Resource-limit failures remain invalid reports, and the adapter
does not reopen their rejected payloads. The CLI clips long diagnostics;
the API report retains the complete findings.

### How drift is caught

Before the 2026-09 split the schema was a symlink into the producer's tree
inside the monorepo, so it could not diverge. It is a real data file now,
shipped in the wheel, and drift is prevented by an explicit guard instead:

* `scripts/check_qvf_conformance.py` pins every vendored file by sha256 and
  `$id`, compares each byte for byte with its source in a checkout of the
  `qvf` repository at `QVF_TAG` and fails on any difference. It then reads
  every archive in qvf's `conformance/corpus`, and those in
  `conformance/legacy` with the warnings their `.expected.json` lists, and
  exercises every `conformance/negative` archive with its expected codes and
  resource limits. The candidate lane enables `--strict-negatives`.
* The Python 3.13 `test` CI job runs it before pytest, on a fresh clone of
  the `qvf` project at `QVF_TAG` and the installed package.
* `tests/test_qvf_schema_identity.py` imports the same pins, so the check runs
  in the test suite too.
* `tests/test_qvf_writer_interop.py` opens an archive written by qvf-writer
  0.2.0 with the calls vibe-sketch makes. With the qvf checkout present, it
  also writes one with that release's writer and runs vibe-view's own
  archives through that release's validator, so both directions of the
  hand-off are checked.

Both lanes go **red** on drift — that is the point. The guard that came out of
the monorepo skipped whenever a sibling checkout was missing, which after the
split would have meant skipping forever.

## Producing QVF files

vibe-view is a consumer, not a producer, with one exception:
`vibe-view import` converts a loose structure or volume file into a QVF
archive, and `vibe-view demo` writes a bundled one.

[vibe-qc](https://vibe-qc.com/docs/) is the reference producer, but nothing
about the viewer assumes it. Any program that emits a conforming archive can
be read here, which is what the conformance corpus in the qvf repository is
for.
