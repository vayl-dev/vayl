"""
Autonomous-action safety gate (Feature 2).

Two layers: the pure `evaluate_fact` verdict (deterministic, `now` injected) and the engine methods
`check` / `safe_recall` that apply it to real memory. The gate must BLOCK on each risky condition —
flagged conflict, low confidence, staleness, recent change, not-current — and PASS a clean fact.
"""
from vayl.memory import llm_memory
from vayl.memory.llm_memory import LLMMemory
from vayl.memory.reconcile import Status
from vayl.security.safety import SafetyPolicy, evaluate_fact

DAY = 86400.0
NOW = 1_000_000_000.0


def prov(status="ACTIVE", confidence=0.9, set_at=NOW, supersedes=None, subject="policy", value="x"):
    return {"id": 1, "subject": subject, "value": value, "status": status,
            "confidence": confidence, "supersedes": supersedes, "source": "", "set_at": set_at}


def fact(action="ADD", subject="policy", value="30-day", scope="global",
         confidence=0.9, time_ref="present", target_id=None):
    o = {"action": action, "subject": subject, "value": value, "scope": scope,
         "confidence": confidence, "time_ref": time_ref}
    if target_id is not None:
        o["target_id"] = target_id
    return o


# ── pure evaluator ────────────────────────────────────────────────────────────

def test_clean_active_fact_is_safe():
    ok, reasons = evaluate_fact(prov(), SafetyPolicy(), NOW)
    assert ok is True and reasons == []


def test_low_confidence_blocks():
    ok, reasons = evaluate_fact(prov(confidence=0.5), SafetyPolicy(min_confidence=0.7), NOW)
    assert ok is False and any("confidence" in r for r in reasons)


def test_flagged_conflict_blocks():
    ok, reasons = evaluate_fact(prov(status="FLAGGED_CONFLICT"), SafetyPolicy(), NOW)
    assert ok is False and any("conflict" in r.lower() for r in reasons)


def test_superseded_fact_is_not_safe_to_act_on():
    ok, reasons = evaluate_fact(prov(status="SUPERSEDED"), SafetyPolicy(), NOW)
    assert ok is False and any("not current" in r for r in reasons)


def test_staleness_blocks_only_when_a_window_is_set():
    stale = prov(set_at=NOW - 400 * DAY)
    assert evaluate_fact(stale, SafetyPolicy(), NOW)[0] is True                    # no window → not checked
    ok, reasons = evaluate_fact(stale, SafetyPolicy(max_staleness_days=90), NOW)
    assert ok is False and any("stale" in r for r in reasons)


def test_recent_change_blocks_a_just_superseded_fact():
    just_changed = prov(set_at=NOW - 0.2 * DAY, supersedes=7)
    ok, reasons = evaluate_fact(just_changed, SafetyPolicy(block_on_recent_change_days=1), NOW)
    assert ok is False and any("recently changed" in r for r in reasons)
    # a brand-new fact (no supersedes) is NOT a "change" — it's new info, so it passes
    fresh = prov(set_at=NOW - 0.2 * DAY, supersedes=None)
    assert evaluate_fact(fresh, SafetyPolicy(block_on_recent_change_days=1), NOW)[0] is True


def test_missing_set_at_does_not_crash_staleness_check():
    ok, _ = evaluate_fact(prov(set_at=None), SafetyPolicy(max_staleness_days=1), NOW)
    assert ok is True     # unknown age → cannot be judged stale, so it doesn't block


# ── engine: check(subject) ────────────────────────────────────────────────────

def test_check_blocks_when_no_fact_exists():
    m = LLMMemory()
    v = m.check("unknown_subject", now=NOW)
    assert v["ok"] is False and "no active fact" in v["reasons"][0]


def test_check_passes_a_clean_current_fact():
    m = LLMMemory()
    m._apply(fact(subject="refund_policy", value="30-day", confidence=0.95), "refunds are 30-day")
    v = m.check("refund_policy", now=NOW)
    assert v["ok"] is True and v["facts"][0]["value"] == "30-day"


def test_check_blocks_a_flagged_conflict():
    m = LLMMemory()
    m._apply(fact(action="FLAG", subject="refund_policy", value="14-day"), "conflicting")
    v = m.check("refund_policy", now=NOW)
    assert v["ok"] is False and any("conflict" in r.lower() for r in v["reasons"])


# ── engine: safe_recall(question) ─────────────────────────────────────────────

def test_safe_recall_answers_when_safe(monkeypatch):
    monkeypatch.setattr(llm_memory, "_qa", lambda ctx, q: "30-day")
    m = LLMMemory()
    m._apply(fact(subject="refund_policy", value="30-day", confidence=0.95), "refunds are 30-day")
    r = m.safe_recall("what is the refund policy?", now=NOW)
    assert r["ok"] is True and r["answer"] == "30-day"


def test_safe_recall_withholds_on_low_confidence(monkeypatch):
    monkeypatch.setattr(llm_memory, "_qa", lambda ctx, q: "maybe-30-day")
    m = LLMMemory()
    m._apply(fact(subject="refund_policy", value="30-day", confidence=0.5), "unsure")
    r = m.safe_recall("what is the refund policy?", policy=SafetyPolicy(min_confidence=0.7), now=NOW)
    assert r["ok"] is False and r["answer"] is None and any("confidence" in x for x in r["reasons"])


def test_safe_recall_withholds_when_a_conflict_exists_on_the_subject(monkeypatch):
    monkeypatch.setattr(llm_memory, "_qa", lambda ctx, q: "30-day")
    m = LLMMemory()
    m._apply(fact(subject="refund_policy", value="30-day", confidence=0.95), "refunds are 30-day")
    # a flagged conflict on the same subject must poison the answer for acting purposes
    m.statements.append(m.statements[0].__class__(
        "refund_policy@global", "refund_policy", "14-day", "global",
        status=Status.FLAGGED, confidence=0.5))
    r = m.safe_recall("what is the refund policy?", now=NOW)
    assert r["ok"] is False and any("conflict" in x.lower() for x in r["reasons"])


def test_safe_recall_withholds_when_nothing_current(monkeypatch):
    monkeypatch.setattr(llm_memory, "_qa", lambda ctx, q: "I don't know")
    m = LLMMemory()
    r = m.safe_recall("what is the refund policy?", now=NOW)
    assert r["ok"] is False and any("nothing safe to act on" in x for x in r["reasons"])
