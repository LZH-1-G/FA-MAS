"""The FA-MAS orchestration workflow as a LangGraph ``StateGraph``.

    START → analyze → plan → validate ─┬─> assign → execute ─┬─> finalize → END
                         ^             │                     │
                         └─(repair)────┘                     └─> diagnose → assess_impact → replan ─┐
                                       ^                                                           │
                                       └─────────────────────── (patched DAG) ──────────────────────┘

Graph state is plain JSON (task DAG, analysis, failure records) so it can be
checkpointed after every node; live services are fetched from the run context.
"""

from __future__ import annotations

from typing import Any, TypedDict

from famas.core.models import (
    Diagnosis,
    FailureRecord,
    ImpactReport,
    RecoveryTag,
    ReplanRecord,
    TaskAnalysis,
    TaskStatus,
)
from famas.orchestration.context import get_context
from famas.orchestration.dispatcher import Dispatcher
from famas.orchestration.graph_runtime import END, START, StateGraph
from famas.orchestration.selector import AgentSelector
from famas.planning.analyzer import TaskAnalyzer
from famas.planning.dag import TaskGraph
from famas.planning.planner import Planner
from famas.planning.validator import validate_graph
from famas.recovery.evaluator import ResultEvaluator
from famas.recovery.policies import make_policy
from famas.simulation.injection import FailureInjector

MAX_PLAN_ATTEMPTS = 3
OVERHEAD = ("failure_analyzer", "replanner", "planner:replan", "verifier")


class RunState(TypedDict, total=False):
    run_id: str
    request: str
    phase: str  # planning | executing | recovery | done
    analysis: dict
    graph: dict
    validation: dict
    plan_attempts: int
    failures: list[dict]
    diagnoses: list[dict]
    impact: dict
    recovery: dict
    rounds: int
    status: str
    error: str | None
    final: dict | None


def _graph(state: RunState) -> TaskGraph:
    return TaskGraph.from_dict(state["graph"])


def _overhead_snapshot(ctx) -> dict[str, float]:
    comps = ctx.metrics.by_component
    return {
        "tokens": sum(comps[c].tokens_in + comps[c].tokens_out for c in OVERHEAD if c in comps),
        "cost": sum(comps[c].cost for c in OVERHEAD if c in comps),
        "t": ctx.clock.now(),
    }


# ----------------------------------------------------------------- nodes
async def analyze_node(state: RunState) -> dict[str, Any]:
    ctx = get_context(state["run_id"])
    analysis = await TaskAnalyzer().analyze(state["request"], ctx)
    ctx.analysis = analysis
    from famas.simulation.scenarios import get_scenario

    rules = list(ctx.config.injection)
    natural = ctx.config.natural_failure_rate
    if ctx.config.scenario and ctx.config.scenario != "none":
        sc = get_scenario(ctx.config.scenario)
        rules = sc.rules + rules
        natural = natural or sc.natural_failure_rate
    if rules or natural:
        ctx.injector = FailureInjector(rules, analysis, seed=ctx.config.seed, natural_rate=natural)
    ctx.events.emit("analysis_completed", analysis=analysis.model_dump(mode="json"))
    return {"analysis": analysis.model_dump(mode="json"), "phase": "planning", "plan_attempts": 0}


async def plan_node(state: RunState) -> dict[str, Any]:
    ctx = get_context(state["run_id"])
    analysis = TaskAnalysis.model_validate(state["analysis"])
    attempts = state.get("plan_attempts", 0) + 1
    planner = Planner(ctx.system.planner)
    feedback = None
    if state.get("validation") and not state["validation"].get("valid", True):
        feedback = [f"{i['code']}: {i['message']}" for i in state["validation"].get("issues", [])]
    if attempts >= MAX_PLAN_ATTEMPTS:
        tasks = planner.template(analysis)  # deterministic fallback plan
    else:
        tasks = await planner.plan(analysis, ctx, feedback=feedback)
    for t in tasks:
        t.max_attempts = ctx.config.max_task_attempts
    if ctx.injector is not None and ctx.system.planner == "dynamic":
        for task_id, dep in ctx.injector.dependency_drops(tasks):
            t = next(x for x in tasks if x.id == task_id)
            t.dependencies = [d for d in t.dependencies if d != dep]
    graph = TaskGraph(tasks)
    ctx.events.emit(
        "plan_created",
        attempt=attempts,
        view=graph.view(),
        layers=graph.layers() if graph.is_acyclic() else [],
    )
    return {"graph": graph.to_dict(), "plan_attempts": attempts}


