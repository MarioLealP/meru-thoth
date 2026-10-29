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
- `decommissioning` status; only Anubis may set `retired` (enforced in the database).
- API contract checks: house lint rules (`.spectral.yaml`, run with vacuum) and breaking-change detection (oasdiff).
