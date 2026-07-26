"""
Accountability and compliance — the tamper-evident audit chain, signed receipts and attestations, decision snapshots, and GDPR erasure/retention.
"""

import json
import sqlite3
import time

import pytest

from vayl.auth.auth import Auth, Role
from vayl.licensing.receipts import Receipts, make_attestation, make_receipt, verify
from vayl.memory import llm_memory
from vayl.memory.decisions import REDACTED, Decisions
from vayl.memory.llm_memory import LLMMemory
from vayl.security import crypto
from vayl.security.audit import GENESIS, Audit
from vayl.security.crypto import Crypter, Signer
from vayl.storage import store as store_mod
from vayl.storage.store import Store
from vayl.telemetry.metrics import Metrics

# ══════════════════════════════════════════════════════════════════
# from test_audit
# ══════════════════════════════════════════════════════════════════

def _db():
    return sqlite3.connect(":memory:")


def test_intact_chain_verifies():
    a = Audit(_db())
    for i in range(5):
        a.record("remember", "u1", detail=f"fact {i}")
    report = a.verify_chain()
    assert report["ok"] is True
    assert report["verified"] == 5 and report["broken_at"] is None


def test_first_row_links_to_genesis():
    a = Audit(_db())
    a.record("remember", "u1", detail="first")
    prev = a.db.execute("SELECT prev_hash FROM audit ORDER BY seq LIMIT 1").fetchone()[0]
    assert prev == GENESIS


def test_head_hash_advances_and_record_returns_it():
    a = Audit(_db())
    h1 = a.record("remember", "u1", detail="a")
    assert a.head_hash() == h1
    h2 = a.record("recall", "u1", detail="b")
    assert a.head_hash() == h2 and h2 != h1        # chain moved forward


def test_tampering_a_detail_breaks_the_chain():
    a = Audit(_db())      # no crypter → detail stored as plaintext, easy to tamper in-place
    a.record("remember", "u1", detail="salary=100")
    a.record("remember", "u1", detail="salary=120")
    a.record("remember", "u1", detail="salary=140")
    # attacker rewrites history: edit the middle row's stored content
    a.db.execute("UPDATE audit SET detail='salary=999' WHERE seq=2")
    a.db.commit()

    report = a.verify_chain()
    assert report["ok"] is False
    assert report["broken_at"] == 2                # the exact tampered row is named
    assert "tamper" in report["reason"]


def test_deleting_a_row_breaks_the_chain():
    a = Audit(_db())
    for i in range(4):
        a.record("remember", "u1", detail=f"e{i}")
    a.db.execute("DELETE FROM audit WHERE seq=2")   # excise a row → the link no longer matches
    a.db.commit()
    report = a.verify_chain()
    assert report["ok"] is False and report["broken_at"] is not None


def test_signed_chain_verifies_and_signature_tamper_is_caught():
    signer = Signer(b"k" * 32)
    a = Audit(_db(), signer=signer)
    a.record("remember", "u1", detail="x")
    a.record("delete", "u1", detail="y")
    assert a.verify_chain()["ok"] is True

    # forge a signature on a row → caught even though the hash link is untouched
    a.db.execute("UPDATE audit SET signature=? WHERE seq=1", ("00" * 64,))
    a.db.commit()
    report = a.verify_chain()
    assert report["ok"] is False and report["broken_at"] == 1


def test_tail_truncation_is_detected_by_the_signed_head_checkpoint():
    """Deleting the NEWEST rows leaves a shorter but internally-consistent chain — old verify_chain
    reported ok. The signed head checkpoint catches it (the attacker can't re-sign a new head)."""
    signer = Signer(b"k" * 32)
    a = Audit(_db(), signer=signer)
    for i in range(5):
        a.record("remember", "u1", detail=f"e{i}")
    assert a.verify_chain()["head_checkpoint"] == "verified"

    # delete the two newest rows — the chain up to seq 3 still links perfectly
    a.db.execute("DELETE FROM audit WHERE seq > 3")
    a.db.commit()
    report = a.verify_chain()
    assert report["ok"] is False
    assert "truncat" in report["reason"] and report["head_checkpoint"] == "mismatch"


