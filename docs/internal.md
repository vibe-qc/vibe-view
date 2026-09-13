# Project history and internals

Engineering documents, kept because they record *why* something is the way it
is. **None of this is user documentation.** These pages were written as
standalone notes at particular moments, they are not maintained to the
lightweight-ongoing cadence the user guide is, and several of them describe
work that has since landed, moved or been superseded.

If you are trying to use vibe-view, everything you want is in
[Using vibe-view](index.md) — start with [Installation](installation.md).

The notes cross-reference each other and the repository by relative path.
Those links resolve on GitLab; several of them do not resolve through this
site, which is a deliberate trade rather than an oversight. Where a note is
still relevant it is cited from the code or the maintained developer guides.

```{toctree}
:maxdepth: 1

audit_2026_07_02
design_refresh_2026
desktop_packaging_design
parity_cross_repo_asks
ROADMAP_AVOGADRO_PARITY
```

## What each one is

| Document | What it records |
|---|---|
| [Audit, 2026-07-02](audit_2026_07_02.md) | A correctness pass over the viewer, with the live-UI QA evidence behind it |
| [Design refresh 2026](design_refresh_2026.md) | The visual and interaction refresh: materials, lighting, ambient occlusion |
| [Desktop packaging design](desktop_packaging_design.md) | How the Electron app is built, owned and updated, and why source and packaged builds differ |
| [Cross-repository parity asks](parity_cross_repo_asks.md) | Things the viewer needs from producers, filed before the repositories were split |
| [Avogadro parity roadmap](ROADMAP_AVOGADRO_PARITY.md) | The feature-parity plan through v2.15.2, frozen at the split. The maintained plan is the [Roadmap](roadmap.md) |

## Not on this site

Historical operator ledgers and workstream handovers are preserved in the
private operations repository with source revisions and checksums. They are
not part of the portable source snapshot. Product design notes, scientific
validation scripts, tests and the maintained roadmap remain in this tree.

## Developer tooling that lives in `docs/`

`docs/` also holds a set of developer scripts. They are not part of the site
and Sphinx does not read them; they retain their `docs/` paths so historical references still identify the
original scripts.

| Script | What it does |
|---|---|
| `bench_cartoon_payload.py`, `bench_label_orientation.py`, `bench_representation_build.py` | Micro-benchmarks for the renderers they name |
| `capture_screenshots.py`, `capture_tui_screenshots.py` | Regenerate the browser and terminal screenshots under `docs/images/` |
| `interact_controls.py` | Trusted-event Playwright driver for the live UI — catches handlers that a headless `eval` harness never fires |
| `qa_*.py` | Live GPU/trame QA harnesses: job containers, live optimisation, live reload, streaming, panel injection, the details sandbox |

None of them is a package or CI dependency.


(regenerating-figures)=
## Regenerating the manual's figures

The capture tools run this checkout's `src/vibeview`, using the interpreter
that starts the script. Install the development tools into the viewer venv:

```sh
.venv/bin/python -m pip install playwright pillow
.venv/bin/python -m playwright install chromium
export VIBE_VIEW_EXAMPLES=/path/to/vibe-qc/examples/vibe_view
PYVISTA_OFF_SCREEN=True .venv/bin/python docs/capture_screenshots.py
PYVISTA_OFF_SCREEN=True .venv/bin/python docs/capture_tui_screenshots.py
cd docs && make strict
```

The input directory must contain `runs/qvf_showcase/water.qvf`,
`runs/h2co_showcase/h2co.qvf` and `output-nacl-showcase.qvf`, written by the
project-authored vibe-qc showcase scripts. These archives are capture inputs,
not a runtime dependency. The tools fail when an input is absent.
`capture_inputs.py` copies them into `docs/_build/capture-inputs/`, sanitizes
provenance, validates every member digest, and records the original input
SHA-256 digests in `input-sha256.json` there. Nothing is written to vibe-qc.

The water demo uses the same `write_demo` path as `vibe-view demo` and needs
no producer. Formaldehyde density, orbitals, vibrations, IR, charges and SCF
history, plus NaCl structure and DOS, come from the computed showcase data.
The missing multi-section archive is replaced by explicitly illustrative
fixtures for difference density, ELF, bands/DOS, ECD, NMR, symmetry, QTAIM,
trajectory and reaction-path panels. Their values demonstrate the interface;
they are not computed physical observables. The figure captions mark these
cases. Input archives remain outside the tracked tree.

Browser figures are compositor captures of the real Vue/Trame/VTK UI in
Chromium. The script dismisses the welcome card, selects the light background,
uses the accessible result splitter, resets the camera after resizing, and
waits for Plotly to paint. Terminal figures come from Textual and Rich's real
rendered output, with remote font references removed. Screenshots are never
image-generated. All outputs go directly into `docs/images/`; there is no
second copy under the pre-split `_static/plots/vibe_view` directory.
