"""System presets: FA-MAS and the baselines it is evaluated against.

All systems run on the same engine, agents, evaluator and failure injection;
they differ only in planning, agent selection and recovery policy. This keeps
the comparison controlled: any difference in the metrics is attributable to
those design choices.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from famas.core.models import SystemName, TaskType


class SystemSpec(BaseModel):
    name: SystemName
    title: str
    short: str
    description: str
    planner: Literal["dynamic", "single", "fixed"]
    selector: Literal["adaptive", "fixed"]
    recovery: Literal["incremental", "naive", "full_replan", "restart"]
    use_cache: bool
    verify_reused: bool = False
    fixed_agents: dict[TaskType, str] = Field(default_factory=dict)


SYSTEMS: dict[str, SystemSpec] = {
    "single_agent": SystemSpec(
        name="single_agent",
        title="Baseline A — Single agent",
        short="A: single agent",
        description="One general-purpose agent performs the entire request in one task; failures restart it.",
        planner="single",
        selector="fixed",
        recovery="restart",
        use_cache=False,
        fixed_agents={TaskType.GENERAL: "generalist"},
    ),
    "fixed_workflow": SystemSpec(
        name="fixed_workflow",
        title="Baseline B — Fixed multi-agent workflow",
        short="B: fixed workflow",
        description="Predefined research → analysis → (code) → writing pipeline with fixed agents; failures restart the pipeline.",
        planner="fixed",
        selector="fixed",
        recovery="restart",
        use_cache=False,
        fixed_agents={
            TaskType.RESEARCH: "research-fast",
            TaskType.ANALYSIS: "analysis-standard",
            TaskType.CODING: "coder",
            TaskType.WRITING: "writer",
            TaskType.GENERAL: "generalist",
        },
    ),
    "full_replan": SystemSpec(
        name="full_replan",
        title="Baseline C — Dynamic MAS with full replanning",
        short="C: full replan",
        description="Dynamic planning and agent selection; on failure the plan is regenerated (with the same failure "
        "diagnosis) and every task is recomputed.",
        planner="dynamic",
        selector="adaptive",
        recovery="full_replan",
        use_cache=False,
    ),
    "naive_incremental": SystemSpec(
        name="naive_incremental",
        title="Ablation D — Naive local retry",
        short="D: naive retry",
        description="Dynamic planning; on failure only the failed task is retried or reassigned. No root-cause "
        "attribution, no upstream invalidation, no decomposition or dependency repair.",
        planner="dynamic",
        selector="adaptive",
        recovery="naive",
        use_cache=True,
    ),
    "fa_mas": SystemSpec(
        name="fa_mas",
        title="FA-MAS — Failure-aware incremental replanning",
        short="FA-MAS",
        description="Dynamic planning, adaptive agent selection, eager DAG scheduling, and failure-aware, "
        "dependency-aware incremental replanning with fingerprint-validated result reuse.",
        planner="dynamic",
        selector="adaptive",
        recovery="incremental",
        use_cache=True,
    ),
    "fa_mas_verify": SystemSpec(
        name="fa_mas_verify",
        title="FA-MAS + reuse audit",
        short="FA-MAS+audit",
        description="FA-MAS that additionally re-verifies preserved upstream results (one small-model check each) "
        "before reusing them in a recovery round, to catch latent errors that incremental reuse would otherwise keep.",
        planner="dynamic",
        selector="adaptive",
        recovery="incremental",
        use_cache=True,
        verify_reused=True,
    ),
}


def get_system(name: str) -> SystemSpec:
    if name not in SYSTEMS:
        raise KeyError(f"unknown system {name!r}; choose from {sorted(SYSTEMS)}")
    return SYSTEMS[name]
