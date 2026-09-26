---
description: What Vayl is, what you need, install, verify, and the two ways to run it.
icon: rocket-launch
---

# Getting started

Vayl gives an AI agent memory that stays **current**. Your agent calls the standard MCP tools (`remember`, `recall`, `forget`, …); Vayl turns each message into structured facts, reconciles them against what it already knows, keeps one active value per slot, and retains the history.

## How it fits together

```
your agent  --MCP-->  Vayl  -->  reconcile (supersede / retract / flag)  -->  SQLite (or Postgres)
                       |
                       +-->  one LLM call to extract facts, an embedder for retrieval
```

The only network calls Vayl makes are to the LLM and embedder you configure. Everything else — reconciliation, storage, history — is local.

## What you'll need

* **Python 3.10+.** Vayl is also validated on free-threaded CPython 3.14t.
* **An LLM and an embedder.** One model extracts structured facts from text; an embedder powers retrieval. Any **OpenAI-compatible** endpoint works:
  * OpenAI (`OPENAI_API_KEY`)
  * A local model via Ollama / vLLM / LM Studio (`OPENAI_BASE_URL`)
  * An EU-region or self-hosted endpoint, if data residency matters
  * With nothing configured, Vayl defaults to a **local Ollama** endpoint — no data leaves the machine.
* **An MCP client** — Claude Desktop, Cursor, Claude Code, or any MCP-capable agent.

## Install

```bash
pip install vayl-mcp
```

Optional extras:

```bash
pip install "vayl-mcp[server]"     # the vayl-server HTTP transport
pip install "vayl-mcp[postgres]"   # the Postgres backend
pip install "vayl-mcp[graph]"      # the optional Neo4j graph projection
```

## Verify

```bash
vayl-mcp --help
```

From your client you can also call the `health()` tool, which checks the database, embedder, LLM, and graph are reachable and reports which piece is failing if any.

## Two ways to run

**Local (stdio):** `vayl-mcp` speaks MCP over stdio for a single local user — the right choice for a desktop client. Memory persists to a local SQLite file at `VAYL_DB`. No auth, no server.

**Team (HTTP):** `vayl-server` runs the same tools over authenticated streamable-HTTP so a team shares one deployment. Every request needs an API key, and roles grant capabilities.

> Vayl stores everything in a single **SQLite** file by default — zero setup, nothing to operate. When you outgrow one node, set `VAYL_DATABASE_URL` and the same code runs on **Postgres**.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-bolt" style="color:$primary;">:bolt:</i> Quickstart</h4></td><td>Connect an MCP client and store your first fact in about 5 minutes.</td><td><a href="quickstart.md">quickstart.md</a></td></tr><tr><td><h4><i class="fa-compass" style="color:$primary;">:compass:</i> Your first memory</h4></td><td>See supersede, retract, and history in action, with the tool calls behind each.</td><td><a href="your-first-memory.md">your-first-memory.md</a></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i> Core concepts</h4></td><td>How reconciliation works under the hood.</td><td><a href="../core-concepts/core-concepts.md">core-concepts.md</a></td></tr></tbody></table>
