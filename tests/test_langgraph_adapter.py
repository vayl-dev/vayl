"""LangGraph adapter: Vayl exposed as LangChain tools an agent can call. We assert the tool surface,
that invoking a tool routes to the underlying client with the right args and returns its result, that
the caller's scope is never a tool parameter (the model can't set whose memory it touches), and that
a borrowed client is left open. Offline — a fake client stands in for a live vayl-mcp connection."""
import pytest

pytest.importorskip("langchain_core")   # the adapter's tools need langchain-core

from vayl.integrations._common import DEFAULT_TOOLS  # noqa: E402
from vayl.integrations.langgraph import VaylMemory  # noqa: E402


class FakeClient:
    """Records calls instead of spawning vayl-mcp; mirrors the Vayl client surface the adapter uses."""
    def __init__(self):
        self.calls = []
        self.closed = False

    def remember(self, text, **kw):
        self.calls.append(("remember", text, kw)); return f"remembered: {text}"

    def recall(self, question, **kw):
        self.calls.append(("recall", question, kw)); return f"answer: {question}"

    def forget(self, text, **kw):
        self.calls.append(("forget", text, kw)); return f"forgot: {text}"

    def call(self, tool, **kw):
        self.calls.append((tool, None, kw)); return f"{tool}: {kw}"

    def close(self):
        self.closed = True


def _by_name(tools):
    return {t.name: t for t in tools}


def test_default_tools_have_the_expected_names():
    mem = VaylMemory(client=FakeClient())
    assert [t.name for t in mem.tools()] == list(DEFAULT_TOOLS)


def test_remember_tool_routes_to_the_client_and_returns_its_result():
    fake = FakeClient()
    tool = _by_name(VaylMemory(client=fake).tools())["remember"]
    out = tool.invoke({"text": "We moved off Redux to Zustand"})
    assert out == "remembered: We moved off Redux to Zustand"
    assert fake.calls == [("remember", "We moved off Redux to Zustand", {})]


def test_recall_tool_routes_to_the_client():
    fake = FakeClient()
    tool = _by_name(VaylMemory(client=fake).tools())["recall"]
    assert tool.invoke({"question": "what do we use?"}) == "answer: what do we use?"
    assert fake.calls == [("recall", "what do we use?", {})]


def test_history_tool_routes_through_the_generic_call():
    fake = FakeClient()
    tool = _by_name(VaylMemory(client=fake).tools())["history"]
    tool.invoke({"subject": "state_library"})
    assert fake.calls == [("history", None, {"subject": "state_library"})]


def test_list_memories_tool_takes_no_arguments():
    fake = FakeClient()
    tool = _by_name(VaylMemory(client=fake).tools())["list_memories"]
    assert tool.args == {}                       # nothing for the model to fill in
    tool.invoke({})
    assert fake.calls == [("list_memories", None, {})]


def test_scope_is_never_a_tool_parameter():
    # The model must not be able to choose user_id/agent_id/run_id — the caller binds it server-side.
    for tool in VaylMemory(client=FakeClient()).tools():
        assert not ({"user_id", "agent_id", "run_id"} & set(tool.args)), tool.name


def test_include_and_exclude_select_the_surface():
    mem = VaylMemory(client=FakeClient())
    assert [t.name for t in mem.tools(include=["recall"])] == ["recall"]
    assert "forget" not in [t.name for t in mem.tools(exclude=["forget"])]


def test_a_borrowed_client_is_not_closed_on_exit():
    fake = FakeClient()
    with VaylMemory(client=fake):
        pass
    assert fake.closed is False                  # we didn't open it, so we don't close it


def test_agent_helper_reports_a_clear_error_without_a_framework():
    # With neither langchain (create_agent) nor langgraph (create_react_agent) installed, agent()
    # must fail with an actionable install hint rather than a bare ImportError.
    mem = VaylMemory(client=FakeClient())
    have_lc = have_lg = True
    try:
        import langchain.agents  # noqa: F401
    except ImportError:
        have_lc = False
    try:
        import langgraph.prebuilt  # noqa: F401
    except ImportError:
        have_lg = False
    if not have_lc and not have_lg:
        with pytest.raises(ImportError, match="langgraph"):
            mem.agent("openai:gpt-4o-mini")


def test_agent_helper_builds_an_agent_when_langchain_is_present():
    pytest.importorskip("langchain.agents")   # runs where the [langgraph] extra is installed
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    agent = VaylMemory(client=FakeClient()).agent(GenericFakeChatModel(messages=iter([])))
    assert hasattr(agent, "invoke")           # a compiled agent wired to the memory tools (not run — no LLM)
