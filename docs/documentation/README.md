---
description: >-
  Give your agent memory that stays true: it keeps what's current, retracts
  what changed, and keeps the full history queryable.
icon: hand-wave
---

# Welcome to Vayl

**Vayl is the reconciling memory layer for AI agents.** When a fact changes, Vayl retires the old value instead of storing both, so your agent gets back what is true now. A removal is a real retraction, ambiguous input is flagged instead of guessed, and the full history stays queryable and auditable. Vayl is an [MCP](https://modelcontextprotocol.io) server, so any MCP client can use it.

<button type="button" class="button primary" data-action="ask" data-icon="gitbook-assistant">Ask a question…</button>

<button type="button" class="button secondary" data-action="ask" data-query="How do I connect Vayl to my MCP client" data-icon="bolt">Connect an MCP client</button><button type="button" class="button secondary" data-action="ask" data-query="What is reconciling memory" data-icon="book">What is reconciling memory?</button><button type="button" class="button secondary" data-action="ask" data-query="How do I scope memory per user" data-icon="sitemap">Scope memory per user</button>

***

## See it in one example

Vayl turns a conversation into facts and keeps only what is true now, while preserving the history.

```
"We use Redux."                            → remembered        (state = Redux)
"Actually we moved off Redux to Zustand."  → Redux superseded  (state = Zustand)
"What do we use?"                          → "Zustand"         not "Redux, Zustand"
"What did we use first?"                   → "Redux"           answered from history
```

{% hint style="info" %}
**This sketch is illustrative.** The store side is deterministic: after the second message the only active value for `state` is `Zustand`. The quoted answers are written by the LLM you configure, so the wording varies. To see the real engine output with no keys, run `vayl-demo` (see the [Quickstart](getting-started/quickstart.md)).
{% endhint %}

Why this matters, and how it differs from memory that only appends: [Why Vayl](why-vayl/README.md).

{% hint style="info" icon="sparkles" %}
**New to reconciling memory?** Read [Reconciliation](core-concepts/core-concepts.md) for _supersede_, _retract_, _flag_, and the same-slot invariant, the ideas that make "what's true now" unambiguous.
{% endhint %}

## Where to start

<table data-card-size="large" data-view="cards"><thead><tr><th></th><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-bolt" style="color:$primary;">:bolt:</i></h4></td><td><h4>Quickstart</h4></td><td>Install Vayl, connect your MCP client, and store your first fact.</td><td><a href="getting-started/quickstart.md">quickstart.md</a></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i></h4></td><td><h4>Reconciliation</h4></td><td>Supersede, retract, flag, the same-slot invariant, and history.</td><td><a href="core-concepts/core-concepts.md">core-concepts.md</a></td></tr><tr><td><h4><i class="fa-graduation-cap" style="color:$primary;">:graduation-cap:</i></h4></td><td><h4>Guides</h4></td><td>Deploy a team server, add safety gates, run on local models.</td><td><a href="guides/guides.md">guides.md</a></td></tr><tr><td><h4><i class="fa-book-open" style="color:$primary;">:book-open:</i></h4></td><td><h4>Reference</h4></td><td>Configuration, CLI, HTTP endpoints, and troubleshooting.</td><td><a href="reference/reference.md">reference.md</a></td></tr></tbody></table>

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-lightbulb" style="color:$primary;">:lightbulb:</i> Why Vayl</h4></td><td>Why appending memory goes stale, and who needs reconciling memory most.</td><td><a href="why-vayl/README.md">README.md</a></td></tr><tr><td><h4><i class="fa-diagram-project" style="color:$primary;">:diagram-project:</i> How Vayl works</h4></td><td>Architecture and the data flow of a write and a read.</td><td><a href="how-vayl-works.md">how-vayl-works.md</a></td></tr><tr><td><h4><i class="fa-plug" style="color:$primary;">:plug:</i> Integrations</h4></td><td>Claude Code, Cursor, and agent frameworks.</td><td><a href="integrations/README.md">README.md</a></td></tr></tbody></table>
