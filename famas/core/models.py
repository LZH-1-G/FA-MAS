"""Core data model shared by every layer of FA-MAS.

Everything that crosses a layer boundary (planner -> scheduler -> agents ->
evaluator -> recovery -> persistence -> API) is a Pydantic model defined here,
so that state is always serialisable, inspectable and checkpointable.
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def wall_time() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TaskType(StrEnum):
    RESEARCH = "research"
    ANALYSIS = "analysis"
    CODING = "coding"
    WRITING = "writing"
    GENERAL = "general"
    AGGREGATE = "aggregate"  # system task produced by decomposition (no LLM)


class TaskStatus(StrEnum):
    PENDING = "pending"  # waiting for dependencies / dispatch
    RUNNING = "running"
    COMPLETED = "completed"  # executed (or reused) and validated
    FAILED = "failed"  # latest attempt failed; awaiting recovery
    SKIPPED = "skipped"  # abandoned by recovery; dependants run degraded
    CANCELLED = "cancelled"


TERMINAL_OK = {TaskStatus.COMPLETED, TaskStatus.SKIPPED}


class RecoveryTag(StrEnum):
    """How the latest recovery round treated a node (used for visualisation + metrics)."""

    REUSED = "reused"  # preserved valid output from before the failure
    RETRY = "retry"  # failed node re-executed
    REASSIGNED = "reassigned"  # failed node moved to another agent
    RECOMPUTE = "recompute"  # invalidated because an upstream output changed
    REPAIR = "repair"  # upstream root cause re-executed with feedback
    NEW = "new"  # inserted by the replanner
    DECOMPOSED = "decomposed"  # turned into a merge node over new subtasks
    SALVAGED = "salvaged"  # partial output kept as its own node
    SKIPPED = "skipped"
    RESTART = "restart"  # reset by a restart / full-replan baseline
    CACHE_HIT = "cache_hit"  # re-dispatched but satisfied from cache (early cutoff)


class FailureType(StrEnum):
    AGENT_ERROR = "agent_error"
    TIMEOUT = "timeout"
    TOOL_ERROR = "tool_error"
    RESOURCE_LIMIT = "resource_limit"
    INVALID_OUTPUT = "invalid_output"
    INCOMPLETE_OUTPUT = "incomplete_output"
    MISSING_INFORMATION = "missing_information"
    CONTEXT_INCONSISTENCY = "context_inconsistency"
    DEPENDENCY_ERROR = "dependency_error"
    UNKNOWN = "unknown"


class RecoveryStrategy(StrEnum):
    RETRY = "retry"
    REASSIGN = "reassign"
    DECOMPOSE = "decompose"
    ADD_DEPENDENCY = "add_dependency"
    ADD_PREREQUISITE = "add_prerequisite"
    REPAIR_UPSTREAM = "repair_upstream"
    SKIP = "skip"
    ABORT = "abort"
    RESTART = "restart"  # baseline: restart whole workflow
    FULL_REPLAN = "full_replan"  # baseline: regenerate the plan, recompute everything


class IssueKind(StrEnum):
    MISSING_INFORMATION = "missing_information"
    INCONSISTENCY = "inconsistency"
    INCOMPLETE = "incomplete"
    INVALID_FORMAT = "invalid_format"
    QUALITY = "quality"


SystemName = Literal[
    "fa_mas", "fa_mas_verify", "single_agent", "fixed_workflow", "full_replan", "naive_incremental"
]


# ---------------------------------------------------------------------------
# Results, evaluation, failures
# ---------------------------------------------------------------------------


class Source(BaseModel):
    title: str
    url: str | None = None
    kind: str = "web"  # web | github | docs | fixture | derived


class Issue(BaseModel):
    kind: IssueKind
    message: str
    subject: str | None = None
    aspect: str | None = None
    source_task: str | None = None  # upstream task suspected to be the root cause
    blocking: bool = True


class TaskResult(BaseModel):
    result_id: str = Field(default_factory=lambda: new_id("res_"))
    task_id: str
    agent_id: str
    attempt: int = 1
    content: str = ""  # human readable (markdown)
    data: dict[str, Any] = Field(default_factory=dict)  # structured output, always has "units"
    sources: list[Source] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    duration_ms: float = 0.0
    llm_calls: int = 0
    input_fingerprint: str = ""
    output_fingerprint: str = ""
    lineage: list[str] = Field(default_factory=list)  # result_ids consumed
    meta: dict[str, Any] = Field(default_factory=dict)  # simulation ground truth lives under meta["gt"]
    created_at: float = Field(default_factory=wall_time)

    @property
    def units(self) -> dict[str, str]:
        units = self.data.get("units")
        return units if isinstance(units, dict) else {}

    def public_view(self, max_chars: int = 4000) -> dict[str, Any]:
        """What downstream agents and the UI are allowed to see (no ground truth)."""
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "content": self.content[:max_chars],
            "data": {k: v for k, v in self.data.items() if not k.startswith("_")},
            "sources": [s.model_dump() for s in self.sources],
        }


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""
    weight: float = 1.0


class Evaluation(BaseModel):
    passed: bool
    score: float
    checks: list[CheckResult] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    missing_units: list[str] = Field(default_factory=list)
    coverage: float = 1.0
    evaluator: str = "rules"
    tokens: int = 0
    duration_ms: float = 0.0


class FailureRecord(BaseModel):
    task_id: str
    attempt: int
    agent_id: str | None
    kind: Literal["exception", "evaluation"]
    error_type: str
    message: str
    transient_hint: bool | None = None
    evaluation: Evaluation | None = None
    partial_result: TaskResult | None = None
    tokens_wasted: int = 0
    at_ms: float = 0.0


class Diagnosis(BaseModel):
    task_id: str
    failure_type: FailureType
    transient: bool
    strategy: RecoveryStrategy
    root_cause_tasks: list[str] = Field(default_factory=list)
    missing_subjects: list[str] = Field(default_factory=list)
    missing_units: list[str] = Field(default_factory=list)
    target_agent: str | None = None
    rationale: str = ""
    confidence: float = 0.8
    explanation: str = ""  # optional LLM-written explanation


class ImpactReport(BaseModel):
    round: int
    failed: list[str] = Field(default_factory=list)
    repair_set: list[str] = Field(default_factory=list)
    affected: list[str] = Field(default_factory=list)
    invalidated: list[str] = Field(default_factory=list)  # completed results that must be recomputed
    reused: list[str] = Field(default_factory=list)  # completed results preserved
    pending_affected: list[str] = Field(default_factory=list)
    running: list[str] = Field(default_factory=list)


class PatchOp(BaseModel):
    op: str  # retry | reassign | add_task | add_edge | remove_edge | invalidate | repair | decompose | skip | reset_all
    task_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ReplanRecord(BaseModel):
    round: int
    policy: str
    at_ms: float = 0.0
    failures: list[FailureRecord] = Field(default_factory=list)
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    impact: ImpactReport | None = None
    ops: list[PatchOp] = Field(default_factory=list)
    reused: list[str] = Field(default_factory=list)
    invalidated: list[str] = Field(default_factory=list)
    retried: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    invalid_reuse: list[str] = Field(default_factory=list)  # filled by the simulation oracle only
    overhead_tokens: int = 0
    overhead_cost: float = 0.0
    overhead_ms: float = 0.0
    aborted: bool = False
    abort_reason: str | None = None


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class AgentSpec(BaseModel):
    id: str
    name: str
    kind: TaskType
    capabilities: list[str]
    model_tier: Literal["small", "large", "none"] = "large"
    description: str = ""
    latency_factor: float = 1.0
    prior_reliability: float = 0.9
    max_concurrency: int = 4
    system: bool = False  # system agents (aggregator) are never selected for LLM work


class CandidateScore(BaseModel):
    agent_id: str
    score: float
    eligible: bool
    reason: str = ""
    breakdown: dict[str, float] = Field(default_factory=dict)


class AgentSelection(BaseModel):
    agent_id: str
    score: float
    strategy: str
    candidates: list[CandidateScore] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


class Task(BaseModel):
    id: str
    name: str
    description: str
    task_type: TaskType
    required_capabilities: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    expected_output: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    acceptance_criteria: list[str] = Field(default_factory=list)
    covers: list[str] = Field(default_factory=list)  # semantic work units this task must deliver
    optional: bool = False
    priority: int = 0
    origin: str = "planner"  # planner | replanner | template
    parent_id: str | None = None

    # ---- runtime state ----
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: str | None = None
    selection: AgentSelection | None = None
    attempts: int = 0
    max_attempts: int = 4
    excluded_agents: list[str] = Field(default_factory=list)
    feedback: list[str] = Field(default_factory=list)
    revision: int = 0
    result: TaskResult | None = None
    previous_result: TaskResult | None = None  # kept for early cut-off after invalidation
    evaluation: Evaluation | None = None
    failures: list[FailureRecord] = Field(default_factory=list)
    recovery_tag: RecoveryTag | None = None
    recovery_round: int | None = None
    started_ms: float | None = None
    finished_ms: float | None = None

    def is_done(self) -> bool:
        return self.status in TERMINAL_OK or self.status == TaskStatus.CANCELLED

    def brief(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.task_type.value,
            "status": self.status.value,
            "agent": self.assigned_agent,
            "attempts": self.attempts,
            "tag": self.recovery_tag.value if self.recovery_tag else None,
        }


# ---------------------------------------------------------------------------
# Task analysis
# ---------------------------------------------------------------------------


class Subject(BaseModel):
    key: str
    name: str
    repo: str | None = None  # owner/name
    url: str | None = None


class TaskAnalysis(BaseModel):
    request: str
    domain: Literal["repo_research", "general"] = "general"
    complexity: Literal["simple", "medium", "complex"] = "medium"
    strategy: Literal["single_agent", "multi_agent"] = "multi_agent"
    subjects: list[Subject] = Field(default_factory=list)
    aspects: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    needs_code: bool = False
    deliverable: str = "report"
    rationale: str = ""
    estimated_tasks: int = 1


# ---------------------------------------------------------------------------
# Failure injection (experiments)
# ---------------------------------------------------------------------------

FaultKind = Literal[
    "timeout",
    "tool_error",
    "rate_limit",
    "agent_crash",
    "agent_incapable",
    "invalid_format",
    "incomplete",
    "too_complex",
    "silent_corruption",
    "drop_dependency",
]


class InjectionRule(BaseModel):
    """Deterministic, reproducible failure injection.

    ``target`` is a glob over work units (e.g. ``research:{s1}:*``); placeholders
    ``{s0}``, ``{s1}``... and ``{a0}``... are resolved from the task analysis so a
    scenario applies to any request.
    """

    kind: FaultKind
    target: str
    on_executions: list[int] | Literal["all"] = Field(default_factory=lambda: [1])
    agent: str | None = None  # restrict to this agent; "@initial" = whichever agent hit it first
    max_units: int | None = None  # too_complex: fires while a task covers more matching units
    subject: str | None = None  # drop_dependency: subject whose edge is removed
    note: str = ""


# ---------------------------------------------------------------------------
# Run configuration and metrics
# ---------------------------------------------------------------------------


class RunConfig(BaseModel):
    system: SystemName = "fa_mas"
    mode: Literal["mock", "live"] = "mock"
    scenario: str = "none"
    injection: list[InjectionRule] = Field(default_factory=list)
    seed: int = 0
    max_concurrency: int = 4
    max_task_attempts: int = 4
    max_recovery_rounds: int = 8
    simulated_clock: bool = True
    time_scale: float = 0.0  # >0 with simulated_clock=False paces mock agents in real time (UI demos)
    natural_failure_rate: float = 0.0
    detection_probability: float = 1.0
    use_llm_judge: bool = False
    llm_failure_analysis: bool = True
    salvage_partial: bool = True
    early_cutoff: bool = True
    verify_reused: bool = False  # audit preserved upstream results during recovery (see IncrementalReplanner)
    planner: Literal["auto", "llm", "template"] = "auto"
    task_timeout_s: float = 180.0
    persist_cache: bool = False
    label: str | None = None


class ComponentUsage(BaseModel):
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    duration_ms: float = 0.0


class RunMetrics(BaseModel):
    success: bool = False
    correct: bool | None = None  # only known under simulation ground truth
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    llm_calls: int = 0
    agent_invocations: int = 0
    task_executions: int = 0
    failed_executions: int = 0
    retries: int = 0
    recovery_rounds: int = 0
    tasks_total: int = 0
    tasks_completed: int = 0
    tasks_skipped: int = 0
    cache_hits: int = 0
    reused_results: int = 0
    invalidated_results: int = 0
    reuse_rate: float | None = None
    invalid_reuse: int = 0
    invalid_reuse_rate: float | None = None
    redundant_executions: int = 0
    wasted_tokens: int = 0
    overhead_tokens: int = 0
    overhead_cost: float = 0.0
    overhead_ms: float = 0.0
    had_failures: bool = False
    recovered: bool | None = None
    by_component: dict[str, ComponentUsage] = Field(default_factory=dict)
    by_agent: dict[str, ComponentUsage] = Field(default_factory=dict)
