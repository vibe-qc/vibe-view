#!/usr/bin/env bash
# Update a standalone vibe-view source install.
#
# USAGE
#     ./vibe-view/scripts/update.sh [OPTIONS]
#
# OPTIONS
#     --dev                 Switch to and fast-forward `main`.
#     --release             Switch to and fast-forward `release`.
#     --branch NAME         Switch to a branch, tag, or commit before install.
#     --ref NAME            Back-compatible spelling of --branch.
#                           With no selector, update the current branch.
#     --extras GROUP        modes (default), core, viewer, tui, all, or test.
#     --python BIN          Python for --recreate-venv (default: python3;
#                           requires --recreate-venv).
#     --venv PATH           Explicit venv. Default: auto-detect, preferring
#                           vibe-view/.venv.
#     --recreate-venv       Safely replace the detected virtualenv.
#     --adopt-legacy        With --recreate-venv, permit an unmarked legacy
#                           venv only when trusted PEP 610 metadata proves it
#                           came from this exact vibe-view checkout.
#     --with-electron       Synchronize the reviewed Electron runtime and wrappers.
#                           Requires a profile containing the viewer.
#     --desktop             Also refresh the source-backed desktop app.
#                           Packaged apps from a DMG/feed are left untouched.
#                           Requires a profile containing the viewer.
#     --adopt-desktop       Let this checkout take over a source app installed
#                           by another checkout. Requires --desktop.
#     --skip-git            Reinstall from the current tree without fetching.
#     --dry-run             Preview only; does not fetch or change files.
#     -h, --help            Show this help.
#
# EXAMPLES
#     ./vibe-view/scripts/update.sh
#     ./vibe-view/scripts/update.sh --dev
#     ./vibe-view/scripts/update.sh --release
#     ./vibe-view/scripts/update.sh --skip-git
#     ./vibe-view/scripts/update.sh --recreate-venv
#     ./vibe-view/scripts/update.sh --with-electron
#     ./vibe-view/scripts/update.sh --desktop
#     ./vibe-view/scripts/update-desktop.sh
#
# Git operations apply to the whole vibe-qc checkout because vibe-view is a
# peer subproject in that repository. The Python reinstall touches only the
# standalone vibe-view environment. macOS and Linux are supported directly.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_venv_helpers.sh
. "$SCRIPT_DIR/_venv_helpers.sh"

BRANCH=""
BRANCH_SOURCE=""
USE_CONFIGURED_UPSTREAM=0
UPSTREAM_REMOTE=""
UPSTREAM_BRANCH=""
UPSTREAM_DISPLAY=""
EXTRAS_PROFILE="modes"
PYTHON_BIN="python3"
PYTHON_OPTION_SET=0
EXPLICIT_VENV=""
RECREATE_VENV=0
ADOPT_LEGACY=0
WITH_ELECTRON=0
UPDATE_DESKTOP=0
ADOPT_DESKTOP=0
SKIP_GIT=0
DRY_RUN=0

print_help() {
    if [ "${VIBE_VIEW_DESKTOP_WRAPPER:-0}" = "1" ]; then
        cat <<'EOF'
USAGE
    ./vibe-view/scripts/update-desktop.sh [OPTIONS]

DESCRIPTION
    Update vibe-view's checkout, standalone Python environment, reviewed
    Electron runtime, and source-backed desktop app. Desktop mode is implied.
    Packaged applications are never replaced.

OPTIONS
    --dev                 Switch to and fast-forward `main`.
    --release             Switch to and fast-forward `release` if it supports
                          the safe desktop update protocol.
    --branch NAME         Switch to a compatible branch, tag, or commit.
    --ref NAME            Back-compatible spelling of --branch.
    --extras GROUP        modes (default), viewer, all, or test. The selected
                          profile must contain the viewer dependencies.
    --python BIN          Python for --recreate-venv (default: python3;
                          requires --recreate-venv).
    --venv PATH           Explicit venv (default: vibe-view/.venv).
    --recreate-venv       Safely replace the detected virtualenv.
    --adopt-legacy        With --recreate-venv, adopt an unmarked legacy venv
                          only after trusted PEP 610 ownership proof.
    --adopt-desktop       Let this checkout take over a source app installed
                          by another checkout.
    --skip-git            Reinstall from the current tree without fetching.
    --dry-run             Preview only; does not fetch or change files.
    -h, --help            Show this help.
EOF
        return
    fi
    awk '/^# USAGE/ {p=1} p && !/^#/ {exit} p {sub(/^# ?/, ""); print}' "$0"
}

