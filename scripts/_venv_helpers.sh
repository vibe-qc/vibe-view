#!/usr/bin/env bash
# Shared virtual-environment helpers for vibe-view's shell entry points.
#
# This file is sourced by install.sh, uninstall.sh, update.sh, build.sh, and
# launch.sh so
# every command agrees on the canonical standalone environment:
#
#     <checkout>/.venv
#
# It was <checkout>/vibe-view/.venv while vibe-view was a directory of the
# vibe-qc monorepo; since the 2026-09 split the repository root is the project
# root. Compatibility fallbacks retain the older nested and vibe-qc-style
# names, but a new standalone install never shares vibe-qc's compiled
# environment.

VIBE_VIEW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
VIBE_VIEW_PROJECT_DIR="$(cd "$VIBE_VIEW_SCRIPT_DIR/.." && pwd -P)"
# vibe-view is its own repository: the project directory IS the repo root.
VIBE_VIEW_REPO_ROOT="$VIBE_VIEW_PROJECT_DIR"
VIBE_VIEW_TOOLSET_LOCK_HELPER="$VIBE_VIEW_SCRIPT_DIR/_lifecycle_lock.sh"
if [ -f "$VIBE_VIEW_TOOLSET_LOCK_HELPER" ]; then
    # shellcheck source=../../scripts/_lifecycle_lock.sh
    . "$VIBE_VIEW_TOOLSET_LOCK_HELPER"
fi

vibe_view_acquire_target_lock() {
    local target="$1"
    local action="${2:-lifecycle}"
    local requested_python="${3:-python3}"
    local lock_python=""

    command -v vibe_toolset_acquire_lifecycle_lock >/dev/null 2>&1 || {
        echo "Error: shared toolset lifecycle lock helper is missing." >&2
        return 1
    }
    vibe_toolset_select_external_python \
        lock_python "$requested_python" "$target" || return
    vibe_toolset_acquire_lifecycle_lock \
        "$lock_python" "$VIBE_VIEW_REPO_ROOT" "$target" "vibe-view $action"
}

vibe_view_release_target_lock() {
    command -v vibe_toolset_release_lifecycle_lock >/dev/null 2>&1 || return 0
    vibe_toolset_release_lifecycle_lock
}

vibe_view_acquire_checkout_lock() {
    local helper="$VIBE_VIEW_REPO_ROOT/scripts/_build_lock.sh"

    if [ -f "$helper" ]; then
        # shellcheck source=../../scripts/_build_lock.sh
        . "$helper"
        vibeqc_acquire_build_lock
    fi
}

