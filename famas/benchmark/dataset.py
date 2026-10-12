"""Benchmark task set: repository-research requests of increasing complexity."""

from __future__ import annotations

from pydantic import BaseModel


class BenchTask(BaseModel):
    id: str
    complexity: str
    request: str


DEFAULT_TASKS: list[BenchTask] = [
    BenchTask(id="T1", complexity="simple", request="Summarize the architecture of LangGraph."),
    BenchTask(id="T2", complexity="medium", request="Compare the architectures of LangGraph and CrewAI."),
    BenchTask(
        id="T3",
        complexity="medium",
        request="How do AutoGen and Microsoft Agent Framework handle failure recovery and state management? Compare them.",
    ),
    BenchTask(
        id="T4",
        complexity="complex",
        request=(
            "Analyze LangGraph, CrewAI, AutoGen, and Microsoft Agent Framework. Compare their system architectures, "
            "agent communication mechanisms, scheduling strategies, and failure recovery capabilities. Generate a "
            "comprehensive technical report."
        ),
    ),
    BenchTask(
        id="T5",
        complexity="complex",
        request=(
            "Analyze LangGraph, CrewAI, AutoGen, MetaGPT and OpenAI Agents SDK. Compare architecture, communication, "
            "scheduling, failure recovery and observability, and include a computed feature matrix."
        ),
    ),
    BenchTask(
        id="T6",
        complexity="complex",
        request=(
            "Research CrewAI, MetaGPT and Microsoft Agent Framework: compare their agent communication mechanisms, "
            "orchestration strategies and tooling ecosystems in a technical report."
        ),
    ),
]

EXAMPLE_REQUESTS = [t.request for t in DEFAULT_TASKS]


def get_tasks(ids: list[str] | None = None) -> list[BenchTask]:
    if not ids:
        return list(DEFAULT_TASKS)
    by_id = {t.id: t for t in DEFAULT_TASKS}
    return [by_id[i] for i in ids if i in by_id]
