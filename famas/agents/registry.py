"""Agent registry: specs + implementations."""

from __future__ import annotations

from famas.agents.analysis import AnalysisAgent
from famas.agents.base import BaseAgent
from famas.agents.coding import CodingAgent
from famas.agents.general import AggregatorAgent, GeneralistAgent
from famas.agents.research import ResearchAgent
from famas.agents.specs import DEFAULT_AGENTS
from famas.agents.writing import WritingAgent
from famas.core.models import AgentSpec, TaskType

AGENT_CLASSES: dict[TaskType, type[BaseAgent]] = {
    TaskType.RESEARCH: ResearchAgent,
    TaskType.ANALYSIS: AnalysisAgent,
    TaskType.CODING: CodingAgent,
    TaskType.WRITING: WritingAgent,
    TaskType.GENERAL: GeneralistAgent,
    TaskType.AGGREGATE: AggregatorAgent,
}


class AgentRegistry:
    def __init__(self, specs: list[AgentSpec] | None = None) -> None:
        self._specs: dict[str, AgentSpec] = {}
        self._impls: dict[str, BaseAgent] = {}
        for s in specs or DEFAULT_AGENTS:
            self.register(s)

    def register(self, spec: AgentSpec, impl: BaseAgent | None = None) -> None:
        self._specs[spec.id] = spec
        self._impls[spec.id] = impl or AGENT_CLASSES[spec.kind](spec)

    def spec(self, agent_id: str) -> AgentSpec:
        return self._specs[agent_id]

    def impl(self, agent_id: str) -> BaseAgent:
        return self._impls[agent_id]

    def specs(self) -> list[AgentSpec]:
        return list(self._specs.values())

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._specs

    def capabilities(self) -> set[str]:
        caps: set[str] = set()
        for s in self._specs.values():
            caps.update(s.capabilities)
        return caps

    def capable(
        self, required: list[str], exclude: list[str] | None = None, include_system: bool = False
    ) -> list[AgentSpec]:
        exclude = exclude or []
        out = []
        for s in self._specs.values():
            if s.id in exclude or (s.system and not include_system):
                continue
            if all(c in s.capabilities for c in required):
                out.append(s)
        return out
