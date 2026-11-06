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

- every write names an **actor** and a **reason** (`thoth.db.session.write_as`), or the database refuses it;
- every real change copies the previous version into `item_history` / `relation_history`, with when it was valid;
  a scan that only bumps `last_seen` is not a change;
- history is append-only, and rows are never deleted - things are `retired`. Only the yearly purge removes rows that
  have been retired for over two years, after exporting them.

## The catalog

Every item and relation type is a Pydantic class in `src/thoth/catalog/`: its key format, its attributes, and for
each attribute the sources allowed to report it, in precedence order (`owned_by(Source.SHIKIGAMI, Source.CADDY)`:
what was requested wins over what Caddy shows). A source may only send the fields it owns.

Changing it: new types and new optional fields just ship. Anything that breaks stored data (a removed or retyped
field, a newly required one, a moved owner, a changed key format) fails CI until the PR carries a data migration and
a `Catalog-Break: <why>` commit trailer. And a new version only reports ready once every stored row fits its catalog,
so a bad change never replaces the running one.

## API

Client routes live under `/v1/`, so a later `/v2` can change shapes while `/v1` keeps working. Only the probes
(`/heartbeat`, `/ready`) sit outside.

## Who may call

Every route except `/heartbeat` and `/ready` needs an Authentik access token (client credentials - one bot per job,
one per scanner instance) and a permission in it: `thoth.read`, `thoth.report:<source>@<scope>` (a scan's source
and scope must match exactly), `thoth.manual` (corrections by hand), `thoth.verdict` (retiring - the database checks
it too). Authentik is the one place that grants them.

## Developing

Python 3.15 with [uv](https://docs.astral.sh/uv/), and podman for the test database and the API linters.

```sh
uv sync
scripts/check.sh                   # everything CI runs: ruff, ty, pytest, contracts, vacuum, oasdiff
scripts/test-db.sh                 # a throwaway Postgres 16 - prints its URL
scripts/test-db.sh stop
```

Changing the model: edit `src/thoth/models/` (tables) or `src/thoth/db/` (functions and triggers), then

```sh
export THOTH_DATABASE_URL=$(scripts/test-db.sh)
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "what changed"
```

Read the generated migration before committing it. A test fails if the models and the migrations disagree.

Changing the API or the catalog: run `uv run scripts/generate.py` and commit `openapi.json` and `catalog.json`.
CI lints the API against `.spectral.yaml` and fails on breaking changes against `main` - for the API that means a
new `/vN` route (before the first release, an `API-Break: <why>` commit trailer).

## Deploy

`deploy/meru.yaml` makes this repo a Meru app: [Meru/meru](https://git.clockmost.com/Meru/meru) finds it and deploys
it into namespace `thoth` as two Applications - the app (`deploy/values.yaml`, rendered with
[Meru/nuwa](https://git.clockmost.com/Meru/nuwa)'s `nuwa-app` chart; migrations run first) and its database
(`deploy/database/`, one CNPG cluster), kept apart so no app deploy can touch the data. Merging to `main` builds the
image and rolls it out; builds run in `thoth-build` on [Meru/sindri](https://git.clockmost.com/Meru/sindri)'s runner,
whose `sindri-check` label (the image the `checks` job runs in) is set in `deploy/meru.yaml`.

## License

MIT
