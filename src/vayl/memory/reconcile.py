#!/usr/bin/env python3
"""
Vayl — reconciliation stress-test harness
================================================
Purpose: prove (or break) the core bet — that a memory layer can RECONCILE
contradictions instead of hoarding them, and, crucially, that it can tell the
difference between "resolve this" and "I'm not sure — flag it."

The thesis under test: the product's job is not perfect auto-resolution
(impossible). It's to NEVER be *silently wrong*. Clear cases auto-resolve;
ambiguous cases get FLAGGED, not guessed. The metric that matters is
`SILENTLY_WRONG` — it must be ~0.

This file is intentionally dependency-free (pure stdlib) so you can run it now:
    python3 -m vayl.memory.reconcile

The classifier here is a transparent HEURISTIC. Slot normalization + relation
classification is the real R&D; this shows the ARCHITECTURE and where the hard
edges are. `classify()` is a clean seam — swap in an LLM classifier and re-run
the same cases to measure whether the LLM does better than the heuristic.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# Domain model  (event log → statements → current view)

class Status(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    FLAGGED = "FLAGGED_CONFLICT"   # honest uncertainty: surfaced, not resolved
    HISTORICAL = "HISTORICAL"      # recorded past fact — never the current belief (valid-time)

class Action(str, Enum):
    ADD = "ADD"                # new slot, no conflict
    DEDUP = "DEDUP"            # same fact already known
    REFINE = "REFINE"          # more detail on an existing fact
    SUPERSEDE = "SUPERSEDE"    # contradicts an active fact -> retire old
    COEXIST = "COEXIST"        # differs but different SCOPE -> both true
    FLAG = "FLAG"              # ambiguous -> surface both, don't guess
    SKIP = "SKIP"              # not a durable fact (hypothetical/sarcasm)
    ARCHIVE = "ARCHIVE"        # valid-time gate: a PAST statement — record as history, never active
    RETRACT = "RETRACT"        # removal with NO replacement: retire the fact, leave the slot empty

_counter = itertools.count(1)

@dataclass
class Statement:
    slot: str                  # subject + scope -> the contradiction key
    subject: str
    value: str
    scope: str
    status: Status = Status.ACTIVE
    confidence: float = 1.0
    supersedes: Optional[int] = None
    raw: str = ""
    id: int = field(default_factory=lambda: next(_counter))
    metadata: Optional[dict] = None
    source: str = ""                  # who/what asserted this fact — belief provenance
    # Graph triple this fact projects to. Persisted so the Neo4j projection can be REBUILT from the
    # store; without them the graph is an unrecoverable side-store rather than a projection.
    head: str = ""
    relation: str = ""
    tail: str = ""

# Heuristic classifier  (THE SEAM — replace with an LLM to compare)

# Real slot normalization is the crux; this keyword map is the honest stand-in.
TOPICS = {
    "state_management": ["state management", "zustand", "redux", "mobx", "recoil", "jotai", "context api"],
    "api_casing":       ["snake_case", "camelcase", "casing", "json keys", "response keys"],
    "auth":             ["auth", "authentication", "jwt", "oauth", "rs256", "login"],
    "database":         ["database", "postgres", "postgresql", "mysql", "mongo", "rds", "db "],
    "deploy_schedule":  ["deploy", "release", "ship on", "ship every", "deployment"],
    "api_style":        ["graphql", "rest api", "grpc", "api style"],
}
KNOWN_VALUES = {
    "state_management": ["zustand", "redux toolkit", "redux", "mobx", "recoil", "jotai", "context api"],
    "api_casing":       ["snake_case", "camelcase"],
    "auth":             ["jwt", "oauth", "rs256", "hs256"],
    "database":         ["postgresql", "postgres", "mysql", "mongodb", "mongo"],
    "deploy_schedule":  ["monday", "tuesday", "wednesday", "thursday", "friday", "daily", "continuous"],
    "api_style":        ["graphql", "rest", "grpc"],
}
# Toy stand-in for the real entity/value normalization layer — the actual hard work.
SYNONYMS = {"postgres": "postgresql", "redux": "redux toolkit", "mongo": "mongodb"}
# Markers that tag the value being RETIRED (so we don't mistake it for the new one).
NEGATIVE_MARKERS = ["forget", "dropping", "dropped", "against", "moving off", "move off",
                    "no longer", "abandon", "abandoning", "instead of", "away from"]

SCOPE_MARKERS = {
    "web": ["web app", "on web", "frontend", "the web"],
    "mobile": ["mobile app", "on mobile", "the mobile"],
    "backend": ["backend", "server side", "the api service"],
}
SUPERSEDE_MARKERS = ["forget", "switched", "moved to", "no longer", "instead of", "actually",
                     "update:", "changed to", "dropping", "abandoning", "we now use",
                     "standardiz", "migrat", "replaced"]
HYPOTHETICAL_MARKERS = ["what if", "should we", "considering", "thinking about", "maybe",
                        "might ", "could ", "hypothetically", "not sure", "wondering", "proposal"]
# DEFINITE removal language — the fact ends and nothing takes its place. Deliberately narrower than
# NEGATIVE_MARKERS: every phrase here asserts the removal as done, so a hedge ("considering dropping
# X") is excluded by HYPOTHETICAL_MARKERS and never reaches a RETRACT. Over-deleting a still-true
# fact is the failure this list has to avoid.
RETRACT_MARKERS = ["no longer", "dropped", "stopped using", "stopped supporting", "got rid of",
                   "removed", "is gone", "are gone", "discontinued", "decommissioned", "sunset",
                   "retired ", "we don't use", "we do not use", "no more", "deprecated",
                   "shut down", "turned off", "cancelled", "canceled"]
SARCASM_MARKERS = ["🙄", "oh sure", "yeah right", "just kidding", "/s", "lol", "as if"]

def _norm(t: str) -> str:
    return " " + t.lower().strip() + " "

def detect_subject(text: str) -> Optional[str]:
    t = _norm(text)
    for subj, kws in TOPICS.items():
        if any(k in t for k in kws):
            return subj
    return None

def canon(v: Optional[str]) -> Optional[str]:
    return SYNONYMS.get(v, v) if v else v

def _values_in(text: str, subject: str) -> dict[str, int]:
    """Canonical value -> earliest position, longest-match-first to avoid substrings."""
    t = text.lower()
    hits: dict[str, int] = {}
    for v in sorted(KNOWN_VALUES.get(subject, []), key=len, reverse=True):
        pos = t.find(v)
        if pos == -1:
            continue
        if any(v in longer for longer in hits):  # skip 'postgres' inside 'postgresql'
            continue
        cv = "redux toolkit" if v in ("redux", "redux toolkit") and "toolkit" in t else canon(v)
        hits.setdefault(cv, pos)
    return hits

def detect_value(text: str, subject: str) -> Optional[str]:
    """The ASSERTED (new) value. If a change is described, return the value that
    is NOT tied to a negation marker (i.e. not the one being retired)."""
    vals = _values_in(text, subject)
    if not vals:
        return None
    if len(vals) == 1:
        return next(iter(vals))
    t = text.lower()
    neg = [t.find(m) for m in NEGATIVE_MARKERS if m in t]
    neg = [p for p in neg if p != -1]
    if neg:
        asserted = [v for v, p in vals.items() if not any(0 <= p - np <= 25 for np in neg)]
        if asserted:
            return sorted(asserted, key=lambda v: vals[v])[-1]
    return sorted(vals, key=lambda v: vals[v])[-1]  # else: last-mentioned wins

def detect_scope(text: str) -> str:
    t = _norm(text)
    for scope, kws in SCOPE_MARKERS.items():
        if any(k in t for k in kws):
            return scope
    return "global"

def has(text: str, markers) -> bool:
    t = _norm(text)
    return any(m in t for m in markers)

@dataclass
class Judgement:
    action: Action
    confidence: float
    reason: str
    target: Optional[Statement] = None   # the statement being superseded/refined

def classify(text: str, active: list[Statement]) -> Judgement:
    """Return the relationship of `text` to the current active statements.

    Honest-uncertainty rule lives in the engine, not here: this returns a
    *confidence*, and the engine decides auto-resolve vs FLAG on the threshold.
    """
    subject = detect_subject(text)

    if has(text, SARCASM_MARKERS):
        return Judgement(Action.SKIP, 0.95, "sarcasm marker — not a decision")
    if has(text, HYPOTHETICAL_MARKERS):
        return Judgement(Action.SKIP, 0.75, "hypothetical/uncertain — not a firm decision")

    if subject is None:
        # Unknown topic: we can't normalize a slot. Honest move: add, low conf.
        return Judgement(Action.ADD, 0.4, "no known slot — stored unreconciled")

    scope = detect_scope(text)
    value = detect_value(text, subject) or "(unspecified)"
    same_slot = [s for s in active if s.subject == subject]

    if not same_slot:
        return Judgement(Action.ADD, 0.9, f"new slot {subject}")

    adds_detail = bool(re.search(r"\d", text)) or " on " in _norm(text)
    for s in same_slot:
        if canon(s.value) == value and s.scope == scope:
            if adds_detail:
                return Judgement(Action.REFINE, 0.8, f"adds detail to '{s.value}'", s)
            return Judgement(Action.DEDUP, 0.9, "same value already known", s)

    conflict = next((s for s in same_slot if canon(s.value) != value and value != "(unspecified)"), None)
    if conflict is None:
        return Judgement(Action.ADD, 0.6, "same subject, non-conflicting", None)

    if scope != "global" and conflict.scope != scope:
        return Judgement(Action.COEXIST, 0.85,
                         f"different scope ({conflict.scope} vs {scope}) — both true", conflict)

    if has(text, SUPERSEDE_MARKERS):
        return Judgement(Action.SUPERSEDE, 0.9,
                         f"explicit change '{conflict.value}' -> '{value}'", conflict)

    # Same slot, same scope, different value, no explicit change word.
    # This is the genuinely ambiguous case. DO NOT GUESS.
    return Judgement(Action.FLAG, 0.5,
                     f"conflicting value for {subject} ({conflict.value} vs {value}), intent unclear",
                     conflict)

# Reconciliation engine  (honest-uncertainty: auto if confident, else FLAG)

AUTO_THRESHOLD = 0.7   # below this on a resolving action, we flag instead of guess

class Engine:
    def __init__(self, classifier=None):
        self.statements: list[Statement] = []
        self.classifier = classifier or classify

    def active(self) -> list[Statement]:
        return [s for s in self.statements if s.status == Status.ACTIVE]

    def add(self, text: str) -> tuple[Action, Judgement]:
        j = self.classifier(text, self.active())
        act = j.action

        # Honest-uncertainty gate: a *resolving* action below threshold -> FLAG.
        if act in (Action.SUPERSEDE, Action.COEXIST, Action.REFINE) and j.confidence < AUTO_THRESHOLD:
            act = Action.FLAG

        if act == Action.SKIP or act == Action.DEDUP:
            return act, j

        subject = detect_subject(text) or "unknown"
        scope = detect_scope(text)
        value = detect_value(text, subject) or "(unspecified)"

        if act == Action.SUPERSEDE and j.target:
            j.target.status = Status.SUPERSEDED
            new = Statement(f"{subject}@{scope}", subject, value, scope,
                            confidence=j.confidence, supersedes=j.target.id, raw=text)
            self.statements.append(new)
        elif act == Action.REFINE and j.target:
            j.target.value = value if value != "(unspecified)" else j.target.value + " (+detail)"
            j.target.raw = text
        elif act == Action.COEXIST:
            self.statements.append(Statement(f"{subject}@{scope}", subject, value, scope,
                                             confidence=j.confidence, raw=text))
        elif act == Action.FLAG:
            self.statements.append(Statement(f"{subject}@{scope}", subject, value, scope,
                                             status=Status.FLAGGED, confidence=j.confidence, raw=text))
        else:  # ADD
            self.statements.append(Statement(f"{subject}@{scope}", subject, value, scope,
                                             confidence=j.confidence, raw=text))
        return act, j

    def current_answer(self, subject: str) -> str:
        act = [s for s in self.active() if s.subject == subject]
        flagged = [s for s in self.statements if s.subject == subject and s.status == Status.FLAGGED]
        if flagged:
            vals = sorted({s.value for s in act} | {s.value for s in flagged})
            return f"⚠ UNRESOLVED CONFLICT: {', '.join(vals)} — needs confirmation"
        if not act:
            return "(nothing known)"
        if len(act) == 1:
            return act[0].value
        return " · ".join(f"{s.value} [{s.scope}]" for s in act)  # scoped coexistence

# The ugly cases  (input sequence -> expected FINAL action on the last add)

@dataclass
class Case:
    name: str
    inputs: list[str]
    expect: Action          # expected action for the LAST input
    note: str = ""
    novel: bool = False     # outside the heuristic's hand-coded vocabulary

CASES = [
    Case("clean contradiction",
         ["We use Zustand for state management.",
          "We switched to Redux Toolkit, dropping Zustand."],
         Action.SUPERSEDE, "the flagship: a real reversal"),
    Case("explicit forget",
         ["API responses use snake_case JSON keys.",
          "Forget the snake_case rule — we standardized on camelCase."],
         Action.SUPERSEDE, "the word 'forget' must actually retire it"),
    Case("scoped coexistence (NOT a conflict)",
         ["We use Redux Toolkit for state in the web app.",
          "We use Zustand for state in the mobile app."],
         Action.COEXIST, "different scope — deleting either would be WRONG"),
    Case("hypothetical (not a decision)",
         ["We use REST for our API.",
          "What if we moved to GraphQL instead?"],
         Action.SKIP, "a question is not a decision"),
    Case("sarcasm (not a decision)",
         ["Our database is PostgreSQL.",
          "Oh sure, let's just switch everything to MongoDB 🙄"],
         Action.SKIP, "sarcasm must not be stored as a switch"),
    Case("near-duplicate",
         ["Auth lives in src/auth and uses JWT.",
          "Authentication uses JWT."],
         Action.DEDUP, "paraphrase of a known fact"),
    Case("refinement (not a conflict)",
         ["Our database is Postgres.",
          "The database is PostgreSQL 16 on RDS."],
         Action.REFINE, "more detail on the same fact"),
    Case("ambiguous conflict (must FLAG, not guess)",
         ["We deploy on Fridays.",
          "We deploy on Mondays."],
         Action.FLAG, "no change-word, same scope — intent unclear, DON'T guess"),
    Case("unrelated (different slot)",
         ["We use Zustand for state.",
          "Our database is PostgreSQL."],
         Action.ADD, "different subject — no conflict"),
    Case("considering (soft, not firm)",
         ["We use Redux Toolkit.",
          "We're considering moving off Redux."],
         Action.SKIP, "'considering' is not a decision"),

    # NOVEL cases: outside the heuristic's vocabulary, so it should degrade to
    # blind-ADD (i.e. become Mem0). The LLM classifier is the real test.
    Case("NOVEL cloud migration",
         ["We host everything on AWS in eu-west-1.",
          "We migrated off AWS to GCP europe-west1."],
         Action.SUPERSEDE, "cloud provider — no keyword in the heuristic", novel=True),
    Case("NOVEL language rewrite",
         ["The billing service is written in Python.",
          "We rewrote the billing service in Go."],
         Action.SUPERSEDE, "language — outside vocab", novel=True),
    Case("NOVEL scoped by service",
         ["The billing service is written in Go.",
          "The web frontend is written in TypeScript."],
         Action.COEXIST, "different service — coexist, not conflict", novel=True),
    Case("NOVEL negation retract",
         ["We deploy with Kubernetes.",
          "We're not using Kubernetes anymore — moved to plain ECS."],
         Action.SUPERSEDE, "negation + replacement, novel domain", novel=True),
    Case("NOVEL sarcasm (novel domain)",
         ["We use Terraform for infra.",
          "Oh brilliant, let's throw it all out and click around the console by hand 🙄"],
         Action.SKIP, "sarcasm the heuristic can't keyword-match", novel=True),
]

# Runner + scorecard

def run(classifier, label):
    print(f"\n\033[1mVAYL — reconciliation stress test · {label}\033[0m")
    print("=" * 68)
    correct = wrong = flagged_ok = 0
    silently_wrong = 0

    for c in CASES:
        eng = Engine(classifier)
        for inp in c.inputs[:-1]:
            eng.add(inp)
        act, j = eng.add(c.inputs[-1])

        ok = (act == c.expect)
        # A FLAG when we expected a confident action is SAFE (not silently wrong).
        safe_flag = (act == Action.FLAG and c.expect in (Action.SUPERSEDE, Action.COEXIST, Action.REFINE))
        # SILENTLY WRONG = took a confident action that was wrong (the trust-killer).
        confident_wrong = (not ok and not safe_flag and act in
                           (Action.SUPERSEDE, Action.COEXIST, Action.REFINE, Action.ADD, Action.DEDUP, Action.SKIP)
                           and act != Action.FLAG)

        if ok:
            mark, color = "✓ PASS", "\033[32m"; correct += 1
        elif safe_flag:
            mark, color = "⚠ FLAGGED (safe)", "\033[33m"; flagged_ok += 1
        else:
            mark, color = "✗ WRONG", "\033[31m"; wrong += 1
            if confident_wrong:
                silently_wrong += 1

        tag = " \033[35m[NOVEL]\033[0m" if c.novel else ""
        print(f"\n{color}{mark}\033[0m  {c.name}{tag}")
        print(f"     expected {c.expect.value:<9} got {act.value:<9}  ({j.confidence:.2f})")
        print(f"     why: {j.reason}")
        subj = detect_subject(c.inputs[-1]) or detect_subject(c.inputs[0])
        if subj:
            print(f"     current answer for '{subj}': \033[1m{eng.current_answer(subj)}\033[0m")

    total = len(CASES)
    handled = correct + flagged_ok
    print("\n" + "=" * 68)
    print(f"\033[1mSCORECARD — {label}\033[0m")
    print(f"  auto-resolved correctly : {correct}/{total}")
    print(f"  correctly flagged unsure: {flagged_ok}/{total}   (safe — surfaced, not guessed)")
    print(f"  wrong                   : {wrong}/{total}")
    print(f"\n  \033[1mSILENTLY WRONG          : {silently_wrong}/{total}\033[0m   ", end="")
    print("← the only number that kills the product" if silently_wrong == 0 else "← ⚠ each is a broken trust promise")
    print(f"\n  trustworthy (resolved OR safely flagged): {handled}/{total}  ({100*handled//total}%)")
    print("=" * 68)
    return silently_wrong, correct, total


def main():
    # Standalone demo of the heuristic classifier over the CASES table. The LLM path this once
    # compared against lived in llm_classify + LLM_SYSTEM; it was an Anthropic-only prototype
    # never wired to the live engine (which extracts and reconciles in one call via
    # llm_memory.SYS), and a second, drifting copy of the reconciliation rules is a maintenance
    # hazard, so it was removed. `git log` has it if the comparison is ever wanted again.
    run(classify, "HEURISTIC")


if __name__ == "__main__":
    main()
