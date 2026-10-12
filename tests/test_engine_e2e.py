"""End-to-end runs through the full workflow (mock LLM, virtual clock)."""

import pytest

from famas.core.models import RecoveryTag, RunConfig, TaskStatus
from famas.planning.dag import TaskGraph
from tests.conftest import FLAGSHIP, MEDIUM

SCENARIOS = [
    "none",
    "transient",
    "agent_failure",
    "complex_task",
    "corruption",
    "missing_dependency",
    "late_failure",
    "cascade",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_fa_mas_recovers_from_every_scenario(run, scenario):
    out = await run(FLAGSHIP, scenario=scenario)
    assert out.status == "completed", out.error
    assert out.metrics.correct is True
    assert out.final_output and "## References" in out.final_output
    g = out.task_graph
    assert g.is_complete() and not g.has_failures()
    if scenario != "none":
        assert out.metrics.recovery_rounds >= 1
        assert out.metrics.redundant_executions == 0


async def test_clean_run_report_and_metrics(run):
    out = await run(FLAGSHIP)
    m = out.metrics
    assert m.task_executions == 9 and m.recovery_rounds == 0 and m.failed_executions == 0
    assert m.total_tokens > 0 and m.cost > 0 and m.latency_ms > 0
    for title in (
        "System Architecture",
        "Agent Communication",
        "Scheduling & Orchestration",
        "Failure Recovery",
    ):
        assert f"## {title}" in out.final_output
    assert "github.com/microsoft/agent-framework" in out.final_output


async def test_parallelism_shortens_the_critical_path(run):
    par = await run(FLAGSHIP, max_concurrency=8)
    seq = await run(FLAGSHIP, max_concurrency=1)
    assert par.metrics.total_tokens == seq.metrics.total_tokens
    assert par.metrics.latency_ms < 0.8 * seq.metrics.latency_ms


async def test_transient_failure_reuses_sibling_results(run):
    out = await run(FLAGSHIP, scenario="transient")
    rec = out.replans[0]
    assert rec.diagnoses[0].strategy.value == "retry"
    assert set(rec.reused) >= {"research_crewai", "research_autogen", "research_agent_framework"}
    assert rec.invalidated == []
    assert out.metrics.task_executions == 10


async def test_corruption_is_traced_to_its_root_cause(run):
    out = await run(FLAGSHIP, scenario="corruption")
    rec = out.replans[0]
    assert all(d.strategy.value == "repair_upstream" for d in rec.diagnoses)
    assert {c for d in rec.diagnoses for c in d.root_cause_tasks} == {"research_agent_framework"}
    assert rec.invalidated == ["research_agent_framework"]
    assert len(rec.reused) == 3
    assert out.task_graph.get("research_agent_framework").recovery_tag == RecoveryTag.REPAIR


async def test_complex_task_is_decomposed_with_salvage(run):
    out = await run(FLAGSHIP, scenario="complex_task")
    g = out.task_graph
    parts = [t for t in g if t.parent_id == "research_crewai"]
    assert any(t.recovery_tag == RecoveryTag.SALVAGED for t in parts)
    assert g.get("research_crewai").task_type.value == "aggregate"
    assert g.get("research_crewai").status == TaskStatus.COMPLETED


async def test_missing_dependency_is_repaired_by_adding_an_edge(run):
    out = await run(FLAGSHIP, scenario="missing_dependency")
    assert out.replans[0].diagnoses[0].strategy.value == "add_dependency"
    assert "research_crewai" in out.task_graph.get("analyze_architecture").dependencies


async def test_agent_failure_triggers_reassignment(run):
    out = await run(FLAGSHIP, scenario="agent_failure")
    t = out.task_graph.get("analyze_architecture")
    assert t.excluded_agents and t.assigned_agent not in t.excluded_agents


async def test_simple_request_uses_a_single_agent(run):
    out = await run("Summarize the architecture of LangGraph.")
    g = out.task_graph
    assert len(g) == 1 and g.get("solve").assigned_agent == "generalist"
    assert out.status == "completed"


async def test_general_requests_work_without_known_subjects(run):
    out = await run(
        "Explain the trade-offs between optimistic and pessimistic concurrency control in databases, with examples, and then propose a decision checklist for choosing between them in a new service."
    )
    assert out.status == "completed"
    assert out.final_output
    long_request = " ".join(
        ["Explain optimistic versus pessimistic concurrency control with worked examples."] * 12
    )
    out2 = await run(long_request)
    assert out2.status == "completed" and len(out2.task_graph) == 2


async def test_code_task_executes_in_sandbox(run):
    out = await run(
        "Analyze LangGraph, CrewAI and AutoGen architecture and scheduling, and include a computed feature matrix."
    )
    assert out.status == "completed", out.error
    code = out.task_graph.get("code_feature_matrix")
    assert code.result.data["matrix"] and "## Feature matrix (computed)" in out.final_output


# ------------------------------------------------------------------ baselines
@pytest.mark.parametrize("system", ["single_agent", "fixed_workflow", "full_replan", "naive_incremental"])
async def test_baselines_complete_clean_runs(run, system):
    out = await run(MEDIUM, system=system)
    assert out.status == "completed" and out.metrics.correct


async def test_full_replan_recomputes_everything(run):
    fa = await run(FLAGSHIP, scenario="late_failure")
    full = await run(FLAGSHIP, system="full_replan", scenario="late_failure")
    assert full.status == "completed"
    assert full.metrics.redundant_executions >= 8 and fa.metrics.redundant_executions == 0
    assert full.metrics.total_tokens > fa.metrics.total_tokens
    assert full.replans[0].reused == [] and fa.replans[0].reused


async def test_naive_retry_cannot_fix_upstream_corruption(run):
    out = await run(FLAGSHIP, system="naive_incremental", scenario="corruption")
    assert out.status == "failed"
    assert out.metrics.invalid_reuse > 0


async def test_fixed_workflow_cannot_decompose(run):
    out = await run(FLAGSHIP, system="fixed_workflow", scenario="complex_task")
    assert out.status == "failed"


async def test_restart_policy_reruns_whole_pipeline(run):
    out = await run(FLAGSHIP, system="fixed_workflow", scenario="late_failure")
    assert out.status == "completed"
    assert out.metrics.task_executions == 6  # 3 stages twice


async def test_partial_detection_probability_still_converges(run):
    out = await run(FLAGSHIP, scenario="corruption", detection_probability=0.5, seed=3)
    assert out.status == "completed"


async def test_stochastic_failures_are_reproducible(run):
    a = await run(FLAGSHIP, scenario="stochastic", seed=7)
    b = await run(FLAGSHIP, scenario="stochastic", seed=7)
    assert a.metrics.total_tokens == b.metrics.total_tokens
    assert a.metrics.latency_ms == b.metrics.latency_ms
    assert [r.diagnoses for r in a.replans] == [r.diagnoses for r in b.replans]


async def test_realtime_clock_mode(run):
    out = await run(MEDIUM, scenario="transient", simulated_clock=False, time_scale=0.0)
    assert out.status == "completed" and out.metrics.recovery_rounds == 1


async def test_events_tell_the_whole_story(run):
    out = await run(MEDIUM, scenario="transient")
    types = [e.type for e in out.events]
    for t in (
        "run_started",
        "analysis_completed",
        "plan_created",
        "agents_assigned",
        "task_started",
        "task_failed",
        "failure_diagnosed",
        "impact_analyzed",
        "replan_applied",
        "task_completed",
        "run_completed",
    ):
        assert t in types
    assert [e.seq for e in out.events] == sorted(e.seq for e in out.events)


async def test_outcome_graph_roundtrips(run):
    out = await run(MEDIUM)
    g = TaskGraph.from_dict(out.graph)
    assert g.get("report").result.content == out.final_output


async def test_recovery_budget_bounds_runs(run):
    out = await run(FLAGSHIP, system="naive_incremental", scenario="complex_task", max_task_attempts=2)
    assert out.status == "failed"
    assert out.metrics.recovery_rounds <= 8


async def test_engine_survives_live_mode_without_credentials(engine):
    cfg = RunConfig(mode="live")
    ctx = engine.build_context("x", cfg)
    assert ctx.config.simulated_clock is False


async def test_single_agent_strategy_escalates_to_multi_agent_plan(run):
    out = await run("Summarize the architecture of LangGraph.", scenario="agent_failure")
    assert out.status == "completed", out.error
    g = out.task_graph
    assert g.get("solve").task_type.value == "aggregate"
    assert any(t.id.startswith("solve__") for t in g)
    assert out.final_output.startswith("# Technical overview: LangGraph")


async def test_partial_detection_is_reproducible(run):
    a = await run(FLAGSHIP, scenario="cascade", detection_probability=0.5, seed=4)
    b = await run(FLAGSHIP, scenario="cascade", detection_probability=0.5, seed=4)
    assert (a.status, a.metrics.total_tokens, a.metrics.correct) == (
        b.status,
        b.metrics.total_tokens,
        b.metrics.correct,
    )


async def test_reuse_audit_catches_latent_corruption(run):
    # With low detection probability plain FA-MAS can keep an undetected corrupted result;
    # the audit variant re-verifies preserved upstream results during recovery.
    plain, audited = 0, 0
    for seed in range(6):
        plain += bool(
            (await run(FLAGSHIP, scenario="cascade", detection_probability=0.5, seed=seed)).metrics.correct
        )
        out = await run(
            FLAGSHIP, system="fa_mas_verify", scenario="cascade", detection_probability=0.5, seed=seed
        )
        audited += bool(out.metrics.correct)
        assert out.metrics.by_component.get("verifier") is not None
    assert audited >= plain
