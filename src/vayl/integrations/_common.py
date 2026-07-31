"""Shared pieces for the framework integrations.

Every adapter (langgraph, openai_agents, crewai, …) exposes the SAME curated memory surface — the same
tool names, the same model-facing descriptions, and the same memory-aware system prompt — so an agent
behaves identically no matter which framework wraps Vayl. Those live here; each adapter only translates
them into its framework's tool type.

The caller's scope (`user_id` / `agent_id` / `run_id`) is bound on the client and is NEVER a tool
argument, so the model can neither see nor choose whose memory it touches.
"""
from vayl.client import Vayl

# The curated surface an agent should drive. Vayl exposes 30+ tools; these are the ones an autonomous
# agent actually needs to keep and use memory. Adapters let callers widen/narrow this per instance.
DEFAULT_TOOLS = ("remember", "recall", "history", "forget", "list_memories")

# name → the model-facing description. Written FOR the model: when to reach for each tool.
TOOL_DESCRIPTIONS = {
    "remember": (
        "Save fact(s) from a statement to long-term memory. Vayl RECONCILES: a new value supersedes "
        "the old one for the same thing, and 'we dropped / stopped using X' retracts X — so memory "
        "stays current instead of accumulating contradictions. Call this whenever the user states a "
        "durable fact, decision, or preference, or changes one."),
    "recall": (
        "Answer a question from long-term memory. Returns the CURRENT value and says \"I don't know\" "
        "rather than guessing. Call this before answering anything that depends on what was said, "
        "chosen, or preferred earlier."),
    "history": (
        "Show the full change-log for a subject — every value it has held, oldest to newest, with "
        "status. Use for 'what did we use before / what changed' questions."),
    "forget": (
        "Retract a fact: it leaves the active set but stays in history. Use when the user says "
        "something is no longer true and gives no replacement value."),
    "list_memories": (
        "List the current active facts in memory (with #ids). Use to review what is known."),
}

DEFAULT_SYSTEM = (
    "You are an assistant with a persistent, reconciling long-term memory. "
    "Before answering anything that depends on earlier facts, decisions, or preferences, call `recall`. "
    "Whenever the user states — or changes — a durable fact, decision, or preference, call `remember` "
    "so the new value replaces the old one. Trust memory over your own assumptions, and say you don't "
    "know rather than guessing when memory doesn't have it.")


class BaseVaylMemory:
    """Connection + lifecycle shared by every framework adapter.

    Wraps a `vayl.client.Vayl` connection (spawned locally over stdio, or pointed at a shared team
    server). Subclasses add a `tools()` method that renders the curated surface into their framework's
    tool type, and usually an `agent()` convenience. Plain `remember` / `recall` / `history` methods
    are provided here for driving memory directly from your own code.
    """

    def __init__(self, client=None, *, user_id="default", agent_id="", run_id="",
                 url=None, api_key=None, command="vayl-mcp", args=None, env=None):
        """Bind memory to a scope. Pass an existing `client=Vayl(...)` to reuse a connection (it is then
        NOT closed on exit); otherwise a client is opened from the given scope / connection arguments
        and closed when this object is closed."""
        if client is None:
            client = Vayl(url=url, api_key=api_key, command=command, args=args, env=env,
                          user_id=user_id, agent_id=agent_id, run_id=run_id)
            self._owns_client = True
        else:
            self._owns_client = False   # borrowed — leave lifecycle to the caller
        self.client = client

    # ── lifecycle (only closes what we opened) ──
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._owns_client:
            self.client.close()

    # ── plain passthroughs, for driving memory from your own code ──
    def remember(self, text, **kw):
        """Store fact(s) from a statement — reconciled (supersede / retract) automatically."""
        return self.client.remember(text, **kw)

    def recall(self, question, **kw):
        """Answer a question from active memory, or 'I don't know'."""
        return self.client.recall(question, **kw)

    def history(self, subject, **kw):
        """The full change-log for a subject, oldest → newest."""
        return self.client.call("history", subject=subject, **kw)

    def forget(self, text, **kw):
        """Retract a fact — retired from the active set but kept in history."""
        return self.client.forget(text, **kw)

    def list_memories(self, **kw):
        """The current active facts (with #ids)."""
        return self.client.call("list_memories", **kw)

    def _selected(self, include, exclude):
        """The tool names to build for this call, honoring include/exclude (validates names)."""
        exclude = set(exclude or ())
        names = list(include) if include else list(DEFAULT_TOOLS)
        return [n for n in names if n not in exclude and n in TOOL_DESCRIPTIONS]
