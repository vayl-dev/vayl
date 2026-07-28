"""The zero-setup `vayl-demo` must always tell the clean reconciliation story in offline mode —
no model, no network — so a first-time user's 30-second impression can't break."""
import sys

from vayl import demo


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
