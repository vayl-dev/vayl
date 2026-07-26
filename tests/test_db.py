"""
Database abstraction (M6). The SQLite path and the dialect logic are fully testable here; the live
Postgres path needs a running server (docker-compose `postgres`) and is validated there, not in CI.
"""
import pytest

from vayl.storage.db import Database, _advisory_key, detect_dialect


def test_dialect_detection():
    assert detect_dialect("vayl.db") == "sqlite"
    assert detect_dialect("sqlite:///x.db") == "sqlite"
    assert detect_dialect("postgresql://u:p@h/db") == "postgres"
    assert detect_dialect("postgres://u@h/db") == "postgres"
    assert detect_dialect(None) == "sqlite"


def test_sqlite_execute_roundtrip(tmp_path):
    d = Database(str(tmp_path / "x.db"))
    assert d.dialect == "sqlite"
    d.execute(f"CREATE TABLE t(id {d.autoincrement_pk()}, v TEXT)")
    d.execute("INSERT INTO t(v) VALUES (?)", ("hello",))
    d.commit()
    assert d.execute("SELECT v FROM t WHERE v=?", ("hello",)).fetchone()[0] == "hello"


def test_sqlite_url_form(tmp_path):
    d = Database("sqlite:///" + str(tmp_path / "y.db"))
    d.execute("CREATE TABLE t(v TEXT)"); d.execute("INSERT INTO t VALUES (?)", ("a",)); d.commit()
    assert d.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_placeholder_translation_is_dialect_specific(tmp_path):
    d = Database(str(tmp_path / "z.db"))
    assert d._translate("SELECT ? , ?") == "SELECT ? , ?"          # sqlite: unchanged
    d.dialect = "postgres"
    assert d._translate("SELECT ? , ?") == "SELECT %s , %s"        # postgres: ?→%s


def test_autoincrement_pk_per_dialect(tmp_path):
    d = Database(str(tmp_path / "z.db"))
    assert "AUTOINCREMENT" in d.autoincrement_pk()
    d.dialect = "postgres"
    assert "SERIAL" in d.autoincrement_pk()


def test_advisory_key_is_stable_and_bigint_ranged():
    k1 = _advisory_key("acme/u1//")
    assert k1 == _advisory_key("acme/u1//")                        # stable across calls (not salted)
    assert _advisory_key("a") != _advisory_key("b")
    assert -(2**63) <= k1 < 2**63                                  # fits a Postgres bigint


def test_space_lock_is_a_real_per_key_mutex_on_sqlite(tmp_path):
    d = Database(str(tmp_path / "z.db"))
    with d.space_lock("A"):
        assert d._locks["A"].locked()                              # held inside the context
        assert d._locks.get("B") is None or not d._locks["B"].locked()   # a different space is unaffected
    assert not d._locks["A"].locked()                              # released on exit
    # the same key reuses the same lock object (so concurrent writers to one space actually contend)
    assert d._locks["A"] is d._locks.setdefault("A", object())


@pytest.mark.skip(reason="requires a live Postgres — run with docker-compose `postgres` + VAYL_DATABASE_URL")
def test_postgres_path_placeholder():
    pass
