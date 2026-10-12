"""Named failure scenarios for experiments and demos.

Targets use placeholders resolved per request: ``{s0}``/``{s1}``/``{s2}`` are
the first/second/third subjects (clamped to the last available one) and
``{a0}`` the first aspect.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from famas.core.models import InjectionRule


class Scenario(BaseModel):
    name: str
    title: str
    description: str
    rules: list[InjectionRule] = Field(default_factory=list)
    natural_failure_rate: float = 0.0
    stochastic: bool = False


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in [
        Scenario(
            name="none", title="No failures", description="Clean execution; measures baseline efficiency."
        ),
        Scenario(
            name="transient",
            title="Transient timeout",
            description="The first research execution on subject s0 times out; a retry succeeds.",
            rules=[InjectionRule(kind="timeout", target="research:{s0}:*", on_executions=[1])],
        ),
        Scenario(
            name="agent_failure",
            title="Incapable agent",
            description="Whichever agent first analyses aspect a0 cannot handle it (persistent); recovery must reassign.",
            rules=[
                InjectionRule(
                    kind="agent_incapable", target="analysis:{a0}:*", on_executions="all", agent="@initial"
                )
            ],
        ),
        Scenario(
            name="complex_task",
            title="Task too complex",
            description="Research on subject s1 only covers two aspects per pass; larger tasks return incomplete output.",
            rules=[InjectionRule(kind="too_complex", target="research:{s1}:*", max_units=2)],
        ),
        Scenario(
            name="corruption",
            title="Silent upstream corruption",
            description="Research on s_last returns a plausible but contradictory claim that passes its own checks; "
            "downstream analysis detects the contradiction.",
            rules=[InjectionRule(kind="silent_corruption", target="research:{s_last}:*", on_executions=[1])],
        ),
        Scenario(
            name="missing_dependency",
            title="Planner omits a dependency",
            description="The first plan forgets to feed research on s1 into the analysis of a0.",
            rules=[InjectionRule(kind="drop_dependency", target="analysis:{a0}:*", subject="{s1}")],
        ),
        Scenario(
            name="late_failure",
            title="Late failure in report writing",
            description="The final report's first execution returns malformed output.",
            rules=[InjectionRule(kind="invalid_format", target="report:*", on_executions=[1])],
        ),
        Scenario(
            name="cascade",
            title="Cascade of failures",
            description="Transient research timeout + silent corruption + late report failure in one run.",
            rules=[
                InjectionRule(kind="timeout", target="research:{s0}:*", on_executions=[1]),
                InjectionRule(kind="silent_corruption", target="research:{s_last}:*", on_executions=[1]),
                InjectionRule(kind="invalid_format", target="report:*", on_executions=[1]),
            ],
        ),
        Scenario(
            name="stochastic",
            title="Stochastic failures",
            description="Each execution fails with probability ~12% (scaled by model tier) with a random failure type.",
            natural_failure_rate=0.12,
            stochastic=True,
        ),
    ]
}


def get_scenario(name: str) -> Scenario:
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}; choose from {sorted(SCENARIOS)}")
    return SCENARIOS[name]
