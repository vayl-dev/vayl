---
description: >-
  How Vayl partitions memory by tenant, user, agent, and run, and how to keep
  each caller inside the spaces it may touch.
icon: sitemap
---

# Memory spaces and tenants

Every fact belongs to exactly one memory space, identified by four keys: `tenant`, `user_id`, `agent_id` and `run_id`. Reconciliation, recall, history, flags and the same-slot invariant all run inside one space, so the same subject in two spaces is two unrelated slots.

## The four partition keys

| Key | Set by | Default | Answers |
| --- | --- | --- | --- |
| `tenant` | The server, from the authenticated principal. Callers can't pass it. | `"default"` | Which organization does this belong to? |
| `user_id` | The caller, on each tool call | `"default"` | Whose memory is this: a customer, end user, patient, or project |
| `agent_id` | The caller, on each tool call | `""` | Which agent owns it, when several agents share a user |
| `run_id` | The caller, on each tool call | `""` | Which session or job, for scratch memory |

Every store query filters on all four. With no arguments, a call reaches the space `(default, "default", "", "")`, which is fine for a single-user setup.

```python
remember("Customer is on the Free plan", user_id="cust_5521")
recall("what plan are they on?", user_id="cust_5521")      # answer is model-written, e.g. "Free."

# a planner and a coder keep separate memory for the same user
remember("Prefers dark mode", user_id="u1", agent_id="planner")
remember("Uses tabs, not spaces", user_id="u1", agent_id="coder")
```

The memory tools (`remember`, `recall`, `forget`, `history`, `list_memories`, `export_memory`, the gating tools and the rest) take `user_id`, `agent_id` and `run_id`. Principal, license and audit-verification tools don't. `explain_decision` and `audit_log` take only `user_id`. See [Memory tools](../mcp-tools/memory.md) for each signature.

## Tenants

A tenant is a hard partition for a shared deployment that serves several organizations. It isn't a tool argument: the tenant comes from the principal whose key made the request.

```python
create_principal("acme-bot", role="agent", tenant="acme")
create_principal("globex-bot", role="agent", tenant="globex")
```

Naming a tenant takes the **deployment operator**: an admin of the `default` tenant, such as the local stdio admin. `tenant` defaults to the caller's own, so an admin of `acme` creates `acme` principals and gets `Access denied: you can only create principals in your own tenant.` for any other.

A key for `acme` can't read `globex` memory, even when both pass `user_id="u1"`. Every memory row is stamped with its tenant and every memory query filters on it.

