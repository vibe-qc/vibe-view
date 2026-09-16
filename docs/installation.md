# Installation

## What you need

* **Python 3.11 or newer.** Debian and Ubuntu also need the matching
  `python3-venv` package.
* **Git**, to clone the repository.

That is the whole list. There is no Docker image, no C++ compiler, and no
native dependency build — vibe-view is pure Python plus wheels. In particular
it does **not** build or install vibe-qc.

:::{note}
There is no `pip install vibeview` yet. The private package index at
`https://vibe-qc.com/pypi/simple/` is planned but not published, so neither
`pip install vibeview` nor `pipx install vibeview` resolves today. Install
from the checkout, as below.

The public GitHub source can be cloned without credentials. Canonical
development access is provided separately to authorized contributors.
:::

## Install from a checkout

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

`install.sh` is for macOS and Linux. It creates a dedicated environment under
`.venv` at the repository root and installs the CLI, the browser viewer, the
source-checkout desktop command and the terminal viewer. Electron downloads
itself the first time you run `vibe-view desktop`; pass `--with-electron` to
fetch it during installation instead.

Run every lifecycle script as your regular login user, **without `sudo`**, so
the checkout, the environment and the Electron runtime keep one consistent
owner.

Check the result:

```sh
vibe-view doctor
```

`doctor` reports the core install and every optional mode, tells you what is
missing, and prints the exact command that would fix it. `--json` makes it
machine-readable.

## Source archives and downloads

Use the standalone viewer's [public tags](https://github.com/vibe-qc/vibe-view/tags) to select a
source snapshot. Extract it, enter the extracted repository root, and run the
same `./scripts/install.sh` command. A source archive has no Git metadata, so
use a clone for `update.sh` branch switching and pulls.

Validated wheel and source-distribution artifacts are retained by the canonical
CI service for the selected revision. Authorized contributors can obtain them
from the release owner. Extract the reviewed artifacts and install the wheel
in a dedicated environment:

```sh
python3 -m venv .venv-viewer
source .venv-viewer/bin/activate
python -m pip install './dist/vibeview-X.Y.Z-py3-none-any.whl[viewer,tui]'
```

Replace `X.Y.Z` with the version in the downloaded filename. The Python wheel
does not contain the source-backed Electron app; use a checkout for desktop.
Repository and CI artifact access may require authentication.

Website wheels belong under
`https://vibe-qc.com/vibe-view/docs/_static/downloads/`, matching the viewer's
own `docs/_static/downloads/` staging directory. As checked on 2026-09-15,
the v2.17.1 website wheel is not published there. Do not treat a constructed
versioned URL as a download that already exists: use the source install or
validated CI artifact until that release's wheel has been published. The old
producer-hosted 2.15.2 wheel is historical, not the current viewer.

## Installation profiles

`install.sh`, `update.sh` and `reinstall.sh` all take `--extras`:

| Profile | Installs | Use when |
|---|---|---|
| `core` | Reading, conversion, headless capture and export | A compute node or CI runner that only produces figures |
| `viewer` | `core` + the trame/uvicorn browser stack | You want `open` / `serve` / `dashboard` |
| `tui` | `core` + Textual | You want the interactive terminal viewer |
| `modes` | The default: viewer + tui + desktop | An ordinary workstation |
| `all` | Everything installable | You do not want to think about it |

Electron and the desktop options need `modes`, `viewer`, `all` or `test`;
`core` and `tui` are non-desktop profiles. `update` and `reinstall` default
back to `modes`, so repeat `--extras` if you are maintaining a narrower one.

## Optional extras

Installing from a wheel or with `pip install -e .` instead? The extras are
what pull in each capability. All of them are optional, and the core install
is deliberately lean so a capture-only host does not drag in a web server.

| Extra | Pulls in | Unlocks |
|---|---|---|
| `viewer` | trame, trame-vtk, trame-vuetify, uvicorn | `vibe-view open`, `serve`, `dashboard`, `desktop` |
| `tui` | textual | `vibe-view tui` (`vibe-view show` is core) |
| `ase` | ase | The long tail of structure formats, via `ase.io.read` |
| `smiles` | rdkit | Build a 3-D structure from a SMILES string |
| `jupyter` | ipython, ipywidgets | The `%vibeview` notebook magic |
| `queue` | vq | The vq job panel and `vibe-view from-vq` — see [Queue integration](queue.md) |
| `all` | viewer + tui + ase + smiles + jupyter | Everything **except** `queue` |

Two of these have caveats worth knowing before you hit them:

* **`queue` is deliberately not in `all`.** `vq` is published on no package
  index, so a `pip install vibeview[all]` that tried to pull it would fail to
  resolve for everyone. Install vibe-queue from its own checkout first;
  `python -m pip install -e '.[queue]'` from the viewer root is then satisfied
  by what is already there.
  [Queue integration](queue.md) has the full story, including why the extra
  cannot resolve at all on Python 3.11.