vibe_view_path_from_project() {
    local raw="$1"
    case "$raw" in
        /*) printf '%s\n' "$raw" ;;
        *)  printf '%s/%s\n' "$VIBE_VIEW_PROJECT_DIR" "$raw" ;;
    esac
}

vibe_view_resolve_venv_path() {
    local out_var="$1"
    local raw="$2"
    local python_bin="$3"
    local resolved

    if ! resolved="$(PYTHONNOUSERSITE=1 "$python_bin" -I -S - \
        "$raw" "$VIBE_VIEW_PROJECT_DIR" <<'PY'
import os
import sys

raw, project = sys.argv[1:]
path = raw if os.path.isabs(raw) else os.path.join(project, raw)
print(os.path.realpath(path))
PY
    )"; then
        return 1
    fi
    [ -n "$resolved" ] || return 1
    printf -v "$out_var" '%s' "$resolved"
}

vibe_view_detect_venv() {
    local out_var="$1"
    local explicit="${2:-}"
    local required_command="${3:-}"
    local requested="${explicit:-${VIBE_VIEW_VENV:-}}"
    local found=""
    local candidate
    local candidates=()

    if [ -n "$requested" ]; then
        candidates+=("$(vibe_view_path_from_project "$requested")")
    else
        candidates+=(
            "$VIBE_VIEW_PROJECT_DIR/.venv"
            "$VIBE_VIEW_PROJECT_DIR/venv"
            "$VIBE_VIEW_PROJECT_DIR/.venv-vibeview"
            "$VIBE_VIEW_PROJECT_DIR/venv-vibeview"
        )
        if [ -n "${VIRTUAL_ENV:-}" ]; then
            candidates+=("$VIRTUAL_ENV")
        fi
        # Compatibility with the standalone tutorial and the older launch
        # script. These are fallbacks only; new installs use the first path.
        candidates+=(
            "$VIBE_VIEW_REPO_ROOT/.venv-viewer"
            "$VIBE_VIEW_REPO_ROOT/.venv"
            "$VIBE_VIEW_REPO_ROOT/venv"
            "$VIBE_VIEW_REPO_ROOT/.venv-vibeqc"
            "$VIBE_VIEW_REPO_ROOT/venv-vibeqc"
        )
    fi

    for candidate in ${candidates[@]+"${candidates[@]}"}; do
        if [ ! -x "$candidate/bin/python" ]; then
            continue
        fi
        if [ -n "$required_command" ] && [ ! -x "$candidate/bin/$required_command" ]; then
            continue
        fi
        found="$candidate"
        break
    done

    printf -v "$out_var" '%s' "$found"
}

vibe_view_detect_standalone_venv() {
    local out_var="$1"
    local explicit="${2:-}"
    local requested="${explicit:-${VIBE_VIEW_VENV:-}}"
    local found=""
    local candidate
    local candidates=()

    if [ -n "$requested" ]; then
        candidates+=("$(vibe_view_path_from_project "$requested")")
    else
        # Only environments whose names make them unambiguously viewer-owned
        # are safe for update.sh to mutate. In particular, do not auto-select
        # an activated environment or the repository-root .venv: either may
        # be the much heavier shared vibe-qc build. Those remain read-only
        # compatibility fallbacks for build.sh and launch.sh.
        candidates+=(
            "$VIBE_VIEW_PROJECT_DIR/.venv"
            "$VIBE_VIEW_PROJECT_DIR/venv"
            "$VIBE_VIEW_PROJECT_DIR/.venv-vibeview"
            "$VIBE_VIEW_PROJECT_DIR/venv-vibeview"
            "$VIBE_VIEW_REPO_ROOT/.venv-viewer"
        )
    fi

    for candidate in ${candidates[@]+"${candidates[@]}"}; do
        if [ -x "$candidate/bin/python" ]; then
            found="$candidate"
            break
        fi
    done

    printf -v "$out_var" '%s' "$found"
}

vibe_view_detect_removable_standalone_venv() {
    local out_var="$1"
    local explicit="${2:-}"
    local requested="${explicit:-${VIBE_VIEW_VENV:-}}"
    local found=""
    local candidate
    local candidates=()

    if [ -n "$requested" ]; then
        candidates+=("$(vibe_view_path_from_project "$requested")")
    else
        # Match update.sh's mutation boundary. Never infer an activated or
        # repository-root environment: either may be a shared vibe-qc build.
        candidates+=(
            "$VIBE_VIEW_PROJECT_DIR/.venv"
            "$VIBE_VIEW_PROJECT_DIR/venv"
            "$VIBE_VIEW_PROJECT_DIR/.venv-vibeview"
            "$VIBE_VIEW_PROJECT_DIR/venv-vibeview"
            "$VIBE_VIEW_REPO_ROOT/.venv-viewer"
        )
    fi

    for candidate in ${candidates[@]+"${candidates[@]}"}; do
        if [ -e "$candidate" ] || [ -L "$candidate" ]; then
            found="$candidate"
            break
        fi
    done

    printf -v "$out_var" '%s' "$found"
}

vibe_view_resolve_removal_path() {
    local out_var="$1"
    local raw="$2"
    local python_bin="$3"
    local candidate
    local resolved

    candidate="$(vibe_view_path_from_project "$raw")"
    if ! resolved="$(PYTHONNOUSERSITE=1 "$python_bin" -I -S - \
        "$candidate" <<'PY'
from __future__ import annotations

import os
import sys
from pathlib import Path

# abspath removes trailing slashes and `/.` without following the final path.
# Check that normalized path itself before realpath so those spellings cannot
# hide a symlinked deletion target.
lexical = Path(os.path.abspath(sys.argv[1]))
if lexical.is_symlink():
    raise SystemExit(2)
print(lexical.resolve())
PY
    )"; then
        echo "Error: refusing to remove a virtualenv through a symbolic link:" >&2
        echo "       $candidate" >&2
        return 1
    fi
    [ -n "$resolved" ] || return 1
    printf -v "$out_var" '%s' "$resolved"
}

vibe_view_assert_removable_venv() {
    local target="$1"

    vibe_view_assert_safe_venv_target "$target" || return
    if [ -L "$target" ]; then
        echo "Error: refusing to remove a virtualenv through a symbolic link:" >&2
        echo "       $target" >&2
        return 1
    fi
    if [ ! -e "$target" ]; then
        return 0
    fi
    if [ ! -d "$target" ] || [ ! -f "$target/pyvenv.cfg" ] || \
       [ -L "$target/pyvenv.cfg" ]; then
        echo "Error: refusing to remove '$target': it is not recognisably a virtualenv." >&2
        echo "Move or remove it manually after checking its contents." >&2
        return 1
    fi
}

vibe_view_mark_standalone_venv() {
    local venv="$1"

    PYTHONNOUSERSITE=1 "$venv/bin/python" -I -S - \
        "$venv" "$VIBE_VIEW_PROJECT_DIR" <<'PY'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

venv = Path(sys.argv[1])
project = Path(sys.argv[2]).resolve()
marker = venv / ".vibe-view-standalone.json"
temporary = venv / f".vibe-view-standalone.{os.getpid()}.tmp"
temporary.write_text(
    json.dumps(
        {
            "schema": 1,
            "kind": "vibe-view-standalone-venv",
            "project": str(project),
        },
        indent=2,
    )
    + "\n"
)
os.replace(temporary, marker)
PY
}

vibe_view_assert_owned_standalone_venv() {
    local venv="$1"
    local python_bin="$2"
    local allow_legacy_pep610="${3:-0}"

    vibe_view_assert_removable_venv "$venv" || return
    [ -e "$venv" ] || return 0
    if ! PYTHONNOUSERSITE=1 "$python_bin" -I -S - \
        "$venv" "$VIBE_VIEW_PROJECT_DIR" \
        "$allow_legacy_pep610" <<'PY'
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

venv = Path(sys.argv[1]).resolve()
project = Path(sys.argv[2]).resolve()
allow_legacy_pep610 = sys.argv[3] == "1"
marker = venv / ".vibe-view-standalone.json"

if marker.is_symlink():
    raise SystemExit(1)
if marker.exists():
    if not marker.is_file():
        raise SystemExit(1)
    try:
        payload = json.loads(marker.read_text())
        raw_project = Path(payload["project"])
        if not raw_project.is_absolute():
            raise ValueError("marker project must be absolute")
        marker_project = raw_project.resolve()
    except (OSError, UnicodeError, KeyError, TypeError, ValueError):
        raise SystemExit(1)
    if (
        payload.get("schema") == 1
        and payload.get("kind") == "vibe-view-standalone-venv"
        and marker_project == project
    ):
        raise SystemExit(0)
    raise SystemExit(1)

if not allow_legacy_pep610:
    raise SystemExit(1)

# Backward compatibility for standalone installs created before the ownership
# marker existed. A regular/editable local pip install records its source in
# PEP 610 direct_url.json; require that exact source checkout before deletion.
patterns = (
    "lib/python*/site-packages/vibeview-*.dist-info/direct_url.json",
    "Lib/site-packages/vibeview-*.dist-info/direct_url.json",
)
records = []
for pattern in patterns:
    records.extend(venv.glob(pattern))
