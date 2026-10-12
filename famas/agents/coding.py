"""Coding agent: writes Python, executes it in the sandbox, returns computed results."""

from __future__ import annotations

from pydantic import BaseModel

from famas.agents import prompts
from famas.agents.base import AgentOutput, BaseAgent, ExecContext
from famas.core.models import Issue, IssueKind, Source, Task, TaskResult
from famas.knowledge.fixtures import FEATURE_KEYWORDS
from famas.llm.base import MockReply


class CodeProposal(BaseModel):
    code: str
    explanation: str = ""


FEATURE_MATRIX_CODE = """
def main(data):
    subjects = data.get("subjects", {})
    features = data.get("features", {})
    matrix, scores = {}, {}
    for name, aspects in subjects.items():
        text = " ".join(aspects.values()).lower()
        row = {feat: any(k.lower() in text for k in kws) for feat, kws in features.items()}
        matrix[name] = row
        scores[name] = sum(row.values())
    return {"matrix": matrix, "scores": scores}
""".strip()


class SandboxExecutionError(RuntimeError):
    transient = False


class CodingAgent(BaseAgent):
    async def run(self, task: Task, inputs: dict[str, TaskResult], ectx: ExecContext) -> AgentOutput:
        if task.inputs.get("program") != "feature_matrix":
            from famas.agents.general import run_generic

            return await run_generic(self, task, inputs, ectx)

        subjects: dict[str, dict[str, str]] = {}

        def _flatten(findings: dict) -> dict[str, str]:
            return {
                a: f.get("summary", "") + " " + " ".join(f.get("key_points", [])) for a, f in findings.items()
            }

        for res in inputs.values():
            name = res.data.get("name") or res.data.get("subject")
            findings = res.data.get("findings") or {}
            if name and findings:
                subjects[name] = _flatten(findings)
            for skey, sf in (res.data.get("subjects_findings") or {}).items():
                from famas.knowledge.catalog import subject_from_key

                subjects[subject_from_key(skey).name] = _flatten(sf)
        data = {"subjects": subjects, "features": FEATURE_KEYWORDS}

        prompt = prompts.CODING_PROMPT.format(
            description=task.description, feedback=self.feedback_block(task)
        )
        resp = await ectx.llm(
            system=prompts.CODING_SYSTEM,
            prompt=prompt,
            schema=CodeProposal,
            mock=lambda: MockReply(
                parsed=CodeProposal(code=FEATURE_MATRIX_CODE, explanation="keyword feature matrix")
            ),
        )
        proposal: CodeProposal = resp.parsed
        run = await ectx.run.sandbox.run(proposal.code, data)
        ectx.add_time(run.duration_ms if ectx.live else 120.0)
        if not run.ok:
            raise SandboxExecutionError(f"generated code failed: {run.error}")
        result = run.result if isinstance(run.result, dict) else {}
        matrix: dict = result.get("matrix") or {}
        scores: dict = result.get("scores") or {}
        issues = []
        if not matrix:
            issues.append(
                Issue(kind=IssueKind.INCOMPLETE, message="program returned an empty feature matrix")
            )
        feats = list(FEATURE_KEYWORDS)
        header = "| System | " + " | ".join(f.replace("_", " ") for f in feats) + " | Score |"
        lines = ["## Feature matrix (computed)", "", header, "|" + "---|" * (len(feats) + 2)]
        for name, row in matrix.items():
            cells = " | ".join("✓" if row.get(f) else "·" for f in feats)
            lines.append(f"| {name} | {cells} | {scores.get(name, '')} |")
        lines.append("")
        lines.append(
            "_Computed by the coding agent: a feature is marked when the research text mentions one of its keywords. "
            "It reflects documentation coverage, not a quality judgement._"
        )
        table = "\n".join(lines)
        return AgentOutput(
            content=table,
            data={
                "matrix": matrix,
                "scores": scores,
                "features": feats,
                "code": proposal.code,
                "units": {"code:feature_matrix": table} if matrix else {},
            },
            sources=[Source(title="Computed from upstream research", kind="derived")],
            issues=issues,
        )
