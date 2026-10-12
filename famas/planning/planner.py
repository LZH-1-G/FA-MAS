"""Planner Agent: turns a task analysis into a task DAG.

Repository-research requests use the structured template planner (its output
is what the specialised agents expect); free-form multi-step requests in live
mode are decomposed by the LLM into a generic DAG. Either way the planning
step is an LLM call whose cost is accounted, and the result is validated
before execution.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from famas.agents import prompts
from famas.core.models import Task, TaskAnalysis, TaskType
from famas.llm.base import MockReply
from famas.planning import templates

if TYPE_CHECKING:  # pragma: no cover
    from famas.orchestration.context import RunContext


class PlannedTask(BaseModel):
    id: str
    name: str
    task_type: str = "general"
    description: str
    dependencies: list[str] = Field(default_factory=list)
    expected_output: str = ""


class PlanSchema(BaseModel):
    tasks: list[PlannedTask]
    rationale: str = ""


def _to_schema(tasks: list[Task]) -> PlanSchema:
    return PlanSchema(
        tasks=[
            PlannedTask(
                id=t.id,
                name=t.name,
                task_type=t.task_type.value,
                description=t.description,
                dependencies=t.dependencies,
                expected_output=t.expected_output,
            )
            for t in tasks
        ],
        rationale="template decomposition",
    )


def _from_schema(plan: PlanSchema) -> list[Task]:
    out = []
    for p in plan.tasks:
        try:
            ttype = TaskType(p.task_type)
        except ValueError:
            ttype = TaskType.GENERAL
        if ttype == TaskType.AGGREGATE:
            ttype = TaskType.GENERAL
        out.append(
            Task(
                id=p.id,
                name=p.name,
                description=p.description,
                task_type=ttype,
                dependencies=p.dependencies,
                expected_output=p.expected_output,
                covers=[f"task:{p.id}"],
                origin="planner",
            )
        )
    return out


class Planner:
    def __init__(self, kind: str = "dynamic") -> None:
        self.kind = kind

    def template(self, analysis: TaskAnalysis) -> list[Task]:
        if self.kind == "single":
            return templates.single_plan(analysis)
        if self.kind == "fixed":
            return templates.fixed_plan(analysis)
        return templates.dynamic_plan(analysis)

    def use_llm(self, analysis: TaskAnalysis, ctx: RunContext) -> bool:
        if self.kind != "dynamic" or ctx.llm.is_mock:
            return False
        mode = ctx.config.planner
        if mode == "template":
            return False
        if mode == "llm":
            return True
        return analysis.domain == "general" and analysis.strategy == "multi_agent"

    async def plan(
        self,
        analysis: TaskAnalysis,
        ctx: RunContext,
        feedback: list[str] | None = None,
        component: str = "planner",
    ) -> list[Task]:
        template_tasks = self.template(analysis)
        if self.kind != "dynamic":
            return template_tasks  # predefined workflows involve no planning call

        prompt = (
            f"Request: {analysis.request}\n\nAnalysis: {analysis.model_dump_json(exclude={'request'})}\n\n"
            "Available task types: research, analysis, coding, writing, general.\n"
        )
        if feedback:
            prompt += "\nFeedback from failed execution (address it in the new plan):\n" + "\n".join(
                f"- {f}" for f in feedback
            )
        if not self.use_llm(analysis, ctx):
            # Template plan; in mock mode the call still models the planning cost.
            if ctx.llm.is_mock:
                await ctx.llm_call(
                    component=component,
                    system=prompts.PLANNER_SYSTEM,
                    prompt=prompt,
                    schema=PlanSchema,
                    mock=lambda: MockReply(parsed=_to_schema(template_tasks)),
                )
            return template_tasks
        resp = await ctx.llm_call(
            component=component,
            system=prompts.PLANNER_SYSTEM,
            prompt=prompt + "\nReturn 2-8 tasks.",
            schema=PlanSchema,
            mock=lambda: MockReply(parsed=_to_schema(template_tasks)),
        )
        tasks = _from_schema(resp.parsed)
        return tasks or template_tasks

    @staticmethod
    def describe(tasks: list[Task]) -> str:
        return json.dumps([{"id": t.id, "deps": t.dependencies} for t in tasks])
