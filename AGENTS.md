# AGENTS.md

Orientation for AI coding agents working on vibe-view: Claude Code, Codex,
Cursor, aider and others. [`CLAUDE.md`](CLAUDE.md) and [`CODEX.md`](CODEX.md)
point here, so this is the one place the rules live. Humans get the same
ground at more length in [`CONTRIBUTING.md`](CONTRIBUTING.md).

Keep this file short and current. If a rule here stops matching the
repository, fix the rule.

## Keep this repository public-safe

This product repository must remain ready for public mirroring at every commit.

- Never put private email addresses, real machine names or host aliases,
  internal hostnames or URLs, private IP addresses, account names, personal
  filesystem paths, credentials, tokens or site-specific deployment details
  in tracked files, filenames, generated artifacts or commit messages.
  This includes code, comments, tests, documentation and agent instructions.
- `project@vibe-qc.com` and `mpei@vibe-qc.com` are explicitly allowed public
  email addresses. Use generic placeholders and reserved example addresses
  for tests and documentation; do not copy real private values into fixtures.
- Keep private configuration separate from product code. Store it outside
  the product checkout on the local machine, or in the private agentic loop
  repository. Select it through environment variables, command-line options
  or an explicit external configuration path. Commit only portable defaults,
  schemas and examples without private values.
- Ignored files and custom folders under `.git` are not private configuration
  stores. Keep private operational logs, inventories and release evidence
  outside the product checkout too. Never commit secrets to the private loop
  repository; use the existing credential or secret store.
- Prevent contamination while making the change. Inspect the diff and use the
  existing automated privacy checks before committing. Fix a finding in the
  source; do not rely on a later sanitizer or create sanitation chats for
  routine releases. Never bypass a privacy failure to publish a release.

## What vibe-view is

A pure-Python viewer for QVF archives (`.qvf`) and common structure and volume
formats. It renders four ways:

- **Browser:** Trame/Vuetify over VTK.
- **Terminal:** Textual.
- **Desktop:** an Electron shell around the browser viewer.
- **Headless:** PyVista off-screen, for figures and exports.

