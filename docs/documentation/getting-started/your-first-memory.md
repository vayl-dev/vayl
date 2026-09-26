---
description: >-
  A hands-on walkthrough of storing, changing, removing, and recalling facts,
  with the MCP tool call and the real return behind each step.
icon: compass
---

# Your first memory

**Store a fact, change it, remove one, and read the history, with the MCP tool call and Vayl's return for each step.** In a chat client you just talk and the model makes these calls. To make them yourself, see [Calling Vayl from code](calling-vayl-from-code.md).

{% hint style="info" %}
`remember`, `forget`, and `history` returns below are Vayl's exact output. Two things come from the model and can differ for you: the **subject names** (`plan`, `primary_database`) are chosen by the extracting LLM, and **`recall` answers** are free text written by the answering LLM.
{% endhint %}

## Store facts

> **You:** The customer is on the Pro plan, and their primary database is Postgres.

```json
{"name": "remember", "arguments": {"text": "The customer is on the Pro plan, and their primary database is Postgres.", "user_id": "cust_1"}}
```

```
Stored: [ADD] plan = Pro; [ADD] primary_database = Postgres
```

One message, two facts, both active. `user_id` picks the memory space; leave it out and the space is `default`.

## Supersede: a value changes

> **You:** Actually, the customer moved to the Free plan.

```json
{"name": "remember", "arguments": {"text": "Customer moved to the Free plan", "user_id": "cust_1"}}
```

```
Stored: [SUPERSEDE] plan = Free
```

`plan` is now Free. Pro is marked `SUPERSEDED` and kept in history; it is no longer an active fact.

## Retract: a value is removed

> **You:** We dropped the Postgres database.

```json
{"name": "forget", "arguments": {"text": "We dropped the Postgres database", "user_id": "cust_1"}}
```

```
Retracted (retained in history for audit): primary_database = Postgres
```

If nothing active matches, `forget` changes nothing and says so:

```
Nothing matching to retract (that fact isn't currently stored).
```

## Ask what's true now

```json
{"name": "recall", "arguments": {"question": "what plan is the customer on?", "user_id": "cust_1"}}
```

A model-generated answer from the active facts, for example `The customer is on the Free plan.` Asked about the database, it answers along the lines of "I don't know": the retracted fact is not loaded.

## See the provenance

```json
{"name": "recall", "arguments": {"question": "what plan?", "user_id": "cust_1", "explain": true}}
```

The answer (model-generated), then the exact facts it was given, in Vayl's format:

```
The customer is on the Free plan.

Based on these facts:
  • #3 plan = Free  [conf 0.95, supersedes #1]
```

Each line shows the memory id, the fact, the extractor's confidence, the source if one was recorded, and what it superseded.

## Ask what changed

```json
{"name": "history", "arguments": {"subject": "plan", "user_id": "cust_1"}}
```

```
History for 'plan' (oldest → newest):
  1. Pro  [SUPERSEDED]
  2. Free  [ACTIVE]
```

For a question in plain language, `recall` with `"include_history": true` lets the model answer from retired facts too ("They were on Pro, then moved to Free."). It is off by default, which is why a normal recall cannot return a stale value.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-code" style="color:$primary;">:code:</i> Calling Vayl from code</h4></td><td>Make these calls from Python, TypeScript, or raw MCP.</td><td><a href="calling-vayl-from-code.md">calling-vayl-from-code.md</a></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i> Reconciliation</h4></td><td>Every action and status, including flag and coexist.</td><td><a href="../core-concepts/core-concepts.md">core-concepts.md</a></td></tr><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory tools</h4></td><td>Full arguments and returns for each tool.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr></tbody></table>
