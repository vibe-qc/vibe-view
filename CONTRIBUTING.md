# Contributing to vibe-view

vibe-view is the QVF viewer: it reads
[QVF](https://github.com/vibe-qc/qvf) archives and a range of
structure and volume formats, and renders them in a browser, a terminal, a
desktop window, or headlessly. It is a companion to
[vibe-qc](https://github.com/vibe-qc/vibe-qc) and works without it.

## Where to report what

| What | Where |
|---|---|
| A viewer bug, a file that will not open, a rendering defect | [vibe-view issues](https://github.com/vibe-qc/vibe-view/issues) |
| A security-relevant bug | **Email mpei@vibe-qc.com** — see [SECURITY.md](https://github.com/vibe-qc/vibe-view/blob/main/SECURITY.md). Do not open a public issue. |
| A wrong number *inside* a `.qvf` (the producer wrote it) | [vibe-qc issues](https://github.com/vibe-qc/vibe-qc/issues) |
| An ambiguity or gap in the QVF format itself | [qvf issues](https://github.com/vibe-qc/qvf/issues) |
| A queue / job-submission bug behind the vq panel | [vibe-queue issues](https://github.com/vibe-qc/vibe-queue/issues) |

When you are not sure whether the producer or the viewer is at fault, attach
the archive and file it here — moving an issue is cheap.

**A bug report that travels well** carries: the `.qvf` or structure file (or a
minimal one that reproduces it), `vibe-view --version`, `vibe-view doctor`
output, and the exact command or click sequence.

## Getting set up

Clone the public source repository:

```sh
git clone https://github.com/vibe-qc/vibe-view.git
```

Enter the standalone repository and install:

```sh
cd vibe-view
./scripts/install.sh
source .venv/bin/activate
```

GitLab remains the canonical development service. Maintainers provide its
connection details separately to authorized contributors. Public snapshots can
lag development; check the available public tags when selecting a release.

Or, for a plain development install:

```sh
python -m venv .venv
.venv/bin/pip install -e '.[test]'
```

`[test]` pulls the viewer, TUI, ASE, TREXIO and Jupyter extras plus pytest, scipy and pyyaml —
everything the suite needs. vibe-qc is deliberately **not** installed: it would
pull a native libint build, and the tests that need a real producer skip via
`importorskip`. That is expected (see "Tests that skip" below).

## Running the tests

```sh
PYVISTA_OFF_SCREEN=True python -m pytest tests -q
```

The full suite takes 10-20 minutes. For ordinary work, run the lanes your
change touches and say which ones in the commit message.

A few tests read real producer archives from the **QVF conformance corpus**,
which lives in the `qvf` repository. Point `QVF_CORPUS` at it, or keep a `qvf`
checkout where `tests/_qvf_corpus.py` looks:

```sh
git clone https://github.com/vibe-qc/qvf.git ../qvf
QVF_CORPUS=../qvf/conformance/corpus python -m pytest tests -q
```

The [QVF GitHub mirror](https://github.com/vibe-qc/qvf) provides the same
standalone format repository.

### Where a test goes

One file per module or feature, named for it: `test_settings.py` for
`vibeview.settings`, `test_file_watcher.py` for hot reload. Do not start a
file named for a milestone, a release or a batch (`_extra`, `_final`,
`_v2`, a test count, or a work item from another tracker). Nobody adding a
test later can tell which of those files it belongs in, and they end up
duplicating the area files. A regression for an issue in this tracker goes
in the area file too. When one change spans several areas,
`test_issue<N>_<area>.py` keeps its rows together and traceable.

Build archives with `tests/_qvf_builders.py` or the fixtures in
`tests/conftest.py` rather than another local builder, and write files under
`tmp_path`. The conftest points `tempfile` and the XDG cache and config
directories at pytest's temporary tree for the whole run, so a test never
writes into the system temp directory or the account running it.

Check behaviour, not source text: the state a controller leaves, or the page
the client is served (`tests/_served.py`). The few contracts about the shape
of the `vibeview.app` package itself live in `tests/test_structural_contracts.py`
and parse the source with `ast`. A guard that has to read source text, such as
"no colour literals", reads it through `tests/_sources.py`, which covers every
module of the package, so it keeps working when code moves between them.

### Tests that skip, and tests that must not

Skips are load-bearing here, so the suite distinguishes two kinds:

* **Environment gates** are legitimate: `importorskip("vibeqc")` (the producer
  is not installed), `importorskip("rdkit")` (the `[smiles]` extra), a missing
  Chrome for the served-DOM lane.
* **Everything else should fail, not skip.** A test guarded on a fixture this
  suite synthesizes, or on a file that no environment provides, retires itself
  silently — and a skip passes forever. Three such tests were found and fixed
  after the 2026-09 split (issue #3). If you are about to write
  `pytest.skip(...)`, check that some real environment actually runs it.

CI sets `VIBEVIEW_REQUIRE_QVF_CORPUS=1` so that in the lane which clones the
corpus on purpose, a missing corpus is an **error** rather than a skip.

Some lanes need something the CI image does not carry, so they run only where
a contributor provides it. Run the one your change touches before you push,
and say so in the merge request:

| Lane | Needs | Runs in CI |
|---|---|---|
| Producer parity: `test_details_panel_sandbox`, `test_job_container_lifecycle`, `test_live_opt`, `test_trexio_vibeqc_parity`, `test_wavefunction_libint_parity` | vibe-qc installed in the same environment | No here. vibe-qc's CI runs the pinned contract files at `VIBE_VIEW_TAG`. |
| Native relocalization backend in `test_relocalize` | `VIBEQC_RELOCALIZE_PYTHON` naming an interpreter that has vibe-qc | No |
| Served-DOM accessibility in `test_design_refresh` | Chrome or Chromium, and Node with `fetch` and `WebSocket` | No |
| TREXIO cross-check in `test_trexio_import` | Node | No |
| TREXIO round trips in `test_trexio_import` | a `trexio` wheel for the interpreter, which the newest Pythons can lack | Yes |
| Timing budgets in `test_benchmarks` | `-m benchmark`; the default run deselects them | No |
| SMILES with RDKit | the `[smiles]` extra, which `[test]` includes | Yes |

### Tests for optional dependencies must run in every environment

**Never gate a test on a dependency being _present_.** A test marked
`skipif(HAVE_SOMETHING)` runs only where that package is missing — which is
usually nobody's machine, or only CI, or only a contributor's. Nobody reads
its failures, so it can be broken for months while looking green.

That is not hypothetical here. Two `test_smiles.py` classes carried
`skipif(HAVE_RDKIT)`, and both contained a broken assertion for the whole life
of the split (2026-09). They asserted `"vibe-view[smiles]" in msg` against a
remediation that is deliberately path-based for a source checkout — so they
were really checking *where the repository sat on disk*, not whether the
message helped a user. They skipped on any machine with RDKit, and passed in
CI only because `$CI_PROJECT_DIR` happens to be named `vibe-view`.

Force the branch instead. Masking the module makes the import fail whether or
not the package is installed:

```python
def test_error_names_the_extra(self, monkeypatch):
    monkeypatch.setitem(sys.modules, "rdkit", None)   # import rdkit now raises
    with pytest.raises(ValueError) as exc:
        smiles_to_qvf("CCO")
```

Check before you push — and note the **positive control**, because an empty
result from a pattern you have not tested is worth nothing:

```sh
# 1. prove the pattern can find the shape, using a throwaway file
echo '@pytest.mark.skipif(HAVE_X, reason="x")' > /tmp/ctl.py
grep -nE 'skipif\((HAVE_|HAS_|_HAS_|_HAVE_)[A-Z_]*[,)]' /tmp/ctl.py   # must match

# 2. then run it for real
grep -rnE 'skipif\((HAVE_|HAS_|_HAS_|_HAVE_)[A-Z_]*[,)]' tests/
```

**Read the hits; a non-empty result is not by itself a finding.** The
pattern is deliberately wide, so it matches safe code too. Check the
*direction* of each condition:

| Condition | Runs when | Verdict |
|---|---|---|
| `skipif(HAVE_RDKIT, ...)` | RDKit is **absent** | **the bug** — hides where nobody looks |
| `skipif(shutil.which("git") is None, ...)` | git is **present** | fine — runs wherever the dependency exists |
| `skipif(not HAVE_RDKIT, ...)` | RDKit is **present** | fine — the ordinary "needs the extra" gate |

Only a condition that is *true when the dependency is present* hides
anything.

The command above is deliberately narrow and matches the trap row **only**,
which planting all three rows in a file confirms: one of three is flagged. Its
one hit on this repository is prose in a `test_smiles.py` docstring that
describes a gate which was removed.

The table earns its keep when you **widen** the search. A grep misses a
condition split over several lines, so list every `skipif` condition in the
suite from the syntax tree instead:

```sh
python - <<'PY'
import ast, pathlib
for path in sorted(pathlib.Path("tests").glob("*.py")):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("skipif") and node.args:
            print(f"{path}:{node.lineno}: {ast.unparse(node.args[0])}")
PY
```

Then read the direction of every line before acting on any of them. When this
section was last checked, every condition was the safe kind: a tool or
producer that is `None` or `not` importable, a missing `/bin/bash`, a
non-root account, or a corpus member that is absent. A new
`find_spec(...) is not None` or `HAVE_X` line is the trap.

**The carve-out:** an *extra leg* of a test may skip when a companion package
is missing — the producer/consumer checks that need `vibeqc` do exactly that —
provided the test's **core assertions still run unconditionally**. The rule
bans hiding a whole test behind a capability nobody has, not gating an
additional check that genuinely needs one.

## Building the documentation

```sh
.venv/bin/pip install -e '.[docs]'
cd docs && make strict
```

`[docs]` is the Sphinx toolchain, and the editable install is what lets
autodoc import `vibeview` for the SDK reference. `.gitlab-ci.yml`'s
`.docs_deps` installs the same extra, so a local build uses the pins the
published site was rendered with — there is no second list to keep in step.
`scripts/build_site.sh` wraps the same `sphinx-build` and adds `robots.txt`,
`sitemap.xml` and `.build-info`; run it to reproduce the live site exactly.

`make strict` builds with `-W`, which is how a broken cross-reference gets
caught here rather than on the published site. The CI build deliberately does
not use `-W`: a warning must not block a release.

## QVF schemas are vendored, and pinned

`src/vibeview/schema.json` and `schema_v2.json` are vendored copies of the QVF
manifest schemas. They were symlinks into vibe-qc's tree before the split and
are real data files now, so the wheel is self-contained.

Do not edit them by hand. They are pinned by sha256 in
`scripts/check_qvf_conformance.py` (`PINNED_SCHEMAS`), checked offline by
`tests/test_qvf_schema_identity.py`, and compared against the normative copy in
the `qvf` repository inside the Python 3.13 `test` job. Re-vendoring is a
deliberate act: move the pin and bump `QVF_TAG` in `.gitlab-ci.yml` in the same
commit. **A failing pin is not a licence to update the constant** — find out
which side moved first.

## What CI runs, and where

See [test inventory and policy](https://vibe-qc.com/vibe-view/docs/testing.html). Routine MRs run Ruff and the
fixed core Python smoke sample. QVF corpus conformance, full suites on
both Python versions, mypy and wheel/sdist/installed-wheel checks run on candidates,
manual web pipelines and schedules. Main/release pushes build docs without a
repeated runtime gate. No new schedule is installed automatically.

## Before you open a merge request

- Run the affected lanes locally and name them. Your merge request runs
  `lint` and fast `test` (including QVF conformance) too, but treat that as a
  second check, not the first: it is the last gate before the change is on
  `main`, not a substitute for having run the thing you changed. `ruff check .`
  and `mypy` take seconds and catch the two that fail
  first.
- Keep `main` release-ready: no half-finished code paths, docs in parity,
  and a changelog fragment for each user-visible change (see "Changelog
  fragments" below).
- If your change touches one of the seven files vibe-qc's `build-test` job runs
  at `VIBE_VIEW_TAG` (`test_container_submission`, `test_details_panel_sandbox`,
  `test_in_memory_handover`, `test_job_container_lifecycle`, `test_kind_drift`,
  `test_live_opt`, `test_wavefunction_libint_parity`), say so — that pin has to
  move in the vibe-qc repository for your change to be exercised.

## Changelog fragments

Record a user-visible change as its own file in `changelog.d/`, named
`<slug>.<type>.md`. The slug is usually the issue number and a word or two,
such as `57-cell-labels`; the type is one of `added`, `changed`,
`deprecated`, `removed`, `fixed` or `security`. The text is one paragraph,
written for a user, ending in the issue numbers:

```text
changelog.d/57-cell-labels.fixed.md
```

Do not edit `## [Unreleased]` in `CHANGELOG.md` directly. Parallel merge
requests that each append to one section conflict on every landing; one file
per change never does. At release time `scripts/fold_changelog.py` moves the
fragments into `[Unreleased]` under their headings and deletes them
(`docs/release_process.md`, step 3). `python scripts/fold_changelog.py
--check` validates the names, and `tests/test_changelog_fragments.py` runs
that check in the suite. Changes that only touch tests or internals need no
fragment.

## Pre-commit hook (one-time setup)

After cloning, point git at the tracked `.githooks/` directory:

```sh
git config --local core.hooksPath .githooks
```

`pre-commit` refuses staged additions containing absolute paths into a
developer's home directory or other personal-info patterns. vibe-view is
destined to be public; a home path in a docstring or a test fixture is a
privacy leak that no test catches.

Keep the path **relative**. An absolute path bakes the checkout location into
the config, and git skips a missing hooks directory **silently** — no warning,
no error — so the guard goes inert without anyone noticing.

To confirm the hook runs without making a commit:

```sh
git hook run pre-commit
```

To bypass it for a reviewed exception, commit with `--no-verify` and explain
why in the commit message.

## Code style

- **Python 3.11+.** PEP 8, four-space indent. Start every module with
  `from __future__ import annotations`. Type hints on public API; local
  helpers can skip them.
- `ruff check .` with the config in `pyproject.toml`. The whole checkout is
  clean, including the `docs/*.py` scripts and the `.githooks/` helper, and the `lint` CI job keeps it that way, so a
  new finding fails the pipeline. Quote the command beside any count you
  report: a narrower path list reads a different number.
- `mypy` with no arguments checks every source module under `strict = true`,
  and the `types` CI job keeps them clean. `[tool.mypy].files` lists them all,
  and `tests/test_type_ramp.py` fails when a new source module is added
  without joining the list. `vibeview.app` alone relaxes
  `disallow_untyped_decorators`, because trame's decorators are untyped (#33).
  The test suite is not type-checked; `mypy tests` shows what it would say.
  `python_version` is `3.12`, not the `3.11` support floor: numpy's bundled
  stubs use `type` statements, which mypy rejects below 3.12, and the stub
  syntax error aborts the run before any of our code is checked.
- Prefer editing existing modules over introducing new ones. If a new file is
  the right choice, follow its neighbours' layout.
- **Imports of optional extras are lazy**, inside the function that needs them,
  with an `ImportError` branch that tells the user how to install the extra —
  see `install_hints.install_hint()`. A module-scope import of an optional
  dependency makes the whole viewer need it.

## Commit messages

- Imperative mood, first line under 72 characters.
- **A commit that fixes a tracked issue carries its iid in the subject line**,
  e.g. `fix(qvf): reject a manifest whose member sha256 disagrees (#42)`. Not
  in the body, not in a trailer — the subject, because that is what
  `git log --oneline` shows and what a triage sweep greps.
- Longer rationale in the body when the *what* does not explain itself.
- Co-author trailers are fine for pair work.
- Do not tag `vX.Y.Z` or push to protected refs; releases are cut separately.

## What we will not accept (for now)

- API changes that break public signatures without a deprecation path.
- New **hard** dependencies without prior discussion — open an issue first. A
  new optional extra with a lazy import and an install hint is a much easier
  conversation.
- Changes that regress the suite without a stated rationale and a plan to
  restore.
- Hand-edited copies of the QVF schemas.

## Licensing

By submitting a patch, merge request, or any other contribution to vibe-view,
you agree that:

1. Your contribution is licensed under the Mozilla Public License 2.0 (the
   project license — see [`LICENSE`](https://github.com/vibe-qc/vibe-view/blob/main/LICENSE)).
2. You grant the project owner (Michael F. Peintinger) the right to relicense
   your contribution under alternative terms, including a future commercial
   license, alongside the MPL 2.0 public license. You retain copyright.

This is a lightweight alternative to a formal Contributor License Agreement.
If you are not comfortable with (2), please open an issue before contributing
so we can discuss.

vibe-view is pure Python. Its runtime dependencies —
[VTK](https://vtk.org/)/[PyVista](https://pyvista.org/) (BSD-3),
[trame](https://kitware.github.io/trame/) (Apache-2.0),
[matplotlib](https://matplotlib.org/) (PSF-based),
[plotly](https://plotly.com/python/) (MIT),
[numpy](https://numpy.org/) (BSD-3),
[click](https://click.palletsprojects.com/) (BSD-3),
[pydantic](https://docs.pydantic.dev/) (MIT) and
[jsonschema](https://python-jsonschema.readthedocs.io/) (MIT) — are all
MPL-compatible, as are the optional
[ase](https://gitlab.com/ase/ase) (LGPL-2.1+, imported not linked),
[TREXIO](https://trex-coe.github.io/trexio/) (BSD-3),
[rdkit](https://www.rdkit.org/) (BSD-3) and
[textual](https://textual.textualize.io/) (MIT) extras.

### Private privacy policy

The portable guard checks home paths without storing real user names. Operators
can add private literal terms through `VIBE_PRIVACY_TERMS_FILE` or the clone-local
`privacy.termsFile` Git setting. Use an absolute path to a UTF-8 file outside the
Git repository (including worktrees and object stores), with one literal term
per line. Matching is case-insensitive;
a configured missing, empty or in-tree file blocks the check. Keep the private
terms file and resolved installation paths out of commits. The repository's
privacy tests load the same external policy when configured.

Site wrappers and deployment configuration are maintained separately in private
operations storage. Product changes must use portable examples and preserve the
existing generic installation and configuration APIs.

### Private operational state

Keep release briefs, host configuration, deployment targets, private guard terms
and operational evidence outside every product checkout, including `.git` and
ignored directories. Credentials stay in the existing secret stores. Private
operator records use `VIBE_PRIVATE_ROOT` when set (an absolute external path),
otherwise `$XDG_STATE_HOME/vibe-private` or `~/.local/state/vibe-private`.
State directories must be caller-owned with mode 0700; records use mode 0600.
Relative paths, Git worktrees/object stores and symlink interfaces are refused.
Ordinary virtualenvs and generated build artifacts keep their existing paths.

CI is portable by default. An authorized deployment operator can set
`VIBE_VIEW_CI_PROJECT`, `VIBE_VIEW_CI_REF` (an immutable commit), and
`VIBE_VIEW_CI_FILE` in the canonical project's CI settings to add private jobs.
Without these settings only product validation runs; incomplete or invalid
configured includes fail CI validation. `QVF_REPOSITORY_URL` can select an
externally configured corpus source; the default is the public QVF repository.
Neither source files nor the public mirror contain the private settings.
