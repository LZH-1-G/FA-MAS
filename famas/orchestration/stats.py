"""Historical agent performance, used by the adaptive agent selector."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass
class AgentRecord:
    successes: int = 0
    failures: int = 0
    total_ms: float = 0.0
    total_tokens: int = 0

    @property
    def runs(self) -> int:
        return self.successes + self.failures


class AgentStatsStore:
    """Per (agent, task type) success/failure counts with a Beta prior.

    ``reliability`` returns the posterior mean of a Beta(prior_strength * p,
    prior_strength * (1 - p)) prior updated with observed outcomes, so new agents
    start at their declared prior and drift toward observed behaviour.
    """

    def __init__(self, prior_strength: float = 4.0) -> None:
        self.prior_strength = prior_strength
        self._data: dict[tuple[str, str], AgentRecord] = defaultdict(AgentRecord)

    def record(
        self, agent_id: str, task_type: str, success: bool, duration_ms: float = 0.0, tokens: int = 0
    ) -> None:
        r = self._data[(agent_id, task_type)]
        if success:
            r.successes += 1
        else:
            r.failures += 1
        r.total_ms += duration_ms
        r.total_tokens += tokens

    def get(self, agent_id: str, task_type: str) -> AgentRecord:
        return self._data.get((agent_id, task_type), AgentRecord())

    def reliability(self, agent_id: str, task_type: str, prior: float) -> float:
        r = self.get(agent_id, task_type)
        a = self.prior_strength * prior + r.successes
        b = self.prior_strength * (1 - prior) + r.failures
        return a / (a + b)

    def snapshot(self) -> list[dict]:
        return [
            {
                "agent_id": a,
                "task_type": t,
                "successes": r.successes,
                "failures": r.failures,
                "avg_ms": (r.total_ms / r.runs) if r.runs else 0.0,
                "tokens": r.total_tokens,
            }
            for (a, t), r in sorted(self._data.items())
        ]

    def load(self, rows: list[dict]) -> None:
        for row in rows:
            r = self._data[(row["agent_id"], row["task_type"])]
            r.successes = int(row.get("successes", 0))
            r.failures = int(row.get("failures", 0))
            runs = r.successes + r.failures
            r.total_ms = float(row.get("avg_ms", 0.0)) * runs
            r.total_tokens = int(row.get("tokens", 0))