async def validate_node(state: RunState) -> dict[str, Any]:
    ctx = get_context(state["run_id"])
    graph = _graph(state)
    report = validate_graph(graph, known_capabilities=ctx.registry.capabilities())
    ctx.events.emit(
        "plan_validated",
        valid=report.valid,
        issues=[i.model_dump() for i in report.issues],
        fixes=report.fixes,
    )
    return {"graph": graph.to_dict(), "validation": report.model_dump()}


def route_after_validate(state: RunState) -> str:
    if state["validation"]["valid"]:
        return "assign"
    if state.get("phase") == "planning" and state.get("plan_attempts", 0) < MAX_PLAN_ATTEMPTS:
        return "plan"
    return "finalize"


async def assign_node(state: RunState) -> dict[str, Any]:
    ctx = get_context(state["run_id"])
    graph = _graph(state)
    selector = AgentSelector(ctx.registry, ctx.stats, ctx.system.fixed_agents)
    assignments = {}
    for t in graph:
        if t.status != TaskStatus.PENDING:
            continue
        if t.assigned_agent and t.assigned_agent not in t.excluded_agents:
            continue
        sel = selector.select(t)
        if sel is not None:
            t.assigned_agent, t.selection = sel.agent_id, sel
            assignments[t.id] = sel.agent_id
    if assignments:
        ctx.events.emit(
            "agents_assigned",
            assignments=assignments,
            rationale={tid: graph.get(tid).selection.model_dump() for tid in assignments},
        )
    return {"graph": graph.to_dict(), "phase": "executing"}


async def execute_node(state: RunState) -> dict[str, Any]:
    ctx = get_context(state["run_id"])
    graph = _graph(state)
    selector = AgentSelector(ctx.registry, ctx.stats, ctx.system.fixed_agents)
    report = await Dispatcher(ctx, selector, ResultEvaluator()).run_wave(graph)
    return {"graph": graph.to_dict(), "failures": [f.model_dump(mode="json") for f in report.failures]}


def route_after_execute(state: RunState) -> str:
    if state.get("failures"):
        return "diagnose"
    return "finalize"


async def diagnose_node(state: RunState) -> dict[str, Any]:
    ctx = get_context(state["run_id"])
    graph = _graph(state)
    rounds = state.get("rounds", 0) + 1
    failures = [FailureRecord.model_validate(f) for f in state["failures"]]
    if rounds > ctx.config.max_recovery_rounds:
        return {
            "rounds": rounds,
            "status": "failed",
            "error": f"recovery budget exhausted after {rounds - 1} rounds",
            "diagnoses": [],
        }
    policy = make_policy(ctx.system.recovery, salvage_partial=ctx.config.salvage_partial)
    snap = _overhead_snapshot(ctx)
    diagnoses = await policy.diagnose(failures, graph, ctx)
    for d in diagnoses:
        ctx.events.emit("failure_diagnosed", task_id=d.task_id, diagnosis=d.model_dump(mode="json"))
    return {
        "rounds": rounds,
        "phase": "recovery",
        "diagnoses": [d.model_dump(mode="json") for d in diagnoses],
        "recovery": {"snapshot": snap},
    }


def route_after_diagnose(state: RunState) -> str:
    return "finalize" if state.get("status") == "failed" else "assess_impact"


async def impact_node(state: RunState) -> dict[str, Any]:
    ctx = get_context(state["run_id"])
    graph = _graph(state)
    policy = make_policy(ctx.system.recovery, salvage_partial=ctx.config.salvage_partial)
    diagnoses = [Diagnosis.model_validate(d) for d in state["diagnoses"]]
    impact = policy.assess(graph, diagnoses, state["rounds"])
    ctx.events.emit("impact_analyzed", impact=impact.model_dump())
    return {"impact": impact.model_dump()}


