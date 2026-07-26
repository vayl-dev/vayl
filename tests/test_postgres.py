"""
Postgres backend integration (M6). Skipped unless VAYL_TEST_DATABASE_URL points at a real Postgres
(e.g. the docker-compose `postgres` service). Exercises every storage module on the live backend, so
the port stays validated whenever a Postgres is available:

    VAYL_TEST_DATABASE_URL=postgresql://vayl:vayl@localhost:5432/vayl pytest tests/test_postgres.py
"""
import os
import threading

import pytest

PG = os.environ.get("VAYL_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG, reason="set VAYL_TEST_DATABASE_URL to run Postgres integration")


@pytest.fixture()
def pg(monkeypatch):
    monkeypatch.setenv("VAYL_DATABASE_URL", PG)
    monkeypatch.setenv("VAYL_ENCRYPT", "on")
    from vayl.memory import llm_memory
    from vayl.storage import store as store_mod
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[0.0] for _ in texts])
    monkeypatch.setattr(llm_memory, "_embed", lambda texts: [[0.0] for _ in texts])
    from vayl.storage.db import Database
    d = Database(PG)
    for t in ("statements", "space_config", "audit", "decisions", "receipts",
              "principals", "metrics", "metric_errors"):
        d.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    d.commit()
    return PG


def _fact(action="ADD", subject="refund_policy", value="30-day", target_id=None):
    o = {"action": action, "subject": subject, "value": value, "scope": "global",
         "confidence": 0.95, "time_ref": "present"}
    if target_id is not None:
        o["target_id"] = target_id
    return o


def test_store_supersede_history_and_delete_on_postgres(pg):
    from vayl.memory.llm_memory import LLMMemory
    from vayl.memory.reconcile import Status
    from vayl.storage.store import Store

    m = LLMMemory(); m._apply(_fact(value="30-day"), "x", source="handbook")
    Store().save("acme", m)
    m2 = Store().load("acme")
    assert [s.value for s in m2.active()] == ["30-day"] and m2.active()[0].source == "handbook"

    m2._apply(_fact(action="SUPERSEDE", value="14-day", target_id=m2.active()[0].id), "x")
    Store().save("acme", m2)
    hist = {s.value: s.status for s in Store().history_rows("acme", "refund_policy")}
    assert hist == {"30-day": Status.SUPERSEDED, "14-day": Status.ACTIVE}
    assert Store().delete("acme", "refund_policy") == 2 and Store().load("acme").active() == []


def test_tenant_isolation_on_postgres(pg):
    from vayl.memory.llm_memory import LLMMemory
    from vayl.storage.store import Store
    a = LLMMemory(); a._apply(_fact(value="AcmeSecret"), "x"); Store(tenant_id="t-a").save("u1", a)
    b = LLMMemory(); b._apply(_fact(value="GlobexSecret"), "x"); Store(tenant_id="t-b").save("u1", b)
    assert [s.value for s in Store(tenant_id="t-a").load("u1").active()] == ["AcmeSecret"]
    assert [s.value for s in Store(tenant_id="t-b").load("u1").active()] == ["GlobexSecret"]
    assert Store(tenant_id="t-c").load("u1").active() == []


def test_audit_decisions_receipts_auth_metrics_on_postgres(pg):
    from vayl.auth.auth import Auth, Role
    from vayl.licensing.receipts import Receipts, make_receipt
    from vayl.licensing.receipts import verify as verify_receipt
    from vayl.memory.decisions import Decisions
    from vayl.security import crypto
    from vayl.security.audit import Audit
    from vayl.storage.store import Store
    from vayl.telemetry.metrics import Metrics

    st = Store()
    crypter, signer = crypto.resolve("vayl.db"), crypto.resolve_signer("vayl.db")

    au = Audit(st.db, crypter, signer)
    for i in range(3):
        au.record("remember", "acme", detail=f"e{i}")
    assert au.verify_chain()["ok"] is True
    au.db.execute("UPDATE audit SET detail=? WHERE seq=(SELECT MIN(seq) FROM audit)", (crypter.enc("X"),))
    au.db.commit()
    assert au.verify_chain()["ok"] is False                       # tamper caught on Postgres

    de = Decisions(st.db, crypter, signer)
    did, _ = de.record("acted", [{"id": 1, "value": "v"}], user_id="acme")   # RETURNING id
    assert de.get(did, user_id="acme")["verified"] is True

    rc = Receipts(st.db, crypter, signer)
    rid = rc.save(make_receipt(signer, "delete", "acme//", "x", 1, "h"))     # RETURNING id
    assert verify_receipt(rc.get(rid)) is True

    auth = Auth(st.db)
    p, key = auth.create("bot", roles=Role.AGENT)
    assert auth.verify(key).id == p.id and auth.revoke(p.id) and auth.verify(key) is None

    mx = Metrics(st.db)
    mx.record_call("recall", 5.0); mx.record_error("recall", "Boom", "msg")   # id PK, not rowid
    assert mx.snapshot()["tools"]["recall"]["calls"] == 1 and mx.recent_errors()[0]["type"] == "Boom"


def test_advisory_space_lock_is_a_real_postgres_lock(pg):
    from vayl.storage.store import Store
    st = Store()
    with st.db.space_lock("acme/u1//"):
        held = st.db.execute("SELECT count(*) FROM pg_locks WHERE locktype='advisory'").fetchone()[0]
        assert held >= 1
    st.db.commit()


def test_advisory_lock_serializes_concurrent_cross_connection_writers(pg):
    """The multi-process guarantee: separate connections (≈ separate vayl-server processes) doing the
    read-MAX-id → insert-MAX+1 pattern on the SAME space must serialize — otherwise two would read the
    same MAX and insert a colliding id (PK violation). With the advisory space_lock, all ids are
    distinct and sequential."""
    from vayl.storage.db import Database
    d0 = Database(PG)
    d0.execute("DROP TABLE IF EXISTS lock_test")
    d0.execute("CREATE TABLE lock_test(space TEXT, id INTEGER, PRIMARY KEY(space, id))")
    d0.commit()

    errors = []

    def worker():
        d = Database(PG)                            # a separate connection == a separate "process"
        try:
            for _ in range(5):
                with d.space_lock("shared-space"):
                    nxt = d.execute(
                        "SELECT COALESCE(MAX(id),0) FROM lock_test WHERE space='shared-space'").fetchone()[0] + 1
                    d.execute("INSERT INTO lock_test(space, id) VALUES('shared-space', ?)", (nxt,))
                    # no explicit commit: space_lock's transaction commits on block exit, which is
                    # also what releases the advisory lock
        except Exception as e:                      # a PK collision would land here
            errors.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors                       # no collisions → the lock serialized the writers
    ids = [r[0] for r in d0.execute(
        "SELECT id FROM lock_test WHERE space='shared-space' ORDER BY id").fetchall()]
    assert ids == list(range(1, 21))                # 4 writers × 5 inserts = 20 distinct sequential ids
    d0.execute("DROP TABLE lock_test"); d0.commit()
