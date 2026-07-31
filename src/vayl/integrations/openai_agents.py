"""OpenAI Agents SDK adapter — give an agent Vayl's *reconciling* long-term memory as function tools.

    from vayl.integrations.openai_agents import VaylMemory
    from agents import Runner

    with VaylMemory(user_id="proj_7") as mem:
        agent = mem.agent(model="gpt-4o")                 # or: Agent(..., tools=mem.tools())
        result = Runner.run_sync(agent, "We moved off Redux to Zustand. What do we use now?")
        print(result.final_output)

Why function tools, not a Session: the SDK's `Session` stores and replays one thread's conversation
items — short-term memory. Vayl is long-term memory the agent should *deliberately* read and write, so
each operation is a `@function_tool`. Use both together if you like: a `Session` for within-conversation
continuity, Vayl tools for cross-session recall.

Scope (`user_id` / `agent_id` / `run_id`) is bound on the client — never a tool argument the model can
see or set.
"""
from vayl.integrations._common import DEFAULT_SYSTEM, TOOL_DESCRIPTIONS, BaseVaylMemory

_MISSING = ("The OpenAI Agents adapter needs the openai-agents package. "
            "Install the extra:  pip install 'vayl-mcp[openai-agents]'")


class VaylMemory(BaseVaylMemory):
    """Reconciling long-term memory for OpenAI Agents SDK agents, backed by Vayl."""

    def tools(self, include=None, exclude=()):
        """Build OpenAI Agents SDK function tools over the bound memory. `include`/`exclude` pick the
        subset; the model-facing name and description are set explicitly, and no tool takes a scope
        argument (the caller's identity stays server-side)."""
        try:
            from agents import function_tool
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
        # use_docstring_info=False: description is set explicitly, so don't make griffe parse a docstring.
        return [function_tool(fns[n], name_override=n, description_override=TOOL_DESCRIPTIONS[n],
                              use_docstring_info=False)
                for n in self._selected(include, exclude)]

    def agent(self, *, name="Assistant", model=None, instructions=None, tools=None, **kwargs):
        """Return an `agents.Agent` wired to this memory. `instructions` overrides the default
        memory-aware system prompt; `model` is a model-name string or Model object (omit for the SDK
        default); extra `tools` are appended; remaining kwargs pass through. Requires the
        [openai-agents] extra."""
        try:
            from agents import Agent
        except ImportError as e:
            raise ImportError(_MISSING) from e
        opts = dict(name=name, instructions=DEFAULT_SYSTEM if instructions is None else instructions,
                    tools=self.tools() + list(tools or []))
        if model is not None:
            opts["model"] = model
        opts.update(kwargs)
        return Agent(**opts)
