"""
Human confirmation for slots where the WRITE is the hazard.

Reconciliation is driven by an LLM reading conversational text. For most slots a wrong write is
recoverable — the value is superseded again later, and history shows what happened. For some, the
write itself is the hazard: "we should probably stop the warfarin" appearing in a sentence is not
an order to stop it, and a memory that quietly retires the medication has done real harm before
anyone reads the answer.

A confirm-required slot does not apply a replacement or removal. It records a PROPOSAL, leaves the
current value untouched, and waits. Nothing changes until a person decides.
"""
import pytest

from vayl.memory import llm_memory
from vayl.memory.llm_memory import LLMMemory
from vayl.memory.reconcile import Action, Status
from vayl.memory.schema import from_dict

SCHEMA = {"slots": [
    {"name": "active_medication", "category": "critical", "confirm": True,
     "aliases": ["meds", "medication"]},
    {"name": "favourite_colour"},                       # declared, but not gated
]}


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", from_dict(SCHEMA))


def fact(subject, value, action="ADD", target_id=None, conf=0.9):
    o = {"action": action, "subject": subject, "value": value, "scope": "global",
         "confidence": conf, "time_ref": "present"}
    if target_id is not None:
        o["target_id"] = target_id
    return o


@pytest.fixture
def on_warfarin(gated):
    m = LLMMemory()
    m._apply(fact("active_medication", "warfarin 5mg daily"), "patient is on warfarin 5mg daily")
    return m


# ── the gate ─────────────────────────────────────────────────────────────────

def test_a_replacement_is_proposed_not_applied(on_warfarin):
    act, _, _ = on_warfarin._apply(
        fact("meds", "apixaban 5mg twice daily", action="SUPERSEDE"),
        "switch to apixaban 5mg twice daily")

    assert act is Action.FLAG
    live = [s for s in on_warfarin.active()]
    assert len(live) == 1
    assert live[0].value == "warfarin 5mg daily"        # unchanged until someone decides


def test_a_removal_is_proposed_not_applied(on_warfarin):
    act, _, _ = on_warfarin._apply(
        fact("active_medication", "warfarin 5mg daily", action="RETRACT"),
        "stop the warfarin")

    assert act is Action.FLAG
    assert [s.value for s in on_warfarin.active()] == ["warfarin 5mg daily"]


def test_the_proposal_is_visible_as_pending(on_warfarin):
    on_warfarin._apply(fact("meds", "apixaban", action="SUPERSEDE"), "switch to apixaban")
    pend = on_warfarin.pending()
    assert len(pend) == 1
    assert pend[0].value == "apixaban"
    assert (pend[0].metadata or {}).get("pending") == "SUPERSEDE"


def test_an_ungated_slot_still_applies_immediately(gated):
    m = LLMMemory()
    m._apply(fact("favourite_colour", "green"), "I like green")
    m._apply(fact("favourite_colour", "blue"), "actually blue now")
    assert [s.value for s in m.active()] == ["blue"]     # no gate, no ceremony


def test_an_undeclared_slot_is_never_gated(gated):
    m = LLMMemory()
    m._apply(fact("db", "Postgres"), "we use Postgres")
    m._apply(fact("db", "Aurora"), "we moved to Aurora")
    assert [s.value for s in m.active()] == ["Aurora"]


def test_a_first_write_to_a_gated_slot_is_not_blocked(gated):
    """There is nothing to lose yet — gating an initial write would just make the slot unusable."""
    m = LLMMemory()
    act, _, _ = m._apply(fact("active_medication", "warfarin"), "started on warfarin")
    assert act is not Action.FLAG
    assert [s.value for s in m.active()] == ["warfarin"]


# ── confirming ───────────────────────────────────────────────────────────────

def test_confirming_applies_the_replacement(on_warfarin):
    on_warfarin._apply(fact("meds", "apixaban", action="SUPERSEDE"), "switch to apixaban")
    pid = on_warfarin.pending()[0].id

    act, subj, val = on_warfarin.confirm(pid, source="dr_smith")
    assert act is Action.SUPERSEDE
    assert [s.value for s in on_warfarin.active()] == ["apixaban"]
    assert not on_warfarin.pending()


