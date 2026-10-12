"""Agent base class and the per-execution context handed to agents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from famas.core.models import AgentSpec, Issue, Source, Task, TaskResult
from famas.llm.base import LLMResponse, MockFn, Tier

if TYPE_CHECKING:  # pragma: no cover
    from famas.orchestration.context import RunContext
    from famas.simulation.injection import Fault


class AgentOutput(BaseModel):
    content: str
    data: dict[str, Any] = Field(default_factory=dict)
    sources: list[Source] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


@dataclass
class ExecContext:
    """Everything one agent execution may use; accumulates the execution's cost."""

    run: RunContext
    task: Task
    agent: AgentSpec
    attempt: int
    fault: Fault | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    duration_ms: float = 0.0
    llm_calls: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def live(self) -> bool:
        return not self.run.llm.is_mock

    async def llm(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[BaseModel] | None = None,
        mock: MockFn | None = None,
        tier: Tier | None = None,
    ) -> LLMResponse:
        resp = await self.run.llm.complete(
            component=f"agent:{self.agent.id}",
            system=system,
            prompt=prompt,
            schema=schema,
            tier=tier or self.agent.model_tier,
            mock=mock,
        )
        u = resp.usage
        self.tokens_in += u.tokens_in
        self.tokens_out += u.tokens_out
        self.cost += u.cost
        self.duration_ms += u.duration_ms
        self.llm_calls += u.calls
        self.run.metrics.record_llm(f"agent:{self.agent.id}", u, agent_id=self.agent.id)
        return resp

    def add_time(self, ms: float) -> None:
        self.duration_ms += ms


class BaseAgent:
    def __init__(self, spec: AgentSpec) -> None:
        self.spec = spec

    @property
    def id(self) -> str:
        return self.spec.id

    async def run(self, task: Task, inputs: dict[str, TaskResult], ectx: ExecContext) -> AgentOutput:
        raise NotImplementedError

    # ------------------------------------------------------------ helpers
    @staticmethod
    def feedback_block(task: Task) -> str:
        if not task.feedback:
            return ""
        lines = "\n".join(f"- {f}" for f in task.feedback)
        return f"\n\nIMPORTANT - feedback from earlier attempts or downstream reviewers:\n{lines}\n"

    @staticmethod
    def upstream_block(inputs: dict[str, TaskResult], max_chars: int = 3500) -> str:
        if not inputs:
            return "(no upstream inputs)"
        parts = []
        for tid, res in inputs.items():
            parts.append(f"### Upstream task `{tid}` (agent {res.agent_id})\n{res.content[:max_chars]}")
        return "\n\n".join(parts)

    @staticmethod
    def collect_sources(inputs: dict[str, TaskResult]) -> list[Source]:
        seen: set[tuple[str, str | None]] = set()
        out: list[Source] = []
        for res in inputs.values():
            for s in res.sources:
                key = (s.title, s.url)
                if key not in seen:
                    seen.add(key)
                    out.append(s)
        return out


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def normalize_aspect(name: str, wanted: list[str]) -> str | None:
    """Map a model-written aspect label (e.g. 'Failure Recovery') to a requested aspect key."""
    s = slug(name)
    if s in wanted:
        return s
    for w in wanted:
        if w in s or s in w or w.split("_")[0] in s:
            return w
    return None
