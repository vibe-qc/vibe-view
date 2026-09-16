<!-- attribution -->

_Created and maintained by Dr. Michael F. Peintinger._

# vibe-view

Standalone visualization and conversion tools for quantum chemistry results.

vibe-view opens [QVF](https://vibe-qc.com/vibe-view/docs/qvf.html) calculation archives and selected
structure and volume files from many chemistry programs. Use it in a web
browser, a native desktop window, an interactive terminal, a headless figure
pipeline, or Python. vibe-qc is one QVF producer, but it is not required to
install or use the viewer.

The names have different roles:

| Where | Name |
|---|---|
| Product and shell command | `vibe-view` |
| Python distribution and import package | `vibeview` |
| Git repository | `vibe-qc/vibe-view` |

## Install

Prerequisites are Python 3.11 or newer and Git. Debian and Ubuntu users also
need the matching `python3-venv` package. Docker is not used, and no C++
compiler or vibe-qc native dependency build is required.

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

The installer is for macOS and Linux. It creates a dedicated environment
under `.venv` at the repository root and installs the standalone CLI, browser viewer,
source-checkout desktop command, and interactive TUI. Electron downloads
automatically the first time you run `vibe-view desktop`; pass
`--with-electron` to download it during installation instead. It does not
build or install vibe-qc. Run every lifecycle script as your regular login
user, without `sudo`, so the checkout, environment, and Electron runtime keep
one consistent owner.

Here, standalone means a dedicated Python environment that does not install or
build vibe-qc. The checkout is self-contained: vibe-view vendors its own copies
of the QVF manifest schemas and ships its Electron source, so nothing outside
this repository has to be present. (Before the 2026-09 split the schemas were
symlinks into vibe-qc's tree and the viewer had to live inside that checkout;
they are real data files now, and the built wheel carries them.)

Two optional desktop integrations make the source install behave like a
native app on macOS:

```sh
./scripts/install.sh --with-electron --dock \
    --link-bin /opt/homebrew/bin
```

`--dock` pins the source-backed `vibe-view.app` to the macOS Dock, and
`--link-bin DIR` writes a marked `vibe-view` launcher into an existing,
writable directory on your PATH (Homebrew macOS: `/opt/homebrew/bin`;
`~/.local/bin` when it is on PATH), so `vibe-view desktop FILE.qvf` works
from any shell. `--adopt-desktop` lets this checkout take over a source app
installed by another checkout. Both integrations are recorded:
`uninstall.sh` removes them with the installation, and re-running
`install.sh` with the same flags refreshes them after the checkout or
environment moves.

Update the current branch and environment with one command:

```sh
./scripts/update.sh          # current branch
./scripts/update.sh --dev    # switch to and update main
./scripts/update-desktop.sh  # source + Python + desktop app
```

Git-aware viewer updates operate on this repository. With no
selector the updater fast-forwards the current branch; `--dev`, `--release`,
and `--branch NAME` can switch the whole checkout. Use `--skip-git` when you
only want to reinstall the currently checked-out source.

Repair or remove a standalone source installation without manual virtualenv
steps. First quit every vibe-view desktop window and any running vibe-view CLI
or browser-server launch:

```sh
./scripts/reinstall.sh           # rebuild modes, keep Git unchanged
./scripts/reinstall.sh --desktop # also refresh Electron + source app
./scripts/uninstall.sh --dry-run
./scripts/uninstall.sh
```

Reinstall is transactional: it keeps the previous environment until the new
one installs and verifies successfully. Uninstall removes the dedicated viewer
environment, a recognizable direct-download Electron runtime in this checkout,
a macOS source app owned by this checkout, and the Dock tile / command link
created by `install.sh --dock` / `--link-bin`. It never removes packaged
apps, apps owned by another checkout, QVF files, settings, recents, logs, or the
app-managed environment under `$XDG_DATA_HOME/vibe-view/venv`. Pass
`--keep-desktop` to retain the Electron download and source app.
The cached interpreter selection is removed only when it points into the
environment being deleted; other user data and window state remain.

For a side-by-side comparison of install, update, repair, and removal across
vibe-view, vibe-qc, vq, and vibe-basis, see the
[toolset lifecycle guide](https://vibe-qc.com/docs/) (`docs/toolset_lifecycle.md`
in the vibe-qc repository). It also covers profile
selection, ownership markers, legacy adoption, and what each uninstall keeps.

## Build a local wheel

The lifecycle installer also installs the Python `build` frontend needed by the
local wheel helper:

```sh
./scripts/install.sh
./scripts/build.sh
```

`build.sh` auto-detects `.venv`, cleans the local viewer build output,
and writes `dist/vibeview-*.whl`. For a custom environment, set
`VIBE_VIEW_VENV=/path/to/venv` or
`VIBE_VIEW_VENV_PYTHON=/path/to/python`. If it reports `no usable vibe-view
virtualenv found`, run `install.sh`; if it reports a missing `build` frontend,
run `update.sh --skip-git`.

`make_wheel.sh` is the separate release-publisher workflow. It builds and
validates the wheel and source distribution, writes checksums, and stages the
website download. Use `build.sh` for ordinary local packaging.

Quit the desktop window before running `update-desktop.sh`. It refreshes the
source-backed Electron app owned by this checkout, including its displayed
vibe-view version, and synchronizes the Electron engine to the reviewed
lockfile version. It never overwrites an app installed from a DMG; a published
macOS package uses Help > Check for Updates, while other packaged builds need
the current replacement artifact. On Linux it updates the Electron runtime and
wrappers, and the app continues to launch through `vibe-view desktop`; Linux
has no Applications copy. `install.sh --with-electron` downloads the runtime
and installs the macOS Applications copy in the same step. If a source app
belongs to an older checkout, review the path it reports and pass
`--adopt-desktop` explicitly to transfer ownership. Branch, tag, and commit
selectors are accepted only when the selected revision contains the safe
desktop-update protocol.

Use `--extras core`, `viewer`, `tui`, or `all` to choose a smaller or broader
profile. Electron and desktop options require `modes`, `viewer`, `all`, or
`test`; `core` and `tui` remain non-desktop profiles. Run the scripts with
`--help` for Python, venv, and recovery options; the updater also documents its
branch and release selectors. Update and reinstall default to `modes`, so
repeat `--extras` when maintaining a non-default profile.

> **Note:** the private package index at `https://vibe-qc.com/pypi/simple/` is
> planned but is **not published yet**, so
> `pip install vibeview --index-url https://vibe-qc.com/pypi/simple/` will not
> resolve. Bare `pip install vibeview` and `pipx install vibeview` do not work
> yet either. Use the checkout installer above; see the
> [installation and downloads guide](https://vibe-qc.com/vibe-view/docs/installation.html).

> **Downloads:** use the standalone repository’s source archives or validated
> build artifacts, described in the installation guide. The old 2.15.2 wheel
> under the producer’s documentation is a historical artifact, not the current
> viewer. A GitHub source mirror does not imply published wheel or desktop assets.


## Features

- **Structure** — CPK-coloured atoms with ball-and-stick, space-filling, wireframe styles
- **Electron density** — translucent isosurface with adjustable isovalue and colormap
- **Molecular orbitals** — HOMO/LUMO and full MO set with signed-lobe rendering
- **Band structure + DOS** — Interactive Plotly charts with Fermi-level reference
- **Compare mode** — Overlay structures from multiple files with Kabsch alignment
- **Vibrations** — Animated normal modes with frequency selector
- **IR/UV-Vis/Raman spectra** — Stem plots with hover tooltips
- **Wavefunction evaluator** — On-demand orbital evaluation from GTO basis
- **Geometry optimisation trajectory** — Frame-by-frame playback with energy plot
- **Bond orders + QTAIM** — Mayer/Wiberg analysis with critical points and bond paths
- **vq Job Manager** — live queue cockpit: monitor vibe-queue jobs by state and fetch results straight into the viewer ([guide](https://vibe-qc.com/vibe-view/docs/queue.html))
- **Biomolecule metadata** — QVF `structure` sections carry optional chains / residues / secondary structure for cartoon rendering
- **Terminal mode** — full 3D viewer, charts and selectable wavefunction surfaces as Unicode braille in any terminal: canonical, alpha/beta, natural and localized orbitals plus computed density, with no display server, GL or X forwarding ([guide](https://vibe-qc.com/vibe-view/docs/capabilities.html))
- **Jupyter notebooks** — `%vibeview` renders structures, volume captures, tables, SCF histories, and bands inline (requires the `[jupyter]` extra)
- **Import** — QVF, vibe-qc Python inputs (`.py`), XYZ, CIF, Cube, PDB, Mol2, Gaussian input, GRO, SDF/Mol; TREXIO geometry and Gaussian orbitals through the optional `[trexio]` extra ([format guide](docs/formats.md#trexio-wavefunctions))
- **Export** — XYZ, CIF, OBJ, glTF, HTML (standalone 3D viewer), JSON

## Quickstart

```sh
vibe-view doctor             # check this installation and its optional modes
vibe-view demo               # write and validate a bundled water structure demo
vibe-view demo --open        # write it, then launch the browser viewer
```

`demo` needs neither vibe-qc nor an input file. It prints browser, desktop,
terminal, and headless next steps. Use `-o FILE` to choose its output path and
`--force` to replace an existing demo. The older `vibe-view quickstart`
command runs a real calculation and therefore requires vibe-qc in the same
environment.

Already have data? Choose the shortest route:

| Goal | Command |
|---|---|
| Open a QVF or supported loose file in a browser | `vibe-view open INPUT` |
| Work over SSH without a display | `vibe-view show FILE.qvf` or `vibe-view tui FILE.qvf` |
| Use a native window from the source checkout | `vibe-view desktop INPUT` |
| See which loose formats this installation supports | `vibe-view formats` |
| Convert a loose file into a persistent QVF | `vibe-view import INPUT` |

The wheel contains the Python browser, terminal, and headless surfaces, but
not the Electron source used by `vibe-view desktop`. Desktop mode currently
uses a source checkout; signed standalone desktop artifacts have not landed.

## CLI commands

```
open             Interactive 3D viewer
demo             Write a bundled water structure demo (--open launches the browser)
tui              Interactive terminal viewer (needs the [tui] extra)
show             Render one frame as terminal text and exit
compare          Side-by-side with auto compare-mode
serve            Web-based QVF file browser
dashboard        Multi-QVF web dashboard
desktop          Launch the native Electron window (source checkout)
table            Dump tabular data (table/csv/json)
batch            Offscreen PNG gallery (+ --volumes)
batch-compare    Batch side-by-side comparisons
info             Metadata + section sizes (--json, --short)
export           XYZ/CIF/CML/JSON/PY structure, OBJ/glTF meshes,
                 POV-Ray/Blender scenes, HTML/SVG/PDF pages (12 formats)
animate          Render animated sections (trajectory, NEB, vibrations) to MP4/GIF
diff             Compare two QVFs (--json)
validate         SHA-256 integrity check
import           Convert loose inputs into persistent QVF archives
formats          List built-in and plugin importers (--json for scripts)
capture          Render any section to PNG
capture-selftest Self-test the headless capture pipeline
slice            Extract subset of sections
merge            Combine sections from multiple QVFs
supercell        Replicate a periodic structure
h-add            Add hydrogens to a structure
from-vq          Fetch a finished vq job's QVF output
vq-features      Report vq integration feature availability
config           Manage configuration (~/.config/vibe-view/)
doctor           Diagnose the installation and optional modes (--json)
recent           Recently opened files
stats            Directory-wide statistics
examples         Show workflows; --copy DIR copies bundled examples
quickstart       Run a vibe-qc calculation and open it (requires vibe-qc)
```

## Python SDK

```python
from vibeview import info, diff, validate, get_structure, capture_structure, render_terminal

data = info("calculation.qvf")
comparison = diff("hf.qvf", "pbe.qvf")
is_valid = validate("result.qvf")
capture_structure("h2o.qvf", "structure.png")

# Same renderers, text instead of a PNG — and no OpenGL context needed,
# so this one works on a headless compute node.
print(render_terminal("h2o.qvf", size=(80, 24)))
print(render_terminal("h2o.qvf", "vol_mo_3", isovalue=0.03))
```

`render_terminal` draws stored `volume.*` sections in one shot. To browse
every orbital embedded in `wavefunction.gto`, including localized and natural
sets, run `vibe-view tui h2o.qvf`; use the focusable surface table on the
right, or `n`/`p`, `D`, and `S` from the keyboard. `D` is available when the
archive declares electron occupations; transition weights and ambiguous
legacy natural values remain orbital-only. On-demand evaluation covers
shells through `l = 3`; periodic Gamma fields omit image-AO tails. The status
line reports an incomplete high-l surface.

## Jupyter

Jupyter support is optional. Use the source installer's `all` profile, or the
wheel's `[jupyter]` extra, in the environment that runs the notebook kernel.

```python
%load_ext vibeview.jupyter
%vibeview calculation.qvf
%vibeview calculation.qvf --table atom_properties
```

Run `%vibeview --help` for `--capture`, `--table`, `--mo`, `--scf`, and
`--bands`.

## Documentation

<https://vibe-qc.com/vibe-view/docs/> — the manual: install and quickstart, a
panel-by-panel tour, the browser viewer and its editor, terminal mode, the
desktop app, headless figures and exports, input formats and importer plugins,
biomolecules, the QVF format, jobs and live results, the `[queue]` extra, the
CLI, the Python SDK and troubleshooting.

Build it from a checkout:

```sh
pip install sphinx myst-parser furo sphinx-copybutton sphinx-design linkify-it-py
cd docs && make html      # renders into docs/_build/html
cd docs && make strict    # the same build with -W; run this before pushing docs
```

> The published site tracks the `release` branch, so it shows the released
> version; a local build shows the tip of your checkout. See
> [`docs/release_process.md`](docs/release_process.md).

## License

MPL 2.0 — see [LICENSE](LICENSE).


## History

vibe-view was split from the vibe-qc monorepo on 2026-09-08. It is now an
independently versioned companion to [vibe-qc](https://github.com/vibe-qc/vibe-qc),
[vibe-queue](https://github.com/vibe-qc/vibe-queue) and
[qvf](https://github.com/vibe-qc/qvf).

The canonical development history remains private. Public snapshots have new
commit identities and record their source revision and per-file checksums in
publication provenance. A public commit ID is not interchangeable with a
canonical development commit ID; original release tags remain immutable.

The QVF manifest schemas under `src/vibeview/` are vendored real files. The
normative copy lives in the [qvf repository](https://github.com/vibe-qc/qvf),
and `scripts/check_qvf_conformance.py` checks the schema pins and corpus.

Copyright (c) 2026 Michael F. Peintinger and vibe-qc contributors.
