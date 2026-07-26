# Deploying Vayl (self-hosted)

A step-by-step runbook to stand up Vayl in your own environment. Aimed at an ops/platform engineer;
follow it top to bottom. End state: an authenticated Vayl memory server your agents connect to over
HTTPS, running entirely on your infrastructure.

> Vayl is self-hosted: your data (and, with Vault, your keys) never leave your environment. The one
> external dependency is the **LLM** — and even that can be a model you host (Ollama / vLLM / an
> EU-hosted endpoint), so there's no egress at all if you want none.

---

## 0. Prerequisites

- **Docker** + **Docker Compose** on a Linux host (2 vCPU / 4 GB RAM is enough if you use a *hosted*
  LLM; running a local model needs much more — see the LLM note below).
- **An LLM endpoint** — either your own OpenAI-compatible API (OpenAI, Azure OpenAI, an EU-hosted
  provider, vLLM), or Ollama running on the host. Vayl needs a chat model **and** an embedding model.
- A **DNS name + TLS cert** for production (any reverse proxy works; a Caddy example is below).
- (Optional) Postgres, HashiCorp Vault, an OIDC IdP, an enterprise license — all covered as options.

---

## 1. Get the files

**If your vendor gave you an image** (the usual case): put
`docker-compose.client.yml`, `.env.example`, and this file in a directory, then log in to the registry
with the pull credentials you were given:

```bash
docker login ghcr.io          # (or your vendor's registry) with the credentials provided
cp .env.example .env
```
Throughout this runbook, use `docker compose -f docker-compose.client.yml …` for compose commands.

**If you're building from source** (vendor/internal): clone the repo and use plain `docker compose`:

```bash
git clone <vayl-repo> vayl && cd vayl
cp .env.example .env
```

## 2. Configure — edit `.env`

Open `.env`. **The LLM block is the only required part.** Pick one:

**Path A — your existing LLM (recommended for production):**
```env
LLM_PROVIDER=openai
OPENAI_BASE_URL=https://your-endpoint/v1     # OpenAI, Azure OpenAI, EU-hosted, vLLM…
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5-mini                       # 0% silently-wrong on messy data; gpt-5-nano is cheaper but weaker on corrections
EMBED_BASE_URL=https://your-endpoint/v1
EMBED_API_KEY=sk-...
EMBED_MODEL=text-embedding-3-small
```

**Path B — Ollama on the host (no egress; needs RAM/GPU; weaker on small models):**
```bash
# on the Docker host:
ollama serve &                 # if not already running
ollama pull qwen2.5:3b         # chat model     ollama pull nomic-embed-text   # embeddings
```
```env
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=qwen2.5:3b
EMBED_BASE_URL=http://host.docker.internal:11434/v1
EMBED_API_KEY=ollama
EMBED_MODEL=nomic-embed-text
```
> **LLM quality matters.** Vayl's reconciliation is only as good as the model. A 3B local model is fine
> for a demo but weak on hard cases; use a strong hosted model (or a larger local one) for production.

Encryption (`VAYL_ENCRYPT=on`) and auth (`VAYL_AUTH_REQUIRED=1`) are on by default — leave them.

## 3. Start

```bash
docker compose up -d --build
docker compose logs -f vayl        # watch for "Uvicorn running" / "Application startup complete"
```

## 4. Bootstrap the first admin (one-off)

The server requires authenticated principals. Mint the first admin and **copy the key — it's shown once**:

```bash
docker compose run --rm vayl python -c "import mcp_server as s; print(s.create_principal('admin', role='admin'))"
```

Use that admin key to create the day-to-day principals your agents/users will use (roles:
`admin` / `member` / `agent` / `viewer` / `auditor`) — via the `create_principal` tool, or repeat the
command above with a different name/role.

## 5. Verify

```bash
curl -s localhost:8080/healthz     # {"status":"ok"}
curl -s localhost:8080/readyz      # {"status":"ready"}   (DB reachable)
curl -s localhost:8080/metrics     # Prometheus metrics

# a real call requires the admin key and the MCP protocol; quickest smoke — auth is enforced:
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8080/mcp        # → 401 (no key = rejected)
```
`health` (via the tool) also checks the LLM + embedder are reachable — run it once you've connected a
client (below). If it reports `llm: FAIL`, your LLM env in `.env` is wrong.

## 6. Put TLS in front (production)

Do **not** expose `:8080` directly. Terminate TLS at a reverse proxy. Simplest — add Caddy to the stack:

