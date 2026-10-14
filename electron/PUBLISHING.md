# Desktop packaging and update feeds

The desktop build uses the generic update provider declared in `package.json`.
The build writes installers, update manifests and blockmaps locally. Deployment
accounts, SSH settings and destination paths are maintained by operators in a
private operations repository and supplied through external configuration.

From `electron/`, run `./build-desktop.sh --dir` for an unpacked development
build. A normal `./build-desktop.sh` creates the platform's packaged artifacts.
On supported macOS arm64 builds, the hosted updater reads the configured feed.
Linux builds currently produce an AppImage without publishing a hosted feed.

The desktop version is the product version. A release sets it in
`package.json` (`version`) and in both `version` entries of
`package-lock.json`, the top-level one and the one under `packages[""]`, in
the same commit as `pyproject.toml`; step 1 of `docs/release_process.md`
lists every site, and `tests/test_version_sites.py` fails when they
disagree. Do not bump the version for a packaging-only rebuild: an update
feed compares versions, so a rebuilt installer under an old number is never
offered, and one under a new number is a release.

Keep update manifests consistent with that version. Review only artifacts
from the intended build: stale files in `dist/` can otherwise enter a feed.
Signing and notarization use the platform's configured signing environment;
private keys and account credentials must never be committed.

The product build never uploads artifacts. Operators publish a reviewed build
from its explicit artifact directory using the separate private deployment
tooling and external SSH configuration. The site setup and recovery runbook
lives with that tooling.

## Bundled dependency licenses

`electron-updater` and its production dependency tree are bundled into the
redistributed app (`app.asar`). Retain their license and copyright notices
when redistributing the application. The existing updater dependency inventory
is preserved here alongside the portable packaging instructions:

| Package | License |
|---------|---------|
| electron-updater, builder-util-runtime, js-yaml, lazy-val, fs-extra, jsonfile, universalify, debug, ms, lodash.escaperegexp, lodash.isequal, tiny-typed-emitter | MIT |
| graceful-fs, semver | ISC |
| sax | BlueOak-1.0.0 |
| argparse | Python-2.0 |

This inventory covers the updater dependencies, not the complete Electron and
Chromium distribution. Their bundled third-party notices also remain applicable.
