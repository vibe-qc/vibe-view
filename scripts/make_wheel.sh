#!/usr/bin/env bash
# make_wheel.sh — build and stage validated vibe-view release artifacts.
#
# Produces a reproducible wheel, sdist, and SHA256SUMS in dist/.
# The validated wheel is then staged under docs/_static/downloads/ for the
# website download link, in the standalone viewer site. An
# existing wheel at the same version is immutable and will not be overwritten;
# release work must bump project.version before changing public bytes. Older
# versioned wheels remain available for installed desktop bootstrap/repair URLs.
#
# The wheel exists because the viewer is useful to people who cannot clone the
# repository: it is pure Python, so one file installs the whole program with
# no compiler and no vibe-qc. See
# docs/installation.md.
#
# Usage:  scripts/make_wheel.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VIEWER_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOWNLOADS="${VIEWER_DIR}/docs/_static/downloads"

PYTHON="${VIBE_VIEW_RELEASE_PYTHON:-python3}"
if ! command -v "${PYTHON}" >/dev/null 2>&1; then
  echo "error: release Python not found: ${PYTHON}" >&2
  exit 1
fi

# build_release_artifacts.py performs both builds in temporary directories and
# only replaces dist/ and the public wheel after reproducibility, content,
# RECORD, twine, and checksum validation all pass.  A failed build therefore
# cannot silently serve the previously committed wheel.
"${PYTHON}" "${SCRIPT_DIR}/build_release_artifacts.py" \
  --output-dir "${VIEWER_DIR}/dist" \
  --stage-wheel "${DOWNLOADS}"

shopt -s nullglob
WHEELS=("${VIEWER_DIR}"/dist/vibeview-*-py3-none-any.whl)
if [ "${#WHEELS[@]}" -ne 1 ]; then
  echo "error: validated build did not produce exactly one wheel" >&2
  exit 1
fi
WHEEL="${WHEELS[0]}"

SIZE="$(du -k "${DOWNLOADS}/$(basename "${WHEEL}")" | cut -f1)"
echo "Staged docs/_static/downloads/$(basename "${WHEEL}") (${SIZE} KB)"
echo
echo "If the version changed, update the download links that name the file:"
grep -rl 'vibeview-.*-py3-none-any\.whl' "${VIEWER_DIR}/docs" --include='*.md' 2>/dev/null \
  | sed 's|^|  |' || true
