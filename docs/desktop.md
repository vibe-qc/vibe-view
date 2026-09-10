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
| A packaged `.app`, `.dmg`, AppImage or NSIS installer | Buildable locally from `electron/`; **no signed, published artifact exists**. An older ad-hoc-signed macOS arm64 package on the update feed can detect a newer version and offer a manual download, but cannot self-install. |

The Python wheel carries the browser, terminal and headless surfaces but not
the Electron source, so desktop mode needs the checkout. A locally built
package is not a self-contained Python bundle either: it still needs a
Python with the `vibeview` distribution and its `[viewer]` extra on the
machine, and its first-run [setup screen](#first-run-the-setup-screen)
says so when that is missing. Bundling a full Python and VTK runtime into
the installer was evaluated and deferred; the reasoning is in
[Desktop packaging design](desktop_packaging_design.md).

## Installing

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
py -3.11 -m venv .venv
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

## First run: the setup screen

When the app starts it looks for a Python that can run the server, in this
order: the interpreter recorded by an earlier `vibe-view desktop` launch,
then `python3`, `python`, `python3.13`, `python3.12`, `python3.11` and
`python3.14` on `PATH` (`py` and `python` on Windows), then the app-managed
environment at `$XDG_DATA_HOME/vibe-view/venv` (by default
`~/.local/share/vibe-view/venv`), then a development checkout's `.venv`.

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
launches and double-clicks reuse it. Confirm the environment is capable
with:

```sh
python -c "import vibeview, trame, uvicorn; print('ok')"
```

**"The vibe-view server did not start."** Older builds showed this and
quit; current builds route the same conditions to the setup screen, which
names what is missing.

**Check for Updates** in the Help menu queries the macOS arm64 feed, which
currently advertises only an older ad-hoc package. It can offer a manual
download but cannot self-install, and it never updates a source checkout.
For the supported route, quit the app and run `./scripts/update-desktop.sh`.
Linux and Windows feeds are not published.

**The window is blank or unresponsive.** The shell is showing the browser
viewer, so the [browser troubleshooting](troubleshooting.md#the-browser-viewer)
applies: most often a port that something else holds. `--port` moves the
server.
