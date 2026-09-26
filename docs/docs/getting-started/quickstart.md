---
description: >-
  Connect Vayl to your MCP client and store your first fact — in about 5
  minutes.
icon: bolt
---

# Quickstart

**Go from install to your first reconciled memory in about 5 minutes.** You'll install Vayl, point your MCP client at it, and watch a fact get _superseded_ live.

{% stepper %}
{% step %}
### Install

```bash
pip install vayl-mcp
vayl-demo          # 30s, no keys — watch reconciling memory in action
```

`vayl-demo` runs the real engine on a scripted conversation and shows a value getting superseded — the fastest way to see what Vayl does before any config.
{% endstep %}

{% step %}
### Configure your MCP client

Add Vayl to your client's MCP configuration. The command is `vayl-mcp`; everything else is passed through `env`.

{% tabs %}
{% tab title="OpenAI" %}
```json
{
  "mcpServers": {
    "vayl": {
      "command": "vayl-mcp",
      "env": {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-…",
        "OPENAI_MODEL": "gpt-5-mini",
        "EMBED_BASE_URL": "https://api.openai.com/v1",
        "EMBED_MODEL": "text-embedding-3-small",
        "VAYL_DB": "/absolute/path/vayl.db"
      }
    }
  }
}
```
{% endtab %}

{% tab title="Local model (Ollama)" %}
```json
{
  "mcpServers": {
    "vayl": {
      "command": "vayl-mcp",
      "env": {
        "OPENAI_BASE_URL": "http://localhost:11434/v1",
        "OPENAI_MODEL": "qwen2.5:3b",
        "VAYL_DB": "/absolute/path/vayl.db"
      }
    }
  }
}
```

Nothing leaves your machine with a local model.
{% endtab %}
{% endtabs %}

{% hint style="success" %}
**Prefer one command to editing JSON?** Vayl is a [FastMCP](https://gofastmcp.com) server, so `fastmcp install` writes the config for you:

```bash
fastmcp install claude-desktop src/vayl/api/mcp_server.py:mcp \
  --with vayl-mcp --env OPENAI_API_KEY=sk-… --env VAYL_DB=/absolute/path/vayl.db
```

Targets: `claude-desktop` · `claude-code` · `cursor` · `gemini-cli` · `mcp-json` (prints/copies the config for any client).
{% endhint %}

{% hint style="info" %}
`VAYL_DB` is where memory persists — use an absolute path. Any OpenAI-compatible endpoint works via `OPENAI_BASE_URL`. See Configuration for every variable.
{% endhint %}
{% endstep %}

{% step %}
### Restart and store your first fact

Restart your client so it loads the server. Your agent now has Vayl's tools. Have this conversation:

> **You:** Remember that we use Redux for state management.
>
> **You:** Actually, we switched to Zustand.
>
> **You:** What do we use for state?

Vayl answers **"Zustand"**. The switch **superseded** Redux — it's retired to history, not returned as current.

Behind those turns your agent called two tools. Here's exactly what Vayl returned:

```
remember("Actually, we switched to Zustand")
  → Stored: [SUPERSEDE] state = Zustand

recall("what do we use for state?")
  → Zustand
```

Ask _"what did we use before?"_ and Vayl answers **Redux**, from history.
{% endstep %}

{% step %}
### Confirm it's healthy

Ask your agent to run the `health()` tool. It checks that the database, embedder, and LLM are reachable — and if something is off, it reports which piece.
{% endstep %}
{% endstepper %}

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-compass" style="color:$primary;">:compass:</i> Your first memory</h4></td><td>A fuller walkthrough — store, correct, retract, flag, and time-travel over facts.</td><td></td></tr><tr><td><h4><i class="fa-code" style="color:$primary;">:code:</i> Calling Vayl from code</h4></td><td>The Python and TypeScript clients, and raw MCP.</td><td></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i> Core concepts</h4></td><td>The reconciliation model: supersede, retract, and the same-slot invariant.</td><td></td></tr></tbody></table>
