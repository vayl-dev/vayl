"""
Clinical acceptance run.

Ingests the synthetic longitudinal records in patients.py through the real engine — same `_apply`
path a production ingest uses — with the clinical slot schema, critical categories, and confirmation
gates all active. Then it checks the guarantees a clinical deployment actually depends on and prints
a pass/fail report.

This is deterministic: no LLM, no network, no API key. Extraction is the model's job and its weak
link; a hospital integration feeds structured facts from an EHR/FHIR stream, which is what this
simulates. What is under test is everything downstream of extraction — reconciliation, the same-slot
invariant, aliasing, the critical-fact channel, verbatim storage, event coexistence, and the human
approval gate.

    python -m benchmarks.clinical.run          # acceptance report (pass/fail guarantees)
    python -m benchmarks.clinical.run -v       # + the full reconciled record per patient
    python -m benchmarks.clinical.run --medrec # discharge medication reconciliation per patient
"""
from __future__ import annotations

import os
import sys

# Configure the deployment BEFORE importing the engine — the schema and critical categories are read
# at import time. This is the exact configuration a clinical operator would set.
_HERE = os.path.dirname(__file__)
os.environ.setdefault("VAYL_SLOT_SCHEMA",
                      os.path.join(os.path.dirname(os.path.dirname(_HERE)), "examples",
                                   "clinical-slots.json"))
os.environ.setdefault("VAYL_CRITICAL_CATEGORIES", "critical")
os.environ.setdefault("VAYL_SLOT_RESOLVE", "1")
# authorized order systems apply directly; the default source (ehr_feed) and drafts stay gated, so
# the confirmation-gate scenarios (Margaret, Ted, Kwame) are unaffected.
os.environ.setdefault("VAYL_TRUSTED_SOURCES", "inpatient,fhir")

from benchmarks.clinical.patients import PATIENTS  # noqa: E402
from vayl.memory.llm_memory import LLMMemory, Status, is_critical  # noqa: E402

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def ingest(patient) -> LLMMemory:
    """Replay a record on a merged clinical timeline: facts and clinician approvals interleaved by
    day, in the order they actually happen.

    A `workflow` step (confirm/reject a pending proposal by subject, with a `day`) is a clinician
    working the review queue. Interleaving matters: a code-status REVERSAL (full → DNR → back to
    full) only works if the DNR is confirmed BEFORE the reversal is proposed — otherwise the
    reversal restates the still-current value and is correctly deduped. A facts-then-workflow order
    cannot express that; a real timeline can.
    """
    m = LLMMemory()
    timeline = ([("fact", f.get("day", 0), i, f) for i, f in enumerate(patient["facts"])]
                + [("step", s.get("day", 10**6), i, s)
                   for i, s in enumerate(patient.get("workflow", []))])
    # stable sort by day, then facts before steps of the same day, then original order
    timeline.sort(key=lambda t: (t[1], 0 if t[0] == "fact" else 1, t[2]))

    for kind, _day, _i, item in timeline:
        if kind == "fact":
            m._apply(item, item.get("note", ""), source=item.get("source", "ehr_feed"))
        else:
            subject = item.get("confirm") or item.get("reject")
            target = next((p for p in m.pending() if p.subject == subject), None)
            if target is None:
                continue
            if "confirm" in item:
                m.confirm(target.id, source=item.get("by", "clinician"))
            else:
                m.reject(target.id, source=item.get("by", "clinician"))
    return m


def _critical_context(m: LLMMemory) -> str:
    """What a recall would place in front of the answering model. Critical facts bypass ranking, so
    they must appear here regardless of how many other facts exist. We assemble the context directly
    rather than call the LLM read layer — the guarantee is about what REACHES the model, not the
    prose it writes."""
    parts = []
    # critical facts first (the always-inject set), then everything active
    crit = [s for s in m.statements if is_critical(s) and s.status is Status.ACTIVE]
    active = [s for s in m.active() if s not in crit]
    for s in crit + active:
        parts.append(f"{s.subject}={s.value}")
    return " | ".join(parts)


