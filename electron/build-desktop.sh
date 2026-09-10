#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

print_help() {
    cat <<'EOF'
USAGE
    ./build-desktop.sh [--dir|--mac|--linux|--win]

With no selector, build the native macOS or Linux package for this host.
Use --dir for a fast unpacked development build. All artifacts remain local.
Operators publish reviewed artifacts through their separate deployment tooling.
EOF
}

if [ "$#" -eq 1 ] && { [ "$1" = "-h" ] || [ "$1" = "--help" ]; }; then
    print_help
    exit 0
fi

if [ "$#" -gt 1 ]; then
    print_help >&2
    exit 1
fi

BUILD_MODE="${1:-}"
if [ -z "$BUILD_MODE" ]; then
    case "$(uname -s)" in
        Darwin) BUILD_MODE="--mac" ;;
        Linux)  BUILD_MODE="--linux" ;;
        *)
            echo "Unsupported build host: $(uname -s)" >&2
            echo "Pass --win on Windows from a Bash-compatible shell." >&2
            exit 1
            ;;
    esac
fi
case "$BUILD_MODE" in
    --dir|--mac|--linux|--win) ;;
    *)
        print_help >&2
        exit 1
        ;;
esac

echo "=== vibe-view Desktop App Builder ==="

# Check Node.js
if ! command -v node &> /dev/null; then
    echo "Node.js is required. Install from https://nodejs.org/"
    exit 1
fi

echo "Node.js: $(node --version)"
echo "npm: $(npm --version)"

# Install dependencies (electron + electron-builder + electron-updater).
# electron-builder fetches its own Electron dist via @electron/get, so a
# blocked `electron` postinstall (allow-scripts policies) does not stop the
# packaging step — only the `npm start` dev path needs install-electron.py.
if [ ! -x "node_modules/.bin/electron-builder" ] || \
   [ ! -f "node_modules/electron-updater/package.json" ]; then
    echo "Installing locked npm packaging dependencies..."
    npm ci
fi

# ── Build ───────────────────────────────────────────────────────────────
# With no option, build the native host target: dmg + zip on macOS and an
# AppImage on Linux. Explicit --mac / --linux / --win selectors are useful to
# make automation self-documenting. --dir asks electron-builder for a fast
# unpacked build for the current host (no installer or update manifest).
echo "Building desktop app (electron-builder ${BUILD_MODE})..."
npx electron-builder "${BUILD_MODE}" --publish never

echo ""
echo "Desktop app built in dist/"
ls -lh dist/ 2>/dev/null || echo "  (no output yet)"

# ── Verify electron-updater is bundled ──────────────────────────────────
# The build.files whitelist (["main.js","icon.png"]) does NOT exclude
# production node_modules — electron-builder adds the prod-dependency tree on
# top of the whitelist. This check fails the build loudly if that ever
# regresses, since a missing electron-updater silently disables auto-update.
# electron-builder names unpacked output by platform and architecture:
# mac/ on Intel, mac-arm64/ on Apple silicon, and *-unpacked elsewhere.
shopt -s nullglob
ASAR_FILES=(
    dist/mac*/vibe-view.app/Contents/Resources/app.asar
    dist/linux*-unpacked/resources/app.asar
    dist/win*-unpacked/resources/app.asar
)
shopt -u nullglob
if [ "${#ASAR_FILES[@]}" -eq 0 ]; then
    echo "✗ No packaged app.asar found — cannot verify electron-updater."
    exit 1
fi
for ASAR in "${ASAR_FILES[@]}"; do
    if npx asar list "$ASAR" | grep -F "electron-updater/out/AppUpdater.js" >/dev/null; then
        echo "✓ electron-updater is bundled in $ASAR"
    else
        echo "✗ electron-updater NOT found in $ASAR — auto-update would be dead."
        echo "  Add \"node_modules/**/*\" to build.files in package.json and rebuild."
        exit 1
    fi
done

echo "Build verified. Artifacts remain local; see PUBLISHING.md for update feeds."
