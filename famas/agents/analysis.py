"""Analysis agent: compares subjects along one aspect using upstream research."""

from __future__ import annotations

from pydantic import BaseModel, Field

from famas.agents import prompts
from famas.agents.base import AgentOutput, BaseAgent, ExecContext
from famas.core.models import Issue, IssueKind, Task, TaskResult
from famas.knowledge.catalog import aspect_title, subject_from_key
from famas.knowledge.fixtures import FEATURE_KEYWORDS
from famas.llm.base import MockReply


class ComparisonRow(BaseModel):
    subject: str
    approach: str
    strengths: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ReportedIssue(BaseModel):
    kind: str
    subject: str | None = None
    message: str = ""


class AspectAnalysis(BaseModel):
    aspect: str
    rows: list[ComparisonRow] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    issues: list[ReportedIssue] = Field(default_factory=list)


FEATURE_PHRASES = {
    "parallel_execution": "parallel execution",
    "checkpointing": "checkpoint/resume support",
    "human_in_the_loop": "human-in-the-loop intervention",
    "retries": "built-in retries",
    "dynamic_replanning": "dynamic re-planning",
    "pub_sub_messaging": "publish/subscribe messaging",
    "hierarchical_orchestration": "manager/orchestrator-style coordination",
    "handoffs": "explicit agent handoffs",
    "guardrails": "output validation guardrails",
    "tracing": "built-in tracing",
}


ASPECT_FEATURES = {
    "architecture": ["hierarchical_orchestration", "handoffs", "pub_sub_messaging", "parallel_execution"],
    "communication": ["pub_sub_messaging", "handoffs", "parallel_execution", "hierarchical_orchestration"],
    "scheduling": [
        "parallel_execution",
        "hierarchical_orchestration",
        "human_in_the_loop",
        "dynamic_replanning",
    ],
    "failure_recovery": ["checkpointing", "retries", "guardrails", "dynamic_replanning", "human_in_the_loop"],
    "state_management": ["checkpointing", "human_in_the_loop"],
    "tooling": ["guardrails", "tracing", "handoffs"],
    "observability": ["tracing", "checkpointing"],
}


def _feature_observations(texts: dict[str, str], aspect: str | None = None) -> list[str]:
    """Deterministic cross-system observations: which systems' findings mention which capabilities."""
    obs = []
    feats = ASPECT_FEATURES.get(aspect or "", list(FEATURE_KEYWORDS))
    for feat in feats:
        kws = FEATURE_KEYWORDS[feat]
        having = [name for name, txt in texts.items() if any(k in txt.lower() for k in kws)]
        if having and len(having) < len(texts):
            who = ", ".join(having)
            obs.append(
                f"{who} {'documents' if len(having) == 1 else 'document'} {FEATURE_PHRASES[feat]}; the others do not emphasise it."
            )
        elif having and len(texts) > 1:
            obs.append(f"All compared systems document {FEATURE_PHRASES[feat]}.")
    return obs[:4] or ["The compared systems take broadly similar approaches on this aspect."]


def locate_findings(
    inputs: dict[str, TaskResult], subject_key: str, aspect: str
) -> tuple[str | None, dict | None]:
    for tid, res in inputs.items():
        data = res.data
        if data.get("subject") == subject_key and aspect in (data.get("findings") or {}):
            return tid, data["findings"][aspect]
        multi = data.get("subjects_findings") or {}
        if subject_key in multi and aspect in multi[subject_key]:
            return tid, multi[subject_key][aspect]
    return None, None


