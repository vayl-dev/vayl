---
description: >-
  Agent memory returns stale facts because most systems append instead of
  reconcile. The root cause — and how to fix it today.
---

# Why your agent's memory returns stale facts

**Agent memory returns stale facts because most memory systems&#x20;**_**append**_**&#x20;instead of&#x20;**_**reconcile**_**.** When a value changes, the old one stays searchable, and retrieval can surface it as current. The fix isn't a bigger model or more memory — it's memory that retires the old value on write and keeps exactly one active value per fact.

## The root cause, precisely

Additive and vector memory (and today's built-in agent memory) treat every statement as a new item. Update a customer's plan four times and you have four `plan = …` memories, all timestamped, all retrievable. At read time the model sees several equally-plausible "current" values and guesses. Under sustained churn the silently-wrong rate _rises_ — the more a fact changes, the worse it gets.

## Why "just prompt it better" doesn't hold

Reconciliation-by-prompt depends on the model noticing the contradiction every time, across a long context, forever. Vayl makes it _structural_: the engine enforces one active value per slot, so correctness stops tracking model strength. A cheaper model can't produce two live contradictory values because the store won't hold them.

## The three shapes of agent memory

* **Additive / vector** — simple, broad recall; **goes stale** under change, and can't cleanly model removal.
* **Temporal graph** — reconciles and answers multi-hop, but heavy (a graph database plus many LLM calls per fact), and removal is often an afterthought.
* **Reconciling (Vayl)** — supersede / retract / flag on write; one active value per slot; history kept; \~2 LLM calls, one SQLite file, no server.

## How to fix it today

Point your MCP client at Vayl. `remember` extracts and reconciles; `recall` answers from the active set or says **"I don't know"** rather than guessing; `history` shows what changed. No migration off your existing RAG — Vayl handles the _changing_ facts; your corpus tools handle the static ones.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-bolt" style="color:$primary;">:bolt:</i> Quickstart</h4></td><td>Point an MCP client at Vayl and store your first fact.</td><td><a href="../getting-started/quickstart.md">quickstart.md</a></td></tr><tr><td><h4><i class="fa-diagram-project" style="color:$primary;">:diagram-project:</i> How Vayl works</h4></td><td>The reconciliation model end to end.</td><td><a href="../how-vayl-works.md">how-vayl-works.md</a></td></tr><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory tools</h4></td><td><code>remember</code>, <code>recall</code>, <code>history</code>, and the rest.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr></tbody></table>
