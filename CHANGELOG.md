# Changelog

All notable changes to Vayl are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-07-28

Onboarding and write-path release: try Vayl in 30 seconds, call it from Python or TypeScript, and
use a domain schema without authoring JSON.

### Added
- **`vayl-demo`** — a zero-setup, ~30-second demonstration of reconciling memory. Runs the real
  engine on a scripted conversation (no keys or network needed); `--live` uses a reachable LLM.
- **Python client `vayl.Vayl`** — a synchronous client wrapping the MCP tools so you call methods
  (`m.remember(...)`, `m.recall(...)`, `m.call(tool, ...)`) instead of `tools/call` JSON. Works over
  stdio (spawns `vayl-mcp`) or authenticated streamable-HTTP.
- **TypeScript client** (`clients/typescript`, npm `vayl`) — the same surface for TS/JS agents:
  `await Vayl.connect({...})`, `m.remember/recall/call`, stdio or HTTP.
- **Built-in slot-schema presets** — `VAYL_SLOT_SCHEMA=preset:clinical` (also `finance`, `support`)
  loads a bundled declared-slot schema, no JSON authoring required.

### Changed
- **Pre-LLM dedup** — a verbatim restatement of facts that are all still active now skips the
  extractor entirely (0 LLM calls) instead of spending one and reconciling to a no-op. Provably no
  staleness regression; disable with `VAYL_DEDUP_PREFILTER=off`.

### Infrastructure
- OpenSSF Scorecard, CodeQL (SAST), and Dependabot workflows; least-privilege and SHA-pinned CI;
  a hardened security policy with private vulnerability reporting.

## [0.1.0] — 2026-07-27

Initial public release: the reconciling-memory engine and MCP server. A new value supersedes the
old, removals retract, ambiguous input is flagged, and history stays queryable — over stdio
(`vayl-mcp`) or an authenticated team server (`vayl-server`). SQLite by default, optional Postgres;
encryption at rest, an Ed25519-signed tamper-evident audit chain, RBAC, and GDPR tools.

[0.2.0]: https://github.com/vayl-dev/vayl/releases/tag/v0.2.0
[0.1.0]: https://github.com/vayl-dev/vayl/releases/tag/v0.1.0
