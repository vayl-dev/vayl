---
description: Issue and revoke API keys, check the license, and read usage stats and health.
icon: user-gear
---

# Administration

These six tools run a deployment: `create_principal`, `list_principals` and `revoke_principal` manage who holds a key and need `admin`; `license_status`, `stats` and `health` are operational reads that every role can call (`verify`). None of them take memory-space arguments.

Outputs below were captured by running the tools offline.

| Tool | Capability | Annotations |
| --- | --- | --- |
| `create_principal` | `admin` | write, not destructive |
| `list_principals` | `admin` | read-only |
| `revoke_principal` | `admin` | destructive |
| `license_status` | `verify` | read-only |
| `stats` | `verify` | read-only |
| `health` | `verify` | read-only, open-world (calls the LLM and embedder) |

## create\_principal

```python
create_principal(name: str, role: str = "member", kind: str = "agent",
                 scopes: str = "", tenant: str = "default") -> str
```

Creates a principal (a person, agent, or service) and issues its API key. The key is shown **once**; only its hash is stored, so a lost key can't be recovered. Create a new principal instead.

| Argument | Description |
| --- | --- |
| `name` | Display name, encrypted at rest (it is personal data). A key named after a trusted source (for example `fhir`) may write with `source="fhir"`. |
| `role` | `admin`, `member`, `agent`, `viewer`, or `auditor`. See [Authentication & access](../core-concepts/authentication-and-access.md) for what each grants. |
| `kind` | Free-text label: `human`, `agent`, or `service`. |
| `scopes` | Comma-separated `user_id`s the key may touch, e.g. `"cust_5521,cust_7788"`. Empty means unrestricted. Set it in any multi-user deployment, otherwise a valid key can read any space by passing its `user_id`. Admin keys are always unrestricted. |
| `tenant` | The org partition the key belongs to. A hard boundary: every store query filters by it, so a key cannot reach another tenant's memory even under the same `user_id`. |

```json
{"name": "create_principal", "arguments": {"name": "support-bot", "role": "agent", "scopes": "cust_5521,cust_7788", "tenant": "acme"}}
```

```
Created principal prin_e9e67145bafa 'support-bot' (role: agent, kind: agent, tenant: acme).
  API key (shown once — save it now):
  vayl_sk_…
```

An unknown role returns `Unknown role 'owner'. Use: admin, member, agent, viewer, auditor.`

**Seat cap.** The number of active principals is capped by the license: 3 on Community. At the cap:

```
Seat limit reached: 3 active principal(s) allowed on the community edition. Revoke an unused principal, or install a license with more seats (see mint_license.py / VAYL_LICENSE).
```

**Bootstrapping the first admin.** A fresh database has no principals, and `vayl-server` requires a key. Create the first admin locally against the same database, where the caller is the local admin:

```bash
VAYL_DB=/data/vayl.db python -c "from vayl.api import mcp_server as s; print(s.create_principal('ops', role='admin'))"
```

With Docker Compose, see [Deploying vayl-server](../guides/deploying-vayl-server.md).

## list\_principals

```python
list_principals() -> str
```

Lists every principal: id, name, roles, kind, and whether it is disabled. Never shows keys.

```
• prin_526651e03f93  ops  [admin]  agent
• prin_e9e67145bafa  support-bot  [agent]  agent
```

A revoked principal is shown with `✗` and `(disabled)`. With none: `No principals yet. Create one with create_principal.`

## revoke\_principal

```python
revoke_principal(principal_id: str, erase: bool = False) -> str
```

Disables a principal; its key stops working on the next request. There is no un-revoke: create a new principal to restore access. With `erase=True` the principal row is hard-deleted instead of kept as disabled (erasure for a team member).

```
Revoked prin_e9e67145bafa — its API key no longer works.
```

```
Erased prin_e9e67145bafa — key revoked and the principal record hard-deleted.
```

Unknown or already revoked: `No active principal prin_e9e67145bafa (unknown or already revoked).` With `erase=True` and an unknown id: `No principal <id>.`

## license\_status

```python
license_status() -> str
```

Shows the edition, seats used against the cap, expiry, and unlocked features. A rejected license (tampered, expired, wrong key) is reported, along with the Community fallback in use.

```
Community edition — up to 3 principals. No license installed.
  principals in use: 1 / 3
```

## stats

```python
stats() -> str
```

Per-tool call counts, average latency and error counts, plus the distribution of reconciliation actions. Computed from the local metrics tables; nothing is sent anywhere. Excerpt:

```
Tools  (calls · avg ms · errors):
  recall: 2 · 0.4 ms · 0 err
  remember: 17 · 1.0 ms · 0 err
  verify_audit: 1 · 8.2 ms · 0 err

Reconciliation actions  (20 total):
  ADD: 9 (45%)
  FLAG: 4 (20%)
  RETRACT: 2 (10%)
  SUPERSEDE: 1 (5%)
  DEDUP: 1 (5%)
  ARCHIVE: 1 (5%)
  COEXIST: 1 (5%)
  SKIP: 1 (5%)
```

These latencies come from an offline run with the model stubbed; real `remember` and `recall` calls include model time. With no activity: `No activity recorded yet.`

When there are recent errors, a `Recent errors (most recent first):` block follows. Only admins see the error text, because it can contain memory content and metrics span every tenant; other roles see the tool and the error type, plus `(error details are visible to admins only)`.

## health

```python
health() -> str
```

Checks configuration and that each dependency is reachable: database, embedder, LLM, and the graph when enabled. Each dependency gets one attempt with no retries, so an unreachable endpoint is reported immediately.

{% hint style="warning" %}
`health` makes one real embedding call and one real LLM extraction call, so each run costs a few tokens.
{% endhint %}

```
config: LLM_PROVIDER=(unset), model=(default)
license: community
encryption: OFF (VAYL_ENCRYPT=off) · signing: on
db: ok
embedder: ok
llm: ok
graph: disabled
```

A failing dependency shows its exception type only, for example `llm: FAIL (URLError)`; the detail is in the server log. A rejected license shows `license: community (rejected: <reason>)`.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication &#x26; access</h4></td><td>Roles, capabilities, scopes, and tenants these principals carry.</td><td><a href="../core-concepts/authentication-and-access.md">authentication-and-access.md</a></td></tr><tr><td><h4><i class="fa-globe" style="color:$primary;">:globe:</i> Deploying vayl-server</h4></td><td>Stand up the authenticated team server, with Docker and Postgres.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr><tr><td><h4><i class="fa-terminal" style="color:$primary;">:terminal:</i> CLI reference</h4></td><td><code>vayl-license</code>, <code>vayl-migrate</code>, and the server commands.</td><td><a href="../reference/cli.md">cli.md</a></td></tr></tbody></table>
