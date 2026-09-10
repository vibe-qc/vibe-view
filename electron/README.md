# vibe-view Desktop App

Electron-based desktop application for vibe-view. Runs on **macOS, Windows, and Linux**
with the same codebase — no platform-specific code required.

## Quick Start

```bash
# One-time standalone environment setup, from the checkout root:
./vibe-view/scripts/install.sh
source vibe-view/.venv/bin/activate

vibe-view desktop                  # native window, browse page for the cwd
vibe-view desktop water.qvf        # native window with water.qvf loaded
vibe-view desktop --port 9090      # custom server port

# Later, with the desktop window closed:
./vibe-view/scripts/update-desktop.sh

# After closing all desktop windows and CLI/server launches, repair or remove:
./vibe-view/scripts/reinstall.sh --desktop
./vibe-view/scripts/uninstall.sh --dry-run
./vibe-view/scripts/uninstall.sh
```

The `vibe-view desktop` commands work from **any directory**; the relative
setup and updater paths above assume the checkout root. On first run the
reviewed Electron binary (~120 MB) is downloaded from GitHub releases via
`install-electron.py` (no npm required) and branded as vibe-view.

## Architecture

```
vibe-view desktop [file]           (Python CLI, src/vibeview/cli.py)
  │  writes a temp JSON config {port, file, python, cwd, version}
  │  and spawns Electron with VIBEVIEW_DESKTOP_CONFIG=<path>
  ▼
Electron main process (main.js)
  │  spawns:  <config.python> -m vibeview.cli serve --port <port>
  │           (cwd = the directory the user launched from)
  │  opens:   BrowserWindow → http://127.0.0.1:<port>
  ▼
vibe-view serve (directory browser + viewer launcher)
  │  GET /                 → browse page with quickstart panel
  │  GET /browse?dir=PATH  → browse any absolute directory
  │                          (⬆ Parent / ⌂ Home links, Open Folder…)
  │  GET /open?file=PATH   → spawns `vibe-view open PATH --no-browser`
  │                          on a free port, loading page polls
  │                          /open-status, then redirects
  ▼
per-file 3D viewer (Trame/PyVista/VTK) on its own port
```

Every path into a file — File → Open (Cmd+O), File → Open Recent,
drag-and-drop onto the window, a drop on the macOS dock icon, a `.qvf`
double-click via file association, or clicking a file on the browse
page — routes through `/open?file=…`. Directories go to the browse
page instead: File → Open Folder… (Cmd+Shift+O), dropping a folder on
the window, or the ⬆ Parent / ⌂ Home links on the browse page itself.

`vibe-view open` handles the actual loading, so everything the CLI can
open works here too: `.qvf`, `.xyz`, `.cif`, `.pdb`, `.cube`, vibe-qc
input scripts (`.py`), and friends (see `converters.detect_format`).

Recent files live in `~/.cache/vibe-view/recent.json`, shared between
the Electron menu and the `vibe-view recent` CLI command.

The source uninstaller deletes a macOS Applications copy only when its marker
and launcher both resolve to the checkout running the command. Packaged,
unknown, symlinked, and other-checkout apps stay in place. User data, including
recents, settings, logs, window state, and the app-managed environment, is
preserved. Close every desktop window and CLI/server launch first.
`--keep-desktop` also keeps the checkout's Electron runtime and source app for
a later reinstall.

## Prerequisites

| Requirement | Version | Install |
|-------------|---------|---------|
| Python | 3.11+ | https://python.org/ |
| vibe-view | 2.0.0+ | `pip install -e 'vibe-view[viewer]'` |

Node.js is **not** required for `vibe-view desktop` — the Electron
binary comes straight from GitHub releases. Node is only needed for
`npm start` (development) or `electron-builder` (packaging). The reviewed
Electron 43 development toolchain requires Node.js 22.12 or newer.

Linux also needs the normal GTK3 desktop runtime used by Electron. On desktop
installations it is usually already present. Minimal Debian 12 or Ubuntu 22.04
images can install the runtime set with:

```bash
sudo apt-get install libgtk-3-0 libnss3 libatk-bridge2.0-0 \
  libdrm2 libxkbcommon0 libgbm1 libasound2
```

