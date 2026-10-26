# The desktop app

`vibe-view desktop` opens the viewer in a native window: an Electron shell
with OS menus, file dialogs, drag-and-drop, an Open Recent list and, on
macOS, a Finder file association for `.qvf`. The same code targets macOS,
Windows and Linux.

```sh
vibe-view desktop                     # a window on the browse page for the current directory
vibe-view desktop water.qvf           # a window with water.qvf loaded
vibe-view desktop --port 9090         # the server port behind the window; 8765 by default
```

It works from any directory. On first run it downloads the reviewed Electron
runtime, about 120 MB, and brands it as vibe-view; pass `--with-electron` to
`install.sh` to fetch it during installation instead.

:::{note}
The desktop app is a thin native shell around the same Python server the
browser viewer uses: it starts `vibe-view serve` in the background and shows
it in a window. Everything on [The browser viewer](browser.md) and
[Building and editing](editing.md) applies unchanged. What the shell adds
is the window, the menus and the file association.
:::

## What is available today

| Route | Status |
|---|---|
| `vibe-view desktop` from a **source checkout** | The supported route, on macOS, Linux and Windows. |
| Apple Silicon `.app`, `.dmg` and ZIP | Buildable locally with Python, PyVista, VTK, Trame and RDKit included. Requires macOS 14+. Developer ID signing and notarization remain release work. |
| Linux x86-64 AppImage | Self-contained runtime; see [Linux desktop](#self-contained-linux-desktop) for host requirements. |
| Windows NSIS installer | Buildable shell requiring an external Python environment; Windows qualification remains incomplete. |

The Python wheel carries the browser, terminal and headless surfaces but not
the Electron source, so the CLI's desktop command needs the checkout. The
self-contained Apple Silicon and Linux x86-64 packages instead run their
included interpreter, including for each per-file viewer; they ignore external
Python environments. On macOS, copy the app from the DMG to Applications
and open it. No Python, pip, Homebrew or Node.js installation is needed for normal viewing, editing or image capture.

External calculation engines and companion applications remain separate.
The bundled profile includes common format readers and SMILES conversion;
ASE's additional formats, TREXIO and notebook widgets remain optional source
extras. See [Desktop packaging design](desktop_packaging_design.md) for the
runtime scope and build instructions. Locally built ad-hoc previews do not
provide Gatekeeper trust or establish Mac App Store readiness.

## Installing

The following commands install from source. They are unnecessary for the
self-contained Apple Silicon app.

Clone the public source repository:

```sh
git clone https://github.com/vibe-qc/vibe-view.git
```

Then enter the checkout before following your platform's install commands:

```sh
cd vibe-view
```

**macOS and Linux.** The lifecycle installer creates the environment and
records it for direct app launches:

```sh
./scripts/install.sh --with-electron          # or plain install.sh; Electron then downloads on first launch
source .venv/bin/activate
vibe-view desktop
```

Desktop mode needs a profile that contains the viewer: `modes` (the
default), `viewer`, `all` or `test`. `core` and `tui` are non-desktop
profiles.

**Windows.** The shell scripts are macOS and Linux only. Create the
environment by hand from PowerShell in the checkout root:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[viewer]"
.venv\Scripts\vibe-view.exe --version
.venv\Scripts\vibe-view.exe desktop
```

Repeat the `pip install -e` line after updating the checkout.

Run install, update, reinstall and uninstall as your regular login user,
without `sudo`. Privileged interactive execution is refused so it cannot
leave a root-owned environment the desktop session cannot maintain, and on
Linux the app refuses to launch as root outright, because Electron's sandbox
does not support it.

### Dock tile and shell command (macOS)

```sh
./scripts/install.sh --with-electron --dock --link-bin /opt/homebrew/bin
```

`--dock` pins the source-backed `vibe-view.app` to the Dock and asks the
Dock to reload; running it again is a no-op. `--link-bin DIR` writes a
marked `vibe-view` launcher into an existing writable directory on your
`PATH` and refuses to overwrite a file it did not create. Both are recorded:
`uninstall.sh` removes them with the installation, and re-running
`install.sh` with the same flags refreshes them after the checkout moves.
`--adopt-desktop` lets this checkout take over a source app another
checkout installed.

### Updating and repairing

```sh
./scripts/update-desktop.sh          # checkout + Python environment + Electron + the source app
./scripts/reinstall.sh --desktop     # rebuild the environment and refresh Electron, no Git
./scripts/uninstall.sh --dry-run     # what a removal would take
```

Quit every desktop window first. `update-desktop.sh` validates the Electron
engine and synchronizes a missing, corrupt or mismatched one to the reviewed
version in `package-lock.json`; on macOS it also refreshes the Applications
copy and its displayed version. It records which checkout owns the source
app and refuses to take over one from another checkout unless you pass
`--adopt-desktop`; inspect the path it reports first. It never replaces a
packaged app, and on Linux there is no Applications copy: the updater
synchronizes Electron and its wrappers, and the window keeps starting
through `vibe-view desktop`.

`uninstall.sh` removes this checkout's Electron runtime, its macOS source
app and the Dock tile or command link; `--keep-desktop` keeps the Electron
download for a later reinstall. Settings, recents, logs, window state, your
archives and the app-managed environment described next are preserved.

## Opening files and quitting

Use **File → Open QVF, TREXIO or ORCA File…** (Ctrl/⌘ O) for a QVF or
supported loose file, **Open Recent** to return to a previous path, or drag
a file into the window. **File → Open
Folder…** (Ctrl/⌘ Shift O) browses an ordinary directory; a TREXIO text
directory is treated as one dataset and needs the TREXIO reader. **Browse
Directory** (Ctrl/⌘ B) returns to the server's directory browser.

These native dialogs select files on the desktop application's machine.
Inside the viewer, **Browse chemistry file…** follows the browser upload
workflow described in [opening files](browser.md#opening-files-from-inside-the-viewer).
Opening a loose file converts it for inspection; use the import command or
a geometry save action when you want a persistent output archive.

Closing the last window does **not** quit the application. It remains
running in the tray/menu bar and keeps its server alive. Use **Quit** from
the tray menu, **vibe-view → Quit** / ⌘Q on macOS, or **File → Quit** on
Linux and Windows. Quitting stops the desktop-managed server and console;
queue jobs submitted separately have their own lifecycle.

Before updating or repairing, save edited geometry and any views you want
to retain, then quit the app explicitly. A session file saves the view;
it is not a substitute for saving edited coordinates.

## First run: the setup screen

Self-contained macOS and Linux packages validate their embedded runtime.
If it cannot start, the repair screen suggests restarting or replacing the
application, keeping your documents and settings. It never asks you to install Python or tries a
different interpreter from your machine. The discovery and setup instructions
below apply to source launches and shells without a bundled runtime, including
the Windows target.

When the app starts it looks for a Python that can run the server, in this
order: the interpreter recorded by an earlier `vibe-view desktop` launch,
then `python3`, `python`, `python3.13`, `python3.12` and `python3.14` on
`PATH` (`py` and `python` on Windows), then the app-managed environment at
`$XDG_DATA_HOME/vibe-view/venv` (by default `~/.local/share/vibe-view/venv`),
then a development checkout's `.venv`.

If none of those can import `vibeview` with the `[viewer]` extra, the app
does not fail. It shows a **setup screen** naming which of three cases
applies (no Python, Python but no vibe-view, vibe-view but no `[viewer]`
extra), with a command that creates a dedicated virtual environment at the
app-managed path and installs into it, a **Copy** button, and an
**I've installed it — recheck** button that re-scans without restarting.

Two things about that command are worth knowing:

* It installs into a **fresh virtualenv on purpose**. Homebrew Python and
  the Debian, Ubuntu and Fedora system Pythons are marked externally managed
  ([PEP 668](https://peps.python.org/pep-0668/)) and refuse a plain
  `pip install`; a fresh virtualenv never is. The app looks for that
  environment on every start, so **Recheck** finds it with no further step.
* The requirement it composes names a wheel by URL for the app's exact
  version. Until the distribution is published, use the source checkout
  instead: from the checkout root, with the app still open,

  ```sh
  python3 -m venv ~/.local/share/vibe-view/venv
  ~/.local/share/vibe-view/venv/bin/pip install -e '.[viewer]'
  ```

  then press **Recheck**.

Launching with `vibe-view desktop` from an environment that already has the
viewer records that interpreter, so the setup screen is skipped from then
on.

## macOS: double-click a `.qvf`

`vibe-view desktop FILE` works immediately after install. A Finder
double-click needs the app bundle, which `install.sh --with-electron`
installs as `vibe-view.app` in Applications and brands with every format the
viewer reads: `.qvf`, `.py` inputs and the loose structure and volume
formats. **Right-click → Open With → vibe-view** therefore works for all of
them, and a `.py` input opens with its structure loaded. The handler rank is
*Alternate*: vibe-view never takes over the default association for Python
files.

macOS usually picks up the declarations on first launch. To force a refresh:

```sh
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f /Applications/vibe-view.app
```

To build a distributable package yourself:

```sh
cd electron
./build-desktop.sh          # dist/mac-arm64/vibe-view.app (dist/mac/ on Intel) and a .dmg
```

That is a local development artifact, ad-hoc signed but **not Developer
ID-signed or notarized**, so Gatekeeper may block its first launch:
right-click the app and choose **Open** once. Signing and notarization are
the gate on a downloadable, self-updating package, and they remain a roadmap
item; `electron/PUBLISHING.md` has the details.

## Linux

Electron needs the usual GTK3 desktop runtime, which a desktop installation
already has. On a minimal Debian 12 or Ubuntu 22.04 system:

```sh
sudo apt-get install libgtk-3-0 libnss3 libatk-bridge2.0-0 \
  libdrm2 libxkbcommon0 libgbm1 libasound2
```

Debian 13 and Ubuntu 24.04 use the time64 package names:

```sh
sudo apt-get install libgtk-3-0t64 libnss3 libatk-bridge2.0-0t64 \
  libdrm2 libxkbcommon0 libgbm1 libasound2t64
```

If a container or a host security policy blocks Chromium's user namespaces,
configure a supported sandbox policy when you can. For a trusted local file,
`vibe-view desktop --no-sandbox` is the explicit last resort; it prints a
warning, because it reduces isolation.

## Windows

`vibe-view desktop` from a source checkout is the route here too, with the
manual environment above. An NSIS installer target exists in `electron/`,
but no Windows artifact is published, and the installer is unsigned.

:::{warning}
**The Windows target has not been verified on a real Windows machine.** It
is developed and tested on macOS and Linux; the `py`-launcher discovery, the
setup screen and the NSIS build have not been run on Windows, and the app
says so in a one-time notice on its first Windows launch. If you try it,
whether it works or not,
[open an issue](https://github.com/vibe-qc/vibe-view/issues):
that feedback is what will make Windows a first-class target, and you will
get help with whatever you hit.
:::

## Troubleshooting

**The setup screen keeps appearing after I installed vibe-view.** The app
scans the fixed interpreter list above plus the one recorded by a previous
`vibe-view desktop` launch. An environment that is on neither list is
invisible to a bare double-click. Run `vibe-view desktop` **once** from that
environment; it records the interpreter under the cache directory, and later
source launches and double-clicks reuse it. This does not change a packaged
app’s embedded interpreter. Confirm the environment is capable
with:

```sh
python -c "import vibeview, trame, uvicorn; print('ok')"
```

**"The vibe-view server did not start."** Older builds showed this and
quit; current builds route the same conditions to the setup screen, which
names what is missing.

**"Could not open" instead of the viewer.** The viewer process for that file
stopped before it was ready, most often because the file is not a valid
archive. The page shows the exit status and the viewer's error message.
Choosing the file again from the browse page or Open Recent starts a new
attempt, so fix the file first.

**Check for Updates** depends on how the app was installed. Source launches
show checkout-update instructions; they do not query the packaged updater or
change Git. Quit and run `./scripts/update-desktop.sh` from the owning
checkout. A packaged macOS arm64 build uses its configured update feed when
the updater is available. An ad-hoc-signed macOS build can offer a manual
download but cannot self-install; self-installation requires a Developer ID
signature. Feed contents determine whether an update is offered.
Linux and Windows builds show replacement-package guidance rather than
querying the macOS feed.

**The window is blank or unresponsive.** The shell is showing the browser
viewer, so the [browser troubleshooting](troubleshooting.md#the-browser-viewer)
applies: most often a port that something else holds. `--port` moves the
server.

## Self-contained Linux desktop

Linux x86-64 AppImages include Python and the application libraries. No external
Python, pip, Node.js or source checkout is needed. Download the package and its
SHA-256 checksum from [the downloads page](https://vibe-qc.com/download/).
Linux desktop downloads are public and require no supporter membership.
macOS and Windows Store editions are planned paid purchases; their availability
is separate from Linux downloads.

The initial Linux target is Debian 12 or Ubuntu 24.04 on x86-64 with a graphical
desktop and standard GTK3, NSS, ALSA, OpenGL and X11/Wayland compatibility libraries.
Make the AppImage executable and launch it as a regular user. If FUSE is not
available, extract with `--appimage-extract` and launch `squashfs-root/AppRun`.
The application and Python runtime remain inside that directory. Offline local
work is supported; remote services and external calculation engines remain optional.
Linux updates currently use replacement AppImages from the website.

Build natively on Linux x86-64 using `electron/build-desktop.sh --linux`. The
Linux runtime lock pins interpreter and wheel hashes; the builder checks native
library resolution and the installed runtime before packaging.

## Python console

Choose **View → Python Console** (Ctrl/⌘ Shift P). Write Python in the editor and
press **Run** or Ctrl/⌘ Enter. The last expression's value, printed output and
tracebacks appear above the editor. Variables persist between executions; `_`
holds the last expression value. **Stop and reset** terminates the console
process and starts the next execution with fresh variables. Closing the console
also clears its process. It does not stop calculations submitted separately to a
queue.

The console uses the desktop application's selected Python interpreter and
working directory, including the packaged interpreter in a bundled app. It
preloads `api` (`vibeview.api`), `np` (NumPy), `ModelScene`, `SceneCommands` and an
empty `scene`. For example:

```python
api.info("water.qvf")
coordinates = np.array([atom["position"] for atom in api.get_structure("water.qvf")["atoms"]])
coordinates.mean(axis=0)
```

The console is a local Python environment with your account's permissions, not
a sandbox. Its execution bridge is available only to the dedicated desktop
console window; ordinary browser pages do not receive it. The console's scene
is independent of the live browser scene. Exchange working models using saved
scene JSON:

```python
import json
from pathlib import Path
scene = ModelScene.from_dict(json.loads(Path("scene.json").read_text(encoding="utf-8")))
scene.select("element O")
Path("modified-scene.json").write_text(
    json.dumps(scene.to_dict(), allow_nan=False), encoding="utf-8"
)
```

Paste the saved JSON into the browser's **Working model scene** restore field.
See [model scenes](model_scenes.md) for scene commands and replay. Output is
bounded in the console display; save large arrays or reports explicitly to files.
