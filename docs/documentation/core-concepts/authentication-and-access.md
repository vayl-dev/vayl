---
description: >-
  Principals, API keys, roles and capabilities, scopes, tenants, seat limits,
  and OIDC single sign-on for a shared Vayl deployment.
icon: lock
---

# Authentication and access

Vayl decides every tool call in three steps: who the caller is (a principal), what it may do (its role's capabilities), and whose memory it may touch (its tenant and scopes). All three are checked in `_guard` before any tool runs. Checks fail closed, and denials are written to the audit log.

## Two modes

| Mode | Transport | Who is the caller |
| --- | --- | --- |
| **Local** | `vayl-mcp` over stdio | A trusted local admin, with no keys. Set `VAYL_AUTH_REQUIRED=on` to make stdio deny unauthenticated calls instead. |
| **Team** | `vayl-server` over HTTP | Whoever the bearer credential resolves to. Authentication is always required: no valid credential means `401`. |

`vayl-server` accepts `Authorization: Bearer vayl_sk_…` (an API key) or, when SSO is enabled, an OIDC ID token.

## Principals and API keys

A **principal** is an identity: a person, an agent or a service. Each has one role, an optional list of scopes, a tenant, and one API key.

* Keys look like `vayl_sk_` followed by 32 random bytes, URL-safe base64 (256 bits).
* Only the key's SHA-256 hash is stored. The key is shown once, at creation, and can't be recovered. To rotate, create a new principal and revoke the old one.
* Principal names are encrypted at rest.

Create a principal with the `create_principal` tool (requires `admin`):

```
create_principal(name, role="member", kind="agent", scopes="", tenant="")
```

| Argument | Values |
| --- | --- |
| `name` | Display name. Also the identity a [trusted source](#trusted-sources) is matched against. |
| `role` | `admin`, `member`, `agent`, `viewer`, `auditor` |
| `kind` | Free-text label, conventionally `human`, `agent` or `service` |
| `scopes` | Comma-separated `user_id`s the key may touch. Empty means unrestricted. |
| `tenant` | The organization partition. Empty means the caller's own tenant; only the [deployment operator](#the-deployment-operator) may name another. See [Memory spaces and tenants](memory-spaces.md#tenants). |

It returns a string, not a tuple:

```
Created principal prin_4a64581ad3dc 'support-bot' (role: agent, kind: agent, tenant: default).
  API key (shown once — save it now):
  vayl_sk_••••••••••••••••••••••••••••••••••••••••••••
```

On a fresh server there are no principals yet. Bootstrap the first admin over stdio, where you run as local admin. The steps are in [Deploying vayl-server](../guides/deploying-vayl-server.md).

### Seat limit

Each active (not revoked) principal counts as a seat, in every tenant. The Community edition allows 3. A licensed edition allows the number of seats in its license. Past the limit, `create_principal` returns:

```
Seat limit reached: 3 active principal(s) allowed on the community edition. Revoke an unused principal, or install a license with more seats (see mint_license.py / VAYL_LICENSE).
```

`license_status` shows the edition, and to the [deployment operator](#the-deployment-operator) also `principals in use: N / cap`. Seats are counted across every tenant. SSO users are not stored as principals and don't take seats.

### Revoking and erasing

```
revoke_principal(principal_id, erase=False)
```

* `erase=False` disables the principal. Its key stops working immediately, and the record is kept, marked disabled. Returns `Revoked <id> — its API key no longer works.`
* `erase=True` hard-deletes the principal record, for team-member erasure requests. Returns `Erased <id> — key revoked and the principal record hard-deleted.`

Both are irreversible and both are audited. `list_principals` shows the tenant's principals (every tenant's, for the deployment operator), disabled ones included, and never shows keys.

## Roles and capabilities

A role grants a fixed set of capabilities (`ROLE_CAPS` in `auth/auth.py`):

| Role | read | write | delete | verify | approve | admin |
| --- | :-: | :-: | :-: | :-: | :-: | :-: |
| `admin` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `member` | ✓ | ✓ | ✓ | ✓ | ✓ | |
| `agent` | ✓ | ✓ | | ✓ | | |
| `viewer` | ✓ | | | ✓ | | |
| `auditor` | | | | ✓ | | |

Every tool requires exactly one capability:

| Capability | Tools |
| --- | --- |
| `read` | `recall`, `recall_related`, `get_memory`, `history`, `list_memories`, `export_memory`, `check_before_act`, `safe_recall`, `pending_changes`, `get_reconcile_policy` |
| `write` | `remember`, `forget`, `update_memory`, `record_decision`, `attest` |
| `delete` | `delete` |
| `verify` | `verify_audit`, `verify_receipt`, `export_public_key`, `explain_decision`, `audit_log`, `stats`, `health`, `license_status` |
| `approve` | `confirm_change`, `reject_change` |
| `admin` | `delete_all`, `purge_expired`, `set_reconcile_policy`, `create_principal`, `list_principals`, `revoke_principal` |

A few tools add a check of their own on top:

* `audit_log` with no `user_id` requires `admin` and returns the caller's tenant's log; only the deployment operator's view spans every tenant. With a `user_id` it is scope-checked like any memory tool.
* `verify_receipt` on a receipt with no `user_id` requires `admin`. A scoped receipt must be inside the caller's scope.
* `remember` with a trusted `source` needs a matching key or `approve`. See [Trusted sources](#trusted-sources).
* `create_principal` into another tenant, `stats`, seat usage in `license_status`, and `purge_expired(include_audit=True)` are for the deployment operator. See [The deployment operator](#the-deployment-operator).

A missing capability returns, for example:

```
Access denied: 'confirm_change' requires the 'approve' capability; your role(s) ['agent'] do not grant it.
```

### The deployment operator

On a deployment with several tenants, most admin work stays inside the admin's own tenant. The **deployment operator** is an admin of the `default` tenant. The local stdio admin is one, and so is an SSO user mapped to `admin`, because SSO principals are always in `default`. Only the operator reaches across tenants:

| Tool | Deployment operator | Admin of another tenant |
| --- | --- | --- |
| `create_principal` | Any tenant (default: `default`) | Own tenant only |
| `list_principals` | Every tenant's principals, tagged `tenant=<name>` outside `default` | Own tenant's |
| `revoke_principal` | Any principal | Own tenant's; another tenant's id reads as unknown |
| `license_status` | Includes `principals in use: N / cap` | No seat usage |
| `stats` | Allowed | Denied, as for every caller outside `default` |
| `audit_log` with no `user_id` | Every tenant, tagged `tenant=<name>` outside `default` | Own tenant's entries |
| `purge_expired(include_audit=True)` | Allowed | Denied |

Decisions and receipts are confined to the caller's tenant for everyone, the operator included: `explain_decision` and `verify_receipt` only find rows recorded in the caller's tenant. The denials read:

```
Access denied: you can only create principals in your own tenant.
Access denied: stats are deployment-wide; ask the deployment operator.
Access denied: the audit log is shared by every tenant, so only the deployment operator may purge it (include_audit).
```

On a single-tenant deployment every principal is in `default`, so every admin is the operator and nothing changes.

### The approve capability

`approve` is what a person signs off with. `admin` and `member` have it; `agent`, `viewer` and `auditor` don't, so an agent can't approve or discard its own proposals. `confirm_change` and `reject_change` record the approver as the authenticated caller, `name [principal_id]`. Their optional `decided_by` argument is only a note stored beside that identity. See [Safety gates and human approval](../guides/safety-gates-and-human-approval.md).

### Trusted sources

`VAYL_TRUSTED_SOURCES` (for example `fhir,hl7`) names sources whose changes skip the confirmation gate. A caller could otherwise claim to be one just by passing `source`, so `remember` with a trusted `source` is allowed only when:

* the calling principal's `name` equals that source (an integration key named `fhir`), or
* the caller has the `approve` capability.

Anyone else gets this response, and the attempt is audited:

```
Access denied: 'fhir' is a trusted source (VAYL_TRUSTED_SOURCES), so its changes skip the confirmation gate. Only a key named 'fhir', or one with the 'approve' capability, may write as it.
```

## Scopes and tenants

Capabilities decide what a caller may do. Scopes and tenants decide whose memory it may do it to.

* **Tenant** is a hard partition taken from the principal. Every memory query filters by it.
* **Scopes** limit the `user_id` values a key may pass. They don't restrict `agent_id` or `run_id`. `admin` ignores scopes.

```
create_principal("support-bot", role="agent", scopes="cust_5521,cust_7788", tenant="acme")
```

An out-of-scope call returns `Access denied: '<tool>' targets a memory space outside your assigned scope.` without echoing the requested `user_id`. Details are in [Memory spaces and tenants](memory-spaces.md).

{% hint style="danger" %}
A principal created without scopes can reach every `user_id` in its tenant. In any deployment that holds more than one customer's data, scope every non-admin key or give each customer its own tenant.
{% endhint %}

## SSO with OIDC

`vayl-server` can accept OIDC ID tokens (JWTs) from your identity provider, so people sign in without a Vayl API key. API keys keep working alongside SSO.

**Requirements:**

* A license that grants the `sso` feature. Without it, the server prints a warning at startup and ignores OIDC tokens.
* The optional dependency: `pip install "vayl-mcp[sso]"` (PyJWT).
* All three of `VAYL_OIDC_ISSUER`, `VAYL_OIDC_AUDIENCE` and `VAYL_OIDC_JWKS_URL`.

**Verification:** the token's signature is checked against the IdP's JWKS, fetched by key id so key rotation works. The algorithm is pinned to RS256, and `exp`, `iss` and `aud` are required and enforced. The token needs a `sub` or `email` claim. Any failure is a `401`.

**Mapping claims to a principal:**

| Variable | Default | Effect |
| --- | --- | --- |
| `VAYL_OIDC_ROLE_CLAIM` | `groups` | Claim holding the user's groups (a list or a single string) |
| `VAYL_OIDC_ROLE_MAP` | `{}` | JSON `{"<idp group>": "<vayl role>"}`, for example `{"vayl-admins": "admin", "eng": "member"}`. Anything other than a JSON object stops `vayl-server` at startup. |
| `VAYL_OIDC_DEFAULT_ROLE` | `viewer` | Role for a user with no mapped group |
| `VAYL_OIDC_SCOPE_CLAIM` | unset | Claim holding the `user_id`s the user may touch (a list or CSV). Unset means unrestricted. |

An SSO user becomes principal `sso:<sub>`, named by the `email` (or `name`) claim, with `kind` `human`. SSO principals are always in the `default` tenant, and there is no claim to set another one. In a multi-tenant deployment, use API keys for tenants other than `default`, and set `VAYL_OIDC_SCOPE_CLAIM` so SSO users are confined.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-server" style="color:$primary;">:server:</i> Deploy vayl-server</h4></td><td>Stand up the authenticated server and bootstrap an admin.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr><tr><td><h4><i class="fa-user-gear" style="color:$primary;">:user-gear:</i> Administration tools</h4></td><td>create_principal, revoke_principal, license_status and more.</td><td><a href="../mcp-tools/administration.md">administration.md</a></td></tr><tr><td><h4><i class="fa-sitemap" style="color:$primary;">:sitemap:</i> Memory spaces and tenants</h4></td><td>How scopes and tenants partition memory.</td><td><a href="memory-spaces.md">memory-spaces.md</a></td></tr></tbody></table>