def test_retention_purge_is_not_mistaken_for_truncation():
    """A legitimate purge deletes OLD rows and re-signs the checkpoint, so verify stays ok."""
    signer = Signer(b"k" * 32)
    a = Audit(_db(), signer=signer)
    for i in range(4):
        a.record("remember", "u1", detail=f"e{i}")
    a.purge(older_than_days=-1)               # cutoff in the future → purge everything but re-anchor
    assert a.verify_chain()["ok"] is True     # anchor + refreshed head checkpoint keep it verifiable


def test_encrypted_detail_is_still_chained():
    """With a crypter, detail is ciphertext; the chain hashes the stored bytes, so tamper-evidence
    holds without needing to decrypt."""
    class FakeCrypter:
        def enc(self, s):
            return None if s is None else "ENC(" + s + ")"
        def dec(self, s):
            return s[4:-1] if s and s.startswith("ENC(") else s

    a = Audit(_db(), crypter=FakeCrypter())
    a.record("remember", "u1", detail="secret")
    assert a.verify_chain()["ok"] is True
    assert a.tail()[0]["detail"] == "secret"        # round-trips through decryption
    a.db.execute("UPDATE audit SET detail='ENC(tampered)' WHERE seq=1")
    a.db.commit()
    assert a.verify_chain()["ok"] is False


def test_concurrent_appends_keep_the_chain_intact(tmp_path):
    """The append (read head hash → compute → insert) is the one part of Audit that must be
    serialized: two threads that both read the same head would chain to it in parallel and FORK the
    chain, which verify_chain flags as tampering. Audit carries its own lock for exactly this, so
    the coarse server lock no longer has to. This drives many threads through record() at once (on a
    real file DB, so each thread gets its own connection) and asserts the chain still verifies with
    every row present and no forks."""
    import threading

    from vayl.storage.db import Database

    db = Database(str(tmp_path / "audit.db"))
    a = Audit(db)
    threads_n, per_thread = 8, 40
    barrier = threading.Barrier(threads_n)

    def worker(t):
        barrier.wait()                               # maximize the overlap on the append
        for i in range(per_thread):
            a.record("remember", f"u{t}", detail=f"t{t}-{i}")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    report = a.verify_chain()
    assert report["ok"] is True, report
    assert report["verified"] == threads_n * per_thread   # nothing lost, nothing forked
    # every prev_hash is distinct → no two rows chained to the same head (a fork)
    prevs = [r[0] for r in a.db.execute("SELECT prev_hash FROM audit").fetchall()]
    assert len(prevs) == len(set(prevs))


# ══════════════════════════════════════════════════════════════════
# from test_receipts
# ══════════════════════════════════════════════════════════════════


def test_erasure_receipt_verifies_with_public_key_only():
    signer = Signer(b"r" * 32)
    rec = make_receipt(signer, "delete", "acme//", "alice_salary", 3, "chainhash123")
    # a third party verifies with ONLY the public key carried in the receipt — no secret, no DB
    assert verify(rec) is True
    assert verify(rec, public_key=signer.public_key_hex()) is True


def test_editing_any_payload_field_invalidates_the_receipt():
    signer = Signer(b"r" * 32)
    rec = make_receipt(signer, "delete", "acme//", "alice_salary", 3, "chainhash123")
    rec["payload"]["count"] = 0          # forge a smaller erasure count
    assert verify(rec) is False
    rec2 = make_receipt(signer, "delete", "acme//", "alice_salary", 3, "chainhash123")
    rec2["payload"]["subject"] = "bob_salary"
    assert verify(rec2) is False


def test_wrong_key_fails_verification():
    signer = Signer(b"r" * 32)
    rec = make_receipt(signer, "delete", "acme//", "x", 1, "h")
    assert verify(rec, public_key=Signer(b"z" * 32).public_key_hex()) is False


