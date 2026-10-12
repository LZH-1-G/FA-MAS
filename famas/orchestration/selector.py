"""Adaptive agent selection.

For each task the selector scores every capable agent on:

* **reliability** – Beta-posterior success rate for this task type (history + prior)
* **fit** – match between task complexity and model tier (large models for
  multi-unit or synthesis-heavy work, small models for narrow tasks)
* **specialisation** – specialists preferred over the generalist
* **cost** and **latency** – expected cost/latency per *successful* completion:
  token-volume x tier pricing (or the latency factor) divided by reliability,
  min-max normalised across candidates
* **load** – tasks already assigned to the agent in the current wave

and picks the highest score. The score breakdown is stored on the task so the
UI (and a human reviewer) can see why an agent was chosen.
"""

from __future__ import annotations

from famas.agents.registry import AgentRegistry
from famas.core.models import AgentSelection, AgentSpec, CandidateScore, Task, TaskType
from famas.llm.pricing import cost_of
from famas.orchestration.stats import AgentStatsStore

WEIGHTS = {
    "reliability": 0.50,
    "fit": 0.15,
    "specialisation": 0.15,
    "cost": 0.15,
    "latency": 0.10,
    "load": 0.10,
}
TIER_MODEL = {"small": "mock-small", "large": "mock-large", "none": "mock-small"}


def task_complexity(task: Task) -> float:
    """Heuristic complexity in [0, 1] from the amount of work a task covers."""
    units = len(task.covers) or 1
    base = min(1.0, (units - 1) / 4.0)
    if task.task_type in (TaskType.WRITING, TaskType.CODING):
        base = max(base, 0.7)
    if task.task_type == TaskType.ANALYSIS and len(task.inputs.get("subjects", [])) >= 3:
        base = max(base, 0.6)
    if task.feedback:
        base = min(1.0, base + 0.2)
    return base


def estimated_tokens(task: Task) -> tuple[int, int]:
    units = len(task.covers) or 1
    return 800 + 600 * units, 150 + 180 * units


class AgentSelector:
    def __init__(
        self, registry: AgentRegistry, stats: AgentStatsStore, fixed: dict[TaskType, str] | None = None
    ) -> None:
        self.registry = registry
        self.stats = stats
        self.fixed = fixed or {}
        self.load: dict[str, int] = {}

    def reset_load(self) -> None:
        self.load = {}

    def candidates(self, task: Task) -> list[AgentSpec]:
        if task.task_type == TaskType.AGGREGATE:
            return [s for s in self.registry.specs() if s.system and "aggregate" in s.capabilities]
        return self.registry.capable(task.required_capabilities, exclude=task.excluded_agents)

    def select(self, task: Task) -> AgentSelection | None:
        if task.task_type == TaskType.AGGREGATE:
            return AgentSelection(agent_id="aggregator", score=1.0, strategy="system")
        if self.fixed:
            agent_id = self.fixed.get(task.task_type) or self.fixed.get(TaskType.GENERAL)
            if agent_id and agent_id not in task.excluded_agents:
                return AgentSelection(agent_id=agent_id, score=1.0, strategy="fixed")
            return None
        return self._adaptive(task)

    def _adaptive(self, task: Task) -> AgentSelection | None:
        cands = self.candidates(task)
        if not cands:
            return None
        complexity = task_complexity(task)
        tin, tout = estimated_tokens(task)
        rels = {c.id: self.stats.reliability(c.id, task.task_type.value, c.prior_reliability) for c in cands}
        # expected cost/latency per *successful* completion: unreliable agents need more attempts
        costs = {c.id: cost_of(TIER_MODEL[c.model_tier], tin, tout) / max(rels[c.id], 0.05) for c in cands}
        lats = {c.id: c.latency_factor / max(rels[c.id], 0.05) for c in cands}

        def norm(values: dict[str, float], key: str) -> float:
            lo, hi = min(values.values()), max(values.values())
            return 0.0 if hi - lo < 1e-12 else (values[key] - lo) / (hi - lo)

        scored: list[CandidateScore] = []
        for c in cands:
            rel = rels[c.id]
            fit = complexity if c.model_tier == "large" else 1.0 - complexity
            spec = 1.0 if c.kind == task.task_type else 0.3
            load = min(1.0, self.load.get(c.id, 0) / max(1, c.max_concurrency))
            br = {
                "reliability": round(rel, 4),
                "fit": round(fit, 4),
                "specialisation": spec,
                "cost": round(norm(costs, c.id), 4),
                "latency": round(norm(lats, c.id), 4),
                "load": round(load, 4),
            }
            score = (
                WEIGHTS["reliability"] * br["reliability"]
                + WEIGHTS["fit"] * br["fit"]
                + WEIGHTS["specialisation"] * br["specialisation"]
                - WEIGHTS["cost"] * br["cost"]
                - WEIGHTS["latency"] * br["latency"]
                - WEIGHTS["load"] * br["load"]
            )
            scored.append(CandidateScore(agent_id=c.id, score=round(score, 4), eligible=True, breakdown=br))
        scored.sort(key=lambda s: (-s.score, s.agent_id))
        best = scored[0]
        self.load[best.agent_id] = self.load.get(best.agent_id, 0) + 1
        for ex in task.excluded_agents:
            scored.append(
                CandidateScore(agent_id=ex, score=0.0, eligible=False, reason="excluded after failure")
            )
        return AgentSelection(
            agent_id=best.agent_id, score=best.score, strategy="adaptive", candidates=scored
        )
