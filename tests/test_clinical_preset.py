"""
The clinical slot preset on the reconciliation engine: list slots that hold several values, critical
tagging, confirmation gates, the trusted-source bypass, and the longitudinal patient acceptance suite.

Facts are written in the shape an authorized structured feed delivers them (source="fhir"), so these
pin the ENGINE's behavior for clinical data, independent of any ingestion adapter.
"""

import os

import pytest

os.environ.setdefault("VAYL_SLOT_SCHEMA",
                      os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                   "examples", "clinical-slots.json"))
os.environ.setdefault("VAYL_CRITICAL_CATEGORIES", "critical")
os.environ.setdefault("VAYL_SLOT_RESOLVE", "1")
from benchmarks.clinical.patients import PATIENTS  # noqa: E402
from benchmarks.clinical.run import check, ingest  # noqa: E402
from vayl.memory import llm_memory  # noqa: E402
from vayl.memory.llm_memory import LLMMemory, Status, is_critical  # noqa: E402
from vayl.memory.schema import load as _load_schema  # noqa: E402

_CLINICAL = _load_schema(os.environ["VAYL_SLOT_SCHEMA"])


@pytest.fixture(autouse=True)
def _clinical_env(monkeypatch):
    """One clinical configuration for every test in this file: the schema loads once at import, so
    pin it regardless of collection order, enable list-slot resolution, and trust the authorized
    order feeds (fhir + inpatient) while narrative stays gated."""
    monkeypatch.setattr(llm_memory, "SLOT_SCHEMA", _CLINICAL)
    monkeypatch.setattr(llm_memory, "_CRITICAL_CATEGORIES", ("critical",))
    monkeypatch.setattr(llm_memory, "_SLOT_RESOLVE", True)
    monkeypatch.setattr(llm_memory, "_TRUSTED_SOURCES", ("inpatient", "fhir"))


def _feed(action, subject, value):
    """A fact as an authorized structured feed (e.g. FHIR) delivers it."""
    return {"subject": subject, "value": value, "action": action, "kind": "state", "scope": "global",
            "time_ref": "present", "confidence": 0.98, "source": "fhir"}


def _apply_all(m, facts):
    for f in facts:
        m._apply(f, f.get("value", ""), source=f.get("source", "fhir"))


def test_a_structured_feed_produces_correct_current_truth():
    m = LLMMemory()
    _apply_all(m, [
        _feed("ADD", "allergy", "Penicillin — Anaphylaxis (severe)"),
        _feed("ADD", "allergy", "Sulfa — Rash (moderate)"),                 # list: BOTH coexist
        _feed("ADD", "active_medication", "Warfarin 5 mg PO daily"),
        _feed("ADD", "active_medication", "Metformin 1000 mg PO BID"),
        _feed("ADD", "primary_diagnosis", "Community-acquired pneumonia"),
    ])
    allergies = {s.value.split(" —")[0] for s in m.active() if s.subject == "allergy"}
    assert allergies == {"Penicillin", "Sulfa"}             # neither deleted the other
    assert all(is_critical(s) for s in m.active() if s.subject == "allergy")
    meds = {s.value for s in m.active() if s.subject == "active_medication"}
    assert meds == {"Warfarin 5 mg PO daily", "Metformin 1000 mg PO BID"}


def test_a_refuted_allergy_leaves_the_active_chart_but_stays_in_history():
    m = LLMMemory()
    _apply_all(m, [_feed("ADD", "allergy", "Penicillin — Rash (severe)")])
    assert any(s.subject == "allergy" for s in m.active())
    _apply_all(m, [_feed("RETRACT", "allergy", "Penicillin — Rash (severe)")])
    assert not any("Penicillin" in s.value for s in m.active() if s.subject == "allergy")
    assert any(s.subject == "allergy" and s.status is not Status.ACTIVE for s in m.statements)


def test_a_narrative_change_is_still_gated_while_the_feed_is_trusted():
    """The bypass is source-scoped: a trusted feed's order applies directly, but a change from the
    narrative path (source not trusted) still queues for review. Both on the same confirm slot."""
    m = LLMMemory()
    _apply_all(m, [_feed("ADD", "active_medication", "Warfarin 5 mg PO daily")])   # trusted -> active
    m._apply({"action": "RETRACT", "subject": "active_medication", "value": "Warfarin 5 mg PO daily",
              "scope": "global", "confidence": 0.9, "time_ref": "present"},
             "note says consider stopping warfarin", source="note_extractor")
    assert any("Warfarin" in s.value for s in m.active())              # still current
    assert len(m.pending()) == 1                                       # queued for a human


