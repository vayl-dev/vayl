---
description: >-
  Build a support agent that remembers each customer's current plan,
  preferences, and issues — isolated per customer, always answering with what's
  true now.
icon: headset
---

# Tutorial: a customer-support assistant

A different domain from the hospital assistant, and a different set of Vayl strengths. There's no life-safety gating here — instead the stars are **per-customer isolation**, keeping facts **current** across many conversations, and resolving **conflicting sources** (what the customer says vs. what your billing system knows) without silently losing either.

## What we're building

A support assistant that, for each customer, remembers their current plan, preferences, and open issues — and answers with what's true **now**, never a stale value from three tickets ago. It runs on a shared team server, and each customer's memory is completely isolated from the next.

## Step 1 — connect to the team server

Support is a team setting, so we run `vayl-server` and connect over HTTP with an API key (see Calling Vayl from code). We pass the customer id per call as the `user_id`:

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = "https://memory.acme.com/mcp"
KEY = "vayl_sk_…"   # the support bot's key

async def main():
    headers = {"Authorization": f"Bearer {KEY}"}
    async with streamablehttp_client(URL, headers=headers) as (r, w, _), ClientSession(r, w) as s:
        await s.initialize()

        async def call(tool, customer, **args):
            res = await s.call_tool(tool, {"user_id": customer, **args})
            return res.content[0].text

        # … steps below …

asyncio.run(main())
```

## Step 2 — a memory per customer

Each customer is an isolated [memory space](../core-concepts/memory-spaces.md). Facts for `cust_5521` never mix with `cust_7788` — the same subject (`plan`) is a different slot in each:

```python
await call("remember", "cust_5521", text="On the Pro plan; prefers email over phone")
await call("remember", "cust_7788", text="On the Free plan")

print(await call("recall", "cust_5521", question="what plan are they on?"))   # -> Pro
print(await call("recall", "cust_7788", question="what plan are they on?"))   # -> Free
```

One deployment, thousands of customers, no cross-talk.

## Step 3 — keep it current (reconciliation)

Weeks later the customer upgrades. You don't append a second plan — Vayl **supersedes** the old one:

```python
await call("remember", "cust_5521", text="Upgraded to the Enterprise plan")
print(await call("recall", "cust_5521", question="what plan are they on?"))   # -> Enterprise
```

Ask an additive memory the same thing after a few changes and it may hand back "Pro" from an old ticket. Vayl keeps exactly one current value, with the rest in history.

## Step 4 — when sources disagree (source authority)

The interesting case: the customer _says_ one thing; your billing system _knows_ another. You don't want to silently trust either — you want the authoritative source to win, and the disagreement flagged for a human. Set a reconciliation policy for the space:

```python
await call("set_reconcile_policy", "cust_5521",
           mode="AUTHORITY", authority={"billing_system": 10, "customer": 1})

await call("remember", "cust_5521", text="I think I'm still on Pro", source="customer")
await call("remember", "cust_5521", text="Plan: Enterprise",         source="billing_system")

print(await call("recall", "cust_5521", question="what plan are they on?", explain=True))
# -> Enterprise (source: billing_system). The customer's "Pro" claim is FLAGGED, not discarded.
```

Because `billing_system` outranks `customer`, the current value is what billing says — but the customer's contradicting claim isn't thrown away, it's **flagged** so an agent can follow up on the confusion. `explain=True` shows which source each answer rests on.

{% hint style="info" %}
The three policies: `RECENCY` (newest wins), `AUTHORITY` (higher-ranked source wins, lower is flagged), `REVIEW` (every cross-source conflict is flagged for a human). Choose per space.
{% endhint %}

## Step 5 — resolve and forget

When an issue is resolved or the customer opts out, retract it — it stops surfacing but stays in history for audit:

```python
await call("remember", "cust_5521", text="Open issue: billing double-charge")
# … later …
await call("forget", "cust_5521", text="The billing issue is resolved")
print(await call("recall", "cust_5521", question="any open issues?"))   # -> none current
```

## Step 6 — isolate customer-facing keys

The internal support bot above may serve every customer. But if you expose a **per-customer** widget or portal, give that integration a key **scoped** to just that customer, so a bug or a leaked token can't read another tenant's memory:

```python
create_principal("portal-cust_5521", role="agent", scopes="cust_5521")
```

A scoped key that passes any other `user_id` is denied, and the denial is audited. See [Authentication & access](../core-concepts/authentication-and-access.md).

## What you leaned on

| Requirement                        | Vayl feature                                                   |
| ---------------------------------- | -------------------------------------------------------------- |
| each customer's memory isolated    | [memory spaces](../core-concepts/memory-spaces.md) (`user_id`) |
| always the current plan/preference | reconciliation (supersede on write)                            |
| customer vs. system disagreements  | `set_reconcile_policy` (AUTHORITY / REVIEW) + `source`         |
| why the agent believes X           | `recall(explain=True)` provenance                              |
| safe multi-tenant exposure         | **scoped** API keys                                            |

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-hospital" style="color:$primary;">:hospital:</i> Tutorial: a hospital medication assistant</h4></td><td>The higher-stakes example — critical facts and approval gates.</td><td><a href="tutorial-a-hospital-medication-assistant.md">tutorial-a-hospital-medication-assistant.md</a></td></tr><tr><td><h4><i class="fa-sitemap" style="color:$primary;">:sitemap:</i> Memory spaces</h4></td><td>The per-customer isolation model in depth.</td><td><a href="../core-concepts/memory-spaces.md">memory-spaces.md</a></td></tr><tr><td><h4><i class="fa-lock" style="color:$primary;">:lock:</i> Authentication &#x26; access</h4></td><td>Scoping keys so a tenant can only see its own memory.</td><td><a href="../core-concepts/authentication-and-access.md">authentication-and-access.md</a></td></tr></tbody></table>