set_branch() {
    local value="$1"
    local source="$2"
    if [ -n "$BRANCH_SOURCE" ]; then
        echo "Error: '$source' conflicts with $BRANCH_SOURCE ($BRANCH)." >&2
        echo "Choose only one of --dev / --release / --branch / --ref." >&2
        exit 1
    fi
    BRANCH="$value"
    BRANCH_SOURCE="$source"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dev)     set_branch main --dev; shift ;;
        --release) set_branch release --release; shift ;;
        --branch)
            [ $# -ge 2 ] && [ -n "$2" ] || {
                echo "Error: --branch requires an argument (non-empty)." >&2
                exit 1
            }
            case "$2" in
                -*) echo "Error: --branch requires a ref name, not option '$2'." >&2; exit 1 ;;
            esac
            set_branch "$2" "--branch $2"
            shift 2
            ;;
        --ref)
            [ $# -ge 2 ] && [ -n "$2" ] || {
                echo "Error: --ref requires an argument (non-empty)." >&2
                exit 1
            }
            case "$2" in
                -*) echo "Error: --ref requires a ref name, not option '$2'." >&2; exit 1 ;;
            esac
            set_branch "$2" "--ref $2"
            shift 2
            ;;
        --extras)
            [ $# -ge 2 ] && [ -n "$2" ] || {
                echo "Error: --extras requires an argument (non-empty)." >&2
                exit 1
            }
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
            PYTHON_OPTION_SET=1
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
            EXPLICIT_VENV="$2"
            shift 2
            ;;
        --recreate-venv) RECREATE_VENV=1; shift ;;
        --adopt-legacy)  ADOPT_LEGACY=1; shift ;;
        --with-electron) WITH_ELECTRON=1; shift ;;
        --desktop)       UPDATE_DESKTOP=1; shift ;;
        --adopt-desktop) ADOPT_DESKTOP=1; shift ;;
        --skip-git)      SKIP_GIT=1; shift ;;
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
if [ "$PYTHON_OPTION_SET" = "1" ] && [ "$RECREATE_VENV" != "1" ]; then
    echo "Error: --python applies only when --recreate-venv creates a new environment." >&2
    exit 1
fi
if [ "$ADOPT_LEGACY" = "1" ] && [ "$RECREATE_VENV" != "1" ]; then
    echo "Error: --adopt-legacy requires --recreate-venv." >&2
    exit 1
fi
if { [ "$WITH_ELECTRON" = "1" ] || [ "$UPDATE_DESKTOP" = "1" ]; } && \
   ! vibe_view_profile_has_viewer "$EXTRAS_PROFILE"; then
    echo "Error: --with-electron and --desktop require an extras profile containing the viewer." >&2
    exit 1
fi
if [ "$ADOPT_DESKTOP" = "1" ] && [ "$UPDATE_DESKTOP" != "1" ]; then
    echo "Error: --adopt-desktop requires --desktop." >&2
    exit 1
fi
if [ "$SKIP_GIT" = "1" ] && [ -n "$BRANCH" ]; then
    echo "Error: --skip-git cannot be combined with a branch or ref selector." >&2
    exit 1
fi

# In local desktop mode, validate the installer protocol before invoking any
# selected environment or creating lock state. Historical copies lack the
# ownership guards needed for a safe Applications refresh.
if [ "$UPDATE_DESKTOP" = "1" ] && [ "$SKIP_GIT" = "1" ]; then
    vibe_view_assert_safe_desktop_installer
