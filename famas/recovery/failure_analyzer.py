"""Failure Analyzer: classifies failures, attributes root causes and picks a recovery strategy.

Classification is rule-based and deterministic (reproducible experiments);
an optional LLM call writes a human-readable explanation and is accounted as
recovery overhead. Root-cause attribution uses the structured issues raised by
evaluators and downstream agents (e.g. an analysis agent reporting that the
findings from ``research_autogen`` contradict other evidence), restricted to
true ancestors in the task DAG.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from famas.agents import prompts
from famas.core.models import (
    Diagnosis,
    FailureRecord,
    FailureType,
    IssueKind,
    RecoveryStrategy,
    Task,
    TaskType,
)
from famas.llm.base import MockReply
from famas.planning.dag import TaskGraph

if TYPE_CHECKING:  # pragma: no cover
    from famas.orchestration.context import RunContext

EXCEPTION_MAP: dict[str, tuple[FailureType, bool]] = {
    "TimeoutError": (FailureType.TIMEOUT, True),
    "LLMTimeout": (FailureType.TIMEOUT, True),
    "TimeoutException": (FailureType.TIMEOUT, True),
    "ToolError": (FailureType.TOOL_ERROR, True),
    "RateLimitError": (FailureType.RESOURCE_LIMIT, True),
    "LLMRateLimit": (FailureType.RESOURCE_LIMIT, True),
    "AgentCapabilityError": (FailureType.AGENT_ERROR, False),
    "AgentCrash": (FailureType.AGENT_ERROR, True),
    "SandboxExecutionError": (FailureType.AGENT_ERROR, False),
    "LLMOutputError": (FailureType.INVALID_OUTPUT, False),
    "ValidationError": (FailureType.INVALID_OUTPUT, False),
}

DECOMPOSABLE_MIN_UNITS = 2


def find_provider(graph: TaskGraph, subject: str | None, aspect: str | None, exclude: str) -> str | None:
    """Task that produces information about ``subject`` (research) or ``aspect`` (analysis)."""
    for t in graph:
        if t.id == exclude or t.status.value == "cancelled":
            continue
        if (
            subject
            and t.task_type in (TaskType.RESEARCH, TaskType.AGGREGATE)
            and t.inputs.get("subject") == subject
        ):
            return t.id
        if subject and t.task_type == TaskType.RESEARCH and subject in (t.inputs.get("subjects") or []):
            return t.id
    if aspect:
        for t in graph:
            if (
                t.id != exclude
                and t.task_type in (TaskType.ANALYSIS, TaskType.AGGREGATE)
                and t.inputs.get("aspect") == aspect
            ):
                return t.id
    return None


def is_monolith(task: Task) -> bool:
    return (
        task.task_type == TaskType.GENERAL
        and task.inputs.get("mode") == "monolith"
        and bool(task.inputs.get("subjects"))
    )


def is_decomposable(task: Task) -> bool:
    if is_monolith(task):
        return True  # escalate the single-agent strategy to a multi-agent plan
    if task.task_type == TaskType.RESEARCH and task.inputs.get("subject"):
        return len(task.inputs.get("aspects") or []) >= DECOMPOSABLE_MIN_UNITS
    if task.task_type == TaskType.ANALYSIS and task.inputs.get("aspect"):
        return len(task.inputs.get("subjects") or []) >= DECOMPOSABLE_MIN_UNITS
    return False


class FailureAnalyzer:
    def __init__(self, mode: str = "full") -> None:
        if mode not in ("full", "naive"):
            raise ValueError(mode)
        self.mode = mode

    # -------------------------------------------------------- classification
    def classify(
        self, rec: FailureRecord, task: Task, graph: TaskGraph
    ) -> tuple[FailureType, bool, list[str], list[str], list[str]]:
        """Return (type, transient, root_cause_tasks, missing_subjects, missing_aspects)."""
        if rec.kind == "exception":
            ftype, transient = EXCEPTION_MAP.get(rec.error_type, (FailureType.AGENT_ERROR, True))
            if rec.transient_hint is not None:
                transient = rec.transient_hint
            return ftype, transient, [], [], []
        ev = rec.evaluation
        issues = ev.issues if ev else []
        ancestors = graph.ancestors(task.id)
        roots = [
            i.source_task for i in issues if i.kind == IssueKind.INCONSISTENCY and i.source_task in ancestors
        ]
        if roots:
            return FailureType.CONTEXT_INCONSISTENCY, False, list(dict.fromkeys(roots)), [], []
        if any(i.kind == IssueKind.INCONSISTENCY for i in issues):
            return FailureType.CONTEXT_INCONSISTENCY, False, [], [], []
        missing_info = [i for i in issues if i.kind == IssueKind.MISSING_INFORMATION]
        if missing_info:
            subs = list(dict.fromkeys(i.subject for i in missing_info if i.subject))
            asps = list(dict.fromkeys(i.aspect for i in missing_info if i.aspect and not i.subject))
            return FailureType.MISSING_INFORMATION, False, [], subs, asps
        if any(i.kind == IssueKind.INVALID_FORMAT for i in issues):
            return FailureType.INVALID_OUTPUT, True, [], [], []
        if ev and ev.missing_units:
            return FailureType.INCOMPLETE_OUTPUT, False, [], [], []
        return FailureType.UNKNOWN, True, [], [], []

    # -------------------------------------------------------------- strategy
    def decide(self, rec: FailureRecord, task: Task, graph: TaskGraph, ctx: RunContext) -> Diagnosis:
        ftype, transient, roots, subs, asps = self.classify(rec, task, graph)
        agent = rec.agent_id or task.assigned_agent
        same_agent = sum(1 for f in task.failures if f.agent_id == agent)
        alternatives = [
            a.id
            for a in ctx.registry.capable(
                task.required_capabilities, exclude=list(task.excluded_agents) + [agent or ""]
            )
        ]
        missing_units = rec.evaluation.missing_units if rec.evaluation else []
        d = Diagnosis(
            task_id=task.id,
            failure_type=ftype,
            transient=transient,
            strategy=RecoveryStrategy.RETRY,
            missing_units=missing_units,
        )

        def reassign_or_retry(reason: str) -> None:
            if alternatives:
                d.strategy = RecoveryStrategy.REASSIGN
                d.target_agent = None
                d.rationale = reason + f"; reassigning away from {agent}"
            elif self.mode == "full" and is_monolith(task):
                d.strategy = RecoveryStrategy.DECOMPOSE
                d.rationale = (
                    reason + "; no other agent can take the whole task -> escalate to a multi-agent plan"
                )
            else:
                d.strategy = RecoveryStrategy.RETRY
                d.rationale = reason + "; no alternative agent, retrying"

        if len(task.failures) >= task.max_attempts:  # budget counts failed attempts, not forced re-runs
            if task.optional:
                d.strategy, d.rationale = (
                    RecoveryStrategy.SKIP,
                    "attempt budget exhausted; task optional -> continue degraded",
                )
            elif self.mode == "full" and is_monolith(task):
                d.strategy, d.rationale = (
                    RecoveryStrategy.DECOMPOSE,
                    "single-agent budget exhausted; escalating to a multi-agent plan",
                )
            elif (
                "generalist" not in task.excluded_agents
                and agent != "generalist"
                and "generalist" in ctx.registry
                and task.task_type != TaskType.AGGREGATE
            ):
                d.strategy, d.target_agent = RecoveryStrategy.REASSIGN, "generalist"
                d.rationale = "attempt budget exhausted; escalating to the generalist agent"
            else:
                d.strategy, d.rationale = (
                    RecoveryStrategy.ABORT,
                    "attempt budget exhausted and no escalation path left",
                )
            return d

        if self.mode == "naive":
            if transient or same_agent < 2:
                d.strategy, d.rationale = RecoveryStrategy.RETRY, "naive policy: retry the failed task"
            else:
                reassign_or_retry("naive policy: repeated failure")
            d.root_cause_tasks = []
            return d

        if ftype in (FailureType.TIMEOUT, FailureType.TOOL_ERROR, FailureType.RESOURCE_LIMIT):
            if transient and same_agent < 2:
                d.strategy, d.rationale = (
                    RecoveryStrategy.RETRY,
                    f"transient {ftype.value}; retry with same agent",
                )
            else:
                reassign_or_retry(f"repeated {ftype.value}")
        elif ftype == FailureType.AGENT_ERROR:
            if not transient:
                reassign_or_retry(f"agent {agent} cannot complete this task ({rec.error_type})")
            elif same_agent < 2:
                d.strategy, d.rationale = RecoveryStrategy.RETRY, "agent error, possibly transient; retry"
            else:
                reassign_or_retry("agent failed repeatedly")
        elif ftype == FailureType.INVALID_OUTPUT:
            if same_agent < 2:
                d.strategy, d.rationale = (
                    RecoveryStrategy.RETRY,
                    "malformed output; retry with format feedback",
                )
            else:
                reassign_or_retry("agent keeps producing malformed output")
        elif ftype == FailureType.INCOMPLETE_OUTPUT:
            coverage = rec.evaluation.coverage if rec.evaluation else 0.0
            if is_decomposable(task) and coverage < 0.75:
                d.strategy, d.rationale = (
                    RecoveryStrategy.DECOMPOSE,
                    (
                        f"only {coverage:.0%} of required units delivered; task likely too large for one pass -> split"
                    ),
                )
            elif same_agent < 2:
                d.strategy, d.rationale = (
                    RecoveryStrategy.RETRY,
                    "incomplete output; retry with explicit list of missing parts",
                )
            else:
                reassign_or_retry("incomplete output repeatedly")
        elif ftype == FailureType.MISSING_INFORMATION:
            ancestors = graph.ancestors(task.id)
            descendants = graph.descendants(task.id)
            add_dep, repair, prereq = [], [], []
            for s in subs:
                p = find_provider(graph, s, None, task.id)
                if p is None:
                    prereq.append(s)
                elif p in ancestors:
                    repair.append(p)
                elif p not in descendants:
                    add_dep.append(p)
            for a in asps:
                p = find_provider(graph, None, a, task.id)
                if p and p not in ancestors and p not in descendants:
                    add_dep.append(p)
                elif p and p in ancestors:
                    repair.append(p)
            d.missing_subjects = prereq
            if add_dep:
                d.strategy, d.root_cause_tasks = RecoveryStrategy.ADD_DEPENDENCY, add_dep
                d.rationale = f"plan is missing dependencies on {add_dep}; add edges and retry"
            elif prereq:
                d.strategy = RecoveryStrategy.ADD_PREREQUISITE
                d.rationale = f"no task produces information on {prereq}; insert research subtasks"
            elif repair:
                d.strategy, d.root_cause_tasks = RecoveryStrategy.REPAIR_UPSTREAM, repair
                d.rationale = f"upstream {repair} did not deliver the needed information; repair upstream"
            else:
                d.strategy, d.rationale = (
                    RecoveryStrategy.RETRY,
                    "missing information with no identifiable provider; retry",
                )
        elif ftype == FailureType.CONTEXT_INCONSISTENCY:
            if roots:
                d.strategy, d.root_cause_tasks = RecoveryStrategy.REPAIR_UPSTREAM, roots
                d.rationale = (
                    f"inputs from {roots} contradict other evidence; invalidate and repair the root cause"
                )
            else:
                d.strategy, d.rationale = (
                    RecoveryStrategy.RETRY,
                    "inconsistency without attributable upstream; retry",
                )
        else:
            if same_agent < 2:
                d.strategy, d.rationale = RecoveryStrategy.RETRY, "unclassified failure; retry"
            else:
                reassign_or_retry("unclassified repeated failure")
        d.confidence = 0.9 if d.strategy != RecoveryStrategy.RETRY else 0.7
        return d

    async def diagnose(
        self, failures: list[FailureRecord], graph: TaskGraph, ctx: RunContext
    ) -> list[Diagnosis]:
        out = []
        for rec in failures:
            task = graph.get(rec.task_id)
            d = self.decide(rec, task, graph, ctx)
            if self.mode == "full" and ctx.config.llm_failure_analysis:
                d.explanation = await self._explain(rec, task, d, ctx)
            out.append(d)
        return out

    async def _explain(self, rec: FailureRecord, task: Task, d: Diagnosis, ctx: RunContext) -> str:
        issues = "; ".join(i.message for i in (rec.evaluation.issues if rec.evaluation else [])[:4])
        prompt = (
            f"Task `{task.id}` ({task.task_type.value}): {task.description}\n"
            f"Agent: {rec.agent_id}, attempt {rec.attempt}\nFailure: {rec.error_type}: {rec.message}\n"
            f"Evaluator issues: {issues or 'n/a'}\nUpstream tasks: {', '.join(task.dependencies) or 'none'}\n"
            f"Rule-based classification: {d.failure_type.value}, proposed strategy: {d.strategy.value}."
        )

        def _mock() -> MockReply:
            cause = (
                f" Root cause attributed to {', '.join(d.root_cause_tasks)}." if d.root_cause_tasks else ""
            )
            return MockReply(
                text=f"{task.id} failed with {d.failure_type.value} ({rec.error_type}).{cause} Chosen recovery: {d.strategy.value} — {d.rationale}."
            )

        resp = await ctx.llm_call(
            component="failure_analyzer",
            system=prompts.FAILURE_SYSTEM,
            prompt=prompt,
            tier="small",
            mock=_mock,
        )
        return resp.text.strip()
