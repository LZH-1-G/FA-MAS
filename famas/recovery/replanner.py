"""Incremental Replanner: edits the task DAG in place to recover from failures.

Supported operations (all recorded as ``PatchOp`` for auditability):

* ``retry``      – reset the failed task (optionally with feedback that changes its spec)
* ``reassign``   – exclude the failing agent; the selector picks another one
* ``decompose``  – split an oversized task into subtasks (binary split of its missing
                   work units), salvage any valid partial output as its own node, and
                   turn the original node into a deterministic merge so downstream
                   edges stay untouched
* ``add_edge``   – add a dependency the planner forgot
* ``add_task``   – insert a missing prerequisite task
* ``repair``     – re-execute an upstream root cause with feedback; invalidate its
                   completed descendants (they are re-validated by fingerprint)
* ``skip``       – give up on an optional task; dependants run degraded
* ``abort``      – no viable recovery

Everything not touched keeps its status and result: that is the reuse.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from pydantic import BaseModel

from famas.agents import prompts
from famas.core.fingerprint import output_fingerprint
from famas.core.models import (
    Diagnosis,
    FailureType,
    PatchOp,
    RecoveryStrategy,
    RecoveryTag,
    Task,
    TaskResult,
    TaskStatus,
    TaskType,
)
from famas.knowledge.catalog import aspect_title, subject_from_key
from famas.llm.base import MockReply
from famas.planning.dag import TaskGraph
from famas.planning.templates import analysis_units, research_units

if TYPE_CHECKING:  # pragma: no cover
    from famas.orchestration.context import RunContext


class SubtaskSpec(BaseModel):
    name: str
    description: str


class DecompositionPlan(BaseModel):
    subtasks: list[SubtaskSpec]


class ReplanOutcome(BaseModel):
    ops: list[PatchOp] = []
    added: list[str] = []
    removed: list[str] = []
    retried: list[str] = []
    aborted: bool = False
    abort_reason: str | None = None


def reset_task(task: Task, tag: RecoveryTag, round_no: int, feedback: str | None = None) -> None:
    task.status = TaskStatus.PENDING
    task.result = None
    task.evaluation = None
    task.recovery_tag = tag
    task.recovery_round = round_no
    task.started_ms = task.finished_ms = None
    if feedback and feedback not in task.feedback:
        task.feedback.append(feedback)


def invalidate_task(task: Task, tag: RecoveryTag, round_no: int) -> None:
    if task.result is not None:
        task.previous_result = task.result
    reset_task(task, tag, round_no)


def _feedback_for(d: Diagnosis, task: Task) -> str | None:
    if d.failure_type == FailureType.INVALID_OUTPUT:
        return (
            "The previous attempt returned malformed output. Return complete, well-formed structured output."
        )
    if d.failure_type == FailureType.INCOMPLETE_OUTPUT and d.missing_units:
        return f"The previous attempt omitted required parts: {', '.join(d.missing_units[:8])}. Cover every one of them."
    if (
        d.failure_type == FailureType.AGENT_ERROR
        and d.strategy == RecoveryStrategy.RETRY
        and task.task_type == TaskType.CODING
    ):
        return "The previous program failed to execute. Write simpler, standard-library-only code."
    return None


class IncrementalReplanner:
    def __init__(self, salvage_partial: bool = True) -> None:
        self.salvage_partial = salvage_partial

    async def apply(
        self, graph: TaskGraph, diagnoses: list[Diagnosis], ctx: RunContext, round_no: int
    ) -> ReplanOutcome:
        out = ReplanOutcome()
        repaired: set[str] = set()
        failed_ids = {d.task_id for d in diagnoses}
        for d in diagnoses:
            task = graph.get(d.task_id)
            s = d.strategy
            if s == RecoveryStrategy.ABORT:
                out.aborted, out.abort_reason = True, f"{task.id}: {d.rationale}"
                out.ops.append(PatchOp(op="abort", task_id=task.id, detail={"reason": d.rationale}))
                continue
            if s == RecoveryStrategy.RETRY:
                reset_task(task, RecoveryTag.RETRY, round_no, _feedback_for(d, task))
                out.retried.append(task.id)
                out.ops.append(PatchOp(op="retry", task_id=task.id, detail={"failure": d.failure_type.value}))
            elif s == RecoveryStrategy.REASSIGN:
                prev = task.assigned_agent
                if prev and prev not in task.excluded_agents:
                    task.excluded_agents.append(prev)
                task.assigned_agent = d.target_agent
                task.selection = None
                reset_task(task, RecoveryTag.REASSIGNED, round_no, _feedback_for(d, task))
                out.retried.append(task.id)
                out.ops.append(
                    PatchOp(op="reassign", task_id=task.id, detail={"from": prev, "to": d.target_agent})
                )
            elif s == RecoveryStrategy.DECOMPOSE:
                added = await self._decompose(graph, task, ctx, round_no, out)
                out.added.extend(added)
            elif s == RecoveryStrategy.ADD_DEPENDENCY:
                for p in d.root_cause_tasks:
                    try:
                        graph.add_dependency(task.id, p)
                        out.ops.append(PatchOp(op="add_edge", task_id=task.id, detail={"from": p}))
                    except ValueError:
                        out.ops.append(
                            PatchOp(
                                op="add_edge_rejected", task_id=task.id, detail={"from": p, "reason": "cycle"}
                            )
                        )
                reset_task(task, RecoveryTag.RETRY, round_no)
                out.retried.append(task.id)
            elif s == RecoveryStrategy.ADD_PREREQUISITE:
                for subj in d.missing_subjects:
                    new_id = self._add_research_prerequisite(graph, task, subj, round_no)
                    out.added.append(new_id)
                    out.ops.append(
                        PatchOp(op="add_task", task_id=new_id, detail={"for": task.id, "subject": subj})
                    )
                    out.ops.append(PatchOp(op="add_edge", task_id=task.id, detail={"from": new_id}))
                await self._account_replanner_call(
                    ctx, f"insert prerequisite research for {d.missing_subjects}"
                )
                reset_task(task, RecoveryTag.RETRY, round_no)
                out.retried.append(task.id)
            elif s == RecoveryStrategy.REPAIR_UPSTREAM:
                issue_msgs = "; ".join(
                    i.message
                    for f in task.failures[-1:]
                    for i in (f.evaluation.issues if f.evaluation else [])
                    if i.source_task in d.root_cause_tasks or i.subject
                )
                for u in d.root_cause_tasks:
                    if u in repaired or u not in graph:
                        continue
                    repaired.add(u)
                    self.repair(graph, u, task.id, issue_msgs, failed_ids, round_no, out)
                reset_task(task, RecoveryTag.RETRY, round_no)
                out.retried.append(task.id)
            elif s == RecoveryStrategy.SKIP:
                task.status = TaskStatus.SKIPPED
                task.recovery_tag, task.recovery_round = RecoveryTag.SKIPPED, round_no
                for succ in graph.successors(task.id):
                    st = graph.get(succ)
                    if task.task_type == TaskType.ANALYSIS and task.inputs.get("aspect"):
                        st.inputs.setdefault("degraded_aspects", []).append(task.inputs["aspect"])
                    st.feedback.append(
                        f"Input `{task.id}` is unavailable (skipped after repeated failures); proceed with what is available."
                    )
                out.ops.append(PatchOp(op="skip", task_id=task.id))
        return out

    def repair(
        self,
        graph: TaskGraph,
        u: str,
        reported_by: str,
        message: str,
        protected: set[str],
        round_no: int,
        out: ReplanOutcome,
    ) -> None:
        """Re-execute upstream task ``u`` with feedback and invalidate its completed descendants."""
        up = graph.get(u)
        up.revision += 1
        if up.revision >= 2 and up.assigned_agent and up.assigned_agent not in up.excluded_agents:
            up.excluded_agents.append(up.assigned_agent)  # repeated repair -> also switch agent
            up.assigned_agent = None
            up.selection = None
        fb = (
            f"`{reported_by}` reported a problem with your previous output: {message or 'inconsistent or missing information'}. "
            "Re-verify every claim against the sources and correct or remove unsupported statements."
        )
        invalidate_task(up, RecoveryTag.REPAIR, round_no)
        if fb not in up.feedback:
            up.feedback.append(fb)
        out.ops.append(PatchOp(op="repair", task_id=u, detail={"reported_by": reported_by}))
        for desc in sorted(graph.descendants(u)):
            dt = graph.get(desc)
            if desc in protected:
                continue
            if dt.status == TaskStatus.COMPLETED:
                invalidate_task(dt, RecoveryTag.RECOMPUTE, round_no)
                out.ops.append(PatchOp(op="invalidate", task_id=desc, detail={"because": u}))

    async def audit_reused_inputs(
        self, graph: TaskGraph, ctx: RunContext, round_no: int, out: ReplanOutcome, audited: set[str]
    ) -> list[str]:
        """Optional reuse audit: before reusing results that feed the re-executed region, re-verify them.

        Incremental recovery preserves results nobody has complained about - including
        ones that are wrong but not yet detected. The audit spends one small-model
        verification call per preserved upstream result (each result at most once per
        run) to catch such latent errors before they propagate further.
        """
        pending = [t for t in graph if t.status == TaskStatus.PENDING]
        targets: set[str] = set()
        for t in pending:
            for a in graph.ancestors(t.id):
                at = graph.get(a)
                if (
                    at.status == TaskStatus.COMPLETED
                    and at.result is not None
                    and at.result.result_id not in audited
                ):
                    targets.add(a)
        flagged = []
        for tid in sorted(targets):
            res = graph.get(tid).result
            audited.add(res.result_id)
            hit = ctx.oracle.detects(f"audit:{tid}", round_no, res) if ctx.llm.is_mock else False
            resp = await ctx.llm_call(
                component="verifier",
                system="You verify an intermediate result of a multi-agent workflow. Report contradictions or unsupported claims.",
                prompt=f"Task `{tid}` output to verify:\n{res.content[:3000]}\n\nAnswer VALID or INVALID with a reason.",
                tier="small",
                mock=lambda hit=hit: MockReply(
                    text="INVALID: contradicts documented capabilities" if hit else "VALID"
                ),
            )
            if resp.text.strip().upper().startswith("INVALID"):
                flagged.append(tid)
        for tid in flagged:
            if graph.get(tid).status == TaskStatus.COMPLETED:
                self.repair(
                    graph, tid, "verifier", "reuse audit flagged an unsupported claim", set(), round_no, out
                )
                out.ops.append(PatchOp(op="audit_flag", task_id=tid))
        return flagged

    # ---------------------------------------------------------------- helpers
    async def _account_replanner_call(
        self, ctx: RunContext, what: str, mock_plan: DecompositionPlan | None = None
    ) -> DecompositionPlan | None:
        resp = await ctx.llm_call(
            component="replanner",
            system=prompts.PLANNER_SYSTEM,
            prompt=f"Revise the plan locally: {what}. Return the new subtasks.",
            schema=DecompositionPlan,
            tier="small",
            mock=lambda: MockReply(parsed=mock_plan or DecompositionPlan(subtasks=[])),
        )
        return resp.parsed

    async def _escalate_monolith(
        self, graph: TaskGraph, task: Task, ctx: RunContext, round_no: int, out: ReplanOutcome
    ) -> list[str]:
        """Strategy escalation: replace a failing single-agent task by the multi-agent sub-plan."""
        from famas.planning.templates import dynamic_plan

        analysis = ctx.analysis
        if analysis is None:  # pragma: no cover - analysis always precedes recovery
            reset_task(task, RecoveryTag.RETRY, round_no)
            return []
        sub = dynamic_plan(analysis.model_copy(update={"strategy": "multi_agent"}))
        await self._account_replanner_call(
            ctx,
            f"escalate {task.id} from a single agent to a {len(sub)}-task multi-agent plan",
            DecompositionPlan(subtasks=[SubtaskSpec(name=t.name, description=t.description) for t in sub]),
        )
        prefix = f"{task.id}__"
        new_ids = []
        for t in sub:
            t.id = prefix + t.id
            t.dependencies = [prefix + d for d in t.dependencies]
            t.dependencies = t.dependencies or list(task.dependencies)
            t.origin, t.parent_id = "replanner", task.id
            t.recovery_tag, t.recovery_round = RecoveryTag.NEW, round_no
            t.max_attempts = task.max_attempts
            graph.add_task(t)
            new_ids.append(t.id)
            out.ops.append(PatchOp(op="add_task", task_id=t.id, detail={"parent": task.id}))
        task.task_type = TaskType.AGGREGATE
        task.required_capabilities = ["aggregate"]
        task.assigned_agent = "aggregator"
        task.selection = None
        reset_task(task, RecoveryTag.DECOMPOSED, round_no)
        graph.set_dependencies(task.id, new_ids)
        out.ops.append(PatchOp(op="escalate", task_id=task.id, detail={"into": new_ids}))
        return new_ids

    async def _decompose(
        self, graph: TaskGraph, task: Task, ctx: RunContext, round_no: int, out: ReplanOutcome
    ) -> list[str]:
        from famas.recovery.failure_analyzer import is_monolith

        if is_monolith(task):
            return await self._escalate_monolith(graph, task, ctx, round_no, out)
        partial: TaskResult | None = task.failures[-1].partial_result if task.failures else None
        delivered = set(partial.units) if (partial and self.salvage_partial) else set()
        if task.task_type == TaskType.RESEARCH:
            key, items = "aspects", list(task.inputs.get("aspects") or [])
            unit_of = lambda a: f"research:{task.inputs['subject']}:{a}"  # noqa: E731
        else:
            key, items = "subjects", list(task.inputs.get("subjects") or [])
            unit_of = lambda s: f"analysis:{task.inputs['aspect']}:{s}"  # noqa: E731
        done = [i for i in items if unit_of(i) in delivered]
        todo = [i for i in items if unit_of(i) not in delivered] or items
        size = max(1, math.ceil(len(todo) / 2))
        chunks = [todo[i : i + size] for i in range(0, len(todo), size)]

        def describe(chunk: list[str]) -> str:
            if task.task_type == TaskType.RESEARCH:
                return f"Investigate {task.inputs.get('name', task.inputs['subject'])} and report findings on: {', '.join(aspect_title(a) for a in chunk)}."
            return f"Compare {', '.join(subject_from_key(s).name for s in chunk)} on {aspect_title(task.inputs['aspect'])}."

        plan = await self._account_replanner_call(
            ctx,
            f"split {task.id} into {len(chunks)} subtasks over {key}",
            DecompositionPlan(
                subtasks=[
                    SubtaskSpec(name=f"{task.name} (part {i + 1})", description=describe(c))
                    for i, c in enumerate(chunks)
                ]
            ),
        )
        specs = (
            plan.subtasks
            if plan and len(plan.subtasks) == len(chunks)
            else [
                SubtaskSpec(name=f"{task.name} (part {i + 1})", description=describe(c))
                for i, c in enumerate(chunks)
            ]
        )
        original_deps = list(task.dependencies)
        new_ids: list[str] = []
        n = 0
        while f"{task.id}__part{n}" in graph:
            n += 1
        if done and partial is not None:
            sid = f"{task.id}__part{n}"
            n += 1
            sub = task.model_copy(deep=True)
            sub.id, sub.name, sub.parent_id, sub.origin = sid, f"{task.name} (salvaged)", task.id, "replanner"
            sub.inputs = {**task.inputs, key: done}
            sub.covers = [unit_of(i) for i in done]
            sub.dependencies = original_deps
            sub.failures, sub.feedback, sub.attempts = [], [], 0
            salvaged = partial.model_copy(deep=True)
            salvaged.task_id = sid
            salvaged.data = {
                **salvaged.data,
                "units": {u: v for u, v in partial.units.items() if u in sub.covers},
            }
            salvaged.data.pop("_partial", None)
            if isinstance(salvaged.data.get("findings"), dict):
                salvaged.data["findings"] = {a: f for a, f in salvaged.data["findings"].items() if a in done}
            salvaged.output_fingerprint = output_fingerprint(salvaged.content, salvaged.data)
            sub.result = salvaged
            sub.status = TaskStatus.COMPLETED
            sub.recovery_tag, sub.recovery_round = RecoveryTag.SALVAGED, round_no
            graph.add_task(sub)
            # fingerprint the salvaged result against its own (new) spec so it validates as reusable
            salvaged.input_fingerprint = graph.input_fingerprint(sid)
            new_ids.append(sid)
            out.ops.append(PatchOp(op="salvage", task_id=sid, detail={key: done}))
        for chunk, spec in zip(chunks, specs, strict=True):
            sid = f"{task.id}__part{n}"
            n += 1
            sub = Task(
                id=sid,
                name=spec.name,
                description=spec.description,
                task_type=task.task_type,
                required_capabilities=list(task.required_capabilities),
                dependencies=original_deps,
                expected_output=task.expected_output,
                inputs={**{k: v for k, v in task.inputs.items() if k != key}, key: chunk},
                acceptance_criteria=list(task.acceptance_criteria),
                covers=[unit_of(i) for i in chunk],
                origin="replanner",
                parent_id=task.id,
                max_attempts=task.max_attempts,
                recovery_tag=RecoveryTag.NEW,
                recovery_round=round_no,
            )
            graph.add_task(sub)
            new_ids.append(sid)
            out.ops.append(PatchOp(op="add_task", task_id=sid, detail={key: chunk, "parent": task.id}))
        # turn the original node into a merge node; downstream edges are untouched
        task.task_type = TaskType.AGGREGATE
        task.required_capabilities = ["aggregate"]
        task.assigned_agent = "aggregator"
        task.selection = None
        reset_task(task, RecoveryTag.DECOMPOSED, round_no)
        graph.set_dependencies(task.id, new_ids)
        out.ops.append(PatchOp(op="decompose", task_id=task.id, detail={"into": new_ids}))
        return new_ids

    def _add_research_prerequisite(self, graph: TaskGraph, task: Task, subject: str, round_no: int) -> str:
        subj = subject_from_key(subject)
        aspects = (
            [task.inputs["aspect"]] if task.inputs.get("aspect") else list(task.inputs.get("aspects") or [])
        )
        base = f"research_{subject}"
        new_id, n = base, 1
        while new_id in graph:
            new_id = f"{base}_{n}"
            n += 1
        graph.add_task(
            Task(
                id=new_id,
                name=f"Research {subj.name}",
                description=f"Investigate {subj.name} and report findings on: {', '.join(aspect_title(a) for a in aspects)}.",
                task_type=TaskType.RESEARCH,
                required_capabilities=["research", "github"],
                inputs={"subject": subject, "name": subj.name, "repo": subj.repo, "aspects": aspects},
                covers=research_units(subject, aspects),
                origin="replanner",
                recovery_tag=RecoveryTag.NEW,
                recovery_round=round_no,
                max_attempts=task.max_attempts,
            )
        )
        graph.add_dependency(task.id, new_id)
        return new_id


__all__ = ["IncrementalReplanner", "ReplanOutcome", "reset_task", "invalidate_task", "analysis_units"]
