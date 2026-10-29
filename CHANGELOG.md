# Changelog

All notable changes to Thoth. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/), with the API contract (`openapi.json`) as the public surface.

## [Unreleased]

### Added

- Data model: item and relation types (the catalog), items, relations, per-source observations.
- Change tracking in the database: every write names an actor and a reason, every real change keeps the previous
  version in a history table, history is append-only, and nothing is deleted outside the yearly purge.
- API skeleton: `/heartbeat`, RFC 9457 problem details for every error.
- API contract checks: house lint rules (`.spectral.yaml`, run with vacuum) and breaking-change detection (oasdiff).
