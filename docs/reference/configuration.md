---
description: >-
  Every environment variable Vayl reads, grouped by concern, with defaults and
  examples.
icon: sliders
---

# Configuration

Vayl is configured entirely through environment variables — there is no config file. Set them in your MCP client's `env` block, your shell, or `docker-compose.yml`.

## Core

| Variable            | Purpose                                     | Default   |
| ------------------- | ------------------------------------------- | --------- |
| `VAYL_DB`           | SQLite database path (use an absolute path) | `vayl.db` |
| `VAYL_DATABASE_URL` | Postgres URL; when set, overrides SQLite    | unset     |

```bash
VAYL_DB=/data/vayl.db
# or, for scale:
VAYL_DATABASE_URL=postgresql://user:pass@host/vayl
```

## LLM and embedder

Vayl calls one model to extract facts and an embedder for retrieval. Any OpenAI-compatible endpoint works; the provider is inferred from whichever key is present, or set it explicitly.

| Variable                         | Purpose                                        | Default               |
| -------------------------------- | ---------------------------------------------- | --------------------- |
| `LLM_PROVIDER`                   | `openai`, `anthropic`, or `groq`               | inferred              |
| `OPENAI_API_KEY`                 | API key                                        | unset -> local Ollama |
| `OPENAI_MODEL`                   | extractor model                                | `gpt-5-mini`          |
| `OPENAI_BASE_URL`                | OpenAI-compatible endpoint (local / EU-region) | OpenAI                |
| `EMBED_MODEL` / `EMBED_BASE_URL` | embedder model and endpoint                    | provider default      |

{% hint style="info" %}
With **no** LLM variables set, Vayl uses a local Ollama endpoint — nothing leaves the machine. That choice, not Vayl, sets your data-residency posture.
{% endhint %}

## Write path

| Variable               | Purpose                                                                                            | Default |
| ---------------------- | -------------------------------------------------------------------------------------------------- | ------- |
| `VAYL_DEDUP_PREFILTER` | skip the LLM extractor for a verbatim restatement of facts that are all still active (0 LLM calls) | on      |

The prefilter only skips when re-extraction is provably a no-op — if any fact from that utterance is no longer active, it falls through to the extractor, so a restatement that should supersede is never missed. Set `VAYL_DEDUP_PREFILTER=off` to always extract.

## Security

| Variable                     | Purpose                                              | Default               |
| ---------------------------- | ---------------------------------------------------- | --------------------- |
| `VAYL_ENCRYPT`               | at-rest encryption; `off` to disable                 | on                    |
| `VAYL_KEY`                   | passphrase; derives an off-disk key via Argon2id     | unset (auto key file) |
| `VAYL_KMS`                   | `file` or `vault` key custody                        | `file`                |
| `VAULT_ADDR` / `VAULT_TOKEN` | Vault Transit endpoint (when `VAYL_KMS=vault`)       | —                     |
| `VAYL_SIGN`                  | Ed25519 signing of the audit chain; `off` to disable | on                    |

Encryption and signing are **fail-closed**: if enabled (the default) but key material is unavailable, Vayl refuses to start rather than run unprotected.

## Server (`vayl-server`)

| Variable                              | Purpose                                          | Default              |
| ------------------------------------- | ------------------------------------------------ | -------------------- |
| `VAYL_HOST` / `VAYL_PORT`             | bind address and port                            | `127.0.0.1` / `8080` |
| `VAYL_AUTH_REQUIRED`                  | deny tools without a bound principal             | set by vayl-server   |
| `VAYL_ALLOWED_HOSTS`                  | allowed `Host` values (DNS-rebinding protection) | localhost            |
| `VAYL_TRUSTED_PROXY_HOPS`             | trusted `X-Forwarded-For` hops for client IP     | `0`                  |
| `VAYL_METRICS_TOKEN`                  | require a bearer token on `/metrics`             | unset (open)         |
| `VAYL_MAX_BODY` / `VAYL_RATE_PER_MIN` | request body cap / per-IP rate limit             | 1 MiB / 120          |

## OIDC SSO (Enterprise)

| Variable                                                         | Purpose                      |
| ---------------------------------------------------------------- | ---------------------------- |
| `VAYL_OIDC_ISSUER` / `VAYL_OIDC_AUDIENCE` / `VAYL_OIDC_JWKS_URL` | verify IdP tokens            |
| `VAYL_OIDC_ROLE_CLAIM` / `VAYL_OIDC_ROLE_MAP`                    | map a group claim to roles   |
| `VAYL_OIDC_SCOPE_CLAIM`                                          | map a claim to tenant scopes |

## Clinical / high-stakes

| Variable                   | Purpose                                                                                                           |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `VAYL_SLOT_SCHEMA`         | path to a declared-slot schema, **or** a built-in preset: `preset:clinical` / `preset:finance` / `preset:support` |
| `VAYL_CRITICAL_CATEGORIES` | categories that bypass ranking (pair with the clinical/finance presets, e.g. `critical`)                          |
| `VAYL_CRITICAL_BUDGET`     | max critical facts before a read raises (default 200)                                                             |

{% hint style="info" %}
Built-in presets give you a domain schema without authoring JSON: `VAYL_SLOT_SCHEMA=preset:clinical` (also `finance`, `support`). Pair the clinical/finance presets with `VAYL_CRITICAL_CATEGORIES=critical` so their critical slots are always surfaced.
{% endhint %}

## Graph (optional)

| Variable                                      | Purpose                           |
| --------------------------------------------- | --------------------------------- |
| `VAYL_GRAPH`                                  | enable the Neo4j projection (`1`) |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | graph connection                  |

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-book-bookmark" style="color:$primary;">:book-bookmark:</i> Glossary</h4></td><td>Definitions of the terms these variables control.</td><td></td></tr><tr><td><h4><i class="fa-wrench" style="color:$primary;">:wrench:</i> Troubleshooting</h4></td><td>When a setting doesn't take effect, start here.</td><td></td></tr><tr><td><h4><i class="fa-globe" style="color:$primary;">:globe:</i> Deploying vayl-server</h4></td><td>Where the server and security variables come together.</td><td></td></tr></tbody></table>
