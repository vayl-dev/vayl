---
description: >-
  Principals, API keys, roles, capabilities, tenant scoping, and SSO for a
  shared deployment.
icon: lock
---

# Authentication & access

How Vayl decides _who_ may do _what_, to _whose_ memory.

## Two modes

* **Local (`vayl-mcp`, stdio).** A single developer on their own machine. The caller runs as a trusted local admin — no keys to manage.
* **Team (`vayl-server`, HTTP).** Every request must present a credential; the server resolves it to a principal and applies role-based access control. An unauthenticated request is rejected with `401`.

## Principals and API keys

A **principal** is a user or agent identity. Each has an API key of the form `vayl_sk_…`.

* Keys are **high-entropy random tokens** (256-bit).
* Only the **SHA-256 hash** is stored — the key itself is shown **once**, at creation, and can't be recovered. To rotate, create a new principal and revoke the old one.

```python
principal, key = create_principal("support-bot", role="agent", scopes="cust_5521,cust_7788")
# copy `key` now — it is shown only once
```

## Roles and capabilities

Each role grants a set of **capabilities**. Every tool declares the capability it requires and is **fail-closed** — if the caller's role doesn't grant it, the call is denied and recorded.

| Role        | read | write | delete | verify | admin |
| ----------- | :--: | :---: | :----: | :----: | :---: |
| **admin**   |   ✓  |   ✓   |    ✓   |    ✓   |   ✓   |
| **member**  |   ✓  |   ✓   |    ✓   |    ✓   |       |
| **agent**   |   ✓  |   ✓   |        |    ✓   |       |
| **viewer**  |   ✓  |       |        |    ✓   |       |
| **auditor** |      |       |        |    ✓   |       |

* **read** — recall, list, history, export
* **write** — remember, forget, update, decisions, attest
* **delete** — single-subject erasure (a data-subject right)
* **verify** — audit, verify, explain, stats, health
* **admin** — wipe-all, retention, policy, and managing principals

## Scopes — the multi-tenant boundary

Capabilities answer _"may this caller read at all?"_. **Scopes** answer _"whose memory?"_. Because `user_id` comes from the caller, a key with no scopes can reach **any** space. Assign `scopes` (a list of `user_id`s) to confine a principal:

```python
create_principal("bot", role="agent", scopes="cust_5521")   # may only touch cust_5521
```

* An **admin** is unrestricted by design (org control implies reaching every space, including to erase it).
* A scoped caller reaching outside its scope is denied, and the denial is recorded to the audit log. The error deliberately **does not echo** the requested `user_id`, so a caller can't enumerate spaces from error text.

{% hint style="danger" %}
A principal created **without** scopes is unrestricted. That is correct for single-tenant, but wrong the moment one deployment holds several customers' data. Always scope non-admin keys in a multi-tenant deployment.
{% endhint %}

## SSO (Enterprise)

With a license, `vayl-server` also accepts **OIDC ID tokens** (JWT) as an alternative to API keys, so people sign in with your existing identity provider. Tokens are verified against the IdP's JWKS with `iss` / `aud` / `exp` enforced and the algorithm pinned to RS256. Claims map to roles (`VAYL_OIDC_ROLE_MAP`), and an optional claim maps to scopes (`VAYL_OIDC_SCOPE_CLAIM`) so SSO users are tenant-confined just like keys. API keys keep working alongside SSO.

## Next

* [Deploying vayl-server](../guides/deploying-vayl-server.md) — stand up the authenticated server.
* [Administration](/broken/pages/YC6VB918DmCHhGZz7ShQ) — the tools for managing principals.
