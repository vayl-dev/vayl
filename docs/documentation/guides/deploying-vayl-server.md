---
description: >-
  Run Vayl as an authenticated, self-hosted team server — install, Docker, auth,
  TLS, Postgres, key custody, logging, and upgrades.
icon: globe
---

# Deploying vayl-server

`vayl-server` exposes the same MCP tools as `vayl-mcp` over authenticated streamable HTTP, so a whole team shares one deployment. This guide takes you from install to a hardened production setup.

{% hint style="info" %}
Use 0.6.0 or later. Earlier releases returned empty responses over the HTTP transport.
{% endhint %}

## 1. Install and run

```bash
pip install "vayl-mcp[server]"
VAYL_HOST=0.0.0.0 VAYL_PORT=8080 vayl-server
```

The server listens on `http://VAYL_HOST:VAYL_PORT/mcp` (default `http://127.0.0.1:8080/mcp`). `vayl-server --version` prints the installed version.

| Path       | Auth     | Purpose                                 |
| ---------- | -------- | --------------------------------------- |
| `/mcp`     | required | the MCP endpoint your agents connect to |
| `/healthz` | open     | liveness probe                          |
| `/readyz`  | open     | readiness probe (checks the database)   |
| `/metrics` | open\*   | Prometheus metrics                      |

\*Set `VAYL_METRICS_TOKEN` to require a bearer token on `/metrics`.

## 2. Authentication

Every request to `/mcp` must send `Authorization: Bearer <credential>`, where the credential is a Vayl API key (`vayl_sk_…`) or, with OIDC single sign-on configured, an ID token (JWT) from your identity provider. A missing or invalid credential returns `401`. `vayl-server` always requires authentication, whatever `VAYL_AUTH_REQUIRED` is set to. See [Authentication & access](../core-concepts/authentication-and-access.md) for the full model.

### Bootstrap the first admin

A fresh database has no principals, and the server prints a reminder at startup. Create the first admin against the same database and key files the server uses. Without `VAYL_AUTH_REQUIRED`, a local process runs as a trusted local admin:

```bash
VAYL_DB=/data/vayl.db python -c \
  "from vayl.api import mcp_server as s; print(s.create_principal('you', role='admin'))"
```

```
Created principal prin_… 'you' (role: admin, kind: agent, tenant: default).
  API key (shown once — save it now):
  vayl_sk_…
```

You can also call `create_principal("you", role="admin")` from an MCP client connected to `vayl-mcp` with the same `VAYL_DB`.

From then on, use that admin key to `create_principal` for the rest of your team — and **scope** non-admin keys to the spaces they need.

### Single sign-on with OIDC

People can authenticate with an ID token from Okta, Entra ID, Google or Auth0 instead of an API key. Install the extra and set the three required variables:

```bash
pip install "vayl-mcp[server,sso]"
VAYL_OIDC_ISSUER=https://acme.okta.com/oauth2/default \
VAYL_OIDC_AUDIENCE=vayl \
VAYL_OIDC_JWKS_URL=https://acme.okta.com/oauth2/default/v1/keys \
VAYL_OIDC_ROLE_MAP='{"vayl-admins":"admin","eng":"member"}' \
vayl-server
```

