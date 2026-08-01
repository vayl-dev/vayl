---
description: Definitions of terms you'll meet in Vayl.
icon: bookmark
---

# Glossary

**Reconciliation** — deciding how a new fact relates to stored facts (supersede, retract, flag, coexist, dedup).

**Supersede** — replace a value; the old one is retired to history.

**Retract** — remove a fact; a tombstone keeps it in history but nothing current is returned.

**Flag** — surface an ambiguous conflict for a human instead of guessing.

**Slot** — a fact's `(subject, scope)`. The same-slot invariant allows at most one active value per slot.

**Same-slot invariant** — the engine guarantee that no slot has two active values at once.

**State vs. event** — state holds until replaced; events happened at a point in time and coexist.

**Memory space** — an isolated store keyed by `(user_id, agent_id, run_id)`.

**Principal** — an authenticated user or agent with an API key and role.

**Scope** — the set of `user_id`s a principal may touch.

**Audit chain** — the tamper-evident, hash-chained, Ed25519-signed log of every operation.

**Attestation / receipt** — signed, third-party-verifiable proof of what was known or erased.

**Critical fact** — a fact whose category bypasses semantic ranking so it is always included.

**Declared slot** — a pre-defined field with a canonical name, aliases, and optional confirm gate.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i> Core concepts</h4></td><td>These terms in context — the full reconciliation model.</td><td><a href="../core-concepts/core-concepts.md">core-concepts.md</a></td></tr><tr><td><h4><i class="fa-sliders" style="color:$primary;">:sliders:</i> Configuration</h4></td><td>The environment variables behind critical facts and declared slots.</td><td><a href="configuration.md">configuration.md</a></td></tr><tr><td><h4><i class="fa-toolbox" style="color:$primary;">:toolbox:</i> MCP tools</h4></td><td>The tools that put these concepts to work.</td><td><a href="../mcp-tools/">mcp-tools</a></td></tr></tbody></table>
