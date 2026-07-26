"""
Result schema
=============

Shapes for benchmark output. Kept deliberately close to the mem0ai/memory-benchmarks
schema (Apache-2.0, see NOTICE) so a Vayl result file and a Mem0 result file can be read
by the same tooling and compared field-for-field.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class GroupMetrics:
    """Accuracy within one question category (LOCOMO: single-hop, multi-hop, ...)."""
    group_name: str = ""
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0          # percentage, 0-100
    avg_score: float = 0.0         # percentage, 0-100


@dataclass
class CutoffMetrics:
    """Metrics at one retrieval depth (top_10, top_20, ...)."""
    cutoff: str = ""
    overall: dict[str, Any] = field(default_factory=dict)
    by_group: dict[str, GroupMetrics] = field(default_factory=dict)


@dataclass
class Metrics:
    overall_accuracy: float = 0.0
    overall_avg_score: float = 0.0
    total: int = 0
    correct: int = 0
    errors: int = 0
    by_group: dict[str, GroupMetrics] = field(default_factory=dict)
    by_cutoff: dict[str, CutoffMetrics] = field(default_factory=dict)


@dataclass
class RunConfig:
    """Everything needed to reproduce a run. Serialized into the result file.

    Benchmark scores are meaningless without the configuration that produced them — the
    extraction model especially, since it is the one doing the reconciliation. Recording
    it inline is what stops a number from drifting away from its conditions.
    """
    project_name: str = ""
    system: str = "vayl"
    judge_mode: str = "parity"           # parity | strict | both
    extraction_model: str = ""           # what Vayl used to extract+reconcile (OPENAI_MODEL)
    read_model: str = ""                 # VAYL_READ_MODEL, if set
    embed_model: str = ""
    answerer_model: str = ""
    judge_model: str = ""
    provider: str = "openai"
    top_k: int = 200
    cutoffs: list[int] = field(default_factory=lambda: [10, 20, 50, 200])
    categories: list[int] = field(default_factory=lambda: [1, 2, 3, 4])
    conversations: str = ""
    dataset_sha256: str = ""
    started_at: str = ""
    finished_at: str = ""
    vayl_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def metrics_to_dict(m: Metrics) -> dict[str, Any]:
    """asdict() on nested dataclass-valued dicts, which asdict handles but verbosely."""
    return {
        "overall_accuracy": m.overall_accuracy,
        "overall_avg_score": m.overall_avg_score,
        "total": m.total,
        "correct": m.correct,
        "errors": m.errors,
        "by_group": {k: asdict(v) for k, v in m.by_group.items()},
        "by_cutoff": {
            k: {"cutoff": v.cutoff, "overall": v.overall,
                "by_group": {gk: asdict(gv) for gk, gv in v.by_group.items()}}
            for k, v in m.by_cutoff.items()
        },
    }
