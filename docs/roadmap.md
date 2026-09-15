# Roadmap

What has shipped, in which release, and what is proposed next.

> **Last reviewed 2026-09-15** after v2.17.1 at `de0a040` was released and its
> documentation deployment verified. Update this page when a listed issue closes,
> and move a release's items into [Shipped](#shipped) as part of
> [the post-release step](release_process.md).

**Issue numbers.** A bare `#N` on this page is a
[vibe-view issue](https://github.com/vibe-qc/vibe-view/issues)
(project 35). The pre-split monorepo tracker, project 19, numbered its issues
independently; its numbers appear only as "project 19 #N".

**Future releases are proposed, not scheduled.** Each unreleased minor below
carries its registered codename, and its proposed scope follows that theme. A registered name
[commits to nothing](codenames.md) about when, or whether, that release is
cut. The scope under each future version is a proposal for the maintainer, who
decides it at cut time.

## Where things stand

| | |
|---|---|
| Latest release | **v2.17.1** *Lorensen's Loon*, 2026-09-15, tag and `release` on `de0a040` |
| Documentation site | serves 2.17.1; index and three guide pages match the tagged commit's CI artifacts (checked 2026-09-15) |
| Desktop update feed | serves **2.10.0** (checked 2026-09-13), see [Desktop distribution](#desktop-distribution) |
| Open issues | 14: four correctness defects, one enhancement, three infrastructure, five cross-repository asks, and #20, which is delivered but still open |

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
TREXIO geometry and molecular-orbital interoperability. The product and
split-audit workstreams preserved in private operations record the earlier work.

| Release | Date | Codename | What shipped | Issues |
|---|---|---|---|---|
| v2.15.3 | 2026-09-09 | *Roothaan's Roadrunner* | First gated tag. QVF drift guard, skip audit, `[queue]` extra, VTK 9.7 test fix; documentation site and its CI; codename catalogue; `release` branch and procedure | #1, #2, #3, #5, #6, #7, #11, #12, #13, #14 |
| v2.16.0 | 2026-09-09 | *Sayle's Starling* | Product manual with real viewer figures; wordmark, favicon and the seven-image codename series; `release` extra; `trame<4` bound; QTAIM and spectrum-panel fixes | #9, #13, #20 |
| v2.16.1 | 2026-09-10 | inherits | The vibe-qc availability probe degrades instead of raising | #18 |
| v2.16.2 | 2026-09-12 | inherits | Missing-RDKit tests run in every environment; patch checklist; contributor guidance | #20 |
| v2.17.0 | 2026-09-13 | *Lorensen's Loon* | TREXIO geometry and molecular Gaussian orbitals; packaging-metadata checks; standalone agent guides | #23 |
| v2.17.1 | 2026-09-15 | inherits | Complex coefficients and Gamma-point lattice tails for MOs; scene replacement that avoids stale viewports; command-palette Escape handling during presentation | — |

## Correctness follow-ups (not included in v2.17.1)

Three correctness defects, found against 2.16.1 while the manuscript figures
were regenerated, and one warning from vibe-qc's contract run.

| Issue | Defect | Effect |
|---|---|---|
| [#24](https://github.com/vibe-qc/vibe-view/issues) | `slice` rebuilds the manifest and drops section fields and root metadata | Data loss; any slice that keeps a `run.record` fails `validate` |
| [#25](https://github.com/vibe-qc/vibe-view/issues) | The combined band structure and DOS panel applies one Fermi shift to both panels | One of the two panels is empty for spec-compliant archives |
| [#27](https://github.com/vibe-qc/vibe-view/issues) | Every scene rebuild repaints the dark background | The user's light-background choice is discarded |
| [#21](https://github.com/vibe-qc/vibe-view/issues) | `create_app` schedules `_reset_export_flag` and never awaits it | A `RuntimeWarning`; not yet diagnosed |

All four touch `src/`, so the `VIBE_VIEW_TAG` check in
[release step 3](release_process.md) applies when these follow-ups land.

Housekeeping: #20 is still open although everything it asked for shipped in
v2.16.0 and v2.16.2.

## Minor releases and remaining proposals

The "not yet" items below come from the parity roadmap, which stopped being
updated on 2026-08-25. Check each one against the code before scoping a
release around it.

### v2.17.0: *Lorensen's Loon*, TREXIO orbitals

The maintainer selected TREXIO interoperability for this release. HDF5 files
and text datasets now supply geometry and real molecular Gaussian s/p/d/f
orbitals to the existing isosurface workflow. The importer preserves units,
normalization, AO ordering and available spin/energy/occupation metadata;
the [format guide](formats.md#trexio-wavefunctions) describes its limits.

The earlier volume-rendering proposal below remains future work; it is not
part of the TREXIO release.

**Today:** marching-cubes isosurfaces for densities, orbitals and ESP;
additional isosurfaces per volume; clip planes; volume cross-fade.

**Proposed:**

- Translucent isosurfaces that do not stall the browser client. The hang was
  traced to vtk.js depth peeling, and orbitals default to opaque as a
  workaround ([design refresh](design_refresh_2026.md), Robustness).

**Future work:** the transparency change still needs scoping.

### v2.18.0: *Richardson's Robin*, biomolecules

**Today:** all of M5. The theme's headline work shipped in v2.15.2, so this
release is polish unless new scope is added.

**Proposed:**

- Isolate or hide everything outside a residue selection.
- Select residues by clicking in the viewport.
- Offer the ribbon-colour modes outside the cartoon representation.
- Distinguish alpha, pi and 3₁₀ helices, and bridges from sheets, with a
  separate ribbon profile for each. The geometric assignment cannot tell them
  apart, so producer-supplied ranges remain the source for that detail.
- Give strands crisp edges instead of an elliptical cross-section, and correct
  the end-cap normals.
- Stop reading a chain-terminal strand's last residue as coil.
- Draw hydrogen bonds in their own dashed style.

### v2.19.0: *Phong's Pheasant*, materials and lighting

**Today:** material presets, Publication, Presentation and Analysis
representation presets, toon shading, OSPRay ray tracing, and light and dark
themes.

**Proposed:**

- Publication mode: white background, thick outlines, no gizmos, one click.
- A theme and accent pass.

**Constraint:** ambient occlusion and fog are VTK render passes, and render
passes never reach the vtk.js client
(`test_ambient_occlusion_is_server_side_only`). Silhouette outlines depend on
the view, so an outline computed on the server goes stale when the client
rotates. Any of these effects therefore needs client-side work.

### v2.20.0: *Levoy's Lemur*, transfer functions and volume rendering

**Today:** nothing. vibe-view extracts isosurfaces and has no direct volume
rendering or transfer-function code.

**Proposed:**

- Direct volume rendering of QVF volume sections, with an editable colour and
  opacity transfer function.

**Measure first.** Rendering stays on Trame and PyVista
([decision 6](ROADMAP_AVOGADRO_PARITY.md)). Cartoon rendering got a payload
and interaction benchmark before it was built, and volume rendering on a
vtk.js client needs the same.

### v3.0.0: *Levinthal's Lynx*, the interactive viewer

The codename is held for a major version about the interactive viewer itself.
Nothing yet decides what would make that release a breaking change.

**Proposed:** work that needs the browser client, not geometry rebuilt on the
server:

- Continuous label billboarding with client-side followers. Today labels
  re-face the camera only when an interaction ends.
- A client-side frame-rate measurement, taken inside the page.
- A plugin API. The Python SDK is documented, but there is no plugin surface
  ([Python API](python_api.md)).

### Not yet placed

No registered codename covers these:

- An energy-window control for the band, DOS and combined panels, honouring a
  `viewer_defaults` hint:
  [#26](https://github.com/vibe-qc/vibe-view/issues).
- A general surface cell for Miller slabs, spanned by two in-plane lattice
  vectors. Orientations that are not c-normal currently raise an error.
- Metal-metal bonds in clusters and organometallics, which the bonding
  heuristic cannot see.
- More fragments and templates; thumbnails in the structure library.
- Unit toggles everywhere (Ha/eV, Å/bohr).
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

| Issue | What |
|---|---|
| [#22](https://github.com/vibe-qc/vibe-view/issues) | The `release` push reruns the full candidate gate, and the CI rules contradict the documented policy |
| [#8](https://github.com/vibe-qc/vibe-view/issues) | ruff and mypy are configured but not enforced. Re-measure rather than quote a count, and choose the lint job's scope first |
| [#19](https://github.com/vibe-qc/vibe-view/issues) | The Sphinx toolchain is pinned inline in CI and re-pins four core dependencies |
| none | Lift `trame<4` once trame-vtk and trame-vuetify accept `trame-client>=4` |
| none | The lifecycle scripts' `--help` text still describes the monorepo layout |

### Desktop distribution

Maintainer-gated. The update feed serves 2.10.0 while the source is at
2.16.2. Publishing it needs the maintainer's SSH deploy key
(`electron/PUBLISHING.md`). Linux and Windows artifacts need build hosts,
because electron-builder cannot cross-build them from macOS. Code signing
stays deferred: there is no Apple Developer ID.

### Cross-repository asks

| Issue | Owner | Ask |
|---|---|---|
| [#4](https://github.com/vibe-qc/vibe-view/issues) | vibe-qc | 17 producer/consumer tests in five files run in neither repository |
| [#15](https://github.com/vibe-qc/vibe-view/issues) | vibe-qc | `.deploy_ssh` guard messages run as shell commands |
| [#16](https://github.com/vibe-qc/vibe-view/issues) | qvf | Publish the frozen v2 schema, and fix the v1 schema's symlink description |
| [#17](https://github.com/vibe-qc/vibe-view/issues) | vibe-queue | Settle the distribution name, `vq` or `vibe-queue` |
| [#10](https://github.com/vibe-qc/vibe-view/issues) | vibe-qc-agentic-loop | `bugctl` still hardcodes project 19 |

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
