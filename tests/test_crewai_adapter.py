"""CrewAI adapter: Vayl exposed as custom crewai tools. Offline — a fake client stands in. Runs only
where crewai is installed (a heavy dependency); the module's import-safety is guarded separately in
tests/test_integrations_import.py, which runs in CI without crewai."""
import pytest

pytest.importorskip("crewai")

from vayl.integrations._common import DEFAULT_TOOLS  # noqa: E402
from vayl.integrations.crewai import VaylMemory  # noqa: E402


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


def test_include_and_exclude_select_the_surface():
    mem = VaylMemory(client=FakeClient())
    assert [t.name for t in mem.tools(include=["recall"])] == ["recall"]
    assert "forget" not in [t.name for t in mem.tools(exclude=["forget"])]


def test_passthroughs_route_to_the_client():
    fake = FakeClient()
    mem = VaylMemory(client=fake)
    assert mem.recall("q") == "answer: q"
    assert fake.calls[0][0] == "recall"


def test_a_borrowed_client_is_not_closed_on_exit():
    fake = FakeClient()
    with VaylMemory(client=fake):
        pass
    assert fake.closed is False
