#!/usr/bin/env bash
# Safely remove a standalone vibe-view source installation.
#
# USAGE
#     ./vibe-view/scripts/uninstall.sh [OPTIONS]
#
# OPTIONS
#     --venv PATH           Explicit standalone venv. Default: auto-detect,
#                           preferring vibe-view/.venv.
#     --python BIN          Trusted Python used for ownership and desktop
#                           cleanup (default: python3).
#     --adopt-legacy        Permit an unmarked legacy venv only when trusted
#                           PEP 610 metadata links it to this exact checkout.
#     --keep-desktop        Keep the checkout-owned Electron runtime and any
#                           source-backed macOS Applications copy.
#     --keep-electron       Alias for --keep-desktop.
#     --dry-run             Show exactly what is in scope without removing it.
#     -h, --help            Show this help.
#
# EXAMPLES
#     ./vibe-view/scripts/uninstall.sh
#     ./vibe-view/scripts/uninstall.sh --dry-run
#     ./vibe-view/scripts/uninstall.sh --venv .venv-py313
#     ./vibe-view/scripts/uninstall.sh --keep-desktop
#
# The default removes only the dedicated viewer venv, this checkout's
# recognizable Electron runtime, a source-backed macOS app owned by this
# checkout, and the Dock tile / command link created by install.sh --dock /
# --link-bin. Packaged apps, other checkouts, QVF files, settings, recents,
# logs, and app-managed data environments are always preserved.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_venv_helpers.sh
. "$SCRIPT_DIR/_venv_helpers.sh"

EXPLICIT_VENV=""
VENV_OPTION_SET=0
PYTHON_BIN="python3"
ADOPT_LEGACY=0
KEEP_DESKTOP=0
DRY_RUN=0

print_help() {
    awk '/^# USAGE/ {p=1} p && !/^#/ {exit} p {sub(/^# ?/, ""); print}' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --venv)
            [ $# -ge 2 ] && [ -n "$2" ] || {
                echo "Error: --venv requires an argument (non-empty)." >&2
                exit 1
            }
            case "$2" in
                -*) echo "Error: --venv requires a path, not option '$2'." >&2; exit 1 ;;
            esac
            EXPLICIT_VENV="$2"
            VENV_OPTION_SET=1
            shift 2
            ;;
        --python)
            [ $# -ge 2 ] && [ -n "$2" ] || {
                echo "Error: --python requires an argument (non-empty)." >&2
                exit 1
            }
            case "$2" in
                -*) echo "Error: --python requires an executable, not option '$2'." >&2; exit 1 ;;
            esac
            PYTHON_BIN="$2"
            shift 2
            ;;
        --adopt-legacy)                 ADOPT_LEGACY=1; shift ;;
        --keep-desktop|--keep-electron) KEEP_DESKTOP=1; shift ;;
        --dry-run)                     DRY_RUN=1; shift ;;
        -h|--help)                     print_help; exit 0 ;;
        *)
            echo "Error: unknown argument '$1'." >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

vibe_view_refuse_privileged_lifecycle

if [ "$VENV_OPTION_SET" = "1" ]; then
    VENV_INPUT="$EXPLICIT_VENV"
else
    VENV_INPUT="${VIBE_VIEW_VENV:-}"
fi
VENV_SELECTION_REQUESTED=0
[ "$VENV_OPTION_SET" = "1" ] && VENV_SELECTION_REQUESTED=1
[ -n "${VIBE_VIEW_VENV:-}" ] && VENV_SELECTION_REQUESTED=1
ALLOW_LEGACY_PEP610="$ADOPT_LEGACY"
vibe_view_detect_removable_standalone_venv VENV_PATH "$VENV_INPUT"
if [ "$VENV_SELECTION_REQUESTED" = "1" ] && [ -z "$VENV_PATH" ]; then
    echo "Error: the selected standalone virtualenv does not exist:" >&2
    echo "       $(vibe_view_path_from_project "$VENV_INPUT")" >&2
    echo "Fix the path, or omit --venv/VIBE_VIEW_VENV for idempotent auto-detection." >&2
    exit 1
fi
if [ "$ADOPT_LEGACY" = "1" ] && [ -z "$VENV_PATH" ]; then
    echo "Error: --adopt-legacy requires an existing legacy virtualenv." >&2
    exit 1
fi