def test_a_stopped_medication_leaves_the_list_but_others_remain():
    m = LLMMemory()
    _apply_all(m, [_feed("ADD", "active_medication", "Warfarin 5 mg PO daily"),
                   _feed("ADD", "active_medication", "Metformin 1000 mg PO BID"),
                   _feed("ADD", "active_medication", "Atorvastatin 40 mg PO daily")])
    _apply_all(m, [_feed("RETRACT", "active_medication", "Warfarin 5 mg PO daily")])
    current = {s.value.split(" ")[0] for s in m.active() if s.subject == "active_medication"}
    assert "Warfarin" not in current
    assert {"Metformin", "Atorvastatin"} <= current         # the rest untouched


def test_a_refuted_allergy_matches_by_substance_not_exact_text():
    """A refute may carry different reaction detail than what was charted. The substance is the
    identity: 'Penicillin — Anaphylaxis (severe)' is retracted by a refute of 'Penicillin —
    Anaphylaxis', because both are the penicillin allergy."""
    m = LLMMemory()
    _apply_all(m, [_feed("ADD", "allergy", "Penicillin — Anaphylaxis (severe)")])
    _apply_all(m, [_feed("RETRACT", "allergy", "Penicillin — Anaphylaxis")])
    assert not any("Penicillin" in s.value for s in m.active() if s.subject == "allergy")


def test_identity_matching_does_not_hit_a_different_drug():
    """Stopping warfarin must not remove metformin — different identities, no cross-match."""
    m = LLMMemory()
    _apply_all(m, [_feed("ADD", "active_medication", "Warfarin 5 mg PO daily"),
                   _feed("ADD", "active_medication", "Metformin 1000 mg PO BID")])
    _apply_all(m, [_feed("RETRACT", "active_medication", "Warfarin 5 mg PO daily")])
    current = {s.value.split(" ")[0] for s in m.active() if s.subject == "active_medication"}
    assert current == {"Metformin"}


# ── longitudinal patient acceptance suite ──

@pytest.mark.parametrize("patient", PATIENTS, ids=lambda p: p["patient_id"])
def test_every_clinical_guarantee_holds(patient):
    m = ingest(patient)
    failures = [label for good, label in check(patient, m) if not good]
    assert not failures, f"{patient['patient_id']}: " + "; ".join(failures)


def test_a_second_allergy_does_not_delete_the_first():
    """The catastrophic bug realistic data found: single-valued allergy slots made a second allergy
    supersede the first, so penicillin + sulfa left only one on the chart."""
    from vayl.memory.llm_memory import LLMMemory, Status
    m = LLMMemory()
    for val in ("penicillin — anaphylaxis", "sulfa — rash", "latex — contact dermatitis"):
        m._apply({"action": "ADD", "subject": "allergy", "value": val, "scope": "global",
                  "confidence": 0.95, "time_ref": "present", "kind": "state"}, val)
    active = [s.value for s in m.active() if s.subject == "allergy"]
    assert len(active) == 3                       # all three coexist, none deleted
    assert all(s.status is Status.ACTIVE for s in m.active())


def test_a_repeated_allergy_still_dedups_on_a_list_slot():
    """Coexistence is for DISTINCT allergies; the same one re-entered is still one."""
    from vayl.memory.llm_memory import LLMMemory
    m = LLMMemory()
    for _ in range(3):
        m._apply({"action": "ADD", "subject": "allergy", "value": "penicillin", "scope": "global",
                  "confidence": 0.95, "time_ref": "present", "kind": "state"}, "penicillin allergy")
    assert len([s for s in m.active() if s.subject == "allergy"]) == 1


def test_stopping_one_list_medication_does_not_touch_the_others():
    """A retract on a list slot must target the named drug by value, not an arbitrary member."""
    from vayl.memory.llm_memory import LLMMemory
    m = LLMMemory()
    for drug in ("warfarin 5 mg daily", "metformin 500 mg BID", "atorvastatin 40 mg daily"):
        m._apply({"action": "ADD", "subject": "active_medication", "value": drug, "scope": "global",
                  "confidence": 0.95, "time_ref": "present", "kind": "state"}, drug)
    # stop warfarin — a confirm slot, so it proposes; the OTHER two must be untouched and current
    m._apply({"action": "RETRACT", "subject": "active_medication", "value": "warfarin 5 mg daily",
              "scope": "global", "confidence": 0.95, "time_ref": "present", "kind": "state"},
             "hold warfarin")
    current = {s.value for s in m.active()}
    assert "metformin 500 mg BID" in current and "atorvastatin 40 mg daily" in current
    assert "warfarin 5 mg daily" in current       # held, not removed until approved
    assert len(m.pending()) == 1
