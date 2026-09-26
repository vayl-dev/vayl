# Changelog

All notable changes to Vayl are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [0.5.0] — 2026-09-26

Faster tool calls on large memories, versioned schema migrations, and request-level logging for the
HTTP server. No breaking API changes; see **Upgrading from 0.4** below.

### Performance
- **Faster tool calls on large memories.** Every tool call reloads the memory space. Before this change
  it decrypted every row again and re-parsed every embedding. Short encrypted fields now go through a
  bounded ciphertext→plaintext cache. Decoded embeddings are cached by the SHA-256 of their stored text
  and carry a precomputed norm, and recall ranks with `math.sumprod` on Python 3.12+. Every hard delete
  (`forget`, account erasure, retention expiry) clears both caches, so erased data doesn't stay in
  process memory. At 1,000 facts, with encryption on and model calls stubbed out, median `remember`
  drops from 122 ms to 23 ms and `recall` from 444 ms to 59 ms.

### Added
- **Request IDs on the HTTP server.** Every request gets an ID, echoed in the `X-Request-ID` response
  header. A plain incoming `X-Request-ID` (up to 64 characters of `A-Z a-z 0-9 . _ : -`) is reused so
  proxy and Vayl logs line up. The ID appears on every log line written while serving that request, so
  an error `ref` shown to a client can be traced to its request. Each request also writes one access
  line at `INFO` with method, path, status and duration, and no client IP, query string or body. Health,
  readiness and metrics probes log at `DEBUG`.
- **Versioned schema migrations.** The schema is now one ordered list of migrations
  (`vayl/storage/migrations.py`), recorded in a new `schema_migrations` table and applied on startup
  under a lock (a cross-process advisory lock on Postgres). A Vayl older than the database's schema
  refuses to start instead of writing rows it doesn't understand. `vayl-migrate status` and
  `vayl-migrate up` show and apply migrations. Existing databases, including ones from releases before
  0.4, are brought to the baseline (v1) automatically, with no data changes.
- `VAYL_LOG_FORMAT=json` writes one JSON object per log line (default `text`). An unknown value fails
  at startup.
- `VAYL_VECTOR_CACHE` (default 8192): how many decoded embeddings to keep, about 6 KB each at 1536
  dimensions. A space with more embedded facts than this re-decrypts the overflow on every recall.

### Changed
- Text-format log lines now include the request ID in brackets after the logger name
  (`… INFO vayl.api.server [3f9a…]: …`), shown as `[-]` on stdio. Update any log parser that matches
  the old format, or switch to `VAYL_LOG_FORMAT=json`.

### Security
- CI now fails on a dependency with a known vulnerability (`pip-audit`) instead of warning, and GitHub
  secret scanning with push protection is on for the repository.

### Upgrading from 0.4
- No action needed. On first start, 0.5 creates `schema_migrations` and records the existing schema as
  v1. It changes no data.
- Rolling back to 0.4.0 is safe: 0.4.0 ignores the new table and reads the database as before.
- If you run several server processes on one Postgres, you can run `vayl-migrate up` once before
  rolling out. If you don't, the first process to start applies the migrations under an advisory lock.

## [0.4.0] — 2026-09-26

Framework adapters, three new presets, per-tenant isolation, and a hardening pass. Several changes are
**breaking** for misconfigured or partially-configured setups; each now fails at startup with a message
naming the setting. See **Upgrading from 0.3** below.

### Added
- **Agent-framework adapters.** `vayl.integrations.langgraph`, `.openai_agents` and `.crewai` expose the
  same memory tools (`remember`, `recall`, `history`, `forget`, `list_memories`) with the caller's scope
  bound server-side. Install with the `langgraph`, `openai-agents` or `crewai` extra. (Vercel AI SDK and
  Mastra adapters ship in the TypeScript client, `@vayl.dev/client`.)
- **Presets `coding`, `assistant` and `sales`** (`VAYL_SLOT_SCHEMA=preset:coding`, …), alongside
  `clinical`, `finance` and `support`.
- **Tenant partitioning.** A principal's `tenant` now filters every store query, so two organizations on
  one deployment never see each other's memory, even under the same `user_id`.
- `examples/coding_assistant/`: a runnable example of an agent that remembers project decisions.
- `VAYL_LOG_LEVEL` (default `WARNING`); logs go to stderr.

### Changed
- **Invalid configuration fails at startup:** unknown `LLM_PROVIDER` (it silently routed extraction to
  Anthropic), unknown `VAYL_KMS` (it silently fell back to a key file), `VAYL_KMS=vault` without
  `VAULT_TOKEN`, and malformed numeric settings.
- `health()` makes one attempt per dependency instead of backing off for minutes when one is down.

### Security
- **Erasure fails closed.** If the Neo4j graph purge fails, `delete` / `delete_all` erase nothing and
  issue **no** signed receipt, instead of a receipt for a partial erasure.
- **`NEO4J_PASSWORD` is required** when `VAYL_GRAPH=1` (it fell back to a hard-coded default). The
  shipped `docker-compose.yml` takes it from `.env` and binds Neo4j to localhost.
- **Exception text no longer leaks.** `stats()` shows error text to admins only (any role could read
  errors carrying another tenant's memory), and `ERROR` logs carry a ref, type and location, not the
  text; full detail is at `DEBUG`.
- `cryptography>=50` (PYSEC-2026-3552); `pyjwt>=2.13.0` for the `sso` extra (CVE-2022-29217).

### Fixed
- A declared single-valued slot now supersedes the old value even when a weak extractor mislabels the
  change as an event; previously both values stayed active.
- Mismatched embedding dimensions (e.g. after changing `EMBED_MODEL`) no longer produce a meaningless
  ranking; recall falls back to lexical ranking and logs it.
- `VAYL_EXTRACT_RETRIES` below 0 no longer skips extraction and crashes.

### Performance
- `load()` stays fast as a memory space's history grows: 3.2 ms instead of 93 ms with 200,000 retired
  facts in one space (SQLite, 500 active facts).

### Removed
- **`vayl.clinical`** (FHIR ingestion and discharge medication reconciliation). It was not wired into any
  MCP tool or entry point. The **`preset:clinical`** slot schema is unchanged.

### Upgrading from 0.3
1. **Back up your database.** The first start applies schema changes automatically (a `tenant_id`
   column; hot-path indexes replace `idx_space`).
2. If you set `VAYL_GRAPH=1`, set `NEO4J_PASSWORD`.
3. `LLM_PROVIDER` must be `openai`, `anthropic` or `groq`. For Ollama or vLLM, use `openai` with
   `OPENAI_BASE_URL`.
4. `VAYL_KMS` must be `file` or `vault`; `vault` requires `VAULT_TOKEN`.
5. Code importing `vayl.clinical` must vendor it. Code importing private helpers from
   `vayl.memory.llm_memory` (e.g. `_embed`, `_http_json`) should import them from
   `vayl.memory.llm_client` or `vayl.memory.retrieval`.

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