fi

if [ "$SKIP_GIT" != "1" ]; then
    if ! git -C "$VIBE_VIEW_REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
        echo "Error: update.sh needs a Git checkout (or use --skip-git)." >&2
        exit 1
    fi
    if [ -z "$BRANCH" ]; then
        BRANCH="$(git -C "$VIBE_VIEW_REPO_ROOT" symbolic-ref --quiet --short HEAD || true)"
        if [ -z "$BRANCH" ]; then
            echo "Error: HEAD is detached; choose --dev, --release, or --branch NAME." >&2
            exit 1
        fi
        BRANCH_SOURCE="current branch"
        USE_CONFIGURED_UPSTREAM=1
        UPSTREAM_REMOTE="$(
            git -C "$VIBE_VIEW_REPO_ROOT" config --get "branch.$BRANCH.remote" || true
        )"
        UPSTREAM_BRANCH="$(
            git -C "$VIBE_VIEW_REPO_ROOT" config --get "branch.$BRANCH.merge" || true
        )"
        if [ -z "$UPSTREAM_REMOTE" ] || [ -z "$UPSTREAM_BRANCH" ]; then
            cat >&2 <<EOF
Error: current branch '$BRANCH' has no configured remote upstream.

Set one explicitly, for example:
    git -C "$VIBE_VIEW_REPO_ROOT" branch --set-upstream-to REMOTE/BRANCH "$BRANCH"

Or reinstall this local checkout without fetching:
    $SCRIPT_DIR/update.sh --skip-git

Or choose a remote branch explicitly:
    $SCRIPT_DIR/update.sh --branch NAME
