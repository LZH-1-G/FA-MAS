"""Run context: the runtime services shared by every phase of one run.

LangGraph state must be serialisable (it is checkpointed), so live objects -
LLM clients, event bus, metrics, caches - live here instead and are looked up
by ``run_id`` from inside graph nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from famas.agents.registry import AgentRegistry
from famas.config import Settings
from famas.core.clock import Clock
from famas.core.events import EventBus
from famas.core.models import ReplanRecord, RunConfig, TaskAnalysis
from famas.llm.base import LLMClient, LLMResponse, MockFn, Tier
from famas.monitoring.metrics import MetricsCollector
from famas.orchestration.cache import ResultCache
from famas.orchestration.stats import AgentStatsStore
from famas.orchestration.systems import SystemSpec
from famas.simulation.injection import FailureInjector
from famas.simulation.oracle import SimulationOracle
from famas.tools.github import GitHubClient
from famas.tools.sandbox import CodeSandbox

if TYPE_CHECKING:  # pragma: no cover
    from pydantic import BaseModel


@dataclass
class RunContext:
    run_id: str
    request: str
    config: RunConfig
    system: SystemSpec
    settings: Settings
    llm: LLMClient
    registry: AgentRegistry
    clock: Clock
    events: EventBus
    metrics: MetricsCollector
    cache: ResultCache
    stats: AgentStatsStore
    oracle: SimulationOracle
    sandbox: CodeSandbox
    github: GitHubClient
    injector: FailureInjector | None = None
    analysis: TaskAnalysis | None = None
    replans: list[ReplanRecord] = field(default_factory=list)
    plan_attempts: int = 0
    audited: set[str] = field(default_factory=set)  # result ids already re-verified by the reuse audit
    running: dict[str, str] = field(default_factory=dict)  # task_id -> agent_id (live view)

    async def llm_call(
        self,
        *,
        component: str,
        system: str,
        prompt: str,
        schema: type[BaseModel] | None = None,
        tier: Tier = "large",
        mock: MockFn | None = None,
        advance_clock: bool = True,
    ) -> LLMResponse:
        """LLM call made by a system component (planner, analyzers): metered and on the run clock."""
        resp = await self.llm.complete(
            component=component, system=system, prompt=prompt, schema=schema, tier=tier, mock=mock
        )
        self.metrics.record_llm(component, resp.usage)
        if advance_clock:
            self.clock.advance(resp.usage.duration_ms)
        return resp


_CONTEXTS: dict[str, RunContext] = {}


def register_context(ctx: RunContext) -> None:
    _CONTEXTS[ctx.run_id] = ctx


def get_context(run_id: str) -> RunContext:
    try:
        return _CONTEXTS[run_id]
    except KeyError as e:
        raise RuntimeError(f"no active run context for {run_id}") from e


def release_context(run_id: str) -> None:
    _CONTEXTS.pop(run_id, None)
