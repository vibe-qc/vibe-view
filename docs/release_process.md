# Release process

vibe-view is independently versioned. Development changes land on the canonical
GitLab main branch through the existing contributor and review process.
Released tags are immutable. Do not rewrite published history or move a
release tag to repair documentation or packaging metadata.

## Contributor evidence

Follow the repository's CONTRIBUTING.md and developer test guidance. A change
needs the relevant tests and validation comparisons, with commands, selected
source revision and results recorded for review. A release blocker or a fix to
an incorrect numerical result requires an independent check before closure.
Record a user-visible change as a changelog fragment, one file per change:
`changelog.d/<slug>.<type>.md`, where `<type>` is `added`, `changed`,
`deprecated`, `removed`, `fixed` or `security` and the text is one paragraph
ending in the issue numbers. Fragments never conflict between merge requests,
which a shared `[Unreleased]` section did. They are folded into
`CHANGELOG.md` when a release is cut (step 3 below). Preserve released
changelog sections.

AI-assisted changes follow the same disclosure, attribution, licensing and
validation requirements as other contributions. Assistance does not replace
review or evidence. See the existing contributor policy for the full contract.

## Release checklist

The release owner cuts a release only when the maintainer asks for one, and
the maintainer chooses the version and the codename. Everything below lands in
one commit on a `release-candidate/vX.Y.Z` branch taken from `main`.
`tests/test_version_sites.py` and `tests/test_release_codenames.py` fail if
any two of these sites disagree.

1. **Version.** Set `X.Y.Z` in each of these files:
   - `pyproject.toml`: `version`.
   - `src/vibeview/__init__.py`: `__version__`, and the codename in its
     trailing comment.
   - `CITATION.cff`: `version`, and `date-released` set to the release date.
   - `electron/package.json`: `version`.
   - `electron/package-lock.json`: both `version` entries, the top-level one
     and the one under `packages[""]`.
2. **Codename.** For a new minor release, add the `X.Y.0` entry to
   `RELEASE_CODENAMES` in `src/vibeview/codenames.py` with its rationale
   comment, set `FALLBACK_CODENAME` in `electron/main.js`, and add the row to
   the catalogue table in [the codename catalogue](codenames.md). A patch
   release inherits its minor's name, so this step is skipped.
3. **Changelog fragments.** Run `python scripts/fold_changelog.py`. It moves
   every `changelog.d/` fragment into `## [Unreleased]` under its `###`
   heading and deletes the fragment files. Edit the folded entries into
   release notes if they need it, and write the release's summary paragraph.
4. **Changelog heading.** Rename `## [Unreleased]` to
   `## [vX.Y.Z] - YYYY-MM-DD - *Codename*`, add a new empty
   `## [Unreleased]` above it, and add the `[vX.Y.Z]` tag link to the link
   list at the end of the file.
5. **Local proof.** Run `ruff check .`, `mypy`, the full test suite, and
   `make strict` in `docs/`. Then run
   `python scripts/build_release_artifacts.py --output-dir dist`, which builds
   the wheel and sdist twice and checks that they are reproducible.
6. **Candidate pipeline.** Push the `release-candidate/vX.Y.Z` branch. Its
   pipeline runs every gate job, the Python 3.11 lane included, and builds the
   docs strictly. The artifacts that pipeline builds are the ones to publish.
7. **Tag and publish.** When the candidate pipeline is green, the release
   owner tags `vX.Y.Z` at the candidate commit, fast-forwards `release` onto
   it, and runs the publication steps below. The version bump reaches `main`
   through a merge request, like any other change.

## Maintainer publication

The release owner selects scope and version only after the required gates
pass. The agentic loop coordinates the source tag and supported publication
jobs. Site-specific commands, accounts, destinations and gate-operation
runbooks are maintained in the private operations repository. Contributor
source archives do not contain or install those operational scripts.

The gate runs once, on the candidate. A `release-candidate/*` pipeline proves
the exact commit a tag will name; `release` then fast-forwards onto that same
commit, and its own pipeline builds and publishes the documentation rather
than repeating the suite against an identical tree. To repeat qualification,
start a manual pipeline on the candidate branch; every candidate trigger runs
the full gate. Development refs never create CI, including web, scheduled and
API requests, and dev builds never wait for a pipeline. A manual pipeline on
`release` offers documentation publication only. `CONTRIBUTING.md` has the rule
matrix.

A public source snapshot has its own commit identity and records the original
source revision and per-file checksums. It contains no private development
ancestry. A correction is published as a new snapshot or patch release;
existing source tags remain unchanged. Public availability is established by
successful anonymous access and the publication verification report.

## Platform packages

See [distribution builds](distribution.md) for Linux supporter packages and paid
Apple/Microsoft Store editions, runtime requirements and channel acceptance.
