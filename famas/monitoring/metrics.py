"""Run metrics collection.

The collector receives raw facts while a run executes (LLM usage per
component, task executions, failures, reuse decisions) and turns them into a
``RunMetrics`` summary at the end. Definitions follow the evaluation plan:

* **overhead** – tokens/cost/time spent by recovery machinery (failure analysis,
  replanning, re-planning calls of the planner) rather than by task work.
* **reuse rate** – over all recovery rounds, preserved valid results divided by
  (preserved + invalidated) completed results.
* **invalid reuse** – preserved results that (by simulation ground truth) were
  derived from corrupted data and should have been invalidated.
* **redundant execution** – re-executing a task whose identical inputs had
  already produced a valid (non-corrupted) result earlier in the run.
* **wasted tokens** – agent tokens spent on executions whose results are not part
  of the final, valid graph state.
* **llm calls** – every usage record charged to the run, i.e. real model requests
  plus *simulated* calls; **simulated llm calls** – injected timeouts, which are
  charged like a timed-out request (prompt tokens, cost, time) but send nothing.
  Real model requests = ``llm_calls - simulated_llm_calls``.
* **agent invocations** – agent task executions, including attempts that failed
  before or without reaching a model (one per attempt, not per LLM request).
* **cost** – sum of the per-call costs (USD), computed with ``math.fsum`` so the
  total is the float nearest the exact sum, independent of reporting order, then
  rounded to 6 decimals. Per-call costs are exact decimals in principle (tokens ×
  price per 1M); when a total lies exactly halfway between two micro-dollars, the
  float representation decides the direction, deterministically.
"""

from __future__ import annotations

import math
from collections import defaultdict

from famas.core.models import ComponentUsage, ReplanRecord, RunMetrics, TaskStatus
from famas.llm.base import LLMUsage

OVERHEAD_COMPONENTS = {"failure_analyzer", "replanner", "planner:replan", "verifier"}


class MetricsCollector:
    def __init__(self) -> None:
        self.by_component: dict[str, ComponentUsage] = defaultdict(ComponentUsage)
        self.by_agent: dict[str, ComponentUsage] = defaultdict(ComponentUsage)
        self.executions = 0
        self.failed_executions = 0
        self.agent_invocations = 0
        self.simulated_llm_calls = 0
        self.cache_hits = 0
        self.redundant_executions = 0
        self.execution_tokens: dict[str, int] = {}  # result_id -> tokens
        self.failed_tokens = 0
        self._valid_fps: dict[str, set[str]] = defaultdict(set)
        # Costs are summed with math.fsum over every recorded call, so totals do not depend on the
        # order in which concurrent tasks report usage (keeps benchmarks bit-for-bit reproducible
        # across the LangGraph runtime and the fallback runtime).
        self._component_costs: dict[str, list[float]] = defaultdict(list)
        self._agent_costs: dict[str, list[float]] = defaultdict(list)

    # ------------------------------------------------------------ recording
    def record_llm(
        self, component: str, usage: LLMUsage, agent_id: str | None = None, *, simulated: bool = False
    ) -> None:
        """Record one LLM call's usage. ``simulated=True`` marks usage that the failure injector
        modelled without sending a prompt (counted in ``llm_calls`` and ``simulated_llm_calls``)."""
        if simulated:
            self.simulated_llm_calls += usage.calls
        c = self.by_component[component]
        c.calls += usage.calls
        c.tokens_in += usage.tokens_in
        c.tokens_out += usage.tokens_out
        self._component_costs[component].append(usage.cost)
        c.cost = math.fsum(self._component_costs[component])
        c.duration_ms += usage.duration_ms
        if agent_id:
            a = self.by_agent[agent_id]
            a.calls += usage.calls
            a.tokens_in += usage.tokens_in
            a.tokens_out += usage.tokens_out
            self._agent_costs[agent_id].append(usage.cost)
            a.cost = math.fsum(self._agent_costs[agent_id])
            a.duration_ms += usage.duration_ms

    def record_execution(
        self, task_id: str, input_fp: str, tokens: int, success: bool, result_id: str | None, valid: bool
    ) -> None:
        self.executions += 1
        self.agent_invocations += 1
        if input_fp in self._valid_fps[task_id]:
            self.redundant_executions += 1
        if success and result_id:
            self.execution_tokens[result_id] = tokens
            if valid:
                self._valid_fps[task_id].add(input_fp)
        else:
            self.failed_executions += 1
            self.failed_tokens += tokens

    def record_cache_hit(self) -> None:
        self.cache_hits += 1

    # ------------------------------------------------------------- summary
    def summarize(
        self, graph, replans: list[ReplanRecord], latency_ms: float, success: bool, correct: bool | None
    ) -> RunMetrics:
        tin = sum(c.tokens_in for c in self.by_component.values())
        tout = sum(c.tokens_out for c in self.by_component.values())
        cost = math.fsum(x for parts in self._component_costs.values() for x in parts)
        calls = sum(c.calls for c in self.by_component.values())
        over = [c for k, c in self.by_component.items() if k in OVERHEAD_COMPONENTS]
        overhead_tokens = sum(c.tokens_in + c.tokens_out for c in over)
        overhead_cost = math.fsum(
            x for k, parts in self._component_costs.items() if k in OVERHEAD_COMPONENTS for x in parts
        )
        overhead_ms = sum(r.overhead_ms for r in replans)

        reused = sum(len(r.reused) for r in replans)
        invalidated = sum(len(r.invalidated) for r in replans)
        invalid_reuse = sum(len(r.invalid_reuse) for r in replans)
        final_result_ids = {
            t.result.result_id for t in graph if t.status == TaskStatus.COMPLETED and t.result
        }
        exec_total = sum(self.execution_tokens.values()) + self.failed_tokens
        useful = sum(v for k, v in self.execution_tokens.items() if k in final_result_ids)
        retries = sum(max(0, t.attempts - 1) for t in graph)
        had_failures = self.failed_executions > 0
        return RunMetrics(
            success=success,
            correct=correct,
            latency_ms=round(latency_ms, 2),
            tokens_in=tin,
            tokens_out=tout,
            total_tokens=tin + tout,
            cost=round(cost, 6),
            llm_calls=calls,
            simulated_llm_calls=self.simulated_llm_calls,
            agent_invocations=self.agent_invocations,
            task_executions=self.executions,
            failed_executions=self.failed_executions,
            retries=retries,
            recovery_rounds=len(replans),
            tasks_total=len(graph),
            tasks_completed=len(graph.by_status(TaskStatus.COMPLETED)),
            tasks_skipped=len(graph.by_status(TaskStatus.SKIPPED)),
            cache_hits=self.cache_hits,
            reused_results=reused,
            invalidated_results=invalidated,
            reuse_rate=(reused / (reused + invalidated)) if (reused + invalidated) else None,
            invalid_reuse=invalid_reuse,
            invalid_reuse_rate=(invalid_reuse / reused) if reused else None,
            redundant_executions=self.redundant_executions,
            wasted_tokens=max(0, exec_total - useful),
            overhead_tokens=overhead_tokens,
            overhead_cost=round(overhead_cost, 6),
            overhead_ms=round(overhead_ms, 2),
            had_failures=had_failures,
            recovered=(success if had_failures else None),
            by_component=dict(self.by_component),
            by_agent=dict(self.by_agent),
        )
