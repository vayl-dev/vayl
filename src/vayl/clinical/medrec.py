"""
Discharge medication reconciliation.

Medication reconciliation at care transitions — comparing what a patient took at home against what
they are on now, to produce a correct discharge list — is one of the highest-yield patient-safety
processes in a hospital, and one of the most error-prone. It is also, structurally, exactly what a
reconciling memory has been doing all along: the home list, the inpatient changes, the stops and the
new starts are already in Vayl's history. This module reconstructs the reconciliation from that
history rather than asking anyone to redo it.

Each medication is classified by comparing its earliest HOME fact (a med the patient was on before
admission, ingested with a home source such as a Best Possible Medication History) against its
current reconciled state:

    CONTINUED   on the home list, unchanged, still active
    CHANGED     on the home list, active, but dose/route differs from home
    STOPPED     on the home list, discontinued during the stay
    NEW         started this admission, not a home med
    HELD        a stop/change is proposed but AWAITING a clinician decision (not final)

The report also surfaces what a discharging clinician must not miss: allergies (never let a
discharge script be written against an unseen allergy), and any unresolved conflict still flagged
for review. Nothing here decides — it presents the reconciled picture and marks what still needs a
human, which is the correct division of labour for a discharge.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from vayl.memory.llm_memory import _list_identity
from vayl.memory.reconcile import Status

DEFAULT_HOME_SOURCES = ("bpmh", "home", "home_med", "outpatient")
_MED_PREFIX = "active_medication"
_TOMBSTONE = "(retracted:"


def _drug_identity(value: str) -> str:
    """The drug name that identifies a medication across its lineage, tombstones included.

    A retraction tombstone stores '(retracted: warfarin 5 mg PO daily)'; strip that wrapper before
    taking the identity, so a stopped drug groups with its own history rather than under '(retracted'.
    """
    v = str(value or "")
    if v.startswith(_TOMBSTONE):
        v = v[len(_TOMBSTONE):].rstrip(")").strip()
    return _list_identity(v)


@dataclass
class MedItem:
    drug: str
    status: str                       # CONTINUED | CHANGED | STOPPED | NEW | HELD
    home_value: str = ""
    current_value: str = ""
    detail: str = ""                  # the source note behind the current/most-recent state


@dataclass
class MedRec:
    items: list = field(default_factory=list)
    allergies: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)   # flagged conflicts still needing review

    def by_status(self, status):
        return [i for i in self.items if i.status == status]


def _med_statements(memory):
    return [s for s in memory.statements if s.subject.startswith(_MED_PREFIX)]


def reconcile_medications(memory, home_sources=DEFAULT_HOME_SOURCES) -> MedRec:
    """Reconstruct the discharge medication reconciliation from Vayl's history."""
    home_sources = {s.lower() for s in home_sources}
    groups: dict[str, list] = defaultdict(list)
    for s in _med_statements(memory):
        groups[_drug_identity(s.value)].append(s)

    # a pending stop/change is keyed on the drug it targets, so a HELD item is not called STOPPED
    pending_by_drug = {_drug_identity(p.value): p for p in memory.pending()
                       if p.subject.startswith(_MED_PREFIX)}

    items = []
    for drug, group in groups.items():
        group.sort(key=lambda s: s.id)
        home = next((s for s in group
                     if str(getattr(s, "source", "")).lower() in home_sources), None)
        current = next((s for s in group if s.status is Status.ACTIVE), None)
        pend = pending_by_drug.get(drug)

        if pend is not None and current is not None:
            # a change was proposed but not applied: the drug is unchanged and the decision is open
            status = "HELD"
        elif current is not None and home is not None:
            status = "CONTINUED" if current.value == home.value else "CHANGED"
        elif current is not None:
            status = "NEW"
        elif home is not None:
            status = "STOPPED"
        else:
            # appeared and left entirely within the stay, never a home med and not current — a
            # transient inpatient course (a completed antibiotic). Report as STOPPED for the record.
            status = "STOPPED"

        # the note behind the current state: the pending proposal, the active fact, or — for a
        # stopped med — the retraction tombstone (the last statement in the lineage), which carries
        # the reason it was stopped.
        detail = ((pend.raw if pend else None)
                  or (current.raw if current else None)
                  or (group[-1].raw if group else None) or "")
        items.append(MedItem(
            drug=drug,
            status=status,
            home_value=home.value if home else "",
            current_value=(current.value if current else
                           (pend.value if pend else "")),
            detail=detail,
        ))

    allergies = sorted(s.value for s in memory.active() if s.subject == "allergy")
    unresolved = [(s.subject, s.value) for s in memory.statements
                  if s.status is Status.FLAGGED and not (s.metadata or {}).get("pending")]

    items.sort(key=lambda i: (["HELD", "STOPPED", "CHANGED", "NEW", "CONTINUED"].index(i.status),
                              i.drug))
    return MedRec(items=items, allergies=allergies, unresolved=unresolved)


# ── rendering ────────────────────────────────────────────────────────────────

_HEAD = {
    "CONTINUED": "CONTINUED (unchanged from home)",
    "CHANGED":   "CHANGED during admission",
    "NEW":       "STARTED this admission",
    "STOPPED":   "STOPPED this admission",
    "HELD":      "AWAITING CLINICIAN DECISION — do not finalize",
}
_ORDER = ["HELD", "CHANGED", "STOPPED", "NEW", "CONTINUED"]


def render(medrec: MedRec, title: str = "", color: bool = True) -> str:
    b = ("\033[1m", "\033[0m", "\033[2m", "\033[31m", "\033[33m") if color else ("", "", "", "", "")
    BOLD, RESET, DIM, RED, YEL = b
    out = []
    out.append(f"{BOLD}DISCHARGE MEDICATION RECONCILIATION{RESET}"
               + (f"{DIM} — {title}{RESET}" if title else ""))
    out.append("")

    for status in _ORDER:
        items = medrec.by_status(status)
        if not items:
            continue
        flag = YEL if status == "HELD" else ""
        out.append(f"{flag}{BOLD}{_HEAD[status]}{RESET}")
        for it in items:
            if status == "CHANGED":
                out.append(f"  • {it.drug}: {DIM}home{RESET} {it.home_value}  →  {it.current_value}")
            elif status == "HELD":
                out.append(f"  • {it.current_value or it.drug}   {DIM}[{it.detail[:60]}]{RESET}")
            elif status == "STOPPED":
                shown = it.home_value or it.current_value or it.drug
                note = f"   {DIM}[{it.detail[:60]}]{RESET}" if it.detail.strip() else ""
                out.append(f"  • {shown.replace(_TOMBSTONE, '').rstrip(')')}{note}")
            else:
                out.append(f"  • {it.current_value or it.drug}")
        out.append("")

    if medrec.allergies:
        out.append(f"{RED}{BOLD}ALLERGIES — verify before prescribing{RESET}")
        for a in medrec.allergies:
            out.append(f"  • {a}")
        out.append("")

    if medrec.unresolved:
        out.append(f"{YEL}{BOLD}UNRESOLVED — needs review before discharge{RESET}")
        for subject, value in medrec.unresolved:
            out.append(f"  • {subject}: {value}")
        out.append("")

    held = medrec.by_status("HELD")
    if held:
        out.append(f"{YEL}This list is NOT final: {len(held)} item(s) await a clinician "
                   f"decision.{RESET}")
    return "\n".join(out)
