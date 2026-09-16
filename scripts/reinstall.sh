#!/usr/bin/env bash
# Reinstall vibe-view from this checkout without changing Git or deleting the
# current environment until its verified replacement is ready.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARGS=()
WITH_DESKTOP=0
ADOPT_DESKTOP=0

print_help() {
    cat <<'EOF'
USAGE
    ./scripts/reinstall.sh [OPTIONS]

DESCRIPTION
    Transactionally rebuild the standalone vibe-view environment from the
    current checkout. The previous environment is restored if creation,
    installation, verification, or optional Electron setup fails. Git and
    user data are unchanged.

OPTIONS
    --extras GROUP        modes (default), core, viewer, tui, all, or test.
    --python BIN          Python used to create the venv (default: python3).
    --venv PATH           Venv path (default: .venv).
    --adopt-legacy        Permit an unmarked legacy venv only when trusted
                          PEP 610 metadata links it to this exact checkout.
    --desktop             Also synchronize the reviewed Electron runtime and
                          strictly refresh the source-backed desktop app.
    --with-electron       Alias for --desktop.
    --adopt-desktop       Let this checkout take over a source app installed
                          by another checkout. Requires --desktop.
    --dry-run             Preview the replacement without changing files.
    -h, --help            Show this help.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            print_help
            exit 0
            ;;
        --desktop|--with-electron)
            WITH_DESKTOP=1
            shift
            ;;
        --adopt-desktop)
            ADOPT_DESKTOP=1
            ARGS+=("$1")
            shift
            ;;
        --adopt-legacy)
            ARGS+=("$1")
            shift
            ;;
        --extras|--python|--venv)
            [ $# -ge 2 ] && [ -n "$2" ] || {
                echo "Error: $1 requires an argument (non-empty)." >&2
                exit 1
            }
            case "$2" in
                -*) echo "Error: $1 requires a value, not option '$2'." >&2; exit 1 ;;
            esac
            ARGS+=("$1" "$2")
            shift 2
            ;;
        --dry-run)
            ARGS+=("$1")
            shift
            ;;
        *)
            echo "Error: unknown argument '$1'." >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

if [ "$ADOPT_DESKTOP" = "1" ] && [ "$WITH_DESKTOP" != "1" ]; then
    echo "Error: --adopt-desktop requires --desktop." >&2
    exit 1
fi

export VIBE_VIEW_REINSTALL_WRAPPER=1
if [ "$WITH_DESKTOP" = "1" ]; then
    if [ "${#ARGS[@]}" -gt 0 ]; then
        exec "$SCRIPT_DIR/update.sh" --skip-git --recreate-venv --desktop \
            "${ARGS[@]}"
    fi
    exec "$SCRIPT_DIR/update.sh" --skip-git --recreate-venv --desktop
fi
if [ "${#ARGS[@]}" -gt 0 ]; then
    exec "$SCRIPT_DIR/install.sh" --force "${ARGS[@]}"
fi
exec "$SCRIPT_DIR/install.sh" --force
