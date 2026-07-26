#!/usr/bin/env python3
"""
Shared organizational memory (Feature 4) — many agents + humans writing one reconciled ground-truth.

A shared space is a memory namespace multiple contributors write to; every write carries its SOURCE
(F0.1). What's new here is SOURCE-AWARE reconciliation: when a new fact from one source contradicts an
active fact from ANOTHER source, how it resolves is a policy decision, not a guess:

  • RECENCY   — the newer assertion wins (supersede). Good when contributors are equally trusted.
  • AUTHORITY — a higher-ranked source wins; a lower-ranked source contradicting a higher one is
                FLAGGED, never silently overwritten. Good when a system-of-record outranks agents.
  • REVIEW    — any cross-source conflict is FLAGGED for a human. Good for high-stakes shared facts.

A source correcting ITS OWN earlier fact always supersedes (a normal update, not a conflict).

SCOPE: this module is the reconciliation POLICY — node-agnostic and deterministic. Single-node
correctness rides the server's write lock; true multi-writer concurrency across processes/nodes is
the hosted (Postgres) phase. We don't pretend the single node is a cluster.
"""
from dataclasses import dataclass, field
from enum import Enum


class ReconcileMode(str, Enum):
    RECENCY = "RECENCY"
    AUTHORITY = "AUTHORITY"
    REVIEW = "REVIEW"


@dataclass
class ReconcilePolicy:
    mode: ReconcileMode = ReconcileMode.RECENCY
    authority: dict = field(default_factory=dict)   # source -> rank (higher = more authoritative)

    def rank(self, source):
        return self.authority.get(source or "", 0)

    def to_dict(self):
        return {"mode": self.mode.value, "authority": self.authority}

    @classmethod
    def from_dict(cls, d):
        if not d:
            return None
        return cls(mode=ReconcileMode(d.get("mode", "RECENCY")), authority=dict(d.get("authority") or {}))


def resolve(new_source, target_source, policy):
    """Decide how a cross-source conflict resolves: 'supersede' (new value wins) or 'flag' (surface
    the disagreement, keep the current value). A source updating its own fact always supersedes."""
    if not policy or policy.mode == ReconcileMode.RECENCY:
        return "supersede"
    if new_source and target_source and new_source == target_source:
        return "supersede"
    if policy.mode == ReconcileMode.REVIEW:
        return "flag"
    if policy.mode == ReconcileMode.AUTHORITY:
        return "supersede" if policy.rank(new_source) > policy.rank(target_source) else "flag"
    return "supersede"
