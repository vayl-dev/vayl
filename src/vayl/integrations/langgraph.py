"""LangGraph / LangChain adapter — give an agent Vayl's *reconciling* long-term memory.

Vayl is exposed as LangChain tools the agent can call — `remember`, `recall`, `history`, `forget`,
`list_memories`. Because Vayl reconciles (a new value *supersedes* the old for the same thing, and
"we dropped X" actually *retracts* X), the agent's memory stays current instead of piling up stale
facts the way a plain vector store does.

    from vayl.integrations.langgraph import VaylMemory

    with VaylMemory(user_id="proj_7") as mem:
        agent = mem.agent("openai:gpt-4o-mini")          # a ready agent wired to memory
        agent.invoke({"messages": [("user", "We moved off Redux to Zustand. What do we use now?")]})

    # or bind the tools onto your own agent / graph:
    with VaylMemory(user_id="proj_7") as mem:
        from langchain.agents import create_agent
        agent = create_agent("openai:gpt-4o-mini", tools=mem.tools())

Why tools, not a `BaseStore`: LangGraph's long-term-memory store is an exact key→value store — a
`get` after a `put` must hand back the same value. Vayl deliberately *reconciles* what you write, so
forcing it behind that contract would hide its whole point. Memory-as-tools-the-model-calls is the
honest fit, and it's exactly how LangChain/LangGraph's own long-term-memory pattern reads/writes.

Why not just `langchain-mcp-adapters`: Vayl is an MCP server, so `MultiServerMCPClient` can load its
tools directly — but that path is stateless by default (a fresh `vayl-mcp` process per tool call) and
surfaces ALL 30+ tools, including admin tools and the `user_id` scope arg the model could then set.
This adapter keeps one persistent session, exposes a curated memory surface, and binds scope
server-side so the model can't touch it. Reach for the raw adapters when you want the full toolset.
"""
from vayl.integrations._common import DEFAULT_SYSTEM, TOOL_DESCRIPTIONS, BaseVaylMemory

_MISSING = ("The LangGraph adapter needs langchain-core (and langchain / langgraph for the agent() "
            "helper). Install the extra:  pip install 'vayl-mcp[langgraph]'")


class VaylMemory(BaseVaylMemory):
    """Reconciling long-term memory for LangGraph / LangChain agents, backed by Vayl.

    Exposes Vayl as LangChain `tools()` for an agent to call, plus the base `remember` / `recall` /
    `history` / … methods for driving memory directly from your own graph nodes.
    """

    def tools(self, include=None, exclude=()):
        """Build LangChain `StructuredTool`s over the bound memory. `include` picks a subset (default:
        remember/recall/history/forget/list_memories); `exclude` drops names. No tool takes a scope
        argument — the caller's identity stays server-side."""
        try:
            from langchain_core.tools import StructuredTool
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
        return [StructuredTool.from_function(func=fns[n], name=n, description=TOOL_DESCRIPTIONS[n])
                for n in self._selected(include, exclude)]

    def agent(self, model, *, system_prompt=None, tools=None, **kwargs):
        """Return a ready agent wired to this memory. `model` is anything the agent factory accepts
        (e.g. \"openai:gpt-4o-mini\" or a chat-model instance); extra `tools` are appended to the memory
        tools; `system_prompt` overrides the default memory-aware prompt; remaining kwargs pass through.

        Prefers LangChain v1's `langchain.agents.create_agent`; falls back to the older
        `langgraph.prebuilt.create_react_agent` on pre-v1 installs. Requires the [langgraph] extra."""
        prompt = DEFAULT_SYSTEM if system_prompt is None else system_prompt
        all_tools = self.tools() + list(tools or [])
        try:
            from langchain.agents import create_agent  # LangChain v1
        except ImportError:
            create_agent = None
        if create_agent is not None:
            return create_agent(model, tools=all_tools, system_prompt=prompt, **kwargs)
        try:
            from langgraph.prebuilt import create_react_agent  # pre-v1 fallback
        except ImportError as e:
            raise ImportError(_MISSING) from e
        return create_react_agent(model, tools=all_tools, prompt=prompt, **kwargs)
