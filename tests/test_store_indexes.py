"""Hot-path indexes: `load()` must stay O(active) regardless of a space's history size. We assert the
indexes exist, that an old space-only `idx_space` is migrated away on open, and — the property that
matters — that `load()` returns exactly the active set even with a large history behind it."""
import sqlite3

import pytest

from vayl.storage.store import Store

_INS = ("INSERT INTO statements(tenant_id,user_id,agent_id,run_id,id,slot,subject,value,scope,status,"
        "confidence,raw) VALUES('default',?,'','',?,?,?,?,'global',?,0.9,?)")


@pytest.fixture(autouse=True)
def _plaintext(monkeypatch):
    monkeypatch.setenv("VAYL_ENCRYPT", "off")
    monkeypatch.setenv("VAYL_SIGN", "off")
    monkeypatch.delenv("VAYL_DATABASE_URL", raising=False)


def _indexes(store):
    return {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}


def test_hot_path_indexes_created(tmp_path):
    s = Store(str(tmp_path / "a.db"))
    idx = _indexes(s)
    assert "idx_id" in idx and "idx_hot" in idx
    assert "idx_space" not in idx          # superseded by idx_id


def test_old_space_only_index_is_migrated(tmp_path):
    db = str(tmp_path / "b.db")
    con = sqlite3.connect(db)              # simulate a pre-upgrade DB with the old space-only index
    con.execute("CREATE TABLE statements(user_id TEXT,id INTEGER,slot TEXT,subject TEXT,value TEXT,"
                "scope TEXT,status TEXT,agent_id TEXT DEFAULT '',run_id TEXT DEFAULT '',"
                "tenant_id TEXT DEFAULT 'default')")
    con.execute("CREATE INDEX idx_space ON statements(tenant_id,user_id,agent_id,run_id)")
    con.commit()
    con.close()
    idx = _indexes(Store(db))              # opening migrates it
    assert "idx_space" not in idx and "idx_id" in idx and "idx_hot" in idx


def test_load_returns_only_active_under_large_history(tmp_path):
    db = str(tmp_path / "c.db")
    Store(db)                              # create schema + indexes
    con = sqlite3.connect(db)
    con.executemany(_INS, [("u", i, f"s{i}@g", f"s{i}", f"v{i}", "ACTIVE", f"v{i}") for i in range(10)])
    con.executemany(_INS, [("u", 1000 + i, f"h{i}@g", f"h{i}", f"hv{i}", "SUPERSEDED", f"hv{i}")
                           for i in range(5000)])
    con.commit()
    con.close()
    m = Store(db).load("u")
    assert sorted(s.value for s in m.active()) == sorted(f"v{i}" for i in range(10))