for record in records:
    if record.is_symlink() or not record.is_file():
        continue
    try:
        payload = json.loads(record.read_text())
        url = payload.get("url")
        if not isinstance(url, str) or not url.startswith("file:"):
            continue
        source = Path(urllib.request.url2pathname(urllib.parse.urlparse(url).path))
        if source.resolve() == project:
            raise SystemExit(0)
    except (OSError, UnicodeError, TypeError, ValueError):
        continue
raise SystemExit(1)
PY
    then
        echo "Error: refusing to remove '$venv': ownership is not proven." >&2
        echo "It is neither marked for this checkout nor linked to it by PEP 610 metadata." >&2
        echo "Remove an unrelated environment manually after checking its packages." >&2
        return 1
    fi
}

vibe_view_clear_matching_interpreter_record() {
    local python_bin="$1"
    local venv="$2"
    local dry_run="${3:-0}"

    PYTHONNOUSERSITE=1 "$python_bin" -I -S - \
        "$venv" "$dry_run" <<'PY'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

venv = Path(sys.argv[1]).resolve()
dry_run = sys.argv[2] == "1"
cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
record = cache / "vibe-view" / "interpreter.json"
if not record.exists():
    raise SystemExit(0)
try:
    payload = json.loads(record.read_text())
    # Keep the lexical venv path: bin/python commonly symlinks to the base
    # interpreter, and resolving that symlink would incorrectly place it
    # outside the environment we are uninstalling.
    interpreter = Path(os.path.abspath(os.path.expanduser(payload["python"])))
    interpreter.relative_to(venv)
except (OSError, UnicodeError, KeyError, TypeError, ValueError):
    raise SystemExit(0)
if dry_run:
    print(f"Would remove stale desktop interpreter record -> {record}")
else:
    record.unlink(missing_ok=True)
    print(f"Removed stale desktop interpreter record -> {record}")
PY
}