def test_attestation_proves_a_value_at_a_time():
    signer = Signer(b"a" * 32)
    att = make_attestation(signer, "refund_policy", "30-day", "chainheadABC")
    assert att["payload"]["kind"] == "knowledge_attestation"
    assert att["payload"]["value"] == "30-day" and att["payload"]["chain_hash"] == "chainheadABC"
    assert verify(att) is True
    att["payload"]["value"] = "14-day"   # rewrite what was 'known'
    assert verify(att) is False


def test_unsigned_receipt_does_not_verify():
    rec = make_receipt(None, "delete", "acme//", "x", 1, "h")   # no signer
    assert rec["signature"] is None
    assert verify(rec) is False


def test_persisted_receipt_roundtrips_and_stays_verifiable():
    signer = Signer(b"r" * 32)
    store = Receipts(_db(), signer=signer)
    rec = make_receipt(signer, "delete", "acme//", "alice_salary", 2, "h9")
    rid = store.save(rec)
    got = store.get(rid)
    assert got["payload"]["subject"] == "alice_salary" and got["payload"]["count"] == 2
    assert verify(got) is True                      # still verifiable after a save/load round-trip


def test_persisted_payload_is_encrypted_at_rest_but_verifiable_on_read():
    signer = Signer(b"r" * 32)
    crypter = Crypter(b"5" * 32)
    store = Receipts(_db(), crypter=crypter, signer=signer)
    rid = store.save(make_receipt(signer, "delete", "acme//", "alice_salary", 1, "h"))

    raw = store.db.execute("SELECT payload FROM receipts WHERE id=?", (rid,)).fetchone()[0]
    assert "alice_salary" not in raw                # subject not readable on disk
    got = store.get(rid)
    assert got["payload"]["subject"] == "alice_salary"   # decrypts on read
    assert verify(got) is True                            # and the signature still checks out


# ══════════════════════════════════════════════════════════════════
# from test_decisions
# ══════════════════════════════════════════════════════════════════


def fact(action="ADD", subject="policy", value="30-day refunds", scope="global",
         confidence=0.9, time_ref="present", target_id=None):
    o = {"action": action, "subject": subject, "value": value, "scope": scope,
         "confidence": confidence, "time_ref": time_ref}
    if target_id is not None:
        o["target_id"] = target_id
    return o


def test_record_and_reconstruct_a_decision():
    d = Decisions(_db())
    beliefs = [{"id": 1, "subject": "policy", "value": "30-day refunds", "confidence": 0.9,
                "source": "handbook", "supersedes": None, "status": "ACTIVE"}]
    did, digest = d.record("Refunded order #91", beliefs, user_id="u1")
    got = d.get(did)
    assert got["summary"] == "Refunded order #91"
    assert got["beliefs"] == beliefs
    assert got["entry_hash"] == digest


def test_signed_receipt_verifies_and_tamper_is_caught():
    signer = Signer(b"d" * 32)
    d = Decisions(_db(), signer=signer)
    did, _ = d.record("acted", [{"id": 1, "value": "x"}], user_id="u1")
    assert d.get(did)["verified"] is True

    # alter the stored snapshot after the fact → the signed digest no longer matches
    d.db.execute("UPDATE decisions SET snapshot=? WHERE id=?", ('[{"id": 1, "value": "TAMPERED"}]', did))
    d.db.commit()
    got = d.get(did)
    assert got["verified"] is False              # receipt catches the alteration
    assert got["beliefs"] == [{"id": 1, "value": "TAMPERED"}]   # and we can still see what it became