Debian 13 and Ubuntu 24.04 use the time64 package names instead:

```bash
sudo apt-get install libgtk-3-0t64 libnss3 libatk-bridge2.0-0t64 \
  libdrm2 libxkbcommon0 libgbm1 libasound2t64
```

Launch as a regular user, without `sudo`. If a restricted container or Linux
security policy blocks Chromium user namespaces, fix that sandbox policy when
possible. `vibe-view desktop --no-sandbox` is an explicit last-resort option
for trusted local files and prints a warning because it weakens isolation.

The source installer installs `[viewer,tui]`, records its interpreter for
reuse by an installed or packaged app, and leaves the Electron download lazy.
Pass `--with-electron` to download and brand the runtime during installation.
Use `./vibe-view/scripts/update-desktop.sh` from the checkout root to update
Git, refresh the Python environment, synchronize the reviewed Electron engine,
and refresh the source-backed Applications copy in one step. A packaged app
from a DMG is deliberately left to its packaged-update workflow.

## Development Mode

```bash
cd vibe-view/electron
python3 install-electron.py   # one-time binary download (or: npm run install-electron)
npm start                     # launches via the wrapper cli.js
```

Without a `VIBEVIEW_DESKTOP_CONFIG` handshake, `main.js` falls back to
port 8765, auto-detected `python3`, and the current directory.

### Project structure

```
electron/
├── main.js              # Electron main process
├── preload.js           # contextBridge for the setup page (setup.html)
├── setup.html           # first-run onboarding page (no Python / vibe-view / [viewer])
├── package.json         # app metadata + electron-builder config
├── install-electron.py  # binary download + npm wrappers + macOS branding
├── make-icon.py         # regenerates icon.png / icon.icns (Pillow)
├── icon.png             # window/tray/dock icon (committed)
├── icon.icns            # macOS bundle icon (committed)
└── README.md            # this file
```

### Key parts of main.js

| Section | What it does |
|---------|-------------|
| `loadDesktopConfig()` | Reads the CLI handshake config (port, file, python, cwd) |
| `resolveEnvironment()` | Classifies the machine: ready / no-python / no-vibeview / no-viewer |
| `launch()` | Starts the server + main window, or falls back to the setup window |
| `showSetupWindow()` / `registerSetupIpc()` | First-run onboarding (`setup.html` + `preload.js`); recheck restarts launch in place |
| `maybeShowWindowsNotice()` | Windows only: one-time "untested, send feedback" notice (gated by a `userData` flag) |
| `startServer(pythonCmd)` | Spawns `vibe-view serve` with the resolved interpreter |
| `openFile(fp)` | Routes any file through `/open?file=…`, tracks recents, sets title |
| `buildRecentSubmenu()` | File → Open Recent from `~/.cache/vibe-view/recent.json` |
| `createMenu()` | Application menu (File / View / Help) |
| `will-navigate` handler | Drag-and-drop: file:// navigation → `openFile()` |
| `open-file` handler | macOS dock-drop / Finder file association |
| `loadWindowState()` / `saveWindowState()` | Persist window position/size |

## Branding

In dev mode the dock and menu-bar names come from the Electron bundle's
`Info.plist`, not from `app.setName()`. `install-electron.py` therefore
rebrands the downloaded `Electron.app` on macOS: it rewrites
`CFBundleName` / `CFBundleDisplayName` / `CFBundleIdentifier`
(`com.vibeqc.vibeview`), installs `icon.icns` into the bundle, and
ad-hoc re-signs (`codesign --force --deep --sign -`) — plist edits
invalidate the stock signature and arm64 macOS refuses unsigned
binaries. The step is idempotent. For a normal user-facing refresh, run
`./vibe-view/scripts/update-desktop.sh` from the checkout root. Directly
running `python3 install-electron.py` remains a developer helper.

To change the icon: edit `make-icon.py`, run `npm run make-icon`, copy
`icon.icns` over
`node_modules/electron/dist/Electron.app/Contents/Resources/vibeview.icns`,
and re-sign with `codesign --force --deep --sign - <path to Electron.app>`
(the installer skips already-branded bundles, so refresh the icon by
hand or delete `dist/` and reinstall).

