"""
Clinical integration — FHIR ingestion, discharge medication reconciliation, and the longitudinal patient acceptance suite.
"""

import os

import pytest  # noqa: E402

os.environ.setdefault("VAYL_SLOT_SCHEMA",
                      os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                   "examples", "clinical-slots.json"))
os.environ.setdefault("VAYL_CRITICAL_CATEGORIES", "critical")
os.environ.setdefault("VAYL_SLOT_RESOLVE", "1")
from benchmarks.clinical.patients import PATIENTS  # noqa: E402  # noqa: E402
from benchmarks.clinical.run import check, ingest  # noqa: E402  # noqa: E402
from vayl.clinical.fhir import bundle_to_facts, resource_to_facts, to_facts  # noqa: E402  # noqa: E402
from vayl.clinical.medrec import _drug_identity, reconcile_medications, render  # noqa: E402  # noqa: E402
from vayl.memory import llm_memory  # noqa: E402  # noqa: E402
from vayl.memory.llm_memory import LLMMemory, Status, is_critical  # noqa: E402  # noqa: E402
from vayl.memory.schema import load as _load_schema  # noqa: E402  # noqa: E402

# ══════════════════════════════════════════════════════════════════
# from test_fhir_adapter
# ══════════════════════════════════════════════════════════════════

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


def allergy(substance, manifestation, severity="severe", verification="confirmed", clinical="active"):
    return {
        "resourceType": "AllergyIntolerance",
        "clinicalStatus": {"coding": [{"code": clinical}]},
        "verificationStatus": {"coding": [{"code": verification}]},
        "code": {"text": substance},
        "reaction": [{"manifestation": [{"text": manifestation}], "severity": severity}],
    }


def med(name, dosage, status="active"):
    return {
        "resourceType": "MedicationRequest",
        "status": status,
        "medicationCodeableConcept": {"text": name},
        "dosageInstruction": [{"text": dosage}],
    }


def condition(name, clinical="active", category="encounter-diagnosis"):
    return {
        "resourceType": "Condition",
        "clinicalStatus": {"coding": [{"code": clinical}]},
        "category": [{"coding": [{"code": category}]}],
        "code": {"text": name},
    }


def bundle(*resources):
    return {"resourceType": "Bundle", "type": "collection",
            "entry": [{"resource": r} for r in resources]}


def test_confirmed_allergy_maps_to_an_add():
    facts = resource_to_facts(allergy("Penicillin", "Anaphylaxis"))
    assert len(facts) == 1
    f = facts[0]
    assert f["subject"] == "allergy" and f["action"] == "ADD" and f["source"] == "fhir"
    assert "Penicillin" in f["value"] and "Anaphylaxis" in f["value"] and "severe" in f["value"]


def test_refuted_allergy_retracts_rather_than_adds():
    """The safety case: a disproven allergy must come OFF the chart, never onto it."""
    facts = resource_to_facts(allergy("Penicillin", "Rash", verification="refuted"))
    assert facts[0]["action"] == "RETRACT"


def test_entered_in_error_allergy_retracts():
    facts = resource_to_facts(allergy("Sulfa", "Rash", verification="entered-in-error"))
    assert facts[0]["action"] == "RETRACT"


def test_resolved_allergy_retracts():
    facts = resource_to_facts(allergy("Egg", "Hives", clinical="resolved"))
    assert facts[0]["action"] == "RETRACT"


def test_allergy_without_a_substance_is_skipped():
    assert resource_to_facts({"resourceType": "AllergyIntolerance", "reaction": []}) == []


def test_active_medication_maps_verbatim():
    f = resource_to_facts(med("Warfarin", "5 mg PO daily"))[0]
    assert f["subject"] == "active_medication" and f["action"] == "ADD"
    assert f["value"] == "Warfarin 5 mg PO daily"


@pytest.mark.parametrize("status", ["stopped", "cancelled", "completed", "entered-in-error"])
def test_discontinued_medication_retracts(status):
    assert resource_to_facts(med("Warfarin", "5 mg PO daily", status=status))[0]["action"] == "RETRACT"


def test_on_hold_medication_stays_on_the_list():
    """on-hold is held, not discontinued — it remains a current medication."""
    assert resource_to_facts(med("Metformin", "1000 mg BID", status="on-hold"))[0]["action"] == "ADD"


def test_encounter_diagnosis_maps_to_primary_diagnosis():
    f = resource_to_facts(condition("Community-acquired pneumonia"))[0]
    assert f["subject"] == "primary_diagnosis" and f["value"] == "Community-acquired pneumonia"


def test_problem_list_condition_maps_to_problem_list():
    f = resource_to_facts(condition("Type 2 diabetes", category="problem-list-item"))[0]
    assert f["subject"] == "problem_list"


def test_resolved_condition_becomes_history():
    f = resource_to_facts(condition("Pneumonia", clinical="resolved"))[0]
    assert f["time_ref"] == "past"


def test_code_status_observation_maps():
    obs = {"resourceType": "Observation",
           "code": {"text": "Resuscitation status"},
           "valueString": "DNR / DNI"}
    f = resource_to_facts(obs)[0]
    assert f["subject"] == "code_status" and f["value"] == "DNR / DNI"


