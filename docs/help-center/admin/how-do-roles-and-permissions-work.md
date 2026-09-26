---
description: Roles grant capabilities; scopes and tenants decide whose memory a key can reach.
icon: user-shield
---

# How do roles and permissions work?

Every key belongs to a principal with a role. The role grants capabilities, and every tool checks its capability before running. Denials are audited.

| Role | read | write | delete | verify | approve | admin |
| --- | :-: | :-: | :-: | :-: | :-: | :-: |
| `admin` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `member` | ✓ | ✓ | ✓ | ✓ | ✓ | |
| `agent` | ✓ | ✓ | | ✓ | | |
| `viewer` | ✓ | | | ✓ | | |
| `auditor` | | | | ✓ | | |

* **`approve`** confirms or rejects a change on an approval-gated slot. Agents don't have it, so an agent can't approve its own proposal.
* **Scopes** limit which `user_id` values a key can reach. An empty scope list means all of them; admins are never scoped.
* **Tenants** are hard partitions for a shared deployment. Every query is filtered by the key's tenant. Only the **deployment operator**, an admin of the `default` tenant, acts across tenants.
* **Local stdio** (`vayl-mcp`) runs as the local admin unless `VAYL_AUTH_REQUIRED=on`.

Full reference, including which tool needs which capability: [Authentication and access](https://vayl.gitbook.io/vayl-docs/documentation/core-concepts/authentication-and-access).
