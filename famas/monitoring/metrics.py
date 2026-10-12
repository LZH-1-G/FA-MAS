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
"""

from __future__ import annotations

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
        self.cache_hits = 0
        self.redundant_executions = 0
        self.execution_tokens: dict[str, int] = {}  # result_id -> tokens
        self.failed_tokens = 0
        self._valid_fps: dict[str, set[str]] = defaultdict(set)

    # ------------------------------------------------------------ recording
    def record_llm(self, component: str, usage: LLMUsage, agent_id: str | None = None) -> None:
        c = self.by_component[component]
        c.calls += usage.calls
        c.tokens_in += usage.tokens_in
        c.tokens_out += usage.tokens_out
        c.cost += usage.cost
        c.duration_ms += usage.duration_ms
        if agent_id:
            a = self.by_agent[agent_id]
            a.calls += usage.calls
            a.tokens_in += usage.tokens_in
            a.tokens_out += usage.tokens_out
            a.cost += usage.cost
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
        cost = sum(c.cost for c in self.by_component.values())
        calls = sum(c.calls for c in self.by_component.values())
        over = [c for k, c in self.by_component.items() if k in OVERHEAD_COMPONENTS]
        overhead_tokens = sum(c.tokens_in + c.tokens_out for c in over)
        overhead_cost = sum(c.cost for c in over)
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
