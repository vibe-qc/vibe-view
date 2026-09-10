#!/usr/bin/env bash
# Install vibe-view from this source checkout.
#
# USAGE
#     ./vibe-view/scripts/install.sh [OPTIONS]
#
# OPTIONS
#     --extras GROUP        Capability profile (default: modes):
#                           modes = standalone/core + browser + desktop + TUI
#                           core  = QVF CLI, show, capture, export
#                           viewer = core + browser/desktop server
#                           tui   = core + interactive terminal viewer
#                           all   = every optional vibe-view integration
#                           test  = viewer/TUI plus the test dependencies
#     --python BIN          Python used to create the venv (default: python3).
#     --venv PATH           Venv path (default: vibe-view/.venv). Relative
#                           paths are resolved from the vibe-view directory.
#     --force               Replace an existing checkout-owned virtualenv.
#     --adopt-legacy        With --force, permit an unmarked legacy venv only
#                           when trusted PEP 610 metadata proves it came from
#                           this exact vibe-view checkout.
#     --with-electron       Synchronize the reviewed Electron runtime and wrappers.
#                           A first-time download is about 120 MB. Otherwise,
#                           `vibe-view desktop` downloads it on first launch.
#                           Requires a profile containing the viewer.
#     --adopt-desktop       With --with-electron, let this checkout replace a
#                           source-backed desktop app owned by another checkout.
#     --dock                macOS: pin the source-backed vibe-view.app to the
#                           Dock. Requires --with-electron.
#     --link-bin DIR        Create a marked `vibe-view` launcher in DIR so the
#                           command works from any shell. DIR must exist, be
#                           writable, and be on your PATH (e.g.
#                           /opt/homebrew/bin on Homebrew macOS).
#     --dry-run             Print the resolved install without changing files.
#     -h, --help            Show this help.
#
# EXAMPLES
#     ./vibe-view/scripts/install.sh
#     ./vibe-view/scripts/install.sh --with-electron
#     ./vibe-view/scripts/install.sh --extras core
#     ./vibe-view/scripts/install.sh --python python3.13 --venv .venv-py313
#     ./vibe-view/scripts/install.sh --force
#     ./vibe-view/scripts/install.sh --with-electron --dock \
#         --link-bin /opt/homebrew/bin
#
# The default source install is intentionally self-contained under
# vibe-view/.venv and does not build or install vibe-qc. The scripts support
# macOS and Linux; on Windows use the equivalent py -m venv / pip commands.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_venv_helpers.sh
. "$SCRIPT_DIR/_venv_helpers.sh"

PYTHON_BIN="python3"
VENV_INPUT="${VIBE_VIEW_VENV:-.venv}"
EXTRAS_PROFILE="modes"
FORCE=0
ADOPT_LEGACY=0
WITH_ELECTRON=0
ADOPT_DESKTOP=0
DOCK=0
LINK_BIN_DIR=""
DRY_RUN=0

print_help() {
    awk '/^# USAGE/ {p=1} p && !/^#/ {exit} p {sub(/^# ?/, ""); print}' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --extras)
            [ $# -ge 2 ] || { echo "Error: --extras requires an argument." >&2; exit 1; }
            case "$2" in
                -*) echo "Error: --extras requires a profile, not option '$2'." >&2; exit 1 ;;
            esac
            EXTRAS_PROFILE="$2"
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
        --venv)
            [ $# -ge 2 ] && [ -n "$2" ] || {
                echo "Error: --venv requires an argument (non-empty)." >&2
                exit 1
            }
            case "$2" in
                -*) echo "Error: --venv requires a path, not option '$2'." >&2; exit 1 ;;
            esac
            VENV_INPUT="$2"
            shift 2
            ;;
        --force)         FORCE=1; shift ;;
        --adopt-legacy)  ADOPT_LEGACY=1; shift ;;
        --with-electron) WITH_ELECTRON=1; shift ;;
        --adopt-desktop) ADOPT_DESKTOP=1; shift ;;
        --dock)          DOCK=1; shift ;;
        --link-bin)
            [ $# -ge 2 ] && [ -n "$2" ] || {
                echo "Error: --link-bin requires an argument (non-empty)." >&2
                exit 1
            }
            case "$2" in
                -*) echo "Error: --link-bin requires a directory, not option '$2'." >&2; exit 1 ;;
            esac
            LINK_BIN_DIR="$2"
            shift 2
            ;;
        --dry-run)       DRY_RUN=1; shift ;;
        -h|--help)       print_help; exit 0 ;;
        *)
            echo "Error: unknown argument '$1'." >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