* **stdio (`vayl-mcp`)** has no principal bound, so it always uses the `default` tenant.
* **SSO principals** are always in the `default` tenant. Confine them with `VAYL_OIDC_SCOPE_CLAIM` instead. See [Authentication and access](authentication-and-access.md#sso-with-oidc).
* **Accountability records** are stamped with the tenant too: `explain_decision`, `verify_receipt` and `audit_log` only see the caller's tenant, and `purge_expired`'s `include_decisions`/`include_receipts` purge only the caller's tenant.
* **Admin tools** stay inside the admin's tenant: `list_principals` and `revoke_principal` reach only its own principals. The deployment operator manages every tenant. `stats` is deployment-wide, so it's denied outside `default`, and `include_audit` purges the one audit chain every tenant shares, so only the operator may use it. See [The deployment operator](authentication-and-access.md#the-deployment-operator).

Reconcile policies and graph edges are partitioned by tenant too: the policy table is keyed by `(tenant, user_id, agent_id, run_id)`, and every Neo4j edge's namespace starts with the tenant.

{% hint style="warning" %}
**Upgrading from 0.6 or earlier?** Before 0.7, policies and graph edges were not keyed by tenant, so tenants sharing a `user_id` could overwrite each other's policy and share `recall_related` edges. Decisions, receipts and audit entries had no tenant either, and a tenant admin could manage other tenants' principals. Schema migration v2 fixes the policy table on first start (back up first; it isn't additive), and v3 adds a tenant to decisions, receipts and audit entries. Rows written before v3 are stamped `default`, so in a multi-tenant deployment they're visible only to the `default` tenant. With the graph enabled, also run [`vayl-migrate reproject-graph`](../reference/cli.md#vayl-migrate-reproject-graph) once to rebuild edges under the new namespace.
{% endhint %}

## What isolation guarantees

* A fact in one space never reconciles against another space. Superseding `plan` for `cust_5521` leaves `cust_7788` alone.
* A recall in one space never sees another space's facts, flags or history.
* Reconcile policies (`set_reconcile_policy`) are set per space.
* `delete_all(user_id=…)` with no `agent_id`/`run_id` erases every space under that `user_id`. Pass them to erase a single space.

## Scopes: which `user_id`s a key may touch

`user_id`, `agent_id` and `run_id` come from the caller, so on a shared server a key could otherwise pass any `user_id`. A principal's `scopes` limit which `user_id` values it may use:

```python
create_principal("support-bot", role="agent", scopes="cust_5521,cust_7788")
```

* Scopes restrict **`user_id` only**. A scoped key can use any `agent_id` and `run_id` under a `user_id` it is allowed.
* An empty scope list means unrestricted. `"*"` on its own also means unrestricted. Mixed with other values, it is dropped.
* The `admin` role ignores scopes.
* An out-of-scope call returns `Access denied: '<tool>' targets a memory space outside your assigned scope.` The error doesn't echo the requested `user_id`, and the denial goes to the audit log.

{% hint style="danger" %}
A principal created without scopes can reach every `user_id` in its tenant. On a shared server with several customers, always scope non-admin keys, or give each customer its own tenant.
{% endhint %}

## Choosing a strategy

| Pattern | Keys | When |
| --- | --- | --- |
| Personal assistant or single project | all defaults | One user, one memory |
| Multi-user product | `user_id` per end user, scoped keys | Isolate each user's state |
| Multi-agent system | `user_id` + `agent_id` | Agents that shouldn't share beliefs |
| Task scratch memory | + `run_id` | Memory for one session or job |
| Several organizations on one server | `tenant` per org, via `create_principal` | Hard separation between customers |
| Shared organizational memory | one `user_id` (e.g. `org:acme`) + `source` on every write | Many agents and people writing one record |

{% hint style="info" %}
Nothing expires on its own, including `run_id` spaces. To remove old data, an admin runs `purge_expired(older_than_days, user_id=…, run_id=…)`, or `delete_all` for a space. See [Compliance (GDPR)](../mcp-tools/compliance-gdpr.md).
{% endhint %}

## Shared spaces and sources

A shared space is an ordinary space that several contributors write to. Pass `source` on each `remember` so every fact records who asserted it. Then an admin picks how conflicting sources resolve:

```python
set_reconcile_policy("AUTHORITY", {"crm": 2, "support-bot": 1}, user_id="org:acme")
remember("Acme is on the Enterprise plan", user_id="org:acme", source="crm")
```

`RECENCY` (the default) lets the newer write win, `AUTHORITY` lets the higher-ranked source win and flags the rest, and `REVIEW` flags every cross-source conflict. See [Reconciliation](core-concepts.md#source-aware-reconciliation).

## Metadata

`remember` accepts `metadata`, a dict that is merged onto every new fact stored by that call. It sits alongside the engine's own keys, such as `kind` for events and `category` from a declared slot. Metadata travels with the fact and appears in `export_memory`:

```python
remember("Patient is allergic to penicillin",
         user_id="patient_42", metadata={"category": "allergy"})
```

```json
{"id": 1, "subject": "allergy", "value": "Penicillin", "scope": "global",
 "status": "ACTIVE", "supersedes": null, "confidence": 0.95,
 "metadata": {"category": "allergy"}}
```

A `category` in `VAYL_CRITICAL_CATEGORIES` makes the fact always reach recall context. See [Safety gates and human approval](../guides/safety-gates-and-human-approval.md).

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication and access</h4></td><td>Principals, roles, scopes and tenants.</td><td><a href="authentication-and-access.md">authentication-and-access.md</a></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i> Reconciliation</h4></td><td>What happens inside a space on every write.</td><td><a href="core-concepts.md">core-concepts.md</a></td></tr><tr><td><h4><i class="fa-server" style="color:$primary;">:server:</i> Deploy vayl-server</h4></td><td>Run a shared, authenticated deployment.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr></tbody></table>