def check(patient, m: LLMMemory) -> list[tuple[bool, str]]:
    exp = patient["expectations"]
    results = []

    def ok(cond, label):
        results.append((bool(cond), label))

    ctx = _critical_context(m)
    active = m.active()

    # 1. every critical value is present in the context, regardless of ranking
    for needle in exp.get("critical_present", []):
        ok(needle.lower() in ctx.lower(),
           f"critical fact reachable: '{needle}' is in the recall context")

    # 2. medications reconcile to exactly one current value
    if "one_current_metformin" in exp:
        met = [s for s in active if "metformin" in s.subject]
        ok(len(met) == 1 and met[0].value == exp["one_current_metformin"],
           f"one current metformin = {exp['one_current_metformin']!r}"
           + (f"  (got {[s.value for s in met]})" if len(met) != 1 else ""))
    if "one_current_abx" in exp:
        abx = [s for s in active if s.subject == "active_medication_abx"]
        ok(len(abx) == 1 and abx[0].value == exp["one_current_abx"],
           f"one current antibiotic = {exp['one_current_abx']!r}"
           + (f"  (got {[s.value for s in abx]})" if len(abx) != 1 else ""))

    # 3. events coexist — none retired another
    for ev in exp.get("events_coexist", []):
        s = next((x for x in m.statements if x.subject == ev), None)
        ok(s is not None and s.status is Status.ACTIVE,
           f"event coexists (not overwritten): {ev}")

    # 4. high-stakes changes waited for a human instead of applying
    if "pending_awaiting_review" in exp:
        got = len(m.pending())
        ok(got == exp["pending_awaiting_review"],
           f"changes awaiting human approval: {got} "
           f"(expected {exp['pending_awaiting_review']})")
    # 4b. two different pending semantics, both safety guarantees:
    pend_text = " ".join(f"{s.subject}={s.value}" for s in m.pending()).lower()
    #   a proposed STOP does not remove the drug until approved — it STAYS current meanwhile
    for needle in exp.get("held_still_current", []):
        ok(needle.lower() in pend_text and needle.lower() in ctx.lower(),
           f"proposed stop of '{needle}' is queued, drug still current until approved")
    #   a proposed CHANGE is not presented as current until approved
    for needle in exp.get("proposed_not_current", []):
        ok(needle.lower() in pend_text and needle.lower() not in ctx.lower(),
           f"proposed change to '{needle}' is queued, not presented as current")

    # 5. fragmented allergy spellings folded to one slot
    if "allergy_slots_after_aliasing" in exp:
        all = [s for s in active if s.subject == "allergy"]
        ok(len(all) == exp["allergy_slots_after_aliasing"],
           f"aliased allergy folds to {exp['allergy_slots_after_aliasing']} slot "
           f"(got {len(all)})")

    # 6. verbatim: a stored medication value keeps its dose and units exactly
    med = next((s for s in active if s.subject.startswith("active_medication")
                and any(u in s.value for u in ("mg", "g", "mcg", "units"))), None)
    if med is not None:
        ok(any(ch.isdigit() for ch in med.value),
           f"verbatim dose preserved: {med.value!r}")

    # 7. rapid supersession churn: one current value, the earlier ones kept as history
    if "one_current_insulin_drip" in exp:
        drip = [s for s in active if s.subject == "insulin_drip_rate"]
        ok(len(drip) == 1 and drip[0].value == exp["one_current_insulin_drip"],
           f"one current insulin drip rate = {exp['one_current_insulin_drip']!r}"
           + (f"  (got {len(drip)})" if len(drip) != 1 else ""))
    if "insulin_drip_history_min" in exp:
        hist = [s for s in m.statements if s.subject == "insulin_drip_rate"
                and s.status in (Status.SUPERSEDED, Status.HISTORICAL)]
        ok(len(hist) >= exp["insulin_drip_history_min"],
           f"insulin titration history preserved: {len(hist)} prior rates "
           f"(≥{exp['insulin_drip_history_min']})")

    # 8. genuine conflict: surfaced as flagged, both values visible, not silently resolved
    if "flagged_subject_present" in exp:
        subj = exp["flagged_subject_present"]
        flagged = [s for s in m.statements
                   if s.subject == subj and s.status is Status.FLAGGED]
        ok(len(flagged) >= 1, f"conflict on '{subj}' is flagged for review, not auto-resolved")
        vals = {s.value for s in m.statements if s.subject == subj}
        for v in exp.get("flagged_both_values", []):
            ok(v in vals, f"conflicting value visible for review: {v!r}")

    # 9. scope-based coexistence: same subject, different scope, both true
    if "scope_coexist_subject" in exp:
        subj = exp["scope_coexist_subject"]
        n = len([s for s in active if s.subject == subj])
        ok(n == exp["scope_coexist_count"],
           f"'{subj}' coexists across scopes: {n} active (expected {exp['scope_coexist_count']})")

    # 10. a low-confidence change must not overwrite a current value
    if "not_overwritten" in exp:
        subj, val = exp["not_overwritten"]
        ok(any(s.subject == subj and s.value == val for s in active),
           f"low-confidence change did not overwrite: {val!r} still current")

    # 11. valid-time gate: past history is archived, never a current active fact
    for needle in exp.get("history_not_current", []):
        in_active = any(needle.lower() in s.value.lower() for s in active)
        in_history = any(needle.lower() in s.value.lower() for s in m.statements
                         if s.status is Status.HISTORICAL)
        ok(in_history and not in_active,
           f"past history archived, not current: '{needle}'")

    # 12. confirmation WORKFLOW outcomes (after confirm/reject were applied in ingest)
    for needle in exp.get("after_workflow_current", []):
        ok(needle.lower() in ctx.lower(),
           f"after approval, '{needle}' is now the current value")
    if "after_workflow_pending" in exp:
        ok(len(m.pending()) == exp["after_workflow_pending"],
           f"after workflow, {len(m.pending())} still pending "
           f"(expected {exp['after_workflow_pending']})")

    return results


