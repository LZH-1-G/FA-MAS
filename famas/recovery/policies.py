"""Recovery policies: FA-MAS and the baselines.

Each policy implements the three recovery phases that the LangGraph workflow
runs as separate nodes - diagnose, assess impact, replan - so that the only
thing that differs between systems is the policy object.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from famas.core.models import (
    Diagnosis,
    FailureRecord,
    ImpactReport,
    PatchOp,
    RecoveryStrategy,
    RecoveryTag,
    TaskStatus,
)
from famas.planning.dag import TaskGraph
from famas.recovery.dependency_analyzer import analyze_impact
from famas.recovery.failure_analyzer import FailureAnalyzer
from famas.recovery.replanner import IncrementalReplanner, ReplanOutcome, reset_task

if TYPE_CHECKING:  # pragma: no cover
    from famas.orchestration.context import RunContext


class RecoveryPolicy:
    name = "base"
    scope = "local"

    async def diagnose(
        self, failures: list[FailureRecord], graph: TaskGraph, ctx: RunContext
    ) -> list[Diagnosis]:
        raise NotImplementedError

    def assess(self, graph: TaskGraph, diagnoses: list[Diagnosis], round_no: int) -> ImpactReport:
        return analyze_impact(graph, diagnoses, round_no, scope=self.scope)

    async def replan(
        self,
        graph: TaskGraph,
        diagnoses: list[Diagnosis],
        impact: ImpactReport,
        ctx: RunContext,
        round_no: int,
    ) -> ReplanOutcome:
        raise NotImplementedError


class IncrementalPolicy(RecoveryPolicy):
    """FA-MAS: failure-aware, dependency-aware incremental replanning."""

    name = "incremental"
    scope = "local"

    def __init__(self, salvage_partial: bool = True) -> None:
        self.analyzer = FailureAnalyzer("full")
        self.replanner = IncrementalReplanner(salvage_partial=salvage_partial)

    async def diagnose(self, failures, graph, ctx):
        return await self.analyzer.diagnose(failures, graph, ctx)

    async def replan(self, graph, diagnoses, impact, ctx, round_no):
        out = await self.replanner.apply(graph, diagnoses, ctx, round_no)
        if not out.aborted and (ctx.system.verify_reused or ctx.config.verify_reused):
            await self.replanner.audit_reused_inputs(graph, ctx, round_no, out, ctx.audited)
        return out


class NaivePolicy(RecoveryPolicy):
    """Ablation: retry/reassign only the failed task; never looks upstream."""

    name = "naive"
    scope = "failed_only"

    def __init__(self) -> None:
        self.analyzer = FailureAnalyzer("naive")
        self.replanner = IncrementalReplanner(salvage_partial=False)

    async def diagnose(self, failures, graph, ctx):
        return await self.analyzer.diagnose(failures, graph, ctx)

    async def replan(self, graph, diagnoses, impact, ctx, round_no):
        return await self.replanner.apply(graph, diagnoses, ctx, round_no)


def reset_all(graph: TaskGraph, round_no: int, tag: RecoveryTag = RecoveryTag.RESTART) -> list[PatchOp]:
    ops = []
    for t in graph:
        if t.status in (TaskStatus.CANCELLED,):
            continue
        t.previous_result = None
        if t.status != TaskStatus.PENDING or t.recovery_tag is None:
            ops.append(PatchOp(op="reset", task_id=t.id))
        reset_task(
            t,
            tag if t.recovery_tag not in (RecoveryTag.NEW, RecoveryTag.DECOMPOSED) else t.recovery_tag,
            round_no,
        )
    return ops


class FullReplanPolicy(RecoveryPolicy):
    """Baseline C: same diagnosis, but regenerate the plan and recompute every task."""

    name = "full_replan"
    scope = "global"

    def __init__(self) -> None:
        self.analyzer = FailureAnalyzer("full")
        self.replanner = IncrementalReplanner(salvage_partial=False)

    async def diagnose(self, failures, graph, ctx):
        return await self.analyzer.diagnose(failures, graph, ctx)

    async def replan(self, graph, diagnoses, impact, ctx, round_no):
        # Apply the same fixes the incremental planner would (so the new plan addresses the failure)...
        out = await self.replanner.apply(graph, diagnoses, ctx, round_no)
        if out.aborted:
            return out
        # ...then regenerate the plan with failure feedback (an LLM planning call)...
        from famas.planning.planner import Planner

        feedback = [
            f"{d.task_id}: {d.failure_type.value} -> {d.strategy.value} ({d.rationale})" for d in diagnoses
        ]
        if ctx.analysis is not None:
            await Planner(ctx.system.planner).plan(
                ctx.analysis, ctx, feedback=feedback, component="planner:replan"
            )
        # ...and recompute everything from scratch.
        out.ops.extend(reset_all(graph, round_no))
        out.retried = sorted(graph.ids())
        return out


class RestartPolicy(RecoveryPolicy):
    """Baselines A/B: restart the whole (fixed) workflow, no diagnosis-driven changes."""

    name = "restart"
    scope = "global"

    def __init__(self) -> None:
        self.analyzer = FailureAnalyzer("naive")

    async def diagnose(self, failures, graph, ctx):
        out = []
        for rec in failures:
            task = graph.get(rec.task_id)
            ftype, transient, *_ = self.analyzer.classify(rec, task, graph)
            exhausted = len(task.failures) >= task.max_attempts
            out.append(
                Diagnosis(
                    task_id=task.id,
                    failure_type=ftype,
                    transient=transient,
                    strategy=RecoveryStrategy.ABORT if exhausted else RecoveryStrategy.RESTART,
                    rationale="attempt budget exhausted"
                    if exhausted
                    else "restart the workflow from scratch",
                )
            )
        return out

    async def replan(self, graph, diagnoses, impact, ctx, round_no):
        out = ReplanOutcome()
        aborts = [d for d in diagnoses if d.strategy == RecoveryStrategy.ABORT]
        if aborts:
            out.aborted, out.abort_reason = True, f"{aborts[0].task_id}: {aborts[0].rationale}"
            out.ops.append(PatchOp(op="abort", task_id=aborts[0].task_id))
            return out
        out.ops.extend(reset_all(graph, round_no))
        out.retried = sorted(graph.ids())
        return out


def make_policy(kind: str, salvage_partial: bool = True) -> RecoveryPolicy:
    if kind == "incremental":
        return IncrementalPolicy(salvage_partial=salvage_partial)
    if kind == "naive":
        return NaivePolicy()
    if kind == "full_replan":
        return FullReplanPolicy()
    if kind == "restart":
        return RestartPolicy()
    raise ValueError(kind)