The client sends the token as `Authorization: Bearer <jwt>`. Vayl verifies the RS256 signature against the JWKS, plus issuer, audience and expiry, then maps the `groups` claim to roles; unmapped users get `VAYL_OIDC_DEFAULT_ROLE` (`viewer`). SSO is honoured only when the license grants `sso`; otherwise the server prints a warning at startup and accepts API keys only. In a multi-tenant deployment, set `VAYL_OIDC_SCOPE_CLAIM` so SSO users are confined like scoped keys. All options are in [Configuration](../reference/configuration.md#oidc-single-sign-on).

## 3. Docker

```bash
docker compose up -d --build

# bootstrap the first admin (one-off):
docker compose run --rm -e VAYL_AUTH_REQUIRED=0 vayl python -c \
  "from vayl.api import mcp_server as s; print(s.create_principal('admin', role='admin'))"

curl localhost:8080/healthz     # liveness
```

The image sets `VAYL_AUTH_REQUIRED=1`, so the bootstrap command turns it off for that one-off container only; without `-e VAYL_AUTH_REQUIRED=0` it returns `Access denied: authentication required`. The server itself always requires a key.

The container runs as a **non-root** user. The SQLite database and the encryption and signing keys persist on the `vayl-data` volume (`/data`). Copy `.env.example` to `.env` and set the model variables there; `docker-compose.yml` reads them from it.

{% hint style="warning" %}
`docker-compose.yml` fills in `OPENAI_MODEL=gpt-5-mini` and `EMBED_MODEL=text-embedding-3-small` when `.env` doesn't set them. To use Ollama on the Docker host, set both explicitly (see [Local models with Ollama](local-models-with-ollama.md)).
{% endhint %}

Postgres and Neo4j are optional Compose profiles:

| Profile | Start with | Then set in `.env` |
| --- | --- | --- |
| `postgres` | `docker compose --profile postgres up -d` | `VAYL_DATABASE_URL=postgresql://vayl:vayl@postgres:5432/vayl` (the profile's default credentials; change them for anything real) |
| `graph` | `docker compose --profile graph up -d` | `NEO4J_PASSWORD`, then add `VAYL_GRAPH: "1"`, `NEO4J_URI: "bolt://neo4j:7687"` and `NEO4J_PASSWORD: "${NEO4J_PASSWORD}"` to the `vayl` service's `environment` |

The `vayl` service only passes through the variables listed in its `environment` block, so any other setting (for example `VAYL_ALLOWED_HOSTS` or `VAYL_TRUSTED_PROXY_HOPS`) has to be added there too.

## 4. TLS and running behind a proxy

`vayl-server` speaks plain HTTP by design — **terminate TLS at your reverse proxy or ingress**, and never expose the server raw.

{% hint style="warning" %}
Behind a proxy the socket peer is the proxy, so also set:

* `VAYL_TRUSTED_PROXY_HOPS` — the number of trusted proxies, so per-IP rate limiting reads the real client from `X-Forwarded-For`.
* `VAYL_ALLOWED_HOSTS` — your public host(s), so DNS-rebinding protection allows legitimate traffic (it stays enabled either way).
{% endhint %}

**Rate limiting.** The built-in limit (`VAYL_RATE_PER_MIN`, default 120 requests per minute per client IP) is kept in memory **per process**: with N `vayl-server` processes the effective ceiling is N × the setting. For a single global limit, rate-limit at your proxy or ingress, the layer that sees all traffic, and set `VAYL_RATE_PER_MIN=0` to turn the built-in limit off.

## 5. Storage and scaling

SQLite is the default — one file, nothing to operate. For multiple concurrent writers, point Vayl at Postgres:

```bash
pip install "vayl-mcp[postgres]"
VAYL_DATABASE_URL=postgresql://user:pass@host/vayl vayl-server
```

* Multiple `vayl-server` processes can share one Postgres. Same-space writes serialize via a cross-process advisory lock; **different spaces run in parallel**, so you scale out by adding processes/nodes sharded by space.
* Within a single process, operations on different memory spaces already run concurrently (thread-local connections, per-space locking).

## 6. Key custody (KMS)

By default the encryption and signing keys are auto-generated files beside the data (`VAYL_KMS=file`). For production, use **HashiCorp Vault**:

```bash
VAYL_KMS=vault VAULT_ADDR=https://vault:8200 VAULT_TOKEN=… vayl-server
```

Vault Transit envelope-encrypts the data key: the master key never leaves Vault, only a wrapped blob sits on disk, and it is unwrapped into memory at startup. If Vault is unreachable, Vayl **fails closed** (won't start) rather than run unencrypted. `VAULT_TOKEN` is required, and any `VAYL_KMS` value other than `file` or `vault` is rejected at startup.

## 7. Logging

Vayl logs to stderr (never stdout, which the stdio transport uses). Set `VAYL_LOG_LEVEL` — `WARNING` by default. `ERROR` lines carry a reference, the exception type and the code location, but never the exception text, which can contain memory content. That detail is logged only at `DEBUG`, so enable `DEBUG` deliberately and treat those logs as sensitive.

**Request IDs.** Every request gets an ID, returned in the `X-Request-ID` response header and included on every log line written while serving it. If your proxy sends its own `X-Request-ID` (up to 64 characters of letters, digits and `. _ : -`), Vayl reuses it so the two logs line up. When a client reports an error `ref`, search the logs for it to find the request ID, then everything else that request did.

At `INFO`, each request writes one access line with method, path, status and duration. It never includes the client IP, query string or body. Health, readiness and metrics probes log at `DEBUG`.

Set `VAYL_LOG_FORMAT=json` for one JSON object per line:

```json
{"ts": "2026-09-26T10:41:10+0000", "level": "INFO", "logger": "vayl.api.server", "request_id": "3f9a1c2e4b7d8a01", "msg": "POST /mcp 200 41.2ms", "method": "POST", "path": "/mcp", "status": 200, "duration_ms": 41.2}
```

## 8. Upgrades and rollback

Back up the database (and, with `VAYL_KMS=file`, the key files) before upgrading. Vayl applies pending schema migrations on startup, in order, and records them in the `schema_migrations` table.

```bash
vayl-migrate status   # applied and pending migrations
vayl-migrate up       # apply pending migrations now
```

If the graph is enabled and you're upgrading from 0.6 or earlier, also run `vayl-migrate reproject-graph` once: 0.7 put the tenant in the graph namespace, and edges written earlier need rebuilding. See the [CLI reference](../reference/cli.md#vayl-migrate-reproject-graph).

Migrations run under a lock, so several processes starting at once are safe. With several `vayl-server` processes on one Postgres, you can still run `vayl-migrate up` once before rolling out.

**Rolling back.** A Vayl older than the database's schema refuses to start and says which version it found, so it never writes to a schema it doesn't understand. To roll back, restore the backup you took before upgrading. 0.7 adds two migrations: v2 (`policy-per-tenant`) rebuilds the reconcile-policy table and isn't additive, so 0.6 can't run on a 0.7 database; v3 (`tenant-accountability`) is additive. The [CLI reference](../reference/cli.md#vayl-migrate) lists every migration.

## Hardening checklist

* [ ] Behind a TLS-terminating proxy; never `0.0.0.0` raw.
* [ ] `VAYL_ALLOWED_HOSTS` and `VAYL_TRUSTED_PROXY_HOPS` set for your topology (and `VAYL_ALLOWED_ORIGINS` for browser clients).
* [ ] A global rate limit at the proxy if you run more than one `vayl-server` process.
* [ ] Non-admin keys are **scoped**; rotate by re-issuing.
* [ ] `/metrics` on an internal network or gated with `VAYL_METRICS_TOKEN`.
* [ ] Encryption + signing on (default); `VAYL_KMS=vault` for production key custody.
* [ ] If the graph is enabled: `NEO4J_PASSWORD` set (Vayl refuses to start the graph without one).
* [ ] `VAYL_LOG_LEVEL` at `WARNING` or `INFO` in production — `DEBUG` logs can contain memory content.
* [ ] OS full-disk encryption; restrict permissions on the data directory.
* [ ] Backups tested, and taken before every upgrade.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>Every environment variable, with defaults.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr><tr><td><h4><i class="fa-gear" style="color:$primary;">:gear:</i> Safety gates &#x26; human approval</h4></td><td>Guardrails for high-stakes agents.</td><td><a href="safety-gates-and-human-approval.md">safety-gates-and-human-approval.md</a></td></tr><tr><td><h4><i class="fa-wrench" style="color:$primary;">:wrench:</i> Troubleshooting</h4></td><td>401s, 429s, host rejections and startup errors.</td><td><a href="../reference/troubleshooting.md">troubleshooting.md</a></td></tr></tbody></table>
