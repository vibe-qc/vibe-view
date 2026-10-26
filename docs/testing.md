# Tests, builds and fast feedback

Routine development uses focused local checks and never triggers or waits for
CI. Full validation is required for release candidates and remains available
locally for a deliberate run.

## Inventory

The pre-change snapshot at `6890700c6a32` (2026-09-26) has **172 test files and
2,506 test definitions**. Local collection produced **3,989 parameterized cases:
3,980 selected and nine benchmark cases deselected**. Optional dependencies affect
collection/skips. The new CI-scope tests are additional to this baseline.

The routine smoke inventory now executes **79 cases in six files**, down from
293 in the previous sample. A local run with rendering/optional imports blocked and external pytest plugins
disabled passed in 6.51 seconds. Nine cell cases were also removed from the
sample because one imports the VTK rendering stack. The previous MR test job took 172.59 seconds including
setup. These are observed runs, not a controlled equivalent-host speedup test.

| Kind | What is actually exercised |
|---|---|
| Unit/contract | Units, kinds, cells, bonds, configuration, archive metadata/schema, code and release metadata guards |
| Reader/API integration | Real temporary QVF/ZIP files, payloads/checksums, ownership/closing, corruption handling, conversion and CLI behavior |
| Rendering/application integration | VTK/PyVista objects and offscreen renders, controller state, browser/TUI behavior, geometry and volume transformations |
| External/optional integration | QC wavefunction/relocalization and live optimization; optional format/tool integrations; served-browser tests need a browser |
| Benchmarks | Nine cases excluded by the normal marker default; opt in with `pytest -m benchmark` |
| Build checks | Wheel/sdist reproducibility, clean installed-wheel smoke and dependency integrity; Sphinx site build |
| Static checks | Ruff, and full strict mypy in full-validation pipelines |

This is a pure-Python package. CI installs prebuilt rendering dependencies; it
normally does not compile a quantum-chemistry core or run a chemistry campaign.
QC-dependent tests can skip here. A pinned subset runs with a real QC core in the
producer's release gate. Full viewer CI is not proof that every optional browser
or QC-dependent route ran.

## Pipeline matrix

| Context | Ruff | Primary Python 3.13 | Mypy + Python 3.12 + 3.14 | Wheel/sdist + installed-wheel smoke |
|---|---|---|---|---|
| Development refs and MRs, including web/scheduled/API requests | No pipeline | No pipeline | No pipeline | No pipeline |
| Release candidate, any trigger | Yes | QVF conformance + full normal suite | Mypy + compatibility samples | Yes |
| Release branch | No repeated runtime gate | Docs publication build only | No | No |

The complete behavior suite runs once on Python 3.13. Python 3.12 and 3.14
run the nine-file `COMPATIBILITY_TESTS` sample: QVF read/write/schema,
API, CLI, renderer dispatch, controller calls, units and kinds. This removes
two repeated full-suite executions while retaining supported-interpreter
integration checks. Detailed feature regressions and optional integrations
run only on 3.13; this sample does not claim full behavior parity on every
interpreter. Expand it when a version-specific regression needs coverage.

At the 2026-10-25 snapshot, local collection selects 7,048 cases in the full
suite and the compatibility sample passes 272 cases in 17.08 seconds on
Python 3.14. This changes the three-interpreter test budget from approximately
21,144 to 7,592 case executions (64% fewer). Dependency installation and
platform skips vary, so this is a case-count reduction, not a measured CI
wall-clock speedup.

A further 2026-10-25 reduction removes 271 collected cases from the main suite:

- Single-command archive checks use seven representative error boundaries;
  table, validate and the API/reader paths retain every corruption kind.
  Diff still checks both argument orders and both output modes, but does not
  cross every low-level ZIP variant with every formatting option.
- SMILES successful-build assertions share one app instead of constructing
  four, and normalization assertions share the Gram/Cartesian checks.
- Normalization grids use 48 rather than 160 points per axis (97.3% fewer
  points). All angular momenta, components and tolerances remain; observed
  norm errors stay below 4e-15.

The three affected modules went from 731 passed / five skipped in 21.27 seconds
to 463 passed / two skipped in 15.05 seconds locally. These runs share a machine
but are not controlled CI benchmarks. The broad seeded fuzzers and targeted
historical failures remain. The removed command/error combinations can no longer
identify an interaction confined to that exact pair; the retained cases cover
container, schema, critical-section, filesystem, binary-member and JSON-member
failure boundaries.

The fast sample covers QVF open/write/schema, units, kinds and privacy.
It omits the broad reader-ownership, API, bond/render/controller and CI-policy
suites from routine runs. There is no automatic changed-test expansion or Git
history lookup. Run affected tests locally or use `--scope changed` deliberately;
in an MR environment that adds changed test files (and selects document contracts
for prose-only diffs). Full validation still includes these tests.

Local smoke setup needs the core package and pytest. Candidate CI always installs
the test/release extras, external QVF corpus and GL/Xvfb stack, and runs conformance,
one full suite, two compatibility samples and packaging unconditionally.
A test-scope variable cannot downgrade it to smoke. Docs build strictly on candidates and publish from
`release`; development docs changes use a local check. Candidate pipelines
disable automatic cancellation.

## Run or inspect a lane

The QVF check uses the existing `QVF_TAG` clone from GitLab and compares
vendored bytes against that release. CI installs the reference validator from
that same clone's `python[validate]` package before the test extras. For local
checks, install it from your checkout of the pinned release first:

```sh
python -m pip install '/path/to/qvf/python[validate]'
python -m pip install -e '.[test,validation]'
python scripts/check_qvf_conformance.py --qvf-root /path/to/qvf --strict-negatives
```

The candidate lane always uses `--strict-negatives`. Positive and legacy
archives must validate, open, decode vibration/GTO payloads and parse viewer
hints. Every negative must produce the expected error or warning codes from
its `.expected.json`; resource-cap fixtures use that file's `limits` overrides.
Warning-only fixtures must remain valid. Exceptions, wrong acceptance, missing
codes and schema drift fail the strict check. No corpus entries are skipped.

A previous v0.4.0 corpus run passed **76/76 positive and legacy archives and
110/110 negative expectations**, with zero gaps or unexpected exceptions.
Those counts are historical evidence, not a result for the current v0.6.0
pin. Use the command above with the current `QVF_TAG` checkout to obtain
evidence for that corpus.
Without `--strict-negatives`, the script still reports missing findings as
gaps for diagnosis. Format and resource rules come from qvf's reference
validator, not a second implementation in vibe-view. Neither lane edits the
corpus or the vendored schema.

```sh
PYVISTA_OFF_SCREEN=True python scripts/run_ci_tests.py --scope fast
python scripts/run_ci_tests.py --scope fast --list-targets
PYVISTA_OFF_SCREEN=True python scripts/run_ci_tests.py --scope changed  # opt-in MR diff expansion
python scripts/run_ci_tests.py --scope full
PYVISTA_OFF_SCREEN=True python scripts/run_ci_tests.py --scope compatibility
python -m pytest tests/test_<area>.py -q
python -m pytest tests --collect-only -q
```

`FAST_TESTS` and `PROSE_TESTS` in `scripts/run_ci_tests.py` are the explicit sample.
The full suite is retained: no bulk deletion of regression tests. GitLab web,
scheduled and API requests create pipelines only on release refs; target a
`release-candidate/*` branch to repeat full qualification. JUnit and the 30 slowest
phases are published for the runtime jobs. Package
artifacts are produced only by the full primary job.
