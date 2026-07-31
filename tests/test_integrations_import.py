"""Every integration module must import WITHOUT its agent framework installed. Adapters import their
framework lazily (inside tools()/agent()), so importing the module is always safe — this guards that
property in CI, where the heavy frameworks (langchain, openai-agents, crewai) aren't present. Per-adapter
behavior is covered by the framework-gated tests."""
import importlib

import pytest


@pytest.mark.parametrize("mod", ["_common", "langgraph", "openai_agents", "crewai"])
def test_integration_module_imports_without_its_framework(mod):
    importlib.import_module(f"vayl.integrations.{mod}")


def test_shared_surface_is_consistent():
    from vayl.integrations._common import DEFAULT_TOOLS, TOOL_DESCRIPTIONS
    assert set(DEFAULT_TOOLS) == set(TOOL_DESCRIPTIONS)     # every default tool has a description
    assert all(TOOL_DESCRIPTIONS.values())                  # and none is empty
