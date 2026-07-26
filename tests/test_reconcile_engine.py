"""
The heuristic reconciliation engine (`reconcile_harness`).

This is the transparent, LLM-free classifier + engine. Its whole point is the
`SILENTLY_WRONG` metric: clear cases auto-resolve, ambiguous ones FLAG, and it
must *never* confidently return a false answer.

The heuristic only reconciles inside its hand-coded vocabulary. On NOVEL domains
(cloud providers, languages) it has no keywords and *deliberately* degrades to
blind-ADD — i.e. it behaves like an additive store. That is a documented limit,
not a bug; the LLM classifier is what generalizes past it. These tests pin both
halves: zero silently-wrong in-vocabulary, and the known degradation out of it.
"""
import pytest

from vayl.memory.reconcile import CASES, Action, Engine

IN_VOCAB = [c for c in CASES if not c.novel]
# Novel cases that require real generalization (a reversal/scoped-coexistence the
# keyword heuristic cannot see) — these are expected to degrade to ADD.
NOVEL_GENERALIZING = [c for c in CASES if c.novel and c.expect in (Action.SUPERSEDE, Action.COEXIST)]


def run_case(case):
    eng = Engine()
    for inp in case.inputs[:-1]:
        eng.add(inp)
    act, _ = eng.add(case.inputs[-1])
    return act


@pytest.mark.parametrize("case", IN_VOCAB, ids=[c.name for c in IN_VOCAB])
def test_in_vocab_case_is_correct_or_safely_flagged(case):
    """Each in-vocab case hits its expected action, or FLAGs where a confident
    action was expected (safe — surfaced, not guessed). Never silently wrong."""
    act = run_case(case)
    safe_flag = act == Action.FLAG and case.expect in (
        Action.SUPERSEDE, Action.COEXIST, Action.REFINE)
    assert act == case.expect or safe_flag, (
        f"{case.name}: expected {case.expect.value}, got {act.value}")


def test_no_in_vocab_case_is_silently_wrong():
    """The one number that kills the product: a confident, wrong answer."""
    confident = (Action.SUPERSEDE, Action.COEXIST, Action.REFINE,
                 Action.ADD, Action.DEDUP, Action.SKIP)
    offenders = []
    for case in IN_VOCAB:
        act = run_case(case)
        ok = act == case.expect
        safe_flag = act == Action.FLAG and case.expect in (
            Action.SUPERSEDE, Action.COEXIST, Action.REFINE)
        if not ok and not safe_flag and act in confident:
            offenders.append(f"{case.name}: expected {case.expect.value}, got {act.value}")
    assert not offenders, "silently-wrong cases:\n" + "\n".join(offenders)


@pytest.mark.parametrize("case", NOVEL_GENERALIZING, ids=[c.name for c in NOVEL_GENERALIZING])
def test_novel_domain_degrades_to_add_the_known_heuristic_limit(case):
    """Documents (and guards) the heuristic's boundary: with no keyword for a
    novel domain it blind-ADDs instead of reconciling. If this ever starts
    passing, the heuristic got smarter and this expectation should be updated."""
    assert run_case(case) == Action.ADD


def test_clean_contradiction_supersedes():
    eng = Engine()
    eng.add("We use Zustand for state management.")
    act, _ = eng.add("We switched to Redux Toolkit, dropping Zustand.")
    assert act == Action.SUPERSEDE
    assert eng.current_answer("state_management") == "redux toolkit"


def test_scoped_facts_coexist():
    eng = Engine()
    eng.add("We use Redux Toolkit for state in the web app.")
    act, _ = eng.add("We use Zustand for state in the mobile app.")
    assert act == Action.COEXIST


def test_sarcasm_is_skipped():
    eng = Engine()
    eng.add("Our database is PostgreSQL.")
    act, _ = eng.add("Oh sure, let's just switch everything to MongoDB 🙄")
    assert act == Action.SKIP
    assert "postgres" in eng.current_answer("database").lower()


def test_ambiguous_conflict_flags():
    eng = Engine()
    eng.add("We deploy on Fridays.")
    act, _ = eng.add("We deploy on Mondays.")
    assert act == Action.FLAG
