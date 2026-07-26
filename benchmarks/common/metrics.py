"""
Metrics
=======

Accuracy, per-category breakdown, and multi-cutoff evaluation.

Definitions follow mem0ai/memory-benchmarks (Apache-2.0, see NOTICE): a question counts as
correct when its judge score is >= 0.5, accuracy is reported as a percentage, and every
metric is also computed at each retrieval cutoff. Matching those definitions is what makes
a Vayl number and a Mem0 number the same kind of number.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from .schema import CutoffMetrics, GroupMetrics, Metrics

PASS_THRESHOLD = 0.5


def _score_of(e: dict[str, Any], cutoff_label: str | None) -> tuple[float, bool]:
    """Return (score, is_error) for one evaluation, at a cutoff if given."""
    if cutoff_label:
        cr = e.get("cutoff_results", {}).get(cutoff_label, {})
        return float(cr.get("score", 0.0)), bool(cr.get("error") or cr.get("judgment") == "ERROR")
    return float(e.get("score", 0.0)), bool(e.get("judgment") == "ERROR")


def compute_group_metrics(
    evaluations: list[dict[str, Any]],
    group_key: str,
    cutoff_label: str | None = None,
    pass_threshold: float = PASS_THRESHOLD,
) -> dict[str, GroupMetrics]:
    """Break accuracy down by `group_key` (LOCOMO uses category_name)."""
    groups: dict[str, list[float]] = defaultdict(list)
    for e in evaluations:
        score, _ = _score_of(e, cutoff_label)
        groups[e.get(group_key, "unknown")].append(score)

    out: dict[str, GroupMetrics] = {}
    for name in sorted(groups):
        scores = groups[name]
        correct = sum(1 for s in scores if s >= pass_threshold)
        out[name] = GroupMetrics(
            group_name=name,
            total=len(scores),
            correct=correct,
            accuracy=correct / len(scores) * 100 if scores else 0.0,
            avg_score=statistics.mean(scores) * 100 if scores else 0.0,
        )
    return out


def compute_overall_metrics(
    evaluations: list[dict[str, Any]],
    group_key: str = "category_name",
    cutoffs: list[str] | None = None,
    pass_threshold: float = PASS_THRESHOLD,
) -> Metrics:
    """Full suite: headline accuracy at the deepest cutoff, plus per-group and per-cutoff."""
    if not evaluations:
        return Metrics()

    primary = cutoffs[-1] if cutoffs else None

    scores, errors = [], 0
    for e in evaluations:
        s, err = _score_of(e, primary)
        scores.append(s)
        errors += int(err)

    correct = sum(1 for s in scores if s >= pass_threshold)
    m = Metrics(
        overall_accuracy=correct / len(scores) * 100 if scores else 0.0,
        overall_avg_score=statistics.mean(scores) * 100 if scores else 0.0,
        total=len(scores),
        correct=correct,
        errors=errors,
    )
    m.by_group = compute_group_metrics(evaluations, group_key, primary, pass_threshold)

    for label in cutoffs or []:
        cut_scores, cut_errors = [], 0
        for e in evaluations:
            s, err = _score_of(e, label)
            cut_scores.append(s)
            cut_errors += int(err)
        cut_correct = sum(1 for s in cut_scores if s >= pass_threshold)
        m.by_cutoff[label] = CutoffMetrics(
            cutoff=label,
            overall={
                "total": len(cut_scores),
                "correct": cut_correct,
                "errors": cut_errors,
                "accuracy": cut_correct / len(cut_scores) * 100 if cut_scores else 0.0,
                "avg_score": statistics.mean(cut_scores) * 100 if cut_scores else 0.0,
            },
            by_group=compute_group_metrics(evaluations, group_key, label, pass_threshold),
        )
    return m


# ── Staleness: the axis the parity judge cannot see ──────────────────────────────
#
# A partial-credit judge marks an answer CORRECT when it contains at least one gold item,
# and explicitly does not penalize extra detail. An additive store that returns "the plan
# was Pro, then Premium, now Free" therefore scores identically to a store that returns
# "Free". These two counters record what that judge discards.

def compute_staleness(evaluations: list[dict[str, Any]], cutoff_label: str | None = None) -> dict:
    """Count answers the strict judge marked as carrying a superseded value as if current.

    Only meaningful on runs judged with judge_mode in (strict, both), where the judge is
    asked to emit `stale` alongside its label.
    """
    total = judged = stale = ambiguous = 0
    for e in evaluations:
        if cutoff_label:
            cr = e.get("cutoff_results", {}).get(cutoff_label, {})
        else:
            cr = e
        total += 1
        if cr.get("stale") is None:
            continue
        judged += 1
        stale += int(bool(cr.get("stale")))
        ambiguous += int(bool(cr.get("ambiguous")))
    return {
        "total": total,
        "judged": judged,
        "stale": stale,
        "ambiguous": ambiguous,
        "stale_rate": stale / judged * 100 if judged else 0.0,
        "ambiguous_rate": ambiguous / judged * 100 if judged else 0.0,
    }