## Packaging a distributable desktop app

The dev-mode setup above already gives a native branded window. For a
packaged, double-clickable desktop shell, use the build script:

```bash
cd vibe-view/electron
./build-desktop.sh          # native host: macOS dmg/zip or Linux AppImage
./build-desktop.sh --dir    # fast unpacked dev build for the current host
./build-desktop.sh --mac    # explicit macOS package
./build-desktop.sh --linux  # explicit Linux package
```

The `build` section of `package.json` carries the electron-builder
config (appId, icons, `.qvf` file association, and the `publish` feed).
Notes:

- The packaged app still runs the Python server from the environment it
  finds via `findPython()` — vibe-view must be pip-installed on the
  target machine. Full Python bundling (PyInstaller of
  `vibeview.cli serve` dropped into `Contents/Resources`) is roadmap
  material, not implemented.
- Distribution outside your own machines needs a Developer ID
  certificate + notarization; ad-hoc signatures do not provide Developer ID
  or Gatekeeper trust for external distribution.
- Cross-platform: `npx electron-builder --linux` / `--win` work on
  their native platforms; cross-builds need CI setup. **Windows caveat:**
  the `--win` (`nsis`) target and the Windows-specific runtime bits (the
  `py`-launcher Python discovery in `findPython()`, the
  `.venv\Scripts\python.exe` dev path) are **shipped but untested** — the
  maintainers have no Windows machine. The Windows installer is also
  unsigned, so SmartScreen warns on first run. Building it needs a Windows
  host or CI runner (electron-builder cannot cross-build Windows from
  macOS/Linux). Windows issues cannot be reproduced on this side, but the
  developer is glad to help anyone who wants to try — the app shows a
  one-time notice saying so on its first Windows launch.

## Auto-update

The packaged app ships electron-updater (bundled in the app.asar) wired to a
generic update feed at `https://vibe-qc.com/updates/vibe-view/`. The published
macOS arm64 package's "Check for Updates" menu items (App menu + tray) trigger
a real check when the feed is reachable. That feed currently advertises the
older 2.10.0 package, while source installs are at 2.14.1; it is not a way to
update a source checkout. Local Intel macOS packages are kept off that
arm64-only feed. Building + publishing a release, the
external feed-host configuration, and the macOS Developer-ID signing gate
(required for self-install; ad-hoc builds can only detect + notify)
are documented in **[`PUBLISHING.md`](PUBLISHING.md)**.

That feed belongs only to packaged apps containing `app.asar`. A source launch
loads the checkout's live `main.js` and editable Python package, so it updates
with `./vibe-view/scripts/update-desktop.sh` from the checkout root. The source
updater marks its Applications copy with the owning checkout, refuses unknown
or packaged bundles, and needs an explicit `--adopt-desktop` before taking over
a source app from another checkout. Linux and Windows require their current
replacement artifacts until platform feed support is enabled in the app and
their feed manifests are published. The builder therefore publishes no Linux
or Windows feed automatically.

## Troubleshooting

**"Electron directory not found"**
→ `vibe-view desktop` needs a source checkout (`vibe-view/electron/`);
it is not shipped in the wheel.

**"Server did not start" / the setup screen appears**
→ The app couldn't find a Python with `vibe-view[viewer]`. The setup window
(`setup.html`) names which case applies and lets you install + recheck. To
debug the server directly, run it in the launching environment:
`python3 -m vibeview.cli serve --port 8765`.

**"Port 8765 already in use"**
→ Another instance is running. Quit it, or `vibe-view desktop --port 9090`.

**App still shows as "Electron" in the dock**
→ Quit the app, run `./vibe-view/scripts/update-desktop.sh` from the checkout
root, and relaunch. Use the direct Python installer only when developing the
Electron tooling itself.

**Blank window / viewer never loads**
→ Check the terminal: `[vibe-view]`-prefixed lines are the server log.
The per-file viewer takes a few seconds on first open (VTK cold start);
the loading page polls until it is up.
