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
| A security-relevant bug | **Email mpei@vibe-qc.com** — see [SECURITY.md](SECURITY.md). Do not open a public issue. |
| A wrong number *inside* a `.qvf` (the producer wrote it) | [vibe-qc issues](https://github.com/vibe-qc/vibe-qc/issues) |
| An ambiguity or gap in the QVF format itself | [qvf issues](https://github.com/vibe-qc/qvf/issues) |
| A queue / job-submission bug behind the vq panel | [vibe-queue issues](https://github.com/vibe-qc/vibe-queue/issues) |

When you are not sure whether the producer or the viewer is at fault, attach
the archive and file it here — moving an issue is cheap.

**A bug report that travels well** carries: the `.qvf` or structure file (or a
minimal one that reproduces it), `vibe-view --version`, `vibe-view doctor`
output, and the exact command or click sequence.

## Getting set up

```sh
git clone https://github.com/vibe-qc/vibe-view.git
cd vibe-view
./scripts/install.sh
source .venv/bin/activate
```

Or, for a plain development install:

```sh
python -m venv .venv
.venv/bin/pip install -e '.[test]'
```

`[test]` pulls the viewer, TUI, ASE, TREXIO and Jupyter extras plus pytest and scipy —
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

The command above is deliberately narrow and matches the trap row **only** —
verified by planting all three rows in a file and running it: one of three
flagged. On this repository it currently returns a single hit, and that hit is
prose in a docstring describing a gate that was removed.

The table earns its keep when you **widen** the pattern — adding
`*_AVAILABLE` / `*_INSTALLED` constants, `find_spec(...) is not None`, or a
bare `shutil.which(...)`. A wide sweep here returns **seven** hits and all
seven are benign: six are the safe `shutil.which(...) is None` form and the
seventh is that same docstring. Widen it when you want confidence there is no
variant the narrow pattern misses, then read the direction of every hit before
acting on any of them.

**The carve-out:** an *extra leg* of a test may skip when a companion package
is missing — the producer/consumer checks that need `vibeqc` do exactly that —
provided the test's **core assertions still run unconditionally**. The rule
bans hiding a whole test behind a capability nobody has, not gating an
additional check that genuinely needs one.

## QVF schemas are vendored, and pinned

`src/vibeview/schema.json` and `schema_v2.json` are vendored copies of the QVF
manifest schemas. They were symlinks into vibe-qc's tree before the split and
are real data files now, so the wheel is self-contained.

Do not edit them by hand. They are pinned by sha256 in
`scripts/check_qvf_conformance.py` (`PINNED_SCHEMAS`), checked offline by
`tests/test_qvf_schema_identity.py`, and compared against the normative copy in
the `qvf` repository by the `qvf-conformance` CI job. Re-vendoring is a
deliberate act: move the pin and bump `QVF_TAG` in `.gitlab-ci.yml` in the same
commit. **A failing pin is not a licence to update the constant** — find out
which side moved first.

## Before you open a merge request

- Run the affected lanes locally and name them. Pushes to `main` also run the
  `test`, `qvf-conformance` and `docs-build` jobs; treat that as a second
  check, not the first.
- Keep `main` release-ready: no half-finished code paths, docs in parity,
  `CHANGELOG.md` `[Unreleased]` reflecting what you actually landed.
- If your change touches one of the seven files vibe-qc's `build-test` job runs
  at `VIBE_VIEW_TAG` (`test_container_submission`, `test_details_panel_sandbox`,
  `test_in_memory_handover`, `test_job_container_lifecycle`, `test_kind_drift`,
  `test_live_opt`, `test_wavefunction_libint_parity`), say so — that pin has to
  move in the vibe-qc repository for your change to be exercised.

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
- `ruff check` with the config in `pyproject.toml`. The tree carries
  pre-existing findings; do not add new ones.
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
   project license — see [`LICENSE`](LICENSE)).
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
source checkout, with one literal term per line. Matching is case-insensitive;
a configured missing, empty or in-tree file blocks the check. Keep the private
terms file and resolved installation paths out of commits. The repository's
privacy tests load the same external policy when configured.

Site wrappers and deployment configuration are maintained separately in private
operations storage. Product changes must use portable examples and preserve the
existing generic installation and configuration APIs.