* **`ase` is large.** vibe-view hand-rolls the common formats — XYZ, CIF,
  PDB, Mol2, GRO, SDF, Cube, Gaussian input — and defers only the long tail
  to ASE. Most users never need it. `vibe-view formats` lists what your
  installation can actually read.

## Windows

The lifecycle scripts are macOS and Linux only. On Windows, create the
environment by hand from PowerShell in the checkout root, and repeat the
`pip install -e` line after updating the checkout:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[viewer,tui]"
.venv\Scripts\vibe-view.exe --version
```

The [desktop app](desktop.md#windows) runs from that environment too, with
the caveat recorded there: the Windows target has not been verified on a
real Windows machine.

## Desktop integration (macOS)

```sh
./scripts/install.sh --with-electron --dock --link-bin /opt/homebrew/bin
```

`--dock` pins the source-backed `vibe-view.app` to the Dock. `--link-bin DIR`
writes a marked `vibe-view` launcher into an existing, writable directory on
your `PATH` — `/opt/homebrew/bin` on Homebrew macOS, or `~/.local/bin` when
that is on `PATH` — so `vibe-view desktop FILE.qvf` works from any shell.
`--adopt-desktop` lets this checkout take over a source app installed by
another one.

Both integrations are recorded: `uninstall.sh` removes them along with the
installation, and re-running `install.sh` with the same flags refreshes them
after the checkout or environment moves.

The wheel contains the Python browser, terminal and headless surfaces, but not
the Electron source that `vibe-view desktop` runs. Desktop mode currently
requires a source checkout; signed standalone desktop artifacts have not
landed. [The desktop app](desktop.md) covers the setup screen, the Finder
association and the Linux and Windows notes.

## Updating

```sh
./scripts/update.sh              # fast-forward the current branch
./scripts/update.sh --dev        # switch to main and update
./scripts/update.sh --release    # switch to the release branch and update
./scripts/update.sh --skip-git   # reinstall the checked-out source only
./scripts/update-desktop.sh      # source + Python + the Electron app
```

Quit every desktop window before `update-desktop.sh`. It refreshes the
source-backed Electron app owned by this checkout — including its displayed
version — and synchronizes the Electron engine to the reviewed lockfile
version. It never overwrites an app installed from a DMG: a published macOS
package uses **Help → Check for Updates**.

## Repair and removal

Quit every desktop window and any running CLI or browser-server launch first.

```sh
./scripts/reinstall.sh              # rebuild modes, leave Git alone
./scripts/reinstall.sh --desktop    # also refresh Electron and the source app
./scripts/uninstall.sh --dry-run    # show what would go
./scripts/uninstall.sh
```

Reinstall is transactional: the previous environment survives until the new
one installs and verifies.

Uninstall removes the dedicated viewer environment, a recognizable
direct-download Electron runtime in this checkout, a macOS source app owned by
this checkout, and the Dock tile or command link created by `install.sh`. It
**never** removes packaged apps, apps owned by another checkout, your QVF
files, settings, recents, logs, or the app-managed environment under
`$XDG_DATA_HOME/vibe-view/venv`. `--keep-desktop` retains the Electron
download and the source app.

The cached interpreter selection is removed only when it points into the
environment being deleted.

## Building a wheel locally

```sh
./scripts/install.sh    # also installs the `build` frontend
./scripts/build.sh
```

`build.sh` auto-detects `.venv`, cleans the local build output and writes
`dist/vibeview-*.whl`. For a custom environment set `VIBE_VIEW_VENV` or
`VIBE_VIEW_VENV_PYTHON`. If it reports *no usable vibe-view virtualenv found*,
run `install.sh`; if it reports a missing `build` frontend, run
`update.sh --skip-git`.

`scripts/make_wheel.sh` is the separate release-publisher workflow — it builds
and validates both the wheel and the source distribution, writes checksums and
stages the website download. Use `build.sh` for ordinary local packaging.

## Comparing lifecycles across the toolset

For a side-by-side comparison of install, update, repair and removal across
vibe-view, vibe-qc, vq and vibe-basis — including profile selection, ownership
markers, legacy adoption and what each uninstall keeps — see the toolset
lifecycle guide in the [vibe-qc documentation](https://vibe-qc.com/docs/).

## Launcher records

`install.sh --link-bin` keeps its directory inventory outside the checkout at
`$VIBE_PRIVATE_ROOT/vibe-view/state/<checkout-hash>/bin-links`. If the variable
is unset, the root is `$XDG_STATE_HOME/vibe-private`, or
`~/.local/state/vibe-private` when `XDG_STATE_HOME` is unset. Paths must be
absolute and outside all Git checkouts and object stores. Private directories
use mode 0700 and the inventory uses mode 0600.

The next link or unlink operation verifies and migrates a legacy
`.vibe-view-bin-links` file before removing the old copy. A dry run does not
move it. Keep the same private root for install, update and uninstall so each
operation can find the same inventory. This does not relocate virtualenvs.
