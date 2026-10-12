"""Research agent: gathers per-aspect findings about one subject (repository)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from famas.agents import prompts
from famas.agents.base import AgentOutput, BaseAgent, ExecContext, normalize_aspect
from famas.core.models import Source, Task, TaskResult
from famas.knowledge.catalog import aspect_title, subject_from_key
from famas.knowledge.fixtures import KB, fixture_for, readme_excerpt
from famas.llm.base import MockReply


class Finding(BaseModel):
    aspect: str
    summary: str
    key_points: list[str] = Field(default_factory=list)


class ResearchReport(BaseModel):
    subject: str
    overview: str = ""
    findings: list[Finding] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


def unit_text(f: dict) -> str:
    pts = "; ".join(f.get("key_points", []))
    return f"{f.get('summary', '')} Key points: {pts}".strip()


def mock_research(subject_key: str, name: str, aspects: list[str]) -> ResearchReport:
    findings = []
    for a in aspects:
        fx = fixture_for(subject_key, a)
        findings.append(Finding(aspect=a, summary=fx["summary"], key_points=list(fx["points"])))
    note = KB.get(subject_key, {}).get("note")
    return ResearchReport(
        subject=name,
        overview=f"{name}: findings for {', '.join(aspect_title(a) for a in aspects)}.",
        findings=findings,
        caveats=[note] if note else [],
    )


class ResearchAgent(BaseAgent):
    async def run(self, task: Task, inputs: dict[str, TaskResult], ectx: ExecContext) -> AgentOutput:
        if task.inputs.get("subjects") and not task.inputs.get("subject"):
            return await self._run_multi(task, inputs, ectx)
        subject_key = task.inputs.get("subject")
        if not subject_key:
            from famas.agents.general import run_generic

            return await run_generic(self, task, inputs, ectx)
        return await self._run_subject(task, ectx, subject_key)

    async def _run_multi(self, task: Task, inputs: dict[str, TaskResult], ectx: ExecContext) -> AgentOutput:
        """Fixed-pipeline research stage: research every subject sequentially inside one task."""
        outputs = [await self._run_subject(task, ectx, s) for s in task.inputs["subjects"]]
        units: dict[str, str] = {}
        per_subject: dict[str, dict] = {}
        sources: list[Source] = []
        for out in outputs:
            units.update(out.data["units"])
            per_subject[out.data["subject"]] = out.data["findings"]
            sources.extend(out.sources)
        return AgentOutput(
            content="\n\n".join(o.content for o in outputs),
            data={
                "subjects_findings": per_subject,
                "aspects": task.inputs.get("aspects", []),
                "units": units,
            },
            sources=sources,
        )

    async def _run_subject(self, task: Task, ectx: ExecContext, subject_key: str) -> AgentOutput:
        subj = subject_from_key(subject_key)
        single = task.inputs.get("subject") == subject_key
        name = (task.inputs.get("name") if single else None) or subj.name
        repo = (task.inputs.get("repo") if single else None) or subj.repo
        aspects: list[str] = list(task.inputs.get("aspects") or [])

        sources: list[Source] = []
        material = ""
        if ectx.live and repo:
            info = await ectx.run.github.repo_overview(repo)
            material = info.as_prompt()
            sources = info.sources()
        else:
            ectx.add_time(250.0)  # modelled GitHub API round-trips
            material = readme_excerpt(subject_key) or "(no repository material available offline)"
            if repo:
                sources.append(
                    Source(title=f"{name} repository", url=f"https://github.com/{repo}", kind="github")
                )
            docs = KB.get(subject_key, {}).get("docs")
            if docs and not (repo and docs.endswith(repo)):
                sources.append(Source(title=f"{name} documentation", url=docs, kind="docs"))
            if not ectx.live:
                sources.append(Source(title=f"FA-MAS offline fixture: {name}", url=None, kind="fixture"))

        prompt = prompts.RESEARCH_PROMPT.format(
            name=name,
            repo_line=f" (GitHub: {repo})" if repo else "",
            aspects=", ".join(aspects),
            description=task.description,
            material=material[:12000],
            feedback=self.feedback_block(task),
        )
        resp = await ectx.llm(
            system=prompts.RESEARCH_SYSTEM,
            prompt=prompt,
            schema=ResearchReport,
            mock=lambda: MockReply(parsed=mock_research(subject_key, name, aspects)),
        )
        report: ResearchReport = resp.parsed
        findings: dict[str, dict] = {}
        for f in report.findings:
            key = normalize_aspect(f.aspect, aspects) or f.aspect
            if key in aspects and key not in findings:
                findings[key] = {"summary": f.summary, "key_points": f.key_points}
        units = {f"research:{subject_key}:{a}": unit_text(findings[a]) for a in aspects if a in findings}
        lines = [f"## {name} — research findings", ""]
        if repo:
            lines.append(f"Repository: https://github.com/{repo}\n")
        for a in aspects:
            if a in findings:
                lines.append(f"### {aspect_title(a)}\n{findings[a]['summary']}")
                lines.extend(f"- {p}" for p in findings[a]["key_points"])
                lines.append("")
        for c in report.caveats:
            lines.append(f"> Note: {c}")
        return AgentOutput(
            content="\n".join(lines).strip(),
            data={
                "subject": subject_key,
                "name": name,
                "repo": repo,
                "aspects": aspects,
                "findings": findings,
                "caveats": report.caveats,
                "units": units,
            },
            sources=sources,
        )
