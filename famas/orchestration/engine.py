"""High-level entry point: build a run context and execute the workflow."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from famas.agents.registry import AgentRegistry
from famas.config import Settings, get_settings
from famas.core.clock import RealClock, SimClock
from famas.core.events import Event, EventBus
from famas.core.models import ReplanRecord, RunConfig, RunMetrics, TaskAnalysis, new_id
from famas.llm import create_llm
from famas.monitoring.metrics import MetricsCollector
from famas.orchestration.cache import InMemoryResultCache, LayeredCache, NullCache, ResultCache
from famas.orchestration.context import RunContext, register_context, release_context
from famas.orchestration.graph_runtime import LANGGRAPH_AVAILABLE, MemorySaver
from famas.orchestration.stats import AgentStatsStore
from famas.orchestration.systems import get_system
from famas.orchestration.workflow import build_workflow
from famas.planning.dag import TaskGraph
from famas.simulation.oracle import SimulationOracle
from famas.tools.github import GitHubClient
from famas.tools.sandbox import CodeSandbox


class RunOutcome(BaseModel):
    run_id: str
    request: str
    system: str
    status: str
    error: str | None = None
    final_output: str | None = None
    analysis: TaskAnalysis | None = None
    graph: dict[str, Any] = Field(default_factory=dict)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    replans: list[ReplanRecord] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)

    @property
    def task_graph(self) -> TaskGraph:
        return TaskGraph.from_dict(self.graph)

    @property
    def success(self) -> bool:
        return self.status == "completed"


class Engine:
    """Runs requests through the FA-MAS workflow (or a baseline system).

    One engine can serve many runs; per-run state lives in a ``RunContext``.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        registry: AgentRegistry | None = None,
        stats: AgentStatsStore | None = None,
        persistent_cache: ResultCache | None = None,
        checkpointer: Any = None,
        keep_events: bool = True,
        llm_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.registry = registry or AgentRegistry()
        self.stats = stats
        self.persistent_cache = persistent_cache
        self.keep_events = keep_events
        # create_llm(mode, settings, time_scale=...) by default; injectable for instrumentation.
        self.llm_factory = llm_factory
        if (
            checkpointer is None
            and self.settings.checkpointer == "memory"
            and LANGGRAPH_AVAILABLE
            and MemorySaver is not None
        ):
            checkpointer = (
                MemorySaver()
            )  # opt-in: an in-memory saver grows with every run in a long-lived server
        self.checkpointer = checkpointer
        self.workflow = build_workflow(checkpointer)
        self.sandbox = CodeSandbox()
        self.github = GitHubClient(self.settings.github_token, self.settings.github_api_url)

    def build_context(
        self, request: str, config: RunConfig, run_id: str | None = None, events: EventBus | None = None
    ) -> RunContext:
        config = config.model_copy(deep=True)
        if config.mode == "live":
            config.simulated_clock = False
        system = get_system(config.system)
        clock = SimClock() if config.simulated_clock else RealClock()
        run_id = run_id or new_id("run_")
        bus = events or EventBus(run_id, keep_history=self.keep_events)
        bus.clock = clock
        if system.use_cache:
            cache: ResultCache = LayeredCache(
                InMemoryResultCache(), self.persistent_cache if config.persist_cache else None
            )
        else:
            cache = NullCache()
        llm = (self.llm_factory or create_llm)(
            config.mode, self.settings, time_scale=0.0 if config.simulated_clock else config.time_scale
        )
        return RunContext(
            run_id=run_id,
            request=request,
            config=config,
            system=system,
            settings=self.settings,
            llm=llm,
            registry=self.registry,
            clock=clock,
            events=bus,
            metrics=MetricsCollector(),
            cache=cache,
            stats=self.stats if self.stats is not None else AgentStatsStore(),
            oracle=SimulationOracle(seed=config.seed, detection_probability=config.detection_probability),
            sandbox=self.sandbox,
            github=self.github,
        )

    async def run(
        self,
        request: str,
        config: RunConfig | None = None,
        run_id: str | None = None,
        events: EventBus | None = None,
        on_event: Callable[[Event], Any] | None = None,
    ) -> RunOutcome:
        ctx = self.build_context(request, config or RunConfig(), run_id, events)
        if on_event is not None:
            ctx.events.add_listener(on_event)
        register_context(ctx)
        ctx.events.emit(
            "run_started",
            request=request,
            config=ctx.config.model_dump(mode="json"),
            system=ctx.system.model_dump(mode="json"),
        )
        state: dict[str, Any] = {}
        try:
            # Provider checks that need no model call (e.g. the Claude Agent SDK provider refuses to
            # run when real calls are disabled or an API key would be billed instead of a subscription).
            await ctx.llm.preflight()
            state = await self.workflow.ainvoke(
                {"run_id": ctx.run_id, "request": request, "rounds": 0, "status": "running"},
                {"configurable": {"thread_id": ctx.run_id}, "recursion_limit": 1000},
            )
        except Exception as e:  # surface engine bugs as failed runs rather than crashing callers
            ctx.events.emit("run_failed", success=False, error=f"{type(e).__name__}: {e}")
            state = {"status": "failed", "error": f"{type(e).__name__}: {e}", "graph": state.get("graph", {})}
        finally:
            release_context(ctx.run_id)
            ctx.events.close()
        final = state.get("final") or {}
        metrics = RunMetrics.model_validate(final["metrics"]) if final.get("metrics") else RunMetrics()
        return RunOutcome(
            run_id=ctx.run_id,
            request=request,
            system=ctx.system.name,
            status=state.get("status", "failed"),
            error=state.get("error"),
            final_output=final.get("output"),
            analysis=ctx.analysis,
            graph=state.get("graph", {}),
            metrics=metrics,
            replans=ctx.replans,
            events=list(ctx.events.history),
        )

    def run_sync(self, request: str, config: RunConfig | None = None, **kw: Any) -> RunOutcome:
        return asyncio.run(self.run(request, config, **kw))
