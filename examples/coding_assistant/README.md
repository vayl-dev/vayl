# Coding assistant that remembers project decisions

Your coding agent keeps suggesting the library you dropped. This example shows the fix: the project's stack is stated in plain sentences as it changes, and Vayl returns the **current** choice, never the stale one alongside it.

```
you: Let's use Redux for state.
you: We're on npm for packages.
you: Tests run on Jest.
you: We moved off Redux to Zustand.
you: Switched the repo to pnpm.
you: We dropped Jest for Vitest.

What the agent gets back now:
  What do we use for state?    Zustand
  Which package manager?       pnpm
  What runs our tests?         Vitest

History (kept for audit, never returned as current):
  state_library: Redux [SUPERSEDED] -> Zustand [ACTIVE]
  package_manager: npm [SUPERSEDED] -> pnpm [ACTIVE]
  test_runner: Jest [SUPERSEDED] -> Vitest [ACTIVE]
```

## Run it

```bash
pip install vayl-mcp
python app.py            # offline: no model, no API key
python app.py --live     # extract from the raw sentences with your LLM
```

`--live` uses any OpenAI-compatible endpoint and defaults to a local Ollama model, so nothing has to leave your machine.

## Why the `coding` preset matters

The example enables `VAYL_SLOT_SCHEMA=preset:coding`. The preset declares the slots a codebase has (state library, package manager, test runner, framework and so on), with their common aliases. That does two things:

- **Different phrasings land in one slot.** "state management" and "state library" are the same thing, so the Zustand switch reconciles against Redux instead of opening a second slot beside it.
- **A switch retires the old value even when the model mislabels it.** The pnpm line is extracted as a plain `ADD`, the kind of mistake a small local model makes. Because `package_manager` is a declared single-valued slot, npm is still superseded. This is what makes a 3B local model reliable here (see the [Cursor guide](../../docs/cursor.md)).

## Use it in your own agent

**Cursor / Claude Code (MCP):** add the preset to the server's env in your MCP config:

```json
"env": { "VAYL_SLOT_SCHEMA": "preset:coding", "VAYL_DB": "/absolute/path/vayl.db" }
```

**LangGraph:** the adapter spawns `vayl-mcp`, which inherits the preset from the environment:

```python
import os
os.environ["VAYL_SLOT_SCHEMA"] = "preset:coding"

from vayl.integrations.langgraph import VaylMemory

with VaylMemory(user_id="my-project") as mem:
    agent = mem.agent("openai:gpt-4o-mini")
    agent.invoke({"messages": [("user", "We moved off Redux to Zustand. What do we use for state?")]})
```

The OpenAI Agents SDK and CrewAI adapters follow the same shape (`pip install 'vayl-mcp[langgraph]'`, `[openai-agents]` or `[crewai]`).