CLEANUP_PYTHON=""
if [ -n "$VENV_PATH" ] || [ "$KEEP_DESKTOP" != "1" ]; then
    # Ownership must be proven by a trusted interpreter, never by code from
    # the as-yet-untrusted environment selected for deletion.
    if [ -n "$VENV_PATH" ]; then
        vibe_view_select_trusted_python \
            CLEANUP_PYTHON "$PYTHON_BIN" "$VENV_PATH"
    else
        vibe_view_command_path CLEANUP_PYTHON "$PYTHON_BIN"
        vibe_view_assert_python "$CLEANUP_PYTHON"
    fi
fi
if [ -n "$VENV_PATH" ]; then
    vibe_view_resolve_removal_path VENV_PATH "$VENV_PATH" "$CLEANUP_PYTHON"
    vibe_view_assert_removable_venv "$VENV_PATH"
    vibe_view_assert_owned_standalone_venv \
        "$VENV_PATH" "$CLEANUP_PYTHON" "$ALLOW_LEGACY_PEP610"
fi

echo "==> vibe-view uninstall"
echo "    checkout:     $VIBE_VIEW_REPO_ROOT"
echo "    venv:         ${VENV_PATH:-<not found>}"
if [ "$KEEP_DESKTOP" = "1" ]; then
    echo "    Desktop:      preserved (--keep-desktop)"
else
    echo "    Desktop:      remove only this checkout's recognized runtime/app"
fi
echo "    User data:    preserved (config, settings, recents, logs, QVFs)"
[ "$DRY_RUN" = "1" ] && echo "    --dry-run:    no files will be changed"
[ "$ADOPT_LEGACY" = "1" ] && \
    echo "    adoption:     exact legacy PEP 610 ownership proof required"
echo
if [ -n "$VENV_PATH" ] || [ "$KEEP_DESKTOP" != "1" ]; then
    echo "Close every vibe-view desktop window and CLI launch before continuing."
    echo
fi

if [ "$DRY_RUN" != "1" ] && \
   { [ -n "$VENV_PATH" ] || [ "$KEEP_DESKTOP" != "1" ]; }; then
    LOCK_TARGET="${VENV_PATH:-$VIBE_VIEW_PROJECT_DIR/.venv}"
    vibe_view_acquire_target_lock \
        "$LOCK_TARGET" uninstall "$CLEANUP_PYTHON"
    trap 'vibe_view_release_target_lock' EXIT
    vibe_view_acquire_checkout_lock
    if [ -n "$VENV_PATH" ]; then
        # Repeat ownership proof while holding the common checkout/target
        # lifecycle lock, before any uninstall mutation begins.
        vibe_view_assert_owned_standalone_venv \
            "$VENV_PATH" "$CLEANUP_PYTHON" "$ALLOW_LEGACY_PEP610"
    fi
fi

if [ "$KEEP_DESKTOP" != "1" ]; then
    ELECTRON_ARGS=(--uninstall)
    [ "$DRY_RUN" = "1" ] && ELECTRON_ARGS+=(--dry-run)
    "$CLEANUP_PYTHON" "$VIBE_VIEW_PROJECT_DIR/electron/install-electron.py" \
        "${ELECTRON_ARGS[@]}"
fi

if [ -n "$VENV_PATH" ]; then
    if [ "$DRY_RUN" = "1" ]; then
        vibe_view_unlink_bin "$VENV_PATH" "$DRY_RUN"
        vibe_view_clear_matching_interpreter_record \
            "$CLEANUP_PYTHON" "$VENV_PATH" "$DRY_RUN"
        echo "Would remove standalone virtualenv -> $VENV_PATH"
    else
        # Keep the ownership check adjacent to recursive removal as a final
        # defense against non-cooperating filesystem changes.
        vibe_view_assert_owned_standalone_venv \
            "$VENV_PATH" "$CLEANUP_PYTHON" "$ALLOW_LEGACY_PEP610"
        vibe_view_unlink_bin "$VENV_PATH" 0
        vibe_view_remove_venv "$VENV_PATH"
        if ! vibe_view_clear_matching_interpreter_record \
            "$CLEANUP_PYTHON" "$VENV_PATH" 0; then
            echo "Warning: the stale desktop interpreter record could not be cleared." >&2
        fi
    fi
else
    echo "No standalone vibe-view virtualenv was found; nothing to remove there."
fi

echo
if [ "$DRY_RUN" = "1" ]; then
    echo "==> Dry-run complete."
else
    echo "==> vibe-view uninstall complete."
fi
echo "    Source files and user data were retained."
echo "    Reinstall later with: $SCRIPT_DIR/reinstall.sh"
