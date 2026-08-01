# Use Vayl with Cursor

Vayl gives Cursor a reconciling memory: new facts replace stale ones, explicit
retractions are remembered as removals, and history remains queryable. Memory is
model-driven in Cursor, so this setup includes both the MCP server and a project
rule that tells the agent when to use it.

## 1. Install Vayl

```bash
pip install vayl-mcp
```

If you use a virtual environment, make sure Cursor can resolve the `vayl-mcp`
executable. An absolute path to the executable is the most reliable option.

## 2. Configure the MCP server

Put the configuration in either:

- `~/.cursor/mcp.json` to make Vayl available in every project; or
- `<project>/.cursor/mcp.json` to enable it for one project only.

Create the parent directory if it does not exist. Choose one of the following
provider configurations.

### OpenAI

```json
{
  "mcpServers": {
    "vayl": {
      "command": "vayl-mcp",
      "env": {
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-...",
        "OPENAI_MODEL": "gpt-5-mini",
        "EMBED_BASE_URL": "https://api.openai.com/v1",
        "EMBED_MODEL": "text-embedding-3-small",
        "VAYL_DB": "/absolute/path/to/project/.vayl/memory.db"
      }
    }
  }
}
```

### Local Ollama

Pull the chat and embedding models before starting Cursor:

```bash
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
```

Then use this configuration:

```json
{
  "mcpServers": {
    "vayl": {
      "command": "vayl-mcp",
      "env": {
        "LLM_PROVIDER": "openai",
        "OPENAI_BASE_URL": "http://localhost:11434/v1",
        "OPENAI_MODEL": "qwen2.5:3b",
        "EMBED_BASE_URL": "http://localhost:11434/v1",
        "EMBED_MODEL": "nomic-embed-text",
        "VAYL_DB": "/absolute/path/to/project/.vayl/memory.db"
      }
    }
  }
}
```

Ollama does not need an API key. Vayl recognizes loopback OpenAI-compatible
endpoints as local and supplies the placeholder authentication value internally.

Restart Cursor after changing `mcp.json`, then check Cursor's MCP settings to
confirm that the `vayl` server and its tools are available.

### FastMCP alternative

From a Vayl checkout, FastMCP can write the Cursor configuration for you:

```bash
fastmcp install cursor src/vayl/api/mcp_server.py:mcp \
  --with vayl-mcp \
  --env OPENAI_API_KEY=sk-... \
  --env OPENAI_MODEL=gpt-5-mini \
  --env VAYL_DB=/absolute/path/to/project/.vayl/memory.db
```

For local Ollama, omit `OPENAI_API_KEY` and pass the local base URL and model
instead:

```bash
fastmcp install cursor src/vayl/api/mcp_server.py:mcp \
  --with vayl-mcp \
  --env OPENAI_BASE_URL=http://localhost:11434/v1 \
  --env OPENAI_MODEL=qwen2.5:3b \
  --env VAYL_DB=/absolute/path/to/project/.vayl/memory.db
```

## 3. Tell Cursor when to use memory

Create `<project>/.cursor/rules/vayl.mdc`:

```markdown
---
description: Use Vayl to keep project memory current
alwaysApply: true
---

- Call `recall` before answering when stored project context could affect the answer.
- Call `remember` when the user states a durable fact, changes a fact, or retracts one.
- Prefer recalled current facts over stale assumptions, and ask when a conflict remains unclear.
```

Cursor decides when to call MCP tools, so the rule is important: installing the
server alone does not guarantee automatic recall or storage.

## Keep projects isolated

Use a different absolute `VAYL_DB` path for each project. Two projects that point
at the same database share memory, even if each has its own `.cursor/mcp.json`.
Keeping the database under a project-specific `.vayl/` directory makes that
boundary visible; add the directory to `.gitignore` so personal memory is not
committed.

## Model quality

Reconciliation asks the model to distinguish additions, corrections, and
retractions. Capable models handle those decisions more reliably. Small local
models can mislabel subtle changes, so review important memories or choose a
stronger local/cloud model when correctness matters.