vibe_view_command_path() {
    local out_var="$1"
    local command_name="$2"
    local resolved_command=""
    local link_value=""
    local link_dir=""
    local hops=0

    resolved_command="$(command -v "$command_name" 2>/dev/null || true)"
    case "$resolved_command" in
        /*) ;;
        *)
            echo "Error: Python interpreter '$command_name' was not found as an executable path." >&2
            return 1
            ;;
    esac
    [ -x "$resolved_command" ] || {
        echo "Error: Python interpreter '$resolved_command' is not executable." >&2
        return 1
    }

    # Resolve ordinary command symlinks without executing the selected
    # interpreter. This lets destructive commands reject an interpreter that
    # secretly points into the environment they are about to remove.
    while [ -L "$resolved_command" ]; do
        [ "$hops" -lt 40 ] || {
            echo "Error: too many symbolic links while resolving '$command_name'." >&2
            return 1
        }
        link_value="$(readlink "$resolved_command")"
        link_dir="$(cd "$(dirname "$resolved_command")" && pwd -P)"
        case "$link_value" in
            /*) resolved_command="$link_value" ;;
            *)  resolved_command="$link_dir/$link_value" ;;
        esac
        resolved_command="$(cd "$(dirname "$resolved_command")" && pwd -P)/$(basename "$resolved_command")"
        hops=$((hops + 1))
    done
    resolved_command="$(cd "$(dirname "$resolved_command")" && pwd -P)/$(basename "$resolved_command")"
    printf -v "$out_var" '%s' "$resolved_command"
}

vibe_view_select_trusted_python() {
    local out_var="$1"
    local requested="$2"
    local protected_target="$3"
    local selected_path=""
    local lexical_target="$protected_target"

    vibe_view_command_path selected_path "$requested" || return
    while [ "$lexical_target" != "/" ]; do
        case "$lexical_target" in
            */.) lexical_target="${lexical_target%/.}" ;;
            */)  lexical_target="${lexical_target%/}" ;;
            *)   break ;;
        esac
    done
    if [ -L "$lexical_target" ]; then
        echo "Error: refusing to replace/remove a virtualenv through a symbolic link:" >&2
        echo "       $lexical_target" >&2
        return 1
    fi
    if [ -d "$lexical_target" ]; then
        lexical_target="$(cd "$lexical_target" && pwd -P)"
    fi
    case "$selected_path" in
        "$lexical_target"|"$lexical_target"/*)
            echo "Error: refusing to use Python from the virtualenv selected for replacement/removal:" >&2
            echo "       $selected_path" >&2
            echo "Deactivate it or pass --python with an external Python 3.11+ interpreter." >&2
            return 1
            ;;
    esac
    vibe_view_assert_python "$selected_path" || return
    printf -v "$out_var" '%s' "$selected_path"
}

vibe_view_assert_python() {
    local python_bin="$1"

    if ! command -v "$python_bin" >/dev/null 2>&1; then
        echo "Error: Python interpreter '$python_bin' was not found." >&2
        return 1
    fi
    if ! PYTHONNOUSERSITE=1 "$python_bin" -I -S -c \
        'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
        echo "Error: vibe-view requires Python 3.11 or newer." >&2
        echo "Found: $(PYTHONNOUSERSITE=1 "$python_bin" -I -S --version 2>&1)" >&2
        return 1
    fi
}

vibe_view_refuse_privileged_lifecycle_for_uid() {
    local lifecycle_euid="$1"

    # A root-created checkout environment cannot later be maintained by the
    # regular user, and Electron deliberately refuses root desktop sessions.
    # Keep every mutating lifecycle entry point on the same ownership model.
    # GitLab's disposable runner containers execute as root, so let that exact
    # CI environment exercise lifecycle behavior. sudo is refused everywhere.
    if [ -n "${SUDO_USER:-}" ] || \
       { [ "$lifecycle_euid" -eq 0 ] && \
         { [ "${CI:-}" != "true" ] || [ "${GITLAB_CI:-}" != "true" ]; }; }; then
        echo "Error: run vibe-view lifecycle commands as your regular user, without sudo." >&2
        return 1
    fi
}

vibe_view_refuse_privileged_lifecycle() {
    vibe_view_refuse_privileged_lifecycle_for_uid "$EUID"
}

vibe_view_assert_safe_venv_target() {
    local target="$1"
    local home_real=""
    local home_uid=""
    local safe_user_location=0

    if [ -n "${HOME:-}" ]; then
        home_real="$(cd "$HOME" 2>/dev/null && pwd -P || true)"
        if [ -n "$home_real" ]; then
            home_uid="$(stat -c '%u' "$home_real" 2>/dev/null || \
                stat -f '%u' "$home_real" 2>/dev/null || true)"
        fi
    fi

    case "$target" in
        ""|/|/Applications|/Library|/System|/Users|/Volumes|/bin|/boot|/dev|/etc|/home|/nix|/opt|/private|/private/tmp|/proc|/root|/run|/sbin|/snap|/srv|/sys|/tmp|/usr|/var|"$VIBE_VIEW_SCRIPT_DIR"|"$VIBE_VIEW_PROJECT_DIR"|"$VIBE_VIEW_REPO_ROOT")
            echo "Error: refusing unsafe virtualenv target '$target'." >&2
            return 1
            ;;
    esac
    # Only an existing, canonical account home owned by this uid can exempt
    # its descendants. HOME, TMPDIR, and XDG variables are caller-controlled.
    if [ "$home_uid" = "$EUID" ]; then
        case "$home_real" in
            /Users/*|/home/*|/var/home/*|/root)
                case "$target" in
                    "$home_real"/*) safe_user_location=1 ;;
                esac
                ;;
        esac
    fi
    case "$target" in
        /tmp/*|/private/tmp/*|/var/tmp/*|/private/var/tmp/*|/var/folders/*|/private/var/folders/*|/run/user/"$EUID"/*)
            safe_user_location=1
            ;;
    esac
    case "$target" in
        .git|.git/*|*/.git|*/.git/*)
            echo "Error: refusing a virtualenv target inside Git metadata." >&2
            return 1
            ;;
    esac
    if [ "$safe_user_location" != "1" ]; then
        case "$target" in
            /Applications/*|/Library/*|/System/*|/Users/*|/bin/*|/boot/*|/dev/*|/etc/*|/home/*|/nix/*|/opt/*|/private/*|/proc/*|/root/*|/run/*|/sbin/*|/snap/*|/srv/*|/sys/*|/usr/*|/var/*)
                echo "Error: refusing unsafe virtualenv target inside a protected system location: '$target'." >&2
                return 1
                ;;
        esac
    fi
    if [ -n "$home_real" ] && [ "$target" = "$home_real" ]; then
        echo "Error: refusing to use the home directory as a virtualenv target." >&2
        return 1
    fi
    case "$VIBE_VIEW_PROJECT_DIR/" in
        "$target"/*)
            echo "Error: refusing a virtualenv target that contains the vibe-view checkout." >&2
            return 1
            ;;
    esac
    if [ -n "$home_real" ]; then
        case "$home_real/" in
            "$target"/*)
                echo "Error: refusing a virtualenv target that contains the home directory." >&2
                return 1
                ;;
        esac
    fi
    if [ -e "$target" ] && [ ! -d "$target" ]; then
        echo "Error: virtualenv target exists but is not a directory: $target" >&2
        return 1
    fi
}

vibe_view_remove_venv() {
    local target="$1"

    vibe_view_assert_removable_venv "$target" || return
    if [ ! -e "$target" ]; then
        return 0
    fi
    echo "==> Removing existing virtualenv: $target"
    rm -rf "$target"
}

# State for one failure-atomic environment replacement. A replacement is built
# at its final path because Python virtualenvs contain absolute paths and are
# not safely relocatable. The old environment waits in a same-parent backup
# until creation, installation, verification, and optional desktop work pass.
VIBE_VIEW_VENV_TX_ACTIVE=0
VIBE_VIEW_VENV_TX_TARGET=""
VIBE_VIEW_VENV_TX_BACKUP=""
VIBE_VIEW_VENV_TX_TOKEN=""
VIBE_VIEW_VENV_TX_HAD_ORIGINAL=0
VIBE_VIEW_VENV_TX_MUTATION_STARTED=0

vibe_view_begin_venv_replacement() {
    local target="$1"
    local expected_had_original="${2:-}"
    local actual_had_original=0
    local backup=""

    if [ "$VIBE_VIEW_VENV_TX_ACTIVE" = "1" ]; then
        echo "Error: a virtualenv replacement transaction is already active." >&2
        return 1
    fi
    case "$expected_had_original" in
        0|1) ;;
        *)
            echo "Error: virtualenv replacement requires an expected target state." >&2
            return 1
            ;;
    esac
    vibe_view_assert_safe_venv_target "$target" || return
    if [ -L "$target" ] || \
       { [ -e "$target" ] && \
         { [ ! -f "$target/pyvenv.cfg" ] || [ -L "$target/pyvenv.cfg" ]; }; }; then
        echo "Error: refusing to replace '$target': it is not recognisably a virtualenv." >&2
        echo "Move or remove it manually after checking its contents." >&2
        return 1
    fi
    if [ -e "$target" ] || [ -L "$target" ]; then
        actual_had_original=1
    fi
    if [ "$actual_had_original" != "$expected_had_original" ]; then
        echo "Error: virtualenv target changed before replacement could begin:" >&2
        echo "       $target" >&2
        return 1
    fi

    mkdir -p "$(dirname "$target")"
    if [ "$actual_had_original" = "1" ]; then
        if ! backup="$(mktemp -d "${target}.previous.XXXXXX")"; then
            echo "Error: could not reserve a rollback path beside '$target'." >&2
            return 1
        fi
        rmdir "$backup"
    fi

    VIBE_VIEW_VENV_TX_ACTIVE=1
    VIBE_VIEW_VENV_TX_TARGET="$target"
    VIBE_VIEW_VENV_TX_BACKUP="$backup"
    VIBE_VIEW_VENV_TX_TOKEN="${BASHPID:-$$}.${RANDOM}.${RANDOM}"
    VIBE_VIEW_VENV_TX_HAD_ORIGINAL="$actual_had_original"
    VIBE_VIEW_VENV_TX_MUTATION_STARTED=0
}

vibe_view_start_venv_replacement() {
    local target="$1"
    local trusted_python="${2:-}"
    local allow_legacy_pep610="${3:-0}"
    local ownership_mode="${4:-standalone}"
    local actual_had_original=0
    local marker

    if [ "$VIBE_VIEW_VENV_TX_ACTIVE" != "1" ] || \
       [ "$target" != "$VIBE_VIEW_VENV_TX_TARGET" ]; then
        echo "Error: virtualenv replacement does not match the active transaction." >&2
        return 1
    fi
    if [ "$VIBE_VIEW_VENV_TX_MUTATION_STARTED" = "1" ]; then
        echo "Error: virtualenv replacement has already started." >&2
        return 1
    fi
    if [ -e "$target" ] || [ -L "$target" ]; then
        actual_had_original=1
    fi
    if [ "$actual_had_original" != "$VIBE_VIEW_VENV_TX_HAD_ORIGINAL" ]; then
        echo "Error: virtualenv target changed before replacement mutation:" >&2
        echo "       $target" >&2
        return 1
    fi
    if [ "$actual_had_original" = "1" ]; then
        [ -n "$trusted_python" ] || {
            echo "Error: virtualenv replacement requires a trusted ownership inspector." >&2
            return 1
        }
        case "$ownership_mode" in
            standalone)
                vibe_view_assert_owned_standalone_venv \
                    "$target" "$trusted_python" "$allow_legacy_pep610" || return
                ;;
            capture)
                command -v capture_assert_replaceable >/dev/null 2>&1 || {
                    echo "Error: capture ownership verifier is unavailable." >&2
                    return 1
                }
                capture_assert_replaceable "$target" "$trusted_python" || return
                ;;
            *)
                echo "Error: unknown virtualenv replacement ownership mode '$ownership_mode'." >&2
                return 1
                ;;
        esac
    fi

    VIBE_VIEW_VENV_TX_MUTATION_STARTED=1
    if [ "$VIBE_VIEW_VENV_TX_HAD_ORIGINAL" = "1" ]; then
        mv "$target" "$VIBE_VIEW_VENV_TX_BACKUP"
    fi
    mkdir "$target"
    marker="$target/.vibe-view-venv-transaction"
    printf '%s\n' "$VIBE_VIEW_VENV_TX_TOKEN" > "$marker"
}

vibe_view_abort_venv_replacement() {
    local marker
    local marker_token=""
    local original_still_in_place=0
    local restore_failed=0

    [ "$VIBE_VIEW_VENV_TX_ACTIVE" = "1" ] || return 0
    if [ "$VIBE_VIEW_VENV_TX_MUTATION_STARTED" = "1" ]; then
        echo "==> Virtualenv replacement failed; restoring the previous state..." >&2
        marker="$VIBE_VIEW_VENV_TX_TARGET/.vibe-view-venv-transaction"
        if [ -f "$marker" ]; then
            marker_token="$(sed -n '1p' "$marker" 2>/dev/null || true)"
        fi
        if [ "$VIBE_VIEW_VENV_TX_HAD_ORIGINAL" = "1" ] && \
           [ -e "$VIBE_VIEW_VENV_TX_TARGET" ] && \
           [ ! -e "$marker" ] && \
           [ ! -e "$VIBE_VIEW_VENV_TX_BACKUP" ]; then
            # The EXIT trap can run after the transaction is armed but before
            # the same-filesystem mv begins. In that state the unmarked target
            # is still the intact original, so rollback is already complete.
            original_still_in_place=1
        fi
        if [ -e "$VIBE_VIEW_VENV_TX_TARGET" ]; then
            if [ "$original_still_in_place" = "1" ]; then
                :
            elif [ "$marker_token" = "$VIBE_VIEW_VENV_TX_TOKEN" ]; then
                if vibe_view_assert_safe_venv_target "$VIBE_VIEW_VENV_TX_TARGET"; then
                    rm -rf "$VIBE_VIEW_VENV_TX_TARGET"
                else
                    restore_failed=1
                fi
            elif ! rmdir "$VIBE_VIEW_VENV_TX_TARGET" 2>/dev/null; then
                echo "Error: refusing to remove an unowned replacement target:" >&2
                echo "       $VIBE_VIEW_VENV_TX_TARGET" >&2
                restore_failed=1
            fi
        fi
        if [ -n "$VIBE_VIEW_VENV_TX_BACKUP" ] && \
           [ -e "$VIBE_VIEW_VENV_TX_BACKUP" ]; then
            if [ "$restore_failed" = "0" ] && \
               ! mv "$VIBE_VIEW_VENV_TX_BACKUP" "$VIBE_VIEW_VENV_TX_TARGET"; then
                restore_failed=1
            fi
        fi
    fi

    VIBE_VIEW_VENV_TX_ACTIVE=0
    if [ "$restore_failed" = "1" ]; then
        echo "Error: automatic virtualenv rollback was incomplete." >&2
        if [ -n "$VIBE_VIEW_VENV_TX_BACKUP" ]; then
            echo "The previous environment remains at: $VIBE_VIEW_VENV_TX_BACKUP" >&2
        fi
    fi
    return 0
}

vibe_view_commit_venv_replacement() {
    local backup="$VIBE_VIEW_VENV_TX_BACKUP"
    local marker="$VIBE_VIEW_VENV_TX_TARGET/.vibe-view-venv-transaction"

    if [ "$VIBE_VIEW_VENV_TX_ACTIVE" != "1" ] || \
       [ "$VIBE_VIEW_VENV_TX_MUTATION_STARTED" != "1" ]; then
        echo "Error: cannot commit an incomplete virtualenv replacement." >&2
        return 1
    fi
    vibe_view_check_venv_health "$VIBE_VIEW_VENV_TX_TARGET"

    # Disable rollback only after the final-path environment is healthy. Any
    # later backup cleanup failure leaves the verified new venv in service.
    VIBE_VIEW_VENV_TX_ACTIVE=0
    if ! rm -f "$marker"; then
        echo "Warning: could not remove transaction marker '$marker'." >&2
    fi
    if [ -n "$backup" ] && ! vibe_view_remove_venv "$backup"; then
        echo "Warning: the old virtualenv remains at '$backup'." >&2
        echo "Remove it manually after confirming the update." >&2
    fi
    VIBE_VIEW_VENV_TX_BACKUP=""
}

vibe_view_check_venv_health() {
    local venv="$1"

    if [ ! -f "$venv/pyvenv.cfg" ] || [ ! -x "$venv/bin/python" ]; then
        echo "Error: '$venv' is not a usable virtualenv." >&2
        return 1
    fi
    if ! "$venv/bin/python" -c \
        'import sys; raise SystemExit(not (sys.prefix != sys.base_prefix and sys.version_info >= (3, 11)))' \
        >/dev/null 2>&1; then
        echo "Error: '$venv/bin/python' is broken or older than Python 3.11." >&2
        return 1
    fi
    if ! "$venv/bin/python" -m pip --version >/dev/null 2>&1; then
        echo "Error: pip is not usable in '$venv'." >&2
        return 1
    fi
    if [ -x "$venv/bin/pip" ] && ! "$venv/bin/pip" --version >/dev/null 2>&1; then
        echo "Error: '$venv/bin/pip' has a stale interpreter path." >&2
        return 1
    fi
}

vibe_view_extras_to_spec() {
    local profile="$1"
    local out_var="$2"
    local spec

    case "$profile" in
        modes)           spec="[viewer,tui]" ;;
        core|none)       spec="" ;;
        viewer|browser|desktop) spec="[viewer]" ;;
        tui)             spec="[tui]" ;;
        all)             spec="[all]" ;;
        test)            spec="[test]" ;;
        *)
            echo "Error: --extras must be one of: modes / core / viewer / tui / all / test (got '$profile')." >&2
            return 1
            ;;
    esac
    printf -v "$out_var" '%s' "$spec"
}

vibe_view_profile_has_viewer() {
    case "$1" in
        modes|viewer|browser|desktop|all|test) return 0 ;;
        *) return 1 ;;
    esac
}

vibe_view_profile_has_tui() {
    case "$1" in
        modes|tui|all|test) return 0 ;;
        *) return 1 ;;
    esac
}

vibe_view_install_environment() {
    local venv="$1"
    local extras_spec="$2"

    echo "==> Updating pip and wheel-build tools..."
    "$venv/bin/python" -m pip install --quiet --upgrade pip setuptools wheel build

    echo "==> Installing vibe-view${extras_spec} from this checkout..."
    "$venv/bin/python" -m pip install --upgrade \
        "${VIBE_VIEW_PROJECT_DIR}${extras_spec}"

    vibe_view_install_naming_path "$venv"
}

vibe_view_install_naming_path() {
    local venv="$1"
    local python_path=""
    local site_dir pth candidate_root

    # Where vibe-qc's python/ tree may be found. Before the 2026-09 split
    # vibe-view sat beside it in one checkout and REPO_ROOT/python was it;
    # now vibe-qc is its own repository, so a sibling checkout is the normal
    # developer layout. Both are probed, sibling first.
    for candidate_root in \
        "$VIBE_VIEW_PROJECT_DIR/../vibe-qc" \
        "$VIBE_VIEW_REPO_ROOT"; do
        if [ -d "$candidate_root/python/vibeqc_naming" ]; then
            python_path="$(cd "$candidate_root/python" && pwd -P)"
            break
        fi
    done

    # vibe-view's standalone wheel does not ship vibeqc_naming (pure Python,
    # lives beside vibe-qc in the same checkout). A .pth entry pointing at the
    # checkout's python/ directory is exactly the "repo checkout with python/
    # on the path" condition the viewer's _naming_available() already expects,
    # so IUPAC name detection works in the source-installed app without
    # building or installing vibe-qc. The file lives inside the venv, so
    # uninstall/reinstall remove and recreate it with the environment.
    # Nothing to wire: vibe-qc is not checked out alongside. Installing it
    # (pip install vibe-qc) is the supported way to get naming in that case.
    [ -n "$python_path" ] || return 0
    site_dir=""
    for candidate in "$venv"/lib/python*/site-packages; do
        [ -d "$candidate" ] && site_dir="$candidate" && break
    done
    if [ -z "$site_dir" ]; then
        echo "Warning: could not locate site-packages for the viewer" >&2
        echo "environment; IUPAC name detection will be unavailable." >&2
        return 0
    fi
    pth="$site_dir/vibe-view-repo-python.pth"
    if [ ! -f "$pth" ] || ! grep -Fqx "$python_path" "$pth"; then
        printf '%s\n' "$python_path" > "$pth"
        echo "Naming path: $python_path (IUPAC name detection)"
    fi
}