def test_unrecognised_observation_is_skipped():
    obs = {"resourceType": "Observation", "code": {"text": "Body temperature"},
           "valueString": "37.2 C"}
    assert resource_to_facts(obs) == []


def test_bundle_maps_every_recognised_entry_in_order():
    b = bundle(allergy("Penicillin", "Anaphylaxis"),
               med("Warfarin", "5 mg PO daily"),
               condition("CAP"),
               {"resourceType": "Practitioner", "name": [{"text": "Dr X"}]})  # unhandled -> skipped
    facts = bundle_to_facts(b)
    assert [f["subject"] for f in facts] == ["allergy", "active_medication", "primary_diagnosis"]


def test_to_facts_accepts_a_bundle_or_a_bare_resource():
    assert len(to_facts(bundle(med("Warfarin", "5 mg daily")))) == 1
    assert len(to_facts(med("Warfarin", "5 mg daily"))) == 1
    assert to_facts(None) == [] and to_facts({"resourceType": "Unknown"}) == []


def _apply_all(m, facts):
    for f in facts:
        m._apply(f, f.get("value", ""), source=f.get("source", "fhir"))


def test_a_fhir_bundle_produces_correct_current_truth():
    m = LLMMemory()
    _apply_all(m, to_facts(bundle(
        allergy("Penicillin", "Anaphylaxis"),
        allergy("Sulfa", "Rash", severity="moderate"),      # multi: BOTH allergies coexist
        med("Warfarin", "5 mg PO daily"),
        med("Metformin", "1000 mg PO BID"),
        condition("Community-acquired pneumonia"),
    )))
    allergies = {s.value.split(" —")[0] for s in m.active() if s.subject == "allergy"}
    assert allergies == {"Penicillin", "Sulfa"}             # neither deleted the other
    assert all(is_critical(s) for s in m.active() if s.subject == "allergy")
    meds = {s.value for s in m.active() if s.subject == "active_medication"}
    assert meds == {"Warfarin 5 mg PO daily", "Metformin 1000 mg PO BID"}


def test_a_refuted_allergy_removes_it_from_the_reconciled_chart():
    """The full safety path: an allergy is charted, then refuted via a later FHIR resource — it must
    leave the active chart, kept in history."""
    m = LLMMemory()
    _apply_all(m, to_facts(allergy("Penicillin", "Rash")))
    assert any(s.subject == "allergy" for s in m.active())

    _apply_all(m, to_facts(allergy("Penicillin", "Rash", verification="refuted")))
    active_allergies = [s.value for s in m.active() if s.subject == "allergy"]
    assert not any("Penicillin" in v for v in active_allergies)   # off the active chart
    assert any(s.subject == "allergy" and s.status is not Status.ACTIVE for s in m.statements)


def test_a_narrative_change_is_still_gated_while_fhir_is_trusted():
    """The bypass is source-scoped: a FHIR order applies directly, but a change from the narrative
    path (source not trusted) still queues for review. Both on the same confirm slot."""
    m = LLMMemory()
    _apply_all(m, to_facts(med("Warfarin", "5 mg PO daily")))          # fhir -> active
    # a narrative-derived stop (untrusted source) must still propose, not apply
    m._apply({"action": "RETRACT", "subject": "active_medication", "value": "Warfarin 5 mg PO daily",
              "scope": "global", "confidence": 0.9, "time_ref": "present"},
             "note says consider stopping warfarin", source="note_extractor")
    assert any("Warfarin" in s.value for s in m.active())              # still current
    assert len(m.pending()) == 1                                       # queued for a human


def test_a_stopped_medication_leaves_the_list_but_others_remain():
    m = LLMMemory()
    _apply_all(m, to_facts(bundle(
        med("Warfarin", "5 mg PO daily"),
        med("Metformin", "1000 mg PO BID"),
        med("Atorvastatin", "40 mg PO daily"),
    )))
    _apply_all(m, to_facts(med("Warfarin", "5 mg PO daily", status="stopped")))
    current = {s.value.split(" ")[0] for s in m.active() if s.subject == "active_medication"}
    assert "Warfarin" not in current
    assert {"Metformin", "Atorvastatin"} <= current         # the rest untouched


def test_a_refuted_allergy_matches_by_substance_not_exact_text():
    """A refute may carry different reaction detail than what was charted. The substance is the
    identity: 'Penicillin — Anaphylaxis (severe)' is retracted by a refute of 'Penicillin —
    Anaphylaxis', because both are the penicillin allergy."""
    m = LLMMemory()
    _apply_all(m, to_facts(allergy("Penicillin", "Anaphylaxis", severity="severe")))
    _apply_all(m, to_facts(allergy("Penicillin", "Anaphylaxis", severity=None, verification="refuted")))
    assert not any("Penicillin" in s.value for s in m.active() if s.subject == "allergy")


