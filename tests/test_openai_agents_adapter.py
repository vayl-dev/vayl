"""OpenAI Agents SDK adapter: Vayl exposed as function tools. Offline — a fake client stands in for a
live vayl-mcp connection. We assert the tool surface, the curated descriptions, that scope is never a
tool parameter, that a borrowed client is left open, and that agent() wires the memory tools in."""
import pytest

pytest.importorskip("agents")   # the openai-agents package

from vayl.integrations._common import DEFAULT_TOOLS  # noqa: E402
from vayl.integrations.openai_agents import VaylMemory  # noqa: E402


class FakeClient:
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
    assert [t.name for t in VaylMemory(client=FakeClient()).tools()] == list(DEFAULT_TOOLS)


def test_tools_carry_the_curated_descriptions():
    tools = _by_name(VaylMemory(client=FakeClient()).tools())
    assert "reconcile" in tools["remember"].description.lower()
    assert all(t.description for t in tools.values())


def test_scope_is_never_a_tool_parameter():
    for t in VaylMemory(client=FakeClient()).tools():
        props = (t.params_json_schema or {}).get("properties", {})
        assert not ({"user_id", "agent_id", "run_id"} & set(props)), t.name


def test_include_and_exclude_select_the_surface():
    mem = VaylMemory(client=FakeClient())
    assert [t.name for t in mem.tools(include=["recall"])] == ["recall"]
    assert "forget" not in [t.name for t in mem.tools(exclude=["forget"])]


def test_passthroughs_route_to_the_client():
    fake = FakeClient()
    mem = VaylMemory(client=fake)
    assert mem.remember("x") == "remembered: x"
    assert mem.recall("q") == "answer: q"
    assert [c[0] for c in fake.calls] == ["remember", "recall"]


def test_a_borrowed_client_is_not_closed_on_exit():
    fake = FakeClient()
    with VaylMemory(client=fake):
        pass
    assert fake.closed is False


def test_agent_wires_the_memory_tools():
    agent = VaylMemory(client=FakeClient()).agent(model="gpt-4o")
    names = {t.name for t in agent.tools}
    assert {"remember", "recall", "history", "forget", "list_memories"} <= names
