---
description: >-
  How Vayl's tools are invoked — by your agent over MCP, or from your own code
  via the Python/TypeScript client or a raw MCP client.
icon: code
---

# Calling Vayl from code

Vayl is an **MCP server**: its tools are invoked over the Model Context Protocol — almost always by the **LLM in your agent or client**. When you want to drive it from your own code, use the small **Python or TypeScript client**, or a raw MCP client for any other language.

## The usual path: your agent calls the tools

In a chat client (Claude Desktop, Cursor) or an agent framework, you don't call Vayl directly. You configure the server once (see the [Quickstart](quickstart.md)); the model then calls `remember`, `recall`, and the rest as it needs them, from the tool descriptions Vayl advertises.

> **You:** Remember we switched to Zustand.

Behind the scenes the client issues an MCP tool call:

```json
{
  "method": "tools/call",
  "params": {
    "name": "remember",
    "arguments": { "text": "We switched to Zustand", "user_id": "proj_7" }
  }
}
```

You write the prompt; the model fills in the arguments.

## From Python — the `vayl` client

`pip install vayl-mcp` ships a small **synchronous** client that wraps the MCP boilerplate, so you call methods instead of writing `tools/call` JSON:

```python
from vayl import Vayl

with Vayl(user_id="proj_7") as m:                 # local: spawns vayl-mcp over stdio
    m.remember("We use Postgres")
    print(m.recall("what database do we use?"))   # -> "Postgres"

# a shared team server (authenticated streamable-HTTP):
# with Vayl(url="https://memory.example.com/mcp", api_key="vayl_sk_…") as m: ...
```

`remember` / `recall` / `forget` are named methods; **any** other tool is `m.call("check_before_act", subject="…")`. A default `user_id` / `agent_id` / `run_id` set on the client is sent on every call (only to tools that accept it).

## From TypeScript — the `@vayl.dev/client` package

The same surface for TS/JS agents (`npm install @vayl.dev/client`):

```ts
import { Vayl } from "@vayl.dev/client";

const m = await Vayl.connect({ userId: "proj_7" });        // stdio: spawns vayl-mcp
await m.remember("We use Postgres");
console.log(await m.recall("what database do we use?"));   // -> "Postgres"
await m.close();
```

`m.remember` / `m.recall` / `m.forget`, and `m.call(tool, args)` for any other tool. Framework adapters (LangGraph, OpenAI Agents, CrewAI, Vercel AI SDK, Mastra) build on these clients — see [Agent frameworks](../integrations/agent-frameworks.md).

## Raw MCP client (any language, no SDK)

The clients above wrap a standard MCP session. In another language, or for direct control, talk MCP yourself. Python over **stdio**:

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

params = StdioServerParameters(
    command="vayl-mcp",
    env={"OPENAI_API_KEY": "sk-…", "VAYL_DB": "/abs/vayl.db"},
)

async def main():
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        await session.call_tool("remember", {"text": "We use Postgres", "user_id": "proj_7"})
        res = await session.call_tool("recall", {"question": "what database do we use?", "user_id": "proj_7"})
        print(res.content[0].text)   # -> "Postgres"

asyncio.run(main())
```

Against a **team server** (HTTP + Bearer):

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    headers = {"Authorization": "Bearer vayl_sk_…"}
    async with streamablehttp_client("https://memory.example.com/mcp", headers=headers) as (r, w, _), ClientSession(r, w) as session:
        await session.initialize()
        res = await session.call_tool("recall", {"question": "what plan is the customer on?", "user_id": "cust_5521"})
        print(res.content[0].text)
```

Or a raw JSON-RPC call for a quick check:

```bash
curl -sN https://memory.example.com/mcp \
  -H "Authorization: Bearer vayl_sk_…" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"recall","arguments":{"question":"what plan?","user_id":"cust_5521"}}}'
```

{% hint style="info" %}
The tool names and arguments are identical across every path — the SDKs, raw stdio, raw HTTP, and the model's own calls. Only the transport and auth differ.
{% endhint %}

## Discovering the exact schema

Signatures in these docs are a guide. For the exact, always-current arguments of every tool, call `tools/list` from any MCP client:

```python
tools = await session.list_tools()
for t in tools.tools:
    print(t.name, t.inputSchema)
```

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory tools</h4></td><td>Every tool you'll call — arguments and returns.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr><tr><td><h4><i class="fa-plug" style="color:$primary;">:plug:</i> The MCP interface</h4></td><td>Transports, the call envelope, annotations, and errors.</td><td><a href="../mcp-tools/the-mcp-interface.md">the-mcp-interface.md</a></td></tr><tr><td><h4><i class="fa-wrench" style="color:$primary;">:wrench:</i> Troubleshooting</h4></td><td>When a call fails.</td><td><a href="../reference/troubleshooting.md">troubleshooting.md</a></td></tr></tbody></table>