def test_identity_matching_does_not_hit_a_different_drug():
    """Stopping warfarin must not remove metformin — different identities, no cross-match."""
    m = LLMMemory()
    _apply_all(m, to_facts(bundle(med("Warfarin", "5 mg PO daily"),
                                  med("Metformin", "1000 mg PO BID"))))
    _apply_all(m, to_facts(med("Warfarin", "5 mg PO daily", status="stopped")))
    current = {s.value.split(" ")[0] for s in m.active() if s.subject == "active_medication"}
    assert current == {"Metformin"}


# ══════════════════════════════════════════════════════════════════
# from test_medrec
# ══════════════════════════════════════════════════════════════════





def _mr_med(value, action="ADD", source="inpatient"):
    return {"subject": "active_medication", "value": value, "action": action, "kind": "state",
            "scope": "global", "time_ref": "present", "confidence": 0.95, "source": source}


def _build():
    """A patient with one medication in every reconciliation category."""
    m = LLMMemory()
    home = lambda v: _mr_med(v, source="bpmh")                       # noqa: E731
    inpt = lambda v, a="ADD": _mr_med(v, action=a, source="inpatient")  # noqa: E731
    # home list
    for v in ("metoprolol 25 mg PO BID", "atorvastatin 20 mg PO nightly",
              "lisinopril 10 mg PO daily", "furosemide 20 mg PO daily"):
        m._apply(home(v), v, source="bpmh")
    # inpatient changes
    m._apply(inpt("atorvastatin 80 mg PO nightly", "SUPERSEDE"), "uptitrate", source="inpatient")
    m._apply(inpt("lisinopril 10 mg PO daily", "RETRACT"), "hold ACEi", source="inpatient")
    m._apply(inpt("aspirin 81 mg PO daily"), "ASA start", source="inpatient")
    # a pharmacy DRAFT (untrusted) — should be HELD
    m._apply(_mr_med("metoprolol 50 mg PO daily", "SUPERSEDE", source="pharmacy_draft"),
             "consolidate", source="pharmacy_draft")
    m._apply({"subject": "allergy", "value": "penicillin — hives", "action": "ADD", "kind": "state",
              "scope": "global", "time_ref": "present", "confidence": 0.95}, "pcn")
    return m


def _by(mr, status):
    return {i.drug for i in mr.by_status(status)}


def test_drug_identity_ignores_dose_and_tombstone():
    assert _drug_identity("Warfarin 5 mg PO daily") == "warfarin"
    assert _drug_identity("(retracted: warfarin 5 mg PO daily)") == "warfarin"
    assert _drug_identity("metoprolol 25 mg PO BID") == "metoprolol"


def test_every_reconciliation_category_is_classified():
    mr = reconcile_medications(_build())
    assert _by(mr, "CONTINUED") == {"furosemide"}
    assert _by(mr, "CHANGED") == {"atorvastatin"}
    assert _by(mr, "STOPPED") == {"lisinopril"}
    assert _by(mr, "NEW") == {"aspirin"}
    assert _by(mr, "HELD") == {"metoprolol"}


def test_changed_item_carries_both_home_and_current_values():
    mr = reconcile_medications(_build())
    atv = next(i for i in mr.items if i.drug == "atorvastatin")
    assert "20 mg" in atv.home_value and "80 mg" in atv.current_value


def test_held_item_keeps_the_home_dose_current_until_approved():
    """A pharmacy draft proposes a change; the drug is unchanged and the item is HELD, not applied."""
    mr = reconcile_medications(_build())
    metop = next(i for i in mr.items if i.drug == "metoprolol")
    assert metop.status == "HELD"
    assert "25 mg" in metop.current_value          # still the home dose, not the proposed 50


def test_allergies_are_surfaced():
    mr = reconcile_medications(_build())
    assert any("penicillin" in a for a in mr.allergies)


def test_a_held_item_is_not_reported_as_stopped():
    """The safety distinction: a proposed change must never read as a completed stop."""
    mr = reconcile_medications(_build())
    assert "metoprolol" not in _by(mr, "STOPPED")


def test_render_marks_the_list_not_final_when_items_are_held():
    text = render(reconcile_medications(_build()), title="Test", color=False)
    assert "AWAITING CLINICIAN DECISION" in text
    assert "NOT final" in text
    assert "ALLERGIES" in text


def test_render_is_clean_when_nothing_is_held():
    m = LLMMemory()
    m._apply(_mr_med("aspirin 81 mg daily", source="bpmh"), "asa", source="bpmh")
    text = render(reconcile_medications(m), color=False)
    assert "NOT final" not in text
    assert "CONTINUED" in text


def test_dorothy_reconciliation_matches_the_declared_expectation():
    from benchmarks.clinical.patients import DOROTHY
    from benchmarks.clinical.run import ingest

    mr = reconcile_medications(ingest(DOROTHY))
    exp = DOROTHY["expectations"]["medrec"]
    for status, drugs in exp.items():
        assert _by(mr, status) == set(drugs), f"{status}: {_by(mr, status)} != {set(drugs)}"


# ══════════════════════════════════════════════════════════════════
# from test_clinical_acceptance
# ══════════════════════════════════════════════════════════════════





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
