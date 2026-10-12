"""Result Evaluator: validates every intermediate output before it is released downstream.

Rule-based checks always run (format, non-empty, work-unit coverage,
agent-reported blocking issues, sources for research). An optional LLM judge
adds a semantic review. The evaluation is structured so the Failure Analyzer
can classify a failure without re-reading the raw output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from famas.agents import prompts
from famas.core.models import CheckResult, Evaluation, Issue, IssueKind, Task, TaskResult, TaskType
from famas.llm.base import MockReply

if TYPE_CHECKING:  # pragma: no cover
    from famas.orchestration.context import RunContext

MIN_UNIT_CHARS = 20


class JudgeIssue(BaseModel):
    kind: str = "quality"
    message: str


class JudgeVerdict(BaseModel):
    passed: bool = True
    score: float = 1.0
    issues: list[JudgeIssue] = Field(default_factory=list)


class ResultEvaluator:
    def __init__(self, min_unit_chars: int = MIN_UNIT_CHARS) -> None:
        self.min_unit_chars = min_unit_chars

    def rule_checks(self, task: Task, result: TaskResult) -> Evaluation:
        checks: list[CheckResult] = []
        issues: list[Issue] = []

        units = result.data.get("units")
        fmt_ok = isinstance(units, dict)
        checks.append(
            CheckResult(
                name="format",
                passed=fmt_ok,
                detail="structured output with work units" if fmt_ok else "output could not be parsed",
                weight=2.0,
            )
        )
        if not fmt_ok:
            issues.append(
                Issue(
                    kind=IssueKind.INVALID_FORMAT,
                    message="Output is malformed or not in the expected structure.",
                )
            )
            units = {}

        non_empty = bool(result.content.strip()) or bool(units)
        checks.append(CheckResult(name="non_empty", passed=non_empty, weight=1.0))

        required = task.covers or []
        missing = [u for u in required if len(str(units.get(u, "")).strip()) < self.min_unit_chars]
        coverage = 1.0 - (len(missing) / len(required)) if required else 1.0
        checks.append(
            CheckResult(
                name="coverage",
                passed=not missing,
                detail=f"{len(required) - len(missing)}/{len(required)} work units delivered"
                + (f"; missing {missing[:6]}" if missing else ""),
                weight=2.0,
            )
        )
        if missing and fmt_ok:
            issues.append(
                Issue(kind=IssueKind.INCOMPLETE, message=f"Missing required parts: {', '.join(missing[:8])}")
            )

        blocking = [i for i in result.issues if i.blocking]
        checks.append(
            CheckResult(
                name="agent_issues",
                passed=not blocking,
                detail="; ".join(i.message for i in blocking[:4]) or "none reported",
                weight=2.0,
            )
        )
        issues.extend(blocking)

        if task.task_type == TaskType.RESEARCH:
            has_sources = bool(result.sources)
            checks.append(
                CheckResult(
                    name="sources", passed=has_sources, detail=f"{len(result.sources)} source(s)", weight=0.5
                )
            )

        hard = {"format", "non_empty", "coverage", "agent_issues"}
        passed = all(c.passed for c in checks if c.name in hard)
        total = sum(c.weight for c in checks)
        score = sum(c.weight for c in checks if c.passed) / total if total else 0.0
        return Evaluation(
            passed=passed,
            score=round(score, 3),
            checks=checks,
            issues=issues,
            missing_units=missing,
            coverage=round(coverage, 3),
        )

    async def evaluate(self, task: Task, result: TaskResult, ctx: RunContext) -> Evaluation:
        ev = self.rule_checks(task, result)
        if not ctx.config.use_llm_judge or not ev.passed or task.task_type == TaskType.AGGREGATE:
            return ev
        prompt = prompts.JUDGE_PROMPT.format(
            description=task.description,
            expected=task.expected_output,
            criteria="; ".join(task.acceptance_criteria) or "complete and consistent",
            output=result.content[:6000],
        )
        resp = await ctx.llm.complete(
            component="evaluator",
            system=prompts.JUDGE_SYSTEM,
            prompt=prompt,
            schema=JudgeVerdict,
            tier="small",
            mock=lambda: MockReply(parsed=JudgeVerdict(passed=True, score=0.9)),
        )
        ctx.metrics.record_llm("evaluator", resp.usage)
        verdict: JudgeVerdict = resp.parsed
        ev.evaluator = "rules+llm"
        ev.tokens = resp.usage.tokens_in + resp.usage.tokens_out
        ev.duration_ms = resp.usage.duration_ms
        ev.checks.append(
            CheckResult(
                name="llm_judge",
                passed=verdict.passed,
                detail="; ".join(i.message for i in verdict.issues[:3]) or f"score {verdict.score:.2f}",
            )
        )
        if not verdict.passed:
            ev.passed = False
            for i in verdict.issues:
                kind = (
                    IssueKind.INCONSISTENCY
                    if i.kind == "inconsistency"
                    else IssueKind.INCOMPLETE
                    if i.kind == "incomplete"
                    else IssueKind.QUALITY
                )
                ev.issues.append(Issue(kind=kind, message=i.message))
        ev.score = round((ev.score + verdict.score) / 2, 3)
        return ev
