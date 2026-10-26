# Included Python runtime

The Apple Silicon application contains CPython 3.13.16, PyVista 0.49.1,
VTK 9.7.1, the Trame browser server and its local JavaScript assets, NumPy,
Matplotlib, Plotly, RDKit 2026.9.1, Textual and psutil. It needs macOS 14 or
newer. Normal viewing, structure editing, SMILES conversion and local image
capture require no separately installed Python, pip, Homebrew or Node.js.

External integrations remain separate products: vibe-qc calculations,
vibe-queue remote jobs, vibe-sketch, ORCA conversion tools, Blender and POV-Ray.
ASE's additional formats, TREXIO and notebook widgets are not in this desktop
profile. Common structure formats and QVF viewing use the included readers.

## Build and verify

From a source checkout on Apple Silicon, install Node.js 22.12+ and a build
Python 3.12+, then run `cd electron && ./build-desktop.sh --mac`. The builder's
beforePack hook runs `scripts/build_desktop_runtime.py`; afterPack checks the
runtime again at its final application path, before signing. Direct calls to
electron-builder use these same hooks. Intel and universal Mac builds are
rejected until they have a separately qualified runtime lock.

`macos-arm64.lock.json` (installed as `runtime-lock.json`) pins every downloaded
input by URL and SHA-256. Update the complete dependency closure deliberately;
ordinary builds do not resolve newer packages. Downloads are verified on every
reuse in `.cache/desktop-runtime`. Python wheels are installed offline into the
standalone interpreter, together with a freshly built vibeview wheel. The build
checks dependency closure, strips installer tools and local installation
records, and records versions and the wheel hash in `runtime.json`. Do not
modify the interpreter under an installed, signed application.

The runtime uses Python's isolated mode, rejects mismatched versions and never
falls back to a user's Python. File-viewer subprocesses preserve isolation.
Source-checkout launchers retain their existing environment discovery.

## Licenses and source

vibe-view remains MPL-2.0. Its Python source and schema resources are included
under `python/lib/python3.13/site-packages/vibeview`, and Electron source is
in `app.asar`. The public project is https://github.com/vibe-qc/vibe-view.
Keep these source files and the MPL notice when redistributing the package.

Every Python distribution retains its `*.dist-info` license files; some also
ship notices beside their code and data (including Matplotlib's fonts). These
must remain with the runtime. Electron/Chromium and Node dependency notices
remain part of their normal Electron bundle and `app.asar` respectively.

`third-party/python/` supplements the wheel notices with:

- CPython and its native dependencies' notices from the matching
  [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
  release, plus zlib-ng's license omitted from that archive.
- VTK's matching upstream source archive, including its vendored dependency
  sources and license texts. Preserve the complete archive, including Eigen's
  MPL source. The runtime uses the unmodified upstream VTK wheel.
- RDKit's native dependency notices. Source supplements for Eigen and Cairo,
  and the pinned RDKit wheel/Conan recipes, accompany the unmodified RDKit
  wheel. Cairo is distributed under its MPL-1.1 option; the corresponding
  source and recipe/patch material are included.

The source supplements contain notices for optional upstream components too;
their presence does not mean every component was compiled into this wheel.
The input lock is the inventory of the exact archives and wheels used. Upstream
code retains its own licenses. Supporter access to official downloads does not
restrict the rights those licenses grant to recipients.

## Installed-package acceptance

Copy the app out of its DMG into a path with spaces. Use a fresh user profile,
a system-only PATH, invalid PYTHONHOME/PYTHONPATH, and no development virtualenv.
Confirm the backend executable and every per-file viewer come from the app;
open a QVF/XYZ structure and a volume, inspect the rendered scene, rotate it,
and capture an image. Quit and confirm its backend children exit. Validate the
signed app with `codesign --verify --deep --strict` and retain package hashes.

Local ad-hoc builds are development previews. Developer ID signing,
notarization, minimum-OS testing, supporter delivery and Mac App Store sandbox
qualification are separate release work. Never overwrite a published release
with a packaging preview of the same version.
