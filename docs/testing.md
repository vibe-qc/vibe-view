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

| Context | Ruff | Primary Python 3.13 | Mypy + Python 3.11 + 3.14 | Wheel/sdist + installed-wheel smoke |
|---|---|---|---|---|
| Development refs and MRs, including web/scheduled/API requests | No pipeline | No pipeline | No pipeline | No pipeline |
| Release candidate, any trigger | Yes | QVF conformance + full normal suite | Yes | Yes |
| Release branch | No repeated runtime gate | Docs publication build only | No | No |

The fast sample covers QVF open/write/schema, units, kinds and privacy.
It omits the broad reader-ownership, API, bond/render/controller and CI-policy
suites from routine runs. There is no automatic changed-test expansion or Git
history lookup. Run affected tests locally or use `--scope changed` deliberately;
in an MR environment that adds changed test files (and selects document contracts
for prose-only diffs). Full validation still includes these tests.

Local smoke setup needs the core package and pytest. Candidate CI always installs
the test/release extras, external QVF corpus and GL/Xvfb stack, and runs conformance,
the full suites and packaging unconditionally. A test-scope variable cannot
downgrade it to smoke. Docs build strictly on candidates and publish from
`release`; development docs changes use a local check. Candidate pipelines
disable automatic cancellation.

## Run or inspect a lane

```sh
PYVISTA_OFF_SCREEN=True python scripts/run_ci_tests.py --scope fast
python scripts/run_ci_tests.py --scope fast --list-targets
PYVISTA_OFF_SCREEN=True python scripts/run_ci_tests.py --scope changed  # opt-in MR diff expansion
python scripts/run_ci_tests.py --scope full
python -m pytest tests/test_<area>.py -q
python -m pytest tests --collect-only -q
```

`FAST_TESTS` and `PROSE_TESTS` in `scripts/run_ci_tests.py` are the explicit sample.
The full suite is retained: no bulk deletion of regression tests. GitLab web,
scheduled and API requests create pipelines only on release refs; target a
`release-candidate/*` branch to repeat full qualification. JUnit and the 30 slowest
phases are published for the runtime jobs. Package
artifacts are produced only by the full primary job.
