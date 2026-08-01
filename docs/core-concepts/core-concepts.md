---
description: >-
  Reconciliation, the same-slot invariant, events vs. state, and history — the
  ideas that make memory tell you what's true now.
icon: book
---

# Core concepts

Vayl exists to answer one question reliably: **what is true right now?** Additive memory can't, because it keeps every fact it has ever seen and lets similarity search decide which surfaces — so a value you explicitly changed can come back as if it were current. Vayl reconciles instead: on every write it decides how a new fact relates to what it already knows, keeps exactly one active value per thing, and preserves the history off to the side.

This page explains the model end to end.

## Facts

Every message your agent sends to `remember` is turned into one or more **facts** by a single LLM call. A fact is structured:

| Field        | Meaning                                                                  |
| ------------ | ------------------------------------------------------------------------ |
| `subject`    | what the fact is about (`active_medication`, `plan`, `primary_database`) |
| `value`      | the value itself (`warfarin 5mg`, `Free`, `Postgres`)                    |
| `scope`      | the space and qualifier the fact lives in                                |
| `kind`       | `state` (holds until replaced) or `event` (happened once)                |
| `confidence` | how sure the extractor is                                                |
| `source`     | who or what asserted it — an agent, a person, a connector                |

One message can produce several facts — _"We moved from AWS to GCP and dropped Sentry"_ becomes a supersede **and** a retract.

## Reconciliation: the five actions

For each new fact, Vayl compares it against what's stored in the same slot and takes exactly one action.

### Supersede

A new value for something already known. The old value is **retired to history** and the new one becomes active.

> "The customer is on Pro." … later … "They moved to Free." → `plan` is now **Free**; **Pro** is in history.

### Retract

An explicit removal. A **tombstone** retires the fact; a recall for the current value returns nothing (or "I don't know"), never the stale value.

> "We dropped Sentry." → `monitoring = Sentry` is retracted.

### Flag

The new fact conflicts with an existing one and the resolution is ambiguous. Vayl **does not guess** — it flags the conflict for a human, and a flagged value never presents itself as current.

> Two different allergies asserted for the same patient → flagged for review.

### Coexist

Both facts are independently true and don't compete for the same slot.

> "The patient takes metformin" and "the patient takes lisinopril" → both active.

### Dedup

The fact is already known. Nothing changes — no duplicate row, no churn.

{% hint style="info" %}
Which action fires is decided by the **engine**, using the extracted intent plus the same-slot rule below — not left entirely to the model. A weak model that mislabels a change still cannot leave two contradictory values live.
{% endhint %}

## The same-slot invariant

A **slot** is a fact's `(subject, scope)`. The invariant Vayl guarantees:

> **At most one active value per slot.**

This is the load-bearing idea. Because it is enforced structurally, "which value is current?" is always answerable from a single row, and reconciliation quality stops tracking model strength — even a small model can't produce two live contradictory values, because the engine won't store them.

## Events vs. state

Not everything should be reconciled. Facts carry a `kind`:

* **State** holds until something replaces it — `we use Postgres`, `the plan is Free`. State obeys the same-slot invariant.
* **Events** happened at a point in time — `ran a charity race`, `the customer called on Tuesday`. Two events are two events, not a correction, so events **coexist** and never supersede each other.

The exemption is enforced in the engine: a mislabelled supersede on an event is downgraded, so neither a confused model nor a later state fact sharing a subject can delete the record that something happened. Untagged facts default to state.

## History and the hot path

Vayl is event-sourced: superseded and retracted facts are **retired, not deleted** — but they move off the **hot path**.

* A normal `recall` (and the internal `load`) reads only the **active + flagged** set. Retired facts are never loaded, so a stale value **cannot** be returned as current, no matter how the model behaves. This is a structural property, not a prompt instruction.
* History lives on disk and is read **lazily** — only when you ask about the past with `recall(..., include_history=True)` or `history(subject)`.

The separation also keeps Vayl fast as memory grows: per-operation cost is **O(active facts)**, not O(total history).

## Provenance

Every active fact records its `source` and `confidence`, and a supersede records what it replaced. `recall(explain=True)` returns that provenance — the exact facts behind an answer, who asserted each, and what it superseded. It is the truthful basis for auditing _why_ the agent believes what it does, and it is what `record_decision` snapshots.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-sitemap" style="color:$primary;">:sitemap:</i> Memory spaces</h4></td><td>How facts are isolated per user, agent, and run.</td><td><a href="memory-spaces.md">memory-spaces.md</a></td></tr><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication &#x26; access</h4></td><td>Who may touch which space.</td><td><a href="authentication-and-access.md">authentication-and-access.md</a></td></tr><tr><td><h4><i class="fa-toolbox" style="color:$primary;">:toolbox:</i> MCP tools</h4></td><td>The tools that expose all of this.</td><td><a href="../mcp-tools/">mcp-tools</a></td></tr></tbody></table>
