#!/usr/bin/env bash
# scripts/launch.sh — convenience launcher for the viewer.
#
# Resolves the project venv, sanity-checks the install, and runs
# ``vibe-view open <qvf>`` with sensible defaults. Any extra flags
# after the QVF path are forwarded verbatim to the CLI.
#
# Usage:
#   ./scripts/launch.sh path/to/calculation.qvf
#   ./scripts/launch.sh calc.qvf --port 9000
#   ./scripts/launch.sh calc.qvf --host 0.0.0.0 --no-browser
#
# Environment overrides:
#   VIBE_VIEW_VENV       Explicit venv path (default: auto-detect
#                        .venv in this checkout first, then documented legacy
#                        environments).
#   VIBE_VIEW_PORT       Default port (default: 8080).
#   VIBE_VIEW_LOG_FILE   Override log path (default: see ``vibe-view
#                        open --help``).
#
# Exits non-zero on missing venv, missing ``vibe-view`` binary, or a
# missing/unreadable QVF.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# shellcheck source=_venv_helpers.sh
. "$SCRIPT_DIR/_venv_helpers.sh"

usage() {
    cat <<USAGE >&2
Usage: $(basename "$0") <file.qvf> [extra vibe-view flags...]

Common flags forwarded to vibe-view:
  --port N           TCP port (default: \$VIBE_VIEW_PORT or 8080)
  --host HOST        Bind interface (default: 127.0.0.1; use 0.0.0.0 for LAN)
  --no-browser       Don't auto-open the system browser
  --log-file PATH    Override the log file location

Run \`<venv>/bin/vibe-view open --help\` for the complete list.
USAGE
    exit 2
}

if [[ $# -lt 1 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
fi

QVF="$1"; shift
if [[ ! -f "$QVF" ]]; then
    echo "vibe-view-launch: QVF not found: $QVF" >&2
    exit 1
fi

# ── Resolve venv ──────────────────────────────────────────────────────────
VENV="${VIBE_VIEW_VENV:-}"
vibe_view_detect_venv VENV "$VENV" "vibe-view"
if [[ -z "$VENV" || ! -x "$VENV/bin/vibe-view" ]]; then
    cat <<EOF >&2
vibe-view-launch: no usable venv found.

Looked under \$VIBE_VIEW_VENV, the canonical standalone path
  $VIBE_VIEW_PROJECT_DIR/.venv
and the documented legacy candidates.

Install the viewer first:
  $SCRIPT_DIR/install.sh
EOF
    exit 1
fi

# ── Forward args (with defaults if absent) ────────────────────────────────
PORT="${VIBE_VIEW_PORT:-8080}"

EXTRA_ARGS=("$@")
has_flag() {
    local needle="$1"; shift
    for a in "$@"; do
        [[ "$a" == "$needle" || "$a" == "$needle="* ]] && return 0
    done
    return 1
}

if [[ ${#EXTRA_ARGS[@]} -eq 0 ]] || ! has_flag --port "${EXTRA_ARGS[@]}"; then
    EXTRA_ARGS+=(--port "$PORT")
fi
if [[ -n "${VIBE_VIEW_LOG_FILE:-}" ]] && ! has_flag --log-file "${EXTRA_ARGS[@]}"; then
    EXTRA_ARGS+=(--log-file "$VIBE_VIEW_LOG_FILE")
fi

# Resolve QVF to an absolute path so the log message is unambiguous and
# so vibe-view can resolve it even if the user cd's elsewhere later.
QVF_ABS="$( cd "$( dirname "$QVF" )" && pwd )/$( basename "$QVF" )"

echo "vibe-view-launch: venv=$VENV"
echo "vibe-view-launch: opening $QVF_ABS"

exec "$VENV/bin/vibe-view" open "$QVF_ABS" "${EXTRA_ARGS[@]}"
