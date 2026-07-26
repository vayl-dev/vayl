"""
Shared organizational memory (Feature 4) — source-aware reconciliation.

Two layers: the pure `resolve()` decision (supersede vs flag by mode) and the engine behavior when a
shared space has a policy — a lower-authority source must NOT silently overwrite a higher-authority
fact, REVIEW flags every cross-source conflict, and a source correcting itself always supersedes.
"""
import pytest

from vayl.memory.llm_memory import LLMMemory
from vayl.memory.orgmemory import ReconcileMode, ReconcilePolicy, resolve
from vayl.memory.reconcile import Status
from vayl.storage import store as store_mod
from vayl.storage.store import Store


def fact(action="ADD", subject="cloud", value="AWS", scope="global",
         confidence=0.95, time_ref="present", target_id=None):
    o = {"action": action, "subject": subject, "value": value, "scope": scope,
         "confidence": confidence, "time_ref": time_ref}
    if target_id is not None:
        o["target_id"] = target_id
    return o


# ── pure resolve() ────────────────────────────────────────────────────────────

def test_recency_always_supersedes():
    assert resolve("a", "b", ReconcilePolicy(ReconcileMode.RECENCY)) == "supersede"
    assert resolve("a", "b", None) == "supersede"          # no policy → recency default


def test_review_flags_cross_source_but_not_self_correction():
    p = ReconcilePolicy(ReconcileMode.REVIEW)
    assert resolve("alice", "bob", p) == "flag"            # different sources → surface it
    assert resolve("alice", "alice", p) == "supersede"     # correcting your own fact is fine


def test_authority_lets_higher_rank_win_and_flags_the_rest():
    p = ReconcilePolicy(ReconcileMode.AUTHORITY, authority={"cfo": 3, "analyst": 1})
    assert resolve("cfo", "analyst", p) == "supersede"     # higher authority overwrites
    assert resolve("analyst", "cfo", p) == "flag"          # lower authority may NOT overwrite
    assert resolve("analyst", "analyst", p) == "supersede"  # self-correction
    assert resolve("unknown", "cfo", p) == "flag"          # unranked (0) < cfo → flag


def test_policy_serialization_roundtrips():
    p = ReconcilePolicy(ReconcileMode.AUTHORITY, authority={"cfo": 3})
    assert ReconcilePolicy.from_dict(p.to_dict()).mode == ReconcileMode.AUTHORITY
    assert ReconcilePolicy.from_dict(p.to_dict()).authority == {"cfo": 3}
    assert ReconcilePolicy.from_dict(None) is None


# ── engine behaviour under a policy ───────────────────────────────────────────

def test_authority_lower_source_flags_instead_of_overwriting():
    """The core shared-memory guarantee: an analyst can't silently overwrite the CFO's number."""
    policy = ReconcilePolicy(ReconcileMode.AUTHORITY, authority={"cfo": 3, "analyst": 1})
    m = LLMMemory(policy=policy)
    m._apply(fact(subject="q3_revenue", value="10M"), "revenue is 10M", source="cfo")
    oid = m.active()[0].id
    act, _, _ = m._apply(fact(action="SUPERSEDE", subject="q3_revenue", value="8M", target_id=oid),
                         "revenue is 8M", source="analyst")

    from vayl.memory.reconcile import Action
    assert act == Action.FLAG                              # the change was surfaced, not applied
    assert [s.value for s in m.active()] == ["10M"]        # CFO's number still stands
    assert any(s.value == "8M" and s.status == Status.FLAGGED for s in m.statements)


def test_authority_higher_source_supersedes():
    policy = ReconcilePolicy(ReconcileMode.AUTHORITY, authority={"cfo": 3, "analyst": 1})
    m = LLMMemory(policy=policy)
    m._apply(fact(subject="q3_revenue", value="8M"), "revenue is 8M", source="analyst")
    oid = m.active()[0].id
    m._apply(fact(action="SUPERSEDE", subject="q3_revenue", value="10M", target_id=oid),
             "revenue is 10M", source="cfo")
    assert [s.value for s in m.active()] == ["10M"]        # CFO overrides the analyst