def test_snapshot_is_immutable_across_later_fact_changes(monkeypatch):
    """The core property: a decision records what was believed THEN. Superseding the fact afterward
    must not change the decision's snapshot — otherwise 'why did the agent act?' would silently
    rewrite itself."""
    monkeypatch.setattr(llm_memory, "_qa", lambda context, question: "30-day refunds")

    m = LLMMemory()
    m._apply(fact(subject="policy", value="30-day refunds"), "refunds are 30-day", source="handbook")
    _answer, used = m.query("what is the refund policy?", with_provenance=True)

    d = Decisions(_db(), signer=Signer(b"d" * 32))
    did, _ = d.record("Refunded order #91 under 30-day policy", used, user_id="u1")

    # policy changes AFTER the decision
    oid = m.active()[0].id
    m._apply(fact(action="SUPERSEDE", subject="policy", value="14-day refunds", target_id=oid), "now 14-day")

    reconstructed = d.get(did)
    assert reconstructed["beliefs"][0]["value"] == "30-day refunds"   # what it believed AT THE TIME
    assert reconstructed["verified"] is True                          # and the record is intact


def test_decisions_are_scoped_per_user():
    d = Decisions(_db())
    did, _ = d.record("secret action", [{"id": 1}], user_id="alice")
    assert d.get(did, user_id="alice") is not None
    assert d.get(did, user_id="bob") is None       # not visible cross-user


def test_list_returns_recent_decisions_newest_first():
    d = Decisions(_db())
    d.record("first", [], user_id="u1")
    d.record("second", [], user_id="u1")
    d.record("other user", [], user_id="u2")
    rows = d.list(user_id="u1")
    assert [r["summary"] for r in rows] == ["second", "first"]


def test_summary_and_snapshot_are_encrypted_at_rest():
    c = Crypter(b"9" * 32)
    d = Decisions(_db(), crypter=c)
    d.record("Refunded order #91", [{"id": 1, "value": "30-day refunds"}], user_id="u1")
    raw = d.db.execute("SELECT summary, snapshot FROM decisions").fetchone()
    assert "Refunded" not in raw[0] and "30-day" not in (raw[1] or "")   # ciphertext on disk
    got = d.get(1)
    assert got["summary"] == "Refunded order #91"                        # plaintext on read
    assert got["beliefs"][0]["value"] == "30-day refunds"


# ══════════════════════════════════════════════════════════════════
# from test_compliance
# ══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[0.0] for _ in texts])
    monkeypatch.setenv("VAYL_ENCRYPT", "off")


def _c_fact(action="ADD", subject="state", value="Redux", target_id=None):
    o = {"action": action, "subject": subject, "value": value, "scope": "global",
         "confidence": 0.9, "time_ref": "present"}
    if target_id is not None:
        o["target_id"] = target_id
    return o


def test_audit_records_who_what_when_newest_first():
    a = Audit(sqlite3.connect(":memory:"))
    a.record("remember", "u1", "", "", "ADD state")
    a.record("delete(erasure)", "u1", "", "", "subject=state rows=2")
    rows = a.tail()
    assert rows[0]["action"] == "delete(erasure)" and rows[1]["action"] == "remember"   # newest first
    assert rows[0]["ts"] and rows[0]["user_id"] == "u1"


def test_audit_filters_by_user():
    a = Audit(sqlite3.connect(":memory:"))
    a.record("recall", "alice")
    a.record("recall", "bob")
    assert [r["user_id"] for r in a.tail(user_id="alice")] == ["alice"]


def test_audit_detail_is_encrypted_at_rest():
    from vayl.security.crypto import Crypter
    db = sqlite3.connect(":memory:")
    a = Audit(db, crypter=Crypter(b"k" * 32))
    a.record("delete(erasure)", "u1", "", "", "subject=salary rows=1")
    # on disk: the personal detail is ciphertext
    raw = db.execute("SELECT detail FROM audit").fetchone()[0]
    assert "salary" not in raw and raw.startswith("gAAAAA")
    # on read (with the key): plaintext
    assert a.tail()[0]["detail"] == "subject=salary rows=1"


