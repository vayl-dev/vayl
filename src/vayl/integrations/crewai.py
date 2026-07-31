"""CrewAI adapter — give an agent Vayl's *reconciling* long-term memory as custom tools.

    from vayl.integrations.crewai import VaylMemory
    from crewai import Agent, Crew, Task, Process

    with VaylMemory(user_id="proj_7") as mem:
        analyst = Agent(role="Analyst", goal="Answer with current facts",
                        backstory="Uses long-term memory.", tools=mem.tools())
        # ... build tasks / a Crew as usual ...

Why tools, not CrewAI's `Memory`: CrewAI's unified `Memory` is an *automatic* pipeline — it extracts
facts from task output and injects retrieved context into prompts; it is not exposed to the agent as
callable actions. Vayl is memory the agent should *deliberately* read and write (remember/recall) with
reconciliation under your control, so each operation is a custom tool. (To instead back CrewAI's
automatic memory transparently, implement its `StorageBackend` — a different integration.)

Note: CrewAI's v0.x `ExternalMemory` class was removed in v1.x — this adapter targets the v1 tools API
(`from crewai.tools import tool`). Scope (`user_id` / `agent_id` / `run_id`) is bound on the client —
never a tool argument the model can see or set.
"""
from vayl.integrations._common import DEFAULT_SYSTEM, TOOL_DESCRIPTIONS, BaseVaylMemory

_MISSING = ("The CrewAI adapter needs the crewai package. "
            "Install the extra:  pip install 'vayl-mcp[crewai]'")


class VaylMemory(BaseVaylMemory):
    """Reconciling long-term memory for CrewAI agents, backed by Vayl."""

    def tools(self, include=None, exclude=()):
        """Build CrewAI tools over the bound memory. `include`/`exclude` pick the subset. The curated
        description becomes each tool's description (what the agent uses to decide when to call it); no
        tool takes a scope argument — the caller's identity stays server-side."""
        try:
            from crewai.tools import tool as crew_tool
        except ImportError as e:
            raise ImportError(_MISSING) from e
        client = self.client

        def remember(text: str) -> str:
            return client.remember(text)

        def recall(question: str) -> str:
            return client.recall(question)

        def history(subject: str) -> str:
            return client.call("history", subject=subject)

        def forget(text: str) -> str:
            return client.forget(text)

        def list_memories() -> str:
            return client.call("list_memories")

        fns = {"remember": remember, "recall": recall, "history": history,
               "forget": forget, "list_memories": list_memories}
        out = []
        for n in self._selected(include, exclude):
            fn = fns[n]
            fn.__doc__ = TOOL_DESCRIPTIONS[n]   # the @tool decorator reads the description off the docstring
            out.append(crew_tool(n)(fn))
        return out

    def agent(self, *, role="Memory-aware assistant", goal=None, backstory=None, llm=None,
              tools=None, **kwargs):
        """Return a `crewai.Agent` wired to this memory. `role` / `goal` / `backstory` default to
        memory-aware text (override any of them); `llm` sets the model (omit for CrewAI's default);
        extra `tools` are appended; remaining kwargs pass through. Requires the [crewai] extra."""
        try:
            from crewai import Agent
        except ImportError as e:
            raise ImportError(_MISSING) from e
        opts = dict(role=role,
                    goal=goal or "Answer accurately from long-term memory and keep it current.",
                    backstory=backstory or DEFAULT_SYSTEM,
                    tools=self.tools() + list(tools or []))
        if llm is not None:
            opts["llm"] = llm
        opts.update(kwargs)
        return Agent(**opts)