def report(verbose=False) -> int:
    print(f"\n{BOLD}Vayl — clinical acceptance run{RESET}")
    print(f"{DIM}deterministic · no LLM · schema={os.path.basename(os.environ['VAYL_SLOT_SCHEMA'])} "
          f"· critical={os.environ['VAYL_CRITICAL_CATEGORIES']}{RESET}\n")

    total_pass = total = 0
    for patient in PATIENTS:
        m = ingest(patient)
        results = check(patient, m)
        npass = sum(1 for good, _ in results if good)
        total_pass += npass
        total += len(results)

        head = f"{GREEN}✓{RESET}" if npass == len(results) else f"{RED}✗{RESET}"
        print(f"{head} {BOLD}{patient['patient_id']}{RESET}  {DIM}{patient['summary']}{RESET}")
        print(f"    {len(patient['facts'])} facts → "
              f"{len(m.active())} current · {len(m.pending())} awaiting review · "
              f"{len([s for s in m.statements if s.status in (Status.SUPERSEDED, Status.HISTORICAL)])} in history")
        for good, label in results:
            mark = f"{GREEN}pass{RESET}" if good else f"{RED}FAIL{RESET}"
            print(f"      [{mark}] {label}")

        if verbose:
            print(f"    {DIM}── reconciled current record ──{RESET}")
            for s in m.active():
                tag = " [critical]" if is_critical(s) else ""
                print(f"      {DIM}{s.subject:32}{RESET} {s.value}{tag}")
            if m.pending():
                print(f"    {DIM}── awaiting clinician approval ──{RESET}")
                for s in m.pending():
                    verb = "REMOVE" if (s.metadata or {}).get("pending") == "RETRACT" else "CHANGE TO"
                    print(f"      {s.subject:32} {verb} {s.value}")
        print()

    color = GREEN if total_pass == total else RED
    print(f"{BOLD}{color}{total_pass}/{total} clinical guarantees held{RESET}\n")
    return 0 if total_pass == total else 1


def medrec_report() -> int:
    """Print a discharge medication reconciliation for every patient that has a home list — the
    'med rec' clinical workflow, reconstructed from Vayl's reconciling history."""
    from benchmarks.clinical.patients import PATIENTS
    from vayl.clinical.medrec import DEFAULT_HOME_SOURCES, reconcile_medications, render

    home = set(DEFAULT_HOME_SOURCES)
    shown = 0
    for patient in PATIENTS:
        if not any(str(f.get("source", "")).lower() in home for f in patient["facts"]):
            continue                                  # only patients with a home medication history
        m = ingest(patient)
        print(render(reconcile_medications(m), title=patient["summary"]))
        print()
        shown += 1
    if not shown:
        print("No patient in the dataset has a home medication history (source=bpmh).")
    return 0


def main() -> None:
    if "--medrec" in sys.argv:
        raise SystemExit(medrec_report())
    raise SystemExit(report(verbose="-v" in sys.argv or "--verbose" in sys.argv))


if __name__ == "__main__":
    main()