def test_export_is_machine_readable_and_includes_history(tmp_path):
    st = Store(str(tmp_path / "vayl.db"))
    m = LLMMemory()
    m._apply(_c_fact(subject="state", value="Redux"), "x")
    oid = m.active()[0].id
    m._apply(_c_fact(action="SUPERSEDE", subject="state", value="Zustand", target_id=oid), "x")
    m._apply(_c_fact(subject="db", value="Postgres"), "x")
    st.save("u1", m)

    exp = st.export("u1")
    json.dumps(exp)                                   # must be JSON-serializable (portability)
    vals = [r["value"] for r in exp["records"]]
    assert "Redux" in vals and "Zustand" in vals and "Postgres" in vals   # active + history
    assert exp["count"] == len(exp["records"]) == 3
    assert exp["space"]["user_id"] == "u1"


def test_expire_deletes_only_old_rows(tmp_path):
    st = Store(str(tmp_path / "vayl.db"))
    m = LLMMemory(); m._apply(_c_fact(subject="old", value="X"), "x"); st.save("u1", m)
    st.db.execute("UPDATE statements SET created_at=? WHERE subject='old'", (time.time() - 40 * 86400,))
    st.db.commit()
    m2 = st.load("u1"); m2._apply(_c_fact(subject="new", value="Y"), "x"); st.save("u1", m2)   # fresh

    n = st.expire("u1", older_than_days=30)
    assert n == 1                                     # only the 40-day-old row
    assert [s.value for s in st.load("u1").active()] == ["Y"]
    assert st.export("u1")["count"] == 1


# ══════════════════════════════════════════════════════════════════
# from test_compliance_fixes
# ══════════════════════════════════════════════════════════════════


def test_resolve_raises_when_encryption_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("VAYL_ENCRYPT", "on")
    monkeypatch.delenv("VAYL_KEY", raising=False)

    def broken(_key):
        raise ImportError("simulated broken cryptography install")
    monkeypatch.setattr(crypto, "Crypter", broken)
    with pytest.raises(RuntimeError, match="VAYL_ENCRYPT is on"):
        crypto.resolve(str(tmp_path / "x.db"))


def test_resolve_signer_raises_unless_explicitly_off(monkeypatch, tmp_path):
    def broken(_seed):
        raise ImportError("simulated broken cryptography install")
    monkeypatch.setattr(crypto, "Signer", broken)
    with pytest.raises(RuntimeError, match="VAYL_SIGN=off"):
        crypto.resolve_signer(str(tmp_path / "x.db"))
    monkeypatch.setenv("VAYL_SIGN", "off")
    assert crypto.resolve_signer(str(tmp_path / "x.db")) is None   # explicit opt-out still works


def test_explicit_encrypt_off_still_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("VAYL_ENCRYPT", "off")
    assert crypto.resolve(str(tmp_path / "x.db")) is None


def _record_decision_with(subjects_values, signer):
    d = Decisions(_db(), signer=signer)
    beliefs = [{"id": i + 1, "subject": s, "value": v, "status": "ACTIVE"}
               for i, (s, v) in enumerate(subjects_values)]
    did, _ = d.record("acted on beliefs", beliefs, user_id="u1")
    return d, did


def test_redact_scrubs_subject_values_and_receipt_still_verifies():
    d, did = _record_decision_with([("alice_diagnosis", "diabetes"), ("dept", "Cardiology")],
                                   Signer(b"r" * 32))
    assert d.redact("u1", subject="alice_diagnosis") == 1
    got = d.get(did, user_id="u1")
    by_subject = {b["subject"]: b for b in got["beliefs"]}
    assert by_subject["alice_diagnosis"]["value"] == REDACTED          # erased value gone
    assert by_subject["alice_diagnosis"]["redacted"] is True
    assert by_subject["dept"]["value"] == "Cardiology"                 # untargeted belief intact
    assert got["verified"] is True                                     # re-signed → still verifies


def test_redact_all_for_user_and_scoping():
    d, did = _record_decision_with([("a", "1"), ("b", "2")], Signer(b"r" * 32))
    assert d.redact("someone_else") == 0                               # other users untouched
    assert d.redact("u1") == 1                                         # subject=None → all beliefs
    got = d.get(did, user_id="u1")
    assert all(b["value"] == REDACTED for b in got["beliefs"])


