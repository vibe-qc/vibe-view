# Roadmap

What has shipped, in which release, and what is proposed next.

> **Last reviewed 2026-10-02** against the released tags through v2.20.1.
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
*Phong's Pheasant* and *Levoy's Lemur* but shipped the #34 audit follow-ups, so
the lighting and volume-rendering proposals under those names are still open
and now sit under [Proposals](#minor-releases-and-remaining-proposals) without
a version. The maintainer decides the scope of the next release at cut time.

## Where things stand

| | |
|---|---|
| Latest release | **v2.20.1** *Levoy's Lemur*, 2026-09-30 |
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
TREXIO geometry and molecular-orbital interoperability, and v2.19.0 and
v2.20.0 work through the #34 viewer audit. The detailed
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
  **On `main` since 2026-10-05, unreleased (#68).** The Publication preset
  outlines every atom and bond with an inverted hull, hides the viewport
  chrome, remembers the look it replaced so clearing the picker restores it,
  and `vibe-view capture --publication` renders the same look headlessly.
- A theme and accent pass. **Partly on `main` (#68):** the dark theme's
  accent now meets WCAG AA as text. A wider pass over contrast and focus
  stays open under [Accessibility](#accessibility).

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

**Today:** nothing. vibe-view extracts isosurfaces and has no direct volume
rendering or transfer-function code.

**Proposed:**

- Direct volume rendering of QVF volume sections, with an editable colour and
  opacity transfer function.

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
[direct volume rendering design](volume_rendering_design.md) proposes the
first version: a per-section *Render as* choice with the isosurface as the
default, a uint8 grid capped at 128³, a transfer function with per-kind
presets that sessions, bookmarks and `viewer_defaults` carry, and skewed
grids falling back to the isosurface. It lists what trame-vtk does and does
not serialise for a volume, the tests, a five-step rollout, and the six
decisions taken on 2026-10-06. Step 1, the payload module
`renderers/volume_render.py`, is on `main`; the viewer does not change
before step 2.

**Not yet measured.** Production-sized cubes, since the example archives are
small test systems; weaker or software-rendered GPUs, because the frame-rate
run used one fast machine; and the full viewer, since the benchmark used a bare
view with a simple opacity ramp and camera steps rather than mouse drags.

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
- More fragments and templates; thumbnails in the structure library.
- Further unit-preference coverage: COOP/COHP, static captures and exports,
  symmetrize shifts and compare RMSD. Browser orbital energies, measurements,
  band/DOS axes and terminal geometry/orbital/band/DOS views share persisted
  preferences (shipped in v2.20.0, #58); spectra retain their own picker.
- A trajectory and vibration scrubber with thumbnails.
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
