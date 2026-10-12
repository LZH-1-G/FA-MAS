"""Dynamic Scheduler (dispatcher).

Executes the task DAG eagerly: a task is dispatched the moment all of its
dependencies are completed (and validated) - not at the next "round" - subject
to a concurrency limit. Before executing, the dispatcher checks whether a valid
result already exists for the task's current input fingerprint (result cache or
a result invalidated by recovery whose inputs turned out unchanged) and reuses
it instead.

When any task fails, the dispatcher stops launching new tasks, lets in-flight
tasks finish (their results are kept), and returns the failures to the
recovery phase.

Two execution modes share the same logic:

* real-time - asyncio tasks, completion order is the wall-clock order;
* simulated - a discrete-event simulation on a virtual clock: mock agents report a
  modelled duration and completions are processed in virtual-time order. This
  makes benchmark latency deterministic while preserving the true schedule.
"""

from __future__ import annotations

import asyncio
import heapq
from dataclasses import dataclass, field

from famas.agents.base import ExecContext
from famas.core.fingerprint import output_fingerprint
from famas.core.models import (
    Evaluation,
    FailureRecord,
    RecoveryTag,
    Task,
    TaskResult,
    TaskStatus,
)
from famas.llm.base import LLMUsage, estimate_tokens
from famas.llm.pricing import cost_of
from famas.orchestration.context import RunContext
from famas.orchestration.selector import AgentSelector
from famas.planning.dag import TaskGraph
from famas.recovery.evaluator import ResultEvaluator
from famas.simulation.injection import ERROR_TYPE_NAMES, Fault, InjectedError

TIMEOUT_COST_MS = 20000.0  # a timed-out call blocks until the client timeout fires
FAST_FAIL_MS = {"tool_error": 400.0, "rate_limit": 250.0}


@dataclass
class Outcome:
    task_id: str
    agent_id: str
    success: bool
    duration_ms: float
    result: TaskResult | None = None
    evaluation: Evaluation | None = None
    failure: FailureRecord | None = None
    tokens: int = 0
    input_fp: str = ""


@dataclass
class WaveReport:
    failures: list[FailureRecord] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    started_ms: float = 0.0
    ended_ms: float = 0.0


class NoCapableAgent(RuntimeError):
    transient = False


