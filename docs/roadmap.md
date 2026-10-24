# Roadmap

What has shipped, in which release, and what is proposed next.

The [vibe-sketch/vibe-view scope decision](sketch_view_scope.md) records the
agreed division between 2D publication drawing and 3D calculation work,
including basic cleanup in both applications and the intended geometry
round trip. It is independent of the historical release snapshot below.

> **Last reviewed 2026-10-22** against main, released tags through v2.20.20,
> changelog fragments and the canonical development tracker.
> Update shipped scope when a release is cut, following the
> [release process](release_process.md). The tracker carries live defect status;
> this page records release history and proposals.

**Issue numbers.** Bare `#N` references below identify the canonical development
tracker. The [public issue tracker](https://github.com/vibe-qc/vibe-view/issues)
uses a separate numbering namespace. Historical monorepo issue numbers are
identified explicitly.

**Future releases are proposed, not scheduled.** Registered codenames
[commit to nothing](codenames.md) about when, or whether, a release is cut,
and a name does not bind the scope. v2.19.0 and v2.20.0 carried the names
*Phong's Pheasant* and *Levoy's Lemur* but shipped the #34 audit follow-ups.
The lighting proposal's publication mode then shipped in v2.20.2, and the
first version of direct volume rendering shipped in v2.20.3; both are recorded under
[Proposals](#minor-releases-and-remaining-proposals) alongside the remaining work. The
maintainer decides the scope of the next release at cut time.

## Where things stand

| | |
|---|---|
| Latest release | **v2.20.20** *Levoy's Lemur* (inherited), 2026-10-21 |
| Next release | Development targets v3.0.0, *Levinthal's Lynx*, since 2026-10-22; 2.21 was retired after its scope shipped in v2.20.19. 3.0 has a [recorded scope](#v300-levinthals-lynx-the-interactive-viewer) (#110, work packages #111–#114 and #120–#135) and no date |
| Current defects | Consult the canonical development tracker; [public reports](https://github.com/vibe-qc/vibe-view/issues) are tracked separately |
| Published installers and documentation | Verify availability separately from source tags; see [Desktop distribution](#desktop-distribution) |

## Current development and the next release

The delivered feature base includes direct volume rendering, publication
figures, playback filmstrips, ORCA result inspection, calculation preparation
and queue submission, and detailed 3D editing. These are existing capabilities,
not work that must be reimplemented for a new release.

**Development targets 3.0 since 2026-10-22.** The 2.21 milestone was closed
without a release: its one item, excited-state tables (#109), shipped in
v2.20.19. New features go to the 3.0 milestone. The 3.0 scope is recorded
below and on its milestone (#110): twenty work packages, the compatibility
decisions, and what was deferred out of it, with no release date. Setting the
package version to 3.0 stays with the release owner under the
[release process](release_process.md). 2.x fixes are cut on demand, decided
2026-10-23: only security or data-correctness fixes qualify. Each lands on
main first and is cherry-picked to a `maint/2.20` branch made from the
v2.20.20 tag when the first such fix is needed, then tagged as a 2.20.x
patch.

**Released in v2.20.19 and v2.20.20:**

- Excited-state tables in the CLI, terminal and browser (#109); linked NTO
  and density identifiers are shown, but drawing them from the table is not
  included. Transition densities are a supported signed volume (#115) and
  open with difference-density defaults (#119).
- The first 3.0 work packages, additive and compatible with 2.x: client-side
  label followers (#111), the client-side silhouette pass (#112), renderer
  extension API v1 (#113) and the client performance harness with the WebGL
  renderer string in the About dialog (#114, matrix not yet complete).
- **Export screenshot** saves the browser frame with its outlines (#116);
  browser file opening and downloads work again (#116, #117); normalized
  spectrum CSV peaks (#118).
- Self-contained Linux x86-64 desktop packaging. As for macOS, this is
  packaging support in the release, not proof of a published installer.

**On main after v2.20.20:** only roadmap updates. The result-inspection
and parity work packages (#120–#135) are recorded but not yet implemented.

The open tracker also retains verified fixes already included in tags, such
as #100–#102 in v2.20.17. Open/closed and release labels alone therefore do
not measure unfinished implementation: compare the release notes and source.
The release owner should qualify the selected candidate and distribution
artifacts under the [release process](release_process.md); the remaining
3.0, accessibility and cross-application proposals are not automatically gates
for every intervening release.

## Milestones and weekly reconciliation

The canonical GitLab project uses **3.0 - interactive viewer** as the
milestone title for current development; **2.21 - bounded feature release**
is closed. Issues hold individual work and acceptance evidence; milestones
group a release's selected scope. Already released fixes are not added as new
release blockers.
The 3.0 milestone has a recorded scope (#110, work packages #111–#114 and
#120–#135), with its compatibility decisions recorded beside it; it is not a
requirement to finish before another 2.x release.

Review this roadmap every week against main, tags, changelog fragments,
linked issues and applicable acceptance evidence. Update stale statements and
record implementation, validation and released status separately. When the
selected milestone passes acceptance, mark the target ready and advance the
next planned release number; update package versions and tags only through
the normal [release process](release_process.md). Semantic major increments
require an agreed contract/scope, not just elapsed time or a paper deadline.
Preserve historical snapshots as dated history and explicitly record deferrals.

## Shipped

### What v2.15.2 already contained

All Avogadro-parity feature work was built in the private monorepo, before
the first public commit. Its full evidence and reasoning is in the frozen
[parity roadmap](ROADMAP_AVOGADRO_PARITY.md).

| Milestone | Scope | State at v2.15.2 |
|---|---|---|
| M1 vq cockpit | Job Manager, submit from the viewer, live monitor with log tail, queue overview | Complete |
| M2 file watching | Content-fingerprint watcher, per-section diff, hot reload that keeps the camera | Complete |
| M3 live optimization | MSINDO relaxation while editing, MACE engine, frozen atoms | Complete |
| M4 live results | Streaming checkpoint QVFs, run-status chip, Watch live | Complete |
| M5 biomolecules | Residues and chains, secondary structure, flat ribbons with sheet arrows, residue selection, colour by chain, secondary structure, residue type and B factor, camera-facing labels | Complete |
| M6 formats (F) | `[ase]` extra, 16 import extensions, twelve export formats round-tripped | Complete |
| M6 builder (E) | SMILES to 3D, Miller slab spacing, periodic bond images, supercell lattice | Partial, see [Not yet placed](#not-yet-placed) |
| M6 platform (G) | Electron build targets, Python SDK page, served-viewer accessibility baseline | Partial, see [Continuous tracks](#continuous-tracks) |

### Since the split

The first post-split releases made vibe-view a standalone product through
repairs, a documentation site, release machinery and artwork. v2.17.0 adds
TREXIO geometry and molecular-orbital interoperability, v2.19.0 and
v2.20.0 work through the #34 viewer audit, and v2.20.2 ships the Publication
figure look and volume payload support; v2.20.3 completes the first direct
volume rendering workflow. The detailed
release notes are in the [changelog](changelog.md).

| Release | Date | Codename | What shipped | Issues |
|---|---|---|---|---|
| v2.15.3 | 2026-09-09 | *Roothaan's Roadrunner* | First gated tag. QVF drift guard, skip audit, `[queue]` extra, VTK 9.7 test fix; documentation site and its CI; codename catalogue; `release` branch and procedure | #1, #2, #3, #5, #6, #7, #11, #12, #13, #14 |
| v2.16.0 | 2026-09-09 | *Sayle's Starling* | Product manual with real viewer figures; wordmark, favicon and the seven-image codename series; `release` extra; `trame<4` bound; QTAIM and spectrum-panel fixes | #9, #13, #20 |
| v2.16.1 | 2026-09-10 | inherits | The vibe-qc availability probe degrades instead of raising | #18 |
| v2.16.2 | 2026-09-12 | inherits | Missing-RDKit tests run in every environment; patch checklist; contributor guidance | #20 |
| v2.17.0 | 2026-09-13 | *Lorensen's Loon* | TREXIO geometry and molecular Gaussian orbitals; packaging-metadata checks; standalone agent guides | #23 |
| v2.17.1 | 2026-09-15 | inherits | Complex coefficients and Gamma-point lattice tails for MOs; scene replacement that avoids stale viewports; command-palette Escape handling during presentation | — |
| v2.18.0 | 2026-09-17 | *Richardson's Robin* | Biomolecule polish; metadata-preserving slice; Fermi-relative DOS; background and export fixes; energy windows; docs extra; Bash 3.2 compatibility and candidate-only product gate | #19, #21, #22, #24, #25, #26, #27, #29 |
| v2.18.1 | 2026-09-22 | inherits | Full-tree lint and strict source typing; general Miller slab orientations; tutorial and documentation updates; stable real-UHF gradient fixture | #8, #20, #30, #33, #41 |
| v2.19.0 | 2026-09-25 | *Phong's Pheasant* | #34 audit consolidation: one QVF writer, one bond rule, one unit-cell module, shared CLI options and headless scene setup; hardened readers and importers; periodic-surface, terminal-chart and archive-merge fixes | #34–#40 |
| v2.20.0 | 2026-09-29 | *Levoy's Lemur* | #34 audit follow-ups: reader, renderer, browser, terminal, CLI and converter correctness fixes; display units for orbital energies, band/DOS axes and lengths in browser and terminal; signed volumes keep both lobes along reaction paths | #43–#63 |
| v2.20.1 | 2026-09-30 | inherits | Open-time errors clipped (#64); `validate` separates layout errors from hash mismatches (#65); malformed spectrum, GTO and MO payloads refused by the reader (#66); `batch-compare` reports reasons (#67); GRO and PDB first-frame and cell validation (#40); typed browser composition root (#48) | #40, #48, #64–#67 |
| v2.20.2 | 2026-10-06 | inherits | Publication preset as the one-click figure look, with outline and hide-chrome switches and `capture --publication` (#68); dark-theme accent at WCAG AA (#68); large unsigned volumes open again through the reduced-detail path (#69); Python 3.14 CI lane and `trexio` extra guard; the direct-volume payload module, step 1 of the [volume design](volume_rendering_design.md) | #68, #69 |

The subsequent v2.20.3 to v2.20.14 releases added direct volume rendering,
filmstrip playback, library thumbnails, broader display units, ORCA result
workflows, vibe-sketch launching, corrected calculation inputs and queue
integration, QVF 0.3 compatibility, and 3D dragging, displacement and FIRE
relaxation. See the [changelog](changelog.md) for the release of each change.
v2.20.15 adds single and combined normal-mode geometry edits, the ORCA
mode-editing tutorial, stricter input handling and the QVF 0.4 reference
validator. v2.20.16 fixes CIF block isolation and updates format documentation.
v2.20.17 adopts the QVF 0.5 toolkit and fixes long GIF export, linear-molecule
symmetry and species-preserving rigid alignment. The QVF archive format remains
version 1; toolkit releases are not archive-version changes.
The detailed sections below retain the remaining proposals and measured limits.

### Correctness fixes shipped in v2.18.0

These corrections are included in the v2.18.0 tag.

| Issue | Defect | Released behaviour |
|---|---|---|
| #24 | `slice` drops section fields and root metadata | Preserve original metadata and kept bytes; prune removed viewer hints and validate before replacing output |
| #25 | Combined bands/DOS uses one Fermi shift for both panels | Shift bands by their own reference; preserve the DOS grid; separate axes when the bands' reference is absent |
| #27 | Scene rebuilds discard the light-background choice | Keep the explicit background through rebuilds and reloads |
| #21 | Headless export creates an unawaited reset coroutine | Schedule a callback only when an event loop is running |

### v2.17.0: *Lorensen's Loon*, TREXIO orbitals

The maintainer selected TREXIO interoperability for this release. HDF5 files
and text datasets now supply geometry and real molecular Gaussian s/p/d/f
orbitals to the existing isosurface workflow. The importer preserves units,
normalization, AO ordering and available spin/energy/occupation metadata;
the [format guide](formats.md#trexio-wavefunctions) describes its limits.

### v2.18.0: *Richardson's Robin*, biomolecules

**Shipped scope:** polish of M5, whose initial biomolecule support shipped in
v2.15.2, plus the correctness fixes above.

- Isolate or hide selected residues without connecting hidden ribbon spans.
- Select residues by clicking the viewport.
- Share chain, secondary-structure, residue and B-factor palettes across ribbons,
  atoms and bonds.
- Retain PDB HELIX/SHEET ranges and distinguish supplied alpha, pi and 3₁₀
  helices and beta bridges. The CA-only geometric assignment stays H/E/C.
- Draw crisp strand edges and independently shaded end caps; include the final
  residue in a chain-terminal strand assignment.
- Show dashed geometric hydrogen-bond contacts using explicit H and N/O
  candidates in the supplied coordinates.

The [biomolecule guide](biomolecules.md) documents the display controls and
scientific limits. No new core dependency or QVF schema change is needed.

## Minor releases and remaining proposals

The proposals below are not a release commitment. Recheck their current code
and tracker evidence before selecting a release scope.

### Materials and lighting

Originally proposed for v2.19.0, *Phong's Pheasant*; not shipped there.

**Today:** material presets, Publication, Presentation and Analysis
representation presets, toon shading, OSPRay ray tracing, and light and dark
themes.

**Proposed:**

- Publication mode: white background, thick outlines, no gizmos, one click.
  **Shipped in v2.20.2 (#68).** The Publication preset outlines every atom
  and bond with an inverted hull, hides the viewport chrome, remembers the
  look it replaced so clearing the picker restores it, and
  `vibe-view capture --publication` renders the same look headlessly.
- A theme and accent pass. **Partly shipped in v2.20.2 (#68):** the dark
  theme's accent now meets WCAG AA as text. A wider pass over contrast and
  focus stays open under [Accessibility](#accessibility).

**Constraint:** ambient occlusion and fog are VTK render passes, and render
passes never reach the vtk.js client
(`test_ambient_occlusion_is_server_side_only`). Silhouette outlines depend on
the view, so an outline computed on the server goes stale when the client
rotates. The shipped outline avoids this by being geometry: a slightly
larger, front-culled black copy of each actor (Raskar and Cohen, 1999),
correct from any camera. The true silhouette pass for isosurfaces and
ribbons runs in the client since #112, below.

### Transfer functions and volume rendering

Originally proposed for v2.20.0, *Levoy's Lemur*; not shipped there.

**Today:** the first version shipped in v2.20.3. *Render
as → Volume* in the volume controls draws a stored section through a
transfer function: a uint8 grid capped at 128 voxels along its largest axis,
the section's colormap, a preset opacity ramp edited through a window and
six sliders, per-section persistence through bookmarks, sessions and the
`render` and `transfer_function` keys of `viewer_defaults`, and
`vibe-view capture --render volume` for the same look headlessly, with
`capture` and `batch` following a section's `render` hint by default. A
skewed grid is resampled onto its bounding box, since vtk.js places a volume
axis-aligned whatever direction matrix its image carries (checked in Chrome
on 2026-10-06), and the opacity curve is edited node by node in a page
widget with pointer and keyboard. v2.20.2 itself carries only the payload
module. The isosurface stays the default.

**Specified in QVF.** The volume-mode `render` and `transfer_function`
hints are documented in the current
[QVF section-hint contract](https://vibe-qc.com/docs/qvf/spec.html#section-hint-object).
They are advisory hints; malformed values do not invalidate an archive.
The original request was filed as qvf#5 on 2026-10-06.

**Measured 2026-10-02.** Rendering stays on Trame and PyVista
([decision 6](ROADMAP_AVOGADRO_PARITY.md)), so volume rendering needed the same
payload and interaction check cartoon rendering got. Both scripts are in
`docs/`.

- **Payload** (`bench_volume_payload.py`). A volume sends the whole grid, so it
  grows with N³ where an isosurface grows with N². On the 19 volume sections in
  ten example archives (largest 75×75×30, most under 0.3 MB) a float32 volume
  sends 0.26 MB or less and uint8 0.06 MB or less, below the 0.60 MB cartoon
  ribbon. Synthetic grids show the risk: a float32 volume is about 8 MB at
  128³ and 67 MB at 256³, against 1.0 and 4.0 MB for the isosurface. Quantising
  to uint8 and capping the grid at about 128³ keeps it near 2 MB.
- **Frame rate** (`bench_volume_fps.py`). In Chrome on an Apple M4 Pro, a
  volume cost about 1 ms more per frame than today's surface (2.7 to 3.1 ms
  against 1.8 to 2.0 ms) from 64³ to 256³, far above 60 fps.

**Conclusion.** Neither measurement blocks a first version. Send a capped,
quantised grid by default.

**Design note, 2026-10-06.** The
[direct volume rendering design](volume_rendering_design.md) sets out the
first version and records the six decisions taken that day. Of its
five-step rollout, step 1 (the payload module) shipped in v2.20.2, and
steps 2 to 5 (the actor and switch, the editor and persistence, the manual,
and the measurements above) shipped in v2.20.3. The rollout is complete; its
last follow-up, the `[for qvf]` ask, is filed (above), and the cases under
"Still unmeasured" below remain.

**Measured 2026-10-06, step 5 of the design** (`bench_volume_viewer.py`).
The three cases the first measurements left open, against the real viewer
at 1440×900, dragging the mouse across the canvas, with a synthetic water
density on 128³, 160³ and 200³ grids (production cubes run 100 to 200 points
per axis):

- **Production-sized cubes.** Building the payload takes under 0.15 s at
  every size, serialising it 0.005 s at the 128³ cap and 0.02 s for a 200³
  grid sent uncapped; the voxels are 2.1 MB raw at the cap and 8.0 MB
  uncapped (0.13 and 0.41 MB after compression, an optimistic figure for a
  smooth synthetic field). Switching *Render as* to *Volume* took 0.5 s from
  click to drawn on both GPUs, 0.8 s uncapped.
- **The hardware GPU** (Apple M4 Pro in Chrome, refresh cap off): a drag
  step costs 2.3 ms with the volume against 2.4 to 2.7 ms with the
  isosurface, at every size and also for the uncapped 200³ grid. In the full
  viewer the volume costs no more than today's surface; the extra
  millisecond of the bare-view measurement is lost in the rest of the scene.
- **The software renderer** (SwiftShader, headless Chromium): a drag step
  costs 100 to 170 ms with the volume (6 to 10 fps) against 340 to 410 ms
  with the isosurface (2 to 3 fps); the uncapped 200³ grid draws at 7 fps.
  On a GPU-less machine the whole viewer is slow, and the contour is the
  slower of the two, so volume mode adds no new floor.

**Measured 2026-10-07, the remaining cases** (`bench_volume_viewer.py` with
`--dpr`, `--vsync` and its new `--archive`, on the same M4 Pro; the 160³
baseline re-measured the same day gave 3.1 ms per drag step with the volume
against 3.3 ms with the isosurface):

- **Retina pixel ratio** (2): 5.3 ms per step with the volume against 5.4 ms
  with the isosurface. Four times the pixels cost about 1.7 times the step in
  both modes, and the volume stays no dearer than the surface.
- **The refresh cap on:** both modes tie at the display's 120 Hz, 8.3 ms per
  step, so a drag feels the same in either; only the cap-off numbers tell
  them apart.
- **A computed cube:** vibe-qc's water density example, a 68×88×76 grid with
  nuclear cusps to 101 e/bohr³ against a 99.5th percentile of 0.26, imported
  with `vibe-view import` and sent whole, being under the cap. Its 0.45 MB of
  voxels compress to 0.04 MB on the wire, 12:1 against the smooth synthetic
  field's 16:1 at the cap: a real field costs a little more wire and stays
  far under the cartoon ribbon. The switch took 0.9 s, a drag step 2.8 ms as
  a volume against 2.7 ms as a surface, and on the software renderer 235 ms
  against 343 ms (4.2 against 2.9 fps), beside 262 against 610 ms for the
  synthetic 128³ grid the same day; the volume stays the faster of the two on
  a GPU-less machine. A five-second drag gives too few steps there to trust;
  the probe's `--seconds 20` does.

**Still unmeasured.** Other operating systems and GPUs.

### v3.0.0: *Levinthal's Lynx*, the interactive viewer

**Scope recorded 2026-10-19** on the 3.0 milestone and its umbrella issue
#110, at the maintainer's request. The release is about the browser client:
work that happens in vtk.js rather than as geometry rebuilt on the server,
plus the first public extension contract beyond importers. No date is set.

**What stays compatible.** The QVF format stays version 1; qvf owns it
(qvf#11). The Python SDK on the [Python API](python_api.md) page, the CLI
commands, the session file and `create_app(readers)`, which vibe-qc's
contract tests call, keep their 2.x behaviour; 3.0 removes nothing. A 2.x
user needs no migration beyond the Python 3.12 floor below.

**What makes it a major version.** A versioned extension contract with a
compatibility promise for the 3.x series, and a change in what the browser
guarantees: labels and outlines become continuous client-side behaviour
rather than something re-rendered when an interaction ends.

**Work packages**, one issue each under the milestone, each accepted on its
own issue with served-browser evidence:

- **WP 3.1 Client-side label followers (#111).** Atom, measurement and axis
  labels keep facing the camera during an interaction through a vtk.js
  follower; the server-side billboard basis stays for captures and the
  terminal. Before this, labels re-faced the camera only when an interaction
  ended. *Released in v2.20.19:* the label mesh carries each glyph's
  anchor and reading frame as point data, trame-vtk is told to send them, and
  a vertex-shader replacement places every glyph for the frame's camera, so
  the mesh is never rewritten in the page; the bounds grid's labels already
  followed in vtk.js, and measurements are a card, not scene labels.
  Evidence: the trusted-drag profile of `tests/test_design_refresh.py` reads
  back a frame mid-drag and matches it to the glyphs rasterised for the
  camera-facing basis; `docs/bench_label_orientation.py --browser` for the
  drag frame time; the [browser manual](browser.md).
- **WP 3.2 Client-side silhouette pass (#112).** Outlines for isosurfaces
  and ribbons as a screen-space edge pass in the client, which the inverted
  hull of #68 cannot give; the geometry outline stays the fallback without
  WebGL2 and for headless captures. *Released in v2.20.20:* a vtk.js
  render pass after the scene's own renders the depth of the isosurfaces,
  Fermi surfaces and ribbons the server marks, and draws their depth and
  crease edges over the frame, depth-tested, with figure outlines on;
  translucent surfaces included. Evidence: the silhouette profile of
  `tests/test_design_refresh.py` (frame readback against the surface
  boundary, before and during a trusted drag, and a measure-mode pick with
  the pass installed); the browser-against-headless figures in
  {ref}`the headless page <silhouette-outlines>`; the limits list and
  `docs/bench_silhouette.py` timings in the [browser manual](browser.md);
  the audit note in `docs/design_refresh_2026.md`.
- **WP 3.3 Extension API v1 for section renderers (#113).** A documented,
  versioned `vibeview.renderers` entry-point group modelled on importer
  plugins: a renderer with `load()` and `render_to_html()`, a sidebar title
  and icon, and a plugin kind that classifies as rendered. A sample plugin
  ships in the manual and runs in candidate CI. *Released in v2.20.20:*
  `vibeview.renderer_plugins` is the contract, at API version 1; plugin
  kinds open as details panels in the browser, are reported by `info`, the
  banner and `doctor`, satisfy `critical`, and fall back to the generic pane
  in the terminal. The sample package in `docs/examples/renderer-plugin` is
  installed by the candidate `test` job and checked end to end by
  `scripts/smoke_renderer_plugin.py`; `tests/test_renderer_plugins.py` holds
  the contract tests; [Extending vibe-view](python_api.md#extending-vibe-view)
  documents both groups and the 3.x compatibility promise.
- **WP 3.4 Client performance qualification (#114).** The in-page
  frame-timing harness extended from volume rendering to drags,
  representation switches and label followers, a measured matrix for Apple
  Silicon and Linux, and the thresholds that gate the 3.0 candidate.
  *Released in v2.20.20; required matrix qualified:*
  `scripts/client_qualification.py` drives the real viewer through eight
  cases (structure drag, two representation switches, label-follower drag,
  isosurface drag plain and with the outline pass, the switch to volume
  rendering and a volume drag) and judges them against
  `scripts/client_thresholds.json` per renderer class. The candidate job
  `client-performance` runs it on Linux with SwiftShader, gating 3.x
  candidates and reporting on 2.x; the About dialog shows the WebGL renderer
  string. The [client performance page](client_performance.md) holds the
  matrix: Apple Silicon with Chrome is measured on the GPU and on
  SwiftShader; Linux SwiftShader is measured from the v2.20.20 candidate,
  with software thresholds tightened to about twice that measured cost.
  Windows GPU qualification is deferred until users request it and does
  not gate 3.0; no Windows test PASS is claimed.

#### Result inspection from one viewer

**Scope extended 2026-10-22** at the maintainer's request: new features go to
the next major release. A user request asked for one program covering
geometry with a basic editor, orbital and density plots, normal modes as
arrows and animation (saved as GIF), and calculated UV-Vis, IR and Raman
spectra with a chosen line width and ASCII export. Most of this exists (ORCA
GBW orbitals, Hessian vibrations and spectra, property-file electronic
spectra, video export, the 3D editor); these work packages close the gaps.
Each is accepted on its issue.

- **WP 3.5 Spectra and modes from an ORCA output alone (#120).** Geometry,
  normal modes and the IR, Raman and absorption/CD tables read from `.out`,
  checked against the Hessian/property route when both exist.
- **WP 3.6 Line shapes and ASCII export (#121).** Gaussian or Lorentzian
  envelopes with a FWHM typed in physical units, broadened in energy, and a
  documented whitespace-delimited export from the browser and the CLI.
- **WP 3.7 Mode arrows and GIF (#122).** Static displacement arrows for the
  selected mode, in the client, captures and exports, and one-step GIF/MP4
  export of a mode from the Vibrations panel.
- **WP 3.8 ORCA densities (#123).** Correlated, excited-state, difference and
  spin densities as cubes through the user's `orca_plot`, folded into the
  calculation when the geometry matches and kept in a saved QVF.
- **WP 3.9 Internal-coordinate editing (#124).** Set a measured distance,
  angle or dihedral to a value, and add, remove or set the order of a bond.
  2D drawing stays with vibe-sketch under the
  [scope decision](sketch_view_scope.md).

#### Feature parity for 3D work and result inspection

**Scope extended again 2026-10-22** at the maintainer's request: the goal is
that vibe-view and vibe-sketch together cover what users now do in the
established desktop programs for 3D structure viewing, building, quantum
chemistry result inspection and 2D chemical drawing. The gap analysis below
was checked against main on that date. It lists only capabilities that are
missing or partial; the delivered base is described in
[capabilities](capabilities.md). The frozen
[2026-09 parity plan](ROADMAP_AVOGADRO_PARITY.md) remains the record of the
earlier builder and live-job work.

| Area | vibe-view today | Missing | WP |
|---|---|---|---|
| Molecular surfaces | Isosurfaces of stored or evaluated fields; a stored potential mapped onto a density isosurface | VdW, SAS and SES surfaces from atoms, property colouring, mesh/dot styles, area and volume | 3.10 (#125) |
| Interactions | Dashed geometric hydrogen bonds | Configurable H-bond criteria, contacts and clashes, salt bridges, halogen bonds, π-stacking, tables | 3.11 (#126) |
| Macromolecules and databases | PDB with chains, residues, B factors; cartoons | Fetch by PDB/AlphaFold/PubChem identifier, mmCIF, ensembles, alternate locations, sequence panel, sequence-based superposition | 3.12 (#127) |
| Experimental maps | Cube files, isosurfaces, volume rendering | MRC/CCP4 maps, sigma levels, mesh, zone around a selection | 3.13 (#128) |
| Orbitals from other programs | ORCA GBW/JSON, TREXIO, vibe-qc | Formatted checkpoint and Molden files | 3.14 (#129) |
| Outputs from other programs | ORCA result files; Gaussian input geometry | Optimisation steps, energies, modes, spectra and charges from common program outputs; multi-frame XYZ | 3.15 (#130) |
| Building | Atom editor, fixed fragments, hydrogens, SMILES, crystal builder, live relaxation, symmetrize | Bond rotation, coordination templates and ligands, peptide/nucleic-acid builder, conformers, larger fragment library | 3.16 (#131) |
| Coordinates as numbers | Measurements; XYZ export | Editable Cartesian and Z-matrix tables, Z-matrix import/export | 3.17 (#132) |
| Inputs for other codes | vibe-qc and ORCA, vq submission | Gaussian, Psi4, NWChem, Q-Chem, GAMESS, CP2K, xtb, VASP structure | 3.18 (#133) |
| Figures and movies | Publication presets, outlines, screenshots, turntable/trajectory/vibration video | Scene measurement labels, 2D annotations, colour keys, colour/radius by attribute, morph movies | 3.19 (#134) |
| Selection and scripting | Click selection, Compare Files overlay, Python SDK | Selection language, command line with replayable log, multi-model scenes, selection measurements, desktop Python console | 3.20 (#135) |

Each work package is accepted on its own issue. Optional parsers and readers
(for example cclib or gemmi) are lazy extras with an install hint; no GPL
dependency is added. Network access (WP 3.12) happens only on an explicit
user action. Formats vibe-view needs beyond the current QVF kinds go to qvf
first.

**Owned by vibe-sketch, tracked on its tracker** under the
[scope decision](sketch_view_scope.md): skeletal drawing, reaction schemes,
publication styles, formula and mass already exist there. Its 2D parity
gaps, filed 2026-10-22 for its 1.0 milestone, are vibe-sketch#90–#99:
R-groups, repeat units and abbreviations; shapes, highlights and TLC plates
in figures; CIP and enhanced-stereo labels; elemental analysis and
descriptors; name to structure; CDXML export; office formats and clipboard;
calculated NMR shifts on atoms; peptide and oligonucleotide sequences; a
stoichiometry table. Spectra line lists (vibe-sketch#87) share the
broadening function of WP 3.6 (#121).
vibe-view's part of that workflow is the 3D round trip, deferred out of
3.0 below.

**Not planned for 3.0:** immersive VR/XR viewing, fitting models into maps,
and homology modelling.

**Conditional.** Translucent isosurfaces through client-side depth peeling
join the scope only if WP 3.2 lands with headroom; otherwise they stay under
*Not yet placed*.

**Decisions taken by the maintainer on 2026-10-23:**

- The Python floor rises from 3.11 to 3.12, the floor of vq and
  vibe-sketch. This retires the 3.11 CI lane and lets the `queue` and
  `sketch` extras resolve on every supported interpreter. It is the one
  compatibility change in 3.0.
- Representations and 3D actors join the extension contract in a later 3.x
  minor, not in 3.0, as WP 3.3 (#113) shipped.
- The codename is *Levinthal's Lynx*.

**Deferred out of 3.0:** metal–metal bonds in clusters, bonds in live
playback frames, the vibe-sketch round trip (cross-repository, see the
[scope decision](sketch_view_scope.md)), representations and 3D actors in
the extension contract (a later 3.x minor) and the trame 4 migration, which
is dependency-driven and stays a continuous track.

**Acceptance for the milestone:** every work package accepted on its issue;
the extension contract and the client performance page published in the
manual; the candidate pipeline green on the Python 3.12+ matrix; release
notes stating the floor change as the only migration; then the normal
[release process](release_process.md).

### Not yet placed

No registered codename covers these:

- Translucent isosurfaces without vtk.js depth-peeling stalls; orbitals
  currently default to opaque as a workaround
  ([design refresh](design_refresh_2026.md)).
- Metal-metal bonds in clusters and organometallics, which the bonding
  heuristic cannot see.
- More fragments and templates, now placed in 3.0 (WP 3.16, #131). The
  structure library's thumbnails are
  **shipped in v2.20.3:** a preview of the picked molecule and a
  picture in every pinned and recent chip, rendered in the background from
  the library's geometry.
- Unit-preference coverage beyond #58, which gave the browser's orbital
  energies, measurements and band/DOS axes and the terminal's geometry,
  orbital, band and DOS views their persisted preferences in v2.20.0.
  **Shipped in v2.20.3:** COOP and COHP charts in the browser and the
  terminal, the band structure `capture` writes and the capture API's chart
  and bond-order functions, the Bond Orders panel, the shift Symmetrize
  reports, the compare legend, and `diff` and `batch-compare` follow the
  saved units; JSON output and CSV downloads keep the archive's units.
  Spectra retain their own picker.
- A trajectory and vibration scrubber with thumbnails. **Shipped in
  v2.20.3:** a filmstrip of frame thumbnails under the trajectory slider,
  and a phase slider with a filmstrip of one period for a vibration, both
  rendered off-screen in the background and seekable by click or by a drag
  across the strip; a click on the energy profile seeks too, and the
  terminal's playback keys (Space, `[` `]`, `<` `>`) work in the browser.
  Still open: bonds in the live playback frames, which the thumbnails draw
  and the playback does not.
- Rendering QVF `excited_states` tables. **Shipped in v2.20.19 (#109):**
  the states are tabulated by `vibe-view table --kind excited_states`, the
  terminal and the browser details panel, with energies in eV and nm,
  oscillator and rotatory strengths, orbital contributions and the linked
  NTO and transition-density sections. Drawing those from the table is not
  part of it.
- Returning edited 3D geometry to its originating vibe-sketch species with
  atom/species identity, graph, state and provenance preserved. Launching and
  sending structures exist; the complete mapped round trip and shared service
  remain planned ([scope decision](sketch_view_scope.md)).
- A vq job handle in QVF provenance (`provenance.vq`), which needs the
  producer's agreement.

## Continuous tracks

### Accessibility

The served-viewer baseline landed before the split, in project 19 #179–#182,
#314, #321, #323, #326, #328, #331, #332 and #341. Still open: systematic
contrast, focus order and visibility, complete keyboard reachability,
reduced-motion behaviour, and how often high-frequency status messages are
announced.

### Release and CI infrastructure

The candidate-only product gate and declared documentation extra shipped in
v2.18.0 (#22, #19). v2.18.1 completed the full-tree `ruff check .` gate and
strict source-module inventory (#8, #33); `tests/test_type_ramp.py` prevents
new source modules from escaping that inventory. The browser composition
refactor (#48) registers callback methods after construction and removes the
former untyped-decorator exemption.

Continuing work includes optional-dependency coverage, supported Python
versions (the Python 3.14 CI lane shipped in v2.20.2), and evaluating trame 4
once the viewer dependencies support it.
Use the tracker and current CI configuration for evidence and status.

### Desktop distribution

Source tags and installer publication are separate steps. Availability of a
source release does not imply that every platform's installer or update feed
has been published. Distribution, signing and publication remain maintainer
operations; see `electron/PUBLISHING.md` for the portable product contract.

### Cross-repository coordination

Producer-dependent contract coverage belongs with vibe-qc, QVF schema and
format decisions with qvf, and queue packaging with vibe-queue. Confirm the
owning repository's current issue status before planning a dependency here.
Changes to another repository require its maintainer's coordination.

## Decisions carried forward

The decisions locked with the maintainer on 2026-07-02
([parity roadmap §0](ROADMAP_AVOGADRO_PARITY.md)) still hold, with one
exception:

- **Compute:** vq first, with a local vibe-qc subprocess for small jobs.
- **Live optimization:** vibe-qc semi-empirical methods in a background
  subprocess.
- **Running jobs:** streaming checkpoint QVFs with hot reload.
- **Format breadth:** a hand-written core, with ASE optional for the rest.
- **Rendering:** stay on Trame and PyVista.
- **Cross-repository work:** formal asks to the owning repository.
- **Delivery (superseded):** decision 7 is replaced by the
  [release process](release_process.md). Work lands on `main` and is proved by
  a `release-candidate/*` pipeline before a tag, and `release` only
  fast-forwards to tags.

## History

- The [Avogadro parity roadmap](ROADMAP_AVOGADRO_PARITY.md) was frozen on
  2026-09-13, with a later dated general-slab note retained. It records M1–M6
  around the split, with the evidence
  behind each item. Its issue numbers belong to project 19, and some of its
  links point into the pre-split layout.
- `ROADMAP_V2.md`, at the repository root, is the original v1.1–v2.0
  proposal. Its version numbers never matched a release, and its codenames
  appear nowhere in this repository's code or catalogue.
