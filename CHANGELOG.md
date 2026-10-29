# Changelog

All notable changes to Thoth. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/), with the API contract (`openapi.json`) as the public surface.

## [Unreleased]

### Added

- Data model: item and relation types (the catalog), items, relations, per-source observations.
- Change tracking in the database: every write names an actor and a reason, every real change keeps the previous
  version in a history table, history is append-only, and nothing is deleted outside the yearly purge.
- API skeleton: `/heartbeat` (liveness), `/ready` (readiness), RFC 9457 problem details for every error. Client
  routes live under `/v1/`.
- The catalog: 17 item types and 14 relation types as Pydantic classes, every attribute tagged with the sources that
  may report it (first one wins); `GET /v1/catalog`; exported to `catalog.json`.
- Catalog safety: breaking catalog changes fail CI unless acknowledged with a `Catalog-Break:` commit trailer, and
  a new version stays unready while any stored row doesn't fit its catalog.
- Scans (D54): `PUT /v1/scans/{scan_id}/{source}/{scope}/items`, then `.../relations` - a source's whole picture
  from one scope, all or nothing. Every owned field must be sent (`null` when empty); anything wrong refuses the
  snapshot. Slices merge by field precedence; misses count once per scan; 3 misses (or, for types that come and go,
  30 days unseen) put a thing on Anubis's list; links whose other end doesn't exist yet are skipped and heal on the
  next scan. `GET /v1/snapshots` - each source's latest run.
- Renames: `POST /v1/renames` - same item, new key; the old key stays in history. Only the first reporter or
  `manual` may rename.
- Reads: `GET /v1/items` (filters: type, key, status, source; paged), `GET /v1/items/{id}`, and the web around an
  item - `GET /v1/items/{id}/impact` (what depends on it) and `/dependencies` (what it needs): one recursive query,
  each item once at its shortest distance with the link it came by, loops reported under `cycles` instead of
  followed, retired links skipped, `depth` 1-50 (default 10).
- `decommissioning` status; only Anubis may set `retired` (enforced in the database).
- API contract checks: house lint rules (`.spectral.yaml`, run with vacuum) and breaking-change detection (oasdiff).
