#!/usr/bin/env bash
# Every check CI runs, runnable locally: lint, format, types, tests, and the API contract.
#   scripts/check.sh                  compare the API against origin/main
#   THOTH_API_BASE=<ref> scripts/check.sh
# Needs podman (test database, vacuum, oasdiff).
set -euo pipefail
cd "$(dirname "$0")/.."

VACUUM=ghcr.io/daveshanley/vacuum@sha256:1f52aa297064b112e37804dded78c0e677514146dacea837f3f0e6df1256172e  # v0.35.7
OASDIFF=docker.io/tufin/oasdiff@sha256:5d409033dfdf1041800f2f5a7af76a6e876b06c80c16771f0652a64c9a44fabc  # v1.35.0
base=${THOTH_API_BASE:-origin/main}

step() { printf '\n== %s\n' "$*"; }

step ruff
uv run ruff check .
uv run ruff format --check .
step ty
uv run ty check
step pytest
THOTH_TEST_DATABASE_URL=${THOTH_TEST_DATABASE_URL:-$(scripts/test-db.sh)} uv run pytest -q

step "openapi.json and catalog.json up to date"
uv run scripts/generate.py --check
step "catalog: breaking changes against $base"
uv run scripts/catalog_breaking.py "$base"
step "vacuum (house rules, .spectral.yaml)"
podman run --rm -v "$PWD:/work:Z,ro" -w /work "$VACUUM" lint -r .spectral.yaml -n warn -d openapi.json
step "oasdiff: breaking changes against $base"
if git cat-file -e "$base:openapi.json" 2>/dev/null; then
    old=$(mktemp -d)
    trap 'rm -rf "$old"' EXIT
    git show "$base:openapi.json" >"$old/openapi.json"
    if ! podman run --rm -v "$old:/base:Z,ro" -v "$PWD:/work:Z,ro" "$OASDIFF" \
        breaking --fail-on ERR /base/openapi.json /work/openapi.json; then
        # Before the first release only: acknowledge with a commit trailer. Released, a break is a new /vN (D50).
        if git log "$base..HEAD" --format='%(trailers:key=API-Break,valueonly)' | grep -q .; then
            echo "acknowledged by an API-Break trailer"
        else
            echo "breaking API change - a new /vN route, or before the first release an \`API-Break: <why>\` trailer"
            exit 1
        fi
    fi
else
    echo "no openapi.json on $base yet - nothing to compare"
fi
printf '\nall checks passed\n'
