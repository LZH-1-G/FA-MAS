from __future__ import annotations

import pytest

from famas.core.models import RunConfig, Task, TaskType
from famas.orchestration.engine import Engine
from famas.orchestration.stats import AgentStatsStore

FLAGSHIP = (
    "Analyze LangGraph, CrewAI, AutoGen, and Microsoft Agent Framework. Compare their system architectures, "
    "agent communication mechanisms, scheduling strategies, and failure recovery capabilities. Generate a "
    "comprehensive technical report."
)
MEDIUM = "Compare the architectures of LangGraph and CrewAI."


@pytest.fixture
def engine() -> Engine:
    return Engine(stats=AgentStatsStore())


@pytest.fixture
def run(engine):
    async def _run(request: str = FLAGSHIP, **cfg):
        engine.stats = AgentStatsStore()
        return await engine.run(request, RunConfig(**cfg))

    return _run


def make_task(tid: str, deps: list[str] | None = None, ttype: TaskType = TaskType.GENERAL, **kw) -> Task:
    return Task(
        id=tid,
        name=tid,
        description=f"task {tid}",
        task_type=ttype,
        dependencies=deps or [],
        covers=kw.pop("covers", [f"task:{tid}"]),
        **kw,
    )
