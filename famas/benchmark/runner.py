"""Benchmark runner: every (task × scenario × system × seed) combination on the same engine."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from famas.benchmark.dataset import get_tasks
from famas.core.models import RunConfig
from famas.orchestration.engine import Engine
from famas.orchestration.stats import AgentStatsStore
from famas.orchestration.systems import SYSTEMS
from famas.simulation.scenarios import SCENARIOS, get_scenario

DEFAULT_SYSTEMS = [
    "single_agent",
    "fixed_workflow",
    "full_replan",
    "naive_incremental",
    "fa_mas",
    "fa_mas_verify",
]
DEFAULT_SCENARIOS = [
    "none",
    "transient",
    "agent_failure",
    "complex_task",
    "corruption",
    "missing_dependency",
    "late_failure",
    "cascade",
    "stochastic",
]


class BenchmarkConfig(BaseModel):
    tasks: list[str] | None = None
    scenarios: list[str] = Field(default_factory=lambda: list(DEFAULT_SCENARIOS))
    systems: list[str] = Field(default_factory=lambda: list(DEFAULT_SYSTEMS))
    seeds: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    deterministic_seeds: list[int] = Field(default_factory=lambda: [0])
    detection_probability: float = 1.0
    max_concurrency: int = 4
    max_recovery_rounds: int = 8
    max_task_attempts: int = 4


def _row(task, scenario: str, system: str, seed: int, out) -> dict[str, Any]:
    m = out.metrics
    return {
        "task": task.id,
        "complexity": task.complexity,
        "scenario": scenario,
        "system": system,
        "seed": seed,
        "status": out.status,
        "success": int(out.status == "completed"),
        "correct": int(bool(m.correct)),
        "latency_s": round(m.latency_ms / 1000.0, 3),
        "tokens": m.total_tokens,
        "cost_usd": round(m.cost, 6),
        "llm_calls": m.llm_calls,
        "agent_invocations": m.agent_invocations,
        "task_executions": m.task_executions,
        "failed_executions": m.failed_executions,
        "recovery_rounds": m.recovery_rounds,
        "reused": m.reused_results,
        "invalidated": m.invalidated_results,
        "invalid_reuse": m.invalid_reuse,
        "redundant_executions": m.redundant_executions,
        "wasted_tokens": m.wasted_tokens,
        "overhead_tokens": m.overhead_tokens,
        "overhead_cost_usd": round(m.overhead_cost, 6),
        "overhead_s": round(m.overhead_ms / 1000.0, 3),
        "had_failures": int(m.had_failures),
        "recovered": "" if m.recovered is None else int(m.recovered),
        "tasks_total": m.tasks_total,
        "error": (out.error or "")[:160],
    }


async def run_benchmark(
    cfg: BenchmarkConfig,
    engine: Engine | None = None,
    progress: Callable[[int, int, dict], Any] | None = None,
) -> list[dict[str, Any]]:
    tasks = get_tasks(cfg.tasks)
    combos = []
    for t in tasks:
        for sc in cfg.scenarios:
            seeds = cfg.seeds if get_scenario(sc).stochastic else cfg.deterministic_seeds
            for system in cfg.systems:
                for seed in seeds:
                    combos.append((t, sc, system, seed))
    rows: list[dict[str, Any]] = []
    eng = engine or Engine(stats=AgentStatsStore(), keep_events=False)
    for i, (t, sc, system, seed) in enumerate(combos, 1):
        # fresh learned state per run: no agent statistics leak between runs (caches are per run already)
        eng.stats = AgentStatsStore()
        rc = RunConfig(
            system=system,
            scenario=sc,
            seed=seed,
            max_concurrency=cfg.max_concurrency,
            detection_probability=cfg.detection_probability,
            max_recovery_rounds=cfg.max_recovery_rounds,
            max_task_attempts=cfg.max_task_attempts,
            simulated_clock=True,
        )
        out = await eng.run(t.request, rc)
        row = _row(t, sc, system, seed, out)
        rows.append(row)
        if progress:
            progress(i, len(combos), row)
    return rows


def _mean(xs: list[float]) -> float | None:
    return round(statistics.fmean(xs), 4) if xs else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per system (overall and per scenario)."""

    def agg(group: list[dict[str, Any]]) -> dict[str, Any]:
        with_fail = [r for r in group if r["had_failures"]]
        reused = sum(r["reused"] for r in group)
        inval = sum(r["invalidated"] for r in group)
        return {
            "runs": len(group),
            "success_rate": _mean([r["success"] for r in group]),
            "correct_rate": _mean([r["correct"] for r in group]),
            "latency_s": _mean([r["latency_s"] for r in group]),
            "tokens": _mean([r["tokens"] for r in group]),
            "cost_usd": _mean([r["cost_usd"] for r in group]),
            "agent_invocations": _mean([r["agent_invocations"] for r in group]),
            "redundant_executions": _mean([r["redundant_executions"] for r in group]),
            "wasted_tokens": _mean([r["wasted_tokens"] for r in group]),
            "overhead_tokens": _mean([r["overhead_tokens"] for r in group]),
            "recovery_success_rate": _mean([r["correct"] for r in with_fail]),
            "runs_with_failures": len(with_fail),
            "reuse_rate": round(reused / (reused + inval), 4) if (reused + inval) else None,
            "invalid_reuse_rate": round(sum(r["invalid_reuse"] for r in group) / reused, 4)
            if reused
            else None,
        }

    by_system: dict[str, list] = defaultdict(list)
    by_sys_scen: dict[tuple[str, str], list] = defaultdict(list)
    by_sys_cx: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        by_system[r["system"]].append(r)
        by_sys_scen[(r["system"], r["scenario"])].append(r)
        by_sys_cx[(r["system"], r["complexity"])].append(r)
    overall = {s: agg(g) for s, g in by_system.items()}
    per_scenario: dict[str, dict[str, Any]] = defaultdict(dict)
    for (s, sc), g in by_sys_scen.items():
        per_scenario[sc][s] = agg(g)
    per_complexity: dict[str, dict[str, Any]] = defaultdict(dict)
    for (s, cx), g in by_sys_cx.items():
        per_complexity[cx][s] = agg(g)
    failure_rows = [r for r in rows if r["scenario"] != "none"]
    under_failure = {s: agg([r for r in failure_rows if r["system"] == s]) for s in by_system}
    return {
        "systems": {s: SYSTEMS[s].short for s in by_system if s in SYSTEMS},
        "scenarios": {sc: SCENARIOS[sc].title for sc in per_scenario if sc in SCENARIOS},
        "overall": overall,
        "under_failure": under_failure,
        "per_scenario": dict(per_scenario),
        "per_complexity": dict(per_complexity),
        "n_runs": len(rows),
    }
