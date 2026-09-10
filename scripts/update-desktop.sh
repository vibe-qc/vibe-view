#!/usr/bin/env bash
# Update vibe-view's source checkout, Python environment, and source-backed
# Electron desktop application in one command.
#
# All update.sh options are accepted. Packaged applications installed from a
# DMG or an update feed are deliberately not replaced by this source updater.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VIBE_VIEW_DESKTOP_WRAPPER=1
exec "$SCRIPT_DIR/update.sh" --desktop "$@"
