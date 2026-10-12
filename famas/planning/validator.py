"""DAG validation with conservative auto-repair.

The validator is run after initial planning *and* after every replanning
round, so the incremental replanner can never leave the system with a broken
plan. Errors that cannot be repaired safely (cycles, empty plans) are reported
back to the planner, which gets a bounded number of repair attempts.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from famas.core.models import TaskType
from famas.planning.dag import TaskGraph


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["error", "warning"]
    message: str
    task_id: str | None = None


class ValidationReport(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    def summary(self) -> str:
        if self.valid and not self.issues:
            return "plan valid"
        parts = [f"{i.severity}:{i.code}:{i.task_id or '-'}" for i in self.issues]
        return "; ".join(parts)


DEFAULT_CAPS_BY_TYPE = {
    TaskType.RESEARCH: ["research"],
    TaskType.ANALYSIS: ["analysis"],
    TaskType.CODING: ["coding"],
    TaskType.WRITING: ["writing"],
    TaskType.GENERAL: ["general"],
    TaskType.AGGREGATE: ["aggregate"],
}


def validate_graph(
    graph: TaskGraph,
    known_capabilities: set[str] | None = None,
    autofix: bool = True,
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    fixes: list[str] = []

    if len(graph) == 0:
        issues.append(ValidationIssue(code="empty_plan", severity="error", message="plan contains no tasks"))
        return ValidationReport(valid=False, issues=issues)

    ids = set(graph.ids())
    for task in list(graph):
        # self dependency
        if task.id in task.dependencies:
            if autofix:
                graph.set_dependencies(task.id, [d for d in task.dependencies if d != task.id])
                fixes.append(f"removed self-dependency on {task.id}")
            else:
                issues.append(
                    ValidationIssue(
                        code="self_dependency",
                        severity="error",
                        message="task depends on itself",
                        task_id=task.id,
                    )
                )
        # unknown dependencies
        unknown = [d for d in task.dependencies if d not in ids]
        if unknown:
            if autofix:
                task.dependencies = [d for d in task.dependencies if d in ids]
                fixes.append(f"dropped unknown dependencies {unknown} of {task.id}")
                issues.append(
                    ValidationIssue(
                        code="unknown_dependency",
                        severity="warning",
                        message=f"dropped {unknown}",
                        task_id=task.id,
                    )
                )
            else:
                issues.append(
                    ValidationIssue(
                        code="unknown_dependency",
                        severity="error",
                        message=f"unknown deps {unknown}",
                        task_id=task.id,
                    )
                )
        # duplicated dependencies
        if len(set(task.dependencies)) != len(task.dependencies):
            task.dependencies = list(dict.fromkeys(task.dependencies))
            fixes.append(f"deduplicated dependencies of {task.id}")
        # capabilities
        if known_capabilities is not None:
            missing = [c for c in task.required_capabilities if c not in known_capabilities]
            if missing:
                if autofix:
                    task.required_capabilities = [
                        c for c in task.required_capabilities if c in known_capabilities
                    ] or list(DEFAULT_CAPS_BY_TYPE.get(task.task_type, ["general"]))
                    fixes.append(
                        f"mapped unknown capabilities {missing} of {task.id} to {task.required_capabilities}"
                    )
                    issues.append(
                        ValidationIssue(
                            code="unknown_capability",
                            severity="warning",
                            message=f"unknown {missing}",
                            task_id=task.id,
                        )
                    )
                else:
                    issues.append(
                        ValidationIssue(
                            code="unknown_capability",
                            severity="error",
                            message=f"no agent offers {missing}",
                            task_id=task.id,
                        )
                    )
        if not task.required_capabilities:
            task.required_capabilities = list(DEFAULT_CAPS_BY_TYPE.get(task.task_type, ["general"]))
            fixes.append(f"defaulted capabilities of {task.id}")
        if not task.covers:
            task.covers = [f"task:{task.id}"]
            fixes.append(f"defaulted work units of {task.id}")

    graph._rebuild()

    if not graph.is_acyclic():
        cycle = graph.find_cycle()
        issues.append(
            ValidationIssue(
                code="cycle",
                severity="error",
                message=f"dependency cycle: {cycle}",
                task_id=cycle[0][0] if cycle else None,
            )
        )

    sinks = graph.sinks()
    if len(sinks) > 1:
        issues.append(
            ValidationIssue(
                code="multiple_sinks",
                severity="warning",
                message=f"plan has {len(sinks)} terminal tasks: {sinks}",
            )
        )

    valid = not any(i.severity == "error" for i in issues)
    return ValidationReport(valid=valid, issues=issues, fixes=fixes)