def test_review_flags_any_cross_source_change():
    m = LLMMemory(policy=ReconcilePolicy(ReconcileMode.REVIEW))
    m._apply(fact(subject="cloud", value="AWS"), "we use AWS", source="alice")
    oid = m.active()[0].id
    m._apply(fact(action="SUPERSEDE", subject="cloud", value="GCP", target_id=oid),
             "we use GCP", source="bob")
    assert [s.value for s in m.active()] == ["AWS"]        # not overwritten — held for review
    assert any(s.value == "GCP" and s.status == Status.FLAGGED for s in m.statements)


def test_cross_source_refine_that_changes_value_is_gated_too():
    """Regression (found by a real-model E2E run): a weak model classified a cross-source value
    CHANGE as REFINE, not SUPERSEDE. REFINE overwrites in place, so it must respect the same
    authority policy — otherwise a low-authority source sneaks a change in as a 'refinement'."""
    from vayl.memory.reconcile import Action
    policy = ReconcilePolicy(ReconcileMode.AUTHORITY, authority={"cfo": 3, "analyst": 1})
    m = LLMMemory(policy=policy)
    m._apply(fact(subject="q3_revenue", value="10M"), "revenue is 10M", source="cfo")
    tid = m.active()[0].id
    act, _, _ = m._apply(fact(action="REFINE", subject="q3_revenue", value="8M", target_id=tid),
                         "correction: 8M", source="analyst")
    assert act == Action.FLAG                              # the sneaky refine was surfaced, not applied
    assert [s.value for s in m.active()] == ["10M"]        # CFO's number still stands


def test_same_value_refine_still_adds_detail_across_sources():
    # a genuine detail-add (same value) is fine even cross-source — only value CHANGES are gated
    policy = ReconcilePolicy(ReconcileMode.REVIEW)
    m = LLMMemory(policy=policy)
    m._apply(fact(subject="db", value="Postgres"), "db is Postgres", source="alice")
    tid = m.active()[0].id
    m._apply(fact(action="REFINE", subject="db", value="Postgres", target_id=tid), "still Postgres", source="bob")
    assert [s.value for s in m.active()] == ["Postgres"]


def test_same_source_supersedes_even_under_review():
    m = LLMMemory(policy=ReconcilePolicy(ReconcileMode.REVIEW))
    m._apply(fact(subject="cloud", value="AWS"), "we use AWS", source="alice")
    oid = m.active()[0].id
    m._apply(fact(action="SUPERSEDE", subject="cloud", value="GCP", target_id=oid),
             "correction: GCP", source="alice")
    assert [s.value for s in m.active()] == ["GCP"]        # alice may correct her own fact


def test_default_no_policy_preserves_plain_supersede():
    m = LLMMemory()      # no policy → RECENCY, existing behavior unchanged
    m._apply(fact(subject="cloud", value="AWS"), "AWS", source="alice")
    oid = m.active()[0].id
    m._apply(fact(action="SUPERSEDE", subject="cloud", value="GCP", target_id=oid), "GCP", source="bob")
    assert [s.value for s in m.active()] == ["GCP"]


# ── persistence: a space's policy round-trips and drives load() ───────────────

@pytest.fixture(autouse=True)
def _no_network_embed(monkeypatch):
    monkeypatch.setattr(store_mod, "_embed", lambda texts: [[0.0] for _ in texts])
    monkeypatch.setenv("VAYL_ENCRYPT", "off")


def test_space_policy_persists_and_is_applied_on_load(tmp_path):
    db = str(tmp_path / "vayl.db")
    st = Store(db)
    st.set_policy("org:acme", ReconcilePolicy(ReconcileMode.AUTHORITY, authority={"cfo": 3, "analyst": 1}))

    # a fresh Store reads the persisted policy and attaches it to the loaded space
    m = Store(db).load("org:acme")
    assert m.policy.mode == ReconcileMode.AUTHORITY and m.policy.authority == {"cfo": 3, "analyst": 1}

    m._apply(fact(subject="q3_revenue", value="10M"), "10M", source="cfo")
    Store(db).save("org:acme", m)
    m2 = Store(db).load("org:acme")
    oid = m2.active()[0].id
    m2._apply(fact(action="SUPERSEDE", subject="q3_revenue", value="8M", target_id=oid), "8M", source="analyst")
    assert [s.value for s in m2.active()] == ["10M"]       # policy survived reload → analyst can't overwrite CFO


def test_get_policy_defaults_to_none(tmp_path):
    assert Store(str(tmp_path / "vayl.db")).get_policy("nobody") is None