vibe_view_verify_environment() {
    local venv="$1"
    local profile="$2"
    local record_interpreter="${3:-1}"
    local imports="import vibeview, pyvista, build.__main__"

    if vibe_view_profile_has_viewer "$profile"; then
        imports="$imports, trame, trame_vtk, trame_vuetify, uvicorn"
    fi
    if vibe_view_profile_has_tui "$profile"; then
        imports="$imports, textual"
    fi

    echo "==> Verifying the installed modes..."
    "$venv/bin/python" -c "$imports"
    "$venv/bin/vibe-view" --version
    if vibe_view_profile_has_viewer "$profile"; then
        "$venv/bin/vibe-view" open --help >/dev/null
        "$venv/bin/vibe-view" desktop --help >/dev/null
    fi
    if vibe_view_profile_has_tui "$profile"; then
        "$venv/bin/vibe-view" tui --help >/dev/null
    fi

    if vibe_view_profile_has_viewer "$profile" && \
       [ "$record_interpreter" = "1" ]; then
        # A directly launched Electron app has no activated shell. Record only
        # viewer-capable interpreters; a core/TUI update must not replace a
        # working desktop interpreter with one that lacks the server stack.
        vibe_view_record_interpreter "$venv"
    fi
}

vibe_view_record_interpreter() {
    local venv="$1"

    "$venv/bin/python" -c \
        'from vibeview.cli import _record_interpreter; _record_interpreter()'
}