def test_confirming_applies_the_removal(on_warfarin):
    on_warfarin._apply(fact("active_medication", "warfarin 5mg daily", action="RETRACT"),
                       "stop the warfarin")
    pid = on_warfarin.pending()[0].id

    act, _, _ = on_warfarin.confirm(pid, source="dr_smith")
    assert act is Action.RETRACT
    assert on_warfarin.active() == []                    # slot is now empty, as intended


def test_confirmation_records_who_decided(on_warfarin):
    on_warfarin._apply(fact("meds", "apixaban", action="SUPERSEDE"), "switch")
    pid = on_warfarin.pending()[0].id
    on_warfarin.confirm(pid, source="dr_smith")
    st = next(s for s in on_warfarin.statements if s.id == pid)
    assert (st.metadata or {}).get("confirmed_by") == "dr_smith"
    assert (st.metadata or {}).get("resolved") == "confirmed"


def test_confirming_an_unknown_id_does_nothing(on_warfarin):
    assert on_warfarin.confirm(99999) is None


def test_a_proposal_cannot_be_confirmed_against_state_that_has_since_changed(on_warfarin):
    """The value it was going to replace is already gone. Applying now would enact a decision
    made against state that no longer holds."""
    on_warfarin._apply(fact("meds", "apixaban", action="SUPERSEDE"), "switch to apixaban")
    pid = on_warfarin.pending()[0].id

    target = next(s for s in on_warfarin.statements if s.value == "warfarin 5mg daily")
    target.status = Status.SUPERSEDED                    # something else retired it meanwhile

    assert on_warfarin.confirm(pid) is None
    st = next(s for s in on_warfarin.statements if s.id == pid)
    assert (st.metadata or {}).get("resolved") == "stale"


# ── rejecting ────────────────────────────────────────────────────────────────

def test_rejecting_leaves_the_current_value_standing(on_warfarin):
    on_warfarin._apply(fact("meds", "apixaban", action="SUPERSEDE"), "switch to apixaban")
    pid = on_warfarin.pending()[0].id

    on_warfarin.reject(pid, source="dr_smith")
    assert [s.value for s in on_warfarin.active()] == ["warfarin 5mg daily"]
    assert not on_warfarin.pending()


def test_a_rejected_proposal_is_kept_as_history(on_warfarin):
    """That someone proposed stopping a medication is itself worth being able to audit."""
    on_warfarin._apply(fact("active_medication", "warfarin 5mg daily", action="RETRACT"),
                       "stop the warfarin")
    pid = on_warfarin.pending()[0].id
    on_warfarin.reject(pid, source="dr_smith")

    st = next(s for s in on_warfarin.statements if s.id == pid)
    assert st.status is Status.HISTORICAL
    assert (st.metadata or {}).get("resolved") == "rejected"
    assert (st.metadata or {}).get("rejected_by") == "dr_smith"


def test_rejecting_an_unknown_id_does_nothing(on_warfarin):
    assert on_warfarin.reject(99999) is None


# ── a pending proposal must not be mistaken for the answer ───────────────────

def test_a_pending_value_is_not_returned_as_current(on_warfarin, monkeypatch):
    monkeypatch.setattr(llm_memory, "_qa", lambda context, question: context)
    on_warfarin._apply(fact("meds", "apixaban", action="SUPERSEDE"), "switch to apixaban")

    ctx = on_warfarin.query("what medication is the patient on?")
    assert "warfarin" in ctx
    # FLAGGED statements are not ACTIVE, so the proposal cannot present itself as the live value
    assert "active_medication=apixaban" not in ctx


def test_schema_documents_the_gate_to_the_extractor():
    frag = from_dict(SCHEMA).prompt_fragment()
    assert "CONFIRMED" in frag
    assert "reviewed by a person" in frag
