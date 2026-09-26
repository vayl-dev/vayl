---
description: The MCP tools Vayl exposes, grouped by what they do.
icon: toolbox
---

# MCP tools

Vayl's API **is** its set of MCP tools — there's no REST layer to learn. Any MCP client connects and calls them; usually the **LLM in your agent** calls them automatically, but you can also call them from your own code. See [Calling Vayl from code](../getting-started/calling-vayl-from-code.md).

## Reading these pages

Each tool page lists the tool's parameters; optional ones have a default or are marked `?`. Over MCP you pass arguments as a JSON object:

```json
{"name": "remember", "arguments": {"text": "We use Postgres", "user_id": "proj_7"}}
```

For the exact, always-current schema of every tool, call `tools/list` from any MCP client.

## Conventions

* **Scoping.** Every tool accepts optional `user_id`, `agent_id`, and `run_id` to target a [memory space](../core-concepts/memory-spaces.md). Leave them blank for a single space.
* **Safety annotations.** Each tool carries MCP annotations, so clients can auto-run reads and confirm before an irreversible erasure.
* **Capabilities.** On `vayl-server`, every tool checks the capability its caller's role grants, and is fail-closed.

## Groups

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory</h4></td><td>Store, query, correct, and time-travel over facts.</td><td><a href="memory.md">memory.md</a></td></tr><tr><td><h4><i class="fa-shield-halved" style="color:$primary;">:shield-halved:</i> Safety and gating</h4></td><td>Gate irreversible actions and require approval.</td><td><a href="safety-and-gating.md">safety-and-gating.md</a></td></tr><tr><td><h4><i class="fa-file-signature" style="color:$primary;">:file-signature:</i> Accountability</h4></td><td>Signed, tamper-evident record of what the agent did.</td><td><a href="accountability.md">accountability.md</a></td></tr><tr><td><h4><i class="fa-scale-balanced" style="color:$primary;">:scale-balanced:</i> Compliance (GDPR)</h4></td><td>Erasure, export, and retention with signed receipts.</td><td><a href="compliance-gdpr.md">compliance-gdpr.md</a></td></tr><tr><td><h4><i class="fa-user-gear" style="color:$primary;">:user-gear:</i> Administration</h4></td><td>Principals, policy, and operations.</td><td><a href="administration.md">administration.md</a></td></tr></tbody></table>