vibe_view_install_electron() {
    local venv="$1"
    shift
    local installer="$VIBE_VIEW_PROJECT_DIR/electron/install-electron.py"

    if [ ! -f "$installer" ]; then
        echo "Error: Electron installer not found: $installer" >&2
        return 1
    fi
    echo "==> Synchronizing the reviewed Electron runtime and desktop wrappers..."
    if [ $# -gt 0 ]; then
        "$venv/bin/python" "$installer" "$@"
    else
        "$venv/bin/python" "$installer"
    fi
}

vibe_view_refresh_desktop() {
    local venv="$1"
    local adopt_app="${2:-0}"
    local installer="$VIBE_VIEW_PROJECT_DIR/electron/install-electron.py"
    local args=(--refresh-app)

    if [ ! -f "$installer" ]; then
        echo "Error: Electron installer not found: $installer" >&2
        return 1
    fi
    echo "==> Refreshing the source-backed vibe-view desktop app..."
    [ "$adopt_app" = "1" ] && args+=(--adopt-app)
    "$venv/bin/python" "$installer" ${args[@]+"${args[@]}"}
}

VIBE_VIEW_BIN_LINK_MARKER="# Generated by vibe-view install.sh --link-bin"
# Paths and operator records belong outside the checkout, even when ignored.
vibe_view_bin_link_record() {
    local dry_run="${1:-0}"
    local args=()
    [ "$dry_run" = "1" ] && args+=(--dry-run)
    python3 -I -S "$VIBE_VIEW_SCRIPT_DIR/_private_state.py" \
        "$VIBE_VIEW_PROJECT_DIR" ${args[@]+"${args[@]}"}
}

vibe_view_link_bin() {
    local venv="$1"
    local dir="$2"
    local link="$dir/vibe-view"
    local record=""
    record="$(vibe_view_bin_link_record 0)" || return

    if [ ! -d "$dir" ]; then
        echo "Error: --link-bin target '$dir' is not a directory." >&2
        return 1
    fi
    if [ ! -w "$dir" ]; then
        echo "Error: --link-bin target '$dir' is not writable." >&2
        return 1
    fi
    if [ ! -x "$venv/bin/vibe-view" ]; then
        echo "Error: no usable vibe-view command in '$venv'." >&2
        return 1
    fi
    case ":$PATH:" in
        *":$dir:"*) ;;
        *)
            echo "Warning: '$dir' is not on your PATH; 'vibe-view' will not" >&2
            echo "be found by new shells until you add it." >&2
            ;;
    esac
    if [ -d "$link" ]; then
        echo "Error: '$link' is a directory; remove it or choose another --link-bin target." >&2
        return 1
    fi
    if [ -L "$link" ]; then
        echo "Error: '$link' is a symbolic link and was not created by vibe-view." >&2
        echo "Remove it or choose another directory with --link-bin." >&2
        return 1
    fi
    if [ -e "$link" ] && ! grep -Fq "$VIBE_VIEW_BIN_LINK_MARKER" "$link"; then
        echo "Error: '$link' exists and was not created by vibe-view." >&2
        echo "Remove it or choose another directory with --link-bin." >&2
        return 1
    fi
    cat > "$link" <<EOF
