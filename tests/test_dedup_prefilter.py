"""
Pre-LLM dedup: an exact restatement of facts that are all still active must skip the extractor
entirely (0 LLM calls), while a restatement whose fact has since changed must still be extracted —
so a value that should now supersede is never skipped. Deterministic/offline: the extractor is
spied, never called for real.
"""

from vayl.memory import llm_memory
from vayl.memory.llm_memory import LLMMemory
from vayl.memory.reconcile import Action


def _fact(subject, value, action="ADD", scope="global", confidence=0.9, time_ref="present"):
    return {"action": action, "subject": subject, "value": value, "scope": scope,
            "confidence": confidence, "time_ref": time_ref}


def _counting_extractor(monkeypatch, facts_for):
    calls = {"n": 0}

    def spy(text, active):
        calls["n"] += 1
        return facts_for(text)

    monkeypatch.setattr(llm_memory, "llm_extract_classify", spy)
    return calls


def test_prefilter_skips_extraction_on_unchanged_restatement(monkeypatch):
    calls = _counting_extractor(monkeypatch, lambda t: [_fact("state", "Redux")])
    m = LLMMemory()

    m.add("we use Redux")                 # extracts once, creates the active fact
    assert calls["n"] == 1

    res = m.add("we use Redux")           # identical, fact still active → SKIP the LLM
    assert calls["n"] == 1                # extractor NOT called a second time
    assert res and res[0][0] is Action.DEDUP
    assert [s.value for s in m.active()] == ["Redux"]


def test_prefilter_normalizes_whitespace_and_case(monkeypatch):
    calls = _counting_extractor(monkeypatch, lambda t: [_fact("state", "Redux")])
    m = LLMMemory()
    m.add("We use Redux")
    m.add("  we   USE   redux ")          # same after normalization → still a skip
    assert calls["n"] == 1


def test_prefilter_falls_through_when_the_fact_is_no_longer_active(monkeypatch):
    def facts(t):
        if "Zustand" in t:
            return [_fact("state", "Zustand", action="SUPERSEDE")]
        return [_fact("state", "Redux")]

    calls = _counting_extractor(monkeypatch, facts)
    m = LLMMemory()

    m.add("we use Redux")                 # active: Redux                       (calls=1)
    m.add("we switched to Zustand")       # supersedes Redux → active: Zustand  (calls=2)
    assert [s.value for s in m.active()] == ["Zustand"]

    # The original "we use Redux" statement is now SUPERSEDED, so re-asserting it must NOT be
    # skipped — it should reach the extractor and supersede Zustand back to Redux.
    m.add("we use Redux")
    assert calls["n"] == 3
    assert [s.value for s in m.active()] == ["Redux"]


def test_prefilter_does_not_skip_a_new_fact(monkeypatch):
    def facts(t):
        return [_fact("db", "Postgres")] if "Postgres" in t else [_fact("state", "Redux")]

    calls = _counting_extractor(monkeypatch, facts)
    m = LLMMemory()
    m.add("we use Redux")                 # calls=1
    m.add("we use Postgres")              # different utterance/fact → must extract (calls=2)
    assert calls["n"] == 2
    assert sorted(s.value for s in m.active()) == ["Postgres", "Redux"]


def test_prefilter_can_be_disabled(monkeypatch):
    monkeypatch.setattr(llm_memory, "_DEDUP_PREFILTER", False)
    calls = _counting_extractor(monkeypatch, lambda t: [_fact("state", "Redux")])
    m = LLMMemory()
    m.add("we use Redux")
    m.add("we use Redux")                 # prefilter off → extractor runs both times
    assert calls["n"] == 2
