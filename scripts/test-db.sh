#!/usr/bin/env bash
# A throwaway Postgres 16 (what CNPG runs) for tests and autogenerate. Prints the URL to export.
#   scripts/test-db.sh        start (or reuse) it
#   scripts/test-db.sh stop   remove it
set -euo pipefail
name=thoth-test-db
port=55432
if [[ ${1:-} == stop ]]; then
    podman rm -f "$name" >/dev/null
    exit 0
fi
if ! podman container exists "$name"; then
    podman run -d --rm --name "$name" -p "127.0.0.1:$port:5432" \
        -e POSTGRES_USER=thoth -e POSTGRES_PASSWORD=thoth -e POSTGRES_DB=thoth \
        docker.io/library/postgres:16.15-trixie@sha256:1beaa3084eee2676f20a9cd9928f0bf2c5bf674f8e49f81017912183d377c61c >/dev/null
fi
until podman exec "$name" pg_isready -U thoth -q; do sleep 0.5; done
echo "postgresql+psycopg://thoth:thoth@127.0.0.1:$port/thoth"
