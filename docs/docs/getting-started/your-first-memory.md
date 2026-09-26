---
description: >-
  A hands-on walkthrough of storing, changing, removing, and recalling facts —
  with the MCP tool calls behind each step.
icon: compass
---

# Your first memory

This walkthrough shows every reconciliation action. In a chat client you just talk — the model makes the tool calls. Each step shows what you say, the **MCP tool call** the client issues, and the result. To make these calls yourself, see Calling Vayl from code.

## Store facts

> **You:** The customer is on the Pro plan, and their primary database is Postgres.

```json
{"name": "remember", "arguments": {"text": "Customer is on the Pro plan", "user_id": "cust_1"}}
{"name": "remember", "arguments": {"text": "Primary database is Postgres", "user_id": "cust_1"}}
```

Two facts, two slots (`plan`, `primary_database`), both active.

## Supersede — a value changes

> **You:** Actually, the customer moved to the Free plan.

```json
{"name": "remember", "arguments": {"text": "Customer moved to the Free plan", "user_id": "cust_1"}}
```

`plan` is now **Free**; **Pro** is retired to history. There are never two live values for one slot.

## Retract — a value is removed

> **You:** We dropped the Postgres database.

```json
{"name": "forget", "arguments": {"text": "We dropped the Postgres database", "user_id": "cust_1"}}
```

`primary_database` is retracted — a recall returns "I don't know", not a stale "Postgres".

## Ask what's true now

> **You:** What plan is the customer on?

```json
{"name": "recall", "arguments": {"question": "what plan is the customer on?", "user_id": "cust_1"}}
```

→ **"Free"** — reconciled state, not a list of every value ever mentioned.

## Ask what changed

> **You:** What plan were they on before?

```json
{"name": "recall", "arguments": {"question": "what plan before?", "user_id": "cust_1", "include_history": true}}
```

→ **"Pro, then Free."** History is opt-in; it never leaks into a normal recall.

## See the provenance

```json
{"name": "recall", "arguments": {"question": "what plan?", "user_id": "cust_1", "explain": true}}
```

Returns the exact facts behind the answer — source, confidence, and what each superseded.

{% hint style="success" %}
A superseded or retracted value **cannot** resurface as current: retired facts aren't loaded on a normal recall at all. History is opt-in, never the default.
{% endhint %}

## Next

* Calling Vayl from code — invoke these tools from a script or over HTTP.
* [Core concepts](../core-concepts/core-concepts.md) — the full reconciliation model.
