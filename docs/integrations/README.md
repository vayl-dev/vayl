---
description: >-
  The MCP clients, agent frameworks, models, and storage backends Vayl works
  with.
icon: puzzle-piece
---

# Integrations

Vayl is a standard **Model Context Protocol** server, so it drops into any MCP client and talks to any OpenAI-compatible model. Point it at the clients, frameworks, and models you already use — no lock-in.

## MCP clients

Any MCP-capable agent can call Vayl's tools. Add the server to your client's configuration (see [Quickstart](../getting-started/quickstart.md)) and restart.

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-robot" style="color:$primary;">:robot:</i> Claude Desktop</h4></td><td>Add Vayl under <code>mcpServers</code> and restart the app.</td><td><a href="../getting-started/quickstart.md">quickstart.md</a></td></tr><tr><td><h4><i class="fa-i-cursor" style="color:$primary;">:i-cursor:</i> Cursor</h4></td><td>Step-by-step: MCP config (OpenAI or local Ollama), a rule, and per-project memory.</td><td><a href="cursor.md">cursor.md</a></td></tr><tr><td><h4><i class="fa-terminal" style="color:$primary;">:terminal:</i> Claude Code</h4></td><td>Add Vayl with <code>claude mcp add</code> and it's available in-session.</td><td><a href="../getting-started/quickstart.md">quickstart.md</a></td></tr><tr><td><h4><i class="fa-plug" style="color:$primary;">:plug:</i> Any MCP client</h4></td><td>Vayl speaks stdio and streamable-HTTP — any compliant client works.</td><td><a href="../mcp-tools/the-mcp-interface.md">the-mcp-interface.md</a></td></tr></tbody></table>

## Agent frameworks

Building on an agent framework instead? Add Vayl's reconciling memory as tools your agent calls — the same `remember` / `recall` surface across all of them. Full setup on the [Agent frameworks](agent-frameworks.md) page.

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-diagram-project" style="color:$primary;">:diagram-project:</i> LangGraph / LangChain</h4></td><td>Bind Vayl's tools to a LangChain <code>create_agent</code>.</td><td><a href="agent-frameworks.md">agent-frameworks.md</a></td></tr><tr><td><h4><i class="fa-robot" style="color:$primary;">:robot:</i> OpenAI Agents SDK</h4></td><td>Reconciling memory as function tools the agent calls.</td><td><a href="agent-frameworks.md">agent-frameworks.md</a></td></tr><tr><td><h4><i class="fa-people-group" style="color:$primary;">:people-group:</i> CrewAI</h4></td><td>Give any crew agent memory that stays current.</td><td><a href="agent-frameworks.md">agent-frameworks.md</a></td></tr><tr><td><h4><i class="fa-bolt" style="color:$primary;">:bolt:</i> Vercel AI SDK</h4></td><td><code>vaylTools(m)</code> for <code>generateText</code> / <code>streamText</code>.</td><td><a href="agent-frameworks.md">agent-frameworks.md</a></td></tr><tr><td><h4><i class="fa-layer-group" style="color:$primary;">:layer-group:</i> Mastra</h4></td><td>Memory tools for a Mastra <code>Agent</code>.</td><td><a href="agent-frameworks.md">agent-frameworks.md</a></td></tr></tbody></table>

## Models & embedders

Vayl needs one model to extract facts and one embedder for retrieval — any **OpenAI-compatible** endpoint. Set them in [Configuration](../reference/configuration.md).

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-brain" style="color:$primary;">:brain:</i> OpenAI</h4></td><td>Set <code>OPENAI_API_KEY</code>; <code>gpt-5-mini</code> is the default.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr><tr><td><h4><i class="fa-house-laptop" style="color:$primary;">:house-laptop:</i> Ollama (local)</h4></td><td>Point <code>OPENAI_BASE_URL</code> at Ollama — nothing leaves the machine.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr><tr><td><h4><i class="fa-microchip" style="color:$primary;">:microchip:</i> vLLM / LM Studio</h4></td><td>Any local OpenAI-compatible server works the same way.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr><tr><td><h4><i class="fa-network-wired" style="color:$primary;">:network-wired:</i> Any OpenAI-compatible</h4></td><td>Self-hosted or EU-region endpoints for data residency.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr></tbody></table>

## Storage

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> SQLite</h4></td><td>The default — one file, zero setup, nothing to operate.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr><tr><td><h4><i class="fa-server" style="color:$primary;">:server:</i> Postgres</h4></td><td>Set <code>VAYL_DATABASE_URL</code> for concurrent, multi-writer scale.</td><td><a href="../guides/deploying-vayl-server.md">deploying-vayl-server.md</a></td></tr><tr><td><h4><i class="fa-diagram-project" style="color:$primary;">:diagram-project:</i> Neo4j graph</h4></td><td>Optional projection for deep multi-hop relational queries.</td><td><a href="../reference/configuration.md">configuration.md</a></td></tr></tbody></table>
