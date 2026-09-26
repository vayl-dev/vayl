# Changelog

All notable changes to Vayl are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

Hardening release. Several changes are **breaking** for misconfigured or partially-configured setups;
each now fails at startup with a message naming the setting.

### Removed
- **`vayl.clinical`** (FHIR ingestion and discharge medication reconciliation). It was not wired into
  any MCP tool or entry point. The **`preset:clinical`** slot schema is unchanged, and its engine
  behavior (list slots, critical tagging, confirmation gates, trusted sources) keeps its tests.

### Security
- **Erasure fails closed.** If the Neo4j graph purge fails, `delete` / `delete_all` erase nothing and
  issue **no** signed receipt, instead of a receipt for a partial erasure.
- **`NEO4J_PASSWORD` is required** when `VAYL_GRAPH=1` (it fell back to a hard-coded default). The
  shipped `docker-compose.yml` takes it from `.env` and binds Neo4j to localhost.
- **Exception text no longer leaks.** `stats()` shows error text to admins only (any role could read
  errors carrying another tenant's memory), and `ERROR` logs carry a ref, type and location, not the
  text; full detail is at `DEBUG`.
- `cryptography>=50` (PYSEC-2026-3552); `pyjwt>=2.13.0` for the `sso` extra (CVE-2022-29217).

### Changed
- **Invalid configuration fails at startup:** unknown `LLM_PROVIDER` (it silently routed extraction to
  Anthropic), unknown `VAYL_KMS` (it silently fell back to a key file), `VAYL_KMS=vault` without
  `VAULT_TOKEN`, and malformed numeric settings.
- Logging via the standard library to stderr; set the level with `VAYL_LOG_LEVEL` (default `WARNING`).
- `health()` makes one attempt per dependency instead of backing off for minutes when one is down.

### Fixed
- Mismatched embedding dimensions (e.g. after changing `EMBED_MODEL`) no longer produce a meaningless
  ranking; recall falls back to lexical ranking and logs it.
- `VAYL_EXTRACT_RETRIES` below 0 no longer skips extraction and crashes.

## [0.3.0] — 2026-07-29

Framework migration: Vayl's MCP server now runs on the maintained standalone **FastMCP** framework
instead of the bundled `mcp.server.fastmcp` (which MCP SDK 2.0 removed).

### Changed
- **Migrated to standalone FastMCP** (`fastmcp>=3,<4`). Unblocks the previous `mcp<2` cap and enables
  **one-command client install** — `fastmcp install {claude-desktop, claude-code, cursor, gemini-cli,
  mcp-json}` — plus FastMCP's auth/middleware/provider system.
- **Scope-aware SDKs.** FastMCP validates tool arguments strictly and rejects unknown ones. The
  Python and TypeScript clients now learn each tool's parameters on connect and send
  `user_id`/`agent_id`/`run_id` only to tools that accept them — so a client-wide default scope no
  longer breaks scope-less tools (`health`, `stats`, `verify_audit`, `export_public_key`, …).

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

[0.3.0]: https://github.com/vayl-dev/vayl/releases/tag/v0.3.0
[0.2.0]: https://github.com/vayl-dev/vayl/releases/tag/v0.2.0
[0.1.0]: https://github.com/vayl-dev/vayl/releases/tag/v0.1.0
