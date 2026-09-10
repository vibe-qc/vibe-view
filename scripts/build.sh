#!/usr/bin/env bash
# Build vibe-view wheel for distribution.
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJ_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
# shellcheck source=_venv_helpers.sh
. "$SCRIPT_DIR/_venv_helpers.sh"
vibe_view_acquire_checkout_lock
cd "$PROJ_DIR"

# ── Resolve venv ──────────────────────────────────────────────────────────
PYTHON="${VIBE_VIEW_VENV_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
    VENV=""
    vibe_view_detect_venv VENV "${VIBE_VIEW_VENV:-}"
    if [[ -n "$VENV" ]]; then
        PYTHON="$VENV/bin/python"
    fi
fi
if [[ -z "$PYTHON" ]] || ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "build.sh: no usable vibe-view virtualenv found." >&2
    echo "Run: $SCRIPT_DIR/install.sh" >&2
    exit 1
fi
if ! "$PYTHON" -c 'import build.__main__' >/dev/null 2>&1; then
    echo "build.sh: the selected environment is missing the Python 'build' frontend." >&2
    echo "Run: $SCRIPT_DIR/update.sh --skip-git" >&2
    exit 1
fi

echo "=== vibe-view build ==="
echo "Python: $("$PYTHON" --version)"

# Clean
rm -rf dist/ build/ *.egg-info/

# Build wheel
"$PYTHON" -m build --wheel

echo ""
echo "=== Built wheels ==="
ls -lh dist/

echo ""
echo "Install: pip install dist/vibeview-*.whl"