class Dispatcher:
    def __init__(
        self, ctx: RunContext, selector: AgentSelector, evaluator: ResultEvaluator | None = None
    ) -> None:
        self.ctx = ctx
        self.selector = selector
        self.evaluator = evaluator or ResultEvaluator()

    # ------------------------------------------------------------- public
    async def run_wave(self, graph: TaskGraph) -> WaveReport:
        report = WaveReport(started_ms=self.ctx.clock.now())
        if self.ctx.clock.simulated:
            await self._run_simulated(graph, report)
        else:
            await self._run_realtime(graph, report)
        report.ended_ms = self.ctx.clock.now()
        self.ctx.events.emit(
            "wave_finished",
            completed=report.completed,
            reused=report.reused,
            failed=[f.task_id for f in report.failures],
            counts=graph.counts(),
        )
        return report

    # ------------------------------------------------------- reuse / assign
    def try_reuse(self, graph: TaskGraph, task: Task) -> bool:
        fp = graph.input_fingerprint(task.id)
        candidate, how = None, None
        if (
            self.ctx.config.early_cutoff
            and task.previous_result is not None
            and task.previous_result.input_fingerprint == fp
        ):
            candidate, how = task.previous_result, "early_cutoff"
        elif self.ctx.system.use_cache:
            cached = self.ctx.cache.get(fp)
            if cached is not None:
                candidate, how = cached, "cache"
        if candidate is None:
            return False
        res = candidate.model_copy(deep=True)
        res.task_id = task.id
        task.result = res
        task.previous_result = None
        task.status = TaskStatus.COMPLETED
        if task.recovery_tag in (
            RecoveryTag.RECOMPUTE,
            RecoveryTag.REPAIR,
            RecoveryTag.RETRY,
            RecoveryTag.RESTART,
        ):
            task.recovery_tag = RecoveryTag.CACHE_HIT
        task.finished_ms = self.ctx.clock.now()
        self.ctx.metrics.record_cache_hit()
        self.ctx.events.emit("task_reused", task_id=task.id, how=how, result_id=res.result_id)
        return True

    def ensure_assigned(self, task: Task) -> str:
        if (
            task.assigned_agent
            and task.assigned_agent not in task.excluded_agents
            and task.assigned_agent in self.ctx.registry
        ):
            return task.assigned_agent
        sel = self.selector.select(task)
        if sel is None:
            raise NoCapableAgent(f"no capable agent for {task.id} (excluded: {task.excluded_agents})")
        task.assigned_agent = sel.agent_id
        task.selection = sel
        self.ctx.events.emit("agents_assigned", assignments={task.id: sel.agent_id}, late=True)
        return sel.agent_id

    def _launchable(self, graph: TaskGraph, running: set[str], capacity: int) -> list[Task]:
        """Ready tasks to start now; reuse is applied first and may cascade."""
        while True:
            progressed = False
            for t in graph.ready_tasks():
                if t.id not in running and self.try_reuse(graph, t):
                    progressed = True
            if not progressed:
                break
        ready = [t for t in graph.ready_tasks() if t.id not in running]
        return ready[: max(0, capacity)]

    # ------------------------------------------------------------ execute
    async def _burn(self, ectx: ExecContext, fault: Fault, task: Task, inputs: dict[str, TaskResult]) -> None:
        """Model the resources a failed call consumes before the error surfaces."""
        if fault.kind == "timeout":
            prompt_tokens = 1200 + sum(estimate_tokens(r.content) for r in inputs.values())
            model = self.ctx.llm.model_for(ectx.agent.model_tier)
            cost = cost_of(model, prompt_tokens, 0)
            ectx.tokens_in += prompt_tokens
            ectx.cost += cost
            ectx.duration_ms += TIMEOUT_COST_MS
            self.ctx.metrics.record_llm(
                f"agent:{ectx.agent.id}",
                LLMUsage(
                    tokens_in=prompt_tokens, tokens_out=0, cost=cost, duration_ms=TIMEOUT_COST_MS, model=model
                ),
                agent_id=ectx.agent.id,
            )
            if not self.ctx.clock.simulated and self.ctx.config.time_scale > 0:
                await asyncio.sleep(TIMEOUT_COST_MS * self.ctx.config.time_scale / 1000.0)
        elif fault.kind in FAST_FAIL_MS:
            ectx.duration_ms += FAST_FAIL_MS[fault.kind]
        else:  # agent_crash / agent_incapable: the agent works, then fails
            impl = self.ctx.registry.impl(ectx.agent.id)
            try:
                await impl.run(task, inputs, ectx)
            except Exception:  # noqa: BLE001
                pass

    async def execute(self, graph: TaskGraph, task: Task, start_ms: float) -> Outcome:
        try:
            agent_id = self.ensure_assigned(task)
        except NoCapableAgent as e:
            task.attempts += 1
            rec = FailureRecord(
                task_id=task.id,
                attempt=task.attempts,
                agent_id=None,
                kind="exception",
                error_type="NoCapableAgent",
                message=str(e),
                transient_hint=False,
                at_ms=start_ms,
            )
            return Outcome(task.id, "none", False, 0.0, failure=rec)
        spec = self.ctx.registry.spec(agent_id)
        impl = self.ctx.registry.impl(agent_id)
        task.attempts += 1
        attempt = task.attempts
        task.status = TaskStatus.RUNNING
        task.started_ms = start_ms
        self.ctx.running[task.id] = agent_id
        self.ctx.events.emit(
            "task_started",
            task_id=task.id,
            agent=agent_id,
            attempt=attempt,
            tag=task.recovery_tag.value if task.recovery_tag else None,
        )
        inputs = {
            d: graph.get(d).result
            for d in task.dependencies
            if d in graph and graph.get(d).result is not None
        }
        fp = graph.input_fingerprint(task.id)
        injector = self.ctx.injector
        fault = injector.decide(task, agent_id, spec.model_tier) if injector else None
        ectx = ExecContext(run=self.ctx, task=task, agent=spec, attempt=attempt, fault=fault)
        try:
            if fault is not None and fault.is_exception:
                await self._burn(ectx, fault, task, inputs)
                raise fault.to_error()
            coro = impl.run(task, inputs, ectx)
            out = await (
                coro
                if self.ctx.clock.simulated
                else asyncio.wait_for(coro, timeout=self.ctx.config.task_timeout_s)
            )
            result = TaskResult(
                task_id=task.id,
                agent_id=agent_id,
                attempt=attempt,
                content=out.content,
                data=out.data,
                sources=out.sources,
                issues=out.issues,
                tokens_in=ectx.tokens_in,
                tokens_out=ectx.tokens_out,
                cost=round(ectx.cost, 6),
                duration_ms=round(ectx.duration_ms, 2),
                llm_calls=ectx.llm_calls,
                input_fingerprint=fp,
                lineage=[r.result_id for r in inputs.values()],
            )
            self.ctx.oracle.propagate(result, inputs)
            if fault is not None:
                result = self.ctx.oracle.apply_output_fault(result, fault)
            result.output_fingerprint = output_fingerprint(result.content, result.data)
            evaluation = await self.evaluator.evaluate(task, result, self.ctx)
            duration = ectx.duration_ms + evaluation.duration_ms
            tokens = ectx.tokens_in + ectx.tokens_out
            if evaluation.passed:
                return Outcome(
                    task.id,
                    agent_id,
                    True,
                    duration,
                    result=result,
                    evaluation=evaluation,
                    tokens=tokens,
                    input_fp=fp,
                )
            primary = evaluation.issues[0].kind.value if evaluation.issues else "evaluation_failed"
            rec = FailureRecord(
                task_id=task.id,
                attempt=attempt,
                agent_id=agent_id,
                kind="evaluation",
                error_type=primary,
                message="; ".join(i.message for i in evaluation.issues[:3]) or "output rejected by evaluator",
                evaluation=evaluation,
                partial_result=result,
                tokens_wasted=tokens,
            )
            return Outcome(
                task.id,
                agent_id,
                False,
                duration,
                evaluation=evaluation,
                failure=rec,
                tokens=tokens,
                input_fp=fp,
            )
        except Exception as e:  # noqa: BLE001 - every agent error becomes a structured failure
            if isinstance(e, InjectedError):
                etype = ERROR_TYPE_NAMES.get(e.kind, "InjectedError")
            elif isinstance(e, asyncio.TimeoutError):
                etype = "TimeoutError"
            else:
                etype = type(e).__name__
            tokens = ectx.tokens_in + ectx.tokens_out
            rec = FailureRecord(
                task_id=task.id,
                attempt=attempt,
                agent_id=agent_id,
                kind="exception",
                error_type=etype,
                message=str(e)[:500],
                transient_hint=getattr(e, "transient", None),
                tokens_wasted=tokens,
            )
            return Outcome(
                task.id, agent_id, False, ectx.duration_ms, failure=rec, tokens=tokens, input_fp=fp
            )

    def apply(self, graph: TaskGraph, outcome: Outcome, report: WaveReport) -> None:
        task = graph.get(outcome.task_id)
        self.ctx.running.pop(task.id, None)
        now = self.ctx.clock.now()
        task.finished_ms = now
        valid = outcome.result is not None and not self.ctx.oracle.tainted(outcome.result)
        if outcome.agent_id != "none":
            self.ctx.metrics.record_execution(
                task.id,
                outcome.input_fp,
                outcome.tokens,
                outcome.success,
                outcome.result.result_id if outcome.result else None,
                valid,
            )
            self.ctx.stats.record(
                outcome.agent_id, task.task_type.value, outcome.success, outcome.duration_ms, outcome.tokens
            )
        if outcome.success and outcome.result is not None:
            task.result = outcome.result
            task.evaluation = outcome.evaluation
            task.previous_result = None
            task.status = TaskStatus.COMPLETED
            if self.ctx.system.use_cache:
                self.ctx.cache.put(outcome.input_fp, outcome.result)
            report.completed.append(task.id)
            self.ctx.events.emit(
                "task_completed",
                task_id=task.id,
                agent=outcome.agent_id,
                attempt=task.attempts,
                duration_ms=round(outcome.duration_ms, 1),
                tokens=outcome.tokens,
                score=outcome.evaluation.score if outcome.evaluation else None,
                checks=[c.model_dump() for c in outcome.evaluation.checks] if outcome.evaluation else [],
                preview=outcome.result.content[:1500],
                sources=[s.model_dump() for s in outcome.result.sources[:8]],
            )
        else:
            rec = outcome.failure
            rec.at_ms = now
            task.status = TaskStatus.FAILED
            task.failures.append(rec)
            task.evaluation = rec.evaluation
            report.failures.append(rec)
            self.ctx.events.emit(
                "task_failed",
                task_id=task.id,
                agent=outcome.agent_id,
                attempt=rec.attempt,
                error_type=rec.error_type,
                message=rec.message,
                duration_ms=round(outcome.duration_ms, 1),
                checks=[c.model_dump() for c in rec.evaluation.checks] if rec.evaluation else [],
                issues=[i.model_dump(mode="json") for i in rec.evaluation.issues] if rec.evaluation else [],
            )

    # --------------------------------------------------------------- loops
    async def _run_simulated(self, graph: TaskGraph, report: WaveReport) -> None:
        heap: list[tuple[float, int, Outcome]] = []
        running: set[str] = set()
        seq = 0
        stop = False
        now = self.ctx.clock.now()
        self.selector.reset_load()
        while True:
            if not stop:
                batch = self._launchable(graph, running, self.ctx.config.max_concurrency - len(running))
                if batch:
                    for t in batch:
                        running.add(t.id)
                    outcomes = await asyncio.gather(*(self.execute(graph, t, now) for t in batch))
                    for o in outcomes:
                        seq += 1
                        heapq.heappush(heap, (now + o.duration_ms, seq, o))
            if not heap:
                break
            end, _, outcome = heapq.heappop(heap)
            now = end
            self.ctx.clock.set(now)
            running.discard(outcome.task_id)
            self.apply(graph, outcome, report)
            if not outcome.success:
                stop = True

    async def _run_realtime(self, graph: TaskGraph, report: WaveReport) -> None:
        pending: dict[asyncio.Task, str] = {}
        stop = False
        self.selector.reset_load()
        while True:
            if not stop:
                running = set(pending.values())
                for t in self._launchable(graph, running, self.ctx.config.max_concurrency - len(running)):
                    fut = asyncio.create_task(self.execute(graph, t, self.ctx.clock.now()))
                    pending[fut] = t.id
            if not pending:
                break
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for fut in done:
                tid = pending.pop(fut)
                try:
                    outcome = fut.result()
                except Exception as e:  # noqa: BLE001 - defensive: execute() already traps agent errors
                    task = graph.get(tid)
                    outcome = Outcome(
                        tid,
                        task.assigned_agent or "none",
                        False,
                        0.0,
                        failure=FailureRecord(
                            task_id=tid,
                            attempt=task.attempts,
                            agent_id=task.assigned_agent,
                            kind="exception",
                            error_type=type(e).__name__,
                            message=str(e),
                        ),
                    )
                self.apply(graph, outcome, report)
                if not outcome.success:
                    stop = True
