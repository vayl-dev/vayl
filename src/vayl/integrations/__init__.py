"""Framework integrations for Vayl — optional, each behind its own extra.

These wrap the built-in `vayl.client.Vayl` in the shape a given agent framework expects, so an existing
app gets Vayl's *reconciling* memory (a new value supersedes the old; "we dropped X" retracts X) without
changing its core logic. Every adapter exposes the SAME curated memory surface (see `_common`), binds
the caller's scope server-side (never a tool the model can set), and imports its framework lazily — so
`import vayl.integrations.<name>` is safe even when the framework isn't installed, and calling `tools()`
raises a clear install hint if it's missing.

  • langgraph      →  from vayl.integrations.langgraph import VaylMemory       (extra: vayl-mcp[langgraph])
  • openai_agents  →  from vayl.integrations.openai_agents import VaylMemory   (extra: vayl-mcp[openai-agents])
  • crewai         →  from vayl.integrations.crewai import VaylMemory          (extra: vayl-mcp[crewai])

TypeScript agents (Vercel AI SDK, Mastra) are served from the TS client — see clients/typescript.
"""
