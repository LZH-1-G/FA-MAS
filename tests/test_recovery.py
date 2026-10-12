import pytest

from famas.core.models import (
    Diagnosis,
    Evaluation,
    FailureRecord,
    FailureType,
    Issue,
    IssueKind,
    RecoveryStrategy,
    RecoveryTag,
    RunConfig,
    TaskResult,
    TaskStatus,
    TaskType,
)
from famas.planning.analyzer import heuristic_analysis
from famas.planning.dag import TaskGraph
from famas.planning.templates import dynamic_plan
from famas.recovery.dependency_analyzer import analyze_impact
from famas.recovery.failure_analyzer import FailureAnalyzer
from famas.recovery.replanner import IncrementalReplanner
from tests.conftest import FLAGSHIP
from tests.test_dag import complete, diamond


@pytest.fixture
def ctx(engine):
    return engine.build_context(FLAGSHIP, RunConfig())


def exc(task_id: str, etype: str, agent: str = "research-fast", transient=None) -> FailureRecord:
    return FailureRecord(
        task_id=task_id,
        attempt=1,
        agent_id=agent,
        kind="exception",
        error_type=etype,
        message=etype,
        transient_hint=transient,
    )


def ev_fail(task_id: str, issues=(), missing=(), coverage=1.0, agent="analysis-standard") -> FailureRecord:
    ev = Evaluation(
        passed=False, score=0.3, issues=list(issues), missing_units=list(missing), coverage=coverage
    )
    return FailureRecord(
        task_id=task_id,
        attempt=1,
        agent_id=agent,
        kind="evaluation",
        error_type="x",
        message="x",
        evaluation=ev,
    )


def flagship_graph() -> TaskGraph:
    g = TaskGraph(dynamic_plan(heuristic_analysis(FLAGSHIP)))
    for t in g:
        t.assigned_agent = {
            "research": "research-fast",
            "analysis": "analysis-standard",
            "writing": "writer",
        }[t.task_type.value]
    return g


def decide(fa, rec, g, ctx):
    t = g.get(rec.task_id)
    t.failures.append(rec)
    t.attempts += 1
    return fa.decide(rec, t, g, ctx)


def test_dependency_impact_on_the_diamond_example():
    g = diamond()
    complete(g, "A")
    complete(g, "C")
    g.get("B").status = TaskStatus.FAILED
    d = Diagnosis(
        task_id="B", failure_type=FailureType.TIMEOUT, transient=True, strategy=RecoveryStrategy.RETRY
    )
    imp = analyze_impact(g, [d], 1)
    assert imp.reused == ["A", "C"]
    assert imp.invalidated == []
    assert imp.pending_affected == ["D", "E"]
    glob = analyze_impact(g, [d], 1, scope="global")
    assert glob.reused == [] and set(glob.invalidated) == {"A", "C"}


def test_root_cause_repair_invalidates_only_the_affected_region():
    g = diamond()
    for t in "ABC":
        complete(g, t)
    g.get("D").status = TaskStatus.FAILED
    d = Diagnosis(
        task_id="D",
        failure_type=FailureType.CONTEXT_INCONSISTENCY,
        transient=False,
        strategy=RecoveryStrategy.REPAIR_UPSTREAM,
        root_cause_tasks=["B"],
    )
    imp = analyze_impact(g, [d], 1)
    assert imp.invalidated == ["B"] and imp.reused == ["A", "C"]
    naive = analyze_impact(g, [d], 1, scope="failed_only")
    assert naive.invalidated == [] and naive.reused == ["A", "B", "C"]


def test_failure_classification_and_strategies(ctx):
    fa = FailureAnalyzer("full")
    g = flagship_graph()
    d = decide(fa, exc("research_langgraph", "TimeoutError"), g, ctx)
    assert (d.failure_type, d.strategy) == (FailureType.TIMEOUT, RecoveryStrategy.RETRY)

    d = decide(fa, exc("analyze_architecture", "AgentCapabilityError", agent="analysis-standard"), g, ctx)
    assert (d.failure_type, d.strategy) == (FailureType.AGENT_ERROR, RecoveryStrategy.REASSIGN)

    rec = ev_fail(
        "research_crewai",
        issues=[Issue(kind=IssueKind.INCOMPLETE, message="m")],
        missing=["research:crewai:scheduling", "research:crewai:failure_recovery"],
        coverage=0.5,
        agent="research-fast",
    )
    d = decide(fa, rec, g, ctx)
    assert (d.failure_type, d.strategy) == (FailureType.INCOMPLETE_OUTPUT, RecoveryStrategy.DECOMPOSE)

    rec = ev_fail(
        "analyze_scheduling",
        issues=[Issue(kind=IssueKind.INCONSISTENCY, message="m", source_task="research_autogen")],
    )
    d = decide(fa, rec, g, ctx)
    assert d.strategy == RecoveryStrategy.REPAIR_UPSTREAM and d.root_cause_tasks == ["research_autogen"]

    rec = ev_fail("report", issues=[Issue(kind=IssueKind.INVALID_FORMAT, message="bad")], agent="writer")
    d = decide(fa, rec, g, ctx)
    assert (d.failure_type, d.strategy) == (FailureType.INVALID_OUTPUT, RecoveryStrategy.RETRY)