class AnalysisAgent(BaseAgent):
    async def run(self, task: Task, inputs: dict[str, TaskResult], ectx: ExecContext) -> AgentOutput:
        aspect = task.inputs.get("aspect")
        subjects: list[str] = list(task.inputs.get("subjects") or [])
        aspects: list[str] = list(task.inputs.get("aspects") or [])
        if not aspect and aspects and subjects:
            return await self._run_multi(task, inputs, ectx, aspects, subjects)
        if not aspect or not subjects:
            from famas.agents.general import run_generic

            return await run_generic(self, task, inputs, ectx)
        return await self._run_aspect(task, inputs, ectx, aspect, subjects)

    async def _run_multi(self, task, inputs, ectx, aspects, subjects) -> AgentOutput:
        """Fixed-pipeline analysis stage: analyse every aspect sequentially inside one task."""
        outs = [await self._run_aspect(task, inputs, ectx, a, subjects) for a in aspects]
        units: dict[str, str] = {}
        issues: list[Issue] = []
        per_aspect = {}
        seen_issue: set[tuple] = set()
        for a, o in zip(aspects, outs, strict=True):
            units.update(o.data["units"])
            per_aspect[a] = {"rows": o.data["rows"], "observations": o.data["observations"]}
            for i in o.issues:
                key = (i.kind, i.subject, i.source_task)
                if key not in seen_issue:
                    seen_issue.add(key)
                    issues.append(i)
        return AgentOutput(
            content="\n\n".join(o.content for o in outs),
            data={"per_aspect": per_aspect, "units": units},
            sources=self.collect_sources(inputs),
            issues=issues,
        )

    async def _run_aspect(self, task, inputs, ectx, aspect: str, subjects: list[str]) -> AgentOutput:
        issues: list[Issue] = []
        found: dict[str, tuple[str, dict]] = {}
        for s in subjects:
            tid, f = locate_findings(inputs, s, aspect)
            if f is None:
                issues.append(
                    Issue(
                        kind=IssueKind.MISSING_INFORMATION,
                        subject=s,
                        aspect=aspect,
                        message=f"No research findings on {aspect_title(aspect)} for {subject_from_key(s).name} in the inputs.",
                    )
                )
            else:
                found[s] = (tid, f)

        # Simulation only: model the analyst noticing a corrupted upstream claim.
        if not ectx.live:
            for tid, res in inputs.items():
                if ectx.run.oracle.detects(task.id, ectx.attempt, res):
                    subj = res.data.get("subject")
                    issues.append(
                        Issue(
                            kind=IssueKind.INCONSISTENCY,
                            subject=subj,
                            aspect=aspect,
                            source_task=tid,
                            message=f"Findings from `{tid}` contradict other evidence (claim conflicts with documented capabilities).",
                        )
                    )

        names = {s: subject_from_key(s).name for s in subjects}
        findings_text = "\n\n".join(
            f"### {names[s]} (from `{tid}`)\n{f.get('summary', '')}\n"
            + "\n".join(f"- {p}" for p in f.get("key_points", []))
            for s, (tid, f) in found.items()
        )

        def _mock() -> MockReply:
            rows = []
            for s, (_tid, f) in found.items():
                pts = f.get("key_points", [])
                rows.append(
                    ComparisonRow(
                        subject=names[s],
                        approach=f.get("summary", ""),
                        strengths=pts[:2],
                        limitations=pts[2:3],
                    )
                )
            texts = {
                names[s]: f.get("summary", "") + " " + " ".join(f.get("key_points", []))
                for s, (_t, f) in found.items()
            }
            return MockReply(
                parsed=AspectAnalysis(
                    aspect=aspect, rows=rows, observations=_feature_observations(texts, aspect)
                )
            )

        prompt = prompts.ANALYSIS_PROMPT.format(
            aspect_title=aspect_title(aspect),
            aspect=aspect,
            subjects=", ".join(names.values()),
            description=task.description,
            findings=findings_text or "(none)",
            feedback=self.feedback_block(task),
        )
        resp = await ectx.llm(
            system=prompts.ANALYSIS_SYSTEM, prompt=prompt, schema=AspectAnalysis, mock=_mock
        )
        result: AspectAnalysis = resp.parsed

        # Map model-reported issues (live mode) back to upstream tasks.
        name_to_key = {v.lower(): k for k, v in names.items()}
        for ri in result.issues:
            skey = name_to_key.get((ri.subject or "").lower())
            if ri.kind == "inconsistency" and skey in found:
                issues.append(
                    Issue(
                        kind=IssueKind.INCONSISTENCY,
                        subject=skey,
                        aspect=aspect,
                        source_task=found[skey][0],
                        message=ri.message,
                    )
                )
            elif ri.kind == "missing_information" and skey and skey not in found:
                pass  # already detected structurally

        units = {}
        row_by_name = {r.subject.lower(): r for r in result.rows}
        for s in found:
            r = row_by_name.get(names[s].lower())
            if r is not None:
                units[f"analysis:{aspect}:{s}"] = f"{r.subject}: {r.approach}"
        lines = [f"## {aspect_title(aspect)} — comparative analysis", ""]
        lines.append("| System | Approach | Strengths | Considerations |")
        lines.append("|---|---|---|---|")
        for r in result.rows:
            lines.append(
                f"| {r.subject} | {r.approach} | {' '.join(r.strengths) or '-'} | {' '.join(r.limitations) or '-'} |"
            )
        lines.append("")
        lines.extend(f"- {o}" for o in result.observations)
        return AgentOutput(
            content="\n".join(lines),
            data={
                "aspect": aspect,
                "rows": [r.model_dump() for r in result.rows],
                "observations": result.observations,
                "units": units,
            },
            sources=self.collect_sources(inputs),
            issues=issues,
        )
