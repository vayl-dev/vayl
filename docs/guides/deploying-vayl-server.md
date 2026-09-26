---
description: >-
  Run Vayl as an authenticated, self-hosted team server — install, Docker, auth,
  TLS, Postgres, and key custody.
icon: globe
---

# Deploying vayl-server

`vayl-server` exposes the same MCP tools as `vayl-mcp`, but over authenticated streamable-HTTP so a whole team shares one deployment. This guide takes you from install to a hardened production setup.

## 1. Install and run

```bash
pip install "vayl-mcp[server]"
VAYL_HOST=0.0.0.0 VAYL_PORT=8080 vayl-server
```

The server listens on `http://VAYL_HOST:VAYL_PORT/mcp`.

| Path       | Auth     | Purpose                                 |
| ---------- | -------- | --------------------------------------- |
| `/mcp`     | required | the MCP endpoint your agents connect to |
| `/healthz` | open     | liveness probe                          |
| `/readyz`  | open     | readiness probe (checks the database)   |
| `/metrics` | open\*   | Prometheus metrics                      |

\*Set `VAYL_METRICS_TOKEN` to require a bearer token on `/metrics`.

## 2. Authentication

Every request to `/mcp` must send `Authorization: Bearer vayl_sk_…`. A missing or invalid key returns `401`. See Authentication & access for the full model.

### Bootstrap the first admin

A fresh database has no principals. Create the first admin over local stdio, copy its key, then use it against the server:

```bash
vayl-mcp    # then call: create_principal("you", role="admin")
```

From then on, use that admin key to `create_principal` for the rest of your team — and **scope** non-admin keys to the spaces they need.

## 3. Docker

```bash
docker compose up -d --build

# bootstrap the first admin (one-off):
docker compose run --rm vayl python -c \
  "from vayl.api import mcp_server as s; print(s.create_principal('admin', role='admin'))"

curl localhost:8080/healthz     # liveness
```

The container runs as a **non-root** user; the SQLite database and the encryption/signing keys persist on the `vayl-data` volume (`/data`). Configure the LLM and any license via env in `docker-compose.yml`.

## 4. TLS and running behind a proxy

`vayl-server` speaks plain HTTP by design — **terminate TLS at your reverse proxy or ingress**, and never expose the server raw.

{% hint style="warning" %}
Behind a proxy the socket peer is the proxy, so also set:

* `VAYL_TRUSTED_PROXY_HOPS` — the number of trusted proxies, so per-IP rate limiting reads the real client from `X-Forwarded-For`.
* `VAYL_ALLOWED_HOSTS` — your public host(s), so DNS-rebinding protection allows legitimate traffic (it stays enabled either way).
{% endhint %}

**Rate limiting.** The built-in limit (`VAYL_RATE_PER_MIN`, default 120 requests per minute per client IP) is kept in memory **per process**: with N `vayl-server` processes the effective ceiling is N × the setting. For a single global limit, rate-limit at your proxy or ingress — the layer that sees all traffic.

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

## Hardening checklist

* [ ] Behind a TLS-terminating proxy; never `0.0.0.0` raw.
* [ ] `VAYL_ALLOWED_HOSTS` and `VAYL_TRUSTED_PROXY_HOPS` set for your topology.
* [ ] A global rate limit at the proxy if you run more than one `vayl-server` process.
* [ ] Non-admin keys are **scoped**; rotate by re-issuing.
* [ ] `/metrics` on an internal network or gated with `VAYL_METRICS_TOKEN`.
* [ ] Encryption + signing on (default); `VAYL_KMS=vault` for production key custody.
* [ ] If the graph is enabled: `NEO4J_PASSWORD` set (Vayl refuses to start the graph without one).
* [ ] `VAYL_LOG_LEVEL` at `WARNING` or `INFO` in production — `DEBUG` logs can contain memory content.
* [ ] OS full-disk encryption; restrict permissions on the data directory.

## Next

* Configuration — every environment variable.
* Safety gates & human approval — guardrails for high-stakes agents.