vibe_view_refuse_privileged_lifecycle
vibe_view_extras_to_spec "$EXTRAS_PROFILE" EXTRAS_SPEC
OPERATION="install"
[ "${VIBE_VIEW_REINSTALL_WRAPPER:-0}" = "1" ] && OPERATION="reinstall"
RAW_VENV_TARGET="$(vibe_view_path_from_project "$VENV_INPUT")"
if [ -e "$RAW_VENV_TARGET" ] || [ -L "$RAW_VENV_TARGET" ]; then
    vibe_view_select_trusted_python PYTHON_BIN "$PYTHON_BIN" "$RAW_VENV_TARGET"
    vibe_view_resolve_removal_path VENV_PATH "$RAW_VENV_TARGET" "$PYTHON_BIN"
else
    vibe_view_assert_python "$PYTHON_BIN"
    vibe_view_resolve_venv_path VENV_PATH "$VENV_INPUT" "$PYTHON_BIN"
fi
if [ "$WITH_ELECTRON" = "1" ] && ! vibe_view_profile_has_viewer "$EXTRAS_PROFILE"; then
    echo "Error: --with-electron requires an extras profile containing the viewer." >&2
    exit 1
fi
if [ "$ADOPT_DESKTOP" = "1" ] && [ "$WITH_ELECTRON" != "1" ]; then
    echo "Error: --adopt-desktop requires --with-electron." >&2
    exit 1
fi
if [ "$DOCK" = "1" ] && [ "$WITH_ELECTRON" != "1" ]; then
    echo "Error: --dock requires --with-electron." >&2
    exit 1
fi
if [ "$DOCK" = "1" ] && [ "$(uname -s)" != "Darwin" ]; then
    echo "Error: --dock is only available on macOS." >&2
    exit 1
fi
vibe_view_assert_safe_venv_target "$VENV_PATH"
VENV_HAD_ORIGINAL=0
[ -e "$VENV_PATH" ] && VENV_HAD_ORIGINAL=1
if [ "$ADOPT_LEGACY" = "1" ] && [ "$FORCE" != "1" ]; then
    echo "Error: --adopt-legacy requires --force." >&2
    exit 1
fi
if [ "$ADOPT_LEGACY" = "1" ] && [ "$VENV_HAD_ORIGINAL" != "1" ]; then
    echo "Error: --adopt-legacy requires an existing legacy virtualenv." >&2
    exit 1
fi
if [ "$VENV_HAD_ORIGINAL" = "1" ] && [ "$FORCE" = "1" ]; then
    # Dry runs and real replacements share the same non-executing ownership
    # proof. PYTHON_BIN was selected outside the target environment above.
    vibe_view_assert_owned_standalone_venv \
        "$VENV_PATH" "$PYTHON_BIN" "$ADOPT_LEGACY"
fi

echo "==> vibe-view $OPERATION"
echo "    source:       $VIBE_VIEW_PROJECT_DIR"
echo "    venv:         $VENV_PATH"
echo "    python:       $PYTHON_BIN"
echo "    capabilities: $EXTRAS_PROFILE  (pip suffix: '${EXTRAS_SPEC:-<core>}')"
if [ "$WITH_ELECTRON" = "1" ]; then
    echo "    Electron:     synchronize reviewed runtime and desktop wrappers"
    [ "$ADOPT_DESKTOP" = "1" ] && echo "    Desktop:      adopt a source app from another checkout"
    [ "$DOCK" = "1" ] && echo "    Dock:         pin the desktop app to the macOS Dock"
elif vibe_view_profile_has_viewer "$EXTRAS_PROFILE"; then
    echo "    Electron:     download automatically on first desktop launch"
else
    echo "    Electron:     not included in the '$EXTRAS_PROFILE' profile"
fi
[ "$FORCE" = "1" ] && echo "    --force:      replace the existing virtualenv"
[ "$ADOPT_LEGACY" = "1" ] && echo "    --adopt-legacy: trusted PEP 610 ownership proof required"
[ -n "$LINK_BIN_DIR" ] && echo "    Command:      link '$LINK_BIN_DIR/vibe-view' to this environment"
[ "$DRY_RUN" = "1" ] && echo "    --dry-run:    no files will be changed"
echo