def test_missing_information_adds_dependency_or_prerequisite(ctx):
    fa = FailureAnalyzer("full")
    g = flagship_graph()
    g.remove_dependency("analyze_communication", "research_crewai")
    rec = ev_fail(
        "analyze_communication",
        issues=[Issue(kind=IssueKind.MISSING_INFORMATION, message="m", subject="crewai")],
    )
    d = decide(fa, rec, g, ctx)
    assert d.strategy == RecoveryStrategy.ADD_DEPENDENCY and d.root_cause_tasks == ["research_crewai"]

    rec = ev_fail(
        "analyze_architecture",
        issues=[Issue(kind=IssueKind.MISSING_INFORMATION, message="m", subject="metagpt")],
    )
    d = decide(fa, rec, g, ctx)
    assert d.strategy == RecoveryStrategy.ADD_PREREQUISITE and d.missing_subjects == ["metagpt"]


def test_naive_mode_never_looks_upstream(ctx):
    fa = FailureAnalyzer("naive")
    g = flagship_graph()
    rec = ev_fail(
        "analyze_scheduling",
        issues=[Issue(kind=IssueKind.INCONSISTENCY, message="m", source_task="research_autogen")],
    )
    d = decide(fa, rec, g, ctx)
    assert d.strategy == RecoveryStrategy.RETRY and d.root_cause_tasks == []


def test_budget_exhaustion_escalates_then_aborts(ctx):
    fa = FailureAnalyzer("full")
    g = flagship_graph()
    t = g.get("research_langgraph")
    t.max_attempts = 2
    decide(fa, exc("research_langgraph", "TimeoutError"), g, ctx)
    d = decide(fa, exc("research_langgraph", "TimeoutError"), g, ctx)
    assert d.strategy == RecoveryStrategy.REASSIGN and d.target_agent == "generalist"
    t.assigned_agent = "generalist"
    d = decide(fa, exc("research_langgraph", "TimeoutError", agent="generalist"), g, ctx)
    assert d.strategy == RecoveryStrategy.ABORT
    t.optional = True
    assert (
        decide(fa, exc("research_langgraph", "TimeoutError", agent="generalist"), g, ctx).strategy
        == RecoveryStrategy.SKIP
    )


async def test_decomposition_salvages_partial_output_and_keeps_downstream_edges(ctx):
    g = flagship_graph()
    t = g.get("research_crewai")
    partial = TaskResult(
        task_id=t.id,
        agent_id="research-fast",
        content="partial",
        data={
            "subject": "crewai",
            "findings": {"architecture": {"summary": "s"}, "communication": {"summary": "s"}},
            "units": {"research:crewai:architecture": "a" * 30, "research:crewai:communication": "b" * 30},
        },
    )
    rec = ev_fail(
        "research_crewai",
        missing=["research:crewai:scheduling", "research:crewai:failure_recovery"],
        coverage=0.5,
    )
    rec.partial_result = partial
    t.failures.append(rec)
    d = Diagnosis(
        task_id=t.id,
        failure_type=FailureType.INCOMPLETE_OUTPUT,
        transient=False,
        strategy=RecoveryStrategy.DECOMPOSE,
    )
    downstream_before = {s for s in g.successors("research_crewai")}
    out = await IncrementalReplanner().apply(g, [d], ctx, 1)
    assert g.is_acyclic()
    merge = g.get("research_crewai")
    assert merge.task_type == TaskType.AGGREGATE and merge.recovery_tag == RecoveryTag.DECOMPOSED
    assert set(g.successors("research_crewai")) == downstream_before
    parts = [g.get(p) for p in merge.dependencies]
    salvaged = [p for p in parts if p.recovery_tag == RecoveryTag.SALVAGED]
    new = [p for p in parts if p.recovery_tag == RecoveryTag.NEW]
    assert len(salvaged) == 1 and salvaged[0].status == TaskStatus.COMPLETED
    assert sorted(a for p in new for a in p.inputs["aspects"]) == ["failure_recovery", "scheduling"]
    assert len(out.added) == 3


async def test_repair_upstream_invalidates_completed_descendants(ctx):
    g = flagship_graph()
    for tid in [
        "research_langgraph",
        "research_crewai",
        "research_autogen",
        "research_agent_framework",
        "analyze_architecture",
    ]:
        complete(g, tid)
    g.get("analyze_scheduling").status = TaskStatus.FAILED
    g.get("analyze_scheduling").failures.append(ev_fail("analyze_scheduling"))
    d = Diagnosis(
        task_id="analyze_scheduling",
        failure_type=FailureType.CONTEXT_INCONSISTENCY,
        transient=False,
        strategy=RecoveryStrategy.REPAIR_UPSTREAM,
        root_cause_tasks=["research_autogen"],
    )
    await IncrementalReplanner().apply(g, [d], ctx, 1)
    up = g.get("research_autogen")
    assert up.status == TaskStatus.PENDING and up.recovery_tag == RecoveryTag.REPAIR and up.feedback
    assert up.previous_result is not None
    assert g.get("analyze_architecture").recovery_tag == RecoveryTag.RECOMPUTE
    assert g.get("research_langgraph").status == TaskStatus.COMPLETED
