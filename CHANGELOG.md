# Changelog

All notable changes to vibe-view are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This file starts at the first public commit. Development before `9585e28`
(tagged `v2.15.2`) took place in a private monorepo whose history was
deliberately not transferred — see the [README](README.md#history).

## [Unreleased]

### Changed
- Public source is usable without private URL rewriting or omitted agent guides.
  Site CI settings and release coordination stay outside the product repository.
- Installer launcher records use external private state with verified migration
  from the legacy checkout record; ordinary virtualenv locations are unchanged.


### Fixed

- Installation, quickstart and desktop tutorials now begin with exact clone
  commands: deploy-key SSH access to GitLab on port 26, or the GitHub mirror.
  README and contributor setup use the same options. (#20)

- Clone, install and desktop help now use the standalone repository root;
  wheel staging stays inside the viewer checkout and repair URLs use its
  own documentation subtree. Installation pages identify the GitHub mirror
  and distinguish available source/CI downloads from unpublished website
  wheels. (#20)

- Desktop builds now retain artifacts locally; operators publish feeds with separate private deployment tooling.


### Maintenance: separate product source and private operations

Site-specific provisioning scripts, deployment configuration and operator
records move to private operations storage. Portable product installers remain
in source; private helpers are installed independently. Contributor privacy
checks support an external private terms file without publishing its contents.


### Maintenance

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
  catalogue by test. The `## [2.15.2]` header carries the codename too. (#13)
- A real `.gitignore`. 100 of 373 tracked files were build output — including
  a stale full copy of the package under `build/lib/vibeview/` — and
  `scripts/build.sh` deletes exactly those directories, so the repository's own
  build removed 92 tracked files. Untracked, not deleted; 373 -> 273. (#2)

## [2.15.2] - 2026-09-08 - *Roothaan's Roadrunner*

First public commit (`9585e28`), split out of the private `vibe-qc` monorepo
along with vibe-qc, vibe-queue, qvf and vibe-qc-agentic-loop. The QVF manifest
schemas, previously symlinks into the producer's tree, became vendored data
files so the built wheel is self-contained.

[Unreleased]: https://github.com/vibe-qc/vibe-view/blob/main/CHANGELOG.md
[v2.17.1]: https://github.com/vibe-qc/vibe-view/blob/main/CHANGELOG.md
[v2.17.0]: https://github.com/vibe-qc/vibe-view/tree/v2.17.0
[v2.16.2]: https://github.com/vibe-qc/vibe-view/tree/v2.16.2
[v2.16.1]: https://github.com/vibe-qc/vibe-view/tree/v2.16.1
[v2.16.0]: https://github.com/vibe-qc/vibe-view/blob/main/CHANGELOG.md
[v2.15.3]: https://github.com/vibe-qc/vibe-view/blob/main/CHANGELOG.md
[2.15.2]: https://github.com/vibe-qc/vibe-view/blob/main/CHANGELOG.md