if [ "$DRY_RUN" = "1" ]; then
    if [ -e "$VENV_PATH" ]; then
        if [ "$FORCE" = "1" ]; then
            echo "    [venv] is owned by this checkout and would be replaced atomically."
        else
            echo "    [venv] exists; install would refuse without --force."
        fi
    else
        echo "    [venv] would be created."
    fi
    echo "    [pip] would install build + vibe-view${EXTRAS_SPEC} from this checkout."
    if [ "$WITH_ELECTRON" = "1" ]; then
        echo "    [desktop] would synchronize Electron and refresh wrappers."
        [ "$DOCK" = "1" ] && echo "    [dock] would pin the desktop app to the macOS Dock."
    fi
    if [ -n "$LINK_BIN_DIR" ]; then
        echo "    [link-bin] would create '$LINK_BIN_DIR/vibe-view'."
    fi
    echo
    echo "==> Dry-run complete."
    exit 0
fi

vibe_view_acquire_target_lock "$VENV_PATH" "$OPERATION" "$PYTHON_BIN"
trap 'vibe_view_release_target_lock' EXIT
vibe_view_acquire_checkout_lock

VENV_REPLACEMENT_EXPECTED_ORIGINAL=0
if [ -e "$VENV_PATH" ] || [ -L "$VENV_PATH" ]; then
    VENV_REPLACEMENT_EXPECTED_ORIGINAL=1
    if [ "$FORCE" != "1" ]; then
        cat >&2 <<EOF
Error: '$VENV_PATH' already exists.

Update it in place:
    $SCRIPT_DIR/update.sh --skip-git --venv "$VENV_PATH"

Or replace it after checking the path:
    $SCRIPT_DIR/install.sh --force --venv "$VENV_PATH"
EOF
        exit 1
    fi
    vibe_view_assert_owned_standalone_venv \
        "$VENV_PATH" "$PYTHON_BIN" "$ADOPT_LEGACY"
fi

vibe_view_begin_venv_replacement \
    "$VENV_PATH" "$VENV_REPLACEMENT_EXPECTED_ORIGINAL"
trap 'vibe_view_abort_venv_replacement; vibe_view_release_target_lock' EXIT
vibe_view_start_venv_replacement "$VENV_PATH" "$PYTHON_BIN" "$ADOPT_LEGACY"

echo "==> Creating replacement virtualenv: $VENV_PATH"
"$PYTHON_BIN" -m venv "$VENV_PATH"
vibe_view_install_environment "$VENV_PATH" "$EXTRAS_SPEC"
vibe_view_verify_environment "$VENV_PATH" "$EXTRAS_PROFILE" 0
if [ "$WITH_ELECTRON" = "1" ]; then
    ELECTRON_ARGS=()
    [ "$ADOPT_DESKTOP" = "1" ] && ELECTRON_ARGS+=(--refresh-app --adopt-app)
    [ "$DOCK" = "1" ] && ELECTRON_ARGS+=(--dock)
    if [ ${#ELECTRON_ARGS[@]} -gt 0 ]; then
        vibe_view_install_electron "$VENV_PATH" "${ELECTRON_ARGS[@]}"
    else
        vibe_view_install_electron "$VENV_PATH"
    fi
fi
if [ -n "$LINK_BIN_DIR" ]; then
    vibe_view_link_bin "$VENV_PATH" "$LINK_BIN_DIR"
fi
if vibe_view_profile_has_viewer "$EXTRAS_PROFILE"; then
    vibe_view_record_interpreter "$VENV_PATH"
fi
vibe_view_mark_standalone_venv "$VENV_PATH"
vibe_view_commit_venv_replacement
trap 'vibe_view_release_target_lock' EXIT

echo
echo "==> vibe-view $OPERATION complete."
echo
echo "    Activate it:"
echo "        source \"$VENV_PATH/bin/activate\""
echo
if vibe_view_profile_has_viewer "$EXTRAS_PROFILE"; then
    echo "    Browser:  $VENV_PATH/bin/vibe-view open FILE.qvf"
    echo "    Desktop:  $VENV_PATH/bin/vibe-view desktop [FILE.qvf]"
fi
if vibe_view_profile_has_tui "$EXTRAS_PROFILE"; then
    echo "    TUI:      $VENV_PATH/bin/vibe-view tui FILE.qvf"
fi
echo "    Terminal: $VENV_PATH/bin/vibe-view show FILE.qvf"
[ -n "$LINK_BIN_DIR" ] && echo "    Command:  $LINK_BIN_DIR/vibe-view"
[ "$DOCK" = "1" ] && echo "    Dock:     pinned to the macOS Dock (see the Electron step above)"
echo "    Build:    $SCRIPT_DIR/build.sh"
