"""
FHIR → Vayl fact ingestion.

Maps the FHIR R4 resources a hospital already emits — AllergyIntolerance, MedicationRequest /
MedicationStatement, Condition, Observation — into the fact dicts Vayl's engine reconciles. This is
the answer to "how does our data get in": a deployment points this at its FHIR feed and Vayl builds
the reconciling, change-tracking view over the episode.

What this is NOT: a replacement for the EHR as system of record. The coded allergy list lives in
Epic/Oracle and stays there. Vayl's job is the layer above it — reconciling those facts as they
change across an encounter, holding one current truth per slot with a full audit trail, and (via the
LLM extractor, separately) folding in the unstructured clinical narrative that never becomes a coded
FHIR resource. This module handles only the structured half.

The clinical correctness lives in the STATUS mapping, not the value mapping. A FHIR resource carries
its own lifecycle, and getting it wrong is a safety issue:

  * an AllergyIntolerance with verificationStatus=refuted or entered-in-error is not an allergy —
    it must RETRACT, never ADD. Adding it would put a disproven allergy on the chart.
  * a MedicationRequest with status=stopped/cancelled/entered-in-error is a discontinuation — RETRACT.
  * a Condition with clinicalStatus=resolved is history, not a current problem.

Each fact is emitted with the slot, kind, action and (for meds/allergies) verbatim value the engine
expects, plus source="fhir" so a deployment can give structured coded data higher authority than
narrative in source-aware reconciliation.
"""
from __future__ import annotations

from typing import Any

# ── status vocabularies (FHIR R4) ────────────────────────────────────────────
# Statuses that mean "this is not / no longer a live fact" → the fact is retracted rather than added.
_ALLERGY_INACTIVE_VERIFICATION = {"refuted", "entered-in-error"}
_ALLERGY_INACTIVE_CLINICAL = {"inactive", "resolved"}
_MED_STOPPED = {"stopped", "cancelled", "completed", "entered-in-error"}
_CONDITION_RESOLVED = {"resolved", "inactive", "remission"}


def _text(concept: dict | None) -> str:
    """Human-readable text from a FHIR CodeableConcept: prefer .text, then a coding .display/.code."""
    if not concept:
        return ""
    if concept.get("text"):
        return str(concept["text"]).strip()
    for c in concept.get("coding", []) or []:
        if c.get("display"):
            return str(c["display"]).strip()
        if c.get("code"):
            return str(c["code"]).strip()
    return ""


def _status_code(concept_or_str: Any) -> str:
    """FHIR status may be a bare string (MedicationRequest.status) or a CodeableConcept
    (clinicalStatus / verificationStatus). Normalize to the lowercase code."""
    if isinstance(concept_or_str, str):
        return concept_or_str.strip().lower()
    for c in (concept_or_str or {}).get("coding", []) or []:
        if c.get("code"):
            return str(c["code"]).strip().lower()
    return ""


def _fact(subject, value, action="ADD", kind="state", time_ref="present"):
    return {"subject": subject, "value": value, "action": action, "kind": kind,
            "scope": "global", "time_ref": time_ref, "confidence": 0.98, "source": "fhir"}


# ── per-resource mappers ─────────────────────────────────────────────────────

def _from_allergy(r: dict) -> list[dict]:
    substance = _text(r.get("code"))
    if not substance:
        return []
    reactions = []
    for rx in r.get("reaction", []) or []:
        man = "; ".join(_text(m) for m in (rx.get("manifestation") or []) if _text(m))
        sev = rx.get("severity")
        reactions.append(f"{man}{f' ({sev})' if sev else ''}".strip())
    value = substance + (f" — {'; '.join(r for r in reactions if r)}" if any(reactions) else "")

    verification = _status_code(r.get("verificationStatus"))
    clinical = _status_code(r.get("clinicalStatus"))
    if verification in _ALLERGY_INACTIVE_VERIFICATION or clinical in _ALLERGY_INACTIVE_CLINICAL:
        # a refuted / resolved allergy must come OFF the chart, not onto it
        return [_fact("allergy", value, action="RETRACT")]
    return [_fact("allergy", value)]


def _from_medication(r: dict) -> list[dict]:
    med = _text(r.get("medicationCodeableConcept"))
    if not med:
        return []
    dosages = [d.get("text", "").strip() for d in (r.get("dosageInstruction") or []) if d.get("text")]
    value = f"{med} {'; '.join(dosages)}".strip() if dosages else med

    status = _status_code(r.get("status"))
    if status in _MED_STOPPED:
        return [_fact("active_medication", value, action="RETRACT")]
    # active / on-hold / draft → a current (or held) medication on the list
    return [_fact("active_medication", value)]


def _from_condition(r: dict) -> list[dict]:
    name = _text(r.get("code"))
    if not name:
        return []
    categories = {_status_code(c) for cat in (r.get("category") or [])
                  for c in [cat] if isinstance(cat, dict)}
    is_problem = any("problem" in c for c in categories)
    subject = "problem_list" if is_problem else "primary_diagnosis"

    clinical = _status_code(r.get("clinicalStatus"))
    if clinical in _CONDITION_RESOLVED:
        # a resolved condition is history, not a current problem
        return [_fact(subject, name, action="RETRACT" if is_problem else "ADD", time_ref="past")]
    return [_fact(subject, name)]


def _from_observation(r: dict) -> list[dict]:
    """Only the observations that map to a declared clinical slot — code status today. Vitals and
    labs are high-volume and better left in the EHR; surfacing them here would just be noise."""
    code = _text(r.get("code")).lower()
    value = _text(r.get("valueCodeableConcept")) or (r.get("valueString") or "").strip()
    if not value:
        return []
    if "resuscitation" in code or "code status" in code or "dnr" in code:
        return [_fact("code_status", value)]
    return []


_MAPPERS = {
    "AllergyIntolerance": _from_allergy,
    "MedicationRequest": _from_medication,
    "MedicationStatement": _from_medication,
    "Condition": _from_condition,
    "Observation": _from_observation,
}


# ── public API ───────────────────────────────────────────────────────────────

def resource_to_facts(resource: dict) -> list[dict]:
    """Map one FHIR resource to zero or more Vayl fact dicts. Unhandled resource types return []."""
    mapper = _MAPPERS.get((resource or {}).get("resourceType", ""))
    return mapper(resource) if mapper else []


def bundle_to_facts(bundle: dict) -> list[dict]:
    """Map a FHIR Bundle (searchset / transaction / collection) to a flat list of fact dicts, in
    entry order. Anything that is not a recognised resource is skipped, so a mixed bundle is safe."""
    facts = []
    for entry in (bundle or {}).get("entry", []) or []:
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if isinstance(resource, dict):
            facts.extend(resource_to_facts(resource))
    return facts


def to_facts(fhir: dict) -> list[dict]:
    """Accept either a Bundle or a single resource and return fact dicts. The one entry point a
    caller needs."""
    if not isinstance(fhir, dict):
        return []
    if fhir.get("resourceType") == "Bundle":
        return bundle_to_facts(fhir)
    return resource_to_facts(fhir)