It is a companion to [vibe-qc](https://github.com/vibe-qc/vibe-qc)
and runs without it.

On 2026-09-08 vibe-view was split out of the vibe-qc monorepo into its own
repository, `vibe-qc/vibe-view`. The brand is `vibe-view`;
the package and distribution are `vibeview`. The licence is MPL-2.0.

## Where things live

```
src/vibeview/
  app.py            browser viewer: layout, state, controllers (~15k lines; grep, don't read it whole)
  cli.py            the `vibe-view` command
  qvf.py            QVFReader, StructureData, archive reading and validation
  renderers/        structure, volume, wavefunction, spectra, bands and DOS
  tui/              terminal mode
  converters.py     PDB, CIF, XYZ, ASE and SMILES to QVF
  live_opt.py       background geometry optimization (needs vibeqc)
  file_watcher.py   hot reload
  codenames.py      release codename catalogue
  install_hints.py  install messages for optional extras
  schema.json, schema_v2.json   vendored QVF manifest schemas (pinned)
tests/              pytest suite, about 100 files
docs/               Sphinx site, published from `release`
electron/           desktop shell; holds three of the four version sites
scripts/            install and update lifecycle, release artifacts, QVF conformance
```

| Looking for | Start at |
|---|---|
| What is planned | [`docs/roadmap.md`](docs/roadmap.md) |
| How a release is cut | [`docs/release_process.md`](docs/release_process.md) |
| Open defects | [the issue tracker](https://github.com/vibe-qc/vibe-view/issues) |
| The QVF format | [`docs/qvf.md`](docs/qvf.md) and the [qvf repository](https://github.com/vibe-qc/qvf) |
| Contributor detail: skips, schemas, commit style | [`CONTRIBUTING.md`](CONTRIBUTING.md) |

## Setup, tests and docs

```sh
python -m venv .venv
.venv/bin/pip install -e '.[test]'

PYVISTA_OFF_SCREEN=True .venv/bin/python -m pytest tests/test_<area>.py -q   # what you touched
PYVISTA_OFF_SCREEN=True .venv/bin/python -m pytest tests -q                 # everything, 10-20 min
```

The Sphinx toolchain is not in any extra yet (#19). To build the docs,
install the pins listed under `.docs_deps` in `.gitlab-ci.yml` into a
separate venv, then run `cd docs && make strict`.

- **Check which tree you are testing.** Run
  `python -c "import vibeview; print(vibeview.__file__)"`. Both
  `scripts/install.sh --force` and `scripts/update-desktop.sh` replace an
  editable install with a wheel. After that, a green run says nothing about
  your working tree.
- **Some skips are expected.** `[test]` does not install vibe-qc, so the
  tests that need it skip. The served-browser tests need Chrome: they run
  locally and skip in CI.

## Ground rules

These are the few rules whose mistakes are hard to undo. Everything else is
judgment; see [Defaults](#defaults).

1. **Never force-push `main` or `release`, and never rewrite pushed
   history.**
2. **Releases follow `docs/release_process.md`.** Create a `v*` tag or move
   `release` only when you are cutting a release the maintainer asked for.
   Codenames are the maintainer's decision.
3. **Keep private data out of the tree.** No credentials, tokens, real IP
   addresses, home-directory paths or employer names. Enable the hook once per
   clone with `git config --local core.hooksPath .githooks`. If it blocks a
   commit, fix the content. Bypass it only for a reviewed exception, and say
   why in the commit message.
4. **Licensing.** vibe-view is MPL-2.0, so new dependencies must be compatible
   with it: no GPL. Add a dependency as an optional extra with a lazy import
   and an install hint (`install_hints.install_hint()`), not to core.
5. **Never hand-edit the vendored QVF schemas.** They are pinned by sha256. To
   re-vendor, move the pin and `QVF_TAG` in the same commit.
6. **Never widen the docs `DEPLOY_PATH`.** vibe-qc.com has several
   publishers, and an rsync `--delete-after` on a wider path deletes their
   sites.

## Defaults

This is how work normally goes. Use judgment when a case doesn't fit.

- **Test what you touched**, plus its obvious consumers. Run the full suite
  when you change shared machinery (reader lifecycle in `app.py`, `qvf.py`, a
  renderer many panels use) or before a release. Name the tests you ran in the
  commit message or merge request.
- **CI is a second check, not the first.** Pushes to `main` run `test`,
  `qvf-conformance` and `docs-build`, and `release-candidate/*` branches run
  the release gate. #22 tracks tidying these rules.
- **Keep `main` working.** Land in small increments, and gate unfinished
  features rather than leaving half-wired code paths.
- **CHANGELOG.** Add user-visible changes to `[Unreleased]`. Changes that
  only touch tests or internals may skip it.
- **Issue numbers.** When a commit fixes a tracked issue, put `(#N)` in the
  subject, because that is how triage finds fixes. Untracked work needs no
  number.
- **Docs follow behaviour.** When you change what a user sees, update the
  manual page in the same change.
- **Ask the maintainer when the decision is theirs:** licensing, breaking a
  public API, a new hard dependency, release scope or codenames, or anything
  that commits another repository. Otherwise, go ahead and explain your
  reasoning in the commit.

## Working alongside other sessions

Several agent sessions may work on vibe-view at once, sometimes in the same
checkout. They all commit under the same git identity, so authorship cannot
tell you who changed what.

- **Work in your own clone or worktree** when you can.
- **Treat a shared checkout's uncommitted changes as someone else's.**
  - Stage files by name; never `git add -A` or `git commit -a`.
  - Don't stash, reset or check out over changes you didn't make.
  - Pull with `git -c rebase.autoStash=true pull --rebase origin main`.
- **Keep both sides of a conflict** in files several sessions append to, such
  as `CHANGELOG.md` and the handovers.
- **Handovers are optional and private.** Keep operational coordination and
  release evidence in the private operations repository or external private
  storage, never in this checkout (including ignored folders or `.git`).
  Product documentation and defects belong in the manual and issue tracker.

## Other repositories

vibe-view consumes QVF; it doesn't own the producer or the format.

- **Report problems where they belong.** A wrong number inside a `.qvf` goes to
  vibe-qc, a format gap to qvf, queue behaviour to vibe-queue. The table under
  "Where to report what" in `CONTRIBUTING.md` has the links.
- **Don't edit another repository from here.** File an issue in its tracker,
  or here with a `[for <repo>]` title, as #4 and #15-#17 do.
- **Flag changes to vibe-qc's contract tests.** vibe-qc's CI runs seven of this
  repository's test files at a pinned tag (`VIBE_VIEW_TAG`). If you change one
  of them, or `create_app`, say so, so the pin can move.
- **Degrade cleanly without vibe-qc.** Live optimization and container
  submission import `vibeqc` when it is present. The MACE engine is vibe-qc's
  approved MLIP exception, and vibe-view offers only its MIT-licensed default
  model.

## Notes that save time

- **The browser viewer renders client-side in vtk.js.** VTK render passes
  such as SSAO and fog never reach the client, and view-dependent geometry
  goes stale as soon as the client rotates.
- **Controllers in `app.py` share closure state.** Reader swaps go through
  the reload path (`_reload_active_file`), and geometry edits through the
  reader's edit overlay. Read `tests/test_controller_state.py` before changing
  that lifecycle.
- **Import optional extras lazily**, inside the function that needs them.
  Catch `ImportError` and raise with an install hint.
- **Skip only for a real environment gate.** A test for a missing optional
  dependency forces the absent branch instead:
  `monkeypatch.setitem(sys.modules, "rdkit", None)`. `CONTRIBUTING.md`
  explains why.
- **ruff is configured in `pyproject.toml`.** The tree carries pre-existing
  findings (#8), so don't add new ones. Counts depend on the invocation, so
  always quote the command beside the number.
- **Cite published algorithms in code.** When you implement one, name the
  paper in a comment beside the code, as the secondary-structure and ribbon
  code does.

## What changed from the monorepo rules

Older comments, handovers and audits cite "CLAUDE.md §N". That means the
vibe-qc monorepo's file, archived with the monorepo. Its rules were written for
dozens of parallel sessions on a native-code project, and most of them don't
carry over:

| Monorepo rule | Here |
|---|---|
| `bugctl` claims, leases and mandatory independent verification | Plain issues. Ask for a second review when a fix is risky. |
| A fixed per-topic clone registry, and drop-box status files per release | Any clone or worktree. The roadmap and CHANGELOG carry release status. |
| Issue number in the subject was non-negotiable; `Patch-candidate:` trailers | An issue number when there is one. Propose patch candidates in the issue. |
| A full definition-of-done checklist before every push | Test what you touched and keep `main` working. |
| A persistent handover file for every chat | Only for workstreams that span sessions. |
| Release operations owned by one designated release chat | Whoever the maintainer asks, following `docs/release_process.md`. |
| No refactors without approval | Propose large refactors in an issue first; small cleanups are fine. |
| Citation database, literature library, `vibeqc.output`, fleet layout, `basissetdev` | vibe-qc's rules. They don't apply to the viewer. |
