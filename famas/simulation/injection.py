"""Controlled, reproducible failure injection.

Faults are attached to *work units* (``research:crewai:architecture``), not to
task ids, so the same scenario applies fairly to every system under test: a
single-agent baseline whose one task covers every unit, a fixed pipeline, or
the dynamic DAG. Each rule keeps an execution counter, and units are visited in
the order a task covers them, so a monolithic task that fails early does not
"consume" faults it never reached.
"""

from __future__ import annotations

import fnmatch
import hashlib
import math
from dataclasses import dataclass, field

from famas.core.models import InjectionRule, Task, TaskAnalysis, TaskType

EXCEPTION_KINDS = {"timeout", "tool_error", "rate_limit", "agent_crash", "agent_incapable"}
OUTPUT_KINDS = {"invalid_format", "incomplete", "too_complex", "silent_corruption"}
TRANSIENT = {"timeout", "tool_error", "rate_limit", "agent_crash"}


class InjectedError(RuntimeError):
    """Raised for exception-type faults. Carries the semantics a real error would."""

    def __init__(self, kind: str, message: str, transient: bool) -> None:
        super().__init__(message)
        self.kind = kind
        self.transient = transient


ERROR_TYPE_NAMES = {
    "timeout": "TimeoutError",
    "tool_error": "ToolError",
    "rate_limit": "RateLimitError",
    "agent_crash": "AgentCrash",
    "agent_incapable": "AgentCapabilityError",
}


@dataclass
class Fault:
    kind: str
    rule_index: int | None
    message: str
    units: list[str] = field(default_factory=list)  # units affected (dropped / corrupted)
    natural: bool = False

    @property
    def is_exception(self) -> bool:
        return self.kind in EXCEPTION_KINDS

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT

    def to_error(self) -> InjectedError:
        return InjectedError(self.kind, self.message, self.transient)


def seeded_uniform(*parts: object) -> float:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return int(h[:12], 16) / float(16**12)


def resolve_placeholders(rule: InjectionRule, analysis: TaskAnalysis | None) -> InjectionRule:
    subjects = [s.key for s in analysis.subjects] if analysis else []
    aspects = list(analysis.aspects) if analysis else []

    def fill(text: str | None) -> str | None:
        if not text:
            return text
        out = text
        for i in range(6):
            if f"{{s{i}}}" in out:
                out = out.replace(
                    f"{{s{i}}}", subjects[min(i, len(subjects) - 1)] if subjects else "__none__"
                )
            if f"{{a{i}}}" in out:
                out = out.replace(f"{{a{i}}}", aspects[min(i, len(aspects) - 1)] if aspects else "__none__")
        out = out.replace("{s_last}", subjects[-1] if subjects else "__none__")
        return out

    return rule.model_copy(update={"target": fill(rule.target), "subject": fill(rule.subject)})


NATURAL_KINDS = ["timeout", "invalid_format", "incomplete", "agent_crash", "tool_error"]
TIER_FACTOR = {"small": 1.5, "large": 0.75, "none": 0.0}


class FailureInjector:
    def __init__(
        self,
        rules: list[InjectionRule],
        analysis: TaskAnalysis | None = None,
        seed: int = 0,
        natural_rate: float = 0.0,
    ) -> None:
        self.rules = [resolve_placeholders(r, analysis) for r in rules]
        self.seed = seed
        self.natural_rate = natural_rate
        self.counters: dict[int, int] = {}
        self.bound_agent: dict[int, str] = {}
        self.plan_faults_applied = False
        self.log: list[dict] = []

    # ----------------------------------------------------------- plan faults
    def dependency_drops(self, tasks: list[Task]) -> list[tuple[str, str]]:
        """Edges (task_id, dep_id) to remove from the *first* generated plan (planner omission faults)."""
        if self.plan_faults_applied:
            return []
        self.plan_faults_applied = True
        drops = []
        for rule in self.rules:
            if rule.kind != "drop_dependency":
                continue
            for t in tasks:
                if not any(fnmatch.fnmatch(u, rule.target) for u in t.covers):
                    continue
                for d in t.dependencies:
                    dep = next((x for x in tasks if x.id == d), None)
                    if dep and dep.inputs.get("subject") == rule.subject:
                        drops.append((t.id, d))
        self.log.extend({"kind": "drop_dependency", "edge": e} for e in drops)
        return drops

    # ------------------------------------------------------ execution faults
    def decide(self, task: Task, agent_id: str, agent_tier: str = "large") -> Fault | None:
        if task.task_type == TaskType.AGGREGATE:
            return None

        def first_index(rule: InjectionRule) -> int:
            for i, u in enumerate(task.covers):
                if fnmatch.fnmatch(u, rule.target):
                    return i
            return -1

        ordered = sorted(
            ((first_index(r), i, r) for i, r in enumerate(self.rules) if r.kind != "drop_dependency"),
            key=lambda x: (x[0], x[1]),
        )
        for pos, idx, rule in ordered:
            if pos < 0:
                continue
            matching = [u for u in task.covers if fnmatch.fnmatch(u, rule.target)]
            if rule.kind == "too_complex":
                limit = rule.max_units or 2
                if len(matching) > limit:
                    fault = Fault(
                        "too_complex",
                        idx,
                        f"task too large for one pass ({len(matching)} units > {limit})",
                        matching[limit:],
                    )
                    self.log.append({"task": task.id, "agent": agent_id, "kind": fault.kind})
                    return fault
                continue
            if rule.agent:
                if rule.agent == "@initial":
                    bound = self.bound_agent.setdefault(idx, agent_id)
                    if bound != agent_id:
                        continue
                elif rule.agent != agent_id:
                    continue
            self.counters[idx] = self.counters.get(idx, 0) + 1
            n = self.counters[idx]
            if rule.on_executions == "all" or n in rule.on_executions:
                units = matching
                if rule.kind == "incomplete":
                    units = matching[math.ceil(len(matching) / 2) :] or matching[-1:]
                elif rule.kind == "silent_corruption":
                    units = matching[:1]
                fault = Fault(
                    rule.kind, idx, f"injected {rule.kind} on {task.id} (execution #{n} of rule {idx})", units
                )
                self.log.append({"task": task.id, "agent": agent_id, "kind": fault.kind, "n": n})
                return fault

        if self.natural_rate > 0:
            p = self.natural_rate * TIER_FACTOR.get(agent_tier, 1.0)
            if seeded_uniform(self.seed, "natural", task.id, task.attempts, agent_id) < p:
                kind = NATURAL_KINDS[
                    int(seeded_uniform(self.seed, "kind", task.id, task.attempts) * len(NATURAL_KINDS))
                ]
                units = task.covers[math.ceil(len(task.covers) / 2) :] if kind == "incomplete" else []
                if kind == "incomplete" and not units:
                    kind = "invalid_format"
                fault = Fault(kind, None, f"natural {kind} on {task.id}", units, natural=True)
                self.log.append({"task": task.id, "agent": agent_id, "kind": kind, "natural": True})
                return fault
        return None
