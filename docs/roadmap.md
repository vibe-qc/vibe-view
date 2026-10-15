# Roadmap

What has shipped, in which release, and what is proposed next.

The [vibe-sketch/vibe-view scope decision](sketch_view_scope.md) records the
agreed division between 2D publication drawing and 3D calculation work,
including basic cleanup in both applications and the intended geometry
round trip. It is independent of the historical release snapshot below.

> **Last reviewed 2026-10-06** against the released tags through v2.20.2.
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
first version of direct volume rendering is on `main` after that tag,
unreleased; both are recorded under
[Proposals](#minor-releases-and-remaining-proposals) without a version. The
maintainer decides the scope of the next release at cut time.

## Where things stand

| | |
|---|---|
| Latest release | **v2.20.2** *Levoy's Lemur* (inherited), 2026-10-06 |
| Next release | Scope and timing remain the maintainer's decision; no codename is registered for it, and the only unspent registered name is *Levinthal's Lynx* (v3.0.0) |
| Current defects | Consult the canonical development tracker; [public reports](https://github.com/vibe-qc/vibe-view/issues) are tracked separately |
| Published installers and documentation | Verify availability separately from source tags; see [Desktop distribution](#desktop-distribution) |

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
figure look and the first piece of direct volume rendering. The detailed
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
correct from any camera. A true silhouette pass still needs client-side
work and sits with the v3.0.0 proposals.

### Transfer functions and volume rendering

Originally proposed for v2.20.0, *Levoy's Lemur*; not shipped there.

**Today:** the first version is on `main`, unreleased after v2.20.2. *Render
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

**Filed 2026-10-06.** The `[for qvf]` ask recording the two hint keys in
the format is qvf#5: it states the keys' shape and validation as the viewer
reads them and proposes a `viewer_defaults` subsection for the
specification. When it lands, the manual's hint section drops "ahead of
the specification" and links the format's text instead.

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
and the measurements above) are on `main`. The rollout is complete; its
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

The codename is held for a major version about the interactive viewer itself.
Nothing yet decides what would make that release a breaking change.

**Proposed:** work that needs the browser client, not geometry rebuilt on the
server:

- Continuous label billboarding with client-side followers. Today labels
  re-face the camera only when an interaction ends.
- A client-side frame-rate measurement, taken inside the page.
- A client-side silhouette pass, for outlines on isosurfaces and ribbons,
  which the inverted hull of #68 does not cover.
- A plugin API. The Python SDK is documented, but there is no plugin surface
  ([Python API](python_api.md)).

### Not yet placed

No registered codename covers these:

- Translucent isosurfaces without vtk.js depth-peeling stalls; orbitals
  currently default to opaque as a workaround
  ([design refresh](design_refresh_2026.md)).
- Metal-metal bonds in clusters and organometallics, which the bonding
  heuristic cannot see.
- More fragments and templates. The structure library's thumbnails are
  **on `main` after v2.20.2:** a preview of the picked molecule and a
  picture in every pinned and recent chip, rendered in the background from
  the library's geometry.
- Unit-preference coverage beyond #58, which gave the browser's orbital
  energies, measurements and band/DOS axes and the terminal's geometry,
  orbital, band and DOS views their persisted preferences in v2.20.0.
  **On `main` after v2.20.2:** COOP and COHP charts in the browser and the
  terminal, the band structure `capture` writes and the capture API's chart
  and bond-order functions, the Bond Orders panel, the shift Symmetrize
  reports, the compare legend, and `diff` and `batch-compare` follow the
  saved units; JSON output and CSV downloads keep the archive's units.
  Spectra retain their own picker.
- A trajectory and vibration scrubber with thumbnails. **On `main` after
  v2.20.2:** a filmstrip of frame thumbnails under the trajectory slider,
  and a phase slider with a filmstrip of one period for a vibration, both
  rendered off-screen in the background and seekable by click or by a drag
  across the strip; a click on the energy profile seeks too, and the
  terminal's playback keys (Space, `[` `]`, `<` `>`) work in the browser.
  Still open: bonds in the live playback frames, which the thumbnails draw
  and the playback does not.
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
versions (a Python 3.14 CI lane is in progress), and evaluating trame 4 once the viewer dependencies support it.
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
  2026-09-13. It records M1–M6 as they stood at the split, with the evidence
  behind each item. Its issue numbers belong to project 19, and some of its
  links point into the pre-split layout.
- `ROADMAP_V2.md`, at the repository root, is the original v1.1–v2.0
  proposal. Its version numbers never matched a release, and its codenames
  appear nowhere in this repository's code or catalogue.
