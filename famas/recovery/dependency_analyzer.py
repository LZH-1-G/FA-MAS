"""Dependency Analyzer: which results survive a failure and which must be recomputed.

Given the diagnoses of a recovery round, the *repair set* is every failed task
plus every upstream task blamed as a root cause. The *affected* region is the
repair set and all of its descendants. Completed results outside that region
are provably unaffected (none of their inputs change) and are reused;
completed results inside it are invalidated.

Invalidated results are not thrown away: they are kept as ``previous_result``
and re-validated by fingerprint at dispatch time. If an upstream task is
re-executed and produces an identical output, downstream input fingerprints do
not change and the old results are reused (early cut-off).
"""

from __future__ import annotations

from famas.core.models import Diagnosis, ImpactReport, RecoveryStrategy, TaskStatus
from famas.planning.dag import TaskGraph


def repair_set(diagnoses: list[Diagnosis], include_root_causes: bool = True) -> set[str]:
    out = {d.task_id for d in diagnoses}
    if include_root_causes:
        for d in diagnoses:
            if d.strategy == RecoveryStrategy.REPAIR_UPSTREAM:
                out.update(d.root_cause_tasks)
    return out


def analyze_impact(
    graph: TaskGraph, diagnoses: list[Diagnosis], round_no: int, scope: str = "local"
) -> ImpactReport:
    """``scope='local'`` → dependency-aware impact; ``scope='global'`` → everything is affected (baselines)."""
    completed = {t.id for t in graph if t.status == TaskStatus.COMPLETED}
    pending = {t.id for t in graph if t.status == TaskStatus.PENDING}
    running = {t.id for t in graph if t.status == TaskStatus.RUNNING}
    failed = sorted(d.task_id for d in diagnoses)
    if scope == "global":
        affected = set(graph.ids())
        repair = set(failed)
    else:
        repair = repair_set(diagnoses, include_root_causes=(scope == "local"))
        affected = repair | graph.descendants_of(repair)
    return ImpactReport(
        round=round_no,
        failed=failed,
        repair_set=sorted(repair),
        affected=sorted(affected),
        invalidated=sorted(completed & affected),
        reused=sorted(completed - affected),
        pending_affected=sorted(pending & affected),
        running=sorted(running),
    )
