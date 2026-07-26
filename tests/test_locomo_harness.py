"""
LOCOMO harness — offline tests.

No network, no API key, no dataset download: every test builds its own fixtures and stubs
the two LLM roles. What is being guarded here is the harness's *scoring semantics*, because
those are what a reader of the published numbers is trusting.

The central test is `test_stale_answer_passes_parity_but_is_flagged_by_strict` — it pins the
claim the whole benchmark rests on: that a partial-credit judge cannot see a superseded value
being reported as current, and that ours can.
"""
import asyncio

import pytest

from benchmarks.common.metrics import compute_overall_metrics, compute_staleness
from benchmarks.locomo import run as R
from benchmarks.locomo.prompts import (
    DEFAULT_CATEGORIES,
    PARITY_CATEGORIES,
    get_answer_prompt,
    get_judge_prompt,
    preprocess_answer,
)

# ── stubs ────────────────────────────────────────────────────────────────────

class StubLLM:
    """Stands in for the answerer and the judge. Distinguishes the two judge prompts by
    looking for the strict rubric's Part 2 header."""

    def __init__(self, answer="ANSWER: Free", label="CORRECT", stale=False, ambiguous=False,
                 strict_label=None):
        self.answer, self.label = answer, label
        self.strict_label = strict_label or label
        self.stale, self.ambiguous = stale, ambiguous
        self.calls = 0
        self.seen_prompts: list[str] = []

    async def complete(self, system, user, max_tokens=1024):
        self.calls += 1
        self.seen_prompts.append(user)
        return self.answer

    async def complete_json(self, system, user, max_tokens=512):
        self.calls += 1
        self.seen_prompts.append(user)
        strict = "Part 2 — stale" in user
        out = {"reasoning": "stub", "label": self.strict_label if strict else self.label}
        if strict:
            out.update(stale=self.stale, ambiguous=self.ambiguous)
        return out


class StubMemory:
    """A memory holding one current value and one superseded value for the same slot."""

    def __init__(self, results=None):
        self.results = results if results is not None else [
            {"id": 1, "memory": "plan: Free", "status": "current"},
            {"id": 2, "memory": "plan: Pro", "status": "superseded"},
        ]
        self.queries: list[str] = []

    async def search(self, query, user_id, top_k=200):
        self.queries.append(query)
        return self.results[:top_k]


def make_args(**over):
    ns = type("NS", (), {})()
    defaults = dict(top_k=200, cutoffs=[200], judge_mode="both", answer_max_tokens=256)
    for k, v in {**defaults, **over}.items():
        setattr(ns, k, v)
    return ns


def run(coro):
    return asyncio.run(coro)


# ── dataset parsing ──────────────────────────────────────────────────────────

def test_parses_locomo_date_formats():
    assert R.parse_locomo_date("1:56 pm on 8 May, 2023").year == 2023
    assert R.parse_locomo_date("9:55 am on 22 October, 2023").hour == 9
    assert R.parse_locomo_date("not a date") is None
    assert R.locomo_date_to_epoch("not a date") is None


def test_sessions_sort_chronologically_not_lexically():
    # session_10 must not sort before session_2 — memory only reconciles right in time order
    conv = {
        "session_2": [], "session_2_date_time": "1:00 pm on 2 May, 2023",
        "session_10": [], "session_10_date_time": "1:00 pm on 10 May, 2023",
        "session_1": [], "session_1_date_time": "1:00 pm on 1 May, 2023",
    }
    assert [k for k, _, _ in R.get_sorted_sessions(conv)] == [
        "session_1", "session_2", "session_10"]


def test_chunking_respects_size_and_renders_images():
    turns = [
        {"speaker": "Alice", "text": "hi"},
        {"speaker": "Bob", "text": "hello"},
        {"speaker": "Alice", "text": "", "blip_caption": "a red bike"},
        {"speaker": "Bob", "text": ""},                      # empty -> dropped
    ]
    one = R.session_to_chunks(turns, "Alice", 1)
    assert [len(c) for c in one] == [1, 1, 1]                # 3 non-empty turns
    assert "a red bike" in one[2][0]["content"]
    assert one[0][0]["role"] == "user" and one[1][0]["role"] == "assistant"

    assert [len(c) for c in R.session_to_chunks(turns, "Alice", 2)] == [2, 1]


# ── prompt selection ─────────────────────────────────────────────────────────

def test_parity_answerer_forbids_abstention_and_ours_permits_it():
    parity = get_answer_prompt("# Memories\n- x", "q?", "2023", allow_abstention=False)
    ours = get_answer_prompt("# Memories\n- x", "q?", "2023", allow_abstention=True)
    # upstream's Step 7 bans the phrase that LOCOMO category 5 requires
    assert 'NEVER say "not specified", "not mentioned"' in parity
    assert 'You MAY answer "Not mentioned"' in ours


def test_parity_judge_grants_partial_credit_and_strict_does_not():
    parity = get_judge_prompt("q", "gold", "resp", mode="parity")
    strict = get_judge_prompt("q", "gold", "resp", mode="strict")
    assert "PARTIAL CREDIT" in parity and "AT LEAST ONE correct item" in parity
    assert "PARTIAL CREDIT" not in strict
    assert "MAJORITY of its items" in strict
    assert "Part 2 — stale" in strict


def test_adversarial_gold_answers_are_normalized():
    for raw in ("No information available", "not mentioned", ""):
        assert preprocess_answer(5, raw).startswith("Not mentioned")
    assert preprocess_answer(1, "Lake Tahoe") == "Lake Tahoe"   # other categories untouched


