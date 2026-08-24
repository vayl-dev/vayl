#!/usr/bin/env python3
"""
Autonomous-action safety gate (Feature 2) — agents refuse to ACT on memory that isn't safe to act on.

Reconciliation already labels the risky states (FLAGGED conflict, low confidence, superseded, stale);
this turns those labels into a deterministic ACT / DON'T-ACT verdict. The point is not to block reads —
it's to stop an agent from taking an irreversible action (a refund, an email, a config change) on a
fact that is disputed, stale, unsettled, or not actually current. Vayl's promise is "never silently
ACT wrong": when the memory isn't safe to act on, the gate says so, with reasons, instead of proceeding.

The evaluator is pure and `now` is injected, so verdicts are deterministic and offline-testable.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class SafetyPolicy:
    min_confidence: float = 0.7
    require_active: bool = True                          # never act on a superseded/historical fact
    block_on_flagged: bool = True                        # a FLAGGED conflict is unresolved — don't act
    max_staleness_days: Optional[float] = None
    block_on_recent_change_days: Optional[float] = None  # a JUST-changed fact may be volatile — hold


def evaluate_fact(fact, policy, now):
    """Pure verdict for one fact — a provenance dict (status / confidence / supersedes / set_at).
    Returns (ok, reasons). `now` is a unix timestamp, injected for determinism."""
    reasons = []
    status = fact.get("status")
    conf = float(fact.get("confidence") or 0.0)
    set_at = fact.get("set_at")

    if policy.block_on_flagged and status == "FLAGGED_CONFLICT":
        reasons.append("unresolved conflict (FLAGGED) — the value is disputed")
    # A flagged fact has its own reason above; require_active is about retired (superseded/historical) values.
    if policy.require_active and status not in (None, "ACTIVE", "FLAGGED_CONFLICT"):
        reasons.append(f"not current (status {status}) — acting on a retired value")
    if conf < policy.min_confidence:
        reasons.append(f"confidence {round(conf, 2)} < required {policy.min_confidence}")
    if policy.max_staleness_days is not None and set_at:
        age = (now - float(set_at)) / 86400.0
        if age > policy.max_staleness_days:
            reasons.append(f"stale: last set {int(age)}d ago > {policy.max_staleness_days}d limit")
    if policy.block_on_recent_change_days is not None and set_at and fact.get("supersedes"):
        age = (now - float(set_at)) / 86400.0
        if age < policy.block_on_recent_change_days:
            reasons.append(f"recently changed {round(age, 2)}d ago — may be unsettled")
    return (len(reasons) == 0, reasons)