def test_metric_errors_are_ciphertext_at_rest():
    db = _db()
    m = Metrics(db, crypter=Crypter(b"9" * 32))
    m.record_error("recall", "HTTPError", "LLM rejected prompt: 'alice has diabetes'")
    raw = db.execute("SELECT emsg FROM metric_errors").fetchone()[0]
    assert "diabetes" not in raw                                       # ciphertext on disk
    assert "diabetes" in m.recent_errors(1)[0]["msg"]                  # plaintext on read


def test_metric_errors_plaintext_without_crypter_still_works():
    m = Metrics(_db())
    m.record_error("recall", "Boom", "kaboom")
    assert m.recent_errors(1)[0]["msg"] == "kaboom"


def test_receipts_for_user_filters_by_scope():
    signer = Signer(b"r" * 32)
    rc = Receipts(_db(), signer=signer)
    rc.save(make_receipt(signer, "delete", "alice//", "salary", 2, "h1"))
    rc.save(make_receipt(signer, "delete", "bob//", "salary", 1, "h2"))
    rc.save(make_receipt(signer, "delete", "alice/agent1/", "dept", 1, "h3"))
    mine = rc.for_user("alice")
    assert len(mine) == 2
    assert all(r["payload"]["scope"].startswith("alice/") for r in mine)
    assert rc.for_user("ali") == []                                    # prefix can't leak 'alice'


def test_audit_purge_keeps_chain_verifiable_via_signed_anchor():
    signer = Signer(b"k" * 32)
    a = Audit(_db(), signer=signer)
    for i in range(4):
        a.record("remember", "u1", detail=f"e{i}")
    assert a.purge(older_than_days=-1) == 4                            # cutoff in the future → all rows
    assert a.db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 0

    a.record("recall", "u1", detail="after purge")                     # chain resumes from the anchor
    report = a.verify_chain()
    assert report["ok"] is True and report["verified"] == 1

    # forging the anchor is caught
    a.db.execute("UPDATE audit_meta SET value=? WHERE key='anchor'", ("0" * 64,))
    a.db.commit()
    assert a.verify_chain()["ok"] is False


def test_decisions_and_receipts_purge():
    signer = Signer(b"r" * 32)
    d = Decisions(_db(), signer=signer)
    d.record("old decision", [], user_id="u1")
    assert d.purge(older_than_days=-1) == 1
    rc = Receipts(_db(), signer=signer)
    rc.save(make_receipt(signer, "delete", "u1//", "x", 1, "h"))
    assert rc.purge(older_than_days=-1) == 1
    assert d.purge(older_than_days=365) == 0                           # nothing that old


def test_principal_name_is_ciphertext_at_rest_and_hard_deletable():
    db = _db()
    a = Auth(db, crypter=Crypter(b"5" * 32))
    p, key = a.create("alice.smith@hospital.eu", roles=Role.MEMBER)

    raw = db.execute("SELECT name FROM principals WHERE id=?", (p.id,)).fetchone()[0]
    assert "alice" not in raw                                          # encrypted on disk
    assert a.verify(key).name == "alice.smith@hospital.eu"             # decrypted on read
    assert a.list()[0]["name"] == "alice.smith@hospital.eu"

    assert a.delete(p.id) is True                                      # hard delete, not disable
    assert db.execute("SELECT COUNT(*) FROM principals").fetchone()[0] == 0
    assert a.delete(p.id) is False


def test_legacy_plaintext_principal_rows_still_readable():
    db = _db()
    plain = Auth(db)                                                   # rows written pre-encryption
    p, _ = plain.create("legacy-bot", roles=Role.AGENT)
    enc = Auth(db, crypter=Crypter(b"5" * 32))                         # later, encryption enabled
    assert any(r["name"] == "legacy-bot" for r in enc.list())
