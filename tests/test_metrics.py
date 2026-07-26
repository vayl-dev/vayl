"""
On-device metrics (`metrics.Metrics`) — offline, deterministic.

Tracks tool calls / latency / errors and the reconciliation-action distribution,
persisted in SQLite so it survives a restart.
"""
import sqlite3

from vayl.telemetry.metrics import Metrics


def test_record_call_counts_latency_and_errors():
    m = Metrics(sqlite3.connect(":memory:"))
    m.record_call("remember", 100.0, ok=True)
    m.record_call("remember", 200.0, ok=True)
    m.record_call("remember", 300.0, ok=False)
    snap = m.snapshot()["tools"]["remember"]
    assert snap["calls"] == 3
    assert snap["errors"] == 1
    assert snap["avg_ms"] == 200.0            # (100+200+300)/3


def test_record_actions_distribution():
    m = Metrics(sqlite3.connect(":memory:"))
    m.record_actions(["ADD", "SUPERSEDE", "ADD", "FLAG"])
    m.record_actions(["RETRACT"])
    assert m.snapshot()["actions"] == {"ADD": 2, "SUPERSEDE": 1, "FLAG": 1, "RETRACT": 1}


def test_empty_snapshot():
    snap = Metrics(sqlite3.connect(":memory:")).snapshot()
    assert snap == {"tools": {}, "actions": {}}


def test_tools_and_actions_do_not_leak_into_each_other():
    m = Metrics(sqlite3.connect(":memory:"))
    m.record_call("recall", 50.0)
    m.record_actions(["SKIP"])
    snap = m.snapshot()
    assert list(snap["tools"]) == ["recall"]
    assert snap["actions"] == {"SKIP": 1}


def test_metrics_persist_across_reload(tmp_path):
    db = str(tmp_path / "vayl.db")
    m = Metrics(sqlite3.connect(db))
    m.record_call("remember", 120.0, ok=True)
    m.record_actions(["SUPERSEDE"])

    # a fresh Metrics on the same file sees the persisted counters
    reloaded = Metrics(sqlite3.connect(db)).snapshot()
    assert reloaded["tools"]["remember"]["calls"] == 1
    assert reloaded["actions"] == {"SUPERSEDE": 1}


def test_counters_accumulate_across_instances(tmp_path):
    db = str(tmp_path / "vayl.db")
    Metrics(sqlite3.connect(db)).record_call("recall", 10.0)
    Metrics(sqlite3.connect(db)).record_call("recall", 30.0)
    snap = Metrics(sqlite3.connect(db)).snapshot()["tools"]["recall"]
    assert snap["calls"] == 2 and snap["avg_ms"] == 20.0


# ── error observability ──────────────────────────────────────────────────────

def test_recent_errors_newest_first():
    m = Metrics(sqlite3.connect(":memory:"))
    m.record_error("remember", "TimeoutError", "llm timed out")
    m.record_error("recall", "KeyError", "OPENAI_API_KEY")
    errs = m.recent_errors(5)
    assert errs[0] == {"tool": "recall", "type": "KeyError", "msg": "OPENAI_API_KEY"}
    assert len(errs) == 2


def test_errors_are_capped_at_50():
    m = Metrics(sqlite3.connect(":memory:"))
    for i in range(60):
        m.record_error("t", "E", f"err{i}")
    errs = m.recent_errors(100)
    assert len(errs) == 50                 # oldest 10 pruned
    assert errs[0]["msg"] == "err59"       # most recent kept
