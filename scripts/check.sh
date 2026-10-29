#!/usr/bin/env bash
# Every check CI runs, runnable locally: lint, format, types, tests, and the API contract.
#   scripts/check.sh                  compare the API against origin/main
#   THOTH_API_BASE=<ref> scripts/check.sh
# Needs podman for the test database, unless THOTH_TEST_DATABASE_URL points at one (CI: a service container).
# vacuum and oasdiff are pinned binaries (scripts/tools.py), the same here and in CI.
set -euo pipefail
cd "$(dirname "$0")/.."

base=${THOTH_API_BASE:-origin/main}
PATH="$(python3 scripts/tools.py):$PATH"

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
vacuum lint -r .spectral.yaml -n warn -d openapi.json
step "oasdiff: breaking changes against $base"
if git cat-file -e "$base:openapi.json" 2>/dev/null; then
    old=$(mktemp -d)
    trap 'rm -rf "$old"' EXIT
    git show "$base:openapi.json" >"$old/openapi.json"
    if ! oasdiff breaking --fail-on ERR "$old/openapi.json" openapi.json; then
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