async def replan_node(state: RunState) -> dict[str, Any]:
    ctx = get_context(state["run_id"])
    graph = _graph(state)
    round_no = state["rounds"]
    policy = make_policy(ctx.system.recovery, salvage_partial=ctx.config.salvage_partial)
    diagnoses = [Diagnosis.model_validate(d) for d in state["diagnoses"]]
    impact = ImpactReport.model_validate(state["impact"])
    failures = [FailureRecord.model_validate(f) for f in state["failures"]]
    before = {t.id: (t.status, t.result.result_id if t.result else None) for t in graph}

    outcome = await policy.replan(graph, diagnoses, impact, ctx, round_no)

    reused, invalidated, invalid_reuse = [], [], []
    for tid, (status, rid) in before.items():
        if outcome.aborted or status != TaskStatus.COMPLETED or tid not in graph:
            continue
        t = graph.get(tid)
        if t.status == TaskStatus.COMPLETED and t.result and t.result.result_id == rid:
            reused.append(tid)
            if t.recovery_tag not in (RecoveryTag.SALVAGED, RecoveryTag.NEW, RecoveryTag.DECOMPOSED):
                t.recovery_tag, t.recovery_round = RecoveryTag.REUSED, round_no
            if ctx.oracle.tainted(t.result):
                invalid_reuse.append(tid)
        else:
            invalidated.append(tid)
    snap = state.get("recovery", {}).get("snapshot") or _overhead_snapshot(ctx)
    now = _overhead_snapshot(ctx)
    record = ReplanRecord(
        round=round_no,
        policy=policy.name,
        at_ms=snap["t"],
        failures=[f.model_copy(update={"partial_result": None}) for f in failures],
        diagnoses=diagnoses,
        impact=impact,
        ops=outcome.ops,
        reused=sorted(reused),
        invalidated=sorted(invalidated),
        retried=sorted(set(outcome.retried)),
        added=outcome.added,
        removed=outcome.removed,
        invalid_reuse=sorted(invalid_reuse),
        overhead_tokens=int(now["tokens"] - snap["tokens"]),
        overhead_cost=round(now["cost"] - snap["cost"], 6),
        overhead_ms=round(now["t"] - snap["t"], 2),
        aborted=outcome.aborted,
        abort_reason=outcome.abort_reason,
    )
    ctx.replans.append(record)
    ctx.events.emit("replan_applied", record=record.model_dump(mode="json"), view=graph.view())
    update: dict[str, Any] = {"graph": graph.to_dict(), "failures": [], "phase": "recovery"}
    if outcome.aborted:
        update.update(status="failed", error=f"recovery aborted: {outcome.abort_reason}")
    return update


def route_after_replan(state: RunState) -> str:
    return "finalize" if state.get("status") == "failed" else "validate"


async def finalize_node(state: RunState) -> dict[str, Any]:
    ctx = get_context(state["run_id"])
    graph = _graph(state) if state.get("graph") else TaskGraph()
    error = state.get("error")
    if not error and state.get("validation") and not state["validation"].get("valid", True):
        error = "plan validation failed: " + "; ".join(
            i["message"] for i in state["validation"].get("issues", [])
        )
    complete = len(graph) > 0 and graph.is_complete() and not graph.has_failures()
    if not complete and not error:
        error = "execution stalled before all tasks completed"
    success = complete and not error
    sinks = [graph.get(s) for s in graph.sinks()] if len(graph) else []
    outputs = [t.result for t in sinks if t.result is not None]
    final_text = "\n\n---\n\n".join(r.content for r in outputs) if outputs else None
    correct = None
    if ctx.injector is not None or ctx.llm.is_mock:
        correct = bool(success and outputs and not any(ctx.oracle.tainted(r) for r in outputs))
    metrics = ctx.metrics.summarize(graph, ctx.replans, ctx.clock.now(), success, correct)
    final = {"output": final_text, "metrics": metrics.model_dump(mode="json"), "sinks": [t.id for t in sinks]}
    ctx.events.emit(
        "run_completed" if success else "run_failed",
        success=success,
        error=error,
        metrics=metrics.model_dump(mode="json"),
        view=graph.view() if len(graph) else None,
    )
    return {"status": "completed" if success else "failed", "error": error, "final": final, "phase": "done"}


def build_workflow(checkpointer: Any = None):
    g = StateGraph(RunState)
    g.add_node("analyze", analyze_node)
    g.add_node("plan", plan_node)
    g.add_node("validate", validate_node)
    g.add_node("assign", assign_node)
    g.add_node("execute", execute_node)
    g.add_node("diagnose", diagnose_node)
    g.add_node("assess_impact", impact_node)
    g.add_node("replan", replan_node)
    g.add_node("finalize", finalize_node)
    g.add_edge(START, "analyze")
    g.add_edge("analyze", "plan")
    g.add_edge("plan", "validate")
    g.add_conditional_edges(
        "validate", route_after_validate, {"assign": "assign", "plan": "plan", "finalize": "finalize"}
    )
    g.add_edge("assign", "execute")
    g.add_conditional_edges("execute", route_after_execute, {"diagnose": "diagnose", "finalize": "finalize"})
    g.add_conditional_edges(
        "diagnose", route_after_diagnose, {"assess_impact": "assess_impact", "finalize": "finalize"}
    )
    g.add_edge("assess_impact", "replan")
    g.add_conditional_edges("replan", route_after_replan, {"validate": "validate", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