def test_category_sets_differ_only_by_adversarial():
    assert set(DEFAULT_CATEGORIES) - set(PARITY_CATEGORIES) == {5}


# ── retrieval rendering ──────────────────────────────────────────────────────

def test_memories_render_with_status_tags_and_respect_cutoff():
    results = [{"memory": f"k{i}: v", "status": "current"} for i in range(5)]
    out = R.format_memories(results, 3)
    assert out.count("- [current]") == 3
    assert "[superseded]" in R.format_memories(
        [{"memory": "plan: Pro", "status": "superseded"}], 10)


def test_extract_answer_takes_text_after_marker():
    assert R.extract_answer("reasoning...\nANSWER: Lake Tahoe") == "Lake Tahoe"
    assert R.extract_answer("bare answer") == "bare answer"


# ── judging ──────────────────────────────────────────────────────────────────

def test_judge_scores_and_reports_unparseable_labels_as_errors():
    ok = run(R.judge_one(StubLLM(label="CORRECT"), "q", "g", "r", "parity"))
    assert ok["judgment"] == "CORRECT" and ok["score"] == 1.0
    bad = run(R.judge_one(StubLLM(label="MAYBE"), "q", "g", "r", "parity"))
    assert bad["judgment"] == "ERROR" and bad["score"] == 0.0     # never silently a zero


def test_strict_judge_returns_staleness_fields():
    v = run(R.judge_one(StubLLM(label="CORRECT", stale=True), "q", "g", "r", "strict"))
    assert v["stale"] is True and v["ambiguous"] is False


# ── the claim the benchmark rests on ─────────────────────────────────────────

def test_stale_answer_passes_parity_but_is_flagged_by_strict():
    """An answer naming the current value *and* a superseded one as if both were current.

    Upstream's rubric marks this CORRECT twice over — rule 1 (at least one gold item present)
    and rule 3 (never penalize extra detail). That is exactly how an additive store scores
    well on a benchmark while returning ambiguous state to its caller. The strict judge is
    the only place that distinction is visible, so this test is the harness's reason to exist.
    """
    qa = {"category": 1, "question": "What plan?", "answer": "Free"}
    answerer = StubLLM(answer="ANSWER: Pro, then Premium, now Free")
    judge = StubLLM(label="CORRECT", stale=True)

    rec = run(R.process_question(qa, 0, StubMemory(), answerer, judge, make_args(), "2023"))
    top = rec["cutoff_results"]["top_200"]

    assert top["judgment"] == "CORRECT"          # parity: passes
    assert top["score"] == 1.0
    assert top["stale"] is True                  # strict: caught
    assert compute_staleness([rec], "top_200")["stale_rate"] == 100.0


def test_both_mode_keeps_parity_as_the_headline_score():
    """judge_mode=both must not let the strict verdict contaminate the comparable number."""
    qa = {"category": 1, "question": "q", "answer": "gold"}
    judge = StubLLM(label="CORRECT", strict_label="WRONG")   # the two judges disagree

    rec = run(R.process_question(qa, 0, StubMemory(), StubLLM(), judge, make_args(), "2023"))
    top = rec["cutoff_results"]["top_200"]

    # the headline score stays the parity one, so it remains comparable to Mem0's
    assert top["judgment"] == "CORRECT" and top["score"] == 1.0
    # the strict disagreement is recorded alongside, not folded in
    assert top["strict_judgment"] == "WRONG" and top["strict_score"] == 0.0
    assert R.strict_view([rec])[0]["cutoff_results"]["top_200"]["score"] == 0.0


def test_strict_view_rescores_without_mutating_the_original():
    qa = {"category": 1, "question": "q", "answer": "g"}
    rec = run(R.process_question(qa, 0, StubMemory(), StubLLM(),
                                 StubLLM(label="CORRECT"), make_args(), "2023"))
    before = rec["cutoff_results"]["top_200"]["score"]
    view = R.strict_view([rec])
    assert (view[0]["cutoff_results"]["top_200"]["score"]
            == rec["cutoff_results"]["top_200"]["strict_score"])
    assert rec["cutoff_results"]["top_200"]["score"] == before   # original untouched


# ── metrics ──────────────────────────────────────────────────────────────────

def test_accuracy_uses_the_half_point_threshold_and_groups_by_category():
    evals = [
        {"category_name": "single-hop", "cutoff_results": {"top_200": {"score": 1.0}}},
        {"category_name": "single-hop", "cutoff_results": {"top_200": {"score": 0.0}}},
        {"category_name": "multi-hop", "cutoff_results": {"top_200": {"score": 1.0}}},
    ]
    m = compute_overall_metrics(evals, "category_name", ["top_200"])
    assert m.total == 3 and m.correct == 2
    assert m.overall_accuracy == pytest.approx(66.67, abs=0.01)
    assert m.by_group["single-hop"].accuracy == 50.0
    assert m.by_group["multi-hop"].accuracy == 100.0


def test_errors_are_counted_separately_from_wrong_answers():
    evals = [{"category_name": "c", "cutoff_results": {"top_200": {"score": 0.0,
                                                                   "judgment": "ERROR"}}}]
    m = compute_overall_metrics(evals, "category_name", ["top_200"])
    assert m.errors == 1 and m.correct == 0


def test_staleness_ignores_answers_the_strict_judge_never_saw():
    evals = [{"cutoff_results": {"top_200": {"score": 1.0}}},                    # parity only
             {"cutoff_results": {"top_200": {"score": 1.0, "stale": True}}}]
    s = compute_staleness(evals, "top_200")
    assert s["total"] == 2 and s["judged"] == 1 and s["stale"] == 1
    assert s["stale_rate"] == 100.0        # rate is over judged, not over total
