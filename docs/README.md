---
description: >-
  Give your agent memory that stays true — it remembers what's current, retracts
  what changed, and keeps the full history queryable.
icon: hand-wave
---

# Welcome to Vayl

**Vayl is the reconciling memory layer for AI agents.** Most memory layers _accumulate_ — they save every fact and later hand your agent a stale one. Vayl **reconciles**: a new value supersedes the old, a removal actually retracts, ambiguous input is flagged instead of guessed, and the full history stays queryable and auditable. It speaks the [Model Context Protocol](https://modelcontextprotocol.io), so any MCP client plugs in.

<button type="button" class="button primary" data-action="ask" data-icon="gitbook-assistant">Ask a question…</button>

<button type="button" class="button secondary" data-action="ask" data-query="How do I connect Vayl to my MCP client" data-icon="bolt">Connect an MCP client</button><button type="button" class="button secondary" data-action="ask" data-query="What is reconciling memory" data-icon="book">What is reconciling memory?</button><button type="button" class="button secondary" data-action="ask" data-query="How do I scope memory per user" data-icon="sitemap">Scope memory per user</button>

***

## See it in one example

Vayl turns a conversation into facts and keeps only what is true now — while preserving the history.

```
"We use Redux."                            → remembered
"Actually we moved off Redux to Zustand."  → Redux retired, Zustand active
"What do we use?"                          → "Zustand"   (not "Redux, Zustand")
"What did we use first?"                   → "Redux"     (history kept)
```

{% hint style="info" icon="sparkles" %}
**New to reconciling memory?** Read [Core concepts](core-concepts/core-concepts.md) to understand _supersede_, _retract_, and the _same-slot invariant_ — the ideas that make "what's true now" unambiguous.
{% endhint %}

## Where to start

<table data-card-size="large" data-view="cards"><thead><tr><th></th><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-rocket-launch" style="color:$primary;">:rocket-launch:</i></h4></td><td><h4>Getting started</h4></td><td>Install Vayl and connect it to your MCP client in minutes.</td><td><a href="getting-started/getting-started.md">getting-started.md</a></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i></h4></td><td><h4>Core concepts</h4></td><td>Reconciliation, the same-slot invariant, events vs. state, and history.</td><td><a href="core-concepts/core-concepts.md">core-concepts.md</a></td></tr><tr><td><h4><i class="fa-graduation-cap" style="color:$primary;">:graduation-cap:</i></h4></td><td><h4>Guides</h4></td><td>Deploy a team server, add safety gates, and scope memory per tenant.</td><td><a href="guides/guides.md">guides.md</a></td></tr><tr><td><h4><i class="fa-book-open" style="color:$primary;">:book-open:</i></h4></td><td><h4>Reference</h4></td><td>Configuration, environment variables, and terminology.</td><td><a href="reference/reference.md">reference.md</a></td></tr></tbody></table>
