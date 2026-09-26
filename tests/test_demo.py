"""The zero-setup `vayl-demo` must always tell the clean reconciliation story in offline mode —
no model, no network — so a first-time user's 30-second impression can't break."""
import sys

import pytest

from vayl import demo


@pytest.fixture(autouse=True)
def _no_local_llm(monkeypatch):
    # the offline demo ends by probing for a local LLM; pin the answer so the test never touches the network
    monkeypatch.setattr(demo, "_llm_reachable", lambda timeout=1.0: False)


def test_demo_suggests_live_mode_only_when_a_local_llm_is_reachable(monkeypatch, capsys):
    demo.run(live=False)
    assert "--live" not in capsys.readouterr().out
    monkeypatch.setattr(demo, "_llm_reachable", lambda timeout=1.0: True)
    demo.run(live=False)
    assert "vayl-demo --live" in capsys.readouterr().out


def test_demo_offline_reconciles_and_keeps_history(capsys):
    demo.run(live=False)
    out = capsys.readouterr().out
    # current truth is the superseding value, not the stale one
    assert "Zustand" in out
    # the old value and the removal are both preserved as history
    assert "Redux" in out
    assert "SUPERSEDED" in out
    assert "retracted" in out.lower()


def test_demo_main_offline_flag(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vayl-demo", "--offline"])
    demo.main()
    assert "reconciling memory" in capsys.readouterr().out.lower()