#!/bin/sh
$VIBE_VIEW_BIN_LINK_MARKER
# Launches the standalone vibe-view environment below. Re-run
# install.sh --link-bin if the checkout or the venv moves.
exec "$venv/bin/vibe-view" "\$@"
EOF
    chmod +x "$link"
    echo "Command link: $link -> $venv/bin/vibe-view"
    local temporary=""
    temporary="$(mktemp "${record}.XXXXXX")" || return
    if { grep -Fxv "$dir" "$record" 2>/dev/null || true; echo "$dir"; } \
            | sort -u > "$temporary"; then
        mv "$temporary" "$record"
    else
        rm -f "$temporary"
        return 1
    fi
}

vibe_view_unlink_bin() {
    local venv="$1"
    local dry_run="${2:-0}"
    local record=""
    record="$(vibe_view_bin_link_record "$dry_run")" || return
    local dir link remaining=""

    [ -f "$record" ] || return 0
    while IFS= read -r dir; do
        [ -n "$dir" ] || continue
        link="$dir/vibe-view"
        if [ -f "$link" ] && \
           grep -Fq "$VIBE_VIEW_BIN_LINK_MARKER" "$link" && \
           grep -Fq "exec \"$venv/bin/vibe-view\"" "$link"; then
            if [ "$dry_run" = "1" ]; then
                echo "Would remove command link -> $link"
            else
                rm -f "$link"
                echo "Removed command link -> $link"
            fi
        else
            remaining="${remaining}${dir}
"
        fi
    done < "$record"
    if [ "$dry_run" = "1" ]; then
        return 0
    fi
    if [ -n "$remaining" ]; then
        printf '%s' "$remaining" > "$record"
    else
        rm -f "$record"
    fi
}

vibe_view_assert_safe_desktop_installer() {
    local installer="$VIBE_VIEW_PROJECT_DIR/electron/install-electron.py"

    if [ ! -f "$installer" ] || \
       ! grep -Fq 'SOURCE_DESKTOP_UPDATE_PROTOCOL = 1' "$installer"; then
        echo "Error: this Git revision does not support safe desktop updates." >&2
        echo "Choose a revision that contains the desktop update protocol," >&2
        echo "or update only its Python environment with scripts/update.sh." >&2
        return 1
    fi
}

vibe_view_assert_safe_desktop_revision() {
    local revision="$1"
    local installer_source

    if ! installer_source="$(
        git -C "$VIBE_VIEW_REPO_ROOT" show \
            "$revision:vibe-view/electron/install-electron.py" 2>/dev/null
    )" || \
       [[ "$installer_source" != *"SOURCE_DESKTOP_UPDATE_PROTOCOL = 1"* ]]; then
        echo "Error: Git revision '$revision' does not support safe desktop updates." >&2
        echo "The checkout was not switched. Choose a compatible revision," >&2
        echo "or update only Python with scripts/update.sh." >&2
        return 1
    fi
}
