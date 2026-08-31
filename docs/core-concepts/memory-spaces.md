---
description: >-
  How Vayl isolates memory per user, agent, and run — and how to use it safely
  in a multi-tenant deployment.
icon: sitemap
---

# Memory spaces

Every fact in Vayl belongs to a **memory space** identified by three optional keys: `user_id`, `agent_id`, and `run_id`. Each unique combination is a completely isolated store — reconciliation, recall, history, and the same-slot invariant all operate **within one space**. The same subject in two spaces is two independent slots.

## The three keys

| Key        | Answers                  | Typical value                                                               |
| ---------- | ------------------------ | --------------------------------------------------------------------------- |
| `user_id`  | _whose_ memory is this?  | a customer, end-user, account, or patient id                                |
| `agent_id` | _which agent_ owns it?   | `planner`, `support-bot` — when several agents share a deployment           |
| `run_id`   | _which session or task_? | a conversation or job id, for scratch memory that shouldn't outlive the run |

Leave any of them blank. With all three blank you get a single shared space — fine for a personal, single-user setup.

Every tool accepts them:

```python
remember("Customer is on the Free plan", user_id="cust_5521")
recall("what plan are they on?",          user_id="cust_5521")     # -> "Free"

# a planner agent and a coder agent keep separate memory for the same user
remember("Prefers dark mode",    user_id="u1", agent_id="planner")
remember("Uses tabs, not spaces", user_id="u1", agent_id="coder")
```

## What isolation guarantees

* Facts in one space never reconcile against another. Superseding `plan` for `cust_5521` doesn't touch `cust_7788`.
* A recall in one space never surfaces another space's facts.
* History, flags, and reconciliation policy are all per-space.

This is how a single deployment safely serves thousands of users: each `user_id` is its own bounded store of a few active facts.

## Choosing a scoping strategy

| Pattern               | Keys                   | When                                |
| --------------------- | ---------------------- | ----------------------------------- |
| Personal assistant    | all blank              | one user, one memory                |
| Multi-tenant SaaS     | `user_id` per customer | isolate each customer's state       |
| Multi-agent system    | `user_id` + `agent_id` | agents that shouldn't share beliefs |
| Ephemeral task memory | + `run_id`             | scratch memory scoped to one run    |

## Metadata

`remember` also accepts `metadata` to tag the facts stored in that call — for example a `category` used by the [critical-fact channel](../guides/safety-gates-and-human-approval.md). Tags travel with the fact and appear in `export_memory`.

```python
remember("Patient is allergic to penicillin",
         user_id="patient_42", metadata={"category": "allergy"})
```

{% hint style="warning" %}
**`user_id` is a scoping parameter, not an identity.** It arrives from the caller, so on a shared `vayl-server` a valid API key could read any space simply by passing a different `user_id`. Confine each key with **scopes** — see [Authentication & access](authentication-and-access.md).
{% endhint %}

## Next

* [Authentication & access](authentication-and-access.md) — confine principals to the spaces they may touch.
