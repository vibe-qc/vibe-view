# Changelog

All notable changes to vibe-view are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Standalone release records begin with `v2.15.2`, the first public commit
(`9585e28`). The earlier v2.0.0 entry is retained as monorepo history; that
repository history was not transferred — see the [README](https://github.com/vibe-qc/vibe-view/blob/main/README.md#history).

Public snapshot tags are linked where available. Older releases without a
public tag link to their historical notes in the v2.18.1 snapshot; v2.0.0 has
no tag in this standalone repository.

## [Unreleased]

## [v2.20.17] - 2026-10-18 - *Levoy's Lemur*

This patch adopts the QVF 0.5 reference toolkit and fixes long GIF exports,
symmetry detection for linear molecules, and species-preserving alignment
of rigidly rotated structures. Existing QVF archives remain compatible.

### Changed

- Pin QVF toolkit 0.5.0 for reference validation and writer interoperability.
  The vendored QVF v1 schema is byte-identical to that release; existing
  archives remain compatible.

### Fixed

- Fix long GIF exports failing with “Too many open files”, including the MP4
  fallback when ffmpeg is unavailable. Decode and close PNG sources one at a
  time, and release decoded frames after successful or failed encoding. (#102)
- Fix molecular symmetry detection reporting finite point groups for linear
  structures with more than two atoms. Linear molecules now report `D∞h` or
  `C∞v` according to element-preserving inversion, and their principal axis
  follows the molecular line, including for diatomics. (#101)
- Fix false nonzero RMSDs from `align_by_species` for rigidly rotated
  structures with unchanged atom order and repeated elements. Compare the
  indexed fit with the existing element pairing and retain the lower RMSD when
  the element sequences match. (#100)

## [v2.20.16] - 2026-10-16 - *Levoy's Lemur*

This patch keeps CIF cell parameters and atom sites within the same data block,
refreshes the format manual, and aligns the pending-QVF integration fixture
with registered QC runtime metadata. Runtime selection is unchanged.

### Fixed

- Restore the manual links to orbital evaluation limits by resolving the existing named target. (#99)

- CIF imports now keep the first structure's atom sites and cell parameters in
  the same data block. Cell values from other structures or metadata blocks no
  longer rescale coordinates or make a molecule periodic. (#97)
- Correct stale unreleased labels for volume rendering, playback, thumbnails
  and units; refresh the release summary and Gamma-point orbital limits while
  retaining deferred excited-state table status. Read the manual version from
  its source tree. Align volume-rendering hints with the current QVF
  specification and record the shipped Python 3.14 lane.

## [v2.20.15] - 2026-10-15 - *Levoy's Lemur*

This patch release adopts the QVF 0.4 reference validator and adds combined
normal-mode geometry editing with an ORCA calculation tutorial. Python
and Gaussian input imports preserve atom definitions and molecular state,
while QVF readers handle archived modes and localized orbitals consistently.

### Added

- Create undoable geometry edits along a selected normal mode with a signed
  peak displacement in Å. Both directions start from the mode's equilibrium
  geometry, stop animation and auto-optimisation, and work with XYZ export,
  ORCA input preparation and vq submission. Includes a peroxide tutorial.
- Combine selected normal modes into one editable structure, with an
  independent signed displacement amplitude per mode. Contributions are summed
  from the archived equilibrium without rescaling the sum. Preserve
  single-mode editing, undo/redo, XYZ export and ORCA input preparation;
  document the combined-mode workflow.

### Changed

- ORCA documentation now connects selected-mode geometry preparation to the
  tested vq calculation and result-inspection workflow. The quickstart and
  peroxide tutorial distinguish the three downloadable example datasets and
  the properties each contains.
- Pin the byte-identical QVF v0.4.0 schema, including the clarified
  unrestricted NTO metadata contract; retain QVF version 1 and legacy archive
  compatibility.
- Expand the ORCA Raman/VCD tutorial with a browser-verified combination of
  two normal modes, screenshots, downloadable XYZ and ORCA inputs, and
  Undo/Redo checks.
- Documented the agreed vibe-sketch/vibe-view responsibilities: 2D chemical
  drawing and publication reaction graphics in vibe-sketch, detailed 3D
  structure work in vibe-view, and input, editing, basic cleanup and
  calculations in both. Recorded the requirements for reference-geometry and
  FIRE cleanup in vibe-sketch and distinguished planned integration from
  existing functionality.

### Fixed

- Report QVF excited-state tables as not yet rendered instead of unsupported, while preserving their natural-transition-orbital energy references.

- CIF imports now reject missing, unknown and non-finite atom coordinates with an error naming the site, preserving an existing output instead of inventing zero coordinates or dropping atoms. Complete Cartesian alternatives are converted per site when fractional coordinates are unavailable. (#95)

- Preserve `QVFOpenError` for unreadable archives and manifests, retaining
  the reference findings on the exception. Bound CLI diagnostics and show
  readable layout byte counts without reopening resource-rejected payloads.

- Python-input imports now accept the `Z` and `xyz` keyword arguments in
  `Atom` constructors, including mixed positional/keyword and module-qualified
  calls in molecular and periodic inputs. Atom identities and coordinates are
  preserved without executing the script. (#94)
- Parse Gaussian route, title and molecule sections by their blank-line
  boundaries, so numeric or multi-line titles no longer prevent Cartesian
  geometry imports. Preserve the actual molecule's charge, multiplicity and
  coordinates. (#93)
- ORCA Mayer bond-order imports now keep native metadata in the QVF vendor
  namespace, so strict payload validation passes. The selected-mode peroxide
  tutorial now follows a real vq-submitted ORCA single point through result
  import, validation and browser inspection, with a downloadable dataset and
  screenshots.
- Refuse Python-input imports when any atom or position in a literal atom list
  cannot be resolved statically. Molecular and periodic imports now report an
  error instead of substituting zero coordinates or silently dropping atoms.
  (#92)
- Read vibration modes without embedded atoms using their archived reference
  structure. Treat IBO, IAO, Boys, Pipek–Mezey and NBO orbital kinds as
  localized for display, without false HOMO/LUMO labels. Ignore malformed
  camera hints using the pinned QVF Camera definition. Exercise all negative
  corpus archives with the QVF reference validator.
- Strict QVF validation now delegates to qvf's reference validator, preserving
  error/warning codes and applying its resource and semantic checks. Install
  the optional `validation` extra after qvf's pinned release package; missing
  support produces an actionable error instead of a partial validation pass.
- Candidate conformance checks require all negative-corpus expectations,
  including fixture-specific limits and warning-only archives. The v0.4.0
  corpus passes 76 positive/legacy and 110 negative cases without schema
  edits.

## [v2.20.14] - 2026-10-15 - *Levoy's Lemur*

This patch release reads QVF 0.3 archives and preserves structure identity,
charge and multiplicity across editing and calculation hand-offs. The
structure editor gains atom and selection dragging, bounded displacements,
XYZ export and FIRE relaxation with the existing optional engines.

### Added

- vibe-view now checks manifests against the qvf v0.3.0 schema, vendored byte
  for byte from that release under the rule recorded in qvf#11 (#88). Natural
  transition orbitals written the v0.3.0 way (`orbital_kind: "nto"`,
  `nto_role`, `state_index`, and `state_ref` naming an `excited_states`
  section that holds the energy) and the way vibe-qc has written them so far
  (`natural` with the now deprecated `excitation_index` and
  `excitation_energy_ev`) both show as transition weights of their state,
  which the terminal viewer and the browser name; before, most of vibe-qc's
  NTO sets were reported as natural orbitals of undeclared meaning.
  Trajectories and reaction paths that name their atoms by `symbols` or
  `atomic_numbers` instead of `atoms` now open, including qvf's own
  withdrawn-version example. An empty `functional` or `basis` in a `job.spec`,
  the deprecated spelling of null, reads as null, and `merge` and `slice`
  write the v1 `schema_uri` beside `qvf_version: 1`.
- Added mouse dragging for individual atoms and arbitrary selections in the 3D
  editor. Shift-click toggles selection; Select connected, Select all and
  Clear selection make groups easy to move. Frozen atoms stay fixed, each drag
  is one undo step, and auto-optimisation pauses until release. Geometry
  exports and calculation inputs use the moved coordinates. Undo/redo also
  clear stale selection highlights. The editor and ORCA tutorials explain the
  new workflow.
- Added bounded, undoable random atom displacements and a direct plain-text
  XYZ download in Atom Editor. Selected and frozen atoms are respected, and
  displacement turns off live optimisation to preserve the perturbed geometry.
  Added FIRE damped-MD relaxation alongside L-BFGS using the existing optional
  MSINDO/MACE engines, with streamed geometry, frozen atoms and per-run undo.
  Expanded the builder and ORCA tutorials with a draw, pre-optimise, perturb,
  export and submit workflow.

### Changed

- Archives stamped with the withdrawn `qvf_version` 2, which vibe-qc 0.10 to
  0.15 wrote for periodic reaction paths, are now read and checked as version
  1 against the bundled v1 schema, as QVF spec § 3.1 and qvf's own validators
  require, and open with the warning `W_VERSION_WITHDRAWN` at the top of the
  browser's Run Info card and under Manifest warnings in the terminal overview
  (#88). vibe-view no longer ships a v2 schema of its own: it vendors only
  byte-identical copies of files from the pinned qvf release, the rule
  recorded in qvf#11, so a field vibe-view needs goes into qvf first.
  `vibe-view doctor` no longer lists a `schema_v2` resource.

### Fixed

- Archives written by qvf-writer 0.2, including everything vibe-sketch saves
  or sends to the viewer, now open (#88). The bundled qvf v0.3.0 schema
  includes the v0.2.0 support for root `byte_order` and `metadata` blocks and
  member `size` that the old copy refused. The new schema is stricter in one
  place a viewer user can meet: each `dos.projected` channel must be an object
  such as `{"label": "Fe d"}`, so archives listing plain strings are refused.
- Charge and multiplicity now survive the trip between vibe-view and
  vibe-sketch (#89). XYZ comments (`charge=-1 multiplicity=1`, as vibe-sketch
  writes them), Gaussian charge/multiplicity lines, SDF `M CHG` / `M RAD`
  records and atom-block charges, and molecules built from SMILES now record
  both in the archive. So vibe-sketch can draw an ion that vibe-view wrote,
  where before it reported that no bonds could be perceived. The Calculation
  Parameters panel also starts from the charge and multiplicity of the file on
  show, so a sketched anion is no longer submitted as neutral.
- vibe-sketch documents and other archives whose structures are not called
  `structure` now open, export and capture (#90). vibe-view looked the
  structure up by that id, so a sketch showed an empty viewport and `export`
  failed with "section not found: 'structure'". It now finds structures by
  kind and shows the archive's first structure: the first one
  `viewer_defaults.auto_open` lists, else the first structure section (QVF
  spec § 4.1). Selecting another structure in the sidebar shows it, marked
  with an eye, and an unsaved edit stays with the structure it was made on
  until you select that structure again; undo history ends at the switch, as
  it does on a file switch. `export --section` and `capture --section` pick
  another structure. The sidebar, `vibe-view info` and the terminal name each
  species by the label and name an archive gives it, and the Calculation
  Parameters panel takes the shown structure's charge and multiplicity before
  the provenance's. `reaction.scheme` sections show their steps in the details
  panel, and `preview` sections their thumbnail, instead of being listed as
  unsupported; neither is drawn in the 3D view.

## [v2.20.13] - 2026-10-14 - *Levoy's Lemur*

This patch release makes QVF container submission choose its vq program by the
program's vibe-qc import check rather than its name, so vq's own runtime is no
longer counted as a vibe-qc program (#87).

### Fixed

- Submitting a QVF container no longer counts the queue's own runtime, or any
  other registered environment that is not vibe-qc, as a candidate program.
  The viewer now recognises a vibe-qc program by the module vq's import check
  loads (`vibeqc`) instead of by a `vibeqc` name prefix, so a host registering
  one vibe-qc program beside the queue's runtime submits without
  `VIBEVIEW_VQ_PROGRAM`, and several vibe-qc programs are still reported as
  ambiguous (#87).

## [v2.20.12] - 2026-10-14 - *Levoy's Lemur*

This patch release makes the `[queue]` extra require the current `vibe-queue`
distribution and passes the vq program and runtime pin when submitting a single
QVF container, which vq refused without them (#86).

### Fixed

- The queue integration works with current vibe-queue again. The `[queue]`
  extra required the distribution `vq`, which vibe-queue renamed to
  `vibe-queue` in 0.26.10, so a current install could not satisfy it; it now
  requires `vibe-queue>=0.26.10`, and `vibe-view doctor` reads the version
  under that name. Submitting a QVF container was rejected by the queue
  because it named no vibe-qc program and no runtime pin; the viewer now
  chooses the registered vibe-qc program (or the one named by
  `VIBEVIEW_VQ_PROGRAM`), passes its runtime pin, and says in the status line
  when no single program qualifies (#86).

## [v2.20.11] - 2026-10-14 - *Levoy's Lemur*

This patch release fixes the vibe-qc Python input templates, which passed
charge and multiplicity to `run_job` instead of setting them on the `Molecule`
(#85), and makes the setup-script tests use the suite's own interpreter (#84).

### Fixed

- Exported and queued vibe-qc scripts for molecules now run (#85). The
  templates passed `charge` and `multiplicity` to `run_job`, which does not
  take them, so every single-point, optimisation, frequency and full-analysis
  script stopped with a `TypeError`. Both values now go on the `Molecule`, so
  an ion or a radical also runs with its real charge and spin. Periodic
  scripts were already correct.

## [v2.20.10] - 2026-10-14 - *Levoy's Lemur*

Recover safely from malformed saved preferences, preserve generated input strings,
open vibe-sketch from Build molecule, and inspect ORCA Raman and VCD spectra
with worked reference tutorials.

### Added

- Sketch a molecule in vibe-sketch from the **Build molecule** dialog or the
  command palette when the new `[sketch]` extra is installed (#77). *Send to
  vibe-view* in the sketcher opens the structure in the running viewer as a
  new file. Without the extra, the button shows the install command.
  vibe-sketch is not on a package index yet, so the extra is not part of
  `[all]`. The SMILES field is unchanged.
- Import ORCA Hessian Raman activities and matching output-log VCD rotatory
  strengths with native units, preserving Raman depolarization ratios. Add
  Raman/VCD options to ORCA input preparation and vq submission, and a worked
  tutorial with real calculated spectra and a mirror-conformer check.

  Make spectrum charts fit resized result panels so lower axes and negative
  peaks remain visible.

### Fixed

- Generated vibe-qc Python inputs preserve quotes, backslashes and line breaks
  in string parameters and output names. Multiline titles stay in comments
  across all molecular and periodic templates. #81
- Recover to default preferences when `settings.json` is unreadable, has
  invalid UTF-8 or JSON, or contains a non-object JSON value, so viewer
  startup and comparison commands remain usable. Preserve valid saved
  preferences and allow an explicit save to repair the file. (#82)

## [v2.20.9] - 2026-10-14 - *Levoy's Lemur*

Prepare and submit molecular ORCA inputs, inspect Hessian vibrations and IR spectra,
and follow the worked water tutorial. Improve table exports, connectivity,
structure framing, atom outlines and safe Python-input selection.

### Added

- Read ORCA `.hess` geometry, harmonic normal modes and IR spectra, alone or
  with same-basename calculation companions. Support browser uploads and
  desktop file selection, preserve imaginary/zero frequencies, and handle
  translated Hessian geometries. Extend the water tutorial with a real ORCA/vq
  frequency calculation, downloadable results, mode animation and IR
  screenshots.
- Prepare basic molecular ORCA HF/DFT inputs from the current edited
  structure, preview and save `.inp` files, and submit them through the local
  vq queue with matching process, memory and time budgets. Open completed ORCA
  jobs as combined calculation datasets in the Job Manager. Add a step-by-step
  ORCA preparation and submission tutorial; retain vibe-qc input and container
  workflows.
- Add a worked ORCA/vq water tutorial with the tested input, a downloadable
  calculated QVF, browser screenshots and numerical reference comparisons.

### Fixed

- Ignore malformed color or opacity node lists in producer transfer-function
  hints so they cannot crash viewer startup or headless volume-hint lookup.
  Other valid hints and valid transfer functions still apply. (#72)
- Keep loaded and skipped files in one Markdown table cell when
  batch-comparison filenames contain pipes, backslashes or line breaks. Notes
  also preserve literal backslashes without introducing extra columns. JSON
  output retains the original paths. (#74)
- Preserve explicitly declared zero bonds when importing Mol2 and V2000
  Mol/SDF files, so the viewer and CML/HTML exports do not invent
  distance-based bonds for disconnected atoms. Inputs without connectivity
  still use the existing bond inference. (#75)
- Fit the standalone HTML viewer's initial camera, orbit center and clipping
  planes to the rendered structure, so translated, large and periodic
  structures open fully in view in landscape and portrait windows. (#76)
- Atom-property tables now export supplied IAO charges and spin populations,
  including sections containing only either array, through the CLI, Python SDK
  and notebook table view. Existing columns are preserved, with `iao` and
  `spin_population` appended when present. #78
- Fit SVG and PDF figures around the complete atom circles rather than their
  centres, so large atoms in space-filling representations retain their full
  outlines. The shared bounds also cover the raster PDF fallback. #79
- Fix ORCA calculation dropdown bindings that prevented Vue from compiling the
  viewer page. Keep engine, method and basis choices in reactive state lists.
- Python-input imports now honor atom-list comprehension filters, multiple
  generators and local loop bindings. Unsupported selections raise an error
  without executing input code or writing an incorrect archive. #80

## [v2.20.8] - 2026-10-14 - *Levoy's Lemur*

Restore desktop updates to compatible tagged source revisions in standalone
and nested checkouts while preserving the safety-protocol check before mutation.

### Fixed

- Fix desktop updates to tagged revisions in standalone and nested source
  checkouts by resolving the installer from the actual Git project prefix.
  Revisions lacking the safe desktop update protocol still refuse before
  checkout or environment changes. (#71)

## [v2.20.7] - 2026-10-13 - *Levoy's Lemur*

Open ORCA calculations as combined datasets and inspect their structures,
properties, orbitals and spectra with a dedicated step-by-step tutorial.
GBW import requires a matching `orca_2json`; supported orbitals are real
spherical s–f. MCD imports remain raw sticks, Compound steps stay separate,
and native Hessian, amplitude and density-matrix support remains incomplete.

### Added

- Open same-basename ORCA results as one dataset with a shared structure,
  orbitals, spectra and properties. Input/output files can locate their result
  companions. Geometry and electronic-state checks reject incompatible files;
  explicit representation choices and timestamps resolve alternate exports.

  Group companions in folder discovery and imports, avoid duplicate aggregate
  and per-transition MCD data, preserve source provenance, and use calculation
  names for datasets and QVF outputs. Browser ORCA JSON uploads are recognized
  from their contents; conversion failures show the concrete cause.
- Open and import ORCA 6.1 property text and JSON with structures, all
  absorption/ECD method and gauge variants, charges, Mayer bond orders and
  available SCF energy/dipole metadata. Preserve other properties as
  unrendered vendor data. ECD plots label signed rotatory strengths with their
  source units.

  Read standalone ORCA MCD sticks, retaining signs and duplicate energies;
  import supported real spherical s–f orbitals from exported ORCA JSON or GBW
  through an installed matching `orca_2json`. GBW export runs in a temporary
  directory without requesting integral or density matrices. Each input
  remains a separate archive.

  Preserve tiny signed spectral strengths and show transition assignments in
  hover labels. Refuse overlays mixing different intensity quantities and
  density reconstruction from CASSCF orbitals without a verified density
  basis.

  Register GBW and MCD as desktop viewer file types. Document supported data,
  conversion settings and limits in the format guide.

### Changed

- Add a dedicated ORCA chapter with step-by-step calculation opening, orbital,
  spectrum and property inspection, portable QVF export, batch workflows and
  troubleshooting. Link the guide from the quickstart, workflows, CLI and
  capability reference, and document the supported scientific scope.

## [v2.20.6] - 2026-10-11 - *Levoy's Lemur*

Clarify contributor roles and completion rules; application behavior is unchanged.

## [v2.20.5] - 2026-10-10 - *Levoy's Lemur*

Release candidates now run the full qualification suite while development updates avoid redundant CI.

### Changed

- Reserve CI for release candidates and release documentation publication.
  Development refs no longer create MR, push, manual or scheduled pipelines.
  Candidates always run the full test matrix, conformance and packaging
  checks.

## [v2.20.4] - 2026-10-10 - *Levoy's Lemur*

Malformed cross-fade hints now preserve the default blend and other viewer settings.

### Fixed

- Ignore malformed cross-fade blend hints when opening a file, preserving the
  default blend and the file's other viewer hints (#70).

## [v2.20.3] - 2026-10-08 - *Levoy's Lemur*

This release adds direct volume rendering to the browser viewer, with a
per-section transfer-function editor and skewed-grid support, an off-screen
volume renderer for `vibe-view capture`, a frame scrubber for trajectory,
reaction-path and vibration playback with energy-profile seeking and playback
keys, and thumbnails in the structure library. The display units of #58 now
reach the rest of the viewer.

### Added

- The **Structure Library** card shows a thumbnail of the molecule you pick,
  and every pinned and recent chip carries a small picture of its molecule.
  The pictures are rendered off-screen in the background from the library's
  geometry once a name is picked, pinned or used, and only the names the card
  shows are kept.
- A click on the **Energy Profile** of a trajectory or reaction path jumps to
  the frame under the pointer, and the terminal's playback keys work in the
  browser: Space plays or pauses the trajectory or vibration on show, `[` and
  `]` step a frame or a phase step, `<` and `>` change the normal mode. The
  shortcut list (`?`) names them.
- The browser's trajectory, reaction-path and vibration playback gained a
  scrubber. A filmstrip of up to sixteen frame thumbnails sits under the
  trajectory slider, rendered off-screen in the background as the section
  opens; a click jumps to the frame and a drag across the strip scrubs. A
  vibration gets a **Phase** slider over one period and a filmstrip of the
  mode at every second step; **Pause** now freezes the motion at the nearest
  step instead of snapping back to the rest geometry, and the strip follows
  the mode and the amplitude.
- `vibe-view capture --render volume` draws a volume section the way the
  browser's volume mode does, off-screen: the same capped grid and the same
  transfer function from the section's hint or the kind's preset. The default,
  `auto`, follows the section's `render` hint and draws the isosurface when
  there is none, so existing archives capture as before; `batch` follows the
  hint the same way. The Python API's `capture_volume` takes `render` and
  `transfer_function`.
- Volume mode's transfer function can be edited node by node: the volume
  controls gain an opacity curve over the window with the colormap as its
  background, where a handle drags with the pointer, a click adds a node, a
  double-click or Delete removes one, and the arrow keys move a focused
  handle. Editing the curve makes the preset *Custom curve*; the six ramp
  sliders show its sampling.
- The command palette gains *Toggle volume rendering of the active volume*,
  which flips the volume controls' **Render as** picker without a visit to the
  card.
- The browser viewer can draw a stored volume as a volume: **Render as →
  Volume** in the volume controls sends the grid as 256 levels over a per-kind
  range, resampled so its largest axis is at most 128 voxels, and draws it
  with the section's colormap and an opacity ramp for the kind whose peak the
  Opacity slider sets, optionally shaded. Skewed grids fall back to the
  isosurface with a message; the contour-only controls hide in volume mode.
  The transfer-function editor, sessions, bookmarks and producer hints follow
  in later steps of the design note.
- Volume mode now draws a skewed grid: the field is interpolated onto the
  axis-aligned box that bounds it before it is sent, at the same cap, and the
  status line says so. The browser's picker no longer returns to the
  isosurface for a skewed grid, and `capture --render volume` draws it the
  same way. The corners of a triclinic cell's box stay empty and periodic
  tiling is not applied.
- Volume mode gained its transfer-function editor: a **Preset** that seeds the
  opacity ramp, two **window** sliders for the range the levels cover, and six
  **opacity ramp** sliders across it. The render mode and the transfer
  function now belong to each volume section, as its isovalue does, and
  bookmarks, presentation slides and sessions keep them. A producer can open a
  section as a volume with its own function through the `render` and
  `transfer_function` keys of `viewer_defaults`.

### Changed

- The display units of #58 now reach the rest of the viewer. COOP and COHP
  charts follow **Settings → Band / DOS energy unit** in the browser and the
  terminal, and `vibe-view capture` draws a band structure in it;
  `capture_bands`, `capture_dos` and `capture_energy_diagram` take an
  `energy_unit` argument and `capture_bond_orders` a `length_unit`. The
  **Length unit** now also reads out the RMSD in the compare legend, the
  distance column of the Bond Orders panel, the largest shift Symmetrize
  reports, and the RMSD of `vibe-view diff` (which printed a bare `A`) and
  `vibe-view batch-compare`. Archives, JSON output and CSV downloads keep
  their stored units.

## [v2.20.2] - 2026-10-06 - *Levoy's Lemur*

This release adds the Publication display preset, repairs reduced-detail
rendering of large unsigned volumes, and extends Python 3.14 compatibility.
It also introduces the internal direct-volume payload module; the complete
interactive volume renderer remains under development.

### Added

- The *Publication* view preset now gives the one-click figure look (#68): the
  scientific material on its near-white background, ambient occlusion and
  labels off, a black outline around every atom and bond, and the viewport
  chrome hidden (the bounds grid, the app-bar chips, the hover tooltip and the
  welcome card). Clearing the picker restores the look the first preset
  replaced, and the look survives file reloads. **Outline atoms and bonds**
  and **Hide viewport chrome** are also separate switches in the Display card
  and the command palette, and `vibe-view capture --publication` renders the
  same look headlessly.

### Changed

- CI now runs the full suite on Python 3.14 (`test-py314`) for candidate, web
  and scheduled pipelines, and `pyproject.toml` lists the 3.14 classifier.
- The `trexio` extra now installs `trexio` only below Python 3.14, which has
  no wheel for it yet; on 3.14 `vibe-view[trexio]` installs nothing and TREXIO
  import reports the missing package.

### Fixed

- The dark UI theme's indigo accent now meets WCAG AA as text on the dark
  surface (5.3:1, was 3.4:1); buttons and the app bar in dark mode use the
  lighter accent with dark text (#68).
- An unsigned stored volume with 200 or more voxels along an axis opens again
  in the browser: the reduced-detail path now hands the renderer the
  subsampled array together with the subsampled grid instead of failing on the
  point-array length, and the status line names the full and reduced grids
  rather than announcing a reduction that did not happen (#69).

## [v2.20.1] - 2026-09-30 - *Levoy's Lemur*

This patch release fixes the error reports left over after v2.20.0. Open-time
errors are clipped like every other error (#64), `validate` tells a layout
error from a hash mismatch (#65), and the reader refuses three malformed
payloads that used to end in the last-resort catch (#66). `batch-compare` gives
a reason for every missing RMSD or skipped file and keeps `--json` output JSON
(#67), and GRO and PDB import validate the first frame and the placeholder
cell (#40). The browser viewer's internals are reorganised around a typed
composition root with no change in behaviour (#48).

### Fixed

- Errors raised while an archive is opened are clipped to the same 2000
  characters as every other error (#64). A manifest whose `qvf_version` or
  section id was 200,000 characters long put about 400 kB of schema error on
  the terminal, because the open path had its own copy of the report and never
  reached the clip added in #63. `info`, `info --json`, `validate`, `show` and
  terminal mode all share one bound now.
- `vibe-view validate` labels a member whose bytes match their hash but not
  their declared `dtype` and `shape` as `LAYOUT ERROR`, keeping `SHA-256
  MISMATCH` for real hash failures (#65). `vibeview.api.validate` gives each
  failing member a `reason` of `"sha256"` or `"layout"`.
- The reader refuses three payloads that used to pass it and fail later in a
  renderer, reaching the CLI last-resort catch (#66): a spectrum whose
  `frequencies` and `intensities` differ in length, or are not flat lists of
  finite numbers; a `wavefunction.gto` shell whose exponent and coefficient
  counts differ, has no primitive, a non-positive exponent, or a negative
  `center` or `l`, and a basis whose shells span more atomic orbitals than
  `n_ao` or the coefficient width; and MO `energies` or `occupations` that
  list more orbitals than the coefficient matrix has rows. Each is a section
  error naming the section, member and shell, so `show`, `capture` and
  `animate -k orbital` report it in one line. `animate -k orbital` counts the
  orbitals from the coefficient rows, as every other consumer does.
- `vibe-view batch-compare` and `vibeview.batch_compare.compare_batch` keep
  going when the reference structure cannot be read, and every missing RMSD
  carries its reason (`cannot read structure: …`, `no atoms`, `atom counts
  differ`) instead of a bare `null` (#67). A file that does not open is listed
  under `skipped` with the reason, `-r` counts the files as given rather than
  only the ones that opened, and a reference file that does not open is a
  clean error. `batch-compare --json` reports every error as a `{"error":
  ...}` object on stdout, as `diff --json` does. `vibeview.align.rmsd` and
  `kabsch_fit` refuse empty input with `ValueError` instead of `AxisError` or
  a silent `nan`.
- `batch-compare --json` now reports argument-validation failures as JSON on
  stdout, including missing files, missing arguments and an invalid reference
  index option. These usage errors retain exit status 2 (#67).
- GRO import now validates the complete first frame, reads its own box,
  supports variable coordinate precision, and rejects malformed or non-finite
  records without replacing an existing output. PDB import recognizes the full
  wwPDB placeholder cell, including its space group and Z value, preserving
  genuine small periodic cells (#40).

## [v2.20.0] - 2026-09-29 - *Levoy's Lemur*

This release works through the follow-ups from the #34 viewer audit (#43 to
#56). QVF readers and the lazy loader, renderers, the browser viewer, terminal
mode, the command line and Python API, converters and exporters, and packaging
get their correctness defects repaired, each with reproducer tests, along with
refactor, documentation and performance follow-ups. The browser and terminal
viewers gain display units for orbital energies, band and DOS energy axes and
lengths (#58); archives keep their stored units. Signed volumes keep both lobes
along a reaction path (#57), and `serve`, error reporting, `get_volume` on
complex data, the bond search's work on dense structures and the commands that
used to end in the last-resort catch are corrected (#59 to #63).

### Added

- **Settings → Band / DOS energy unit** shows the browser's band-structure,
  DOS and combined panels in eV (the default), Hartree or kcal/mol (#58). The
  DOS is rescaled with its axis, so the number of states is unchanged. The
  energy-window fields follow the unit and keep a typed window when the unit
  changes. It is a separate setting, `band_energy_unit`, so orbital energies
  keep their own Hartree-default **Energy unit**. Archives and `energy_window`
  hints stay in eV.
- Energy display unit (#58). **Settings → Energy unit** shows orbital energies
  in Hartree, eV or kcal/mol in the browser's Molecular Orbitals list and
  energy diagram. The choice persists, and saving it re-labels an open list
  and diagram. Archives stay in Hartree. The conversion and formatting live in
  `vibeview.units` (`ENERGY_UNITS`, `format_energy`).
- **Settings → Length unit** shows the browser's measured distances in
  Ångström or bohr (#58). It applies to interactive picks and to the full bond
  list, persists as the `measure_unit` setting, and re-measures an open
  readout when saved. Structures stay in Ångström. `vibeview.units` gains
  `LENGTH_UNITS`, `length_unit`, `length_label`, `length_from_angstrom` and
  `format_length`.
- Terminal mode follows the orbital-energy unit setting (#58). The surface
  table, the orbital data table and its HOMO–LUMO gap use the unit chosen in
  the browser's **Settings → Energy unit**. The new <kbd>e</kbd> key steps it
  through Eh, eV and kcal/mol and saves it for both viewers. The archive stays
  in Hartree.
- Terminal mode reads the browser's **Length unit** and **Band / DOS energy
  unit** settings (#58). The geometry table shows cell edges, coordinates and
  bond lengths, and the bond-order table its distances, in Å or bohr. Bands
  and DOS charts in `vibe-view tui` and `vibe-view show` draw their energy
  axis in eV, Hartree or kcal/mol, with the DOS rescaled to match. The
  defaults are unchanged. `vibeview.api.render_terminal` keeps eV unless it is
  given `band_energy_unit`.

### Changed

- One colour palette for every chart and scene. Band-structure channels beyond
  the tenth are the same colour in PNG and HTML output, COOP and spectra
  comparisons draw from the same series, and the terminal viewer's chart
  series, chain colours and secondary-structure colours now match the
  browser's cartoon and chart palettes instead of a separate set. (#34, #36,
  #38)
- Every Plotly chart shares one layout and one serializer. Charts are all 450
  px tall (spectra were 350, SCF convergence 380, COOP 400), the Fermi, zero
  and k-path guides look the same in bands, DOS, COOP and phonon panels, and
  every chart accepts the same Plotly-runtime option so the app can bundle
  Plotly for offline use uniformly. (#34, #36)
- Reader, kinds and viewer-defaults follow-ups (#44). `QVFReader.infer_bonds`
  is now `QVFReader.connectivity`, named for what it returns (explicit bonds,
  or the inference, enriched with `bond_orders`). The lazy loader is gone:
  symmetry detection reads the active reader and the memory panel reports
  `QVFReader.cached_bytes()` by kind. Structure payloads are parsed and
  validated once per archive content, an edit overlay no longer builds the
  file's atoms it replaces, and a structure's chain map, residue flag and CA
  list are computed once. Periodic bond inference searches pairs only from
  real atoms (same bonds, less work). Viewer hints are merged over the kind
  defaults once, when the state is built from the manifest and the section
  kinds, and the first section carrying a replication hint sets the supercell.
  `kinds.RENDERED_VIA_STRUCTURE` names the via-structure rule,
  `vibeview.energy_window` moved out of the renderer package,
  `qvf.RELOCALIZED_PREFIX` names the re-localization overlay prefix, and the
  unused `LAZY_KINDS`, `is_lazy_kind` and unknown-kind id set are removed. The
  editing manual says an edit drops residue, chain and secondary-structure
  metadata, and the Python API page gives the volume dtype correctly.
- Renderer follow-ups (#46). Renderers read through typed reader methods
  (`read_dos`, `read_bands_projections`, `read_fermi_surface`,
  `read_json_member`) instead of private members; bands projections and the
  wavefunction's centre structure are read once. The three wavefunction
  evaluators share one shell walk and one monomial angular table, and the five
  molecular sampling grids one builder. NCI takes the Hessian's middle
  eigenvalue in closed form instead of allocating a `(n, 3, 3)` block. Spectra
  sticks, the energy-diagram levels and QTAIM critical points are one trace or
  actor per family rather than one per item, and trajectory and reaction-path
  energy plots are cached per frame on demand rather than pre-rendered for
  every frame. The renderer registry is built once; `band_axis_ticks` is
  public; the NMR and EPR panels share one column picker; `volume.COMPONENTS`
  is the one component vocabulary (`abs` accepted for `magnitude`); the dead
  `make_bands_dos_html` is gone. The manual's spectra, bond-order and
  SCF-history passages now match the charts, the Lorentzian envelope names
  itself in the legend, and the circular-mean helper cites its source.
- Switching to another file now clears the residue selection instead of
  re-applying it to a different structure, where it highlighted unrelated
  residues. A hot reload of the same file keeps the selection. (#47)
- The browser viewer's code is a package, `vibeview.app`, instead of one
  16,000-line module (#48). The page layout, the controls drawer, the sidebar,
  the scene hooks and the state types are modules of their own; `create_app`
  and every name importable from `vibeview.app` are unchanged.
- Opening a section goes through one registry of section kinds in the browser
  viewer (#48). The sidebar click, the file's auto-open hints and the
  bands/DOS energy-window redraw resolve a kind the same way, the simple side
  panels (phonons, equation of state, citations, symmetry, NMR, EPR, job spec,
  bond orders, COOP/COHP) are rows of one table, and leaving the
  atom-properties overlay restores the element colours in one place. Switching
  between two chart or table panels no longer re-sends the whole 3D scene to
  the browser when nothing in it changed, and returning from an animation no
  longer redraws the atoms twice.
- Terminal and browser now share one scene convention. - Signed lobes are blue
  positive and red negative everywhere: terminal orbitals and volumes, the
  browser spin density and the density difference (blue = accumulation, red =
  depletion). - Chain colours follow the same chain order in the terminal atom
  view, the terminal backbone and the browser. - The terminal b-factor
  colouring uses the browser's ramp. An atom without a b-factor is grey, and a
  column with no variation sits at the middle of the ramp instead of falling
  back to element colours. - The browser cartoon's b-factor ramp now spans the
  whole structure instead of each chain, so equal b-factors get the same
  colour in every chain and match the terminal. (#49)
- The browser and the terminal now draw normal modes with one amplitude
  convention: each mode is scaled so its largest atomic excursion is 0.6 Å,
  multiplied by the browser's displacement-amplitude slider (×1.0 by default).
  The effective peak excursion is shown in the Vibrational Modes card and in
  the terminal status line, and the vibration video export uses the same scale
  and the selected mode. Previously the browser drew the raw displacement
  vectors, so a mode could look 30 times larger in the terminal than in the
  browser. The stored displacement data is unchanged. (#49)
- Terminal viewer follow-ups (#50). The frontier-orbital rule, its tolerances
  and the occupation semantics live once in `vibeview.renderers.wavefunction`
  (`frontier_orbitals`, `occupation_problem`) and the terminal panes and app
  use them. The interactive viewer and `vibe-view show` share one chart
  dispatch (`show.render_chart`) and one label overlay (`scene.label_atoms`);
  the viewer's caches follow one bounded policy and no longer wipe the mesh
  cache on an isovalue change. `kinds.VOLUME_KINDS` is the one volumetric kind
  set (`LAZY_KINDS` names the same set), the near plane and the projection
  scale are single constants in the rasteriser (`NEAR_PLANE`, `DEFAULT_FOV`),
  hex colours parse through the palette, the scan-surface heat map uses
  matplotlib's viridis, and `CellGrid.row_runs` finds its runs with one
  vectorised comparison. Dead code is gone: `Scene.bounds`,
  `Canvas.draw_pixels`, the module-level braille converters, the unused
  `_section_meta`; the orbital table activates a clicked row without
  overriding a private Textual handler. Tests pin the key bindings against the
  help text and the manual and exercise every terminal plot builder; the
  manual's tabulated list names `structure` and `vibrations`.
- CLI, API and headless follow-ups (#52). `info`, `diff`, `validate` and
  `table` are built on `vibeview.api`, which now declares `__all__`; the SCF
  energy and the provenance block are read through `QVFReader.scf_energy_eh()`
  / `QVFReader.provenance` everywhere instead of seven private copies.
  Trajectory, reaction-path and vibration videos render every frame through
  one plotter and one structure renderer (`headless.structure_frame_writer`),
  and the turntable writes its frames to a real temporary directory. `capture`
  reads the shared signed-volume kind set, `export` reads the structure once,
  `/serve-qvf?file=` streams the archive from disk, the dead
  `vibeview.desktop` module is gone, and the manual no longer says `dashboard`
  needs the browser stack.
- Converter and exporter follow-ups (#54). The SVG and PDF exports draw one
  flat figure (`vibeview.figure2d`) whose colours and radii, like the POV-Ray,
  Blender and HTML exports and the hydrogen builder, come from
  `vibeview.elements`; the Blender script and the HTML page embed tables only
  for the elements present, and the page reuses one material per element. The
  converters read their source through one helper, build CIF and PDB cells
  with `crystal_builder.cell_from_abc`, derive their extension table and
  format detection from the one capability table, and parse CIF loops into
  rows; `detect_format` documents the ASE, `.py` and plugin routes.
- Contributor and release tooling follow-ups (#56). User-visible changes are
  recorded as `changelog.d/` fragments, and `scripts/fold_changelog.py` folds
  them into `CHANGELOG.md` at release time; `docs/release_process.md` has the
  release checklist again, naming every version and codename site. CI runs the
  test suite on Python 3.11 as well as 3.13, installs the `[smiles]` extra in
  the test lane, and builds the documentation with warnings as errors on merge
  requests and release candidates. Timing benchmarks carry a `benchmark`
  marker and leave the default run, markers are strict, and the suite no
  longer writes into the system temp directory or the account's cache and
  config directories. The unused legacy `electron/desktop-launcher.py` is
  removed, and the README and browser manual quote the dashboard command and
  the Apply Replication button as the product shows them.

### Fixed

- Reader correctness follow-ups to the #35 audit. The lazy loader reads
  `spectra.epr`, `job.spec`, `fermi_surface` and vendor sections instead of
  failing on them, reports the real bytes of loaded arrays, and `unload()` now
  frees the reader's cached arrays (new `QVFReader.cached_bytes()` and
  `drop_cached()`). Bonds to periodic images are found in cells thinner than
  the bond cutoff, including a lone atom bonded to its own images. A malformed
  wavefunction or reaction-path payload, a non-object `dos.coop` meta, and a
  member read against its declared `format` raise `QVFError` and are reported
  on the section. Edit overlays refuse unknown element symbols and positions
  that are not finite (n, 3). `StructureData.dim` is derived from `pbc` and
  cannot contradict it. `.QVF` files open like `.qvf`. (#43)
- Projected-DOS channel labels are now HTML-escaped in the legend and hover
  text and kept out of the hover template, so a label cannot inject markup or
  Plotly placeholders. (#45)
- Renderer correctness (#45). The orbital energy diagram marks HOMO/LUMO (or
  HONO/LUNO) with the same rule as the MO picker, so fractional or unconfirmed
  occupations no longer get a spurious frontier pair and gap. An NCI surface
  computed from a wavefunction uses the stored-`volume.rdg` isovalue (s =
  0.3), colour map and ±0.05 a.u. range, and honours the periodic mask.
  COOP/COHP charts always label the energy axis in eV, as QVF fixes it, and
  producer pair labels are escaped rather than interpolated into the hover
  template. Trajectory and reaction-path energy plots accept array energies.
  `GridData` carries its length unit (`units`, `to_angstrom`), replacing the
  `grid_units` keyword of `build_isosurface_mesh` and `sampled_field_meshes`.
  Material presets keep only fields the viewer applies, and isosurfaces now
  take a preset's lighting terms; `enable_toon_rendering` drops its ignored
  `levels` parameter. The QTAIM table names units only for a producer whose
  convention is documented and otherwise says the values are as written. Bands
  charts keep their k-point axis title without path segments.
- Browser viewer correctness fixes from the app.py audit follow-up (#47). STL
  geometry export writes a real binary STL of the scene (it raised an error).
  Save Session, Load Session and the auto-save share one file beside the
  archive, and Load Session with an empty path loads it instead of failing on
  "Is a directory". Saved settings (background, material, auto-save interval)
  now apply at startup, and the auto-save honours its interval.
  `viewer_defaults.auto_open` renders what it opens for every section kind,
  and `vibe-view open --section` / `--auto-compare` activate the section and
  draw the overlay. A file switch no longer keeps the previous file's QA
  warnings or a "Molecular Orbitals" title for a crystal; streaming sections
  are marked on first open. "Center View Here" centres on the right-clicked
  point. Restoring a view keeps the Orthographic switch truthful, and
  bookmarks, slides and sessions restore the representation, material and
  computed orbital/density surface. Measure and edit modes are mutually
  exclusive and Esc leaves either. The HOMO/LUMO buttons work on natural
  orbitals (HONO/LUNO). Add Hydrogens saturates only the selected atoms. HQ
  render progress reaches the dialog; the video turntable and HQ render no
  longer race section switches, and the turntable restores the camera. The Job
  Manager monitor restarts when its drawer reopens; a second viewer in one
  process no longer runs every state watcher twice; headless screenshot/video
  calls no longer crash for want of an event loop; delivered downloads are
  dropped from the session state; ambient occlusion uses one configuration on
  every rebuild path.
- A hot reload of the same file from the file watcher now keeps the residue
  selection, as documented; only a switch to another file clears it. (#47)
- The saved "Dark background (default)" setting is honoured when the browser
  viewer starts, including a light background with the default material, whose
  own background is dark (#48). The browser's background now has one source:
  the toolbar toggle, the Settings dialog and every scene rebuild use the same
  colours and keep a chosen background across file switches, and the viewer's
  3D overlay colours (bond-order legend, pick and edit highlights, ELF,
  Laplacian, Fermi and compare tints) come from the shared palette, so the
  bond-order legend always matches the bonds.
- The browser manual now describes the controls as they are (#48): Show
  isosurface belongs to the computed wavefunction surface, the element colour
  override is in the Display card and the background default in Settings, the
  clip plane cuts the isosurface with three planes at once, QTAIM points are
  coloured by the Laplacian sign rather than clickable, sessions are named
  after the archive, video export offers format and frame rate, fragments land
  beside the selection or the structure, and no JSON geometry export is
  claimed. The vq submit dialog no longer shows an input-path field whose
  value was never used, the job monitor refreshes as soon as it is switched
  on, the calculation panel opens for a structure section whatever its id, and
  transparent screenshots no longer loop over every pixel in Python.
- Browser-viewer consolidation and performance follow-ups (#48). Uploading a
  file through the Load dialog accepts every single-file format `vibe-view
  open` reads, vibe-qc input scripts included, and names it after the file
  instead of "<in-memory>"; every newly opened, built or fetched file opens in
  the representation that suits it. Switching files no longer brings back an
  ambient-occlusion pass that was switched off. The orbital-opacity slider now
  also fades computed density, ELF, NCI and Laplacian surfaces, which keep
  their own translucency by default. The Python-input export is listed under
  Recent Exports like every other download. Hovering over the viewport no
  longer re-reads the structure (opening a file reads it twice instead of nine
  times), stepping through a trajectory reads it once, a view preset rebuilds
  the scene once instead of twice, the wrap-to-cell switch sends the scene
  once, and the resize handles and theme no longer poll the page every second.
- Edited structures in the browser viewer are drawn by the regular structure
  renderer, so they keep the chosen representation (space-filling, sticks) and
  the periodic replication instead of switching to plain balls and sticks, and
  large edited structures use the same batched drawing as unedited ones (#48).
  Every live-optimization step and geometry edit now sends the scene to the
  browser once instead of twice. Undo and redo are recorded by one routine for
  every edit.
- Clipping a stored orbital, electrostatic potential or basis-function volume
  in the browser now shows both lobes in the shared colours, blue positive and
  red negative, like the unclipped view. Previously only the positive lobe was
  drawn, in colormap colours. (#49)
- A stored volume section's `component` is now treated as a label only, on
  every path: the browser (open, clip, 2D slice, cross-fade), the terminal,
  `vibe-view show` and headless captures (batch, notebook and API) contour the
  stored array as it is. A producer stores the reduced field (|ψ| for `abs`,
  ψ² for `density`), so reducing it again drew the ψ⁴ surface for `density`
  and nothing for `imag`. A complex volume array, which QVF v1 does not allow,
  is reduced by its label or to its real part. (#49)
- A cross-fade between two signed volumes (orbital, electrostatic potential,
  basis function, density difference or spin density), declared in a file's
  viewer defaults, now shows both lobes of each volume in the shared colours,
  blue positive and red negative, at the blended opacity. Previously each
  volume drew only its positive lobe, in colormap colours. (#49)
- Computed molecular orbitals in the browser now use the shared lobe colours,
  blue positive and red negative, like every other signed surface. Previously
  they were drawn in a different blue and red. (#49)
- Terminal viewer correctness fixes from the audit follow-up (#49). Chart tick
  labels, k-path labels and the E_F legend are no longer dropped or
  overwritten, and the y-axis caption is shown in full. Near-zero ticks print
  0. Charts and the scan heat map honour `--mode` / the pixel mode. Band
  structures use the browser's Fermi convention: a stored 0.0 means no Fermi
  level, and any other value references the axis to E_F. Stored volumes
  contour through the NaN-aware path and use the browser's list of signed
  kinds. Translucent surfaces blend over what is behind them, large triangles
  no longer show pinholes, and a half-block blit keeps text already drawn. The
  wavefunction pane shows the geometry its `structure_ref` names. Vibrations
  show a frame counter and stop at the last mode. The section list scrolls to
  the selection. Kinds the terminal cannot draw read "skipped", and readers
  are closed. `vibe-view show --normal-mode N` picks the mode that `--frame`
  animates, and the CLI and the terminal share one default view rotation.
- `validate` reports an archive whose manifest is not a JSON object, fails the
  schema or declares an unsupported critical section as `INVALID` instead of
  printing a traceback. A `manifest.json` nested too deeply to parse, or
  deeper than 256 levels, is reported as `QVFOpenError`, and a JSON member
  nested that deeply is a section error. A section member that fails its CRC,
  cannot be decompressed, is encrypted or uses an unsupported compression
  method is reported as `Error: cannot read zip member …` with exit status 1
  by `capture`, `h-add`, `supercell`, `animate`, `slice`, `show`, `validate`
  and `merge`; the terminal viewer shows it in the viewport, and the file
  watcher no longer stops on such archives. (#51)
- `batch-compare`, and the Python helpers `blender_export.export_from_qvf` and
  `povray_export.export_from_qvf`, now close every file they open, including
  when the reference index is out of range or an export fails. (#51)
- Command-line correctness follow-ups to the #39 audit. `animate` names its
  output correctly for dotted stems, reports an encoding failure instead of
  crashing on a file it never wrote, falls back to a turntable for a
  structure-only file and lists every kind in its error. `capture` and `batch`
  open a volume with the same isovalue and colormap as the viewer, `batch`
  renders volumes with `capture`'s signed-lobe, periodic-aware path, and both
  read `size` and `representation` from the `[display]` config table.
  `compare` is `open --auto-compare` again, with `-s` and the recent-files
  list, and `quickstart` records its file there too. `batch-compare` reports a
  bad `-r` and a missing file as errors, `stats` prints `Total size:` with its
  space and counts unreadable files apart, and `h-add`/`supercell` say which
  sections and provenance they do not carry over. A manifest that is not a
  JSON object is a clean error for every command. Archives are closed by
  `show`, `table`, `batch`, `diff`, the file browser and the dashboard, a
  killed live-optimisation worker is reaped, and the HTTP no-cache patch is
  installed once. (#51)
- Every command now ends an unexpected error with one line, `Error: <type>:
  <message>`, and exit status 1 instead of a Python traceback; `info --json`
  reports it as `{"error": ...}` on stdout. `vibe-view --debug` or
  `VIBEVIEW_DEBUG=1` shows the full traceback instead; Ctrl+C, explicit exit
  codes, usage errors and a closed output pipe behave as before. Content that
  opened and then broke commands is now refused where the archive is read: a
  string with an unpaired UTF-16 surrogate escape (`"\ud800"`), which made
  `info`, `show`, `diff`, `stats`, `table` and the viewer commands fail to
  print (in `manifest.json` the open fails; in a JSON member it is a section
  error); a section of an unknown kind without `members`, which broke `merge`;
  an `atomic_number` of `2**63` or more, which broke `h-add`; a periodic cell
  whose length, volume or interplanar spacing overflows or underflows in
  double precision (lattice vectors of length 1e200, 1e-300 or the largest
  double) or whose atoms lie too many cells apart for the bond search, which
  broke `show`, `capture` and `animate`; and vibrations whose atom positions,
  frequencies or displacements do not have the shape the commands use, which
  broke `animate` and `table --kind vibrations`. (#51)
- The `open`, `quickstart` and `export` commands now close the files they open
  on every exit path. `export` reports an exporter or output-write failure
  (for example a missing output directory) as a clean error instead of a
  traceback. (#51)
- The `from-vq` command now closes the files it opens when the viewer exits,
  is interrupted or fails to start. `demo` closes its validation reader on
  every path. (#51)
- A malformed but parseable `manifest.json` can no longer escape the reader as
  a Python error such as `OverflowError` or `TypeError`. Every such manifest
  now fails the open as a QVF error: `validate` reports it as `INVALID`, and
  `info`, `table`, `export` and the other commands exit 1 with a message
  instead of a traceback. The two remaining cases were `"qvf_version": 1e400`
  and a critical section whose `kind` is a list. Any other error raised while
  the manifest is checked is now also reported as `ManifestValidationError`,
  or as `QVFOpenError` when the manifest cannot be read at all, with the
  original error chained. A manifest number that no double can hold (`NaN`,
  `Infinity`, `1e400`, or an integer beyond the double range) is refused at
  open with its location. Before, such a number broke `info`, `diff` and
  `stats` after the archive had opened. Opening an archive is also about 0.15
  s faster, because the manifest schema is checked once per process instead of
  on every open. The 256-level nesting bound and the number limit are
  documented in the QVF format page. (#51)
- A `.qvf` that cannot be opened — a manifest that is not UTF-8, a corrupt
  (CRC or deflate) manifest entry, a directory named `*.qvf`, or an unreadable
  file — is now reported as `QVFOpenError`, with no archive handle left open.
  `info`, `table`, `export`, `validate`, `capture`, `show` and `tui` exit 1
  with an error message instead of a traceback, `stats` counts such files as
  unreadable, and `from-vq`, `diff`, `open` and `batch-compare` close the
  files they had already opened, whatever the failure. (#51)
- Converter and exporter correctness follow-ups to the #40 audit. GRO atom
  names are read as force-field names, so a C-alpha is carbon, not calcium,
  and the triclinic box uses all nine GROMACS box values, with a zero box read
  as not periodic. PDB reads only the first `MODEL` and the first alternate
  location, and the wwPDB `CRYST1` placeholder of a non-crystallographic entry
  is no longer a 1 Å cell. CIF files are tokenized per CIF 1.1 (quoted values,
  wrapped loop rows, text fields, comments) and a bad site coordinate is an
  error. Gaussian inputs keep labelled atoms (`C1`, `6`, `C(Fragment=1)`,
  `C-CA-0.25`) and freeze codes, and Z-matrix input is refused rather than
  silently dropped. Point groups no longer depend on how the molecule is
  oriented. The PDF export's documentation matches its PDF output, its
  fallback draws bonds with the same colours and style, and the SVG, PDF, HTML
  and Blender exports draw bonds through periodic cell faces to the image they
  reach. (#53)
- The `all` extra now names its parts instead of repeating them: it pulls
  `jupyter` through the extra rather than spelling its packages, and psutil,
  which the About dialog uses for CPU and RAM figures, is declared in a new
  `sysinfo` extra that `all` includes. The installation guide lists every
  extra, including `capture`, `povray` and `blender`, which install nothing
  because the capability is core or the program is external. (#55)
- Reaction-path per-frame volumes now show both lobes of signed fields in the
  shared positive/negative palette; nonnegative densities retain their single
  green surface (#57).
- Opening a file that the viewer cannot load, from `vibe-view desktop FILE`,
  the desktop window or the browse page, no longer spins forever. The loading
  page now says the viewer stopped, with its exit status and error message,
  and the status poll no longer starts a new viewer process every half second.
  (#59)
- Damaged archives now report the underlying error (#60). `%vibeview
  --capture` names the read error of a damaged volume member instead of only
  the section. `vibe-view diff` and `vibeview.api.diff` say why the geometry
  RMSD was skipped, a structure that cannot be read or differing atom counts,
  through the new `geo_rmsd_skipped` field. `vibe-view diff --json` reports an
  archive it cannot open as JSON on stdout. The new
  `vibeview.capture.render_volume` raises `CaptureError` with the reason where
  `capture_volume` returns False.
- `vibe-view diff` and `vibeview.api.diff` of a structure with no atoms skip
  the geometry RMSD with the reason `no atoms (A: n, B: m)` instead of
  stopping with `AxisError` (#60). `vibe-view diff --json` reports every error
  as a `{"error": ...}` object on stdout, as `info --json` does, not only an
  archive it cannot open.
- `vibeview.api.get_volume` reports the value range of the field the viewer
  draws. A complex volume array, which QVF v1 does not allow, is reduced by
  the section's `component` label before its range is taken, instead of
  reporting the real part of NumPy's complex min and max. (#61)
- An archive whose structure would make the bond search allocate without limit
  is now a clear section error, "structure too dense or lattice inconsistent
  for bond search", reported when the structure is read (#62). This covers a
  supercell written with its unit-cell lattice and atoms packed closer than in
  any solid. Such archives used to hang `show`, `capture` or the browser, or
  use gigabytes until the process was killed. The search now bounds its image
  atoms, candidate pairs and bonds. `vibe-view show --info` and the terminal
  panes clip archive strings longer than 200 characters, so a
  1,000,000-character section id no longer keeps `--info` busy for over an
  hour.
- Root causes that reached the CLI's last-resort catch are fixed where they
  occur (#63). - `stats` counts a dangling `*.qvf` symlink as unreadable
  instead of stopping. - The reader refuses `atom_properties` arrays of
  different lengths, which broke `table --kind atom_properties`. - The reader
  refuses band eigenvalues that are not finite or span beyond what a plot can
  draw, which broke `capture` of a bands section, and a non-numeric or
  infinite `kpath.fermi`. - `supercell` refuses a structure with no periodic
  axis, replication along a non-periodic axis, a factor below 1, and a request
  whose cell would overflow. - `info` and error lines show control characters
  from an archive as `\xNN` escapes instead of sending them to the terminal. -
  `validate` checks that each binary member's bytes fill its declared `dtype`
  and `shape`. - Error messages longer than 2000 characters are clipped.

## [v2.19.0] - 2026-09-25 - *Phong's Pheasant*

This release consolidates the viewer's shared plumbing (#34): one QVF writer
for every import, one bond rule, one unit-cell module, shared command-line
options, headless scene setup, table styling, network helpers and worker
lifecycle. It also hardens QVF readers and importers against malformed input
and fixes periodic-surface, terminal-chart and archive-merge defects (#35 to
#40).

### Added

- Miller slabs now support general orientations of nonsingular cells,
  including (111), (110), and (210). The integer surface basis retains the
  parent cell volume and handedness, and each requested layer repeats its
  contents along the normal. Atoms remain in the parent Cartesian frame;
  vacuum adds twice its value to the cell length without centering the atoms.
  This shipped in v2.18.1 without a changelog entry and is recorded here.

### Changed

- Every archive vibe-view writes (the XYZ, CIF, PDB, GRO, Gaussian, Mol2,
  SDF, cube, vibe-qc input, ASE and TREXIO imports, the molecule builder,
  and the `h-add` and `supercell` commands) now comes from one writer with
  one member layout (`sections/<id>/<member>.json|.bin`), a sha256 per
  member, deflate compression and `source` naming the program and version
  that actually wrote it (vibe-view, not vibe-qc or ASE). `dimensionality`
  is derived from `pbc`, so an ASE import of a molecule in a box is no
  longer flagged periodic, and Mol2/SDF bonds are written once, as the
  structure's `bonds` member, instead of twice. (#34, #40)
- Scientific conversions now share the established CODATA 2018 factors.
  CLI, SDK and batch energy differences use full-precision eV and kcal/mol
  factors; live optimization uses the reciprocal of the same bohr/Å factor
  as geometry import and rendering. (#34, #36, #38, #40)
- One bond rule everywhere. The browser edit overlay and measurement panel,
  the terminal, the hydrogen-adding tool and the CML, SVG, PDF, POV-Ray,
  Blender and HTML exports all use the viewer's covalent-radius inference
  (Cordero radii plus 0.45 Å, periodic images, no metal-metal bonds in ionic
  solids) and honour an archive's explicit bonds. Exports previously used
  their own 1.2× rules with ten-element radius tables, so heavy atoms were
  unbonded and a molecule's connectivity differed between outputs. CML now
  writes explicit bond orders, and POV-Ray draws bonds across cell faces.
  (#34, #40)
- Unit-cell geometry is shared by every front end: the browser, terminal, POV-
  Ray export and reaction-path viewer enumerate cell edges, supercell
  translations and periodic bond images through one module, and the browser
  draws the cell wireframe as one actor instead of one per edge. (#34, #36,
  #38)
- The command line's shared options come from one module: `--port`, `--host`,
  `--no-browser` and `--log-file` are declared once for every server command,
  `--size` is one `WxH` type with one error message, and `--json`, `--output`,
  `--isovalue`, `--colormap` and `--fps` are declared once. `config.toml`'s
  `[server]` defaults now apply to `compare`, `serve`, `from-vq` and `demo
  --open` as the manual says, not only to `open`. Commands that transform one
  archive (`capture`, `animate`, `export`, `slice`, `h-add`, `supercell`)
  write beside the input by default, and `--help` says so; commands that
  gather several inputs (`batch`, `merge`, `dashboard`, `import`) keep writing
  in the working directory, `merge` now as `<first stem>_merged.qvf`. `demo
  --open` gains `--host`. (#34, #39)
- Untyped producer fields in bond-order tables, QTAIM critical points and the
  terminal's SCF plots and tables go through one shared guard, so a NumPy
  scalar or a numeric string reads as a number in every panel instead of only
  some. (#34, #36)
- File and section sizes are formatted by one shared helper everywhere: whole
  bytes below 1 KB, then one decimal per binary step up to TB. The run-record
  panel no longer stops at MB, and the browser memory panel shows the same
  units as `vibe-view info`. (#34)
- Every headless render shares one scene setup: the dark vibe-view background,
  an isometric camera fitted to the actors, and one screenshot path. Dashboard
  and Jupyter thumbnails therefore now match the framing and theme of `vibe-
  view capture` and `batch` output instead of a white, unfitted view, and
  `export obj`/`gltf` builds its mesh on the same scene. (#34)
- The details panel's table views (charges, bond orders, QTAIM critical
  points, job specification, run record, NMR, EPR, symmetry and citations)
  share one stylesheet and document wrapper, so they read alike, and every
  cell is escaped, so a producer string containing `<` no longer breaks a
  table. (#34, #36)
- Matplotlib figures (band structure, spectra, scan surfaces and the reaction-
  path and trajectory energy plots) are rasterised by one shared helper at 120
  dpi; the energy plots and scan surfaces were 100 dpi. The Agg backend is
  chosen on first use instead of when a renderer is imported, so importing
  vibe-view no longer reconfigures matplotlib for a notebook or GUI process.
  (#34, #36)
- Port probing, the wait-for-bind loop and the free-port search live in one
  module used by the command line, the programmatic launcher and the file
  browser. `vibe-view from-vq` and `launch_qvf` now start their ready watcher
  after the viewer is built, as `open` already did, so a slow first import can
  no longer time out the "ready" message and skip opening the browser. (#34,
  #39)
- The re-localization and live-optimization workers share one subprocess
  lifecycle: one strict event parser for request/response exchanges, one
  lenient filter for streamed steps, bounded output, and a child that is
  always killed and waited for on timeout or cancellation. The file browser
  now waits for the viewer processes it terminates at shutdown instead of
  leaving them to the operating system. (#34, #39)

### Fixed

- The browser viewer's auto-saved session for a viewer opened without a
  file honours `$XDG_CACHE_HOME` like every other cache file; all cache
  and configuration paths now come from one module. (#34)
- Browser and CLI CIF exports use Cartesian Å coordinates for molecules,
  including nonperiodic boxes, and fractional coordinates only with a cell. (#40)
- Terminal trajectory, reaction-path and vibration frames use their own atom
  identities and coordinates, including archives without a primary structure.
  Reaction cells follow the selected frame instead of borrowing an unrelated
  structure cell, and scene caches distinguish section and frame context. (#38)
- Terminal spectrum envelopes use the same native-unit half-width and sampling
  as the browser, preserving signed peaks without extra span-based broadening. (#38)
- XYZ imports read exactly the first declared frame instead of combining
  later frames or silently skipping malformed atoms. Invalid counts, truncated
  frames and non-finite coordinates fail before writing an archive. (#40)
- QVF grids reject dimensions that disagree with their binary fields, preventing
  incorrectly positioned surfaces. Trajectory and reaction readers validate
  atom/frame coordinates, finite energies and waypoint indices before rendering,
  with errors attributed to the owning section. Empty grids and trajectories,
  negative/skew grids and partial energy lists remain supported. (#35)
- Terminal equation-of-state charts draw the producer’s fitted curve using the
  shared browser evaluator, rather than connecting the computed points. (#38)
- Mol2 and SDF imports reject malformed atom and bond records before writing,
  preventing shifted or dangling connectivity. Mol2 bonds now resolve arbitrary
  source atom IDs, comments and blank lines remain valid within sections, and
  multi-molecule files consistently import only the first molecule. (#40)
- Periodic volume surfaces retain exact lattice translations for skewed grids
  and fractional voxel periods. Exact scalar tiling preserves original samples;
  incompatible grids use centred surface copies without fabricated gap values
  or overwritten overlaps. Only periodic axes repeat. (#36)
- Orbital playback stops after the final orbital instead of re-evaluating it
  forever. Scheduled frames render on the browser server's event loop and
  send their state updates immediately. (#37)
- Cube imports with multiple datasets select the first complete scalar field
  from interleaved samples. Invalid dataset counts, incomplete orbital ID
  records and truncated data now raise clear errors. (#40)
- Python imports reject an unresolved periodic lattice instead of inventing
  a 0.529 Å cubic cell. The static parser keeps partial atom and parameter
  information without executing the input. (#40)
- Cube imports accept the negative-count ångström extension, converting grid
  coordinates and atoms consistently. Zero counts and ambiguous mixed signs
  now raise clear errors. Scalar samples retain their order while binary
  encoding avoids creating a Python argument for every voxel. (#40)
- Malformed structure coordinates, periodicity and connectivity now raise
  recorded QVF errors before rendering. Bond indices and present orders no
  longer silently coerce into different atoms or default values. Legacy
  dimensionality-only structures and omitted explicit orders remain supported.
  (#35)
- NCI surfaces preserve skewed grid geometry and requested periodic replicas
  when a density field supplies their colours. Scalar colours follow the same
  grid orientation and interpolation as the surface. Density on a different
  grid leaves the surface uncoloured with a mismatch message. (#36)
- Python API operations close owned readers when a second input cannot open or
  a lazy import fails. API calls and the launcher consistently preserve readers
  and binary streams supplied by the caller. (#39)
- Archive merges preserve source-local reaction, difference-volume and basis
  references when section IDs collide, retain metadata, and verify all member
  hashes before atomically replacing the destination. Generated IDs and member
  paths cannot shadow existing entries; merging onto an input is safe. Slices
  also refuse dangling basis references, and both transforms write current QVF
  version 1 when accepting legacy version-2 inputs. (#39)
- Signed volume surfaces now render grids with reversed axes in the browser
  and headless captures, preserving their physical coordinates and lobe colours.
  Both paths use the shared contour builder. (#39)
- Terminal DOS charts preserve the already Fermi-relative energy grid,
  regardless of absent, zero or out-of-range absolute Fermi metadata.
  Projected channels use the same labels and spin summation as browser
  charts. (#38)
- QVF member failures are recorded for every section kind and reported with
  their actual category. Corrupt phonon DOS metadata and bond-order data now
  raise reader errors instead of silently falling back to defaults. Malformed
  manifest sections raise a QVF validation error; ZIP member corruption is
  reported as a member error after the archive has opened. (#35)
- Python-input imports and symbol-only structure rendering now recognize
  elements through curium consistently, including Pt, Au and U. Shared
  element data preserves existing viewer colors, color overrides and radius
  defaults. (#34, #40)
- Correct duplicate and incomplete release records, align citation/version
  checks, and refresh the roadmap and released codename status. (#41)
- Equal-energy CLI comparisons now report zero in every unit and in text.
  File-browser listings and detail pages display a legitimate zero energy
  instead of marking it missing. (#39)
- Volume isovalue, colormap and opacity edits no longer change the defaults
  of other sections or subsequently opened files. Partial producer hints also
  retain the original kind defaults for settings they do not specify. (#36)

## [v2.18.1] - 2026-09-22 - *Richardson's Robin*

This patch consolidates the lint and strict type gates, documentation fixes,
and the stable real-UHF producer/consumer gradient fixture.

### Fixed

- Every source module is now in the strict mypy gate. `app.py`, the browser
  viewer, is annotated: its trame controllers keep their real signatures,
  the pyvista `Plotter` methods whose stubs mypy misreads go through
  `plotter_compat`, and `disallow_untyped_decorators` is relaxed for that one
  module because trame ships no types. The `types` job covers the whole
  tree; `tests/test_type_ramp.py` fails when a new source module is added
  without joining the gate. (#33)
- The mypy ramp grows from 80 to 90 modules: the QVF reader, the structure
  renderer, the wavefunction renderer, the converters, the CLI, the
  re-localization backend and the whole terminal viewer now pass full strict
  and stay gated, leaving only `app.py` deferred. Two real defects came out
  of it: the terminal viewer guarded its status updates with Textual's
  `App.is_mounted`, a method that is always truthy, and now uses
  `is_running`; and every bond-typed path declared bonds as 3-tuples while
  the reader has always emitted `(i, j, order, image)`, which the new
  `qvf.Bond` alias names. (#33)
- The mypy ramp grows from 75 to 80 modules: batch rendering, headless
  capture, video export and the launcher now pass full strict and stay gated,
  through a small `plotter_compat` module that carries the one `type: ignore`
  each for the pyvista 0.49 `Plotter` methods mypy misreads. The structure
  renderer's cached bond list is typed with the bond order it always carried.
  (#33)
- The mypy ramp grows from 71 to 75 modules: the directory server, the
  terminal cell encoder, the QTAIM renderer and the ray-trace engine now pass
  full strict and stay gated. The QTAIM renderer reads its untyped
  critical-point fields through guards, so a malformed field skips the point
  instead of raising mid-render. (#33)
- The mypy ramp grows from 65 to 71 modules: the Jupyter integration, lazy
  loader, live optimization, and the bond-order, reaction-path and
  equation-of-state renderers now pass full strict and stay gated. The
  bond-order table reads its untyped pair fields through guards instead of
  bare `float()` casts. (#33)
- The mypy ramp grows from 61 to 65 modules: the Python API, the profiler,
  the terminal rasterizer and the volume renderer now pass full strict and
  stay gated. (#8)
- The mypy ramp grows from 50 to 58 modules: the build tools, user
  configuration, dashboard, PDF and SVG exports, input-file parser, COOP/COHP
  renderer and TREXIO importer now pass full strict and stay gated. The
  configuration reader drops its `tomli` fallback, which Python 3.11, the
  support floor, made unreachable. (#8)
- The `lint` CI job runs `ruff check .` over the whole checkout instead of
  `tests/ src/ scripts/`. The narrower scope left `electron/desktop-launcher.py`,
  the `docs/*.py` capture scripts and the privacy hook's tests unlinted, and
  the nine findings they carried are cleared. The mypy ramp grows from 41 to
  50 modules: the material presets, the energy window, the Fermi-surface,
  NCI, phonon, SCF-history and spectra renderers, the Gamma-point Bloch sum
  and the one-shot terminal renderer now pass full strict and stay gated.
  (#8)
- Keep documentation deployment manual for every web-triggered pipeline,
  including pipelines on `release`, using product-local rules over the
  configured deployment job. Ordinary release pushes still publish. (#22)
- Build the documentation for merge requests changing package/toolchain
  metadata or the API modules and schemas imported by the site, while
  retaining the filter for unrelated viewer changes. (#30)

### Changed

- Rework the first-run guide and add a complete import, inspect and share
  tutorial using bundled inputs. Clarify section IDs, missing data, capture
  limits, terminal plotting and reproducible figure exports; refresh actual
  viewer captures without changing codename artwork. (#20)

### Added

- **ruff and mypy are enforced.** Both were configured in `pyproject.toml`
  before the split and run by nothing, so the tree had drifted to 325 ruff
  findings. Some were inert style, but the classes the issue names hide real
  defects — 70 unused imports, 13 assigned-but-never-used locals, 26 `zip()`
  calls with no `strict=`, 41 files opened without a context manager, and
  three annotations naming a type the module never imports. All of them are
  gone, each decided rather than batch-annotated: the `zip()` calls are
  `strict=True` where the lengths are fixed by construction and `strict=False`
  with a stated reason where they are not. Two deliberate suppressions remain,
  both with their reason on the line — `E741` where `l` is the
  angular-momentum quantum number (also a `BasisShell` field name), `h, k, l`
  the Miller indices or `I` the inertia tensor, and one `SIM117` in the
  viewer's Vuetify layout. A `lint` CI job now runs `ruff check tests/ src/
  scripts/` on every merge request, so a new finding fails the pipeline.

  mypy gets the per-module ramp the issue asked for rather than a
  whitewash. `strict = true` over the whole tree is about 4300 findings across
  170 files, `app.py` alone holding 478, so `[tool.mypy].files` names the 41
  modules that are clean today and the `types` job checks exactly those under
  full strict. Everything else is still analysed for the types it provides but
  its own findings are not reported, and the list only ever grows. Getting
  there also fixed the two things that stopped mypy running at all: the
  `python_version` floor of 3.11 made numpy's own stubs a syntax error that
  aborted the run before any of our code was reached, and ~78 unstubbed
  third-party and companion imports drowned everything else. Both CI jobs
  share `.rules_product_gate` with `test` and `qvf-conformance`, so a ref gets
  the whole gate or none of it, and `tests/test_ci_rules.py` checks the
  widened matrix, that the jobs really invoke the tools, and that the ramp
  names files that exist. (#8)

## [v2.18.0] - 2026-09-17 - *Richardson's Robin*

Periodic band-structure and DOS charts now open on the states around E_F.
An all-electron archive puts its core shells hundreds or thousands of eV
below the valence region -- silicon at STO-3G places its 1s at -2431 eV --
and an axis autoscaled over all of them left the bands unreadable without a
manual zoom. Pseudopotential archives, which have no core states, keep the
axis they had. The Sphinx toolchain is now a declared `docs` extra instead
of ten pins inlined in CI, so the site can be built from package metadata.
Setup scripts run on stock macOS Bash 3.2, and a release push no longer
repeats the gate its candidate already passed.

### Added

- **An energy window for the band-structure and DOS charts.** All-electron
  periodic archives carry core states hundreds or thousands of eV below E_F —
  silicon at STO-3G puts its 1s at -2431 eV — and an axis autoscaled over all
  of them squeezed the valence and conduction bands into a few pixels around
  the E_F line, so the chart could not be read without a manual drag-zoom. The
  bands, DOS and combined panels now open on the states grouped around E_F,
  with the core shells off the axis, and carry **E − E_F min** / **E − E_F
  max** fields and a **Reset window** button. A spectrum with no core states is
  not windowed at all, so pseudopotential archives keep the axis they had. A
  producer can ship its own first view with an `energy_window` hint in
  `viewer_defaults`, and `render_to_html` / `render_to_bytes` take
  `energy_window=` and `auto_window=False` for scripted figures. (#26)

- **A `docs` extra** declaring the Sphinx toolchain. `.gitlab-ci.yml`'s
  `.docs_deps` pinned all of it inline, so the requirement appeared in no
  package metadata and the only way to render the site locally was to read the
  CI file and retype ten pins — `docs/Makefile` and `scripts/build_site.sh`
  both assumed `sphinx-build` was already on `PATH` and said nothing about how
  it got there. Four of those pins (`numpy`, `click`, `pydantic`, `jsonschema`)
  were already `[project.dependencies]`, repeated only because the docs job did
  not install the package, and would have kept an old floor after a core pin
  moved. CI, `CONTRIBUTING.md`, `README.md`, `AGENTS.md`, the Makefile and the
  site builder now all name `pip install -e '.[docs]'`, and
  `tests/test_docs_toolchain.py` keeps the extra in step with `docs/conf.py`'s
  extensions, with the core dependency list and with the CI job. (#19)

- Biomolecule residue isolation, hiding and viewport selection, with chain,
  secondary-structure, residue-type and B-factor colours in atom and bond views.
- PDB HELIX/SHEET annotations are retained. Optional secondary-structure subtype
  metadata distinguishes alpha, pi and 3₁₀ helices and beta bridges in ribbons.
- Optional dashed hydrogen-bond contact display using explicit hydrogens and
  documented geometric criteria in the input cell.

### Fixed

- The setup scripts no longer abort on stock macOS Bash 3.2, which rejects
  an empty `"${array[@]}"` as an unbound variable under `set -u`. This made
  `uninstall.sh` fail outright on macOS. (#29)

- **A release push no longer repeats the gate the candidate already passed.**
  `test` and `qvf-conformance` carried no `rules:` of their own, so they fell
  through to the `workflow:` admission list and ran on every ref in it. After
  v2.16.2 that was visible: the candidate pipeline proved the exact commit,
  then fast-forwarding `release` onto that same SHA started the identical two
  jobs again against an identical tree, and preparing `main` needed a `ci.skip`
  push option to get out of the way of a third copy. Both jobs now name their
  refs — merge requests, `release-candidate/*` and manual `web` runs — which
  is the delivery model `docs/roadmap.md` already described: work lands on
  `main`, a candidate pipeline proves it, and `release` only fast-forwards.
  `docs-build` still runs on `main` and `release`, because the site artifact
  and the deployment jobs need it. The matrix is a table at the top of
  `.gitlab-ci.yml` and in `CONTRIBUTING.md`, and `tests/test_ci_rules.py`
  evaluates the file against it, so a job that loses its rules fails the suite
  instead of silently inheriting every ref again. (#22)

- Restore **Re-localize with** using a separately installed vibe-qc worker.
  A persistent backend Python setting, native capability probe and archive
  checks enable compatible molecular methods. Exact archived occupied orbitals
  are preserved; new RHF is an explicit option. Validated session overlays
  include localization descriptors and IAO charges, with cancellation and
  stale-result protection. Periodic, complex and incomplete inputs explain
  why localization is unavailable.
- Strands have crisp edges, correctly shaded end caps and complete terminal
  residue assignments. Hidden ribbon spans are never connected across gaps.
- QVF slicing preserves root, section and member metadata, prunes removed
  viewer hints, refuses dangling section references, and validates before
  replacing the output. (#24)
- DOS energy grids retain their specified Fermi-relative values; combined
  bands/DOS plots subtract only the bands' own Fermi reference, with separate
  axes when that reference is absent. (#25)
- Scene rebuilds and file reloads preserve the light-background choice. (#27)
- Headless exports do not create an unawaited reset coroutine. (#21)

### Changed

- Installation, quickstart and desktop tutorials now begin with exact clone
  commands for the canonical repository or the public GitHub mirror.
  README and contributor setup use the same options. (#20)

- Clone, install and desktop help now use the standalone repository root;
  wheel staging stays inside the viewer checkout and repair URLs use its
  own documentation subtree. Installation pages identify the GitHub mirror
  and distinguish available source/CI downloads from unpublished website
  wheels. (#20)

- Desktop builds now retain artifacts locally; operators publish feeds with separate private deployment tooling.

- Public source is usable without private URL rewriting or omitted agent guides.
  Site CI settings and release coordination stay outside the product repository.
- Installer launcher records use external private state with verified migration
  from the legacy checkout record; ordinary virtualenv locations are unchanged.

- Site-specific provisioning scripts, deployment configuration and operator
  records move to private operations storage. Portable product installers remain
  in source; private helpers are installed independently. Contributor privacy
  checks support an external private terms file without publishing its contents.

- Use the publication repository in package/citation metadata and the desktop
  issue link. Strengthen contributor privacy checks and redact their diagnostics.

## [v2.17.1] - 2026-09-15 - *Lorensen's Loon*

Correctness fixes for complex and periodic molecular orbitals and browser
scene replacement. Inherits v2.17.0's codename.

### Fixed

- Escape closes the command palette and returns focus to its launching
  control during presentation mode, as it does in the normal viewer.
- Scene rebuilds retain the previous VTK objects until the replacement is
  sent, preventing reused object identifiers from freezing the browser or
  desktop viewport after periodic replication or section changes.
- QVF complex split-axis orbital coefficients decode without dropping the
  imaginary component. The orbital panel offers real, imaginary and magnitude
  surfaces. Explicit Gamma-point orbitals in 3D cells include lattice-image
  tails and preserve skew cells; unsupported Bloch cases report why.
- Periodic/complex re-localization now explains the molecular worker's limit
  before probing vibe-qc. Single-k-point blocks cannot be mistaken for a full
  periodic density or its derived fields.

## [v2.17.0] - 2026-09-13 - *Lorensen's Loon*

TREXIO geometry and molecular orbitals can now be opened directly, bringing
another wavefunction format into the viewer's isosurface workflow. QVF
remains the native archive format. Periodic and complex orbital rendering
and correlated CI/RDM visualization are outside this release's scope.

### Added

- TREXIO import through the optional `[trexio]` extra (included in `[all]`):
  HDF5 files and text-backend directories open directly or convert to QVF.
  Geometry, cells and real molecular Gaussian s/p/d/f orbitals retain their
  units, normalization, AO ordering and available spin/energy/occupation
  metadata. Browser uploads accept HDF5; directory scans treat text datasets
  as one input. Unsupported wavefunctions produce explicit errors.

- Packaging checks now guard the viewer's own distribution metadata, CLI entry
  point, and core/browser dependency split without requiring a sibling checkout.
  (#23)
- **A roadmap aligned with the release series**, `docs/roadmap.md`. It records
  what each tag shipped, including the parity work already in v2.15.2, and
  records correctness follow-ups, scope for each registered codename, and the tracks
  no single minor owns. The Avogadro parity roadmap is frozen as the record of
  pre-split work, and `ROADMAP_V2.md` points at the new page.

### Changed

- Agent guides now describe the standalone viewer's development and release
  workflow, replacing inherited monorepo guidance and stale code references.

## [v2.16.2] - 2026-09-12 - *Sayle's Starling*

A test and documentation patch. Inherits v2.16.0's codename; viewer runtime
behavior is unchanged from v2.16.1.

### Fixed

- Missing-RDKit tests now force the absent-dependency branch even when RDKit
  is installed, and check the installation hint without depending on the
  checkout directory's name.
- The patch-release checklist now includes the Electron package version and
  both lockfile versions, matching the full release procedure.

### Changed

- Contributor guidance explains how to exercise missing optional dependencies
  in every environment and distinguish safe dependency gates from tests that
  hide when their dependency is installed.
- The product handover records current release evidence and the differences
  between local tests and the release gate, including editable-install and
  release-tooling checks.

## [v2.16.1] - 2026-09-10 - *Sayle's Starling*

A one-fix patch. Inherits v2.16.0's codename, as every patch does.

### Fixed

- **A stubbed `vibeqc` in `sys.modules` crashed the browser viewer instead of
  disabling container submission.** `_vibeqc_importable()` called
  `importlib.util.find_spec("vibeqc")` unguarded, and `find_spec` is not
  total: it raises `ValueError` for a `sys.modules` entry whose `__spec__` is
  `None` — which is exactly what a bare `types.ModuleType("vibeqc")` is — and
  propagates whatever a meta-path finder raises. Because `create_app` calls
  the probe twice, any stub in the process took app construction down with
  `vibeqc.__spec__ is None` rather than degrading to "no container
  submission". The probe now answers the question the submit path actually
  asks — will `from vibeqc import ...` work — and treats a refusing finder as
  "not available". Found by the vibe-qc release chat while independently
  measuring the producer/consumer contract. (#18)

## [v2.16.0] - 2026-09-09 - *Sayle's Starling*

The release the codename was chosen for: vibe-view as a product you can
adopt on its own. The documentation site gains its whole content — the
product manual, real viewer figures, a visual identity — and the release
series gains its artwork. Roger Sayle's RasMol (1992) was the first
molecular viewer a scientist could simply install and run, without the
program that produced the data.

### Added

- **Real viewer figures throughout the manual:** the landing page and
  quickstart show the standalone demo, and capabilities show structure,
  orbitals, density, bands/DOS, spectra, vibrations and QTAIM. Browser and
  terminal captures are regenerated from this checkout; captions identify
  illustrative panel data. Capture tools now resolve the standalone tree
  and validate sanitized showcase inputs. (#20)

- **The full seven-image codename series**, with both light-studio and dark
  cinematic treatments. Lorensen's Loon, Richardson's Robin, Phong's
  Pheasant, Levoy's Lemur and Levinthal's Lynx illustrate the five future
  names approved and registered separately in `eb20af1`; registration does
  not announce a release date. (#20)

- **A visual identity for the documentation:** theme-specific SVG wordmarks,
  an orbital-eye favicon and a social card, hand-authored to match the
  vibe-qc family. Link previews use the 1200 × 630 card. (#20)

- **Five more release codenames**, approved by the maintainer and registered
  in `RELEASE_CODENAMES`: v2.17.0 *Lorensen's Loon* (marching cubes, VTK),
  v2.18.0 *Richardson's Robin* (the ribbon diagram), v2.19.0 *Phong's
  Pheasant* (the reflection model), v2.20.0 *Levoy's Lemur* (direct volume
  rendering) and v3.0.0 *Levinthal's Lynx* (the first interactive molecular
  graphics system). Registering a name resolves that version whenever it is
  cut; it schedules nothing. (#13)

- **Release artwork** for Roothaan's Roadrunner and Sayle's Starling, in the
  shared 1672 × 941 light-studio treatment, embedded in the codename
  catalogue. Starling is numbered 02 in vibe-view's own series. (#20)

- **The manual.** The documentation site had eight user pages and pointed
  at vibe-qc's documentation for everything else, where the viewer's user
  guide still lived, written for the monorepo layout. It now carries the
  whole product manual: a panel-by-panel tour, the browser viewer and its
  editor, terminal mode with its key map and figures, the desktop app and
  its setup screen, headless figures, exports and the capture and animation
  APIs, input formats with the importer-plugin contract, biomolecules, and
  jobs and live results (the vq Job Manager, QVF containers, live reload
  and streaming checkpoints). The CLI, Python SDK and troubleshooting pages
  were extended to match, and `vibe-view examples` points at the site
  instead of a monorepo path. Every claim was checked against the code at
  the time of writing; the strict Sphinx build stays at zero warnings.

- **A `release` extra** declaring `build` and `twine`. They were pinned inline
  in `.gitlab-ci.yml` and nowhere else, so `scripts/build_release_artifacts.py`
  had a build requirement that appeared in no package metadata: a developer
  following `CONTRIBUTING.md` into a `[test]` venv hit a missing tool nothing
  declared. The script's tool list is now the module-level
  `RELEASE_BUILD_TOOLS`, the extra is asserted to match it in both directions,
  and its missing-tool error points at `pip install -e '.[release]'` instead of
  a bare `pip install build twine`. CI installs `.[test,release]`. (#9)

### Fixed

- **Spectrum controls leave room for the chart.** Their rows no longer grow
  to fill the results panel and push the plot axis outside the visible
  frame. Found during real documentation captures. (#20)

- **Selecting QTAIM now renders its critical points, bond paths and scalar
  table.** The sidebar dispatch omitted this kind even though auto-open
  supported it; clicking its row previously left an empty panel. QTAIM
  overlays are removed when switching to another section. Found while
  recapturing the manual. (#20)

- **`vibeview[viewer]` resolved to a broken dependency set the day trame 4.0.0
  was published.** The extra pinned `trame>=3.6` with no upper bound, while
  every release of trame-vtk and trame-vuetify requires `trame-client<4`; pip
  therefore took trame 4.0.0 and backtracked the other two to trame-vtk 2.8.13
  and trame-vuetify 2.7.2, the last releases without that requirement. The
  first carries the VTK 9.7 unhashable-array serializer the split audit had
  already worked around (#6), the second renders boolean props differently,
  and `test_ambient_occlusion_is_server_side_only` and the presentation-mode
  template test went red on a pipeline whose only change was documentation.
  The extra now reads `trame>=3.6,<4`, `trame-vtk>=2.11.4` (the first
  release that collects arrays into a list) and `trame-vuetify>=3.2`. Lift
  the bound once both publish trame-4 releases.

The next minor is v2.16.0 *Sayle's Starling* — see `docs/codenames.md` for the
pool and the policy.

## [v2.15.3] - 2026-09-09 - *Roothaan's Roadrunner*

First gated tag of the split-out repository. `v2.15.2` sits on the seed
commit, from before this repository had CI, so no pipeline ever ran at that
SHA and the fleet's release report rejects it as a pin. This tag is proved by
a `release-candidate/*` pipeline and publishes the documentation site.

### Fixed

- **The QVF schema drift guard now fails, instead of skipping forever.**
  `tests/test_qvf_schema_identity.py` compared the vendored schema against a
  path inside the pre-split monorepo. In that layout the viewer's file *was*
  that path (a symlink), so the test hashed a symlink against its own target;
  afterwards the path could not resolve and the test skipped. Both vendored
  schemas are now pinned by sha256 in `scripts/check_qvf_conformance.py` and
  checked offline, in every lane. (#1)
- **`src/vibeview/schema_v2.json` was governed by nothing at all.** The
  `qvf-conformance` job compared only the v1 schema, so a corrupted v2 — which
  `vibeview.qvf` validates `qvf_version: 2` manifests against — passed green.
  It is pinned now, and the job compares a normative v2 as soon as the `qvf`
  repository publishes the frozen artifact its registry says is retained. (#1)
- **Three tests could not run in any environment.** The panel-only hot-reload
  regression guarded on an archive that is gitignored inside vibe-qc too; a
  website test asserted on vibe-qc's `build_site.sh` through a path that never
  resolved. Rebuilt on the synthesized `showcase_qvf` fixture and removed
  respectively, plus three defensive skips over this suite's own fixtures
  turned into assertions. Local skips: 41 -> 34. (#3)
- **`vibe-view[queue]` named a distribution that does not exist.** The extra
  required `vibe-queue`, which is the monorepo *directory* name; vibe-queue
  ships as `vq`. `pip install vibeview[queue]` therefore failed to resolve even
  with a vibe-queue checkout installed. (#5)
- All seven lazy `from vq.*` sites now tell the user how to install the extra.
  The queue-overview strip previously swallowed `ImportError` in a bare
  `except Exception` and went blank, indistinguishable from a dead daemon, and
  `vibe-view from-vq` printed `pip install -e vibe-queue/` — a
  monorepo-relative path. (#5)
- `vibe-view doctor` reports the `queue` capability. It was the only declared
  extra with no capability row. Its remediation for the desktop app also
  pointed at `./vibe-view/scripts/install.sh`, the pre-split path. (#5)
- `test_ambient_occlusion_is_server_side_only` failed on VTK 9.7 with
  `TypeError: unhashable type: 'VTKAOSArray_vtkFloatArray'`. It serialized
  through VTK's bundled `render_window_serializer`, whose
  `extractRequiredFields` collects arrays in a `set()`; VTK 9.7 gave
  `vtkDataArray` an `__eq__` without a `__hash__`. The viewer was never
  affected — `VtkLocalView` serializes through trame-vtk, which already
  collects into a list — so the test now uses the path the client actually
  gets. (#6)
- `[project.urls]` pointed `Repository` and `Issues` at `the archived monorepo`, the
  frozen archive with no open issues. (#5)
- README install instructions cloned the monorepo and ran
  `./vibe-view/scripts/install.sh`; every lifecycle path, the `../docs/...`
  cross-repo links and the License link were monorepo-shaped.

### Added

- **A documentation site.** Sphinx + furo, shaped like vibe-qc's so the two
  read as one project, published at <https://vibe-qc.com/vibe-view/docs/>.
  `docs/` had no generator and no entry point; a user who installed the viewer
  had the README and nothing else. Install, quickstart, capabilities, CLI,
  Python SDK, the QVF format, the `[queue]` extra and troubleshooting are
  written for a user; the audits, design notes and the Avogadro-parity roadmap
  moved behind `internal.md`, which says they are not user documentation.
  Nothing was renamed, so every citation from the root ledgers still resolves.
  (#11)
- **`docs-build` and `docs-deploy` CI.** `docs-build` renders the site on
  every ref that makes a pipeline; `docs-deploy` rsyncs it, `--delete-after`,
  into the dedicated documentation subtree and nothing wider. External
  deployment configuration pins the independently verified host keys. Two guards project 34
  does not have: one names an empty `DEPLOY_SSH_KEY_B64` and the protected-ref
  rule behind it (the failure that broke vibe-qc's first `docs-deploy` with
  `error in libcrypto`), one rejects a key that decoded to something that is
  not a key. (#12)
- The README has a **Documentation** section, and every placeholder link in it
  now points at a real page instead of vibe-qc's homepage — the QVF format,
  the quickstart, the queue guide and the terminal-mode guide were all
  `https://vibe-qc.com`. `[project.urls] Documentation` pointed there too.
  (#11)
- **A `release` branch and a written release procedure.** Project 35 had only
  `main` and no documented cut. `release` now exists at `v2.15.2` — the
  fast-forward base — and is protected; `docs/release_process.md` covers the
  branch model, the coupled version-bump edits, the `release-candidate/*`
  pipeline that must be green before a tag is spent, tagger identity, and what
  a deploy may and may not touch. (#14)
- **A release codename catalogue**, `vibeview.codenames`. Version to name, with
  patch releases inheriting the parent minor and PEP-440 dev/rc suffixes
  stripped — the same contract vibe-qc's `RELEASE_CODENAMES` has.
  `tests/test_release_codenames.py` holds every surface to it. (#13)
- **v2.16.0 is *Sayle's Starling***, the first name from vibe-view's own
  codename pool — visualization, computer graphics and crystallographic
  imaging — approved by the maintainer on 2026-09-09 and registered ahead of
  the cut so the artwork brief could be written against it. Roger Sayle's
  RasMol (1992) was the first molecular viewer a scientist could simply
  install and run, free, without the program that produced the data; that is
  what this release is. `docs/codenames.md` carries the pool and the policy,
  the private operations repository preserves the artwork brief. (#13)
- `VIBEVIEW_REQUIRE_QVF_CORPUS=1` in the CI `test` job: in a lane that clones
  the QVF conformance corpus on purpose, a missing corpus is now an error
  rather than a skip. `tests/test_qvf_corpus_guard.py` covers the guard. (#3)
- `tests/_qvf_corpus.py` replaces two duplicated copies of the corpus probe and
  also finds the per-chat clone layout, so the corpus tests run on a developer
  machine and not only in CI. (#3)
- `tests/test_queue_extra.py` — the `[queue]` metadata, that all seven vq
  import sites still catch `ImportError`, and that the remediation names both
  halves of the install. (#5)
- `.githooks/pre-commit` refuses staged additions containing absolute home
  paths or the maintainer's employer name, with
  `tests/test_no_maintainer_paths.py` as the always-running half. Enable it
  with `git config --local core.hooksPath .githooks`.
- `CONTRIBUTING.md`, `SECURITY.md` and this `CHANGELOG.md`; `CITATION.cff`
  gained `repository-code`.

### Changed

- **The release codename is resolved, not pasted.** It was hardcoded in four
  surfaces — `__init__.py`'s version comment, `vibe-view --version`, the
  browser About box and the Electron About box — with a fifth copy asserted in
  the test suite, and nothing coupling them. All four now read the catalogue.
  `vibe-view desktop` puts the codename in the launcher handshake config, so
  even `electron/main.js` reports what Python says; its `FALLBACK_CODENAME`
  covers a double-click launch and is the only literal left, pinned to the
  catalogue by test. The `## [v2.15.2]` header carries the codename too. (#13)
- A real `.gitignore`. 100 of 373 tracked files were build output — including
  a stale full copy of the package under `build/lib/vibeview/` — and
  `scripts/build.sh` deletes exactly those directories, so the repository's own
  build removed 92 tracked files. Untracked, not deleted; 373 -> 273. (#2)

## [v2.15.2] - 2026-09-08 - *Roothaan's Roadrunner*

First public commit (`9585e28`), split out of the private `vibe-qc` monorepo
along with vibe-qc, vibe-queue, qvf and vibe-qc-agentic-loop. The QVF manifest
schemas, previously symlinks into the producer's tree, became vendored data
files so the built wheel is self-contained.

## [v2.0.0] - 2026-07-01 - *Pauling's Panther*

Monorepo-era release, recorded before the split in vibe-qc's changelog and
carried here so the viewer's own history holds it (#32). The version has no
tag in this repository and its codename predates the per-product pools; both
are history, not entries in the current catalogue.

vibe-view 2.0 achieves feature parity with Avogadro 2.0 as an interactive
molecular editor and viewer with seamless vibe-qc workflow integration. 512
tests, 9 versioned feature releases, 24 CLI commands, 7 export formats.

[Unreleased]: https://github.com/vibe-qc/vibe-view/commits/main/
[v2.20.4]: https://github.com/vibe-qc/vibe-view/tree/v2.20.4
[v2.20.3]: https://github.com/vibe-qc/vibe-view/tree/v2.20.3
[v2.20.2]: https://github.com/vibe-qc/vibe-view/tree/v2.20.2
[v2.20.1]: https://github.com/vibe-qc/vibe-view/tree/v2.20.1
[v2.20.0]: https://github.com/vibe-qc/vibe-view/tree/v2.20.0
[v2.19.0]: https://github.com/vibe-qc/vibe-view/tree/v2.19.0
[v2.18.1]: https://github.com/vibe-qc/vibe-view/tree/v2.18.1
[v2.18.0]: https://github.com/vibe-qc/vibe-view/tree/v2.18.0
[v2.17.1]: https://github.com/vibe-qc/vibe-view/blob/v2.18.1/CHANGELOG.md#v2171---2026-09-15---lorensens-loon
[v2.17.0]: https://github.com/vibe-qc/vibe-view/tree/v2.17.0
[v2.16.2]: https://github.com/vibe-qc/vibe-view/tree/v2.16.2
[v2.16.1]: https://github.com/vibe-qc/vibe-view/tree/v2.16.1
[v2.16.0]: https://github.com/vibe-qc/vibe-view/blob/v2.18.1/CHANGELOG.md#v2160---2026-09-09---sayles-starling
[v2.15.3]: https://github.com/vibe-qc/vibe-view/blob/v2.18.1/CHANGELOG.md#v2153---2026-09-09---roothaans-roadrunner
[v2.15.2]: https://github.com/vibe-qc/vibe-view/blob/v2.18.1/CHANGELOG.md#2152---2026-09-08---roothaans-roadrunner
[v2.0.0]: https://github.com/vibe-qc/vibe-view/blob/v2.18.1/CHANGELOG.md#v200---2026-07-01---paulings-panther

[v2.20.5]: https://github.com/vibe-qc/vibe-view/releases/tag/v2.20.5
[v2.20.6]: https://github.com/vibe-qc/vibe-view/releases/tag/v2.20.6
[v2.20.7]: https://github.com/vibe-qc/vibe-view/releases/tag/v2.20.7
[v2.20.13]: https://github.com/vibe-qc/vibe-view/releases/tag/v2.20.13
[v2.20.12]: https://github.com/vibe-qc/vibe-view/releases/tag/v2.20.12
[v2.20.11]: https://github.com/vibe-qc/vibe-view/releases/tag/v2.20.11
[v2.20.10]: https://github.com/vibe-qc/vibe-view/releases/tag/v2.20.10
[v2.20.9]: https://github.com/vibe-qc/vibe-view/releases/tag/v2.20.9
[v2.20.8]: https://github.com/vibe-qc/vibe-view/releases/tag/v2.20.8

[v2.20.14]: https://github.com/vibe-qc/vibe-view/releases/tag/v2.20.14

[v2.20.15]: https://github.com/vibe-qc/vibe-view/releases/tag/v2.20.15

[v2.20.16]: https://github.com/vibe-qc/vibe-view/releases/tag/v2.20.16

[v2.20.17]: https://github.com/vibe-qc/vibe-view/releases/tag/v2.20.17