EOF
            exit 1
        fi
        case "$UPSTREAM_BRANCH" in
            refs/heads/*) UPSTREAM_BRANCH="${UPSTREAM_BRANCH#refs/heads/}" ;;
            *)
                echo "Error: branch '$BRANCH' has unsupported upstream '$UPSTREAM_BRANCH'." >&2
                echo "Configure a single remote branch, or use --skip-git." >&2
                exit 1
                ;;
        esac
        if [ "$UPSTREAM_REMOTE" != "." ] && \
           ! git -C "$VIBE_VIEW_REPO_ROOT" remote get-url \
                "$UPSTREAM_REMOTE" >/dev/null 2>&1; then
            echo "Error: configured upstream remote '$UPSTREAM_REMOTE' does not exist." >&2
            echo "Repair the branch upstream, or use --skip-git." >&2
            exit 1
        fi
        UPSTREAM_DISPLAY="$(
            git -C "$VIBE_VIEW_REPO_ROOT" for-each-ref \
                --format='%(upstream:short)' "refs/heads/$BRANCH"
        )"
        [ -n "$UPSTREAM_DISPLAY" ] || \
            UPSTREAM_DISPLAY="$UPSTREAM_REMOTE/$UPSTREAM_BRANCH"
    fi
fi

VENV_INPUT="${EXPLICIT_VENV:-${VIBE_VIEW_VENV:-}}"
vibe_view_detect_standalone_venv VENV_PATH "$VENV_INPUT"
OPERATION="update"
[ "${VIBE_VIEW_REINSTALL_WRAPPER:-0}" = "1" ] && OPERATION="reinstall"
if [ "$RECREATE_VENV" = "1" ]; then
    if [ -n "$VENV_PATH" ]; then
        VENV_TARGET="$VENV_PATH"
    elif [ -n "$VENV_INPUT" ]; then
        VENV_TARGET="$VENV_INPUT"
    else
        VENV_TARGET="$VIBE_VIEW_PROJECT_DIR/.venv"
    fi
    RAW_VENV_TARGET="$(vibe_view_path_from_project "$VENV_TARGET")"
    if [ -e "$RAW_VENV_TARGET" ] || [ -L "$RAW_VENV_TARGET" ]; then
        vibe_view_select_trusted_python \
            PYTHON_BIN "$PYTHON_BIN" "$RAW_VENV_TARGET"
        vibe_view_resolve_removal_path \
            VENV_PATH "$RAW_VENV_TARGET" "$PYTHON_BIN"
    else
        vibe_view_assert_python "$PYTHON_BIN"
        vibe_view_resolve_venv_path VENV_PATH "$VENV_TARGET" "$PYTHON_BIN"
    fi
elif [ -n "$VENV_PATH" ]; then
    # A healthy existing venv is self-sufficient. Use its interpreter for
    # canonicalisation so update does not depend on a separate `python3`
    # command that may be absent or older than 3.11.
    VENV_TARGET="$VENV_PATH"
    if ! vibe_view_resolve_venv_path \
        VENV_PATH "$VENV_TARGET" "$VENV_TARGET/bin/python"; then
        VENV_PATH="$VENV_TARGET"
    fi
fi
VENV_HAD_ORIGINAL=0
if [ "$RECREATE_VENV" = "1" ] && [ -e "$VENV_PATH" ]; then
    VENV_HAD_ORIGINAL=1
    # Dry runs and real replacements share the same ownership decision.
    vibe_view_assert_owned_standalone_venv \
        "$VENV_PATH" "$PYTHON_BIN" "$ADOPT_LEGACY"
fi
if [ "$ADOPT_LEGACY" = "1" ] && [ "$VENV_HAD_ORIGINAL" != "1" ]; then
    echo "Error: --adopt-legacy requires an existing legacy virtualenv." >&2
    exit 1
fi
if [ -n "$VENV_PATH" ]; then
    # Keep dry-run and real execution on the same destructive-target boundary.
    vibe_view_assert_safe_venv_target "$VENV_PATH"
fi

echo "==> vibe-view $OPERATION"
echo "    checkout:     $VIBE_VIEW_REPO_ROOT"
if [ "$SKIP_GIT" = "1" ]; then
    echo "    Git:          unchanged (--skip-git)"
else
    echo "    Git target:   $BRANCH ($BRANCH_SOURCE)"
    if [ "$USE_CONFIGURED_UPSTREAM" = "1" ]; then
        echo "    Git upstream: $UPSTREAM_DISPLAY"
    fi
fi
echo "    venv:         ${VENV_PATH:-<not found>}"
echo "    capabilities: $EXTRAS_PROFILE  (pip suffix: '${EXTRAS_SPEC:-<core>}')"
if [ "$UPDATE_DESKTOP" = "1" ]; then
    echo "    Desktop:      sync reviewed runtime + source app; protect packaged apps"
    [ "$ADOPT_DESKTOP" = "1" ] && \
        echo "    Ownership:    adopt a source app from another checkout"
elif [ "$WITH_ELECTRON" = "1" ]; then
    echo "    Electron:     synchronize reviewed runtime and desktop wrappers"
elif vibe_view_profile_has_viewer "$EXTRAS_PROFILE"; then
    echo "    Electron:     keep current runtime; first launch downloads if absent"
else
    echo "    Electron:     not included in the '$EXTRAS_PROFILE' profile"
fi
[ "$RECREATE_VENV" = "1" ] && echo "    --recreate-venv: replace the virtualenv"
[ "$ADOPT_LEGACY" = "1" ] && echo "    --adopt-legacy: trusted PEP 610 ownership proof required"
[ "$DRY_RUN" = "1" ] && echo "    --dry-run: no files will be changed"
echo

if [ "$DRY_RUN" = "1" ]; then
    if [ "$SKIP_GIT" != "1" ]; then
        if [ "$USE_CONFIGURED_UPSTREAM" = "1" ]; then
            if [ "$UPSTREAM_REMOTE" = "." ]; then
                echo "    [git] would fast-forward '$BRANCH' from its local configured"
                echo "          upstream '$UPSTREAM_DISPLAY' without fetching."
            else
                echo "    [git] would fetch '$UPSTREAM_REMOTE' and fast-forward '$BRANCH'"
                echo "          from its configured upstream '$UPSTREAM_DISPLAY'."
            fi
        else
            echo "    [git] would fetch origin and fast-forward/check out '$BRANCH'."
        fi
        echo "          (dry-run deliberately does not fetch.)"
    fi
    if [ "$RECREATE_VENV" = "1" ] && [ -e "$VENV_PATH" ]; then
        echo "    [venv] would replace atomically: $VENV_PATH"
    elif [ "$RECREATE_VENV" = "1" ]; then
        echo "    [venv] would create: $VENV_PATH"
    elif [ -n "$VENV_PATH" ]; then
        echo "    [venv] would update: $VENV_PATH"
    else
        echo "    [venv] none found; update would refuse and point to install.sh."
    fi
    echo "    [pip] would install build + vibe-view${EXTRAS_SPEC} from this checkout."
    if [ "$UPDATE_DESKTOP" = "1" ]; then
        echo "    [desktop] would validate/synchronize Electron and refresh the"
        echo "              source-backed app; packaged apps stay untouched."
    elif [ "$WITH_ELECTRON" = "1" ]; then
        echo "    [desktop] would synchronize Electron and refresh wrappers."
    fi
    echo
    echo "==> Dry-run complete."
    exit 0
fi

if [ -z "$VENV_PATH" ]; then
    cat >&2 <<EOF
Error: no standalone vibe-view virtualenv was found.

Create it once with:
    $SCRIPT_DIR/install.sh

Or name an existing one explicitly:
    $SCRIPT_DIR/update.sh --skip-git --venv PATH
EOF
    exit 1
fi

UPDATE_LOCKS_ACQUIRED=0
SHARED_UPDATE_LOCK_ACQUIRED=0
acquire_shared_update_lock() {
    local shared_lock_python="python3"

    [ "$SHARED_UPDATE_LOCK_ACQUIRED" = "0" ] || return 0
    if [ "$RECREATE_VENV" = "1" ]; then
        shared_lock_python="$PYTHON_BIN"
    else
        # Do not execute or trust an activated target's python3 merely to take
        # the common lock. The installed environment runs only after locking.
        vibe_toolset_find_external_python shared_lock_python "$VENV_PATH"
    fi
    vibe_view_acquire_target_lock \
        "$VENV_PATH" "$OPERATION" "$shared_lock_python"
    trap 'vibe_view_release_target_lock' EXIT
    SHARED_UPDATE_LOCK_ACQUIRED=1
}

acquire_update_locks() {
    [ "$UPDATE_LOCKS_ACQUIRED" = "0" ] || return 0
    acquire_shared_update_lock
    vibe_view_acquire_checkout_lock
    if [ "$VENV_HAD_ORIGINAL" = "1" ]; then
        # Recheck after taking the cross-platform lifecycle lock.
        vibe_view_assert_owned_standalone_venv \
            "$VENV_PATH" "$PYTHON_BIN" "$ADOPT_LEGACY"
    fi
    UPDATE_LOCKS_ACQUIRED=1
}

VENV_PREFLIGHT_COMPLETE=0
VENV_REPLACEMENT_EXPECTED_ORIGINAL=0
prepare_venv_update() {
    [ "$VENV_PREFLIGHT_COMPLETE" = "0" ] || return 0
    if [ "$RECREATE_VENV" = "1" ]; then
        VENV_REPLACEMENT_EXPECTED_ORIGINAL=0
        if [ -e "$VENV_PATH" ] || [ -L "$VENV_PATH" ]; then
            VENV_REPLACEMENT_EXPECTED_ORIGINAL=1
            vibe_view_assert_owned_standalone_venv \
                "$VENV_PATH" "$PYTHON_BIN" "$ADOPT_LEGACY"
        fi
        # Arm cleanup before checkout mutation. The old venv stays in place
        # until replacement work begins and is restored after any later error.
        vibe_view_begin_venv_replacement \
            "$VENV_PATH" "$VENV_REPLACEMENT_EXPECTED_ORIGINAL"
        trap 'vibe_view_abort_venv_replacement; vibe_view_release_target_lock' EXIT
    elif ! vibe_view_check_venv_health "$VENV_PATH"; then
        echo "Re-run with --recreate-venv to replace it safely." >&2
        exit 1
    fi
    VENV_PREFLIGHT_COMPLETE=1
}

# Serialize this checkout before fetch/switch/pull. Desktop revision checks may
# still defer executing the installed environment until after inspection, but
# the shared lock itself uses only a trusted isolated external Python.
acquire_shared_update_lock
if [ "$UPDATE_DESKTOP" != "1" ] || [ "$SKIP_GIT" = "1" ]; then
    acquire_update_locks
    prepare_venv_update
fi

if [ "$SKIP_GIT" != "1" ]; then
    if ! git -C "$VIBE_VIEW_REPO_ROOT" diff --quiet || \
       ! git -C "$VIBE_VIEW_REPO_ROOT" diff --cached --quiet; then
        echo "Error: the checkout has uncommitted tracked changes." >&2
        echo "Commit, stash, or revert them before updating." >&2
        exit 1
    fi

    if [ "$USE_CONFIGURED_UPSTREAM" = "1" ]; then
        if [ "$UPSTREAM_REMOTE" = "." ]; then
            echo "==> Using local configured upstream '$UPSTREAM_DISPLAY' (no fetch)."
        else
            echo "==> Fetching configured upstream remote '$UPSTREAM_REMOTE'..."
            git -C "$VIBE_VIEW_REPO_ROOT" fetch --quiet "$UPSTREAM_REMOTE" --tags
        fi
        if ! git -C "$VIBE_VIEW_REPO_ROOT" rev-parse --verify \
            "${BRANCH}@{upstream}^{commit}" >/dev/null 2>&1; then
            echo "Error: configured upstream '$UPSTREAM_DISPLAY' was not available after fetch." >&2
            echo "Repair the branch tracking configuration, or use --skip-git." >&2
            exit 1
        fi
    else
        echo "==> Fetching origin..."
        git -C "$VIBE_VIEW_REPO_ROOT" fetch --quiet origin --tags
    fi
    if [ "$UPDATE_DESKTOP" = "1" ]; then
        DESKTOP_REVISION=""
        if [ "$USE_CONFIGURED_UPSTREAM" = "1" ]; then
            DESKTOP_REVISION="${BRANCH}@{upstream}"
        elif git -C "$VIBE_VIEW_REPO_ROOT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
            if git -C "$VIBE_VIEW_REPO_ROOT" show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
                DESKTOP_REVISION="origin/$BRANCH"
            else
                DESKTOP_REVISION="$BRANCH"
            fi
        elif git -C "$VIBE_VIEW_REPO_ROOT" show-ref --verify --quiet "refs/tags/$BRANCH"; then
            DESKTOP_REVISION="$BRANCH"
        elif git -C "$VIBE_VIEW_REPO_ROOT" show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
            DESKTOP_REVISION="origin/$BRANCH"
        elif git -C "$VIBE_VIEW_REPO_ROOT" cat-file -e "$BRANCH^{commit}" 2>/dev/null; then
            DESKTOP_REVISION="$BRANCH"
        fi
        if [ -n "$DESKTOP_REVISION" ]; then
            vibe_view_assert_safe_desktop_revision "$DESKTOP_REVISION"
        fi
        # Inspect the selected desktop revision before invoking even the old
        # environment's Python. Health still precedes checkout/merge mutation.
        acquire_update_locks
        prepare_venv_update
    fi
    if [ "$USE_CONFIGURED_UPSTREAM" = "1" ]; then
        echo "==> Updating '$BRANCH' by fast-forwarding from '$UPSTREAM_DISPLAY'..."
        git -C "$VIBE_VIEW_REPO_ROOT" merge --ff-only --quiet \
            "${BRANCH}@{upstream}"
    elif git -C "$VIBE_VIEW_REPO_ROOT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
        echo "==> Switching to '$BRANCH' and fast-forwarding..."
        git -C "$VIBE_VIEW_REPO_ROOT" checkout --quiet "$BRANCH"
        git -C "$VIBE_VIEW_REPO_ROOT" pull --ff-only --quiet origin "$BRANCH"
    elif git -C "$VIBE_VIEW_REPO_ROOT" show-ref --verify --quiet "refs/tags/$BRANCH"; then
        echo "==> Checking out tag '$BRANCH' (detached HEAD)..."
        git -C "$VIBE_VIEW_REPO_ROOT" checkout --quiet "$BRANCH"
    elif git -C "$VIBE_VIEW_REPO_ROOT" show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
        echo "==> Creating local branch '$BRANCH' tracking origin/$BRANCH..."
        git -C "$VIBE_VIEW_REPO_ROOT" checkout --quiet -b "$BRANCH" "origin/$BRANCH"
    elif git -C "$VIBE_VIEW_REPO_ROOT" cat-file -e "$BRANCH^{commit}" 2>/dev/null; then
        echo "==> Checking out commit '$BRANCH' (detached HEAD)..."
        git -C "$VIBE_VIEW_REPO_ROOT" checkout --quiet --detach "$BRANCH"
    else
        echo "Error: '$BRANCH' is not a known branch, tag, or commit." >&2
        exit 1
    fi
    echo "    now at $(git -C "$VIBE_VIEW_REPO_ROOT" rev-parse --short HEAD): $(git -C "$VIBE_VIEW_REPO_ROOT" log -1 --format='%s')"
fi

if [ "$UPDATE_DESKTOP" = "1" ]; then
    # Check the selected revision before executing its installer. Historical
    # refs contain a legacy implementation without ownership/package guards.
    vibe_view_assert_safe_desktop_installer
fi

if [ "$RECREATE_VENV" = "1" ]; then
    vibe_view_start_venv_replacement \
        "$VENV_PATH" "$PYTHON_BIN" "$ADOPT_LEGACY"
    echo "==> Creating replacement virtualenv: $VENV_PATH"
    "$PYTHON_BIN" -m venv "$VENV_PATH"
    vibe_view_install_environment "$VENV_PATH" "$EXTRAS_SPEC"
else
    vibe_view_install_environment "$VENV_PATH" "$EXTRAS_SPEC"
fi

if [ "$UPDATE_DESKTOP" = "1" ]; then
    # A refused foreign/packaged refresh must not redirect that app to this
    # checkout's Python environment.
    vibe_view_verify_environment "$VENV_PATH" "$EXTRAS_PROFILE" 0
    vibe_view_refresh_desktop "$VENV_PATH" "$ADOPT_DESKTOP"
    vibe_view_record_interpreter "$VENV_PATH"
elif [ "$WITH_ELECTRON" = "1" ]; then
    vibe_view_verify_environment "$VENV_PATH" "$EXTRAS_PROFILE" 0
    vibe_view_install_electron "$VENV_PATH"
else
    vibe_view_verify_environment "$VENV_PATH" "$EXTRAS_PROFILE" 0
fi
if [ "$UPDATE_DESKTOP" != "1" ] && \
   vibe_view_profile_has_viewer "$EXTRAS_PROFILE"; then
    vibe_view_record_interpreter "$VENV_PATH"
fi
if [ "$RECREATE_VENV" = "1" ]; then
    vibe_view_mark_standalone_venv "$VENV_PATH"
    vibe_view_commit_venv_replacement
    trap 'vibe_view_release_target_lock' EXIT
fi

echo
echo "==> vibe-view $OPERATION complete."
echo "    Version: $("$VENV_PATH/bin/vibe-view" --version | head -n 1)"
echo "    Activate: source \"$VENV_PATH/bin/activate\""
