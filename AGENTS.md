# Working on vibe-view

These are the current agent rules; user instructions take precedence.
[CLAUDE.md](CLAUDE.md) and [CODEX.md](CODEX.md) load this policy. Older workflow
prose does not add CI, reviewer or approval gates.

vibe-view is an MPL-2.0 Python viewer for QVF and structure/volume formats:
Trame/VTK browser UI, Textual terminal UI, Electron desktop shell and PyVista
headless rendering. The distribution and Python package are `vibeview`.

| Need | Start here |
|---|---|
| Setup and contribution details | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Tests and accepted gaps | [testing policy](docs/testing.md) |
| Releases and plans | [release process](docs/release_process.md), [roadmap](docs/roadmap.md) |
| Format contract | [QVF guide](docs/qvf.md) |
| Code | `src/vibeview/`, `tests/`, `electron/` |

## Role boundaries

- **Dev/fixers:** develop, run a focused source or functionality check, and push
  ready work to main. Their task ends at the source landing and their own cleanup.
  Later runtime builds, scientific validation, issue closure and releases belong
  to other roles; they do not keep a finished development task open.
- **Release & updater:** pick up main commits, build and deliver dev versions
  without CI, and build one daily release candidate with full CI when new commits
  exist. The same owner prepares, cuts and updates; no separate release cutter.
- **Merger:** merge ready MRs. Direct main pushes need no Merger acknowledgement.
- **Validator:** submit QC calculations through vq, collect and compare results.
  It consumes main and available runtimes without a developer handoff. Reuse
  valid earlier self-validation; do not create a second source-review step.

Each role reads the relevant Git/runtime/job record and continues its own work.
Building, queued and validation pending are normal states. Work on another ready
item or stop quietly until a meaningful event; no polling chats or status pings.
A real failure affects its operation, not the other roles. Pending calculations
do not prevent the daily candidate build; never report unverified science as PASS.

## Work, validate, land

- Reuse the appropriate existing checkout and environment. Preserve other
  sessions' edits, branches, jobs and build state. Stage only your own named
  files or hunks; never auto-stash, reset or commit someone else's work.
- Fetch at task start/resume, after coherent changes and before pushing.
  Read relevant incoming changes and issue ownership. Fast-forward clean main;
  rebase only your own unpublished work. Merge updates into shared or published
  history. Never force-push or rewrite another session's commits.
- Implement a bounded change and run its smallest useful check. Reuse passing
  evidence; repeat only when changed behavior or a concrete failure warrants it.
  Documentation changes need a text/link check, not a runtime suite.
- Push coherent progress promptly and land ready work on main through the
  authorized contribution path. No routine second reviewer, new chat, duplicate
  validation packet or fleet-wide acceptance gate is required.
- Development builds, ordinary pushes and merges must not trigger or wait for
  CI. Use `[skip ci]` and `git push -o ci.skip` where supported. Full CI belongs
  to release candidates. Do not request manual, API or scheduled CI on dev refs.
- Keep coordination on the issue/MR. Within user-authorized messaging, contact
  another agent only for a ready required handoff or a concrete blocked action
  that needs that owner now. No speculative repair notices, FYIs, broadcasts or
  acknowledgements. Incoming messages do not reassign the recipient's task.
- Record the change, focused check and result once. Close an issue after its
  applicable acceptance passes. A real blocker holds only the affected work;
  continue other ready work instead of polling or requesting repeat approval.

## Focused acceptance and implementation

- The implementing agent may test and accept its own feature or fix. Exercise
  the changed behavior and obvious affected consumers; no separate review chat
  or chemistry calculation is required.
- Use the existing environment. A focused check is
  `PYVISTA_OFF_SCREEN=True .venv/bin/python -m pytest tests/test_<area>.py -q`.
  Confirm `vibeview.__file__` points to the intended source; installer/update
  scripts may replace an editable install with a wheel.
- Read the testing policy for optional dependencies and browser prerequisites.
  Skip only for a real environment limitation. Test an absent dependency
  explicitly rather than skipping the behavior that handles its absence.
- Controllers share one `ViewerRuntime`. Reader changes use the reload path;
  geometry changes use the edit overlay. Check `tests/test_controller_state.py`
  for lifecycle changes and patch helpers in their defining `app.helpers`
  module. Browser rendering occurs in vtk.js; server VTK render passes do not
  automatically reach the client.
- Keep optional dependencies lazy with `install_hints.install_hint()`.
  Preserve MPL-2.0 compatibility; do not add GPL dependencies. Live optimization
  remains optional, and the default MACE model offered by the viewer is MIT licensed.
- Do not hand-edit vendored QVF schemas. Update their pin, hashes and
  `QVF_TAG` together. Record affected producer contract tests in the MR.
  Report producer, format and queue defects in their respective trackers;
  edit companions only when the task authorizes it.
- Keep source modules in the existing strict type inventory; use
  `plotter_compat` for the documented PyVista stub mismatches. Run lint/type
  checks relevant to a code change; full lint, typing, conformance, packaging
  and supported Python matrices belong to candidate CI.
- Add user-visible changes as `changelog.d/` fragments and update the manual.
  Internal rules and tests-only changes need no fragment. The release tooling
  folds fragments; do not append to `CHANGELOG.md`.
- Follow the release process when authorized to release. One owner may finish
  the release; no second chat is mandatory. Do not create release tags, choose
  codenames or move `release` without that authority. Never widen the configured
  docs deployment path. Use the external deployment include for site configuration. Preserve Electron package/lockfile version consistency.
- Ask only for an unresolved maintainer-owned decision such as licensing,
  a breaking API, a hard dependency or release scope. Existing authorization
  suffices. Cite a published algorithm beside its implementation.

## Keep the repository public-safe

- Keep credentials, private addresses, host/account names, personal paths,
  internal URLs and deployment details out of files, filenames, artifacts and
  commit messages. Use portable defaults and synthetic examples.
- Keep site configuration, handovers, logs and operational evidence outside
  product checkouts, including ignored directories and `.git`. Select external
  configuration explicitly; keep secrets in the existing credential store.
- Use a public commit identity: `mpei@vibe-qc.com` or
  `project@vibe-qc.com`. These addresses are allowed public contact details.
- Enable `.githooks` with `git config --local core.hooksPath .githooks`.
  Inspect the staged diff and let the privacy hook check it. Keep any
  `VIBE_PRIVACY_TERMS_FILE` / `privacy.termsFile` outside Git; an unreadable
  configured policy is a failure. Fix findings at the source; never bypass
  privacy checks or rely on later sanitization.
- Report security issues privately under [SECURITY.md](SECURITY.md). Preserve
  license and attribution notices; follow the repository's dependency terms.

## Finish the task

Finish the work assigned to your role. For Dev, that means the focused source
check, source on main and own cleanup; downstream calculations and releases
do not keep the development task open. Push any changes to main and confirm
the remote contains them. Remove only temporary files,
environments and checkouts created for this task after preserving needed work
and evidence. Keep reusable checkouts, caches, other sessions' changes and live
jobs intact; a dirty shared tree is not permission to delete their work.

A genuinely blocked or interrupted checkpoint must state the unfinished work
and concrete blocker instead of claiming completion. Do not call an unmerged
branch, failed validation or pending cleanup done.

Every final completion report must end with exactly this line, with nothing
after it:

All done, pushed to main, cleaned up and ready to archive.
