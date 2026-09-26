---
description: >-
  Give your existing agent reconciling memory — in LangGraph, the OpenAI Agents
  SDK, CrewAI, the Vercel AI SDK, or Mastra.
---

# Agent frameworks

Already building on an agent framework? Add Vayl as a small set of memory **tools** your agent calls — `remember`, `recall`, `history`, `forget`, `list_memories` — and its memory stops going stale.

Because Vayl **reconciles**, the agent is never handed a fact and its replacement at the same time:

```
"We use Redux."                           → remembered
"Actually we moved off Redux to Zustand." → Redux retired, Zustand active
recall("what do we use?")                  → "Zustand"      (not "Redux, Zustand")
```

A plain vector store returns both. Vayl returns the one that's true now — and keeps the old one in history.

## How it works

Every adapter is the same shape: bind a memory to a **memory space** once, then hand its tools to your agent.

* **Same surface everywhere** — `remember` · `recall` · `history` · `forget` · `list_memories`, described for the model so it knows when to reach for each.
* **Scope is bound server-side** — the `user_id` / `agent_id` / `run_id` lives on the client, never as a tool argument, so the model can't read or set whose memory it touches.
* **One connection** — the adapter keeps a single session to a local `vayl-mcp` (stdio) or a shared team server over HTTP.

| Framework             | Language   | Install                                   | Import                                                   |
| --------------------- | ---------- | ----------------------------------------- | -------------------------------------------------------- |
| LangGraph / LangChain | Python     | `pip install 'vayl-mcp[langgraph]'`       | `from vayl.integrations.langgraph import VaylMemory`     |
| OpenAI Agents SDK     | Python     | `pip install 'vayl-mcp[openai-agents]'`   | `from vayl.integrations.openai_agents import VaylMemory` |
| CrewAI                | Python     | `pip install 'vayl-mcp[crewai]'`          | `from vayl.integrations.crewai import VaylMemory`        |
| Vercel AI SDK         | TypeScript | `npm i @vayl.dev/client ai zod`           | `import { vaylTools } from "@vayl.dev/client/vercel"`    |
| Mastra                | TypeScript | `npm i @vayl.dev/client @mastra/core zod` | `import { vaylTools } from "@vayl.dev/client/mastra"`    |

## Python

{% tabs %}
{% tab title="LangGraph" %}
```python
from vayl.integrations.langgraph import VaylMemory

with VaylMemory(user_id="proj_7") as mem:
    agent = mem.agent("openai:gpt-4o-mini")      # ready agent wired to memory
    agent.invoke({"messages": [("user", "We moved off Redux to Zustand. What do we use now?")]})
```

Prefer your own graph? Bind the tools directly with `create_agent(model, tools=mem.tools())` (`from langchain.agents import create_agent`).
{% endtab %}

{% tab title="OpenAI Agents SDK" %}
```python
from vayl.integrations.openai_agents import VaylMemory
from agents import Runner

with VaylMemory(user_id="proj_7") as mem:
    agent = mem.agent(model="gpt-4o")
    result = Runner.run_sync(agent, "We moved off Redux to Zustand. What do we use now?")
    print(result.final_output)
```

Memory is exposed as function tools — the agent decides when to `remember` and `recall`.
{% endtab %}

{% tab title="CrewAI" %}
```python
from vayl.integrations.crewai import VaylMemory
from crewai import Agent

with VaylMemory(user_id="proj_7") as mem:
    analyst = Agent(role="Analyst", goal="Answer with current facts",
                    backstory="Uses long-term memory.", tools=mem.tools())
    # build your tasks and Crew as usual
```
{% endtab %}
{% endtabs %}

Narrow the tool set with `mem.tools(include=[...])` or `mem.tools(exclude=[...])`.

## TypeScript

{% tabs %}
{% tab title="Vercel AI SDK" %}
```typescript
import { generateText, stepCountIs } from "ai";
import { openai } from "@ai-sdk/openai";
import { Vayl } from "@vayl.dev/client";
import { vaylTools } from "@vayl.dev/client/vercel";

const m = await Vayl.connect({ userId: "proj_7" });
const { text } = await generateText({
  model: openai("gpt-4o"),
  tools: vaylTools(m),
  stopWhen: stepCountIs(5),
  prompt: "We moved off Redux to Zustand. What do we use now?",
});
await m.close();
```
{% endtab %}

{% tab title="Mastra" %}
```typescript
import { Agent } from "@mastra/core/agent";
import { Vayl } from "@vayl.dev/client";
import { vaylTools } from "@vayl.dev/client/mastra";

const m = await Vayl.connect({ userId: "proj_7" });
const agent = new Agent({
  id: "assistant",
  name: "Assistant",
  instructions: "Call recall before answering; remember when the user states or changes a fact.",
  model: "openai/gpt-4o",
  tools: vaylTools(m),
});
```
{% endtab %}
{% endtabs %}

`vaylTools(m, { include: [...], exclude: [...] })` narrows the tool set.

## Framework memory vs. Vayl

Every one of these frameworks ships its own memory or store — and they **accumulate**. Vayl **reconciles** and keeps an auditable history. Use the framework's own store for raw conversation history, and Vayl for the durable facts an agent must never get wrong. They compose — run both.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-code" style="color:$primary;">:code:</i> Calling Vayl from code</h4></td><td>The Python &#x26; TypeScript clients these adapters build on.</td><td><a href="../getting-started/calling-vayl-from-code.md">calling-vayl-from-code.md</a></td></tr><tr><td><h4><i class="fa-database" style="color:$primary;">:database:</i> Memory tools</h4></td><td>The tools your agent gets — remember, recall, history, and more.</td><td><a href="../mcp-tools/memory.md">memory.md</a></td></tr><tr><td><h4><i class="fa-sitemap" style="color:$primary;">:sitemap:</i> Memory spaces</h4></td><td>How <code>user_id</code> / <code>agent_id</code> / <code>run_id</code> isolate memory.</td><td><a href="../core-concepts/memory-spaces.md">memory-spaces.md</a></td></tr></tbody></table>
