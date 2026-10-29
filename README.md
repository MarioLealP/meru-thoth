# Thoth

Meru's CMDB: what exists, how it connects, and how it got that way.

Part of [Meru](https://git.clockmost.com/Meru), a small self-built platform for running apps on a home Kubernetes
cluster. Shikigami (the request controller) records what it creates here first; scanners for each source (Kubernetes,
Authentik, Gitea, hosts, the router) report what they actually see; Thoth merges the two.

## The model

- **Items** - one thing each: a host, an app, an account, a volume. An item has a stable `id` and a `type` + `key`
  that every source spells the same way (the key format is part of the type).
- **Relations** - typed, directed links, dependent first: `recipes uses recipes-db`, `recipes owned-by mario`.
  Ownership is a relation too, not a column.
- **Observations** - what each source reported about an item. Each source owns its own fields; the merged result is
  the item's `attributes`.
- **Status** - `planned` (requested, not seen yet), `active`, `missing` (a source stopped seeing it), `retired`.

## Nothing changes silently

The rules live in the database (Postgres triggers), so no code path can skip them:

- every write names an **actor** and a **reason** (`thoth.db.write_as`), or the database refuses it;
- every real change copies the previous version into `item_history` / `relation_history`, with when it was valid;
  a scan that only bumps `last_seen` is not a change;
- history is append-only, and rows are never deleted - things are `retired`. Only the yearly purge removes rows that
  have been retired for over two years, after exporting them.

## Developing

Python 3.15 with [uv](https://docs.astral.sh/uv/), and podman for the test database and the API linters.

```sh
uv sync
scripts/check.sh                   # everything CI runs: ruff, ty, pytest, openapi.json, vacuum, oasdiff
scripts/test-db.sh                 # a throwaway Postgres 16 - prints its URL
scripts/test-db.sh stop
```

Changing the model: edit `src/thoth/models/` (tables) or `src/thoth/sql/` (functions and triggers), then

```sh
export THOTH_DATABASE_URL=$(scripts/test-db.sh)
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "what changed"
```

Read the generated migration before committing it. A test fails if the models and the migrations disagree.

Changing the API: run `uv run scripts/openapi.py` and commit `openapi.json`. CI lints it against `.spectral.yaml`
and fails on breaking changes against `main`.

## License

MIT
