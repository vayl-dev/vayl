---
description: Create, scope, and revoke principals.
icon: rectangle-terminal
---

# Managing API keys

A **principal** is a user or agent with an API key (`vayl_sk_…`). Only the key's hash is stored, and the key is shown once.

* **Create** — `create_principal(name, role, scopes)` returns the key (copy it now).
* **Scope** — pass `scopes="cust_1,cust_2"` to confine a key to those memory spaces.
* **List** — `list_principals()` shows principals and roles, never keys.
* **Revoke** — `revoke_principal(id)` disables a key immediately; `erase=True` hard-deletes the record.

Bootstrap the first admin over local stdio (`vayl-mcp` → `create_principal("you", role="admin")`), then issue keys to your team.
