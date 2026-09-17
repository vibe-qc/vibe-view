#!/bin/bash
# Build the vibe-view documentation site into ./public/.
#
# Used by .gitlab-ci.yml's docs-build and docs-deploy jobs, and by anyone who
# wants to reproduce the live site locally. Deliberately the same shape as
# vibe-qc's scripts/build_site.sh (project 34), so the two publishers behave
# identically where they can.
#
# Requires the `[docs]` extra on PATH: `pip install -e '.[docs]'` from the
# repository root. That is what .gitlab-ci.yml's .docs_deps installs, so a
# local run of this script reproduces the published build.
#
# Reads:
#
#   docs/                          -- Sphinx source
#
# Writes:
#
#   public/                        -- built HTML, ready to rsync
#   public/robots.txt              -- search-engine directive
#   public/sitemap.xml             -- every content URL with lastmod
#   public/.build-info             -- commit SHA / branch / pipeline ID
#
# Environment:
#
#   CANONICAL                      -- base URL for robots + sitemap. MUST match
#                                     docs/conf.py's html_baseurl and the
#                                     docs-deploy rsync target. Default is the
#                                     published location.
#   OUT                            -- output directory (default public)
#   CI_COMMIT_REF_NAME             -- branch name; set by GitLab CI, falls back
#                                     to `git rev-parse` for local builds
#   CI_PIPELINE_ID                 -- recorded in .build-info when set
set -euo pipefail

# Override the public canonical URL for a separately configured documentation site.
# This script only builds local files; deployment belongs to external tooling.
CANONICAL="${CANONICAL:-https://vibe-qc.com/vibe-view/docs}"
OUT="${OUT:-public}"

rm -rf "$OUT"

# --keep-going without -W: warnings do not fail the publish. `make strict` in
# docs/ is the developer-facing build that turns them into errors, which is
# where a broken cross-reference should be caught.
sphinx-build -b html --keep-going -q docs/ "$OUT"

# robots.txt
cat > "$OUT/robots.txt" <<EOF
User-agent: *
Allow: /

Sitemap: $CANONICAL/sitemap.xml
EOF

# sitemap.xml -- every content .html, excluding indexes / sources / errors
LASTMOD=$(date -u +%Y-%m-%d)
{
    printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>'
    printf '%s\n' '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ( cd "$OUT" && find . -type f -name '*.html' | sed 's|^\./||' | sort ) \
        | while IFS= read -r path; do
            case "$path" in
                _modules/*|_sources/*|error/*|genindex.html|py-modindex.html|search.html)
                    continue
                    ;;
            esac
            printf '  <url><loc>%s/%s</loc><lastmod>%s</lastmod></url>\n' \
                "$CANONICAL" "$path" "$LASTMOD"
        done
    printf '%s\n' '</urlset>'
} > "$OUT/sitemap.xml"

# .build-info -- what produced this site
{
    git log -1 --format='%H %ci %s'
    branch="${CI_COMMIT_REF_NAME:-$(git rev-parse --abbrev-ref HEAD)}"
    printf 'branch %s\n' "$branch"
    [ -n "${CI_PIPELINE_ID:-}" ] && printf 'pipeline %s\n' "$CI_PIPELINE_ID"
} > "$OUT/.build-info"

# Strip Sphinx internals we don't serve
rm -rf "$OUT/.doctrees" "$OUT/_sources" "$OUT/.buildinfo"

printf 'built %s files into %s\n' \
    "$(find "$OUT" -type f | wc -l | tr -d ' ')" "$OUT"