`Caddyfile`:
```
vayl.your-domain.com {
    reverse_proxy vayl:8080
}
```
Add to `docker-compose.yml`:
```yaml
  caddy:
    image: caddy:2
    ports: ["443:443", "80:80"]
    volumes: ["./Caddyfile:/etc/caddy/Caddyfile", "caddy-data:/data"]
    depends_on: [vayl]
# and add `caddy-data:` under top-level volumes
```
(nginx/Traefik/your ingress work the same — proxy `https://vayl.your-domain.com` → `vayl:8080`.)

## 7. Connect an agent / MCP client

Vayl speaks **MCP over streamable-HTTP** at `/mcp`, Bearer-authenticated. Point any MCP client
(Claude Desktop, Cursor, your agent framework) at it:

```
URL:    https://vayl.your-domain.com/mcp
Header: Authorization: Bearer vayl_sk_<the principal's key>
```

The client then has the memory tools (`remember`, `recall`, `check_before_act`, …) gated by that
principal's role.

---

## Options (skip any you don't need)

**Postgres (scale-out / multiple server processes):**
```bash
# in .env:  VAYL_DATABASE_URL=postgresql://vayl:vayl@postgres:5432/vayl   (change the password!)
docker compose --profile postgres up -d
```
Same-space writes serialize across processes via an advisory lock; different spaces run in parallel.

**HashiCorp Vault (key custody off the host):**
```bash
# one-time in Vault:  vault secrets enable transit && vault write -f transit/keys/vayl
# in .env:
VAYL_KMS=vault
VAULT_ADDR=https://vault.internal:8200
VAULT_TOKEN=<a token with transit encrypt/decrypt on the vayl key>
```
The master key never leaves Vault; only a wrapped blob sits on the host. If Vault is unreachable, Vayl
**fails closed** (won't start) rather than run unencrypted.

**Enterprise license (raise seat cap / unlock features):** set `VAYL_VENDOR_PUBKEY` + `VAYL_LICENSE`
in `.env` (from your vendor). Check with the `license_status` tool. Omit → Community edition.

**SSO / OIDC (human login via your IdP; requires a license):** set `VAYL_OIDC_ISSUER`,
`VAYL_OIDC_AUDIENCE`, `VAYL_OIDC_JWKS_URL`. Users then present an IdP JWT as the Bearer token; API keys
still work alongside.

**Graph recall (Neo4j):** `docker compose --profile graph up -d`, set `VAYL_GRAPH=1` + `NEO4J_*`.

---

## Operations

- **Backups:** back up the `vayl-data` volume (SQLite DB + keys) — or your Postgres, if used. The
  `<db>.key*` files are the *only* way to decrypt at-rest data; back them up securely (or use Vault,
  where the master key is in Vault and only a wrapped blob is on the host).
- **Upgrades:** `git pull && docker compose up -d --build`. Schema migrations are automatic and
  backward-compatible.
- **Logs & metrics:** `docker compose logs -f vayl`; scrape `/metrics` (Prometheus) from your monitoring.
- **Rotation:** rotate principal keys with `revoke_principal` + `create_principal`. For the master key,
  rotate in Vault (`VAYL_KMS=vault`).

## Security checklist (before go-live)

- [ ] TLS terminating in front; `:8080` not publicly exposed.
- [ ] `VAYL_ENCRYPT=on` and `VAYL_AUTH_REQUIRED=1` (defaults — confirm they weren't overridden).
- [ ] Admin key stored in your secrets manager; least-privilege roles for agents (`agent`/`member`, not `admin`).
- [ ] For regulated/sensitive data: `VAYL_KMS=vault`, and read `COMPLIANCE.md` (GDPR / AI-Act mapping) + `SECURITY.md`.
- [ ] Changed default Postgres/Neo4j passwords if you enabled those profiles.
- [ ] Backups of the data volume (and keys) tested.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `remember`/`recall` error, or `health` shows `llm: FAIL` | LLM env wrong in `.env`. For Ollama-on-host, `OPENAI_BASE_URL=http://host.docker.internal:11434/v1` and the model is pulled. |
| Every call returns **401** | No/invalid `Authorization: Bearer vayl_sk_…`. Bootstrap an admin (step 4) and use its key. |
| Server won't start with `VAYL_KMS=vault` | Vault unreachable or the transit key missing — this is **fail-closed** by design. Fix `VAULT_ADDR`/`VAULT_TOKEN` / create the transit key. |
| `readyz` → 503 | Database not reachable (Postgres down, or bad `VAYL_DATABASE_URL`). |
| "Seat limit reached" on `create_principal` | Community cap hit — install an Enterprise license, or revoke an unused principal. |

Questions during onboarding? That's expected for a first deploy — reach your Vayl contact.
